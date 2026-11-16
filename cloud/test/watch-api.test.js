// 自選股關注（2026-11-15 規格，A 票）：站主逐檔打開關注，上限 20 檔，只有站主看得到。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { policyForViewer, releasePolicy, strategyWorkflowPolicy } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ON = { MARKET_ANA_WATCHLIST_WATCH: "owner" };
const ORIGIN = "https://market-ana.pages.dev";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function addSecurity(code, name, analysisStatus = "available") {
  await env.DB.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES (?, ?, ?, ?, '普通股', ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", code, name, "上市", `TW000${code}0008`.slice(0, 12), "1994/09/05", "半導體業", "ESVUFR", "2026-11-13", 300, analysisStatus, 1, "liquid-v1").run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, ...ON };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-13T14:00:00Z"), rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2026-11-13", "https://example.invalid/catalog", "a".repeat(64), "2026-11-13T12:00:00Z", 1, 0, "liquid-v1").run();
  await addSecurity("2330", "台積電");
  await addSecurity("2317", "鴻海");
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, ?)")
    .bind("v1", "2026-11-13T12:01:00Z").run();
});

async function req(path, { email = OWNER, method = "GET", body, origin = ORIGIN } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  const write = method !== "GET";
  if (write) {
    headers.set("content-type", "application/json");
    if (origin) headers.set("origin", origin);
  }
  return handle(new Request(ORIGIN + path, { method, headers, body: write ? JSON.stringify(body ?? {}) : undefined }), env, next, deps);
}

const json = async (path, options) => (await req(path, options)).json();
const follow = (code, options = {}) => req("/api/watchlist", { method: "POST", body: { code, horizon: "none" }, ...options });

test("站主替自選股裡的一檔打開關注，之後讀得到它正在關注", async () => {
  assert.equal((await follow("2330")).status, 201);
  const opened = await req("/api/watch/2330", { method: "POST" });
  assert.equal(opened.status, 200);
  assert.deepEqual(await opened.json(), { available: true, code: "2330", watching: true, count: 1, limit: 20 });
  assert.deepEqual(await json("/api/watch"), { available: true, count: 1, limit: 20, items: [{ code: "2330", since: "2026-11-13T14:00:00.000Z" }], unwatchable: [] });
});

test("原本就在自選股裡的股票預設沒有關注；關掉之後可以再打開", async () => {
  await follow("2330");
  assert.deepEqual(await json("/api/watch"), { available: true, count: 0, limit: 20, items: [], unwatchable: [] });

  await req("/api/watch/2330", { method: "POST" });
  const closed = await req("/api/watch/2330", { method: "DELETE" });
  assert.equal(closed.status, 204, "和其他個人資料的刪除一樣：成功沒有內容");
  assert.equal((await json("/api/watch")).count, 0);
  assert.deepEqual((await json("/api/watch")).items, []);
  assert.deepEqual((await json("/api/watchlist")).items.map((item) => item.code), ["2330"], "關掉關注不會把它從自選股拿掉");

  assert.equal((await json("/api/watch/2330", { method: "POST" })).watching, true);
  assert.equal((await json("/api/watch")).count, 1);
});

test("不在自選股裡、或不在分析範圍的股票不能關注，並說明原因", async () => {
  const missing = await req("/api/watch/2317", { method: "POST" });
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).error, "自選股中沒有這檔股票");

  // 名錄母體沒收錄、靠官方報價補進自選股的那種：網站沒有它的分析資料。
  deps.quoteFetch = async () => new Response(JSON.stringify({ msgArray: [{ c: "6789", n: "範圍外", ex: "tse" }] }));
  assert.equal((await follow("6789")).status, 201);
  const notAnalysed = await req("/api/watch/6789", { method: "POST" });
  assert.equal(notAnalysed.status, 409);
  assert.equal((await notAnalysed.json()).error, "這檔不在網站的分析範圍內，沒有資料可以判斷");
  assert.equal((await json("/api/watch")).count, 0);

  assert.equal((await req("/api/watch/2317", { method: "DELETE" })).status, 404);
  assert.equal((await req("/api/watch/abc", { method: "POST" })).status, 400);
});

test("關注最多 20 檔；第 21 檔被拒並說明已滿，同時送出也繞不過", async () => {
  const codes = Array.from({ length: 21 }, (_, index) => String(3000 + index));
  for (const code of codes) {
    await addSecurity(code, `股票${code}`);
    await follow(code);
  }
  for (const code of codes.slice(0, 19)) assert.equal((await req(`/api/watch/${code}`, { method: "POST" })).status, 200);
  // 最後一個名額，兩檔同時搶：只會有一檔成功。
  const race = await Promise.all(codes.slice(19).map((code) => req(`/api/watch/${code}`, { method: "POST" })));
  assert.deepEqual(race.map((r) => r.status).sort(), [200, 409]);
  const refused = await race.find((r) => r.status === 409).json();
  assert.equal(refused.error, "關注最多 20 檔，請先關掉一檔");
  assert.equal((await json("/api/watch")).count, 20);

  // 已經在關注的再按一次不算超過；關掉一檔之後才有名額。
  assert.equal((await req("/api/watch/3000", { method: "POST" })).status, 200);
  await req("/api/watch/3000", { method: "DELETE" });
  const stillWaiting = race[0].status === 409 ? codes[19] : codes[20];
  assert.equal((await req(`/api/watch/${stillWaiting}`, { method: "POST" })).status, 200);
});

test("其他獲准使用者得到「沒有內容」，也動不了任何東西；他們的自選股回應和原來一樣", async () => {
  await follow("2330", { email: "a@example.com" });
  assert.deepEqual(await json("/api/watch", { email: "a@example.com" }), { available: false });
  for (const method of ["POST", "DELETE"]) {
    const refused = await req("/api/watch/2330", { email: "a@example.com", method });
    assert.equal(refused.status, 200, method);
    assert.deepEqual(await refused.json(), { available: false }, method);
  }
  const [item] = (await json("/api/watchlist", { email: "a@example.com" })).items;
  // 關注的欄位（watching、watch_since）不會出現在自選股的回應裡。group 是自選的群組（2026-11-16 加的），每個人都有。
  assert.deepEqual(Object.keys(item).sort(), ["code", "created_at", "group", "horizon", "in_universe", "market", "name", "updated_at"]);

  // 站主的關注和別人的自選股互不相干。
  await follow("2330");
  await req("/api/watch/2330", { method: "POST" });
  assert.equal((await json("/api/watch")).count, 1);
  assert.deepEqual(Object.keys((await json("/api/watchlist")).items[0]).sort(), Object.keys(item).sort(), "站主的自選股回應也不變");
});

test("未登入、未獲准、跨站的請求被擋", async () => {
  assert.equal((await req("/api/watch", { email: null })).status, 401);
  assert.equal((await req("/api/watch", { email: "stranger@example.com" })).status, 403);
  await follow("2330");
  assert.equal((await req("/api/watch/2330", { method: "POST", origin: "https://evil.example" })).status, 403);
  assert.equal((await req("/api/watch/2330", { method: "POST", origin: null })).status, 403);
  assert.equal((await json("/api/watch")).count, 0);
  assert.equal((await req("/api/watch", { method: "POST" })).status, 405);
  assert.equal((await req("/api/watch/2330", { method: "PUT" })).status, 405);
});

test("功能開關關著時站主也讀不到、也打不開", async () => {
  await follow("2330");
  for (const value of [undefined, "", "1", "true", "OWNER", "all", " owner "]) {
    env = { ...env, MARKET_ANA_WATCHLIST_WATCH: value };
    assert.deepEqual(await json("/api/watch"), { available: false }, String(value));
    assert.deepEqual(await json("/api/watch/2330", { method: "POST" }), { available: false }, String(value));
  }
  env = { ...env, ...ON };
  assert.equal((await json("/api/watch")).count, 0);
});

test("關注開關是獨立的：打開它不會連帶打開其他功能，別的開關也不會打開它", () => {
  const on = releasePolicy(ON);
  assert.equal(on.features.watchlist_watch_v1, true);
  assert.equal(on.strategies_enabled, false);
  for (const feature of ["strategy_v1", "market_trend_v1", "level_advice", "ai_digest", "ai_stock_advice", "forecast_ledger_v1"]) {
    assert.equal(on.features[feature], false, feature);
  }
  for (const event of ["schedule", "workflow_run"]) {
    assert.deepEqual(strategyWorkflowPolicy({ ...ON, EVENT_NAME: event }), { allowed: false, dry_run: true });
  }
  assert.equal(releasePolicy({ MARKET_ANA_STRATEGIES_ENABLED: "true" }).features.watchlist_watch_v1, false);
  assert.equal(releasePolicy({ MARKET_ANA_FORECAST_LEDGER: "owner" }).features.watchlist_watch_v1, false);
  assert.equal(releasePolicy({ MARKET_ANA_ENABLED_FEATURES: "watchlist_watch_v1" }).features.watchlist_watch_v1, false);
  assert.equal(releasePolicy({}).features.watchlist_watch_v1, false);
});

test("正式環境設定：關注只對站主打開，其他人的功能清單看不到，也不連帶打開策略與 AI", async () => {
  const { readFileSync } = await import("node:fs");
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  const vars = JSON.parse(text).vars;
  assert.equal(vars.MARKET_ANA_WATCHLIST_WATCH, "owner");
  const policy = releasePolicy(vars);
  assert.equal(policy.features.watchlist_watch_v1, true);
  assert.equal(policyForViewer(policy, false).features.watchlist_watch_v1, false);
  assert.equal(policy.strategies_enabled, false);
  for (const feature of ["strategy_v1", "level_advice", "ai_digest", "ai_stock_advice"]) {
    assert.equal(policy.features[feature], false, feature);
  }
});

test("從自選股刪除一檔，它的關注一併結束；之後再加回來不會自己變成關注", async () => {
  await follow("2330");
  await req("/api/watch/2330", { method: "POST" });
  assert.equal((await req("/api/watchlist/2330", { method: "DELETE" })).status, 204);
  assert.deepEqual(await json("/api/watch"), { available: true, count: 0, limit: 20, items: [], unwatchable: [] });
  await follow("2330");
  assert.equal((await json("/api/watch")).count, 0);
});

test("改自選股的短期／長期標記不會動到關注", async () => {
  await follow("2330");
  await req("/api/watch/2330", { method: "POST" });
  assert.equal((await req("/api/watchlist", { method: "POST", body: { code: "2330", horizon: "long" } })).status, 200);
  assert.deepEqual((await json("/api/watch")).items.map((item) => item.code), ["2330"]);
});

test("關注和其他個人資料共用同一個每分鐘額度", async () => {
  deps.rate = { ...deps.rate, userData: 2 };
  assert.equal((await req("/api/watch")).status, 200);
  assert.equal((await req("/api/watch")).status, 200);
  assert.equal((await req("/api/watch")).status, 429);
});

test("能不能關注以現在的名錄為準：畫面拿得到不能關注的清單，後來進了分析範圍的可以打開", async () => {
  deps.quoteFetch = async () => new Response(JSON.stringify({ msgArray: [{ c: "6789", n: "後來才進範圍", ex: "tse" }] }));
  await follow("6789");
  await follow("2330");
  await addSecurity("5555", "資料不足", "insufficient");
  await follow("5555");
  await addSecurity("5556", "資料稍舊", "stale");
  await follow("5556");
  assert.deepEqual((await json("/api/watch")).unwatchable, ["5555", "6789"]);

  // 名錄裡有、但分析資料不足的那一檔同樣不能關注。
  const thin = await req("/api/watch/5555", { method: "POST" });
  assert.equal(thin.status, 409);
  assert.equal((await thin.json()).error, "這檔不在網站的分析範圍內，沒有資料可以判斷");
  assert.equal((await req("/api/watch/5556", { method: "POST" })).status, 200, "資料稍舊的仍在分析範圍內");

  // 6789 之後進了分析範圍：自選股那一列當初記的是「不在範圍」，但現在可以關注了。
  await addSecurity("6789", "後來才進範圍");
  assert.deepEqual((await json("/api/watch")).unwatchable, ["5555"]);
  assert.equal((await req("/api/watch/6789", { method: "POST" })).status, 200);
});

test("關掉再打開算重新排隊：順序依最後一次打開的時間", async () => {
  await follow("2330");
  await follow("2317");
  await req("/api/watch/2330", { method: "POST" });
  deps.now = () => Date.parse("2026-11-13T14:05:00Z");
  await req("/api/watch/2317", { method: "POST" });
  await req("/api/watch/2330", { method: "DELETE" });
  deps.now = () => Date.parse("2026-11-13T14:10:00Z");
  await req("/api/watch/2330", { method: "POST" });
  // 已經在關注的再按一次不會改順序。
  deps.now = () => Date.parse("2026-11-13T14:20:00Z");
  await req("/api/watch/2317", { method: "POST" });
  assert.deepEqual((await json("/api/watch")).items, [
    { code: "2317", since: "2026-11-13T14:05:00.000Z" },
    { code: "2330", since: "2026-11-13T14:10:00.000Z" },
  ]);
});

test("其他獲准使用者看到的功能清單裡沒有關注這一項；站主看得到", async () => {
  assert.equal((await json("/api/features")).features.watchlist_watch_v1, true);
  const other = await json("/api/features", { email: "a@example.com" });
  assert.equal(other.features.watchlist_watch_v1, false);
  assert.equal(other.schema, "market-ana-features/v1");
});
