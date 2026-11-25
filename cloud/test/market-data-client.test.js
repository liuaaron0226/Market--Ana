import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../public-src/market-data-cloud.js', import.meta.url), 'utf8');
const json = body => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
function client(fetch, timers = {}) {
  const window = {};
  vm.runInNewContext(source, { window, fetch, AbortController, setTimeout, clearTimeout, ...timers });
  return window.MarketData;
}

test('同日換版後，同一天明細不能沿用舊版快取', async () => {
  let version = 'v1', calls = 0;
  const data = client(async url => {
    if (url.endsWith('/overview')) return json({ version });
    calls++;
    assert.equal(new URL(url, 'https://test').searchParams.get('v'), version);
    return json({ tables: { foreign_buy: [{ name: version }] } });
  });
  await data.overview();
  assert.equal((await data.detail('foreign', '2026-11-25')).foreign_buy[0].name, 'v1');
  version = 'v2';
  await data.overview();
  assert.equal((await data.detail('foreign', '2026-11-25')).foreign_buy[0].name, 'v2');
  assert.equal(calls, 2);
});

test('換版時在途舊明細不被新版呼叫合併，也不能污染新版快取', async () => {
  let version = 'v1', finishOld;
  const data = client(async url => {
    if (url.endsWith('/overview')) return json({ version });
    if (url.includes('v=v1')) return new Promise(resolve => { finishOld = () => resolve(json({ tables: { name: 'old' } })); });
    return json({ tables: { name: 'new' } });
  });
  await data.overview();
  const old = data.detail('trust', '2026-11-24');
  version = 'v2';
  await data.overview();
  const current = data.detail('trust', '2026-11-24');
  assert.notEqual(old, current, '不同版本不能共用在途明細');
  assert.equal((await current).name, 'new');
  finishOld();
  await old;
  assert.equal((await data.detail('trust', '2026-11-24')).name, 'new');
});

test('同時查總覽只發一個請求；完成後可再讀同日新版', async () => {
  let finish, calls = 0;
  const data = client(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const a = data.overview(), b = data.overview();
  assert.equal(calls, 1);
  finish(json({ version: 'v1' }));
  assert.equal((await a).version, 'v1');
  assert.equal((await b).version, 'v1');
  const c = data.overview();
  assert.equal(calls, 2);
  finish(json({ version: 'v2' }));
  assert.equal((await c).version, 'v2');
});

test('總覽失敗不丟掉成功快取；同版快取仍用，明細失敗不快取且可重試', async () => {
  let offline = false, failDetail = true, detailCalls = 0;
  const data = client(async url => {
    if (url.endsWith('/overview')) return offline ? new Response('', { status: 503 }) : json({ version: 'v1' });
    detailCalls++;
    if (url.includes('kind=trust') && failDetail) return new Response('', { status: 503 });
    return json({ tables: { value: 42 } });
  });
  await data.overview();
  await Promise.all([data.detail('foreign', '2026-11-24'), data.detail('foreign', '2026-11-24')]);
  assert.equal(detailCalls, 1);
  offline = true;
  await assert.rejects(data.overview(), e => e.kind === 'server');
  assert.equal((await data.detail('foreign', '2026-11-24')).value, 42);
  assert.equal(detailCalls, 1);
  offline = false;
  await data.overview();
  await data.detail('foreign', '2026-11-24');
  assert.equal(detailCalls, 1);
  await assert.rejects(data.detail('trust', '2026-11-24'), e => e.kind === 'server');
  failDetail = false;
  assert.equal((await data.detail('trust', '2026-11-24')).value, 42);
  assert.equal(detailCalls, 3);
});

test('總覽連線卡住會逾時，之後可重試而非永久沿用在途工作', async () => {
  let expire, stalled = true;
  const data = client((_url, options) => {
    if (!stalled) return Promise.resolve(json({ version: 'retry' }));
    return new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(new Error('aborted'))));
  }, { setTimeout: callback => { expire = callback; return 1; }, clearTimeout() {} });
  const pending = data.overview();
  assert.equal(typeof expire, 'function', '卡住的連線必須有有限逾時');
  expire();
  await assert.rejects(pending, e => e.kind === 'network');
  stalled = false;
  assert.equal((await data.overview()).version, 'retry');
});
