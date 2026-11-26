// 設定頁「AI 模型」（本人 2026-11-19 晚）：站主為五項 AI 任務選模型與思考強度，存在 D1，排程開跑時讀。
// 和用量端點同一個接縫：測試用資料庫＋簽章身分直接呼叫請求處理。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createHash } from 'node:crypto';
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const PATH = "/api/settings/ai-models";
const ARGON = "gemini-4-argon", FLASH = "gemini-3.8-flash";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-19T12:00:00Z") };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
});

async function call(method = "GET", body = undefined, { email = OWNER, origin = ORIGIN } = {}) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  if (body !== undefined) { headers.set("content-type", "application/json"); headers.set("origin", origin); }
  return handle(new Request(ORIGIN + PATH, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }), env, next, deps);
}

const byKey = (body) => Object.fromEntries(body.tasks.map((t) => [t.key, t]));

test("還沒選過：六項（含首頁消息）都是預設值（和 gemini_models.py 一樣），依設定頁的順序", async () => {
  const res = await call();
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "private, no-store");
  const body = await res.json();
  assert.equal(body.available, true);
  assert.equal(body.ready, true);
  assert.deepEqual(body.tasks.map((t) => [t.key, t.model, t.effort, t.saved]), [
    ["forecast", ARGON, "medium", false],
    ["watch_judge", FLASH, "high", false],
    ["watch_nominate", FLASH, "high", false],
    ["plain", ARGON, "medium", false],
    ["digest", ARGON, "medium", false],
    ["news", FLASH, "high", false],
  ]);
  assert.equal(body.updated_at, null);
});

test("官方目錄的新模型與各自強度可選，既有手動選擇不因新模型改變", async () => {
  await call("PUT", { tasks: { forecast: { model: ARGON, effort: "high" } } });
  const body = await (await call()).json();
  const neon = body.catalog.models.find(m => m.id === "gemini-4-neon");
  assert.deepEqual(neon.efforts, ["high", "xhigh", "medium", "low"]);
  assert.equal(byKey(body).forecast.model, ARGON);
  const saved = await call("PUT", { tasks: { plain: { model: neon.id, effort: "xhigh" } } });
  assert.equal(saved.status, 200);
  assert.equal(byKey(await saved.json()).plain.effort, "xhigh");
  assert.equal((await call("PUT", { tasks: { plain: { model: FLASH, effort: "xhigh" } } })).status, 400);
});

test("朋友拿不到也改不了", async () => {
  assert.deepEqual(await (await call("GET", undefined, { email: "a@example.com" })).json(), { available: false });
  const res = await call("PUT", { tasks: { plain: { model: FLASH, effort: "low" } } }, { email: "a@example.com" });
  assert.equal(res.status, 403);
  assert.equal(await env.DB.prepare("SELECT count(*) AS n FROM ai_model_settings").first().then((r) => r.n), 0);
});

test("站主存檔：只改送來的那幾項，回傳存好的結果與時間", async () => {
  const res = await call("PUT", { tasks: { forecast: { model: FLASH, effort: "medium" }, plain: { model: FLASH, effort: "low" } } });
  assert.equal(res.status, 200);
  const body = await res.json();
  const tasks = byKey(body);
  assert.deepEqual([tasks.forecast.model, tasks.forecast.effort, tasks.forecast.saved], [FLASH, "medium", true]);
  assert.deepEqual([tasks.plain.model, tasks.plain.effort], [FLASH, "low"]);
  assert.deepEqual([tasks.digest.model, tasks.digest.saved], [ARGON, false]);
  assert.equal(body.updated_at, "2026-11-19T12:00:00Z");
  // 排程讀的就是這張表
  const rows = (await env.DB.prepare("SELECT task, model, effort FROM ai_model_settings ORDER BY task").all()).results;
  assert.deepEqual(rows.map((r) => [r.task, r.model, r.effort]), [["forecast", FLASH, "medium"], ["plain", FLASH, "low"]]);
});

test("再存一次覆蓋舊值", async () => {
  await call("PUT", { tasks: { digest: { model: FLASH, effort: "low" } } });
  const body = await (await call("PUT", { tasks: { digest: { model: ARGON, effort: "high" } } })).json();
  assert.deepEqual([byKey(body).digest.model, byKey(body).digest.effort], [ARGON, "high"]);
});

test("不認得的任務、模型或思考強度整筆拒絕，什麼都不存", async () => {
  for (const tasks of [
    { friend: { model: FLASH, effort: "high" } },
    { forecast: { model: "gemini-9-ultra", effort: "high" } },
    { forecast: { model: FLASH, effort: "max" } },
    { forecast: { model: FLASH, effort: "low" }, plain: { model: ARGON } },
    {},
  ]) {
    const res = await call("PUT", { tasks });
    assert.equal(res.status, 400, JSON.stringify(tasks));
  }
  assert.equal(await env.DB.prepare("SELECT count(*) AS n FROM ai_model_settings").first().then((r) => r.n), 0);
});

test("R2 目錄更新共用儲存驗證，停用選擇仍可見，只有站主可讀替代記錄", async () => {
  const initial = await (await call()).json();
  const future = { id: 'gemini-5-future', label: 'Gemini 5 Future', efforts: ['medium'], slugs: { medium: 'gemini-5-future-medium' } };
  const raw = Buffer.from('gemini-5-future-medium Gemini 5 Future (Medium)\n');
  const digest = createHash('sha256').update(raw).digest('hex');
  const objects = {
    'ai-models/current.json': { ...initial.catalog, raw_sha256: digest, models: [future] },
    'ai-models/status.json': { state: 'failed', checked_at: '2026-11-19T11:00:00Z' },
    'ai-models/resolutions/forecast.json': { state: 'replaced', requested: { model: ARGON, effort: 'high' }, actual: { model: future.id, effort: 'medium' }, checked_at: '2026-11-19T13:00:00Z' },
  };
  await call('PUT', { tasks: { forecast: { model: ARGON, effort: 'high' } } });
  let reads = 0;
  env.STATE = { get: async key => {
    reads++;
    if (key === `ai-models/raw/${digest}.txt`) return new Response(raw);
    return objects[key] ? { json: async () => objects[key] } : null;
  } };
  const body = await (await call()).json();
  assert.equal(byKey(body).forecast.model, ARGON, 'keep saved retired models visible');
  assert.equal(body.resolutions.forecast.actual.model, future.id);
  assert.equal(body.catalog_status.state, 'failed');
  assert.equal((await call('PUT', { tasks: { forecast: { model: future.id, effort: 'medium' } } })).status, 200);
  assert.equal((await (await call()).json()).resolutions.forecast, undefined, 'old replacement does not apply after a manual change');
  reads = 0;
  assert.deepEqual(await (await call('GET', undefined, {email: 'a@example.com'})).json(), {available: false});
  assert.equal(reads, 0, 'friend requests never read private resolution objects');
});

test('來源失敗或快照與原文不同時不放進選單與儲存白名單，也不覆寫手動設定', async () => {
  await call('PUT', { tasks: { forecast: { model: ARGON, effort: 'high' } } });
  const initial = await (await call()).json();
  const raw = Buffer.from('gemini-fixture-high Fixture (High)\n');
  const digest = createHash('sha256').update(raw).digest('hex');
  const validRow = { id: 'gemini-fixture', label: 'Fixture', efforts: ['high'], slugs: { high: 'gemini-fixture-high' } };
  for (const mode of ['slug', 'effort', 'raw', 'missing', 'network']) {
    const row = structuredClone(validRow);
    if (mode === 'slug') row.slugs.high = 'gemini-unlisted-high';
    if (mode === 'effort') { row.efforts.push('max'); row.slugs.max = 'gemini-fixture-max'; }
    env.STATE = { get: async key => {
      if (mode === 'network') throw new Error('fake offline');
      if (key === 'ai-models/current.json') return Response.json({ ...initial.catalog, raw_sha256: digest, models: [row] });
      if (key === `ai-models/raw/${digest}.txt` && mode !== 'missing') return new Response(mode === 'raw' ? Buffer.concat([raw, Buffer.from('\n')]) : raw);
      return null;
    } };
    const body = await (await call()).json();
    assert.equal(body.catalog.source_state, 'unreadable', mode);
    assert.equal(body.catalog.models.some(m => m.id === row.id), false, mode);
    assert.equal((await call('PUT', { tasks: { forecast: { model: row.id, effort: mode === 'effort' ? 'max' : 'high' } } })).status, 400, mode);
    const after = byKey(await (await call()).json()).forecast;
    assert.deepEqual([after.model, after.effort, after.saved], [ARGON, 'high', true], mode);
  }
});

test("不完整 CLI 快照不可提供新選項，明示退回內建快照", async () => {
  const initial = await (await call()).json();
  env.STATE = { get: async key => key === 'ai-models/current.json' ? { json: async () => ({ ...initial.catalog, release: { ...initial.catalog.release, sha256: '' } }) } : null };
  const body = await (await call()).json();
  assert.equal(body.catalog.source_state, 'unreadable');
  assert.equal(body.catalog.cli_version, '1.5.12');
});

test("模型通知沿既有站主推播裝置送出，不通知朋友、不把失敗記成已送", async () => {
  const { sendModelNotices } = await import('../model-notices.mjs');
  for (const [email, endpoint] of [[OWNER, 'https://fcm.googleapis.com/owner'], ['a@example.com', 'https://fcm.googleapis.com/friend']]) {
    await env.DB.prepare('INSERT INTO push_subscriptions (endpoint,email,p256dh,auth,label,created_at) VALUES (?,?,?,?,?,?)')
      .bind(endpoint,email,'a'.repeat(87),'b'.repeat(22),'device','2026-11-19T00:00:00Z').run();
  }
  const sent = [];
  const notices = [{ id: 'a'.repeat(64), key: 'catalog', state: 'failed' }];
  const pushEnv = { ...env, VAPID_PUBLIC_KEY: 'pub', VAPID_PRIVATE_KEY: 'priv' };
  const result = await sendModelNotices(pushEnv, notices, { send: async (subscription, payload) => { sent.push([subscription.endpoint, payload]); return {ok: true}; } });
  assert.deepEqual(result.delivered, ['a'.repeat(64)]);
  assert.deepEqual(sent.map(s => s[0]), ['https://fcm.googleapis.com/owner']);
  assert.equal(sent[0][1].url, '/minimal.html#ai-models');
  assert.deepEqual((await sendModelNotices(pushEnv, notices, {send: async () => ({ok:false})})).delivered, []);
  await call('PUT', {tasks:{forecast:{model:FLASH,effort:'high'}}});
  const obsolete = await sendModelNotices(pushEnv, [{...notices[0], key:'forecast', requested:{model:ARGON,effort:'medium'}, checked_at:'2026-11-19T11:00:00Z'}], {send: async () => { throw new Error('obsolete notice must not send'); }});
  assert.deepEqual(obsolete.delivered, []);
  assert.deepEqual(obsolete.discarded, ['a'.repeat(64)]);
});

test("跨站送來的存檔被擋", async () => {
  const res = await call("PUT", { tasks: { plain: { model: FLASH, effort: "low" } } }, { origin: "https://evil.example" });
  assert.equal(res.status, 403);
});

test("頂端那一行：Argon 與 Flash 本週剩多少（和用量籤同一份讀數）", async () => {
  const reading = [
    { id: "gemini-weekly", window: "weekly", remaining: 0.71, reset_time: "2026-11-25T05:55:00Z" },
    { id: "gemini-5h", window: "5h", remaining: 0.89, reset_time: "2026-11-19T15:00:00Z" },
    { id: "gemini-argon-wtu-weekly", window: "weekly", remaining: 0.18, reset_time: "2026-11-25T05:55:00Z" },
  ];
  await env.DB.prepare("INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, buckets_json) VALUES ('watch-ledger', '1', ?, ?)")
    .bind("2026-11-19T11:00:00Z", JSON.stringify(reading)).run();
  const body = await (await call()).json();
  assert.deepEqual(body.quota, { argon: 0.18, flash: 0.71 });
});

test("沒有用量讀數時頂端那一行是空的", async () => {
  assert.deepEqual((await (await call()).json()).quota, { argon: null, flash: null });
});

test("每支排程最近一班讀不到設定才列出來；之後又讀到就不再列", async () => {
  const read = (task, runId, at, source) => env.DB
    .prepare("INSERT INTO ai_model_reads (task, run_id, read_at, source) VALUES (?, ?, ?, ?)").bind(task, runId, at, source).run();
  await read("forecast-ledger", "1", "2026-11-18T10:00:00Z", "unreadable");
  await read("forecast-ledger", "2", "2026-11-19T10:00:00Z", "saved");
  await read("watch-ledger", "3", "2026-11-19T10:05:00Z", "unreadable");
  await read("daily-update", "4", "2026-11-19T09:00:00Z", "unset");
  const body = await (await call()).json();
  assert.deepEqual(body.unread, [{ task: "watch-ledger", read_at: "2026-11-19T10:05:00Z" }]);
});

test("遷移 0030 還沒套用：顯示預設、標 ready=false，存檔回 503", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((f) => !f.startsWith("0030")));
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
  const body = await (await call()).json();
  assert.equal(body.ready, false);
  assert.equal(byKey(body).forecast.model, ARGON);
  assert.deepEqual(body.unread, []);
  const res = await call("PUT", { tasks: { plain: { model: FLASH, effort: "low" } } });
  assert.equal(res.status, 503);
});
