// 本人 2026-11-24「把我所擁有的全部功能都開放給我本人以外都可以一起使用」：用正式環境設定（wrangler.jsonc）的開關值，
// 確認獲准的朋友拿到和站主同一份「大家共用」的內容，而站主自己的東西（自選股與庫存衍生的盯項、追蹤事項、個人筆記）一筆都拿不到。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { resetIntradayCache } from "../lib/index-intraday.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const VARS = JSON.parse(readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "")).vars;
const ORIGIN = "https://market-ana.pages.dev";
const FRIEND = "friend@example.com";
const NOW = Date.parse("2026-11-24T07:00:00Z");   // 台北 11-24 15:00
const TOKYO_OPEN = Date.parse("2026-11-24T00:00:00Z") / 1000;
let issuer, env, deps, calls;
const next = async () => new Response("static-ok", { status: 200 });

// 站主自己的東西：個股盯項（代號、名稱、條件、筆記、因應）與追蹤事項；朋友的回應裡一個字都不能有
const SECRETS = ["2454", "3008", "聯發科", "大立光", "OWNER-NOTE", "OWNER-PREPARE", "OWNER-RESULT", "OWNER-TRACKING", OWNER];

async function addWatch(id, over = {}) {
  const row = {
    source_block: "market_news", source_date: "2026-11-21", source_ref: `ref-${id}`, subject: "market", kind: "numeric",
    condition_key: id, params: "{}", label: `條件 ${id}`, note: null, impact: 2, prepare: JSON.stringify(["如果成立，就先檢查成交量"]),
    created_date: "2026-11-21", expires_on: "2026-12-19", days_left: 18, status: "watching", result_date: null, result_note: null,
    show_until: null, visibility: "all", ...over,
  };
  const names = Object.keys(row);
  await env.DB.prepare(`INSERT INTO impact_watch_items (id, ${names.join(", ")}, created_at) VALUES (?, ${names.map(() => "?").join(", ")}, ?)`)
    .bind(id, ...names.map((name) => row[name]), "2026-11-21T06:05:00Z").run();
}

function yahoo() {
  const bars = Array.from({ length: 20 }, (_, i) => [TOKYO_OPEN + i * 300, 50000 + i * 10]);
  const meta = { exchangeTimezoneName: "Asia/Tokyo", gmtoffset: 32400, regularMarketPrice: 50190, chartPreviousClose: 50000, previousClose: 50000,
    regularMarketTime: bars.at(-1)[0], currentTradingPeriod: { regular: { start: TOKYO_OPEN, end: TOKYO_OPEN + 6.5 * 3600, timezone: "JST", gmtoffset: 32400 } } };
  return { chart: { result: [{ meta, timestamp: bars.map((b) => b[0]), indicators: { quote: [{ close: bars.map((b) => b[1]), volume: bars.map(() => 0) }] } }], error: null } };
}

beforeEach(async () => {
  resetIntradayCache();
  issuer = await makeIssuer();
  env = { ...VARS, DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  calls = [];
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW, intradayCache: null,
    rate: { market: 60, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40, indexIntraday: 60 },
    intradayFetch: async (url) => { calls.push(String(url)); return new Response(JSON.stringify(yahoo()), { status: 200 }); } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'F', 'approved', ?)").bind(FRIEND, "2026-11-09T00:00:00Z").run();
  // 大盤的盯項（所有人可見）
  await addWatch("below_ma20", { label: "加權指數收盤跌破月線", note: "外資續賣時容易先破月線", impact: 3 });
  await addWatch("foreign_sell_streak", { label: "外資連續賣超達 9 天", status: "met", result_date: "2026-11-21",
    result_note: "成立：外資連續賣超 9 天。", show_until: "2026-11-26", days_left: null });
  // 站主自選股與庫存的個股盯項（只給站主）；其中一筆被排程標錯成所有人可見，也不能回給別人
  const stock = { visibility: "owner", note: "OWNER-NOTE 法說後賣壓", prepare: JSON.stringify(["如果跌破季線，就先確認 OWNER-PREPARE"]) };
  await addWatch("stock_2454", { ...stock, subject: "2454", label: "收盤跌破季線", params: JSON.stringify({ name: "聯發科", ma: 60 }) });
  await addWatch("stock_3008", { ...stock, subject: "3008", label: "收盤站上壓力", params: JSON.stringify({ name: "大立光", level: 2500 }),
    status: "failed", result_date: "2026-11-21", result_note: "否定：OWNER-RESULT", show_until: "2026-11-26", days_left: null });
  await addWatch("stock_mislabelled", { ...stock, subject: "2454", visibility: "all", condition_key: "below_ma60", params: JSON.stringify({ name: "聯發科" }) });
  // 盤勢解讀：大家共用的類別＋只給站主的追蹤事項
  const cat = (key, say) => ({ key, say, effect: `${key} 的影響`, conditions: [], prepare: [], data_date: "2026-11-24", quiet: false });
  const brief = { schema: "market-brief/v1", trade_date: "2026-11-24", slot: "close", generated_at: "2026-11-24T14:31:00+08:00", source: "ai",
    one_line: "外資續賣，指數守在月線附近", lead: "chips", cats: [cat("chips", "外資連 9 賣"), cat("tech", "站在月線之上"), cat("tracking", "OWNER-TRACKING 聯發科站上壓力")] };
  await env.DB.prepare("INSERT INTO market_brief_runs (trade_date, slot, generated_at, source, model, payload) VALUES (?, ?, ?, ?, ?, ?)")
    .bind("2026-11-24", "close", "2026-11-24T06:31:00Z", "ai", "test-model", JSON.stringify(brief)).run();
});

async function req(path, email) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  return handle(new Request(ORIGIN + path, { headers }), env, next, deps);
}
const noSecrets = (text, where) => { for (const secret of SECRETS) assert.ok(!text.includes(secret), `${where} 不該出現 ${secret}`); };

test("正式設定：盯項、盤勢解讀、海外分時開給全部獲准使用者；策略與 AI 解讀照舊關著", () => {
  assert.equal(VARS.MARKET_ANA_IMPACT_WATCH, "all");
  assert.equal(VARS.MARKET_ANA_MARKET_BRIEF, "all");
  assert.equal(VARS.MARKET_ANA_INDEX_INTRADAY, "all");
  assert.equal(VARS.MARKET_ANA_FORECAST_LEDGER, "all");
  assert.equal(VARS.MARKET_ANA_LIVE_QUOTES, "all");
  assert.equal(VARS.MARKET_ANA_BROKER_BRANCHES, "owner", "有資料的股票＝站主的名單：抓取範圍決定前先不開");
  assert.equal(VARS.MARKET_ANA_WATCHLIST_WATCH, "owner", "自選股關注還沒有朋友版");
  assert.equal(VARS.MARKET_ANA_STRATEGIES_ENABLED, undefined, "未驗證的策略照舊關著");
});

test("正式設定下朋友的功能清單：三項是開的；站主的 AI 解讀照舊是關的", async () => {
  const features = (await (await req("/api/features", FRIEND)).json()).features;
  assert.equal(features.impact_watch_v1, true);
  assert.equal(features.market_brief_v1, true);
  assert.equal(features.index_intraday_v1, true);
  assert.equal(features.forecast_ledger_v1, true);
  assert.equal(features.broker_branches_v1, false);
  assert.equal(features.watchlist_watch_v1, false);
  assert.equal(features.ai_digest, false);
});

test("正式設定下的盯項：朋友只拿到大盤的兩筆（和站主那兩筆一模一樣），個股的、筆記、因應一筆都沒有", async () => {
  const friendText = await (await req("/api/impact-watch", FRIEND)).text();
  noSecrets(friendText, "朋友的盯項");
  const friend = JSON.parse(friendText);
  assert.equal(friend.available, true);
  assert.deepEqual(friend.items.map((item) => item.id).sort(), ["below_ma20", "foreign_sell_streak"]);
  assert.ok(friend.items.every((item) => item.subject === "market" && !("subject_name" in item)));
  const owner = (await (await req("/api/impact-watch", OWNER)).json()).items;
  assert.equal(owner.length, 5, "站主照舊全部拿得到");
  assert.deepEqual(friend.items, owner.filter((item) => item.subject === "market"), "朋友拿到的大盤盯項和站主的同一份");
});

test("正式設定下的盤勢解讀：朋友拿到和站主同一份，只少追蹤事項那一類", async () => {
  const friendText = await (await req("/api/market-brief", FRIEND)).text();
  noSecrets(friendText, "朋友的盤勢解讀");
  const friend = JSON.parse(friendText).brief;
  const owner = (await (await req("/api/market-brief", OWNER)).json()).brief;
  assert.deepEqual(owner.cats.map((cat) => cat.key), ["chips", "tech", "tracking"]);
  assert.deepEqual(friend, { ...owner, cats: owner.cats.filter((cat) => cat.key !== "tracking") });
});

test("正式設定下的海外分時：朋友拿到和站主同一份公開指數資料；同一個指數 60 秒內只問來源一次", async () => {
  deps.intradayCache = undefined;   // 用真正的共用快取
  const owner = await (await req("/api/indices/n225/intraday", OWNER)).text();
  const friend = await (await req("/api/indices/n225/intraday", FRIEND)).text();
  assert.equal(friend, owner);
  assert.equal(JSON.parse(friend).available, true);
  noSecrets(friend, "朋友的海外分時");
  assert.equal(calls.length, 1, "朋友不會讓來源多被問一次");
});
