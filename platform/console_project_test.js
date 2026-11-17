const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'console', 'app.js'), 'utf8')
  .replace(/\bboot\(\);\s*$/, '');

function consoleFixture() {
  const elements = new Map();
  const requests = [];
  const listeners = {};
  const context = vm.createContext({
    URL,
    location: { origin: 'http://console.test' },
    localStorage: { setItem() {} },
    document: {
      addEventListener(type, handler) { listeners[type] = handler; },
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, { id });
        return elements.get(id);
      },
    },
    fetch(url, options) {
      return new Promise(resolve => requests.push({
        url: new URL(url, 'http://console.test'),
        body: options?.body && JSON.parse(options.body),
        reply(data) { resolve({ ok: true, json: async () => data }); },
      }));
    },
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  run("state.project = 'alpha'; updateChrome = renderPage = refreshPage = () => {}");
  return { elements, requests, listeners, run };
}

test('a late response cannot replace the selected project overview', async () => {
  const { requests, run } = consoleFixture();
  const old = run('loadOverview()');
  const changed = run("switchProject('beta')");
  requests[1].reply({ project: 'beta' });
  await changed;
  requests[0].reply({ project: 'alpha' });
  await old;
  assert.equal(run('state.data.project'), 'beta');
});

test('a response from a previous visit to the same project is discarded', async () => {
  const { requests, run } = consoleFixture();
  const old = run('loadOverview()');
  const beta = run("switchProject('beta')");
  const alpha = run("switchProject('alpha')");
  requests[2].reply({ project: 'alpha', visit: 2 });
  requests[1].reply({ project: 'beta' });
  requests[0].reply({ project: 'alpha', visit: 1 });
  await Promise.all([old, beta, alpha]);
  assert.equal(run('state.data.visit'), 2);
});

test('a late settings response cannot render the previous project', async () => {
  const { requests, run } = consoleFixture();
  run(fs.readFileSync(path.join(__dirname, 'console', 'settings.js'), 'utf8'));
  run("state.page = 'settings'; SettingsView.render = () => {}");
  const old = run('SettingsView.load()');
  const changed = run("switchProject('beta')");
  const settings = run('SettingsView.load()');
  requests[1].reply({ project: 'beta' });
  requests[2].reply({ config: { project: 'beta' } });
  await Promise.all([changed, settings]);
  requests[0].reply({ config: { project: 'alpha' } });
  await old;
  assert.equal(run('SettingsView.cfg.project'), 'beta');
});

test('file encoding and auto-start keep the project from form submission', async () => {
  const { elements, requests, listeners, run } = consoleFixture();
  elements.set('at-content', { value: 'original request' });
  elements.set('at-prio', { value: 'normal' });
  elements.set('at-images', { files: [{ name: 'ref.png', type: 'image/png', size: 8 }] });
  elements.set('at-autostart', { checked: true });
  run('fileToBase64 = () => new Promise(resolve => { globalThis.finishImage = resolve; })');
  const submitted = listeners.submit({ target: { id: 'act-form' }, preventDefault() {} });
  const changed = run("switchProject('beta')");
  requests[0].reply({ project: 'beta' });
  await changed;
  run("finishImage('base64')");
  await new Promise(setImmediate);
  requests[1].reply({ task_id: 'alpha-task' });
  await new Promise(setImmediate);
  requests[2].reply({ job_id: 'alpha-job' });
  await submitted;

  for (const req of requests.slice(1, 3)) {
    assert.equal(req.url.searchParams.get('project'), 'alpha');
    assert.equal(req.body.project, 'alpha');
  }
  assert.equal(requests[2].body.task_id, 'alpha-task');
  assert.equal(elements.get('at-content').value, 'original request');
});
