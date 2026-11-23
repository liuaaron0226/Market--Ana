// 海外大盤的分時（本人 2026-11-24，.scratch/index-intraday 票 01）：GET /api/indices/<key>/intraday。
// 來源（Yahoo Finance 公開圖表入口）一律換成假的，測試不連網路。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { MARKET_INDICES } from "../lib/market-indices.js";
import { INTRADAY_INDICES, resetIntradayCache, sourceUrl, transformChart } from "../lib/index-intraday.js";
import { policyForViewer, releasePolicy } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const FRIEND = "friend@example.com";
const T0 = Date.parse("2026-11-24T07:00:00Z");
// 日經 11-24：09:00～15:30（日本時間）＝ 00:00～06:30 UTC
const TOKYO_OPEN = Date.parse("2026-11-24T00:00:00Z") / 1000;
const TOKYO_CLOSE = TOKYO_OPEN + 6.5 * 3600;
let issuer, env, deps, nowMs, calls, upstream;
const next = async () => new Response("static-ok", { status: 200 });

// 來源會回的那一份。bars：[秒, 收盤或 null]…
function yahoo({ bars, prev = 50000, price, timezone = "Asia/Tokyo", gmtoffset = 32400, period = { start: TOKYO_OPEN, end: TOKYO_CLOSE }, extra = {} }) {
  const meta = { exchangeTimezoneName: timezone, gmtoffset, regularMarketPrice: price, chartPreviousClose: prev, previousClose: prev,
    regularMarketTime: bars.at(-1)?.[0], currentTradingPeriod: period ? { regular: { ...period, timezone: "X", gmtoffset } } : undefined, ...extra };
  return { chart: { result: [{ meta, timestamp: bars.map((b) => b[0]), indicators: { quote: [{ close: bars.map((b) => b[1]), volume: bars.map(() => 0) }] } }], error: null } };
}

// 日經一整天的 5 分鐘線：11:30～12:25（日本時間）午休沒有價
function tokyoBars(open = TOKYO_OPEN) {
  const bars = [];
  for (let i = 0; i <= 78; i += 1) bars.push([open + i * 300, i >= 30 && i < 42 ? null : 50000 + i * 10]);
  return bars;
}
const ok = (body) => async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const status = (code, text = "{}") => async () => new Response(text, { status: code });

beforeEach(async () => {
  resetIntradayCache();
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_INDEX_INTRADAY: "all" };
  nowMs = T0;
  calls = [];
  upstream = ok(yahoo({ bars: tokyoBars(), price: 50780 }));
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => nowMs, intradayCache: null,
    intradayFetch: async (url, init) => { calls.push([String(url), init]); return upstream(url, init); } };
  for (const [email, state] of [[FRIEND, "approved"], ["pending@example.com", "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
      .bind(email, "F", state, "2026-11-09T00:00:00Z").run();
  }
});

async function req(path, { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { method, headers }), env, next, deps);
}
const body = async (path, options) => (await req(path, options)).json();

test("七個：六個和大盤頁的國外指數同一組 key 與名稱，再加只有分時的那斯達克期貨；來源網址的 ^ 與 = 有編碼", () => {
  assert.deepEqual(INTRADAY_INDICES.slice(0, 6).map((item) => [item.key, item.name]), MARKET_INDICES.map((item) => [item.key, item.name]));
  assert.deepEqual(INTRADAY_INDICES.map((item) => [item.key, item.symbol]), [["dji", "^DJI"], ["ixic", "^IXIC"], ["gspc", "^GSPC"], ["sox", "^SOX"],
    ["n225", "^N225"], ["ks11", "^KS11"], ["nq_fut", "NQ=F"], ["usdtwd", "TWD=X"]]);   // 美元／台幣另見 index-usdtwd.test.js
  assert.equal(sourceUrl("^N225"), "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225?interval=5m&range=1d");
  assert.equal(sourceUrl("NQ=F"), "https://query1.finance.yahoo.com/v8/finance/chart/NQ%3DF?interval=5m&range=1d");
});

test("成功：時間與價格、前一日收盤、時區、當地日期、開收盤時間、最新一根的時間；不快取在瀏覽器", async () => {
  const response = await req("/api/indices/n225/intraday");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  const data = await response.json();
  assert.deepEqual({ ...data, points: null }, {
    available: true, key: "n225", name: "日經 225", timezone: "Asia/Tokyo", date: "2026-11-24", interval: 300,
    previous_close: 50000, last: 50780, points: null, session: { start: TOKYO_OPEN, end: TOKYO_CLOSE }, as_of: TOKYO_CLOSE, stale: false,
  });
  assert.equal(data.points.length, 79 - 12, "午休那 12 根沒有價，不給");
  assert.deepEqual(data.points[0], [TOKYO_OPEN, 50000]);
  assert.deepEqual([data.points[29], data.points[30]], [[TOKYO_OPEN + 29 * 300, 50290], [TOKYO_OPEN + 42 * 300, 50420]], "午休前後的時間照原樣，中間留洞");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], sourceUrl("^N225"));
  assert.match(calls[0][1].headers["user-agent"], /^Mozilla\/5\.0 /);
});

test("同一個指數 60 秒內只問來源一次（不同人也一樣）；過了 60 秒才再問；不同指數各問各的", async () => {
  await body("/api/indices/n225/intraday");
  nowMs += 59_000;
  const again = await body("/api/indices/n225/intraday", { email: FRIEND });
  assert.equal(again.available, true);
  assert.equal(again.stale, false);
  assert.equal(calls.length, 1);
  await body("/api/indices/ks11/intraday");
  assert.equal(calls.length, 2);
  nowMs += 2_000;
  await body("/api/indices/n225/intraday");
  assert.equal(calls.length, 3);
});

test("同時進來的請求只問來源一次", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const good = upstream;
  upstream = async (...args) => { await gate; return good(...args); };
  const both = Promise.all([body("/api/indices/n225/intraday"), body("/api/indices/n225/intraday", { email: FRIEND })]);
  await new Promise((resolve) => setTimeout(resolve, 20));
  release();
  const [a, b] = await both;
  assert.equal(calls.length, 1);
  assert.deepEqual(a, b);
});

test("來源被擋（429）、回錯（500）、格式不對、回 error：給最近一次成功的那一份並標 stale", async () => {
  const good = await body("/api/indices/n225/intraday");
  const failures = [status(429), status(500), status(200, "<html>not json</html>"), ok({ chart: { result: null, error: { code: "Not Found" } } }),
    ok({ chart: { result: [{ meta: {}, timestamp: [1, 2] }], error: null } }), async () => { throw new Error("network"); }];
  for (const [i, failure] of failures.entries()) {
    nowMs += 61_000;
    upstream = failure;
    const stale = await body("/api/indices/n225/intraday");
    assert.deepEqual(stale, { ...good, stale: true }, `第 ${i} 種`);
  }
  assert.equal(calls.length, 1 + failures.length);
});

test("來源剛失敗過：30 秒內不再去撞，照樣回上一份", async () => {
  await body("/api/indices/n225/intraday");
  nowMs += 61_000;
  upstream = status(429);
  assert.equal((await body("/api/indices/n225/intraday")).stale, true);
  nowMs += 10_000;
  assert.equal((await body("/api/indices/n225/intraday")).stale, true);
  assert.equal(calls.length, 2);
  nowMs += 25_000;
  upstream = ok(yahoo({ bars: tokyoBars(), price: 50999 }));
  const fresh = await body("/api/indices/n225/intraday");
  assert.deepEqual([fresh.stale, fresh.last, calls.length], [false, 50999, 3]);
});

test("從來沒成功過、或上一份已經超過 24 小時：回「暫時拿不到」，不是錯誤", async () => {
  upstream = status(429);
  const response = await req("/api/indices/n225/intraday");
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { available: false, key: "n225", name: "日經 225" });
  nowMs += 40_000;
  upstream = ok(yahoo({ bars: tokyoBars(), price: 50780 }));
  assert.equal((await body("/api/indices/n225/intraday")).available, true);
  nowMs += 24 * 3_600_000 + 1_000;
  upstream = status(500);
  assert.deepEqual(await body("/api/indices/n225/intraday"), { available: false, key: "n225", name: "日經 225" });
});

test("平台快取（caches.default 那種 match／put）：別的執行個體存的那一份也拿來用", async () => {
  const store = new Map();
  deps.intradayCache = {
    match: async (url) => (store.has(url) ? new Response(store.get(url)) : undefined),
    put: async (url, response) => { store.set(url, await response.text()); },
  };
  const first = await body("/api/indices/n225/intraday");
  assert.equal(store.size, 1);
  resetIntradayCache();   // 換了一個執行個體：記憶體是空的
  nowMs += 30_000;
  assert.deepEqual(await body("/api/indices/n225/intraday"), first);
  assert.equal(calls.length, 1);
  resetIntradayCache();
  nowMs += 40_000;
  upstream = status(429);
  assert.deepEqual(await body("/api/indices/n225/intraday"), { ...first, stale: true });
  deps.intradayCache = { match: async () => { throw new Error("cache down"); }, put: async () => { throw new Error("cache down"); } };
  resetIntradayCache();
  upstream = ok(yahoo({ bars: tokyoBars(), price: 50780 }));
  assert.equal((await body("/api/indices/n225/intraday")).available, true, "平台快取壞了也照樣回");
});

test("開關：off 誰都沒有、owner 只有站主、all 核准的人都有；沒開的人不問來源也不扣額度", async () => {
  for (const [value, owner, friend] of [[undefined, false, false], ["off", false, false], ["on", false, false], ["owner", true, false], ["all", true, true]]) {
    resetIntradayCache();
    if (value === undefined) delete env.MARKET_ANA_INDEX_INTRADAY; else env.MARKET_ANA_INDEX_INTRADAY = value;
    calls = [];
    const mine = await body("/api/indices/n225/intraday");
    assert.equal(mine.available, owner, `站主 ${value}`);
    const theirs = await req("/api/indices/n225/intraday", { email: FRIEND });
    assert.equal(theirs.status, 200);
    const theirBody = await theirs.json();
    if (!friend) assert.deepEqual(theirBody, { available: false }, `朋友 ${value}`); else assert.equal(theirBody.available, true);
    assert.equal(calls.length, owner ? 1 : 0, `問來源的次數 ${value}`);
    const policy = releasePolicy(env);
    assert.equal(policy.features.index_intraday_v1, owner, `功能清單（站主）${value}`);
    assert.equal(policyForViewer(policy, false, env).features.index_intraday_v1, friend, `功能清單（朋友）${value}`);
    assert.equal((await body("/api/features")).features.index_intraday_v1, owner);
    assert.equal((await body("/api/features", { email: FRIEND })).features.index_intraday_v1, friend);
  }
  assert.equal(policyForViewer(releasePolicy({ MARKET_ANA_INDEX_INTRADAY: "all" }), false).features.index_intraday_v1, false, "env 沒給時對朋友是關");
  env.MARKET_ANA_INDEX_INTRADAY = "off";
  const hits = (await env.DB.prepare("SELECT COUNT(*) AS n FROM api_hits WHERE bucket = 'indexIntraday' AND email = ?").bind(FRIEND).first()).n;
  await req("/api/indices/n225/intraday", { email: FRIEND });
  assert.equal((await env.DB.prepare("SELECT COUNT(*) AS n FROM api_hits WHERE bucket = 'indexIntraday' AND email = ?").bind(FRIEND).first()).n, hits);
});

test("不是這七個的代號查無（台股加權也不在這裡）；只能讀；還沒核准、沒登入的人不給", async () => {
  assert.equal((await req("/api/indices/twii/intraday")).status, 404);
  assert.equal((await req("/api/indices/NVDA/intraday")).status, 404);
  assert.equal((await req("/api/indices/n225/intraday", { method: "POST" })).status, 405);
  assert.equal((await req("/api/indices/n225/intraday", { email: "pending@example.com" })).status, 403);
  assert.equal((await req("/api/indices/n225/intraday", { email: null })).status, 401);
  assert.equal(calls.length, 0);
  assert.equal((await req("/api/indices/nq_fut/intraday")).status, 200);
  assert.equal((await req("/api/indices/nq_fut")).status, 404, "期貨沒有日線");
});

test("查太頻繁時回 429，而且不問來源", async () => {
  deps.rate = { indexIntraday: 1 };
  assert.equal((await req("/api/indices/n225/intraday", { email: FRIEND })).status, 200);
  const limited = await req("/api/indices/ks11/intraday", { email: FRIEND });
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
  assert.equal(calls.length, 1);
  assert.equal((await req("/api/indices/ks11/intraday")).status, 200, "各人各算");
});

// ---------- 來源的那一份 → 回給畫面的那一份（純函式） ----------
const N225 = INTRADAY_INDICES.find((item) => item.key === "n225");
const NQ = INTRADAY_INDICES.find((item) => item.key === "nq_fut");

test("轉換：沒有價（null）、不是數字、0 的那幾根不給；時間照原樣、由舊到新；價取到小數兩位", () => {
  const out = transformChart(yahoo({ bars: [[TOKYO_OPEN + 600, 50010.456], [TOKYO_OPEN, 50000], [TOKYO_OPEN + 300, null], [TOKYO_OPEN + 900, 0],
    [TOKYO_OPEN + 1200, "x"], [null, 5]], price: 50010.456 }), N225);
  assert.deepEqual(out.points, [[TOKYO_OPEN, 50000], [TOKYO_OPEN + 600, 50010.46]]);
  assert.equal(out.as_of, TOKYO_OPEN + 600);
  assert.equal(out.last, 50010.46);
});

test("轉換：前一日收盤先看 chartPreviousClose、再看 previousClose，都沒有就用第一根；最新價沒給就用最後一根", () => {
  const bars = [[TOKYO_OPEN, 49900], [TOKYO_OPEN + 300, 49950]];
  assert.equal(transformChart(yahoo({ bars, extra: { chartPreviousClose: 49800, previousClose: 49700 } }), N225).previous_close, 49800);
  assert.equal(transformChart(yahoo({ bars, extra: { chartPreviousClose: null, previousClose: 49700 } }), N225).previous_close, 49700);
  const bare = transformChart(yahoo({ bars, extra: { chartPreviousClose: undefined, previousClose: 0 } }), N225);
  assert.deepEqual([bare.previous_close, bare.last], [49900, 49950]);
});

test("轉換：休市時來源給的是最近一個交易日——日期寫那一天（當地），開收盤時間挪到那一天", () => {
  // 資料是 11-20（五）的，來源的「目前時段」是 11-24（二）
  const friday = TOKYO_OPEN - 4 * 86400;
  const out = transformChart(yahoo({ bars: tokyoBars(friday), price: 50780 }), N225);
  assert.equal(out.date, "2026-11-20");
  assert.deepEqual(out.session, { start: friday, end: friday + 6.5 * 3600 });
  // 美股：當地日期照時差算（台北已經是隔天）
  const nyOpen = Date.parse("2026-11-23T14:30:00Z") / 1000;
  const us = transformChart(yahoo({ bars: [[nyOpen, 47000], [nyOpen + 6.5 * 3600, 47100]], timezone: "America/New_York", gmtoffset: -18000,
    period: { start: nyOpen, end: nyOpen + 6.5 * 3600 }, prev: 46900 }), INTRADAY_INDICES[0]);
  assert.deepEqual([us.date, us.timezone, us.key, us.name], ["2026-11-23", "America/New_York", "dji", "道瓊工業"]);
  // 來源沒給時差：用時區名稱算
  const named = transformChart(yahoo({ bars: [[nyOpen + 6 * 3600, 47000]], timezone: "America/New_York", gmtoffset: null, period: null }), INTRADAY_INDICES[0]);
  assert.equal(named.date, "2026-11-23");
});

test("轉換：開收盤時間包不住資料（或沒給）就不給，畫面照第一根到最後一根畫；tradingPeriods 有給就優先", () => {
  const bars = [[TOKYO_OPEN, 50000], [TOKYO_OPEN + 3600, 50100]];
  assert.equal(transformChart(yahoo({ bars, period: null }), N225).session, null);
  assert.equal(transformChart(yahoo({ bars, period: { start: TOKYO_OPEN + 1800, end: TOKYO_OPEN + 2400 } }), N225).session, null);
  const listed = transformChart(yahoo({ bars, period: { start: 1, end: 0 }, extra: { tradingPeriods: [[{ start: TOKYO_OPEN, end: TOKYO_CLOSE }]] } }), N225);
  assert.deepEqual(listed.session, { start: TOKYO_OPEN, end: TOKYO_CLOSE });
});

test("轉換：期貨當成連續的一段——還在走就把時間軸留到 24 小時，已經停了就到最後一根", () => {
  const midnight = Date.parse("2026-11-24T05:00:00Z") / 1000;   // 紐約 00:00
  const bars = [[midnight, 25000], [midnight + 300, 25010], [midnight + 7200, 25100]];
  const feed = yahoo({ bars, timezone: "America/New_York", gmtoffset: -18000, prev: 24900, price: 25100, period: { start: midnight, end: midnight + 86340 } });
  const running = transformChart(feed, NQ, midnight + 7200 + 120);
  assert.deepEqual(running.session, { start: midnight, end: midnight + 86400 - 300 });
  assert.deepEqual([running.date, running.key, running.name, running.previous_close], ["2026-11-24", "nq_fut", "那斯達克期貨", 24900]);
  const stopped = transformChart(feed, NQ, midnight + 7200 + 3600);
  assert.deepEqual(stopped.session, { start: midnight, end: midnight + 7200 });
});

test("轉換：格式不對一律當成來源失敗（null）", () => {
  const good = yahoo({ bars: [[TOKYO_OPEN, 50000]] });
  for (const bad of [null, "text", {}, { chart: null }, { chart: { result: [], error: null } }, { chart: { ...good.chart, error: { code: "x" } } },
    { chart: { result: [{ ...good.chart.result[0], meta: null }], error: null } },
    { chart: { result: [{ ...good.chart.result[0], timestamp: "nope" }], error: null } },
    { chart: { result: [{ ...good.chart.result[0], indicators: {} }], error: null } },
    yahoo({ bars: [[TOKYO_OPEN, null], [TOKYO_OPEN + 300, null]] })]) {
    assert.equal(transformChart(bad, N225), null, JSON.stringify(bad)?.slice(0, 80));
  }
});
