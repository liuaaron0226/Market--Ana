// 美元／台幣（本人 2026-11-24：「加入台幣對美金的即時匯率在大盤頁」）：大盤頁切換清單的一項，key 是 usdtwd、來源代號 TWD=X。
// 分時走海外大盤分時同一個入口（/api/indices/usdtwd/intraday）；日線資料表沒有，/api/indices/usdtwd 整份向來源要最近一年。
// 只給看得到海外分時的人（MARKET_ANA_INDEX_INTRADAY）。價取到小數三位；數字變大是台幣貶值。來源一律換成假的，測試不連網路。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { handle } from "../lib/gate.js";
import { INTRADAY_INDICES, resetIntradayCache, sourceUrl, transformChart, transformDaily, upstreamDailyOf, yearDailySourceUrl } from "../lib/index-intraday.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const FRIEND = "friend@example.com";
const T0 = Date.parse("2026-11-24T06:00:00Z");   // 台北 14:00；倫敦 11-24 06:00
const MIDNIGHT = Date.parse("2026-11-24T00:00:00Z") / 1000;   // 來源的匯率一天從倫敦 00:00 起
const TODAY = { start: MIDNIGHT, end: MIDNIGHT + 86340 };
const FX = INTRADAY_INDICES.find((item) => item.key === "usdtwd");
const dayOf = (date) => Date.parse(`${date}T00:00:00Z`) / 1000;
let issuer, env, deps, nowMs, calls, upstream;
const next = async () => new Response("static-ok", { status: 200 });

const META = { exchangeTimezoneName: "Europe/London", gmtoffset: 0, currentTradingPeriod: { regular: { ...TODAY, gmtoffset: 0 } } };
// 來源會回的 5 分鐘線。bars：[秒, 價或 null]…
function yahooIntraday(bars, { prev = 32.3801, price } = {}) {
  return { chart: { result: [{ meta: { ...META, chartPreviousClose: prev, previousClose: prev, regularMarketPrice: price },
    timestamp: bars.map((b) => b[0]), indicators: { quote: [{ close: bars.map((b) => b[1]), volume: bars.map(() => 0) }] } }], error: null } };
}
// 來源會回的日線。bars：[秒, 開, 高, 低, 收]…（匯率的量一律是 0）
function yahooDaily(bars) {
  const column = (i) => bars.map((bar) => bar[i]);
  return { chart: { result: [{ meta: META, timestamp: column(0),
    indicators: { quote: [{ open: column(1), high: column(2), low: column(3), close: column(4), volume: bars.map(() => 0) }] } }], error: null } };
}
const INTRADAY_BARS = [[MIDNIGHT, 32.4012], [MIDNIGHT + 300, 32.41549], [MIDNIGHT + 600, null], [T0 / 1000 - 60, 32.4604]];
const DAILY_BARS = [
  [dayOf("2026-11-19"), 32.31, 32.3649, 32.2951, 32.35],
  [dayOf("2026-11-20"), 32.35, 32.4, 32.33, 32.38012],
  [dayOf("2026-11-23"), null, null, null, null],   // 來源偶爾整天沒有價：不給
  [T0 / 1000 - 60, 32.4012, 32.47, 32.39, 32.4604],
];
const DAILY_CANDLES = [
  { date: "2026-11-19", open: 32.31, high: 32.365, low: 32.295, close: 32.35, volume: null },
  { date: "2026-11-20", open: 32.35, high: 32.4, low: 32.33, close: 32.38, volume: null },
  { date: "2026-11-24", open: 32.401, high: 32.47, low: 32.39, close: 32.46, volume: null, provisional: true },
];
const HEAD = { ok: true, key: "usdtwd", symbol: "TWD=X", name: "美元／台幣", zone: null };
const ok = (body) => async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const status = (code) => async () => new Response("{}", { status: code });
// 分時與日線是同一個代號、不同的 interval：照網址回
const both = async (url) => (String(url).includes("interval=5m") ? ok(yahooIntraday(INTRADAY_BARS, { price: 32.4604 })) : ok(yahooDaily(DAILY_BARS)))();

beforeEach(async () => {
  resetIntradayCache();
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_INDEX_INTRADAY: "all" };
  nowMs = T0;
  calls = [];
  upstream = both;
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => nowMs, intradayCache: null,
    intradayFetch: async (url, init) => { calls.push([String(url), init]); return upstream(url, init); } };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(FRIEND, "F", "approved", "2026-11-09T00:00:00Z").run();
});

async function req(path, { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { method, headers }), env, next, deps);
}
const body = async (path, options) => (await req(path, options)).json();

test("清單裡的那一項：代號 TWD=X（= 有編碼）、連續交易、小數三位、日線向來源要；其他指數都不是", () => {
  assert.deepEqual(FX, { key: "usdtwd", symbol: "TWD=X", name: "美元／台幣", continuous: true, digits: 3, upstreamDaily: true });
  assert.equal(sourceUrl(FX.symbol), "https://query1.finance.yahoo.com/v8/finance/chart/TWD%3DX?interval=5m&range=1d");
  assert.equal(yearDailySourceUrl(FX.symbol), "https://query1.finance.yahoo.com/v8/finance/chart/TWD%3DX?interval=1d&range=1y");
  assert.equal(upstreamDailyOf("usdtwd"), FX);
  assert.deepEqual(["dji", "nq_fut", "twii", "nope"].map(upstreamDailyOf), [null, null, null, null]);
});

test("分時：同一個入口；價取到小數三位、基準是前一日收盤、沒有價的那一根不給、時間軸照連續交易（還在走就留到 24 小時）", async () => {
  const response = await req("/api/indices/usdtwd/intraday");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await response.json(), {
    available: true, key: "usdtwd", name: "美元／台幣", timezone: "Europe/London", date: "2026-11-24", interval: 300,
    previous_close: 32.38, last: 32.46, points: [[MIDNIGHT, 32.401], [MIDNIGHT + 300, 32.415], [T0 / 1000 - 60, 32.46]],
    session: { start: MIDNIGHT, end: MIDNIGHT + 86400 - 300 }, as_of: T0 / 1000 - 60, stale: false,
  });
  assert.deepEqual(calls.map((call) => call[0]), [sourceUrl("TWD=X")]);
  // 週末：來源給的是上一個交易日、已經不走了，時間軸到最後一根為止
  const closed = transformChart(yahooIntraday(INTRADAY_BARS.slice(0, 2)), FX, MIDNIGHT + 2 * 86400);
  assert.deepEqual(closed.session, { start: MIDNIGHT, end: MIDNIGHT + 300 });
  // 來源沒給前一日收盤：退回第一根（照樣三位）
  const bare = yahooIntraday(INTRADAY_BARS);
  delete bare.chart.result[0].meta.chartPreviousClose; delete bare.chart.result[0].meta.previousClose;
  assert.equal(transformChart(bare, FX, T0 / 1000).previous_close, 32.401);
});

test("日線：整份來自來源最近一年（資料表沒有它）；小數三位、量是 null、還沒走完的那一天標 provisional；形狀和其他指數一樣", async () => {
  const response = await req("/api/indices/usdtwd");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await response.json(), { ...HEAD, candles: DAILY_CANDLES, fetch: null });
  assert.deepEqual(calls.map((call) => call[0]), [yearDailySourceUrl("TWD=X")]);
  assert.match(calls[0][1].headers["user-agent"], /^Mozilla\/5\.0 /);
  assert.equal((await req("/api/indices/usdtwd", { method: "POST" })).status, 405);
  // 其他指數的日線照舊取到兩位
  assert.equal(transformDaily(yahooDaily(DAILY_BARS), T0 / 1000).bars[0].high, 32.36);
  assert.equal(transformDaily(yahooDaily(DAILY_BARS), T0 / 1000, 3).bars[0].high, 32.365);
});

test("日線快取 5 分鐘（不同人共用）；和分時各存各的", async () => {
  await body("/api/indices/usdtwd");
  nowMs += 299_000;
  assert.equal((await body("/api/indices/usdtwd", { email: FRIEND })).candles.length, 3);
  assert.equal(calls.length, 1);
  assert.equal((await body("/api/indices/usdtwd/intraday")).points.length, 3);
  assert.equal(calls.length, 2);
  nowMs += 2_000;
  await body("/api/indices/usdtwd");
  assert.deepEqual(calls.map((call) => call[0].includes("interval=1d")), [true, false, true]);
});

test("日線：來源回錯、被擋、格式不對、連不上，而且沒有上一份：candles 是空的、available:false（畫面只留分時），不是錯誤", async () => {
  const failures = [status(429), status(500), async () => new Response("<html>", { status: 200 }), ok({ chart: { result: null, error: { code: "x" } } }),
    async () => { throw new Error("network"); }];
  for (const [i, failure] of failures.entries()) {
    resetIntradayCache();
    upstream = failure;
    const response = await req("/api/indices/usdtwd");
    assert.equal(response.status, 200, `第 ${i} 種`);
    assert.deepEqual(await response.json(), { ...HEAD, available: false, candles: [], fetch: null }, `第 ${i} 種`);
  }
  // 剛失敗過 30 秒內不再撞來源
  nowMs += 10_000;
  await body("/api/indices/usdtwd");
  assert.equal(calls.length, failures.length);
});

test("日線：來源後來失敗，24 小時內用上一次成功的那一份並註明沒更新到；超過就當成沒有", async () => {
  const first = await body("/api/indices/usdtwd");
  nowMs += 3_600_000;
  upstream = status(429);
  const stale = await body("/api/indices/usdtwd");
  assert.deepEqual(stale.candles, first.candles);
  assert.deepEqual(stale.fetch, { ok: false, succeeded_at: null, problem: "來源暫時拿不到" });
  nowMs += 24 * 3_600_000;
  assert.deepEqual(await body("/api/indices/usdtwd"), { ...HEAD, available: false, candles: [], fetch: null });
});

test("開關沒開、或只開站主而來的是朋友：日線和不認得的 key 一樣是 404、分時回「沒有」，都不問來源", async () => {
  for (const [value, owner, friend] of [[undefined, false, false], ["off", false, false], ["owner", true, false], ["all", true, true]]) {
    resetIntradayCache();
    if (value === undefined) delete env.MARKET_ANA_INDEX_INTRADAY; else env.MARKET_ANA_INDEX_INTRADAY = value;
    calls = [];
    for (const [email, allowed] of [[FRIEND, friend], [OWNER, owner]]) {
      const before = calls.length;
      const daily = await req("/api/indices/usdtwd", { email });
      const intraday = await req("/api/indices/usdtwd/intraday", { email });
      assert.equal(daily.status, allowed ? 200 : 404, `日線 ${value} ${email}`);
      assert.equal(intraday.status, 200);
      if (allowed) {
        assert.equal((await daily.json()).candles.length, 3);
        assert.equal((await intraday.json()).available, true);
      } else {
        assert.equal(daily.status, (await req("/api/indices/nope", { email })).status, "和不認得的 key 一樣");
        assert.deepEqual(await intraday.json(), { available: false });
        assert.equal(calls.length, before, `不問來源 ${value} ${email}`);
      }
    }
  }
});

// ---------- 畫面上的寫法（web/market-index.js 的純函式） ----------
function loadMarketIndex() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("../../web/market-index.js", import.meta.url), "utf8"), { window, console });
  return window.MarketIndex;
}

test("選單：美元／台幣排在最後，小數三位；和伺服器那一項同一個 key 與名稱", () => {
  const item = loadMarketIndex().LIST.at(-1);
  assert.deepEqual([item.key, item.short, item.name, item.fx, item.digits, item.intradayOnly, item.zone], ["usdtwd", "美元／台幣", "美元／台幣", true, 3, undefined, undefined]);
  assert.deepEqual([item.key, item.name, item.digits], [FX.key, FX.name, FX.digits]);
});

test("價：固定小數三位、不加千分位；不是數字寫「—」", () => {
  const { fxPrice } = loadMarketIndex();
  assert.deepEqual([fxPrice(32.415), fxPrice(32.4), fxPrice(32), fxPrice(32.4156), fxPrice(1234.5, 2)], ["32.415", "32.400", "32.000", "32.416", "1234.50"]);
  assert.deepEqual([fxPrice(null), fxPrice(undefined), fxPrice(NaN), fxPrice("32.4")], ["—", "—", "—", "—"]);
});

test("漲跌：數字變大是台幣貶值、變小是台幣升值；金額寫元、小數三位，後面是幅度；四捨五入後是 0 寫持平", () => {
  const { fxChange } = loadMarketIndex();
  assert.equal(fxChange(0.08, 0.2471), "台幣貶值 0.080 元(0.25%)");
  assert.equal(fxChange(-0.05, -0.1544), "台幣升值 0.050 元(0.15%)");
  assert.equal(fxChange(0.0004, 0.0012), "台幣持平(0.00%)");
  assert.equal(fxChange(0, 0), "台幣持平(0.00%)");
  assert.equal(fxChange(0.1234, null), "台幣貶值 0.123 元");
  assert.equal(fxChange(-1.5, -4.6, 2), "台幣升值 1.50 元(4.60%)");
  assert.deepEqual([fxChange(null, 1), fxChange(NaN, 1), fxChange(undefined)], ["", "", ""]);
});

test("分時圖要多帶的：小數位、上方那一行寫「匯率」、漲跌那一段用上面的寫法", () => {
  const { fxModel, LIST } = loadMarketIndex();
  const extra = fxModel(LIST.at(-1));
  assert.deepEqual([extra.digits, extra.quoteLabel], [3, "匯率"]);
  assert.equal(extra.changeText(32.46 - 32.38, (32.46 - 32.38) / 32.38 * 100), "台幣貶值 0.080 元(0.25%)");
  assert.equal(extra.changeText(32.33 - 32.38, (32.33 - 32.38) / 32.38 * 100), "台幣升值 0.050 元(0.15%)");
});
