'use strict';

/* 新建需求表单：POST /api/intake（可自动入队 start） */

const $ = id => document.getElementById(id);
const lines = s => s.split('\n').map(x => x.trim()).filter(Boolean);
const csv = s => s.split(',').map(x => x.trim()).filter(Boolean);

function notice(msg, cls = '') {
  const el = $('notice');
  el.hidden = false;
  el.className = 'jobbar ' + cls;
  el.textContent = msg;
}

async function boot() {
  try {
    const m = await (await fetch('/api/mode', { cache: 'no-store' })).json();
    if (!m.intake) {
      notice('当前为只读模式，无法新建需求；请以 --control 启动控制台。', 'jbad');
      $('submit').disabled = true;
    }
  } catch (e) { /* 忽略 */ }
}

$('f').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('submit'), out = $('result');
  const payload = {
    title: $('title').value.trim(),
    goal: $('goal').value.trim(),
    acceptance_criteria: lines($('ac').value),
    non_goals: $('non').value.trim(),
    scope: { be: csv($('be').value), fe: csv($('fe').value) },
    priority: $('prio').value,
  };
  btn.disabled = true;
  out.className = 'hint';
  out.textContent = '提交中…';
  try {
    const res = await fetch('/api/intake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Intake': '1' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      out.className = 'hint s-failed';
      out.textContent = '创建失败：' + (data.error || res.status);
    } else {
      let msg = '已创建 ' + data.task_id;
      if ($('autostart').checked) {
        const r2 = await fetch('/api/task/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Control': '1' },
          body: JSON.stringify({ task_id: data.task_id }),
        });
        const d2 = await r2.json().catch(() => ({}));
        msg += r2.ok
          ? '\n已提交启动（' + (d2.job_id || '-') + '），回控制台查看进度：/'
          : '\n启动失败：' + (d2.error || r2.status);
      } else {
        msg += '\n未启动。可在控制台任务表点「启动」。';
      }
      out.className = 'hint s-done';
      out.textContent = msg;
    }
  } catch (err) {
    out.className = 'hint s-failed';
    out.textContent = '请求异常：' + err;
  }
  btn.disabled = false;
});

boot();
