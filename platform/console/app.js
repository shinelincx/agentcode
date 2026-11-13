'use strict';

/* 交付平台管理控制台前端（静态资源，仅通过 /api/* 与后端交互） */

const $ = id => document.getElementById(id);
const esc = s => (s ?? '').toString().replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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

const MODE = { control: false, intake: false };

function table(head, rows, render) {
  if (!rows || !rows.length) return '<div class="empty">暂无数据</div>';
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

const pendingRows = d => d.pending_approvals || (d.approvals || []).filter(a => a.decision === 'pending');

function renderPending(d) {
  const rows = pendingRows(d);
  $('pending-count').textContent = rows.length;
  if (!rows.length) {
    $('pending').innerHTML = '<div class="empty">没有待审批的闸门。用「＋ 新建需求」创建任务并启动。</div>';
    return;
  }
  $('pending').innerHTML = rows.map(a => {
    const kind = KIND_OF[a.gate_name] || '';
    const art = ((d.gate_artifacts || {})[a.task_id] || {})[a.gate_name];
    const acts = [];
    if (kind) acts.push(`<button class="btn btn-sm" data-art="${esc(a.task_id)}" data-kind="${esc(kind)}">查看产物</button>`);
    if (MODE.control) {
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

function renderTasks(d) {
  const waiting = new Set(pendingRows(d).map(a => a.task_id));
  $('tasks').innerHTML = table(
    ['任务', '需求', '优先级', '状态', '当前节点', '流程', 'SLA', 'calls', '操作'],
    d.tasks, t => {
      const acts = [];
      if (MODE.control) {
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
        <td class="s-${esc(t.status)}" title="${esc(stateText(t.status))}">${esc(stateText(t.status))}</td>
        <td>${esc(t.current_node || '-')}</td>
        <td><div class="flow">${flow(t, d)}</div></td>
        <td class="${t.sla_overdue ? 's-failed' : ''}">${t.sla_wait_hours != null ? t.sla_wait_hours + 'h/' + t.sla_hours + 'h' : '-'}</td>
        <td class="mono">${t.calls}</td>
        <td><div class="acts">${acts.join('')}</div></td></tr>`;
    });
}

function renderAll(d) {
  $('proj').textContent = d.config.project;
  $('gen').textContent = d.generated_at;
  $('buf').textContent = `缓冲 ${d.buffer_pending}`;

  const st = $('state');
  st.textContent = d.budget.state;
  st.className = 'badge ' + d.budget.state;

  const pct = Math.min(100, d.budget.ratio * 100).toFixed(1);
  const fill = $('fill');
  fill.style.width = pct + '%';
  fill.className = 'fill ' + d.budget.state;
  $('usage').textContent = `${num(d.budget.calls)} / ${num(d.budget.quota)}`;
  $('usagepct').textContent = `${pct}%`;
  $('usagehint').textContent =
    `阈值 alert ${(d.config.alert * 100) | 0}% · degraded ${(d.config.degraded * 100) | 0}% · `
    + `paused ${(d.config.paused * 100) | 0}% · 单任务上限 U=${d.config.per_task}`;

  $('k-tasks').textContent = d.counts.tasks;
  $('k-running').textContent = d.counts.running;
  $('k-awaiting').textContent = d.counts.awaiting;
  $('k-approvals').textContent = d.counts.approvals;
  $('k-retro').textContent = d.counts.retro_pending;
  $('k-tokens').textContent = num(d.budget.tokens);

  renderPending(d);
  renderTasks(d);

  $('approvals').innerHTML = table(['任务', '闸门', '结论', '决策人', '时间'], d.approvals, a =>
    `<tr><td class="mono">${esc(a.task_id.slice(0, 8))}</td><td>${esc(a.gate_name)}</td>
     <td class="s-${decisionState(a.decision)}">${esc(decisionText(a.decision))}</td>
     <td>${esc(a.decided_by || '-')}</td><td class="meta-text">${esc(a.decided_at || a.created_at)}</td></tr>`);

  $('gates').innerHTML = table(['任务', '门禁', '结果', '时间'], d.gates, g =>
    `<tr><td class="mono">${esc(g.task_id.slice(0, 8))}</td><td>${esc(g.gate_name)}</td>
     <td class="p-${g.passed}">${g.passed ? 'PASS' : 'FAIL'}</td>
     <td class="meta-text">${esc(g.checked_at)}</td></tr>`);

  $('runs').innerHTML = table(['任务', '节点', 'lane', '尝试', '状态', '分支', '开始', '结束'], d.node_runs, r =>
    `<tr><td class="mono">${esc(r.task_id.slice(0, 8))}</td><td>${esc(r.node_name)}</td>
     <td>${esc(r.lane || '-')}</td><td>${r.attempt}</td>
     <td class="s-${esc(r.status)}">${esc(stateText(r.status))}</td>
     <td class="mono">${esc(r.branch || '-')}</td>
     <td class="meta-text">${esc(r.started_at)}</td><td class="meta-text">${esc(r.ended_at || '-')}</td></tr>`);

  $('lanes').innerHTML = table(['任务', 'lane', '顺序', '分支', '基线', '状态', '并入时间'], d.lanes || [], l =>
    `<tr><td class="mono">${esc(l.task_id.slice(0, 8))}</td><td>${esc(l.name)}</td><td>${l.merge_order}</td>
     <td class="mono">${esc(l.branch || '-')}</td><td class="mono">${esc(l.base_branch || '-')}</td>
     <td class="s-${esc(l.status)}">${esc(stateText(l.status))}</td>
     <td class="meta-text">${esc(l.merged_at || '-')}</td></tr>`);

  $('models').innerHTML = table(['模型', '执行器', 'calls', 'tokens'], d.usage_by_model, m =>
    `<tr><td class="mono">${esc(m.model)}</td><td>${esc(m.executor)}</td>
     <td>${num(m.calls)}</td><td>${num(m.tokens)}</td></tr>`);

  $('leases').innerHTML = table(['任务', '节点', 'Worker', '角色', 'host', 'epoch', '心跳', '到期', '状态'], d.leases || [], l =>
    `<tr><td class="mono">${esc(l.task_id.slice(0, 8))}</td><td>${esc(l.node_name)}</td>
     <td class="mono">${esc((l.worker_id || '').slice(0, 8))}</td><td>${esc(l.worker_role || '-')}</td>
     <td>${esc(l.host || '-')}</td><td class="mono">${l.lease_epoch}</td>
     <td class="meta-text">${esc(l.heartbeat_at || '-')}</td>
     <td class="meta-text">${esc(l.lease_expires_at || '-')}</td>
     <td class="${l.live ? 's-running' : 's-pending'}">${l.live ? '持有中' : '已释放/过期'}</td></tr>`);

  $('outbox').innerHTML = table(['任务', '队列#', '状态', '尝试', '下次重试', '最近错误', '投递时间'], d.outbox || [], o =>
    `<tr><td class="mono">${esc((o.task_id || '').slice(0, 8))}</td><td class="mono">${o.id}</td>
     <td class="${o.status === 'dead' ? 's-failed' : (o.status === 'done' ? 's-done' : 's-pending')}">${esc(o.status)}</td>
     <td>${o.attempts}</td><td class="meta-text">${esc(o.next_attempt_at || '-')}</td>
     <td class="meta-text" title="${esc(o.last_error || '')}">${esc((o.last_error || '-').slice(0, 40))}</td>
     <td class="meta-text">${esc(o.delivered_at || '-')}</td></tr>`);
}

async function tick() {
  try {
    const res = await fetch('/api/overview', { cache: 'no-store' });
    const d = await res.json();
    if (d.error) { toast(d.error, 'jbad'); return; }
    renderAll(d);
  } catch (e) { /* 网络抖动：下轮重试 */ }
}

function toast(msg, cls = 'jrun') {
  const bar = $('jobbar');
  bar.hidden = false;
  bar.className = 'jobbar ' + cls;
  bar.textContent = msg;
}

async function act(url, payload) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Control': '1' },
      body: JSON.stringify(payload),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) return toast('操作失败：' + (d.error || res.status), 'jbad');
    toast(`已提交 ${d.action || ''} · ${(d.task_id || '').slice(0, 8)}（${d.job_id || '-'}），执行中…`);
    pollJobs();
  } catch (e) { toast('请求异常：' + e, 'jbad'); }
}

async function showArtifact(taskId, kind) {
  try {
    const res = await fetch(`/api/artifact?task_id=${encodeURIComponent(taskId)}&kind=${encodeURIComponent(kind)}`);
    const d = await res.json();
    if (!res.ok) return toast('读取失败：' + (d.error || res.status), 'jbad');
    $('m-title').textContent = `${kind} · ${taskId.slice(0, 8)}`;
    if (kind === 'prototype_html') {
      // 沙箱 iframe：无 allow-same-origin，原型脚本无法触达控制台
      $('m-body').innerHTML = `<div class="meta-text mono">${esc(d.path)} · ${d.size} B</div>
        <iframe sandbox="allow-scripts" style="width:100%;height:70vh;border:1px solid var(--line);border-radius:10px;background:#fff;margin-top:8px"
          srcdoc="${esc(d.content)}"></iframe>`;
    } else {
      $('m-body').innerHTML = `<div class="meta-text mono">${esc(d.path)} · ${d.size} B</div>
        <pre class="doc">${esc(d.content)}</pre>`;
    }
    $('modal').hidden = false;
  } catch (e) { toast('请求异常：' + e, 'jbad'); }
}

async function showDetail(taskId) {
  try {
    const res = await fetch(`/api/task/detail?task_id=${encodeURIComponent(taskId)}`, { cache: 'no-store' });
    const d = await res.json();
    if (!res.ok) return toast('读取失败：' + (d.error || res.status), 'jbad');

    const arts = Object.entries(d.artifacts || {}).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td class="mono">${esc(v.path || '-')}</td>
       <td>${v.exists ? v.size + ' B' : '缺失'}</td>
       <td>${v.exists ? `<button class="btn btn-sm" data-art="${esc(taskId)}" data-kind="${esc(k)}">查看</button>` : ''}</td></tr>`).join('');
    const runs = (d.node_runs || []).map(r =>
      `<tr><td>${esc(r.node_name)}</td><td>${r.attempt}</td>
       <td class="s-${esc(r.status)}">${esc(stateText(r.status))}</td>
       <td>${esc(r.lane || '-')}</td><td class="meta-text">${esc(r.started_at)}</td>
       <td class="meta-text">${esc(r.ended_at || '-')}</td></tr>`).join('');
    const apps = (d.approvals || []).map(a =>
      `<tr><td>${esc(a.gate_name)}</td>
       <td class="s-${decisionState(a.decision)}">${esc(decisionText(a.decision))}</td>
       <td>${esc(a.decided_by || '-')}</td><td class="meta-text">${esc(a.decided_at || '-')}</td></tr>`).join('');

    $('m-title').textContent = `任务 ${taskId.slice(0, 8)}`;
    $('m-body').innerHTML =
      `<div class="meta-text">状态 ${esc(stateText(d.task.status))} · 当前节点 ${esc(d.task.current_node || '-')}
         · 重试 ${d.task.retry_count} · 创建 ${esc(d.task.created_at)}</div>
       <div style="margin:12px 0"><button class="btn btn-sm" data-rerun="${esc(taskId)}">从节点重跑…</button>
         <span class="meta-text">失败或已结束后可从指定节点复跑（不影响已提交分支）</span></div>
       <h3>节点时间线</h3>
       ${runs ? `<div class="table-wrap"><table><thead><tr><th>节点</th><th>尝试</th><th>状态</th><th>lane</th><th>开始</th><th>结束</th></tr></thead><tbody>${runs}</tbody></table></div>` : '<div class="empty">暂无节点记录</div>'}
       <h3>产物</h3>
       <div class="table-wrap"><table><thead><tr><th>类型</th><th>路径</th><th>大小</th><th></th></tr></thead><tbody>${arts}</tbody></table></div>
       <h3>审批</h3>
       ${apps ? `<div class="table-wrap"><table><thead><tr><th>闸门</th><th>结论</th><th>决策人</th><th>时间</th></tr></thead><tbody>${apps}</tbody></table></div>` : '<div class="empty">暂无审批记录</div>'}`;
    $('modal').hidden = false;
  } catch (e) { toast('请求异常：' + e, 'jbad'); }
}

async function pollJobs() {
  if (!MODE.control) return;
  try {
    const d = await (await fetch('/api/jobs', { cache: 'no-store' })).json();
    const j = (d.jobs || [])[0];
    const bar = $('jobbar');
    if (!j) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.className = 'jobbar ' + (j.status === 'failed' ? 'jbad' : j.status === 'done' ? 'jok' : 'jrun');
    bar.innerHTML = `<b>${esc(j.action)}</b> ${esc((j.task_id || '').slice(0, 8))}
      · ${esc(j.status)} · ${esc(j.message || '')}
      <span class="meta-text" style="margin-left:auto">${esc(j.queued_at)}</span>`;
  } catch (e) { /* 忽略轮询异常 */ }
}

document.addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (!btn) return;
  if (btn.id === 'm-close') { $('modal').hidden = true; return; }
  if (btn.dataset.art) return showArtifact(btn.dataset.art, btn.dataset.kind);
  if (btn.dataset.start) return act('/api/task/start', { task_id: btn.dataset.start });
  if (btn.dataset.decide) return act('/api/task/decide',
    { task_id: btn.dataset.task, decision: btn.dataset.decide, by: 'console' });
  if (btn.dataset.detail) return showDetail(btn.dataset.detail);
  if (btn.dataset.rerun) {
    const from = prompt('从哪个节点重跑？可用：requirements / design / prototype / implement / review / test / deploy / retro', 'test');
    if (!from) return;
    return act('/api/task/rerun', { task_id: btn.dataset.rerun, from: from.trim(), by: 'console' });
  }
});

async function boot() {
  try {
    const m = await (await fetch('/api/mode', { cache: 'no-store' })).json();
    MODE.control = !!m.control;
    MODE.intake = !!m.intake;
    $('mode').textContent = m.mode || '只读';
    $('mode').className = 'chip' + (MODE.control ? ' chip-live' : '');
    if (MODE.intake) $('newreq').hidden = false;
  } catch (e) { /* 模式未知时按只读渲染 */ }
  tick();
  setInterval(tick, 3000);
  if (MODE.control) { pollJobs(); setInterval(pollJobs, 2000); }
}

boot();
