// Real transport functions with controlled fetch failures; no live service or credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function transport(fetch, invoke) {
  const source = fs.readFileSync(path.resolve(__dirname, '../../ui/api.ts'), 'utf8')
    .replaceAll('import.meta', '({ env: { DEV: false } })') + '\nexport { localFetch };';
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: () => ({ invoke }), fetch,
    Headers, AbortController, TypeError, window: { setTimeout, clearTimeout } });
  return exports;
}

(async () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    let requests = 0, reconnects = 0;
    const api = transport(async () => { requests++; throw new TypeError('Lost response'); },
      async () => { reconnects++; });
    await assert.rejects(api.localFetch({ baseUrl: 'http://fixture.invalid', sessionToken: 'fixture' },
      '/write', { method }), /操作结果尚未确认/);
    assert.equal(requests, 1, `${method} must never be replayed after an ambiguous network failure`);
    assert.equal(reconnects, 0);
  }
  let reconnects = 0;
  const requests = [];
  const api = transport(async (url, options) => {
    requests.push({ url, token: options.headers.get('X-Session-Token') });
    if (requests.length === 1) throw new TypeError('Disconnected');
    return { ok: true };
  }, async () => { reconnects++; return { baseUrl: 'http://new.invalid', sessionToken: 'new-fixture' }; });
  const connection = { baseUrl: 'http://old.invalid', sessionToken: 'old-fixture' };
  assert.equal((await api.localFetch(connection, '/read', {
    headers: { 'X-Session-Token': connection.sessionToken },
  })).ok, true);
  assert.equal(reconnects, 1);
  assert.deepEqual(requests, [
    { url: 'http://old.invalid/read', token: 'old-fixture' },
    { url: 'http://new.invalid/read', token: 'new-fixture' },
  ]);
  const rejected = transport(async () => ({ ok: false }), () => assert.fail('Health must not restart services'));
  assert.equal(await rejected.checkSidecarHealth(connection), false);
  const offline = transport(async () => { throw new TypeError('Offline'); }, () => assert.fail());
  assert.equal(await offline.checkSidecarHealth(connection), false);
  console.log('7 connection behavior checks passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
