// Run with: node tests/ui/interaction-behavior.cjs
// Executes the real TSX handlers with delayed API responses and a minimal hook host.
// No browser, user database, model endpoint, or native dialog is accessed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const model = { id: 'model', provider_id: 'provider', name: 'Test provider',
  model_name: 'test-model', enabled: true, verified_at: '2026-01-01',
  protocol_id: 'ollama', base_url: 'http://localhost', has_credential: false,
  supports_vision: false, vision_verified: false, max_concurrency: 3 };
const settings = { manual_model_id: 'model', diagram_model_id: 'model', vision_model_id: null,
  temperature: 0.3, max_output_tokens: 8192, generation_concurrency: 3,
  source_strategy: 'standard', auto_preview: true, document_style_prompt: 'document',
  diagram_style_prompt: 'diagram' };
const connection = { baseUrl: 'http://fixture.invalid', sessionToken: 'fixture' };
const text = (node) => node == null || typeof node === 'boolean' ? '' :
  typeof node !== 'object' ? String(node) : Array.isArray(node) ? node.map(text).join('') : text(node.props?.children);

function mount(name, apiOverrides = {}, initialProps = {}) {
  const slots = [], effects = [], intervals = new Map(), intervalDurations = new Map(), revoked = [];
  const componentStubs = new Map();
  const windowListeners = new Map(), timeouts = new Map(); let timeoutId = 0;
  let index = 0, dirty = true, mounted = true, tree, props = { connection, ...initialProps }, intervalId = 0, uuidCounter = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((item, i) => Object.is(item, b[i]));
  const useEffect = (effect, deps) => {
    const i = index++, old = slots[i];
    if (!old || !same(old.deps, deps)) {
      slots[i] = { deps, cleanup: old?.cleanup };
      effects.push(() => { slots[i].cleanup?.(); slots[i].cleanup = effect(); });
    }
  };
  const react = {
    useState(initial) {
      const i = index++;
      if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, (next) => {
        if (!mounted) return;
        const value = typeof next === 'function' ? next(slots[i].value) : next;
        if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; }
      }];
    },
    useRef(value) { const i = index++; return slots[i] ||= { current: value }; },
    useEffect, useLayoutEffect: useEffect,
    useMemo(factory, deps) { const i = index++; if (!slots[i] || !same(slots[i].deps, deps))
      slots[i] = { deps, value: factory() }; return slots[i].value; },
  };
  const api = { listModelConfigs: async () => [model], loadAppSettings: async () => ({ ...settings }),
    hasModelCredential: async () => true, listFormalManualJobs: async () => [],
    listFormalManualDocuments: async () => [], listQuickStartRuns: async () => [], ...apiOverrides };
  const jsx = (type, properties, key) => ({ type, props: properties || {}, key: key === undefined ? null : String(key) });
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(root, `ui/${name}.tsx`), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const localRequire = (id) => {
    if (id === 'react') return react;
    if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
    if (id === './api') return api;
    if (id.endsWith('.json')) return require(path.resolve(root, 'ui', id));
    if (id === '@tauri-apps/plugin-dialog') return { open: async () => null, save: async () => null,
      ...apiOverrides.__dialog };
    return new Proxy({}, { get: (_, key) => {
      const idKey = `${id}:${String(key)}`;
      if (!componentStubs.has(idKey)) componentStubs.set(idKey,
        Object.defineProperty((properties) => jsx(String(key), properties), 'name', { value: String(key) }));
      return componentStubs.get(idKey);
    } });
  };
  vm.runInNewContext(source, { exports, require: localRequire, console, structuredClone,
    crypto: { randomUUID: () => `fixture-uuid-${++uuidCounter}` }, URL: { revokeObjectURL: (url) => revoked.push(url) },
    window: { confirm: () => true, prompt: () => 'Fixture reviewed', scrollTo() {},
      addEventListener(type, listener) {
        if (!windowListeners.has(type)) windowListeners.set(type, new Set());
        windowListeners.get(type).add(listener);
      },
      removeEventListener(type, listener) { windowListeners.get(type)?.delete(listener); },
      requestAnimationFrame: () => 1, cancelAnimationFrame() {},
      setTimeout: (fn, duration) => {
        if (!apiOverrides.__controlTimers) { fn(); return 1; }
        timeouts.set(++timeoutId, { fn, duration }); return timeoutId;
      },
      clearTimeout: (id) => timeouts.delete(id),
      setInterval: (fn, duration) => {
        intervals.set(++intervalId, fn); intervalDurations.set(intervalId, duration); return intervalId;
      },
      clearInterval: (id) => { intervals.delete(id); intervalDurations.delete(id); } },
    document: { addEventListener() {}, removeEventListener() {}, elementFromPoint: () => null },
    ResizeObserver: class { observe() {} disconnect() {} }, CSS: { escape: (x) => x },
  }, { filename: `${name}.tsx` });
  const host = {
    render() { index = 0; dirty = false; tree = exports[name](props); while (effects.length) effects.shift()(); },
    async flush() { for (let i = 0; i < 16; i++) { if (dirty) host.render(); await new Promise(setImmediate); } },
    nodes(predicate) {
      const result = [];
      const walk = (node) => { if (Array.isArray(node)) return node.forEach(walk);
        if (!node || typeof node !== 'object') return;
        if (predicate(node)) result.push(node); walk(node.props?.children); };
      walk(tree); return result;
    },
    button(label) { const button = host.nodes((node) => node.type === 'button' && text(node).includes(label))[0];
      assert.ok(button, `Missing button: ${label}`); return button; },
    async click(label) { const button = host.button(label); assert.ok(!button.props.disabled, `Disabled: ${label}`);
      const pending = button.props.onClick(); await host.flush(); return pending; },
    text: () => text(tree), intervals, revoked,
    async tickInterval(duration) {
      for (const [id, tick] of intervals) if (intervalDurations.get(id) === duration) await tick();
      await host.flush();
    },
    async runTimeout(duration) {
      for (const [id, timer] of [...timeouts]) if (timer.duration === duration) { timeouts.delete(id); timer.fn(); }
      await host.flush();
    },
    async dispatchWindowEvent(type, event) {
      for (const listener of windowListeners.get(type) || []) listener(event);
      await host.flush();
    },
    timeouts,
    update(next) { props = { ...props, ...next }; dirty = true; },
    unmount() { mounted = false; for (const slot of slots) slot?.cleanup?.(); tree = null; dirty = false; },
  };
  host.render(); return host;
}

function quickRun(status = 'failed') {
  return { id: 'run', task_id: 'task', manual_job: null, status, current_stage: 'manual',
    created_at: '2026-09-01', started_at: null, finished_at: null, outputs: {},
    safe_error_message: 'Temporary fixture failure',
    config: { software_name: 'Fixture project', version: 'V1.0', concurrency: 3, retry_limit: 2 },
    stages: [{ key: 'manual', title: '生成软件说明书', status: status === 'running' ? 'running' : 'failed',
      attempt: 1, events: [] }] };
}

async function testSettingsPreserveUnsavedPreferences() {
  const request = deferred();
  const host = mount('Settings', { testModelConnection: () => request.promise,
    saveModelEndpointMode: async () => {}, markModelVerified: async () => {} });
  await host.flush(); await host.click('生成偏好');
  let temperature = host.nodes((n) => n.type === 'input' && n.props.step === '0.1')[0];
  temperature.props.onChange({ target: { value: '0.9' } }); await host.flush();
  await host.click('Test provider');
  const pending = host.button('测试连接').props.onClick(); await host.flush();
  request.resolve({ elapsedMs: 1, endpointMode: 'chat_completions' }); await pending; await host.flush();
  await host.click('生成偏好');
  temperature = host.nodes((n) => n.type === 'input' && n.props.step === '0.1')[0];
  assert.equal(temperature.props.value, 0.9, 'Testing a model must preserve unsaved generation preferences');
}

async function testModelOperationsCannotOverlapOrDelete() {
  const request = deferred(); let tests = 0, deletes = 0;
  const host = mount('Settings', { saveModelVisionCapability: () => request.promise,
    testModelConnection: async () => { tests++; }, deleteModelConfig: async () => { deletes++; } });
  await host.flush();
  const test = host.button('测试连接').props.onClick, remove = host.button('删除').props.onClick;
  const pending = host.button('验证并启用图片能力').props.onClick();
  await test(); await remove(); await host.flush();
  assert.equal(tests, 0); assert.equal(deletes, 0);
  assert.equal(host.button('测试连接').props.disabled, true);
  request.resolve(); await pending; await host.flush();
  assert.equal(host.button('测试连接').props.disabled, false);
}

async function testFailedSettingsLoadCannotOverwriteDefaults() {
  let saves = 0;
  const host = mount('Settings', { loadAppSettings: async () => { throw new Error('Fixture unavailable'); },
    saveAppSettings: async () => { saves++; } });
  await host.flush(); await host.click('生成偏好');
  assert.equal(host.button('保存通用设置').props.disabled, true);
  await host.button('保存通用设置').props.onClick();
  assert.equal(saves, 0); assert.ok(host.button('重新读取设置'));
}

async function testDiscoveryNeverReplacesSavedCredential() {
  for (const fail of [false, true]) {
    const credentials = new Map([['provider', 'existing-fixture-token']]);
    const probe = deferred(); let probeId;
    const host = mount('Settings', {
      listModelConfigs: async () => [{ ...model, protocol_id: 'openai_compatible', has_credential: true }],
      storeModelCredential: async (id, value) => { credentials.set(id, value); },
      deleteModelCredential: async (id) => { credentials.delete(id); },
      probeModelConfig: ({ configId }) => { probeId = configId; return probe.promise; },
    });
    await host.flush(); await host.click('编辑配置');
    const password = host.nodes((n) => n.type === 'input' && n.props.type === 'password')[0];
    password.props.onChange({ target: { value: 'new-fixture-token' } }); await host.flush();
    const pending = host.button('自动获取模型').props.onClick(); await host.flush();
    assert.notEqual(probeId, 'provider');
    assert.equal(credentials.get('provider'), 'existing-fixture-token');
    assert.equal(credentials.get(probeId), 'new-fixture-token');
    if (fail) probe.reject(new Error('Fixture probe failure'));
    else probe.resolve({ normalizedBaseUrl: 'http://localhost', discoveredModels: ['test-model'] });
    await pending; await host.flush();
    assert.equal(credentials.size, 1, 'Temporary credentials must be removed on success and failure');
    await host.click('返回'); assert.equal(credentials.get('provider'), 'existing-fixture-token');
  }
}

async function testQuickRecoveryKeepsIdentityAndHistory() {
  const request = deferred(); let retries = 0, creates = 0, removes = 0;
  const host = mount('QuickStart', { listQuickStartRuns: async () => [quickRun()],
    retryQuickStartRun: (_, id) => { assert.equal(id, 'run'); retries++; return request.promise; },
    createQuickStartRun: async () => { creates++; }, discardQuickStartRun: async () => { removes++; } },
  { onTaskChange() {}, onNavigate() {}, onOpenAssets() {}, onOpenSettings() {} });
  await host.flush();
  assert.equal(host.nodes((n) => n.type === 'button' && text(n).includes('开始生成材料')).length, 0,
    'A failed run must show recovery, not another start form');
  const retry = host.button('已处理，继续生成').props.onClick;
  const pending = retry(); await retry(); assert.equal(retries, 1);
  request.resolve({ ...quickRun(), status: 'completed' }); await pending; await host.flush();
  await host.click('新建生成任务');
  assert.equal(creates, 0); assert.equal(removes, 0);
  assert.ok(host.text().includes('历史快速任务 · 1 个'));
  assert.ok(host.button('开始生成材料'));
}

async function testQuickProgressUpdatesHistoryWithoutOverlappingPolls() {
  const request = deferred(); let calls = 0;
  const host = mount('QuickStart', { listQuickStartRuns: async () => [quickRun('running')],
    loadQuickStartRun: () => { calls++; return request.promise; } },
  { onTaskChange() {}, onNavigate() {}, onOpenAssets() {}, onOpenSettings() {} });
  await host.flush();
  for (const tick of host.intervals.values()) { void tick(); void tick(); }
  assert.equal(calls, 1, 'A slow progress request must not accumulate polls');
  request.resolve({ ...quickRun(), status: 'completed' }); await host.flush();
  const history = host.nodes((n) => n.type === 'details' && n.props.className === 'quick-history')[0];
  assert.ok(text(history).includes('已完成')); assert.ok(!text(history).includes('运行中'));
}

async function testQualityBlockersLeadToRepairBeforeRetry() {
  let retries = 0, destination = '';
  const run = quickRun();
  run.current_stage = 'finalize';
  run.stages = [{ key: 'finalize', title: '装配双文档', status: 'failed', attempt: 1,
    output: { failure: { document_version: 1, failed_checks: [{ key: 'content.placeholders' }] } } }];
  const host = mount('QuickStart', { listQuickStartRuns: async () => [run],
    retryQuickStartRun: async () => { retries++; } }, { onTaskChange() {},
    onNavigate(page) { destination = page; }, onOpenAssets() {}, onOpenSettings() {} });
  await host.flush();
  assert.equal(host.button('查看并处理问题').props.className, 'primary');
  await host.click('查看并处理问题');
  assert.equal(destination, 'manual'); assert.equal(retries, 0);
  assert.notEqual(host.button('已处理，继续生成').props.className, 'primary');
}

async function testManualProjectSwitchDiscardsOldResponses() {
  const old = deferred();
  const host = mount('ManualWorkspace', { listFormalManualJobs: (_, taskId) => taskId === 'old' ? old.promise : Promise.resolve([]) },
    { taskId: 'old', trackedJob: null, onTaskChange() {}, onOpenDiagrams() {}, onOpenScreenshots() {} });
  await host.flush(); host.update({ taskId: 'new' }); await host.flush();
  old.resolve([{ id: 'old-job', task_id: 'old', version: 91, status: 'failed', nodes: [], steps: [],
    updated_at: '2026-09-01', progress: { completed: 0, total: 1, percent: 0 } }]);
  await host.flush();
  assert.ok(!host.text().includes('v91'), 'Old project response must not populate the new project');
  assert.ok(host.button('开始生成说明书'));
}

async function testFailedQaCannotFinalizeAndClosedPreviewStaysClosed() {
  const page = deferred();
  const document = { id: 'document', job_id: 'job', task_id: 'task', version: 1,
    document_kind: 'formal_candidate', filename: 'fixture.docx', project_name: 'Fixture',
    project_version: 'V1.0', created_at: '2026-09-01', integrity: { status: 'verified', size_bytes: 1000 },
    freshness: { status: 'current' }, quality: { status: 'failed' },
    qa: { section_count: 8, figure_count: 2, screenshot_count: 1, warnings: [] } };
  const qa = { passed: false, page_count: 2, checks: [], decisions: [],
    summary: { warning_count: 0, blocker_count: 1, underfilled_pages: [] } };
  const host = mount('ManualWorkspace', {
    listFormalManualJobs: async () => [{ id: 'job', task_id: 'task', version: 1, status: 'completed',
      nodes: [], steps: [], updated_at: '2026-09-01', progress: { completed: 1, total: 1, percent: 100 } }],
    listFormalManualDocuments: async () => [document], loadFormalManualQa: async () => qa,
    loadFormalManualQaPage: () => page.promise,
  }, { taskId: 'task', trackedJob: null, onTaskChange() {}, onOpenDiagrams() {}, onOpenScreenshots() {} });
  await host.flush();
  assert.equal(host.nodes((n) => n.type === 'button' && text(n) === '生成终稿').length, 0,
    'A failed quality gate must not expose a finalize override');
  const pending = host.button('逐页预览').props.onClick(); await host.flush();
  await host.click('关闭');
  page.resolve('blob:late-preview'); await pending; await host.flush();
  assert.equal(host.nodes((n) => n.props.className?.includes('manual-document-viewer')).length, 0);
  assert.ok(host.revoked.includes('blob:late-preview'), 'A late preview blob must be released');
}

async function testManualEditingIsLockedWhileSaving() {
  const saved = deferred(); let saves = 0;
  const section = { section_key: 'overview', title: 'Original title', status: 'generated',
    blocks: [{ type: 'paragraph', text: 'Original paragraph' }] };
  const doc = { id: 'doc', job_id: 'job', task_id: 'task', version: 1, document_kind: 'formal_candidate',
    project_name: 'Fixture', project_version: 'V1.0', filename: 'fixture.docx', created_at: '2026-09-01',
    integrity: { status: 'verified', size_bytes: 1000 }, freshness: { status: 'current' },
    quality: { status: 'passed' }, qa: { section_count: 1, figure_count: 0, screenshot_count: 0 } };
  const host = mount('ManualWorkspace', {
    listFormalManualJobs: async () => [{ id: 'job', task_id: 'task', version: 1, status: 'completed',
      nodes: [], steps: [], updated_at: '2026-09-01', progress: { completed: 1, total: 1, percent: 100 } }],
    listFormalManualDocuments: async () => [doc],
    loadFormalManualPreview: async () => ({ sections: [section] }),
    editFormalManualSection: () => { saves++; return saved.promise; },
  }, { taskId: 'task', trackedJob: null, onTaskChange() {}, onOpenDiagrams() {}, onOpenScreenshots() {} });
  await host.flush(); await host.click('编辑内容');
  let title = host.nodes((n) => n.type === 'input' && n.props.value === 'Original title')[0];
  title.props.onChange({ target: { value: 'Saved draft' } }); await host.flush();
  const save = host.button('保存本章修订').props.onClick;
  const pending = save(); await save(); await host.flush();
  assert.equal(saves, 1, 'A rapid repeated save must not create duplicate revisions');
  title = host.nodes((n) => n.type === 'input' && n.props.value === 'Saved draft')[0];
  assert.equal(title.props.disabled, true);
  assert.ok(host.nodes((n) => typeof n.type === 'function' && n.type.name === 'BlockEditor')
    .every((n) => n.props.disabled === true));
  const navigation = host.nodes((n) => n.type === 'button' && n.props.className === 'active'
    && text(n).includes('Saved draft'))[0];
  assert.equal(navigation.props.disabled, true);
  title.props.onChange({ target: { value: 'Late edit from stale event' } }); await host.flush();
  saved.resolve({ ...section, title: 'Saved draft', version: 2 }); await pending; await host.flush();
  title = host.nodes((n) => n.type === 'input' && n.props.value === 'Saved draft')[0];
  assert.ok(title); assert.equal(title.props.disabled, false);
}

async function testAppReconnectPreservesWorkspaceIdentityAndUnsavedChapter() {
  for (const workspace of ['ManualWorkspace', 'FormalDiagramWorkspace']) {
    const first = { baseUrl: 'http://first.invalid', sessionToken: 'first-fixture', version: '1.0' };
    const replacement = { baseUrl: 'http://reconnected.invalid', sessionToken: 'next-fixture', version: '1.1' };
    const reconnect = deferred(); let connects = 0, healthy = true;
    const app = mount('App', {
      connectSidecar: () => ++connects === 1 ? Promise.resolve(first) : reconnect.promise,
      checkSidecarHealth: async () => healthy,
      listRecentTasks: async () => [{ task_id: 'task' }],
    });
    await app.flush(); await app.click(workspace === 'ManualWorkspace' ? '说明书' : '图表资产');
    const child = () => app.nodes((n) => typeof n.type === 'function' && n.type.name === workspace)[0];
    const original = child();
    assert.ok(original); assert.equal(original.key, 'task', 'The harness must retain the actual React child key');
    assert.equal(original.props.connection, first);

    let manual, versionLoads = 0;
    if (workspace === 'ManualWorkspace') {
      const section = { section_key: 'overview', title: 'Original title', status: 'generated',
        blocks: [{ type: 'paragraph', text: 'Original paragraph' }] };
      const document = { id: 'doc', job_id: 'job', task_id: 'task', version: 1,
        document_kind: 'formal_candidate', project_name: 'Fixture', project_version: 'V1.0',
        filename: 'fixture.docx', created_at: '2026-09-01', integrity: { status: 'verified', size_bytes: 1000 },
        freshness: { status: 'current' }, quality: { status: 'passed' },
        qa: { section_count: 1, figure_count: 0, screenshot_count: 0 } };
      // Execute the real child hooks with the real props produced by App. This checks state retention,
      // not browser reconciliation or DOM behavior; key identity is asserted separately above/below.
      manual = mount('ManualWorkspace', {
        listFormalManualJobs: async () => { versionLoads++; return [{ id: 'job', task_id: 'task',
          version: 1, status: 'completed', nodes: [], steps: [], updated_at: '2026-09-01',
          progress: { completed: 1, total: 1, percent: 100 } }]; },
        listFormalManualDocuments: async () => [document],
        loadFormalManualPreview: async () => ({ sections: [section] }),
      }, original.props);
      await manual.flush(); await manual.click('编辑内容');
      manual.nodes((n) => n.type === 'input' && n.props.value === 'Original title')[0]
        .props.onChange({ target: { value: 'Unsaved correction' } });
      await manual.flush();
    }
    const assertWorkspacePreserved = async () => {
      const current = child();
      assert.equal(current.key, original.key, 'A health transition must not remount the workspace');
      assert.equal(current.type, original.type);
      assert.equal(current.props.connection, first, 'A health transition must retain the connection object');
      assert.equal(current.props.taskId, 'task');
      if (manual) {
        manual.update(current.props); await manual.flush();
        assert.ok(manual.nodes((n) => n.type === 'input' && n.props.value === 'Unsaved correction').length,
          'Unsaved chapter edits must survive disconnect, reconnect and recovered health');
        assert.equal(versionLoads, 1, 'Health transitions must not reinitialize the manual workspace');
      }
    };

    healthy = false;
    await app.tickInterval(8000); await assertWorkspacePreserved();
    assert.equal(app.nodes((n) => n.props.className === 'reconnect-button').length, 0,
      'One transient failed health check must not mark the service offline');
    await app.tickInterval(8000); await assertWorkspacePreserved();
    assert.equal(app.button('重新连接本地服务').props.disabled, false);
    assert.ok(app.text().includes('本地服务连接已中断'));
    await app.click('重新连接本地服务');
    assert.equal(app.button('正在连接…').props.disabled, true);
    await assertWorkspacePreserved();
    healthy = true; reconnect.resolve(replacement); await app.flush();
    await assertWorkspacePreserved();
    assert.equal(connects, 2);
    assert.equal(first.baseUrl, replacement.baseUrl); assert.equal(first.sessionToken, replacement.sessionToken);
    assert.equal(app.nodes((n) => n.props.className === 'reconnect-button').length, 0);

    // A later transient outage can recover through health polling without changing child identity either.
    healthy = false; await app.tickInterval(8000); await app.tickInterval(8000);
    assert.ok(app.button('重新连接本地服务')); await assertWorkspacePreserved();
    healthy = true; await app.tickInterval(8000); await assertWorkspacePreserved();
    assert.equal(app.nodes((n) => n.props.className === 'reconnect-button').length, 0);
    assert.equal(connects, 2, 'Health recovery itself must not restart the service');
    manual?.unmount(); app.unmount();
  }
}

function overviewFixture(taskId = 'task') {
  const summary = { file_count: 3, ignored_count: 1, total_bytes: 1000, secret_finding_count: 0,
    languages: ['TypeScript'] };
  const inspection = { task: { status: 'waiting_for_user' }, facts: [{ key: 'project.name',
    value: `Project ${taskId}`, confidence: 1 }], confirmations: [{ field_key: 'project.version',
    status: 'pending', question: '确认软件版本', candidates: ['V1.0'] }] };
  return { result: { task_id: taskId, snapshot_id: `snapshot-${taskId}`, summary, inspection },
    recent: { task_id: taskId, snapshot_id: `snapshot-${taskId}`, summary, source_kind: 'directory',
      status: 'waiting_for_user', display_name: `Project ${taskId}` } };
}

async function testDrawioLoadTimeoutAndReconnectPreserveCurrentXml() {
  let saves = 0; const changes = [], messages = [];
  const host = mount('DrawioEditor', { __controlTimers: true }, { title: 'Fixture diagram',
    xml: '<initial/>', onSave: async () => { saves++; return { version: 1, message: 'Saved' }; },
    onXmlChange: (value) => changes.push(value), canUndoAi: false, hasUnconfirmedChanges: false,
    onUndoAi() {}, onRestoreConfirmed() {} });
  const frame = () => host.nodes((n) => n.type === 'iframe')[0];
  const firstWindow = { postMessage: (value) => messages.push(JSON.parse(value)) };
  frame().props.ref.current = { contentWindow: firstWindow };
  const originalKey = frame().key;
  host.update({ xml: '<latest-before-init/>' }); await host.flush();
  await host.runTimeout(25000);
  assert.ok(host.text().includes('网络连接尚未就绪'));
  assert.ok(host.button('重新连接编辑器'));
  assert.equal(frame().key, originalKey, 'A timeout must not retry the iframe automatically');
  await host.dispatchWindowEvent('message', { origin: 'https://embed.diagrams.net', source: firstWindow,
    data: JSON.stringify({ event: 'init' }) });
  assert.equal(messages.find((item) => item.action === 'load').xml, '<latest-before-init/>',
    'First init must load the latest supplied XML rather than the mount closure');
  assert.equal(host.nodes((n) => n.type === 'button' && text(n) === '重新连接编辑器').length, 0);
  assert.equal(host.timeouts.size, 0, 'Init must cancel the load timeout');
  await host.dispatchWindowEvent('message', { origin: 'https://embed.diagrams.net', source: firstWindow,
    data: JSON.stringify({ event: 'autosave', xml: '<unsaved-canvas/>' }) });
  assert.deepEqual(changes, ['<unsaved-canvas/>']);

  // Keep a legitimate timeout button handler, then let a late init/save happen before its stale event runs.
  const waiting = mount('DrawioEditor', { __controlTimers: true }, { title: 'Waiting diagram',
    xml: '<preserved/>', onSave: async () => { saves++; }, onXmlChange() {}, canUndoAi: false,
    hasUnconfirmedChanges: false, onUndoAi() {}, onRestoreConfirmed() {} });
  const waitingFrame = () => waiting.nodes((n) => n.type === 'iframe')[0];
  const waitingWindow = { postMessage: (value) => messages.push(JSON.parse(value)) };
  waitingFrame().props.ref.current = { contentWindow: waitingWindow };
  await waiting.runTimeout(25000);
  const reconnect = waiting.button('重新连接编辑器').props.onClick;
  // Autosaved state can arrive during a slow editor handshake and must survive a manual reconnect.
  await waiting.dispatchWindowEvent('message', { origin: 'https://embed.diagrams.net', source: waitingWindow,
    data: JSON.stringify({ event: 'autosave', xml: '<latest-unsaved/>' }) });
  await waiting.click('重新连接编辑器');
  assert.notEqual(waitingFrame().key, '0');
  const secondWindow = { postMessage: (value) => messages.push(JSON.parse(value)) };
  waitingFrame().props.ref.current = { contentWindow: secondWindow };
  const beforeOldInit = messages.length;
  await waiting.dispatchWindowEvent('message', { origin: 'https://embed.diagrams.net', source: waitingWindow,
    data: JSON.stringify({ event: 'init' }) });
  assert.equal(messages.length, beforeOldInit, 'Late messages from the old frame must be ignored');
  await waiting.dispatchWindowEvent('message', { origin: 'https://embed.diagrams.net', source: secondWindow,
    data: JSON.stringify({ event: 'init' }) });
  assert.equal(messages.at(-1).xml, '<latest-unsaved/>', 'Reconnect must preserve current canvas XML');
  assert.equal(waiting.timeouts.size, 0);
  const loadedKey = waitingFrame().key;
  await waiting.click('确认并装配说明书');
  reconnect(); await waiting.flush();
  assert.equal(waitingFrame().key, loadedKey, 'A stale reconnect event cannot interrupt saving');
  assert.equal(saves, 0, 'Loading/reconnecting must not invoke document saving or generation');
  waiting.unmount(); host.unmount();
  assert.equal(waiting.timeouts.size, 0, 'Unmount must clean up all pending editor timers');
  assert.equal(host.timeouts.size, 0);
}

async function testOverviewLocksScanOpenAndConfirmBeforeRerender() {
  const scanned = deferred(), confirmed = deferred(); let scans = 0, opens = 0, answers = 0;
  const fixture = overviewFixture(); const navigations = [];
  const host = mount('ProjectOverview', {
    listRecentTasks: async () => [fixture.recent],
    scanProject: () => { scans++; return scanned.promise; },
    loadInspection: async () => { opens++; return fixture.result.inspection; },
    answerConfirmation: () => { answers++; return confirmed.promise; },
    __dialog: { open: async () => '/fixture/project' },
  }, { ensureConnection: async () => connection, onTaskCreated: (id) => navigations.push(id) });
  await host.flush(); await host.click('选择项目目录');
  const openRecent = host.button('Project task').props.onClick;
  const scan = host.button('开始本地扫描').props.onClick;
  const scanning = scan(); await scan(); await openRecent(); await host.flush();
  assert.equal(scans, 1, 'Double click must create only one scan before React rerenders');
  assert.equal(opens, 0, 'Opening a recent task cannot overlap the scan');
  assert.equal(host.button('选择项目目录').props.disabled, true);
  scanned.resolve(fixture.result); await scanning; await host.flush();
  assert.deepEqual(navigations, ['task']);
  const confirm = host.nodes((n) => n.type === 'button' && text(n) === '确认')[0].props.onClick;
  const confirming = confirm(); await confirm(); await scan(); await host.flush();
  assert.equal(answers, 1, 'Double confirmation must submit only one answer');
  assert.equal(scans, 1, 'A stale scan handler cannot overlap confirmation');
  assert.equal(host.nodes((n) => n.type === 'input' && n.props.value === 'V1.0')[0].props.disabled, true);
  confirmed.resolve({ inspection: { ...fixture.result.inspection, confirmations: [] }, remaining_required: 0 });
  await confirming; await host.flush();
  assert.ok(host.text().includes('全部必填信息已确认')); assert.equal(host.button('开始本地扫描').props.disabled, false);
  host.unmount();
}

async function testOverviewIgnoresResponsesAfterLeavingOrReplacingConnection() {
  for (const action of ['scan', 'recent']) {
    const old = deferred(), destinations = [];
    const before = overviewFixture('old'), after = overviewFixture('new'); let calls = 0;
    const host = mount('ProjectOverview', {
      listRecentTasks: async () => [before.recent],
      scanProject: () => { calls++; return old.promise; },
      loadInspection: () => { calls++; return old.promise; },
      __dialog: { open: async () => '/fixture/project' },
    }, { ensureConnection: async () => connection, onTaskCreated: (id) => destinations.push(id) });
    await host.flush();
    if (action === 'scan') await host.click('选择项目目录');
    const start = host.button(action === 'scan' ? '开始本地扫描' : 'Project old').props.onClick;
    const pending = start(); await start(); await host.flush();
    assert.equal(calls, 1, `${action}: repeated action must be locked synchronously`);
    host.unmount();
    const current = mount('ProjectOverview', { listRecentTasks: async () => [after.recent],
      loadInspection: async () => after.result.inspection,
    }, { ensureConnection: async () => connection, onTaskCreated: (id) => destinations.push(id) });
    await current.flush(); await current.click('Project new');
    old.resolve(action === 'scan' ? before.result : before.result.inspection); await pending; await host.flush();
    assert.deepEqual(destinations, ['new'], `${action}: the old page must not navigate back after it unmounts`);
    assert.ok(current.text().includes('Project new')); assert.ok(!current.text().includes('Project old'));
    current.unmount();
  }
  const staleRecent = deferred(); const fresh = overviewFixture('fresh');
  const nextConnection = { ...connection, baseUrl: 'http://replacement.invalid' };
  const recentHost = mount('ProjectOverview', {
    listRecentTasks: (active) => active === connection ? staleRecent.promise : Promise.resolve([fresh.recent]),
  }, { ensureConnection: async () => connection, onTaskCreated() {} });
  await recentHost.flush(); recentHost.update({ connection: nextConnection }); await recentHost.flush();
  staleRecent.resolve([overviewFixture('stale').recent]); await recentHost.flush();
  assert.ok(recentHost.text().includes('Project fresh')); assert.ok(!recentHost.text().includes('Project stale'),
    'A disposed initial recent-task request must not replace the current connection result');
  recentHost.unmount();
}

async function testSourceRescanCannotNavigateAfterLeavingProject() {
  for (const leaving of ['unmount', 'switch', 'stay']) {
    const rescan = deferred(), destinations = [];
    const host = mount('SourceMaterials', {
      loadSourceMaterials: async (_, taskId) => ({ task: { id: taskId, status: 'completed' },
        project: { name: 'Fixture', version: 'V1.0' }, source_document: null, source_plan: null,
        code_preview: { version: 1, summary: { sufficient: false, generated_pages: 1,
          target_pages: 59, included_files: 1 } }, actions: {}, blockers: ['More source needed'] }),
      loadSourceDocumentQaCapability: async () => ({ available: false }),
      rescanProject: () => rescan.promise,
    }, { taskId: 'old', onTaskCreated: (id) => destinations.push(id), onBackToOverview() {} });
    await host.flush();
    const pending = host.button('重新扫描当前项目').props.onClick(); await host.flush();
    if (leaving === 'unmount') host.unmount();
    if (leaving === 'switch') { host.update({ taskId: 'new' }); await host.flush(); }
    rescan.resolve({ task_id: 'rescanned' }); await pending; await host.flush();
    assert.deepEqual(destinations, leaving === 'stay' ? ['rescanned'] : [],
      `Rescan may navigate only while the initiating project remains mounted (${leaving})`);
  }
}

async function testSourceExportRequiresCurrentFileIntegrity() {
  for (const status of ['verified', 'missing', 'mismatch', 'invalid_path']) {
    let saves = 0, exports = 0;
    const source = { version: 1, quality: { status: 'passed' }, integrity: { status },
      summary: { total_pages_expected: 60 } };
    const host = mount('SourceMaterials', {
      loadSourceMaterials: async () => ({ task: { id: 'task', status: 'completed' },
        project: { name: 'Fixture', version: 'V1.0' }, source_document: source, source_plan: null,
        code_preview: null, actions: { source_docx: true }, blockers: [] }),
      loadSourceDocumentQaCapability: async () => ({ available: false }),
      __dialog: { save: async () => { saves++; return '/fixture/source.docx'; } },
      exportSourceDocument: async () => { exports++; },
    }, { taskId: 'task', onTaskCreated() {}, onBackToOverview() {} });
    await host.flush();
    const healthy = status === 'verified';
    const exportButton = host.button(healthy ? '导出…' : '重新生成后导出');
    assert.equal(exportButton.props.disabled, !healthy);
    // Invoke even the disabled handler to prove the action guard protects the native export boundary.
    await exportButton.props.onClick(); await host.flush();
    assert.equal(saves, healthy ? 1 : 0); assert.equal(exports, healthy ? 1 : 0);
    if (!healthy) {
      assert.ok(host.text().includes('源代码文档需要重新生成'));
      assert.equal(host.nodes((n) => n.type === 'details' && n.props.className === 'source-generation-tools')[0].props.open, true);
    }
    const assetHost = mount('AssetLibrary', {
      listRecentTasks: async () => [{ task_id: 'task', display_name: 'Fixture', updated_at: '2026-09-01' }],
      loadSourceMaterials: async () => ({ project: { name: 'Fixture', version: 'V1.0' }, source_document: source }),
      listFormalManualJobs: async () => [{ id: 'job' }],
      listFormalManualDocuments: async () => [{ id: 'manual', job_id: 'job', version: 1,
        created_at: '2026-09-01', document_kind: 'final_document', qa: { section_count: 8 },
        integrity: { status: 'verified' }, freshness: { status: 'current' }, quality: { status: 'passed' } }],
    }, { onOpen() {}, onPreview() {} });
    await assetHost.flush();
    assert.equal(assetHost.text().includes('双文档可交付'), healthy);
    const sourceRow = assetHost.nodes((n) => n.type === 'div' && /^asset-file /.test(n.props.className || '')
      && text(n).includes('源代码文档'))[0];
    assert.equal(text(sourceRow).includes('导出…'), healthy);
  }
}

async function testCompletedManualOperationsRespectClosedPreview() {
  for (const action of ['qa', 'assemble', 'finalize', 'repair', 'defer']) for (const dismiss of [true, false]) {
    const pendingResult = deferred(); let pageCalls = 0;
    const original = { id: 'doc', job_id: 'job', task_id: 'task', version: 1,
      document_kind: action === 'assemble' ? 'final_document' : 'formal_candidate',
      filename: 'fixture.docx', project_name: 'Fixture', project_version: 'V1.0',
      created_at: '2026-09-01', integrity: { status: 'verified', size_bytes: 1000 },
      freshness: { status: 'current' }, quality: { status: action === 'finalize' ? 'passed' : 'failed' },
      qa: { section_count: 1, figure_count: 0, screenshot_count: 0, warnings: [] } };
    const qa = { passed: action === 'finalize', page_count: 2, decisions: [],
      checks: [{ key: action === 'qa' ? 'structure.toc_page_numbers' :
        action === 'defer' ? 'render.page_density' : 'content.section_depth',
      passed: false, severity: 'error', message: 'Fixture issue', actual: [{ section_key: 'overview' }] }],
      summary: { warning_count: 0, blocker_count: 1, underfilled_pages: [] } };
    const refreshed = { ...original, id: ['qa', 'defer'].includes(action) ? 'doc' : 'revised',
      version: ['qa', 'defer'].includes(action) ? 1 : 2,
      document_kind: action === 'finalize' ? 'final_document' : 'formal_candidate', quality: { status: 'passed' } };
    const passedQa = { ...qa, passed: true, checks: [] };
    let stored = original;
    const host = mount('ManualWorkspace', {
      listFormalManualJobs: async () => [{ id: 'job', task_id: 'task', version: 1,
        status: 'completed', nodes: [], steps: [], updated_at: '2026-09-01',
        progress: { completed: 1, total: 1, percent: 100 } }],
      listFormalManualDocuments: async () => [stored], loadFormalManualQa: async () => qa,
      loadFormalManualQaPage: async () => `blob:page-${++pageCalls}`,
      loadFormalManualPreview: async () => ({ sections: [{ section_key: 'overview',
        title: 'Overview', blocks: [] }] }),
      regenerateFormalManualSection: async () => ({}),
      assembleFormalManualDocument: async () => refreshed,
      runFormalManualQa: () => pendingResult.promise,
      finalizeFormalManualDocument: () => pendingResult.promise,
      deferFormalManualQaCheck: () => pendingResult.promise,
    }, { taskId: 'task', trackedJob: null, onTaskChange() {}, onOpenDiagrams() {}, onOpenScreenshots() {} });
    await host.flush(); await host.click('逐页预览');
    const label = { qa: '校正目录页码并复检', assemble: '重新装配并复检', finalize: '生成终稿',
      repair: 'AI 修复命中章节并装配', defer: '忽略并留痕' }[action];
    const pending = host.button(label).props.onClick(); await host.flush();
    if (dismiss) await host.click('关闭');
    else await host.click('下一页');
    stored = refreshed;
    pendingResult.resolve(action === 'defer' ? passedQa : { document: refreshed, qa_run: passedQa });
    await pending; await host.flush();
    assert.equal(host.nodes((n) => n.props.className?.includes('manual-document-viewer')).length, dismiss ? 0 : 1,
      `${action}: completing an operation must respect whether the preview was closed`);
    assert.equal(pageCalls, dismiss ? 1 : action === 'defer' ? 2 : 3,
      `${action}: page navigation keeps preview intent; closing prevents additional page fetches`);
    assert.ok(host.button(action === 'finalize' ? '导出终稿' : '生成终稿'),
      `${action}: closing the preview must not discard the updated document quality`);
  }
}

async function testAssetsExposeLatestFailureAndExportCandidatesAsReviewOnly() {
  const task = { task_id: 'task', display_name: 'Fixture project', updated_at: '2026-09-01' };
  const old = { id: 'old', job_id: 'job', version: 1, created_at: '2026-08-01',
    document_kind: 'final_document', filename: 'fixture.docx', integrity: { status: 'verified' },
    freshness: { status: 'current' }, quality: { status: 'passed' }, qa: { section_count: 8 } };
  const current = { ...old, id: 'current', version: 2, created_at: '2026-09-01', quality: { status: 'failed' } };
  let opened = '';
  const api = { listRecentTasks: async () => [task], loadSourceMaterials: async () => ({
    project: { name: 'Fixture', version: 'V1.0' }, source_document: null }),
  listFormalManualJobs: async () => [{ id: 'job' }], listFormalManualDocuments: async () => [old, current] };
  const host = mount('AssetLibrary', api, { onOpen() {}, onPreview() {},
    onPreviewManual(id) { opened = id; } });
  await host.flush();
  assert.ok(host.text().includes('质量检查未通过，待修复'));
  assert.equal(host.nodes((n) => n.type === 'button' && text(n).includes('导出')).length, 0);
  await host.click('检查并修复说明书'); assert.equal(opened, 'task');

  let exportArgs;
  const ready = mount('AssetLibrary', { ...api,
    listFormalManualDocuments: async () => [{ ...current, document_kind: 'formal_candidate', quality: { status: 'passed' } }],
    __dialog: { save: async () => '/fixture/review.docx' },
    exportManualDocument: async (...args) => { exportArgs = args; },
  }, { onOpen() {}, onPreview() {} });
  await ready.flush(); await ready.click('导出审阅稿');
  assert.equal(exportArgs[3], true, 'A candidate must use the review-export API contract');
}

async function testScreenshotReviewUsesTaskModelAndExposesBlockingClaims() {
  const save = deferred(); let calls = 0, payload, returnedToQuick = 0;
  const asset = { id: 'screen', title: 'Page', analysis_status: 'completed', review_status: 'pending',
    adoption_status: 'pending', sensitive_status: 'unreviewed', group_title: 'Page', sort_order: 1,
    version: 1, interpretation_version: 1, width: 1000, height: 700,
    unresolved_claims: ['按钮用途需要确认'], interpretation: { page_title: 'Page',
      purpose: 'Visible page', unresolved_claims: ['按钮用途需要确认'], warnings: [] } };
  const workspace = { profile: { version: 1, profile: {} }, assets: [asset], batches: [],
    preferred_vision_model_id: 'glm', quick_start_status: 'waiting_for_user',
    ui_evidence_decision: { decision: 'waiting_for_screenshots', reason: '' },
    vision_models: [{ id: 'qwen', name: 'Qwen', model_name: 'qwen3.8' },
      { id: 'glm', name: 'GLM', model_name: 'glm-5.3-flash' }] };
  const host = mount('ScreenshotAssetWorkspace', {
    loadScreenshotEvidenceWorkspace: async () => workspace,
    loadScreenshotEvidenceImage: async () => 'blob:screen',
    reviewScreenshotEvidence: (...args) => { calls++; payload = args; return save.promise; },
  }, { taskId: 'task', onTaskChange() {}, onOpenManual() {}, onOpenSettings() {}, onOpenQuickStart() { returnedToQuick++; } });
  await host.flush();
  assert.equal(host.nodes(n => n.type === 'select')[0].props.value, 'glm');
  await host.click('审核并采用当前截图'); assert.equal(calls, 0);
  assert.ok(host.text().includes('请先处理右侧顶部'));
  const claims = host.nodes(n => n.type === 'textarea' && n.props.value === '按钮用途需要确认')[0];
  assert.ok(claims, 'Blocking claims must be editable');
  claims.props.onChange({ target: { value: '' } }); await host.flush();
  const action = host.button('审核并采用当前截图').props.onClick;
  const pending = action(); action(); await host.flush();
  assert.equal(calls, 1); assert.equal(payload[3].unresolved_claims.length, 0);
  assert.equal(host.nodes(n => n.type === 'fieldset')[0].props.disabled, true);
  asset.review_status = 'reviewed'; asset.adoption_status = 'adopted';
  asset.sensitive_status = 'confirmed_safe';
  asset.unresolved_claims = []; asset.interpretation.unresolved_claims = [];
  save.resolve({}); await pending; await host.flush();
  assert.ok(host.text().includes('全部处理后回到快速开始'));
  assert.equal(host.nodes(n => n.type === 'select')[0].props.value, 'glm');
  await host.click('返回快速开始，继续原任务');
  assert.equal(returnedToQuick, 1, 'Screenshot review returns to the owning Quick Start instead of creating another update pipeline');
  host.unmount();
}

(async () => {
  for (const test of [testSettingsPreserveUnsavedPreferences, testModelOperationsCannotOverlapOrDelete,
    testFailedSettingsLoadCannotOverwriteDefaults, testQuickRecoveryKeepsIdentityAndHistory,
    testQuickProgressUpdatesHistoryWithoutOverlappingPolls, testManualProjectSwitchDiscardsOldResponses,
    testFailedQaCannotFinalizeAndClosedPreviewStaysClosed, testQualityBlockersLeadToRepairBeforeRetry,
    testAssetsExposeLatestFailureAndExportCandidatesAsReviewOnly, testDiscoveryNeverReplacesSavedCredential,
    testManualEditingIsLockedWhileSaving, testSourceRescanCannotNavigateAfterLeavingProject,
    testCompletedManualOperationsRespectClosedPreview, testSourceExportRequiresCurrentFileIntegrity,
    testAppReconnectPreservesWorkspaceIdentityAndUnsavedChapter,
    testOverviewLocksScanOpenAndConfirmBeforeRerender, testOverviewIgnoresResponsesAfterLeavingOrReplacingConnection,
    testDrawioLoadTimeoutAndReconnectPreserveCurrentXml, testScreenshotReviewUsesTaskModelAndExposesBlockingClaims]) {
    await test(); console.log(`PASS ${test.name}`);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
