// 國外指數的日線補到最新（本人 2026-11-24：「國外大盤的指數雖然可以看到分時，但是k線圖卻是很舊的」）。
// GET /api/indices/<key>：看得到海外分時的人，資料表的日線後面接上來源（Yahoo Finance 公開圖表入口，日線、最近一個月）比較新的那幾天。
// 來源一律換成假的，測試不連網路。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { appendRecentDaily } from "../lib/market-indices.js";
import { dailySourceUrl, resetIntradayCache, sourceUrl, transformDaily } from "../lib/index-intraday.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const FRIEND = "friend@example.com";
const T0 = Date.parse("2026-11-24T16:00:00Z");   // 美東 11-24 11:00，盤中
const NY = -18000;
const openOf = (date) => Date.parse(`${date}T14:30:00Z`) / 1000;   // 美東 09:30
const TODAY = { start: openOf("2026-11-24"), end: openOf("2026-11-24") + 6.5 * 3600 };
let issuer, env, deps, nowMs, calls, upstream;
const next = async () => new Response("static-ok", { status: 200 });

// 來源會回的那一份。bars：[秒, 開, 高, 低, 收, 量]…
function yahooDaily(bars, { period = TODAY, gmtoffset = NY, timezone = "America/New_York" } = {}) {
  const column = (i) => bars.map((bar) => bar[i]);
  return { chart: { result: [{
    meta: { exchangeTimezoneName: timezone, gmtoffset, currentTradingPeriod: period ? { regular: { ...period, gmtoffset } } : undefined },
    timestamp: column(0),
    indicators: { quote: [{ open: column(1), high: column(2), low: column(3), close: column(4), volume: column(5) }] },
  }], error: null } };
}
// 資料表到 11-20（五）；來源有 11-19、11-20、11-23（都收盤了）與 11-24（盤中，時間是最新一筆成交）
const FEED = [
  [openOf("2026-11-19"), 6900, 6990, 6890, 6950.126, 0],
  [openOf("2026-11-20"), 1, 2, 0.5, 1.5, 0],   // 和資料表同一天、數字不一樣：不可以蓋過去
  [openOf("2026-11-23"), 7010, 7120, 7000, 7100.5, 0],
  [T0 / 1000 - 30, 7100, 7155.555, 7090, 7150, 0],
];
const ok = (body) => async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const status = (code) => async () => new Response("{}", { status: code });

const D1_ROWS = [["2026-11-19", 6900, 6990, 6890, 6950, 0], ["2026-11-20", 6950, 7020, 6940, 7000, 0]];
const D1_CANDLES = D1_ROWS.map(([date, open, high, low, close, volume]) => ({ date, open, high, low, close, volume }));

beforeEach(async () => {
  resetIntradayCache();
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_INDEX_INTRADAY: "all" };
  nowMs = T0;
  calls = [];
  upstream = ok(yahooDaily(FEED));
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => nowMs, intradayCache: null,
    intradayFetch: async (url, init) => { calls.push([String(url), init]); return upstream(url, init); } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(FRIEND, "F", "approved", "2026-11-09T00:00:00Z").run();
  for (const [date, open, high, low, close, volume] of D1_ROWS) {
    await env.DB.prepare("INSERT INTO overseas_quotes (symbol, date, open, high, low, close, volume, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'eodhd', ?)")
      .bind("SOX.INDX", date, open, high, low, close, volume, "2026-11-20T23:20:00Z").run();
  }
});

async function req(path, { email = OWNER } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { headers }), env, next, deps);
}
const body = async (path, options) => (await req(path, options)).json();

test("資料表到 11-20、來源有到 11-24：資料表的每一天照原樣，後面接 11-23（收盤）與 11-24（盤中，標 provisional）", async () => {
  const response = await req("/api/indices/sox");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  const data = await response.json();
  assert.deepEqual(data.candles, [
    ...D1_CANDLES,
    { date: "2026-11-23", open: 7010, high: 7120, low: 7000, close: 7100.5, volume: null },
    { date: "2026-11-24", open: 7100, high: 7155.56, low: 7090, close: 7150, volume: null, provisional: true },
  ]);
  assert.deepEqual([data.ok, data.key, data.symbol, data.zone, data.fetch], [true, "sox", "SOX.INDX", "us", null]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?interval=1d&range=1mo");
  assert.equal(calls[0][0], dailySourceUrl("^SOX"));
  assert.match(calls[0][1].headers["user-agent"], /^Mozilla\/5\.0 /);
});

test("來源回錯、被擋、格式不對、連不上：照資料表的回，不是錯誤", async () => {
  const failures = [status(429), status(500), async () => new Response("<html>", { status: 200 }), ok({ chart: { result: null, error: { code: "x" } } }),
    async () => { throw new Error("network"); }];
  for (const [i, failure] of failures.entries()) {
    resetIntradayCache();
    upstream = failure;
    const response = await req("/api/indices/sox");
    assert.equal(response.status, 200, `第 ${i} 種`);
    assert.deepEqual((await response.json()).candles, D1_CANDLES, `第 ${i} 種`);
  }
  assert.equal(calls.length, failures.length);
});

test("開關沒開、或只開站主而來的是朋友：和原來一模一樣，而且不問來源", async () => {
  for (const [value, owner, friend] of [[undefined, false, false], ["off", false, false], ["owner", true, false], ["all", true, true]]) {
    resetIntradayCache();
    if (value === undefined) delete env.MARKET_ANA_INDEX_INTRADAY; else env.MARKET_ANA_INDEX_INTRADAY = value;
    calls = [];
    const theirs = await body("/api/indices/sox", { email: FRIEND });
    assert.equal(theirs.candles.length, friend ? 4 : 2, `朋友 ${value}`);
    if (!friend) {
      assert.deepEqual(theirs, { ok: true, key: "sox", symbol: "SOX.INDX", name: "費城半導體", zone: "us", candles: D1_CANDLES, fetch: null });
      assert.equal(calls.length, 0, `朋友不問來源 ${value}`);
    }
    const mine = await body("/api/indices/sox");
    assert.equal(mine.candles.length, owner ? 4 : 2, `站主 ${value}`);
    assert.equal(calls.length, owner ? 1 : 0, `問來源的次數 ${value}`);
  }
});

test("同一個指數 5 分鐘內只問來源一次（不同人也一樣）；過了才再問；和分時各存各的", async () => {
  await body("/api/indices/sox");
  nowMs += 299_000;
  assert.equal((await body("/api/indices/sox", { email: FRIEND })).candles.length, 4);
  assert.equal(calls.length, 1);
  nowMs += 2_000;
  await body("/api/indices/sox");
  assert.equal(calls.length, 2);
  // 分時那一份（5 分鐘線）另外問、另外存，不會把日線的快取當成分時回出去
  const session = TODAY.start;
  upstream = ok({ chart: { result: [{ meta: { exchangeTimezoneName: "America/New_York", gmtoffset: NY, chartPreviousClose: 7100, regularMarketPrice: 7150,
    currentTradingPeriod: { regular: TODAY } }, timestamp: [session, session + 300], indicators: { quote: [{ close: [7110, 7150] }] } }], error: null } });
  const intraday = await body("/api/indices/sox/intraday");
  assert.deepEqual([intraday.available, intraday.points.length, calls.length, calls[2][0]], [true, 2, 3, sourceUrl("^SOX")]);
  assert.equal((await body("/api/indices/sox")).candles.length, 4);
  assert.equal(calls.length, 3);
});

test("來源後來失敗：24 小時內用上一次成功的那一份（盤中抓到的那一根照樣標 provisional）；剛失敗過 30 秒內不再撞", async () => {
  const first = await body("/api/indices/sox");
  nowMs += 8 * 3_600_000;   // 已經收盤了，但來源被擋
  upstream = status(429);
  assert.deepEqual((await body("/api/indices/sox")).candles, first.candles);
  nowMs += 10_000;
  await body("/api/indices/sox");
  assert.equal(calls.length, 2);
  nowMs += 24 * 3_600_000;
  assert.deepEqual((await body("/api/indices/sox")).candles, D1_CANDLES, "超過 24 小時的上一份不用");
});

test("資料表已經比來源新、或資料表是空的：不接；空的連來源都不問", async () => {
  upstream = ok(yahooDaily(FEED.slice(0, 2)));
  assert.deepEqual((await body("/api/indices/sox")).candles, D1_CANDLES);
  const empty = await body("/api/indices/dji");
  assert.deepEqual([empty.candles, empty.fetch], [[], null]);
  assert.equal(calls.length, 1);
  assert.equal((await req("/api/indices/nq_fut")).status, 404, "期貨照舊沒有日線");
});

// ---------- 來源的那一份 → 日線（純函式） ----------
test("轉換：開高低收有任何一個沒有價（null、0、不是數字）的那一天不給；由舊到新；價取到小數兩位；量沒有或 0 給 null", () => {
  const out = transformDaily(yahooDaily([
    [openOf("2026-11-20"), 10, 12, 9, 11.456, 5e8],
    [openOf("2026-11-18"), 8, 9, 7, 8.5, 0],
    [openOf("2026-11-19"), 9, null, 8, 9.5, 1],
    [openOf("2026-11-23"), null, null, null, null, null],
    [openOf("2026-11-17"), 7, 8, 0, 7.5, 1],
    [null, 1, 1, 1, 1, 1],
  ], { period: null }), T0 / 1000);
  assert.deepEqual(out, { available: true, bars: [
    { date: "2026-11-18", open: 8, high: 9, low: 7, close: 8.5, volume: null },
    { date: "2026-11-20", open: 10, high: 12, low: 9, close: 11.46, volume: 5e8 },
  ] });
});

test("轉換：日期是交易所當地的那一天（日經的 09:00 是 UTC 的 00:00；沒給時差就用時區名稱）；同一天兩根留時間晚的", () => {
  const tokyo = (date) => Date.parse(`${date}T00:00:00Z`) / 1000;
  const jp = transformDaily(yahooDaily([[tokyo("2026-11-20"), 1, 2, 1, 2, 0], [tokyo("2026-11-24"), 3, 4, 3, 4, 0], [tokyo("2026-11-24") + 3600, 3, 5, 3, 5, 0]],
    { gmtoffset: 32400, timezone: "Asia/Tokyo", period: null }), T0 / 1000);
  assert.deepEqual(jp.bars.map((bar) => [bar.date, bar.close]), [["2026-11-20", 2], ["2026-11-24", 5]]);
  // 美股收盤前後（UTC 已經是隔天）照美東的日期
  const late = Date.parse("2026-11-24T01:30:00Z") / 1000;
  assert.equal(transformDaily(yahooDaily([[late, 1, 2, 1, 2, 0]], { gmtoffset: null, period: null }), T0 / 1000).bars[0].date, "2026-11-23");
});

test("轉換：provisional 只標在「現在這個交易時段那一天、而且還沒收盤」的最後一根", () => {
  const flags = (nowSec, bars = FEED, options) => transformDaily(yahooDaily(bars, options), nowSec).bars.map((bar) => bar.provisional === true);
  assert.deepEqual(flags(T0 / 1000), [false, false, false, true], "盤中");
  assert.deepEqual(flags(TODAY.end + 60), [false, false, false, false], "收盤後");
  assert.deepEqual(flags(TODAY.start - 3600, FEED.slice(0, 3)), [false, false, false], "開盤前：最後一根是上一個交易日，已經收完");
  assert.deepEqual(flags(T0 / 1000, FEED, { period: null }), [false, false, false, false], "來源沒給交易時段");
});

test("轉換：格式不對、沒有任何一根有價：當成來源失敗（null）", () => {
  const good = yahooDaily(FEED);
  for (const bad of [null, "text", {}, { chart: null }, { chart: { result: [], error: null } }, { chart: { ...good.chart, error: { code: "x" } } },
    { chart: { result: [{ ...good.chart.result[0], meta: null }], error: null } },
    { chart: { result: [{ ...good.chart.result[0], timestamp: "nope" }], error: null } },
    { chart: { result: [{ ...good.chart.result[0], indicators: { quote: [{ close: [1, 2, 3, 4] }] } }], error: null } },
    yahooDaily([[openOf("2026-11-23"), null, null, null, null, null]])]) {
    assert.equal(transformDaily(bad, T0 / 1000), null, JSON.stringify(bad)?.slice(0, 80));
  }
});

test("接上：只接比資料表最後一天新的；資料表是空的、來源沒有（null）都照原樣", () => {
  const recent = [{ date: "2026-11-20", close: 1 }, { date: "2026-11-23", close: 2 }];
  assert.deepEqual(appendRecentDaily(D1_CANDLES, recent), [...D1_CANDLES, { date: "2026-11-23", close: 2 }]);
  assert.equal(appendRecentDaily(D1_CANDLES, null), D1_CANDLES);
  assert.deepEqual(appendRecentDaily([], recent), []);
});
