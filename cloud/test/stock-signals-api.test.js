// 個股頁五塊新功能（本人 2026-11-16 共識；規格 .scratch/stock-signals/spec.md）的網站入口：
// 獨立的開關三種狀態（關、只開站主、開給全部獲准使用者）下，誰拿得到個股分析結果裡的 signals（五塊資料）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { onRequest as aiDigest } from "../functions/api/ai-digest.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const EXAMPLE = JSON.parse(readFileSync(new URL("./fixtures/stock-analysis-v2.example.json", import.meta.url), "utf8"));
const SIGNALS = {
  data_date: "2026-11-09",
  versus_market: { rows: [
    { days: 5, stock_pct: 1.23, market_pct: -1.97, gap: 3.2, tone: "up", label: "強於大盤" },
    { days: 20, stock_pct: -13.3, market_pct: -16.1, gap: 2.8, tone: "up", label: "強於大盤" },
  ] },
  blocks: ["versus_market"],
};
const FACTS = { data_date: "2026-11-09", today: [], blocks: [] };
const WITH_BOTH = { ...EXAMPLE, code: "2330", name: "台積電", facts: FACTS, signals: SIGNALS };
const FRIEND = "friend@x.com";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    stockPack: { as_of: "2026-11-09", analysis_schema: "stock-analysis/v2", results: { "2330": WITH_BOTH } },
    now: () => Date.parse("2026-11-10T10:00:05Z"),
  };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind(FRIEND, "n", "2026-11-07T00:00:00Z").run();
});

async function get(path, email) {
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  const response = await handle(new Request("https://market-ana.pages.dev" + path, { headers: h }), env, next, deps);
  assert.equal(response.status, 200, path);
  return response.json();
}
const analysisOf = (email) => get("/api/stocks/2330/analysis", email);
const featureOf = async (email) => (await get("/api/features", email)).features.stock_signals_v1;

test("開關沒設：站主與其他獲准使用者都拿不到五塊資料，功能清單是關的，其他內容照常", async () => {
  for (const email of [OWNER, FRIEND]) {
    const body = await analysisOf(email);
    assert.equal(Object.hasOwn(body, "signals"), false, email);
    assert.ok(Array.isArray(body.levels.support), "其他內容照常");
    assert.equal(await featureOf(email), false, email);
  }
});

test("只開站主：站主拿得到五塊資料；其他獲准使用者的個股分析與功能清單和開關關著時一樣", async () => {
  env.MARKET_ANA_STOCK_SIGNALS = "owner";
  assert.deepEqual((await analysisOf(OWNER)).signals, SIGNALS);
  assert.equal(await featureOf(OWNER), true);
  assert.equal(Object.hasOwn(await analysisOf(FRIEND), "signals"), false);
  assert.equal(await featureOf(FRIEND), false);
});

test("開給全部：站主與其他獲准使用者都拿得到五塊資料", async () => {
  env.MARKET_ANA_STOCK_SIGNALS = "all";
  for (const email of [OWNER, FRIEND]) {
    assert.deepEqual((await analysisOf(email)).signals, SIGNALS, email);
    assert.equal(await featureOf(email), true, email);
  }
});

test("未登入拿不到個股分析，三種開關狀態都一樣", async () => {
  for (const value of [undefined, "owner", "all"]) {
    if (value === undefined) delete env.MARKET_ANA_STOCK_SIGNALS; else env.MARKET_ANA_STOCK_SIGNALS = value;
    const response = await handle(new Request("https://market-ana.pages.dev/api/stocks/2330/analysis"), env, next, deps);
    assert.equal(response.status, 401, String(value));
    assert.doesNotMatch(await response.text(), /強於大盤|signals/, String(value));
    const features = await handle(new Request("https://market-ana.pages.dev/api/features"), env, next, deps);
    assert.equal(features.status, 401, String(value));
    assert.doesNotMatch(await features.text(), /stock_signals_v1/, String(value));
  }
});

test("和事實區塊的開關互不相干：各自只決定自己那一塊", async () => {
  env.MARKET_ANA_STOCK_FACTS = "all";
  let body = await analysisOf(FRIEND);
  assert.deepEqual(body.facts, FACTS);
  assert.equal(Object.hasOwn(body, "signals"), false, "事實區塊打開不會帶出五塊資料");
  delete env.MARKET_ANA_STOCK_FACTS;
  env.MARKET_ANA_STOCK_SIGNALS = "all";
  body = await analysisOf(FRIEND);
  assert.deepEqual(body.signals, SIGNALS);
  assert.equal(Object.hasOwn(body, "facts"), false, "五塊打開不會帶出事實資料");
});

test("只開站主時，其他人從大盤資料與水位入口拿到的功能清單也看不到這一項是開的", async () => {
  env.MARKET_ANA_STOCK_SIGNALS = "owner";
  deps.marketPack = { version: "v1", overview: { analysis: {}, dashboard: {} }, details: {} };
  assert.equal((await get("/api/market/overview", OWNER)).features.stock_signals_v1, true);
  assert.equal((await get("/api/market/overview", FRIEND)).features.stock_signals_v1, false);
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(FRIEND)));
  const level = await handle(new Request("https://market-ana.pages.dev/api/holdings/level", { headers: h }), env, next, deps);
  assert.equal(level.status, 403);
  assert.equal((await level.json()).features.stock_signals_v1, false);
});

test("只開站主時，AI 摘要入口（關閉中）回給其他人的功能清單也看不到五塊與事實區塊是開的", async () => {
  env.MARKET_ANA_STOCK_SIGNALS = "owner";
  env.MARKET_ANA_STOCK_FACTS = "owner";
  const digestFeatures = async (email) => {
    const response = await aiDigest({
      request: new Request("https://market-ana.pages.dev/api/ai-digest", { headers: { "cf-access-jwt-assertion": await issuer.sign(claims(email)) } }),
      env, fetch: deps.fetchImpl,
    });
    assert.equal(response.status, 403, email);
    return (await response.json()).features;
  };
  const owner = await digestFeatures(OWNER);
  assert.equal(owner.stock_signals_v1, true);
  const friend = await digestFeatures(FRIEND);
  assert.equal(friend.stock_signals_v1, false);
  assert.equal(friend.stock_facts_v1, false);
});

test("只認一字不差的 owner 與 all，其他寫法一律是關", async () => {
  for (const value of ["", "true", "1", "OWNER", " owner", "All", "everyone", 1, true]) {
    env.MARKET_ANA_STOCK_SIGNALS = value;
    assert.equal(Object.hasOwn(await analysisOf(OWNER), "signals"), false, JSON.stringify(value));
    assert.equal(releasePolicy(env).features.stock_signals_v1, false, JSON.stringify(value));
  }
});

test("資料庫裡存成字串的分析結果同樣依開關拿掉五塊資料", async () => {
  deps.stockPack = { ...deps.stockPack, results: {} };
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES ('v-1', '2026-11-09', 'u', ?, '2026-11-09T10:00:00Z', 1, 1)")
    .bind("5".repeat(64)).run();
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', 'v-1', NULL, '2026-11-09T10:00:00Z')").run();
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES ('v-1', '2330', '2026-11-09', ?)")
    .bind(JSON.stringify(WITH_BOTH)).run();
  env.MARKET_ANA_STOCK_SIGNALS = "owner";
  assert.deepEqual((await analysisOf(OWNER)).signals, SIGNALS);
  assert.equal(Object.hasOwn(await analysisOf(FRIEND), "signals"), false);
});

test("打開五塊不連帶打開任何其他功能；其他開關也打不開五塊", () => {
  const base = releasePolicy({}).features;
  assert.equal(base.stock_signals_v1, false);
  for (const value of ["owner", "all"]) {
    const features = releasePolicy({ MARKET_ANA_STOCK_SIGNALS: value }).features;
    assert.equal(features.stock_signals_v1, true, value);
    assert.deepEqual({ ...features, stock_signals_v1: false }, base, value);
  }
  for (const other of [{ MARKET_ANA_STRATEGIES_ENABLED: "true" }, { MARKET_ANA_STOCK_FACTS: "all" }, { MARKET_ANA_FORECAST_LEDGER: "owner" },
    { MARKET_ANA_WATCHLIST_WATCH: "owner" }, { MARKET_ANA_ENABLED_FEATURES: "stock_signals_v1,market_trend_v1" }]) {
    assert.equal(releasePolicy(other).features.stock_signals_v1, false, JSON.stringify(other));
  }
  assert.doesNotMatch(JSON.stringify(releasePolicy({ MARKET_ANA_STOCK_SIGNALS: "owner" })), /MARKET_ANA_STOCK_SIGNALS/);
});

test("正式環境設定（wrangler.jsonc）裡五塊只開站主：其他獲准使用者看不到，也不連帶打開策略與 AI", () => {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  const vars = JSON.parse(text).vars;
  assert.equal(vars.MARKET_ANA_STOCK_SIGNALS, "owner");
  const policy = releasePolicy(vars);
  assert.equal(policyForViewer(policy, true, vars).features.stock_signals_v1, true);
  assert.equal(policyForViewer(policy, false, vars).features.stock_signals_v1, false);
  assert.equal(policy.strategies_enabled, false);
  for (const feature of ["strategy_v1", "level_advice", "ai_digest", "ai_stock_advice"]) {
    assert.equal(policy.features[feature], false, feature);
  }
});
