// 個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）的網站入口：
// 開關三種狀態（關、只開站主、開給全部獲准使用者）下，誰拿得到個股分析結果裡的 facts。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { releasePolicy } from "../lib/release-policy.js";
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

test("正式環境設定（wrangler.jsonc）裡事實區塊是關的", () => {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  assert.equal(releasePolicy(JSON.parse(text).vars).features.stock_facts_v1, false);
});
