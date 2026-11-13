'use strict';

/* 角色配置页：GET/POST /api/config（是否可编辑由接口 editable 决定） */

let CFG = null;
let META = null;
let EDITABLE = false;

const $ = id => document.getElementById(id);
const esc = s => (s ?? '').toString().replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const get = p => p.split('.').reduce((o, k) => (o == null ? undefined : o[k]), CFG);
const dis = () => (EDITABLE ? '' : 'disabled');

function field(path, label, opts = {}) {
  const v = get(path);
  const hint = opts.hint ? ` <span class="hint">${esc(opts.hint)}</span>` : '';
  if (opts.options) {
    return `<label class="field"><span>${esc(label)}${hint}</span>
      <select data-path="${path}" ${dis()}>${opts.options.map(o =>
        `<option value="${esc(o)}" ${String(v) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
  }
  if (opts.type === 'lines') {
    return `<label class="field full"><span>${esc(label)}${hint}</span>
      <textarea data-path="${path}" data-type="list" rows="3" ${dis()}>${esc((v || []).join('\n'))}</textarea></label>`;
  }
  if (opts.type === 'bool') {
    return `<label class="field"><span>${esc(label)}</span>
      <label class="chk"><input type="checkbox" data-path="${path}" data-type="bool" ${v ? 'checked' : ''} ${dis()}> 启用</label></label>`;
  }
  const step = opts.step ? ` step="${opts.step}"` : '';
  return `<label class="field"><span>${esc(label)}${hint}</span>
    <input data-path="${path}" data-type="${opts.type || 'text'}" type="${opts.type === 'number' ? 'number' : 'text'}"${step}
      value="${esc(v ?? '')}" ${dis()}></label>`;
}

function checks(group, label, options) {
  const cur = get(group) || [];
  return `<div class="field full"><span>${esc(label)}</span><div class="checks">${options.map(o =>
    `<label class="chk"><input type="checkbox" data-group="${group}" value="${esc(o)}" ${cur.includes(o) ? 'checked' : ''} ${dis()}> ${esc(o)}</label>`).join('')}</div></div>`;
}

function render() {
  $('proj').textContent = CFG.project || '-';
  $('file').textContent = META.file || '';
  $('modehint').textContent = EDITABLE
    ? '控制台模式：修改后点「保存配置」。校验失败不会写入文件。'
    : '只读模式：配置可查看；写入需以 --control 启动控制台。';

  $('f-executor').innerHTML =
    field('executor.primary', '主执行器', { options: META.executors }) +
    field('executor.fallback', '备用执行器', { options: META.executors }) +
    field('executor.codex_home', 'Codex 凭据目录', { hint: '须含 auth.json' });
  $('f-model').innerHTML =
    field('model.default', '默认模型') +
    field('model.degraded', '降级模型') +
    checks('model.strong_roles', '永不降级的角色', META.roles);
  $('f-budget').innerHTML =
    field('budget.monthly_quota', '月度配额（calls）', { type: 'number' }) +
    field('budget.max_usage_per_task', '单任务上限 U', { type: 'number' }) +
    field('budget.max_inflight_tasks', '并发上限 N', { type: 'number' }) +
    field('budget.thresholds.alert', 'alert 阈值', { type: 'number', step: '0.01' }) +
    field('budget.thresholds.degraded', 'degraded 阈值', { type: 'number', step: '0.01' }) +
    field('budget.thresholds.paused', 'paused 阈值', { type: 'number', step: '0.01' });
  $('f-test').innerHTML =
    checks('test.gates', '启用的测试门禁', META.gate_commands) +
    META.gate_commands.map(name =>
      field(`tech_stack.gate_commands.${name}`, `命令 · ${name}`)).join('');
  $('f-deploy').innerHTML =
    field('deploy.staging.required', 'required', { type: 'bool' }) +
    field('deploy.staging.timeout_seconds', '超时（秒）', { type: 'number' }) +
    field('deploy.staging.healthcheck', '健康检查（URL 或命令）') +
    field('deploy.staging.commands', '部署命令（一行一条）',
          { type: 'lines', hint: 'WSL 构建需在同一会话内完成' });
  $('f-gates').innerHTML = META.gates.map(g =>
    field(`gates.${g}.approver`, `${g} 审批人`) +
    field(`gates.${g}.sla_hours`, `${g} SLA（小时）`, { type: 'number' })).join('');
  $('f-paths').innerHTML =
    field('repo_path', '业务仓库路径 repo_path') +
    field('workspace', '平台工作区 workspace');

  $('audit').innerHTML = (META.audit || []).length
    ? `<table><thead><tr><th>时间</th><th>操作人</th><th>变更</th></tr></thead><tbody>${
      META.audit.map(e => `<tr><td class="mono">${esc(e.at)}</td><td>${esc(e.actor)}</td>
        <td class="hint">${esc((e.changes || []).map(c => c.path).join(', '))}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty">暂无配置变更记录</div>';
  $('save').style.display = EDITABLE ? '' : 'none';
}

function collect() {
  const updates = {};
  const put = (p, v) => { if (JSON.stringify(get(p)) !== JSON.stringify(v)) updates[p] = v; };
  document.querySelectorAll('[data-path]').forEach(el => {
    const type = el.dataset.type || 'text';
    let v;
    if (type === 'bool') v = el.checked;
    else if (type === 'number') {
      if (el.value.trim() === '') return;
      v = el.step && el.step.includes('.') ? parseFloat(el.value) : parseInt(el.value, 10);
    } else if (type === 'list') v = el.value.split('\n').map(s => s.trim()).filter(Boolean);
    else v = el.value.trim();
    put(el.dataset.path, v);
  });
  const groups = {};
  document.querySelectorAll('[data-group]').forEach(el => {
    (groups[el.dataset.group] = groups[el.dataset.group] || []).push(el);
  });
  Object.entries(groups).forEach(([g, els]) => put(g, els.filter(e => e.checked).map(e => e.value)));
  return updates;
}

function msg(text, cls = '') {
  const el = $('msg');
  el.hidden = false;
  el.className = 'jobbar ' + cls;
  el.textContent = text;
}

async function load() {
  const res = await fetch('/api/config', { cache: 'no-store' });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) { msg('配置读取失败：' + (d.error || res.status), 'jbad'); return; }
  CFG = d.config;
  META = d.meta || {};
  META.file = d.file || '';
  META.audit = d.audit || [];
  EDITABLE = !!d.editable;
  render();
}

$('cfg').addEventListener('submit', async e => {
  e.preventDefault();
  if (!EDITABLE) return;
  const updates = collect();
  const keys = Object.keys(updates);
  if (!keys.length) { msg('没有检测到变更'); return; }
  msg('保存中…');
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Control': '1' },
      body: JSON.stringify({ updates, by: 'console' }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { msg('保存失败：' + (d.error || res.status), 'jbad'); return; }
    msg(`已保存 ${d.changed.length} 项：${d.changed.join(', ')}`
      + (d.runtime_refreshed ? '（运行时已刷新）' : '（运行时刷新失败，需重启）'), 'jok');
    await load();
  } catch (err) { msg('请求异常：' + err, 'jbad'); }
});

load();
