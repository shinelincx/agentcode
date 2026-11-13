'use strict';

/* 设置页：角色 / 预算 / 门禁 / 部署 / 审批 / 路径（GET/POST /api/config，按项目） */

const SettingsView = {
  cfg: null, meta: {}, editable: false, file: '', loadingFor: '',

  async load(project) {
    this.loadingFor = project;
    $('view').innerHTML = '<div class="empty">加载配置…</div>';
    const { ok, data } = await getJSON('/api/config');
    if (this.loadingFor !== state.project) return;  // 已切到其他项目
    if (!ok) {
      $('view').innerHTML = `<div class="empty">${esc(data.error || '配置读取失败')}</div>`;
      return;
    }
    this.cfg = data.config;
    this.meta = data.meta || {};
    this.meta.audit = data.audit || [];
    this.editable = !!data.editable;
    this.file = data.file || '';
    this.render();
  },

  get(path) {
    return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), this.cfg);
  },

  field(path, label, opts = {}) {
    const v = this.get(path);
    const dis = this.editable ? '' : 'disabled';
    const hint = opts.hint ? ` <span class="hint">${esc(opts.hint)}</span>` : '';
    if (opts.options) {
      return `<label class="field"><span>${esc(label)}${hint}</span>
        <select data-path="${path}" ${dis}>${opts.options.map(o =>
          `<option value="${esc(o)}" ${String(v) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
    }
    if (opts.type === 'lines') {
      return `<label class="field full"><span>${esc(label)}${hint}</span>
        <textarea data-path="${path}" data-type="list" rows="3" ${dis}>${esc((v || []).join('\n'))}</textarea></label>`;
    }
    if (opts.type === 'bool') {
      return `<label class="field"><span>${esc(label)}</span>
        <label class="chk"><input type="checkbox" data-path="${path}" data-type="bool"
          ${v ? 'checked' : ''} ${dis}> 启用</label></label>`;
    }
    const step = opts.step ? ` step="${opts.step}"` : '';
    const type = opts.type === 'number' ? 'number' : 'text';
    return `<label class="field"><span>${esc(label)}${hint}</span>
      <input data-path="${path}" data-type="${opts.type || 'text'}" type="${type}"${step}
        value="${esc(v ?? '')}" ${dis}></label>`;
  },

  checks(group, label, options) {
    const cur = this.get(group) || [];
    const dis = this.editable ? '' : 'disabled';
    return `<div class="field full"><span>${esc(label)}</span><div class="checks">${options.map(o =>
      `<label class="chk"><input type="checkbox" data-group="${group}" value="${esc(o)}"
        ${cur.includes(o) ? 'checked' : ''} ${dis}> ${esc(o)}</label>`).join('')}</div></div>`;
  },

  section(title, body, extra = '') {
    return `<section class="panel"><div class="panel-head"><h2>${esc(title)}</h2>${extra}</div>
      <div class="fields">${body}</div></section>`;
  },

  render() {
    const m = this.meta;
    const gates = m.gates || [];
    const cmds = m.gate_commands || [];
    const audit = (m.audit || []).length
      ? `<table><thead><tr><th>时间</th><th>操作人</th><th>变更</th></tr></thead><tbody>${
        m.audit.map(e => `<tr><td class="mono">${esc(e.at)}</td><td>${esc(e.actor)}</td>
          <td class="hint">${esc((e.changes || []).map(c => c.path).join(', '))}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">暂无配置变更记录</div>';

    $('view').innerHTML = `
      <div class="jobbar" id="cfg-msg" hidden></div>
      <div class="notice ${this.editable ? 'ok' : ''}">${esc(this.editable
        ? '控制台模式：修改后点「保存配置」，校验失败不会写入文件。'
        : '只读模式：配置可查看；写入需以 --control 启动控制台。')}</div>
      <form id="cfg-form">
        ${this.section('执行器 · executor',
          this.field('executor.primary', '主执行器', { options: m.executors || [] })
          + this.field('executor.fallback', '备用执行器', { options: m.executors || [] })
          + this.field('executor.codex_home', 'Codex 凭据目录', { hint: '须含 auth.json' }))}
        ${this.section('模型路由 · model',
          this.field('model.default', '默认模型')
          + this.field('model.degraded', '降级模型')
          + this.checks('model.strong_roles', '永不降级的角色', m.roles || []))}
        ${this.section('预算边界 · budget',
          this.field('budget.monthly_quota', '月度配额（calls）', { type: 'number' })
          + this.field('budget.max_usage_per_task', '单任务上限 U', { type: 'number' })
          + this.field('budget.max_inflight_tasks', '并发上限 N', { type: 'number' })
          + this.field('budget.thresholds.alert', 'alert 阈值', { type: 'number', step: '0.01' })
          + this.field('budget.thresholds.degraded', 'degraded 阈值', { type: 'number', step: '0.01' })
          + this.field('budget.thresholds.paused', 'paused 阈值', { type: 'number', step: '0.01' }))}
        ${this.section('测试门禁 · test / tech_stack.gate_commands',
          this.checks('test.gates', '启用的测试门禁', cmds)
          + cmds.map(name => this.field(`tech_stack.gate_commands.${name}`, `命令 · ${name}`)).join(''))}
        ${this.section('部署 staging · deploy.staging',
          this.field('deploy.staging.required', 'required', { type: 'bool' })
          + this.field('deploy.staging.timeout_seconds', '超时（秒）', { type: 'number' })
          + this.field('deploy.staging.healthcheck', '健康检查（URL 或命令）')
          + this.field('deploy.staging.commands', '部署命令（一行一条）',
            { type: 'lines', hint: 'WSL 构建需在同一会话内完成' }))}
        ${this.section('审批闸门 · gates',
          gates.map(g => this.field(`gates.${g}.approver`, `${g} 审批人`)
            + this.field(`gates.${g}.sla_hours`, `${g} SLA（小时）`, { type: 'number' })).join(''))}
        ${this.section('项目路径',
          this.field('repo_path', '业务仓库路径 repo_path')
          + this.field('workspace', '平台工作区 workspace'),
          `<span class="meta-text mono">${esc(this.file)}</span>`)}
        <div class="savebar" style="margin-top:16px">
          <button class="btn btn-primary" type="submit" id="cfg-save"
            ${this.editable ? '' : 'hidden'}>保存配置</button>
          <span class="hint">保存后写入 project.yaml（定点替换、保留注释）并刷新运行时。</span>
        </div>
      </form>
      <section class="panel"><div class="panel-head"><h2>最近配置变更 · config-audit.jsonl</h2></div>
        <div class="table-wrap">${audit}</div></section>`;
  },

  collect() {
    const updates = {};
    const put = (p, v) => {
      if (JSON.stringify(this.get(p)) !== JSON.stringify(v)) updates[p] = v;
    };
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
    Object.entries(groups).forEach(([g, els]) =>
      put(g, els.filter(e => e.checked).map(e => e.value)));
    return updates;
  },

  msg(text, cls = '') {
    const el = $('cfg-msg');
    if (!el) return;
    el.hidden = false;
    el.className = 'jobbar ' + cls;
    el.textContent = text;
  },

  async submit() {
    if (!this.editable) return;
    const updates = this.collect();
    const keys = Object.keys(updates);
    if (!keys.length) { this.msg('没有检测到变更'); return; }
    this.msg('保存中…');
    const { ok, data } = await postJSON('/api/config', { updates, by: 'console' });
    if (!ok) { this.msg('保存失败：' + (data.error || ''), 'jbad'); return; }
    this.msg(`已保存 ${data.changed.length} 项：${data.changed.join(', ')}`
      + (data.runtime_refreshed ? '（运行时已刷新）' : '（运行时刷新失败，需重启）'), 'jok');
    await this.load(state.project);
  },
};

document.addEventListener('submit', e => {
  if (e.target.id !== 'cfg-form') return;
  e.preventDefault();
  SettingsView.submit();
});
