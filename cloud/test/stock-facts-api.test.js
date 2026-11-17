// 個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）的網站入口：
// 開關三種狀態（關、只開站主、開給全部獲准使用者）下，誰拿得到個股分析結果裡的 facts。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const EXAMPLE = JSON.parse(readFileSync(new URL("./fixtures/stock-analysis-v2.example.json", import.meta.url), "utf8"));
const FACTS = {
  data_date: "2026-11-06",
  today: [
    { key: "close", label: "收盤", tone: "up", text: "收紅 +4.68%" },
    { key: "volume", label: "成交量", tone: null, text: "均量的 1.15 倍" },
    { key: "foreign", label: "外資", tone: "down", text: "賣超 8,759 張" },
  ],
  blocks: [],
};
const WITH_FACTS = { ...EXAMPLE, code: "2330", name: "台積電", facts: FACTS };
const FRIEND = "friend@x.com";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    stockPack: { as_of: "2026-11-06", analysis_schema: "stock-analysis/v2", results: { "2330": WITH_FACTS } },
    now: () => Date.parse("2026-11-07T10:00:05Z"),
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
const featureOf = async (email) => (await get("/api/features", email)).features.stock_facts_v1;

test("開關沒設（正式預設）：站主與其他獲准使用者都拿不到 facts，功能清單是關的", async () => {
  for (const email of [OWNER, FRIEND]) {
    const body = await analysisOf(email);
    assert.equal(Object.hasOwn(body, "facts"), false, email);
    assert.ok(Array.isArray(body.levels.support), "其他內容照常");
    assert.equal(await featureOf(email), false, email);
  }
});

test("只開站主：站主拿得到 facts；其他獲准使用者的個股分析與功能清單和開關關著時一樣", async () => {
  env.MARKET_ANA_STOCK_FACTS = "owner";
  assert.deepEqual((await analysisOf(OWNER)).facts, FACTS);
  assert.equal(await featureOf(OWNER), true);
  assert.equal(Object.hasOwn(await analysisOf(FRIEND), "facts"), false);
  assert.equal(await featureOf(FRIEND), false);
});

test("開給全部：站主與其他獲准使用者都拿得到 facts", async () => {
  env.MARKET_ANA_STOCK_FACTS = "all";
  for (const email of [OWNER, FRIEND]) {
    assert.deepEqual((await analysisOf(email)).facts, FACTS, email);
    assert.equal(await featureOf(email), true, email);
  }
});

test("未登入拿不到個股分析，三種開關狀態都一樣", async () => {
  for (const value of [undefined, "owner", "all"]) {
    if (value === undefined) delete env.MARKET_ANA_STOCK_FACTS; else env.MARKET_ANA_STOCK_FACTS = value;
    const response = await handle(new Request("https://market-ana.pages.dev/api/stocks/2330/analysis"), env, next, deps);
    assert.equal(response.status, 401, String(value));
    assert.doesNotMatch(await response.text(), /收紅|facts/, String(value));
  }
});

test("只開站主時，其他人從大盤資料與水位入口拿到的功能清單也看不到這一項是開的", async () => {
  env.MARKET_ANA_STOCK_FACTS = "owner";
  deps.marketPack = { version: "v1", overview: { analysis: {}, dashboard: {} }, details: {} };
  assert.equal((await get("/api/market/overview", OWNER)).features.stock_facts_v1, true);
  assert.equal((await get("/api/market/overview", FRIEND)).features.stock_facts_v1, false);
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(FRIEND)));
  const level = await handle(new Request("https://market-ana.pages.dev/api/holdings/level", { headers: h }), env, next, deps);
  assert.equal(level.status, 403);
  assert.equal((await level.json()).features.stock_facts_v1, false);
});

test("只認一字不差的 owner 與 all，其他寫法一律是關", async () => {
  for (const value of ["", "true", "1", "OWNER", " owner", "All", "everyone", 1, true]) {
    env.MARKET_ANA_STOCK_FACTS = value;
    assert.equal(Object.hasOwn(await analysisOf(OWNER), "facts"), false, JSON.stringify(value));
    assert.equal(releasePolicy(env).features.stock_facts_v1, false, JSON.stringify(value));
  }
});

test("資料庫裡存成字串的分析結果同樣依開關拿掉 facts", async () => {
  deps.stockPack = { ...deps.stockPack, results: {} };
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES ('v-1', '2026-11-06', 'u', ?, '2026-11-06T10:00:00Z', 1, 1)")
    .bind("5".repeat(64)).run();
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', 'v-1', NULL, '2026-11-06T10:00:00Z')").run();
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES ('v-1', '2330', '2026-11-06', ?)")
    .bind(JSON.stringify(WITH_FACTS)).run();
  env.MARKET_ANA_STOCK_FACTS = "owner";
  assert.deepEqual((await analysisOf(OWNER)).facts, FACTS);
  assert.equal(Object.hasOwn(await analysisOf(FRIEND), "facts"), false);
});

test("打開事實區塊不連帶打開任何其他功能", () => {
  const base = releasePolicy({}).features;
  for (const value of ["owner", "all"]) {
    const features = releasePolicy({ MARKET_ANA_STOCK_FACTS: value }).features;
    assert.equal(features.stock_facts_v1, true, value);
    assert.deepEqual({ ...features, stock_facts_v1: false }, base, value);
  }
  assert.doesNotMatch(JSON.stringify(releasePolicy({ MARKET_ANA_STOCK_FACTS: "owner" })), /MARKET_ANA_STOCK_FACTS/);
});

// 預測帳本的站主限制在 API 層（forecast.js：非站主一律 available:false），不在功能清單，這裡只鎖自選股關注。
test("正式環境設定（本人 11-17「全部照做」）：事實區塊開給全部獲准使用者；自選股關注仍只給站主", () => {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  const vars = JSON.parse(text).vars;
  assert.equal(vars.MARKET_ANA_STOCK_FACTS, "all");
  const policy = releasePolicy(vars);
  assert.equal(policyForViewer(policy, true, vars).features.stock_facts_v1, true);
  assert.equal(policyForViewer(policy, false, vars).features.stock_facts_v1, true);
  assert.equal(policyForViewer(policy, false, vars).features.watchlist_watch_v1, false);
});


// ---------- 重大訊息（D 票）：/api/stocks/<代號>/announcements，跟著事實區塊的開關 ----------
const ITEMS = [
  { market: "上市", code: "2330", name: "台積電", date: "2026-11-15", time: "17:47:06", subject: "董事會決議", clause: "第14款", fact_date: "2026-11-15", body: "說明全文" },
  { market: "上市", code: "2330", name: "台積電", date: "2026-11-03", time: "07:00:04", subject: "法說會", clause: "第12款", fact_date: "2026-11-20", body: null },
];

async function seedAnnouncements() {
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES ('v-1', '2026-11-16', 'u', ?, '2026-11-16T10:00:00Z', 1, 1)")
    .bind("5".repeat(64)).run();
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', 'v-1', NULL, '2026-11-16T10:00:00Z')").run();
  await env.DB.prepare("INSERT INTO stock_announcements (version, code, as_of, payload) VALUES ('v-1', '2330', '2026-11-16', ?)")
    .bind(JSON.stringify({ schema: "stock-announcements/v1", code: "2330", items: ITEMS })).run();
}
const announcementsOf = (code, email) => get(`/api/stocks/${code}/announcements`, email);

test("重大訊息：只開站主時站主拿得到這一檔近 90 天的清單（新到舊），其他人拿到「沒有內容」", async () => {
  await seedAnnouncements();
  env.MARKET_ANA_STOCK_FACTS = "owner";
  assert.deepEqual(await announcementsOf("2330", OWNER), { available: true, code: "2330", items: ITEMS });
  assert.deepEqual(await announcementsOf("2330", FRIEND), { available: false });
});

test("重大訊息：開關關著時連站主也是「沒有內容」；開給全部時其他獲准使用者也拿得到", async () => {
  await seedAnnouncements();
  assert.deepEqual(await announcementsOf("2330", OWNER), { available: false });
  env.MARKET_ANA_STOCK_FACTS = "all";
  assert.deepEqual((await announcementsOf("2330", FRIEND)).items, ITEMS);
});

test("重大訊息：這幾天沒有公告的代號是空清單；資料表還沒建立時也是空清單，不是錯誤", async () => {
  await seedAnnouncements();
  env.MARKET_ANA_STOCK_FACTS = "owner";
  assert.deepEqual(await announcementsOf("1101", OWNER), { available: true, code: "1101", items: [] });
  env.DB._raw.exec("DROP TABLE stock_announcements");
  assert.deepEqual(await announcementsOf("2330", OWNER), { available: true, code: "2330", items: [] });
});

test("重大訊息：未登入擋下、代號格式不對回 400", async () => {
  env.MARKET_ANA_STOCK_FACTS = "all";
  const anonymous = await handle(new Request("https://market-ana.pages.dev/api/stocks/2330/announcements"), env, next, deps);
  assert.equal(anonymous.status, 401);
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(OWNER)));
  const bad = await handle(new Request("https://market-ana.pages.dev/api/stocks/23%2730/announcements", { headers: h }), env, next, deps);
  assert.notEqual(bad.status, 200);
});


test("重大訊息：已登入但還沒核准的人被擋下", async () => {
  env.MARKET_ANA_STOCK_FACTS = "all";
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES ('pending@x.com', 'p', 'pending', '2026-11-07T00:00:00Z')").run();
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims("pending@x.com")));
  const response = await handle(new Request("https://market-ana.pages.dev/api/stocks/2330/announcements", { headers: h }), env, next, deps);
  assert.equal(response.status, 403);
});
