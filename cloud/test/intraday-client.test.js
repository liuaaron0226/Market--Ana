import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadIntraday() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/intraday.js", ROOT), "utf8"), {
    window, URL, URLSearchParams, Date, Math, Number, Object, Array, Map, Set,
    setTimeout() { return 1; }, clearTimeout() {}, AbortController, fetch: async () => ({}), console,
  });
  return window.Intraday;
}

const pack = JSON.parse(readFileSync(new URL("cloud/.stock-pack.json", ROOT), "utf8"));
const base = typeof pack.results["2330"] === "string" ? JSON.parse(pack.results["2330"]) : pack.results["2330"];
const live = {
  ok: true, environment: "local", market_open: true, live: true,
  quote: {
    price: 2265, previous_close: 2235, change: 30, change_percent: 1.34,
    open: 2265, high: 2280, low: 2255, volume_shares: 14037000,
    data_time: "2026-11-10T10:22:00+08:00", data_date: "2026-11-10",
  },
  candles: {
    "1": [{ t: "2026-11-10T10:22:00+08:00", label: "10:22", open: 2265, high: 2265, low: 2265, close: 2265, volume: 0 }],
    "5": [], "15": [],
  },
};

test("盤中現價以 Python 同一套規則重算指標、價位、主要訊號與操作指引輸入", () => {
  const projected = loadIntraday().projectAnalysis(base, live);
  assert.equal(projected.quote.close, 2265);
  assert.equal(projected.quote.chg_pct, 1.34);
  assert.equal(projected.bars.at(-1).date, "2026-11-10");
  assert.deepEqual(
    Object.fromEntries(["ma5", "ma10", "ma20", "ma60", "ma120", "ma240", "rsi14", "atr14", "atr_pct", "relative_volume", "high20_close", "low20_close", "prior_high20", "low20_intraday", "k9", "d9", "macd_dif", "macd_dea", "macd_hist"].map(key => [key, projected.indicators[key]])),
    { ma5: 2227, ma10: 2297, ma20: 2369, ma60: 2417.5, ma120: 2389.12, ma240: 2082.65,
      rsi14: 35.2, atr14: 66.07, atr_pct: 2.92, relative_volume: 0.36, high20_close: 2540,
      low20_close: 2135, prior_high20: 2575, low20_intraday: 2020, k9: 39.26, d9: 26.6,
      macd_dif: -62.027, macd_dea: -43.016, macd_hist: -19.01 },
  );
  assert.equal(projected.levels.resistance[0].tick, 2295);
  assert.equal(projected.levels.resistance[1].tick, 2405);
  assert.equal(projected.levels.support[0].tick, 2240);
  assert.equal(projected.levels.support[1].tick, 2195);
  assert.equal(projected.conditions.confirmed.ma_trend.state, "triggered");
  assert.equal(projected.conditions.confirmed.kd_bullish.state, "triggered");
  assert.equal(projected.conditions.confirmed.volume_confirmed.state, "confirming");
  assert.equal(projected.conditions.confirmed.revenue_decline.state, base.conditions.confirmed.revenue_decline.state);
  assert.equal(projected.reference.low20_intraday.value, 2020);
});

test("MIS 暫缺 OHLC 或成交量時保留盤後分析，不把 null 當成 0", () => {
  const missing = structuredClone(live);
  missing.quote.low = null;
  assert.strictEqual(loadIntraday().projectAnalysis(base, missing), base);
});

test("同資料日只替換當日暫定 K，不重複新增", () => {
  const sameDay = structuredClone(live);
  sameDay.quote.data_date = base.as_of;
  sameDay.quote.data_time = `${base.as_of}T10:22:00+08:00`;
  const projected = loadIntraday().projectAnalysis(base, sameDay);
  assert.equal(projected.bars.length, base.bars.length);
  assert.equal(projected.bars.at(-1).date, base.as_of);
  assert.equal(projected.bars.at(-1).close, 2265);
});

test("前端輪詢：主報價 5 秒、429 依伺服器退避、休市停止", () => {
  const { nextPollDelay } = loadIntraday();
  assert.equal(nextPollDelay({ ok: true, market_open: true, delay_seconds: { quote: 5 } }), 5000);
  assert.equal(nextPollDelay({ ok: false, market_open: true, retry_after_seconds: 60 }), 60000);
  assert.equal(nextPollDelay({ ok: false, error: "rate-limited", retry_after_seconds: 60 }), 60000);
  assert.equal(nextPollDelay({ ok: true, market_open: false }), null);
  assert.equal(nextPollDelay({ ok: true, market_open: false, market_phase: "closed" }), null);
});

test("前端輪詢：盤中時段來源還停在前一日（unknown）不得永久停止", () => {
  const { nextPollDelay } = loadIntraday();
  assert.equal(nextPollDelay({ ok: true, market_open: false, market_phase: "unknown" }), 15000);
  assert.equal(nextPollDelay({ ok: false, market_open: false, market_phase: "unknown", retry_after_seconds: 60 }), 60000);
});

test("個股頁接上 1／5／15 分、LIVE 新鮮度與雲端失敗明示", () => {
  const html = readFileSync(new URL("web/stock.html", ROOT), "utf8");
  for (const [value, label] of [["1m", "1分"], ["5m", "5分"], ["15m", "15分"]]) {
    assert.match(html, new RegExp(`<option value="${value}">${label}K</option>`));
  }
  assert.match(html, /id="liveBadge"/);
  assert.match(html, /id="liveStatus"/);
  assert.ok(html.indexOf("intraday.js") < html.indexOf("stock.js"));
  const stock = readFileSync(new URL("web/stock.js", ROOT), "utf8");
  assert.match(stock, /Intraday\.createPoller/);
  assert.match(stock, /Intraday\.projectAnalysis/);
  assert.match(stock, /雲端無法取得即時資料/);
  assert.match(stock, /has\('as_of'\)/, "歷史查詢不得啟動盤中輪詢");
  assert.doesNotMatch(stock, /3 \* 86_400_000/, "不可用固定 72 小時誤擋週末後開盤");
});
