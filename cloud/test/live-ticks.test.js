// 秒級第一步（本人 2026-11-14「可以，先做當天」）：當天逐筆成交，由瀏覽器彙總成秒 K。
// 樣本列逐字取自 Fugle 官方文件 intraday/trades 的回應範例（2330，2023-05-29）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fetchTickPage, parseTrades, PAGE_SIZE } from "../lib/live-ticks.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const AFTER_HOURS = { price: 568, size: 32, time: 1685341800000000, serial: 99999999 };
const CLOSE = { bid: 567, ask: 568, price: 568, size: 4778, volume: 54538, time: 1685338200000000, serial: 6652422 };
const TRADE = { bid: 565, ask: 566, price: 566, size: 1, volume: 49760, time: 1685337899721587, serial: 6622549 };
const body = (data, extra = {}) => ({ date: "2023-05-29", type: "EQUITY", exchange: "TWSE", market: "TSE", symbol: "2330", data, ...extra });

test("逐筆成交轉成〔毫秒, 價格, 張數, 流水號〕，盤後定價那一列不算進盤中", () => {
  const parsed = parseTrades(body([TRADE, CLOSE, AFTER_HOURS]), "2330");
  assert.equal(parsed.date, "2023-05-29");
  assert.equal(parsed.rows, 3);  // 下一頁的位移以官方回傳的列數計，包含被略過的盤後列
  assert.deepEqual(parsed.ticks, [[1685337899722, 566, 1, 6622549], [1685338200000, 568, 4778, 6652422]]);
});

test("不是指定股票、或沒有成交明細陣列的回應視為錯誤，不當成「今天沒有成交」", () => {
  assert.throws(() => parseTrades(body([TRADE], { symbol: "2317" }), "2330"));
  assert.throws(() => parseTrades({ date: "2023-05-29", symbol: "2330" }, "2330"));
});

test("向 Fugle 由舊到新取一頁：位移照給、一頁 500 筆，回報下一頁位移與是否已到最新", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push([String(url), init.headers["X-API-KEY"]]);
    return new Response(JSON.stringify(body([TRADE, CLOSE])), { status: 200 });
  };
  const page = await fetchTickPage({ code: "2330", offset: 1000, fugleApiKey: "k", fetchImpl });
  assert.deepEqual(calls, [["https://api.fugle.tw/marketdata/v1.0/stock/intraday/trades/2330?limit=500&offset=1000&sort=asc", "k"]]);
  assert.equal(PAGE_SIZE, 500);
  assert.deepEqual({ date: page.date, offset: page.offset, next: page.next_offset, complete: page.complete, n: page.ticks.length },
    { date: "2023-05-29", offset: 1000, next: 1002, complete: true, n: 2 });
});

test("Fugle 回 429 時帶出要等多久，其他非 200 也照實丟出", async () => {
  const limited = async () => new Response("", { status: 429, headers: { "retry-after": "37" } });
  await assert.rejects(fetchTickPage({ code: "2330", offset: 0, fugleApiKey: "k", fetchImpl: limited }),
    (error) => error.status === 429 && error.retryAfter === 37);
  const broken = async () => new Response("", { status: 500 });
  await assert.rejects(fetchTickPage({ code: "2330", offset: 0, fugleApiKey: "k", fetchImpl: broken }), (error) => error.status === 500);
});

// ---- 經站台的 API：只有站主拿得到（行情商條款不得轉給第三方，與既有即時行情一致） ----
const STOCKS = { as_of: "2026-11-09", analysis_schema: "stock-analysis/v2",
  results: { "2330": JSON.stringify({ schema: "stock-analysis/v2", code: "2330", market: "上市" }) } };
let issuer, env, deps, asked;
const next = async () => new Response("static-ok");

beforeEach(async () => {
  issuer = await makeIssuer();
  asked = [];
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, FUGLE_API_KEY: "secret-key" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), stockPack: STOCKS, now: () => Date.parse("2026-11-10T02:22:05Z"),
    tickPage: async (args) => { asked.push(args); return { ok: true, code: args.code, date: "2026-11-10", offset: args.offset, next_offset: args.offset + 2, complete: true, ticks: [[1, 2, 3, 4], [5, 6, 7, 8]] }; } };
});
async function req(path, email = OWNER) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { headers }), env, next, deps);
}

test("站主取得當天逐筆；核准的非站主與未登入者拿不到，金鑰不出現在回應裡", async () => {
  const response = await req("/api/stocks/2330/ticks?offset=500");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  const text = await response.text();
  assert.doesNotMatch(text, /secret-key/);
  assert.deepEqual(JSON.parse(text), { ok: true, code: "2330", date: "2026-11-10", offset: 500, next_offset: 502, complete: true, ticks: [[1, 2, 3, 4], [5, 6, 7, 8]] });
  assert.deepEqual(asked.map(({ code, offset, fugleApiKey }) => [code, offset, fugleApiKey]), [["2330", 500, "secret-key"]]);
  assert.equal((await req("/api/stocks/2330/ticks", null)).status, 401);
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind("ok@x.com", "n", "approved", "2026-11-10T00:00:00Z").run();
  assert.equal((await req("/api/stocks/2330/ticks", "ok@x.com")).status, 403);
  assert.equal(asked.length, 1);
});

// 本人 2026-11-24 晚「全部都開」：MARKET_ANA_LIVE_QUOTES 是 "all" 時獲准使用者也拿得到逐筆；
// 逐筆每次都直接問富果，所以站主以外的人另外共用一份每分鐘的總額度（ticksShared），站主自己不算在裡面
test("開關是 all：獲准的朋友拿得到逐筆；朋友們合計超過每分鐘總額度就 429、不再問富果，站主不受影響", async () => {
  env.MARKET_ANA_LIVE_QUOTES = "all";
  deps.rate = { ticksShared: 3 };
  for (const email of ["a@x.com", "b@x.com", "p@x.com"]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
      .bind(email, "n", email === "p@x.com" ? "pending" : "approved", "2026-11-10T00:00:00Z").run();
  }
  const first = await req("/api/stocks/2330/ticks?offset=0", "a@x.com");
  assert.equal(first.status, 200);
  assert.doesNotMatch(await first.text(), /secret-key/);
  assert.equal((await req("/api/stocks/2330/ticks", "a@x.com")).status, 200);
  assert.equal((await req("/api/stocks/2330/ticks", "b@x.com")).status, 200);
  const limited = await req("/api/stocks/2330/ticks", "b@x.com");
  assert.equal(limited.status, 429, "朋友們合計第 4 次");
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
  assert.equal(asked.length, 3, "超過總額度的那一次沒有問富果");
  assert.equal((await req("/api/stocks/2330/ticks")).status, 200, "站主不算在朋友的總額度裡");
  assert.equal((await req("/api/stocks/2330/ticks", "p@x.com")).status, 403, "還沒核准的人照樣擋");
  for (const value of [undefined, "owner", "ALL", "on"]) {
    env.MARKET_ANA_LIVE_QUOTES = value;
    assert.equal((await req("/api/stocks/2330/ticks", "a@x.com")).status, 403, String(value));
  }
});

test("位移必須是非負整數；沒有設定行情金鑰或上游失敗時明講拿不到，不回空資料假裝沒有成交", async () => {
  assert.equal((await req("/api/stocks/2330/ticks?offset=-1")).status, 400);
  assert.equal((await req("/api/stocks/2330/ticks?offset=abc")).status, 400);
  deps.tickPage = async () => { const error = new Error("Fugle HTTP 429"); error.status = 429; error.retryAfter = 37; throw error; };
  const limited = await req("/api/stocks/2330/ticks");
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), "37");
  deps.tickPage = async () => { throw new Error("boom"); };
  const failed = await req("/api/stocks/2330/ticks");
  assert.equal(failed.status, 503);
  assert.equal((await failed.json()).error, "ticks-unavailable");
  delete env.FUGLE_API_KEY;
  const unset = await req("/api/stocks/2330/ticks");
  assert.equal(unset.status, 503);
  assert.equal((await unset.json()).error, "ticks-unavailable");
});

test("逐筆有自己的額度（每分鐘 40 次），不佔用報價輪詢的額度", async () => {
  for (let i = 0; i < 40; i++) assert.equal((await req("/api/stocks/2330/ticks")).status, 200);
  const over = await req("/api/stocks/2330/ticks");
  assert.equal(over.status, 429);
  assert.ok(Number(over.headers.get("retry-after")) > 0);
  deps.liveQuoteService = { snapshot: async () => ({ ok: true, code: "2330", market: "tse", market_open: true, live: true, quote: { price: 1 }, candles: {}, retry_after_seconds: 0 }) };
  assert.equal((await req("/api/stocks/2330/live")).status, 200);
});
