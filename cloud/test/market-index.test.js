// 大盤頁切換國外指數（本人 2026-11-19）：web/market-index.js 把 /api/indices 的日線換成 K 線圖要的格式（日／週／月），
// 判斷有沒有成交量，並照「短中長期走勢」同一套規則算國外指數的走勢（國外沒有法人，那一票不列）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { MARKET_INDICES } from "../lib/market-indices.js";

const WEB = new URL("../../web/", import.meta.url);

function load() {
  const window = {};
  const sandbox = { window, console, getComputedStyle: () => ({ getPropertyValue: () => "" }) };
  vm.createContext(sandbox);
  for (const file of ["kchart.js", "chart-studies.js", "trend-votes.js", "market-index.js"]) {
    vm.runInContext(readFileSync(new URL(file, WEB), "utf8"), sandbox, { filename: file });
  }
  return window;
}
const plain = (value) => JSON.parse(JSON.stringify(value));

// 平日連續的日線（收盤一路往上或往下），足夠算長期（240 根以上）
function rows(count, { from = "2025-09-01", start = 100, step = 1, volume = 1000 } = {}) {
  const out = [];
  const day = new Date(`${from}T00:00:00Z`);
  let close = start;
  while (out.length < count) {
    if (![0, 6].includes(day.getUTCDay())) {
      out.push({ date: day.toISOString().slice(0, 10), open: close, high: close + 2, low: close - 2, close: close + step, volume });
      close += step;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return out;
}

test("選單是台股加權在最前面，接著五個國外指數；名稱和伺服器那一份一樣", () => {
  const { MarketIndex } = load();
  assert.deepEqual(plain(MarketIndex.LIST.map((item) => [item.key, item.short, item.name])), [
    ["twii", "台股", "台股加權"],
    ["dji", "道瓊", "道瓊工業"],
    ["ixic", "那指", "那斯達克綜合"],
    ["gspc", "標普", "標普 500"],
    ["n225", "日經", "日經 225"],
    ["ks11", "韓綜", "韓國綜合"],
  ]);
  assert.deepEqual(plain(MarketIndex.LIST.slice(1).map((item) => [item.key, item.name, item.zone])),
    MARKET_INDICES.map((item) => [item.key, item.name, item.zone]));
});

test("日K：日期、月/日標籤、開高低收量照原樣", () => {
  const { MarketIndex } = load();
  const daily = MarketIndex.candles([{ date: "2026-11-18", open: 1, high: 3, low: 0.5, close: 2, volume: 9 }], "daily");
  assert.deepEqual(plain(daily), [{ t: "2026-11-18", label: "11/18", open: 1, high: 3, low: 0.5, close: 2, volume: 9 }]);
});

test("週K 以週一為一週、月K 以月份為一組：開盤取第一天、收盤取最後一天、高低取極值、量相加", () => {
  const { MarketIndex } = load();
  const days = [
    { date: "2026-10-29", open: 10, high: 12, low: 9, close: 11, volume: 1 },   // 週四
    { date: "2026-10-30", open: 11, high: 15, low: 10, close: 14, volume: 2 },  // 週五
    { date: "2026-11-02", open: 14, high: 14, low: 8, close: 9, volume: 4 },    // 下週一、下個月
    { date: "2026-11-03", open: 9, high: 10, low: 7, close: 8, volume: null },
  ];
  assert.deepEqual(plain(MarketIndex.candles(days, "weekly")), [
    { t: "2026-10-30", label: "10/30", open: 10, high: 15, low: 9, close: 14, volume: 3 },
    { t: "2026-11-03", label: "11/03", open: 14, high: 14, low: 7, close: 8, volume: 4 },
  ]);
  assert.deepEqual(plain(MarketIndex.candles(days, "monthly")).map((bar) => [bar.t, bar.open, bar.close, bar.high, bar.low]), [
    ["2026-10-30", 10, 14, 15, 9],
    ["2026-11-03", 14, 8, 14, 7],
  ]);
});

test("成交量：有任何一天大於 0 才算有量（日經這類全是 0 的不畫）", () => {
  const { MarketIndex } = load();
  assert.equal(MarketIndex.hasVolume([{ volume: 0 }, { volume: null }]), false);
  assert.equal(MarketIndex.hasVolume([{ volume: 0 }, { volume: 5 }]), true);
  assert.equal(MarketIndex.hasVolume([]), false);
});

test("國外指數的走勢：三期照同一套規則；中期沒有法人那一票、短期不寫 5 日法人", () => {
  const { MarketIndex, TrendVotes } = load();
  const daily = MarketIndex.candles(rows(260), "daily");
  const trend = MarketIndex.trend(daily, { dataDate: "2026-11-18" });
  assert.equal(trend.schema, "market-trend/v1");
  assert.equal(trend.data_date, "2026-11-18");
  assert.deepEqual(plain(trend.periods.map((period) => [period.key, period.conclusion])), [["short", "偏多"], ["medium", "偏多"], ["long", "偏多"]]);
  assert.equal("institutional_20d_billion" in trend.periods[1].metrics, false);
  assert.doesNotMatch(trend.periods[0].sentence + trend.periods[1].sentence, /法人/);
  const read = plain(TrendVotes.read(trend));
  assert.deepEqual(read.periods[1].votes.map((vote) => vote.name), ["MA20", "MA20/60", "近 20 日"]);
  assert.equal(read.title, "三期同調");
});

test("台股加權照舊帶法人（數字由頁面傳入）；K 棒不夠時那一期寫資料不足", () => {
  const { MarketIndex, TrendVotes } = load();
  const daily = MarketIndex.candles(rows(260, { step: -1, start: 400 }), "daily");
  const trend = MarketIndex.trend(daily, { dataDate: "2026-11-18", institutional5: -12.34, institutional20: -56.78 });
  assert.equal(trend.periods[1].metrics.institutional_20d_billion, -56.78);
  assert.match(trend.periods[0].sentence, /5 日法人 −12\.3 億/);
  assert.match(trend.periods[1].sentence, /20 日法人 −56\.8 億/);
  assert.deepEqual(plain(TrendVotes.read(trend)).periods[1].votes.map((vote) => vote.name), ["MA20", "MA20/60", "近 20 日", "法人"]);
  const short = MarketIndex.trend(MarketIndex.candles(rows(100), "daily"), { dataDate: "2026-11-18" });
  assert.deepEqual(plain(short.periods.map((period) => period.state)), ["bullish", "bullish", "insufficient"]);
  assert.match(short.periods[2].sentence, /需要至少 240 根完整日 K，目前只有 100 根/);
});
