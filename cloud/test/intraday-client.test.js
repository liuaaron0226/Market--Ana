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
  // 價位（壓力／支撐）盤中固定用昨天收盤那組，見下一個測試；這裡只確認上下位置照現價分。
  assert.ok(projected.levels.resistance.every((item) => item.level > 2265));
  assert.ok(projected.levels.support.every((item) => item.level < 2265));
  assert.equal(projected.conditions.confirmed.ma_trend.state, "triggered");
  assert.equal(projected.conditions.confirmed.kd_bullish.state, "triggered");
  assert.equal(projected.conditions.confirmed.volume_confirmed.state, "confirming");
  assert.equal(projected.conditions.confirmed.revenue_decline.state, base.conditions.confirmed.revenue_decline.state);
  assert.equal(projected.reference.low20_intraday.value, 2020);
});

// 本人 11-19「為什麼壓力支撐會一直變動」→ 選「固定住」：盤中價位數字一律用前一交易日收盤算的那組，
// 整天不變，只依現價決定在上（壓力）或下（支撐）。
const levelSet = (levels) => [...levels.resistance_all, ...levels.support_all].map((group) => group.level).sort((a, b) => a - b);
const atPrice = (price) => {
  const moved = structuredClone(live);
  moved.quote = { ...moved.quote, price, high: Math.max(price, 2280), low: Math.min(price, 2255) };
  return loadIntraday().projectAnalysis(base, moved).levels;
};

test("盤中價位就是昨天收盤那組（盤後分析的價位），數字不重算", () => {
  const away = (level) => Math.abs(level - 2265) > 2265 * 0.001;
  assert.deepEqual(levelSet(atPrice(2265)), levelSet(base.levels).filter(away));
});

test("盤中現價移動、甚至穿過某個價位，整組數字都不變，只換上下", () => {
  const pool = levelSet(base.levels);
  const crossed = pool.find((level) => level > 2265 * 1.002);
  assert.ok(crossed, "現價上方要有價位才能測穿越");
  const before = atPrice(2265), after = atPrice(Math.ceil(crossed * 1.002));
  const all = (levels) => levelSet(levels).filter((level) => Math.abs(level - 2265) > 2265 * 0.001 && Math.abs(level - Math.ceil(crossed * 1.002)) > crossed * 0.002);
  assert.deepEqual(all(after), all(before));
  assert.ok(before.resistance_all.some((group) => group.level === crossed));
  assert.ok(after.support_all.some((group) => group.level === crossed));
  assert.ok(after.resistance.every((item) => item.level > crossed) && after.support.every((item) => item.level < Math.ceil(crossed * 1.002)));
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

// ---- 收盤後到每日更新發布前（本人 11-17「我剛剛看股票卻是昨天的」） ----
const plainCopy = value => JSON.parse(JSON.stringify(value));
const dayAfter = (day, count = 1) => new Date(Date.parse(`${day}T00:00:00Z`) + count * 86_400_000).toISOString().slice(0, 10);
const TODAY = dayAfter(base.as_of);   // 日資料停在前一天，今天已收盤
const closedLive = {
  ok: true, environment: "cloud", market_open: false, market_phase: "closed", live: false,
  quote: {
    price: 2165, previous_close: 2235, change: -70, change_percent: -3.13,
    open: 2200, high: 2210, low: 2160, volume_shares: 30123000,
    data_time: `${TODAY}T13:30:00+08:00`, data_date: TODAY,
  },
  candles: { "1": [], "5": [], "15": [], "60": [] },
};
const taipeiAt = (day, time) => Date.parse(`${day}T${time}+08:00`);
const shortDay = day => `${day.slice(5, 7)}/${day.slice(8, 10)}`;

test("收盤後、日資料仍是前一天：報價卡顯示今天的收盤價、漲跌與資料時間，寫明是今日收盤", () => {
  const I = loadIntraday();
  const shown = I.projectAnalysis(base, closedLive);
  assert.equal(shown.quote.close, 2165);
  assert.equal(shown.quote.chg_pct, -3.13);
  assert.equal(shown.as_of, TODAY);
  assert.equal(shown.bars.at(-1).date, TODAY);
  assert.equal(shown.bars.length, base.bars.length + 1);
  assert.deepEqual(plainCopy(I.quoteCaption(shown, taipeiAt(TODAY, "14:56:00"))),
    { mode: "今日收盤", stamp: `${TODAY} 13:30`, ladderLabel: "收盤", ladderMeta: "盤後暫用即時報價" });
  assert.equal(I.liveStatusText(closedLive, shown, taipeiAt(TODAY, "14:56:00")), "已收盤，顯示今日收盤（日資料稍後更新）");
});

test("每日更新發布後日資料已到今天：用日資料，不再拿即時報價蓋過", () => {
  const I = loadIntraday();
  const published = structuredClone(closedLive);
  published.quote.data_date = base.as_of;
  published.quote.data_time = `${base.as_of}T13:30:00+08:00`;
  const shown = I.projectAnalysis(base, published);
  assert.strictEqual(shown, base);
  const now = taipeiAt(base.as_of, "17:00:00");
  assert.deepEqual(plainCopy(I.quoteCaption(shown, now)),
    { mode: "資料日", stamp: base.as_of, ladderLabel: "收盤", ladderMeta: "資料日收盤" });
  assert.equal(I.liveStatusText(published, shown, now), "已收盤，顯示今日收盤");
});

test("週末、盤前沒有今天的報價：仍顯示上一個交易日，並寫明是哪一天", () => {
  const I = loadIntraday();
  const none = { ok: true, market_open: false, market_phase: "closed", live: false, quote: null };
  assert.strictEqual(I.projectAnalysis(base, none), base);
  // 比日資料舊的快取報價也不能蓋過日資料
  const older = structuredClone(closedLive);
  older.quote.data_date = dayAfter(base.as_of, -1);
  assert.strictEqual(I.projectAnalysis(base, older), base);
  const saturday = (() => { let day = base.as_of; while (new Date(`${day}T12:00:00Z`).getUTCDay() !== 6) day = dayAfter(day); return day; })();
  assert.equal(I.liveStatusText(none, base, taipeiAt(saturday, "10:00:00")), `休市，顯示 ${shortDay(base.as_of)} 收盤`);
  const nextWeekday = (() => { let day = dayAfter(base.as_of); while ([0, 6].includes(new Date(`${day}T12:00:00Z`).getUTCDay())) day = dayAfter(day); return day; })();
  assert.equal(I.liveStatusText(none, base, taipeiAt(nextWeekday, "08:30:00")), `尚未開盤，顯示 ${shortDay(base.as_of)} 收盤`);
  assert.equal(I.liveStatusText({ ...none, market_phase: "unknown" }, base, taipeiAt(nextWeekday, "09:05:00")),
    `尚未取得今日報價，顯示 ${shortDay(base.as_of)} 收盤`);
});

// 雙軸審查：13:30 剛過還沒拿到收盤價（或上游失敗）時，手上的是收盤前最後一筆，不能寫成「今日收盤」。
test("收盤價還沒確認（close_pending）：寫「今日最後成交」並在 15 秒後再問", () => {
  const I = loadIntraday();
  const pending = structuredClone(closedLive);
  pending.close_pending = true;
  pending.quote.data_time = `${TODAY}T13:24:58+08:00`;
  const shown = I.projectAnalysis(base, pending);
  assert.equal(shown.quote.close, 2165);
  const now = taipeiAt(TODAY, "13:30:05");
  assert.equal(I.quoteCaption(shown, now).mode, "今日最後成交");
  assert.equal(I.quoteCaption(shown, now).ladderLabel, "最後成交");
  assert.equal(I.liveStatusText(pending, shown, now), "已收盤，顯示今日最後成交（收盤價更新中）");
  assert.equal(I.nextPollDelay(pending), 15000);
  assert.equal(I.nextPollDelay({ ...pending, retry_after_seconds: 40 }), 40000);
  assert.equal(I.nextPollDelay(closedLive), null, "拿到收盤價就停");
});

test("平日國定假日下午：伺服器的最後報價停在前一個交易日，寫「今日休市」", () => {
  const I = loadIntraday();
  const holiday = structuredClone(closedLive);
  holiday.quote.data_date = base.as_of;
  holiday.quote.data_time = `${base.as_of}T13:30:00+08:00`;
  let day = dayAfter(base.as_of);
  while ([0, 6].includes(new Date(`${day}T12:00:00Z`).getUTCDay())) day = dayAfter(day);
  assert.equal(I.liveStatusText(holiday, base, taipeiAt(day, "14:00:00")), `今日休市，顯示 ${shortDay(base.as_of)} 收盤`);
});

test("盤中與雲端失敗的狀態字不變", () => {
  const I = loadIntraday();
  const shown = I.projectAnalysis(base, live);
  assert.equal(I.quoteCaption(shown, taipeiAt("2026-11-10", "10:22:05")).mode, "盤中");
  assert.equal(I.liveStatusText(live, shown, taipeiAt("2026-11-10", "10:22:05")), "10:22:00 更新");
  assert.equal(I.liveStatusText({ ok: true, market_open: true, live: false, quote: live.quote }, shown, 0), "即時資料逾 30 秒，顯示最近一筆");
  assert.equal(I.liveStatusText({ ok: false, environment: "cloud", error: "cloud-live-unavailable" }, base, 0), "雲端無法取得即時資料");
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

test("個股頁週期是分時、60 分、日、週、月（本人 11-18：秒 K 與 1／5／15 分 K 拿掉）、LIVE 新鮮度與雲端失敗明示", () => {
  const html = readFileSync(new URL("web/stock.html", ROOT), "utf8");
  const options = [...html.matchAll(/<option value="([^"]+)"[^>]*>([^<]+)<\/option>/g)].map(m => [m[1], m[2]]);
  assert.deepEqual(options, [["ts", "分時"], ["60m", "60分"], ["daily", "日"], ["weekly", "週"], ["monthly", "月"]]);
  assert.match(html, /id="liveBadge"/);
  assert.match(html, /id="liveStatus"/);
  assert.ok(html.indexOf("intraday.js") < html.indexOf("stock.js"));
  const stock = readFileSync(new URL("web/stock.js", ROOT), "utf8");
  assert.match(stock, /Intraday\.createPoller/);
  assert.match(stock, /Intraday\.projectAnalysis/);
  assert.match(stock, /Intraday.liveStatusText/, "狀態字（含雲端失敗明示）由 intraday.js 的 liveStatusText 決定");
  assert.match(stock, /has\('as_of'\)/, "歷史查詢不得啟動盤中輪詢");
  assert.doesNotMatch(stock, /3 \* 86_400_000/, "不可用固定 72 小時誤擋週末後開盤");
});

// 本人 11-18：秒 K 拿掉（換成分時），逐筆彙總的程式一起拿掉
test("秒 K 的逐筆彙總已經拿掉", () => {
  assert.equal(loadIntraday().secondCandles, undefined);
});
