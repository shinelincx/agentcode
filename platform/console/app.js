'use strict';

/* 交付平台管理控制台（单页应用 · 左侧菜单 · 多项目）
   路由：#/overview | #/tasks | #/approvals | #/actions | #/query | #/settings
   所有数据都通过 /api/* 获取；项目通过 ?project= 或请求体 project 指定。 */

const $ = id => document.getElementById(id);
const state = {
  mode: {}, projects: [], project: '', data: null, page: 'overview',
  jobs: [], taskFilter: '', queryDone: false,
};

function esc(s) {
  return (s ?? '').toString().replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const num = n => Number(n || 0).toLocaleString();

const NODES = [
  ['requirements', '需求'], ['review_g1', 'G1'], ['design', '设计'], ['prototype', '原型'],
  ['review_g2', 'G2'], ['implement', '实现'], ['test', '测试'], ['deploy', '部署'],
  ['review_g3', 'G3'], ['retro', 'retro'],
];
const GATE_OF = { review_g1: 'G1_requirements', review_g2: 'G2_design', review_g3: 'G3_staging' };
const KIND_OF = { G1_requirements: 'requirements', G2_design: 'design', G3_staging: 'deploy' };
const STATUS_TEXT = {
  pending: '待启动', queued: '排队中', running: '运行中', awaiting_human: '待人工',
  done: '已完成', failed: '失败', gate_failed: '门禁失败',
};
const stateText = s => STATUS_TEXT[s] || s || '-';
const decisionText = d => ({ approved: '批准', rejected: '驳回', pending: '待审' }[d] || d || '-');
const decisionState = d => d === 'rejected' ? 'failed' : (d === 'approved' ? 'done' : 'awaiting_human');

const ROUTES = {
  overview: ['总览', '预算、任务与审批的实时快照'],
  tasks: ['任务', '流程节点、状态与操作'],
  approvals: ['审批', '待审批闸门与历史结论'],
  actions: ['操作', '新建需求、启动与重跑'],
  query: ['查询', '按关键字或状态检索任务'],
  settings: ['设置', '角色配置、预算、门禁与项目路径'],
};

/* ---------- 网络 ---------- */
function withProject(path) {
  const u = new URL(path, location.origin);
  if (state.project) u.searchParams.set('project', state.project);
  return u.pathname + u.search;
}
async function getJSON(path) {
  try {
    const res = await fetch(withProject(path), { cache: 'no-store' });
    return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
  } catch (e) {
    return { ok: false, status: 0, data: { error: String(e) } };
  }
}
async function postJSON(path, body, header = 'X-Control') {
  try {
    const res = await fetch(withProject(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [header]: '1' },
      body: JSON.stringify({ ...body, project: state.project }),
    });
    return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
  } catch (e) {
    return { ok: false, status: 0, data: { error: String(e) } };
  }
}

/* ---------- 通用渲染 ---------- */
function table(head, rows, render, empty = '暂无数据') {
  if (!rows || !rows.length) return `<div class="empty">${esc(empty)}</div>`;
  return `<table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead>`
    + `<tbody>${rows.map(render).join('')}</tbody></table>`;
}

function flow(t, d) {
  const steps = (d.task_steps || {})[t.id] || {};
  const gates = (d.gate_states || {})[t.id] || {};
  return NODES.map(([key, label]) => {
    let st = 'pending';
    if (GATE_OF[key]) {
      const dec = gates[GATE_OF[key]];
      st = dec === 'approved' ? 'done' : dec === 'rejected' ? 'failed'
        : dec === 'pending' ? 'awaiting_human' : 'pending';
      if (st === 'pending' && t.current_node === key && t.status === 'awaiting_human') st = 'awaiting_human';
    } else {
      const s = steps[key];
      if (s) st = s.status;
      else if (t.current_node === key && t.status === 'running') st = 'running';
    }
    return `<span class="chip c-${esc(st)}" title="${esc(label)} · ${esc(stateText(st))}">${esc(label)}</span>`;
  }).join('<span class="arrow">›</span>');
}

const pendingOf = d => d.pending_approvals || (d.approvals || []).filter(a => a.decision === 'pending');

function pendingCards(d) {
  const rows = pendingOf(d);
  if (!rows.length) return '<div class="empty">没有待审批的闸门。用「操作 → 新建需求」创建任务并启动。</div>';
  return rows.map(a => {
    const kind = KIND_OF[a.gate_name] || '';
    const art = ((d.gate_artifacts || {})[a.task_id] || {})[a.gate_name];
    const acts = [];
    if (kind) acts.push(`<button class="btn btn-sm" data-art="${esc(a.task_id)}" data-kind="${esc(kind)}">查看产物</button>`);
    if (a.gate_name === 'G1_requirements') {
      acts.push(`<button class="btn btn-sm" data-compare="${esc(a.task_id)}">原始 vs 标准需求</button>`);
    }
    if (state.mode.control) {
      acts.push(`<button class="btn btn-sm btn-ok" data-decide="approved" data-task="${esc(a.task_id)}">批准</button>`);
      acts.push(`<button class="btn btn-sm btn-bad" data-decide="rejected" data-task="${esc(a.task_id)}">驳回</button>`);
    } else {
      acts.push('<span class="meta-text">只读模式</span>');
    }
    return `<div class="card-row">
      <div><b class="mono">${esc(a.task_id.slice(0, 8))}</b> <span class="gate">${esc(a.gate_name)}</span>
        <div class="meta-text">提交于 ${esc(a.created_at)} · 产物 ${art ? art.size + ' B' : '缺失'}</div></div>
      <div class="acts">${acts.join('')}</div></div>`;
  }).join('');
}

function taskRows(d, rows) {
  const waiting = new Set(pendingOf(d).map(a => a.task_id));
  return rows.map(t => {
    const acts = [];
    if (state.mode.control) {
      if (t.status === 'pending') acts.push(`<button class="btn btn-sm" data-start="${esc(t.id)}">启动</button>`);
      if (waiting.has(t.id)) {
        acts.push(`<button class="btn btn-sm btn-ok" data-decide="approved" data-task="${esc(t.id)}">批准</button>`);
        acts.push(`<button class="btn btn-sm btn-bad" data-decide="rejected" data-task="${esc(t.id)}">驳回</button>`);
      }
    }
    acts.push(`<button class="btn btn-sm" data-detail="${esc(t.id)}">详情</button>`);
    return `<tr><td class="mono">${esc(t.id.slice(0, 8))}</td>
      <td class="req" title="${esc(t.requirement)}">${esc(t.requirement)}</td>
      <td>${esc(t.priority)}</td>
      <td class="s-${esc(t.status)}">${esc(stateText(t.status))}</td>
      <td>${esc(t.current_node || '-')}</td>
      <td><div class="flow">${flow(t, d)}</div></td>
      <td class="${t.sla_overdue ? 's-failed' : ''}">${t.sla_wait_hours != null ? t.sla_wait_hours + 'h/' + t.sla_hours + 'h' : '-'}</td>
      <td class="mono">${t.calls}</td>
      <td><div class="acts">${acts.join('')}</div></td></tr>`;
  }).join('');
}

function tasksTable(d, rows) {
  if (!rows || !rows.length) return '<div class="empty">暂无任务</div>';
  return `<table><thead><tr><th>任务</th><th>需求</th><th>优先级</th><th>状态</th><th>当前节点</th>
    <th>流程</th><th>SLA</th><th>calls</th><th>操作</th></tr></thead>
    <tbody>${taskRows(d, rows)}</tbody></table>`;
}

function approvalsTable(d) {
  return table(['任务', '闸门', '结论', '决策人', '时间'], d.approvals, a =>
    `<tr><td class="mono">${esc(a.task_id.slice(0, 8))}</td><td>${esc(a.gate_name)}</td>
     <td class="s-${decisionState(a.decision)}">${esc(decisionText(a.decision))}</td>
     <td>${esc(a.decided_by || '-')}</td><td class="meta-text">${esc(a.decided_at || a.created_at)}</td></tr>`);
}

function gatesTable(d) {
  return table(['任务', '门禁', '结果', '时间'], d.gates, g =>
    `<tr><td class="mono">${esc(g.task_id.slice(0, 8))}</td><td>${esc(g.gate_name)}</td>
     <td class="p-${g.passed}">${g.passed ? 'PASS' : 'FAIL'}</td>
     <td class="meta-text">${esc(g.checked_at)}</td></tr>`);
}

function gauge(d) {
  const pct = Math.min(100, d.budget.ratio * 100).toFixed(1);
  return `<div class="panel">
    <div class="panel-head"><h2>月度用量</h2><span class="meta-text">call_count 主单位</span></div>
    <div class="gauge-body">
      <div class="gauge-num"><b>${num(d.budget.calls)} / ${num(d.budget.quota)}</b>
        <span class="meta-text">${pct}%</span></div>
      <div class="bar"><div class="fill ${esc(d.budget.state)}" style="width:${pct}%"></div></div>
      <p class="meta-text">阈值 alert ${(d.config.alert * 100) | 0}% · degraded ${(d.config.degraded * 100) | 0}%
        · paused ${(d.config.paused * 100) | 0}% · 单任务上限 U=${d.config.per_task}</p>
    </div></div>`;
}

function kpis(d) {
  return `<div class="kpis">
    <div class="kpi"><span>任务总数</span><b>${d.counts.tasks}</b></div>
    <div class="kpi"><span>运行中</span><b>${d.counts.running}</b></div>
    <div class="kpi"><span>待人工</span><b>${d.counts.awaiting}</b></div>
    <div class="kpi"><span>待审批</span><b>${d.counts.approvals}</b></div>
    <div class="kpi"><span>Retro 待处理</span><b>${d.counts.retro_pending}</b></div>
    <div class="kpi"><span>累计 tokens</span><b>${num(d.budget.tokens)}</b></div>
  </div>`;
}

/* ---------- 页面：总览 ---------- */
function overviewView(d) {
  const recent = (d.tasks || []).slice(0, 8);
  return `<section class="hero">${gauge(d)}${kpis(d)}</section>
    <section class="panel accent">
      <div class="panel-head"><h2>待审批 · 需求 G1 / 设计 G2 / staging G3</h2>
        <span class="count">${pendingOf(d).length}</span></div>
      ${pendingCards(d)}
    </section>
    <section class="panel">
      <div class="panel-head"><h2>最近任务</h2><a class="meta-text" href="#/tasks">查看全部 →</a></div>
      <div class="table-wrap">${tasksTable(d, recent)}</div>
    </section>
    <div class="cols-2">
      <section class="panel"><div class="panel-head"><h2>审批记录</h2></div>
        <div class="table-wrap">${approvalsTable(d)}</div></section>
      <section class="panel"><div class="panel-head"><h2>门禁结果</h2></div>
        <div class="table-wrap">${gatesTable(d)}</div></section>
    </div>`;
}

/* ---------- 页面：任务 ---------- */
function tasksView(d) {
  const f = state.taskFilter;
  const rows = (d.tasks || []).filter(t => !f || t.status === f);
  return `<section class="panel">
    <div class="panel-head"><h2>任务与节点流程</h2>
      <span class="meta-text">共 ${(d.tasks || []).length} 条（最近 30）· 3s 刷新</span></div>
    <div class="toolbar">
      <select id="t-status">
        <option value="">全部状态</option>
        ${['pending', 'running', 'awaiting_human', 'done', 'failed', 'gate_failed']
          .map(s => `<option value="${s}" ${f === s ? 'selected' : ''}>${esc(stateText(s))}</option>`).join('')}
      </select>
      <span class="meta-text">共 ${rows.length} 条</span>
    </div>
    <div id="t-body" class="table-wrap">${tasksTable(d, rows)}</div>
  </section>
  <div class="cols-2">
    <section class="panel"><div class="panel-head"><h2>节点执行</h2><span class="meta-text">最近 30 条</span></div>
      <div class="table-wrap">${runsTable(d)}</div></section>
    <section class="panel"><div class="panel-head"><h2>Lane 计划（合并顺序）</h2></div>
      <div class="table-wrap">${lanesTable(d)}</div></section>
  </div>
  <div class="cols-2">
    <section class="panel"><div class="panel-head"><h2>Worker 租约</h2></div>
      <div class="table-wrap">${leasesTable(d)}</div></section>
    <section class="panel"><div class="panel-head"><h2>Retro 投递（outbox）</h2></div>
      <div class="table-wrap">${outboxTable(d)}</div></section>
  </div>`;
}

function runsTable(d) {
  return table(['任务', '节点', 'lane', '尝试', '状态', '分支', '开始', '结束'], d.node_runs, r =>
    `<tr><td class="mono">${esc(r.task_id.slice(0, 8))}</td><td>${esc(r.node_name)}</td>
     <td>${esc(r.lane || '-')}</td><td>${r.attempt}</td>
     <td class="s-${esc(r.status)}">${esc(stateText(r.status))}</td>
     <td class="mono">${esc(r.branch || '-')}</td>
     <td class="meta-text">${esc(r.started_at)}</td><td class="meta-text">${esc(r.ended_at || '-')}</td></tr>`);
}
function lanesTable(d) {
  return table(['任务', 'lane', '顺序', '分支', '基线', '状态', '并入时间'], d.lanes || [], l =>
    `<tr><td class="mono">${esc(l.task_id.slice(0, 8))}</td><td>${esc(l.name)}</td><td>${l.merge_order}</td>
     <td class="mono">${esc(l.branch || '-')}</td><td class="mono">${esc(l.base_branch || '-')}</td>
     <td class="s-${esc(l.status)}">${esc(stateText(l.status))}</td>
     <td class="meta-text">${esc(l.merged_at || '-')}</td></tr>`);
}
function leasesTable(d) {
  return table(['任务', '节点', 'Worker', '角色', 'host', 'epoch', '心跳', '到期', '状态'], d.leases || [], l =>
    `<tr><td class="mono">${esc(l.task_id.slice(0, 8))}</td><td>${esc(l.node_name)}</td>
     <td class="mono">${esc((l.worker_id || '').slice(0, 8))}</td><td>${esc(l.worker_role || '-')}</td>
     <td>${esc(l.host || '-')}</td><td class="mono">${l.lease_epoch}</td>
     <td class="meta-text">${esc(l.heartbeat_at || '-')}</td>
     <td class="meta-text">${esc(l.lease_expires_at || '-')}</td>
     <td class="${l.live ? 's-running' : 's-pending'}">${l.live ? '持有中' : '已释放/过期'}</td></tr>`);
}
function outboxTable(d) {
  return table(['任务', '队列#', '状态', '尝试', '下次重试', '最近错误', '投递时间'], d.outbox || [], o =>
    `<tr><td class="mono">${esc((o.task_id || '').slice(0, 8))}</td><td class="mono">${o.id}</td>
     <td class="${o.status === 'dead' ? 's-failed' : (o.status === 'done' ? 's-done' : 's-pending')}">${esc(o.status)}</td>
     <td>${o.attempts}</td><td class="meta-text">${esc(o.next_attempt_at || '-')}</td>
     <td class="meta-text" title="${esc(o.last_error || '')}">${esc((o.last_error || '-').slice(0, 40))}</td>
     <td class="meta-text">${esc(o.delivered_at || '-')}</td></tr>`);
}

/* ---------- 页面：审批 ---------- */
function approvalsView(d) {
  return `<section class="panel accent">
      <div class="panel-head"><h2>待审批</h2><span class="count">${pendingOf(d).length}</span></div>
      ${pendingCards(d)}
    </section>
    <div class="cols-2">
      <section class="panel"><div class="panel-head"><h2>审批记录</h2></div>
        <div class="table-wrap">${approvalsTable(d)}</div></section>
      <section class="panel"><div class="panel-head"><h2>门禁结果</h2></div>
        <div class="table-wrap">${gatesTable(d)}</div></section>
    </div>
    <section class="panel"><div class="panel-head"><h2>按模型用量</h2></div>
      <div class="table-wrap">${table(['模型', '执行器', 'calls', 'tokens'], d.usage_by_model, m =>
        `<tr><td class="mono">${esc(m.model)}</td><td>${esc(m.executor)}</td>
         <td>${num(m.calls)}</td><td>${num(m.tokens)}</td></tr>`)}</div></section>`;
}

/* ---------- 页面：操作 ---------- */
function actionPending(d) {
  const rows = (d.tasks || []).filter(t => t.status === 'pending');
  if (!rows.length) return '<div class="empty">没有待启动的任务</div>';
  return rows.map(t => `<div class="card-row">
    <div><b class="mono">${esc(t.id.slice(0, 8))}</b> <span class="meta-text">${esc(t.requirement)}</span>
      <div class="meta-text">${esc(t.priority)} · 创建于 ${esc(t.created_at)}</div></div>
    <div class="acts">
      <button class="btn btn-sm" data-detail="${esc(t.id)}">详情</button>
      ${state.mode.control ? `<button class="btn btn-sm btn-primary" data-start="${esc(t.id)}">启动</button>` : ''}
    </div></div>`).join('');
}

function jobsTable() {
  return table(['动作', '任务', '状态', '消息', '排队时间', '结束时间'], state.jobs, j =>
    `<tr><td>${esc(j.action)}</td><td class="mono">${esc((j.task_id || '').slice(0, 8))}</td>
     <td class="${j.status === 'failed' ? 's-failed' : (j.status === 'done' ? 's-done' : 's-running')}">${esc(j.status)}</td>
     <td class="meta-text" title="${esc(j.message || '')}">${esc((j.message || '-').slice(0, 60))}</td>
     <td class="meta-text">${esc(j.queued_at)}</td><td class="meta-text">${esc(j.ended_at || '-')}</td></tr>`);
}

function actionsView(d) {
  return `<div class="cols-2 wide-left">
    <section class="panel">
      <div class="panel-head"><h2>新建交付需求</h2><span class="meta-text">写入 ${esc(state.project)} 账本</span></div>
      <form class="card-body" id="act-form">
        <label><b>标题 <span class="req">*</span></b>
          <input id="at-title" maxlength="120" placeholder="例：stats 接口增加 startedAt 字段"></label>
        <label><b>目标 <span class="req">*</span></b>
          <textarea id="at-goal" placeholder="用户可观察的行为变化，一两句话说清"></textarea></label>
        <label><b>验收标准 <span class="req">*</span></b>
          <textarea id="at-ac" placeholder="一行一条，例：GET /api/stats 返回 ISO 8601 的 startedAt"></textarea></label>
        <label>非目标<textarea id="at-non" placeholder="明确不做什么（可空）"></textarea></label>
        <div class="row-2">
          <label>后端范围（逗号分隔）<input id="at-be" placeholder="apps/api/src/**"></label>
          <label>前端范围（逗号分隔）<input id="at-fe" placeholder="apps/web/src/**"></label>
        </div>
        <label>优先级<select id="at-prio">
          <option value="normal">normal</option><option value="high">high</option><option value="low">low</option>
        </select></label>
        <label><b>参考图片</b> <span class="meta-text">PNG/JPEG，最多 4 张，单张 ≤2 MB</span>
          <input id="at-images" type="file" accept="image/png,image/jpeg" multiple>
          <div id="at-thumbs" class="thumbs"></div></label>
        <label class="check-inline"><input type="checkbox" id="at-autostart" checked>
          <span>创建后立即启动（图跑到需求闸门 G1）</span></label>
        <div class="savebar">
          <button class="btn btn-primary" type="submit" id="at-submit">创建任务</button>
          <span class="hint" id="at-result"></span>
        </div>
      </form>
    </section>
    <div class="stack">
      <section class="panel"><div class="panel-head"><h2>待启动任务</h2></div>
        <div id="act-pending">${actionPending(d)}</div></section>
      <section class="panel"><div class="panel-head"><h2>控制台执行记录</h2>
        <span class="meta-text">最近 20 条</span></div>
        <div id="act-jobs" class="table-wrap">${jobsTable()}</div></section>
    </div>
  </div>`;
}

/* ---------- 页面：查询 ---------- */
function queryView() {
  return `<section class="panel">
    <div class="panel-head"><h2>任务检索</h2><span class="meta-text">按任务 id 或需求关键字，可叠加状态</span></div>
    <form class="card-body" id="q-form">
      <div class="row-3">
        <label>关键字<input id="q-key" placeholder="任务 id / 需求文字"></label>
        <label>状态<select id="q-status">
          <option value="">全部</option>
          ${['pending', 'running', 'awaiting_human', 'done', 'failed', 'gate_failed']
            .map(s => `<option value="${s}">${esc(stateText(s))}</option>`).join('')}
        </select></label>
        <label>上限<input id="q-limit" type="number" min="1" max="500" value="100"></label>
      </div>
      <div class="savebar"><button class="btn btn-primary" type="submit">查询</button>
        <span class="hint" id="q-hint">查询范围：${esc(state.project)}</span></div>
    </form>
  </section>
  <section class="panel"><div class="panel-head"><h2>结果</h2><span class="meta-text" id="q-count"></span></div>
    <div id="q-result" class="table-wrap"><div class="empty">输入条件后查询</div></div></section>`;
}

function queryResults(rows) {
  if (!rows.length) return '<div class="empty">没有匹配的任务</div>';
  return `<table><thead><tr><th>任务</th><th>需求</th><th>优先级</th><th>状态</th>
    <th>当前节点</th><th>calls</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${
    rows.map(t => `<tr><td class="mono">${esc(t.id.slice(0, 8))}</td>
      <td class="req" title="${esc(t.requirement)}">${esc(t.requirement)}</td>
      <td>${esc(t.priority)}</td><td class="s-${esc(t.status)}">${esc(stateText(t.status))}</td>
      <td>${esc(t.current_node || '-')}</td><td class="mono">${t.calls}</td>
      <td class="meta-text">${esc(t.created_at)}</td>
      <td><button class="btn btn-sm" data-detail="${esc(t.id)}">详情</button></td></tr>`).join('')}</tbody></table>`;
}

/* ---------- 页面框架 ---------- */
function renderProjectSelect() {
  const sel = $('project');
  sel.innerHTML = state.projects.map(p =>
    `<option value="${esc(p.name)}" ${p.name === state.project ? 'selected' : ''}>`
    + `${esc(p.label && p.label !== p.name ? p.label + ' · ' : '')}${esc(p.name)}${p.db ? '' : '（无账本）'}</option>`).join('');
}

function updateChrome(d) {
  if (!d || !d.counts) return;
  $('gen').textContent = '更新于 ' + d.generated_at;
  const st = $('state');
  st.textContent = d.budget.state;
  st.className = 'badge ' + d.budget.state;
  $('nav-tasks').textContent = d.counts.tasks;
  $('nav-approvals').textContent = d.counts.approvals;
  $('nav-approvals').className = 'nav-count' + (d.counts.approvals ? ' warn' : '');
  $('dbhint').textContent = `${state.project}/state.db`;
}

function setPage(page) {
  state.page = page;
  const meta = ROUTES[page] || ROUTES.overview;
  $('page-title').textContent = meta[0];
  $('page-sub').textContent = meta[1];
  document.querySelectorAll('#nav a').forEach(a =>
    a.classList.toggle('on', a.dataset.nav === page));
  renderPage();
}

function renderPage() {
  const v = $('view');
  if (state.page === 'query') { v.innerHTML = queryView(); return; }
  if (state.page === 'settings') { SettingsView.load(state.project); return; }
  if (!state.data) { v.innerHTML = '<div class="empty">加载中…</div>'; return; }
  const d = state.data;
  if (state.page === 'overview') v.innerHTML = overviewView(d);
  else if (state.page === 'tasks') v.innerHTML = tasksView(d);
  else if (state.page === 'approvals') v.innerHTML = approvalsView(d);
  else if (state.page === 'actions') v.innerHTML = actionsView(d);
}

function refreshPage() {
  if (!state.data) return;
  if (['overview', 'tasks', 'approvals'].includes(state.page)) renderPage();
  else if (state.page === 'actions') {
    if ($('act-pending')) $('act-pending').innerHTML = actionPending(state.data);
    if ($('act-jobs')) $('act-jobs').innerHTML = jobsTable();
  }
}

/* ---------- 数据 ---------- */
async function loadOverview(initial = false) {
  const { ok, data } = await getJSON('/api/overview');
  if (!ok || data.error) {
    toast(data.error || '读取快照失败', 'jbad');
    state.data = null;
    if (state.page !== 'settings' && state.page !== 'query') {
      $('view').innerHTML = `<div class="empty">${esc(data.error || '读取快照失败')}</div>`;
    }
    return;
  }
  state.data = data;
  updateChrome(data);
  if (initial) renderPage(); else refreshPage();
}

async function pollJobs() {
  if (!state.mode.control) return;
  const { ok, data } = await getJSON('/api/jobs');
  if (!ok) return;
  state.jobs = data.jobs || [];
  const j = state.jobs[0];
  const bar = $('jobbar');
  if (!j) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.className = 'jobbar ' + (j.status === 'failed' ? 'jbad' : j.status === 'done' ? 'jok' : 'jrun');
  bar.innerHTML = `<b>${esc(j.action)}</b> ${esc((j.task_id || '').slice(0, 8))}
    · ${esc(j.status)} · ${esc(j.message || '')}
    <span class="meta-text" style="margin-left:auto">${esc(j.queued_at)}</span>`;
  if (state.page === 'actions' && $('act-jobs')) $('act-jobs').innerHTML = jobsTable();
}

function toast(msg, cls = 'jrun') {
  const bar = $('jobbar');
  bar.hidden = false;
  bar.className = 'jobbar ' + cls;
  bar.textContent = msg;
}

async function act(path, payload) {
  const { ok, data } = await postJSON(path, payload);
  if (!ok) { toast('操作失败：' + (data.error || ''), 'jbad'); return; }
  toast(`已提交 ${data.action || ''} · ${(data.task_id || '').slice(0, 8)}（${data.job_id || '-'}），执行中…`);
  pollJobs();
}

/* ---------- 详情与产物 ---------- */
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(new Error('读取图片失败：' + file.name));
    reader.readAsDataURL(file);
  });
}

function originalHtml(o) {
  const acs = (o.acceptance_criteria || []).map(x => `<li>${esc(x)}</li>`).join('');
  const scope = Object.entries(o.scope || {}).map(([k, v]) =>
    `<li>${esc(k)}: ${esc((v || []).join(', '))}</li>`).join('');
  return `<dl class="kv">
    <dt>标题</dt><dd>${esc(o.title)}</dd>
    <dt>目标</dt><dd>${esc(o.goal)}</dd>
    <dt>验收标准</dt><dd><ul>${acs}</ul></dd>
    ${o.non_goals ? `<dt>非目标</dt><dd>${esc(o.non_goals)}</dd>` : ''}
    ${scope ? `<dt>涉及范围</dt><dd><ul>${scope}</ul></dd>` : ''}
    <dt>优先级</dt><dd>${esc(o.priority || '-')}</dd>
  </dl>`;
}

function thumbsHtml(list) {
  if (!list || !list.length) return '';
  return `<div class="thumbs">${list.map(a =>
    `<a href="${esc(a.url)}" target="_blank" rel="noopener" title="${esc(a.name)}">
       <img src="${esc(a.url)}" alt="${esc(a.name)}">
       <span class="meta-text">${esc(a.name)} · ${Math.round((a.size || 0) / 1024)} KB</span></a>`).join('')}</div>`;
}

async function showRequirementCompare(taskId) {
  const [det, art] = await Promise.all([
    getJSON(`/api/task/detail?task_id=${encodeURIComponent(taskId)}`),
    getJSON(`/api/artifact?task_id=${encodeURIComponent(taskId)}&kind=requirements`),
  ]);
  if (!det.ok) { toast('读取失败：' + (det.data.error || ''), 'jbad'); return; }
  const data = det.data, original = data.original || {};
  const rawText = original.requirement || (data.task && data.task.requirement) || '（无原始需求）';
  const std = art.ok ? art.data.content : '';
  $('m-title').textContent = `原始需求 vs Agent 标准需求 · ${taskId.slice(0, 8)}`;
  $('m-body').innerHTML = `<div class="compare">
    <div class="cmp-col"><h3>原始需求（用户填写）</h3>
      ${original.title ? originalHtml(original) : `<pre class="doc">${esc(rawText)}</pre>`}
      ${thumbsHtml(data.attachments)}
    </div>
    <div class="cmp-col"><h3>Agent 标准需求（REQUIREMENTS.md）</h3>
      ${std ? `<pre class="doc">${esc(std)}</pre>`
             : '<div class="empty">尚未生成（任务还没跑到需求节点）</div>'}
    </div></div>`;
  $('modal').hidden = false;
}

async function showArtifact(taskId, kind) {
  const { ok, data } = await getJSON(
    `/api/artifact?task_id=${encodeURIComponent(taskId)}&kind=${encodeURIComponent(kind)}`);
  if (!ok) { toast('读取失败：' + (data.error || ''), 'jbad'); return; }
  $('m-title').textContent = `${kind} · ${taskId.slice(0, 8)}`;
  if (kind === 'prototype_html') {
    $('m-body').innerHTML = `<div class="meta-text mono">${esc(data.path)} · ${data.size} B</div>
      <iframe sandbox="allow-scripts" style="width:100%;height:70vh;border:1px solid var(--line);border-radius:10px;background:#fff;margin-top:8px"
        srcdoc="${esc(data.content)}"></iframe>`;
  } else {
    $('m-body').innerHTML = `<div class="meta-text mono">${esc(data.path)} · ${data.size} B</div>
      <pre class="doc">${esc(data.content)}</pre>`;
  }
  $('modal').hidden = false;
}

async function showDetail(taskId) {
  const { ok, data } = await getJSON(`/api/task/detail?task_id=${encodeURIComponent(taskId)}`);
  if (!ok) { toast('读取失败：' + (data.error || ''), 'jbad'); return; }
  const arts = Object.entries(data.artifacts || {}).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td class="mono">${esc(v.path || '-')}</td>
     <td>${v.exists ? v.size + ' B' : '缺失'}</td>
     <td>${v.exists ? `<button class="btn btn-sm" data-art="${esc(taskId)}" data-kind="${esc(k)}">查看</button>` : ''}</td></tr>`).join('');
  const runs = (data.node_runs || []).map(r =>
    `<tr><td>${esc(r.node_name)}</td><td>${r.attempt}</td>
     <td class="s-${esc(r.status)}">${esc(stateText(r.status))}</td>
     <td>${esc(r.lane || '-')}</td><td class="meta-text">${esc(r.started_at)}</td>
     <td class="meta-text">${esc(r.ended_at || '-')}</td></tr>`).join('');
  const apps = (data.approvals || []).map(a =>
    `<tr><td>${esc(a.gate_name)}</td>
     <td class="s-${decisionState(a.decision)}">${esc(decisionText(a.decision))}</td>
     <td>${esc(a.decided_by || '-')}</td><td class="meta-text">${esc(a.decided_at || '-')}</td></tr>`).join('');
  $('m-title').textContent = `任务 ${taskId.slice(0, 8)}`;
  $('m-body').innerHTML =
    `<div class="meta-text">状态 ${esc(stateText(data.task.status))} · 当前节点 ${esc(data.task.current_node || '-')}
       · 重试 ${data.task.retry_count} · 创建 ${esc(data.task.created_at)}</div>
     <div style="margin:12px 0"><button class="btn btn-sm" data-rerun="${esc(taskId)}">从节点重跑…</button>
       <button class="btn btn-sm" data-compare="${esc(taskId)}">原始 vs 标准需求</button>
       <span class="meta-text">失败或已结束后可从指定节点复跑</span></div>
     <h3>节点时间线</h3>
     ${runs ? `<div class="table-wrap"><table><thead><tr><th>节点</th><th>尝试</th><th>状态</th><th>lane</th><th>开始</th><th>结束</th></tr></thead><tbody>${runs}</tbody></table></div>` : '<div class="empty">暂无节点记录</div>'}
     <h3>产物</h3>
     <div class="table-wrap"><table><thead><tr><th>类型</th><th>路径</th><th>大小</th><th></th></tr></thead><tbody>${arts}</tbody></table></div>
     <h3>审批</h3>
     ${apps ? `<div class="table-wrap"><table><thead><tr><th>闸门</th><th>结论</th><th>决策人</th><th>时间</th></tr></thead><tbody>${apps}</tbody></table></div>` : '<div class="empty">暂无审批记录</div>'}`;
  $('modal').hidden = false;
}

/* ---------- 查询 ---------- */
async function runQuery() {
  const q = ($('q-key')?.value || '').trim();
  const status = $('q-status')?.value || '';
  const limit = $('q-limit')?.value || '100';
  state.queryDone = true;
  const { ok, data } = await getJSON(
    `/api/search?q=${encodeURIComponent(q)}&status=${encodeURIComponent(status)}&limit=${encodeURIComponent(limit)}`);
  if (!ok) { $('q-result').innerHTML = `<div class="empty">${esc(data.error || '查询失败')}</div>`; return; }
  const rows = data.tasks || [];
  $('q-count').textContent = `共 ${rows.length} 条`;
  $('q-result').innerHTML = queryResults(rows);
}

/* ---------- 项目切换 ---------- */
async function switchProject(name) {
  if (!name || name === state.project) return;
  state.project = name;
  state.data = null;
  state.jobs = [];
  state.taskFilter = '';
  state.queryDone = false;
  localStorage.setItem('ac.project', name);
  $('jobbar').hidden = true;
  $('dbhint').textContent = `${name}/state.db`;
  await loadOverview(true);
  if (state.mode.control) pollJobs();
}

/* ---------- 事件 ---------- */
document.addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  if (btn.id === 'm-close') { $('modal').hidden = true; return; }
  if (btn.id === 'refresh') { loadOverview(false); if (state.page === 'settings') SettingsView.load(state.project); return; }
  if (btn.dataset.art) return showArtifact(btn.dataset.art, btn.dataset.kind);
  if (btn.dataset.compare) return showRequirementCompare(btn.dataset.compare);
  if (btn.dataset.start) return act('/api/task/start', { task_id: btn.dataset.start });
  if (btn.dataset.decide) return act('/api/task/decide',
    { task_id: btn.dataset.task, decision: btn.dataset.decide, by: 'console' });
  if (btn.dataset.detail) return showDetail(btn.dataset.detail);
  if (btn.dataset.rerun) {
    const from = prompt('从哪个节点重跑？可用：requirements / design / prototype / implement / review / test / deploy / retro', 'test');
    if (!from) return;
    return act('/api/task/rerun', { task_id: btn.dataset.rerun, from: from.trim(), by: 'console' });
  }
  if (btn.dataset.go) { location.hash = '#/' + btn.dataset.go; }
});

document.addEventListener('change', e => {
  if (e.target.id === 'project') switchProject(e.target.value);
  if (e.target.id === 'at-images') {
    const box = $('at-thumbs');
    if (!box) return;
    box.innerHTML = '';
    Array.from(e.target.files || []).forEach(f => {
      const img = document.createElement('img');
      img.src = URL.createObjectURL(f);
      img.title = `${f.name} · ${Math.round(f.size / 1024)} KB`;
      box.appendChild(img);
    });
  }
  if (e.target.id === 't-status') {
    state.taskFilter = e.target.value;
    if (state.data) $('t-body').innerHTML = tasksTable(state.data,
      (state.data.tasks || []).filter(t => !state.taskFilter || t.status === state.taskFilter));
  }
});

document.addEventListener('submit', async e => {
  if (e.target.id === 'q-form') { e.preventDefault(); runQuery(); return; }
  if (e.target.id !== 'act-form') return;
  e.preventDefault();
  const btn = $('at-submit'), out = $('at-result');
  const payload = {
    title: $('at-title').value.trim(),
    goal: $('at-goal').value.trim(),
    acceptance_criteria: $('at-ac').value.split('\n').map(x => x.trim()).filter(Boolean),
    non_goals: $('at-non').value.trim(),
    scope: {
      be: $('at-be').value.split(',').map(x => x.trim()).filter(Boolean),
      fe: $('at-fe').value.split(',').map(x => x.trim()).filter(Boolean),
    },
    priority: $('at-prio').value,
  };
  btn.disabled = true;
  out.className = 'hint';
  out.textContent = '提交中…';
  try {
    const files = Array.from(($('at-images') && $('at-images').files) || []);
    if (files.length > 4) throw new Error('最多 4 张图片');
    payload.images = [];
    for (const f of files) {
      if (!['image/png', 'image/jpeg'].includes(f.type)) throw new Error(`不支持的图片类型：${f.name}`);
      if (f.size > 2 * 1024 * 1024) throw new Error(`${f.name} 超过 2MB`);
      payload.images.push({ name: f.name, type: f.type, data: await fileToBase64(f) });
    }
  } catch (err) {
    btn.disabled = false;
    out.className = 'hint s-failed';
    out.textContent = '图片不合格：' + err.message;
    return;
  }
  const { ok, data } = await postJSON('/api/intake', payload, 'X-Intake');
  if (!ok) {
    out.className = 'hint s-failed';
    out.textContent = '创建失败：' + (data.error || '');
  } else {
    let msg = '已创建 ' + data.task_id;
    if ($('at-autostart').checked) {
      const r2 = await postJSON('/api/task/start', { task_id: data.task_id });
      msg += r2.ok ? `，已提交启动（${r2.data.job_id || '-'}）` : `，启动失败：${r2.data.error || ''}`;
      pollJobs();
    } else {
      msg += '；可在右侧「待启动任务」手动启动';
    }
    out.className = 'hint s-done';
    out.textContent = msg;
    $('at-title').value = '';
    $('at-goal').value = '';
    $('at-ac').value = '';
    $('at-non').value = '';
    if ($('at-images')) $('at-images').value = '';
    if ($('at-thumbs')) $('at-thumbs').innerHTML = '';
    loadOverview(false);
  }
  btn.disabled = false;
});

/* ---------- 启动 ---------- */
async function boot() {
  const [mode, projects] = await Promise.all([getJSON('/api/mode'), getJSON('/api/projects')]);
  state.mode = mode.data || {};
  const list = (projects.data && projects.data.projects) || [];
  state.projects = list.length ? list : [{ name: (mode.data && mode.data.default_project) || 'river', label: '', db: true }];
  const names = state.projects.map(p => p.name);
  const saved = localStorage.getItem('ac.project');
  state.project = names.includes(saved) ? saved
    : ((projects.data && projects.data.default) || state.projects[0].name);
  renderProjectSelect();

  $('mode').textContent = state.mode.mode || '只读';
  $('mode').className = 'chip' + (state.mode.control ? ' chip-live' : '');
  if (state.mode.intake) $('newreq').hidden = false;

  const hash = (location.hash || '').replace(/^#\/?/, '');
  let page = ROUTES[hash] ? hash : 'overview';
  if (!location.hash) {
    if (location.pathname === '/intake') page = 'actions';
    else if (location.pathname === '/config') page = 'settings';
    if (page !== 'overview') history.replaceState(null, '', '/#/' + page);
  }
  setPage(page);
  await loadOverview(true);

  window.addEventListener('hashchange', () => {
    const p = (location.hash || '').replace(/^#\/?/, '');
    setPage(ROUTES[p] ? p : 'overview');
  });
  setInterval(() => loadOverview(false), 3000);
  if (state.mode.control) { pollJobs(); setInterval(pollJobs, 2000); }
}

boot();
