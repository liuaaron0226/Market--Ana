// 短中長期走勢：把現有走勢資料（market-trend/v1）讀成畫面要的東西——每一期的每一票、最上面那一句。
// 只驗對外的輸入輸出，不碰畫面。票的比法要和規則（market_trend.py）一樣；算出來和資料裡的結論對不上時不給票。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadRead() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/trend-votes.js", ROOT), "utf8"), { window });
  // 頁面腳本在另一個執行環境跑；轉一次 JSON 再比，順便確認結果是純資料。
  return (trend) => JSON.parse(JSON.stringify(window.TrendVotes.read(trend)));
}

// 樣本資料（資料日 2026-11-06）用正式規則實算出來的結果
const SAMPLE = {
  schema: "market-trend/v1", rule_version: "market-trend-v1", data_date: "2026-11-06",
  periods: [
    { key: "short", label: "短期", state: "bearish", conclusion: "偏空",
      sentence: "偏空：收盤 39,701.64 低於 MA5 42,490.59，MACD 柱 −762.83，KD 14.1／15.1，RSI 16.1；量比 1.33 倍，5 日法人 −3,955.9 億。",
      metrics: { close: 39701.64, ma5: 42490.59, ma10: 44320.63, macd_hist: -762.8345, kd_k: 14.1467, kd_d: 15.0613, rsi14: 16.1281, volume_ratio_20d: 1.3314, institutional_5d_billion: -3955.897 } },
    { key: "medium", label: "中期", state: "bearish", conclusion: "偏空",
      sentence: "偏空：收盤 39,701.64 低於 MA20 46,133.96，MA20 低於 MA60 46,415.23，近 20 日 −20.3%；20 日法人 −12,581.2 億。",
      metrics: { close: 39701.64, ma20: 46133.9585, ma60: 46415.2255, return_20d_pct: -20.3139, institutional_20d_billion: -12581.1729 } },
    { key: "long", label: "長期", state: "neutral", conclusion: "盤整",
      sentence: "盤整：收盤 39,701.64 低於 MA120 45,346.99，MA60 46,415.23／MA120 45,346.99／MA240 38,834.74，近 60 日 −10.2%。",
      metrics: { close: 39701.64, ma60: 46415.2255, ma120: 45346.9919, ma240: 38834.7439, return_60d_pct: -10.2299 } },
  ],
};
const clone = (value) => JSON.parse(JSON.stringify(value));
const sides = (period) => period.votes.map((vote) => vote.side);
const names = (period) => period.votes.map((vote) => vote.name);

test("每一期照規則的順序給出每一票：短期 5 票、中期與長期各 4 票", () => {
  const { periods } = loadRead()(SAMPLE);
  assert.deepEqual(periods.map((period) => [period.key, period.label, period.conclusion, period.state]),
    [["short", "短期", "偏空", "bear"], ["medium", "中期", "偏空", "bear"], ["long", "長期", "盤整", "flat"]]);
  assert.deepEqual(names(periods[0]), ["MA5", "MA5/10", "MACD", "KD", "RSI"]);
  assert.deepEqual(sides(periods[0]), [-1, -1, -1, -1, -1]);
  assert.deepEqual(names(periods[1]), ["MA20", "MA20/60", "近 20 日", "法人"]);
  assert.deepEqual(sides(periods[1]), [-1, -1, -1, -1]);
  assert.deepEqual(names(periods[2]), ["MA120", "MA60/120", "MA120/240", "近 60 日"]);
  assert.deepEqual(sides(periods[2]), [-1, 1, 1, -1]);
});

test("每一票帶著那個指標實際的數字", () => {
  const { periods } = loadRead()(SAMPLE);
  assert.deepEqual(periods[0].votes.map((vote) => vote.text), [
    "收盤 39,701.64 低於 MA5 42,490.59（−6.6%）",
    "MA5 42,490.59 低於 MA10 44,320.63",
    "MACD 柱 −762.83，在零軸下",
    "K 14.1 低於 D 15.1",
    "RSI14 16.1，低於 50",
  ]);
  assert.deepEqual(periods[1].votes.map((vote) => vote.text), [
    "收盤 39,701.64 低於 MA20 46,133.96（−13.9%）",
    "MA20 46,133.96 低於 MA60 46,415.23",
    "近 20 日 −20.3%",
    "近 20 日外資加投信合計賣超 12,581.2 億",
  ]);
  assert.deepEqual(periods[2].votes.map((vote) => vote.text), [
    "收盤 39,701.64 低於 MA120 45,346.99（−12.4%）",
    "MA60 46,415.23 高於 MA120 45,346.99",
    "MA120 45,346.99 高於 MA240 38,834.74",
    "近 60 日 −10.2%",
  ]);
});

test("短期另外帶著不算票的量比與 5 日法人；其他期沒有", () => {
  const { periods } = loadRead()(SAMPLE);
  assert.equal(periods[0].extra, "量比 1.33 倍，5 日法人 −3,955.9 億");
  assert.equal(periods[1].extra, null);
  assert.equal(periods[2].extra, null);
  const noExtra = clone(SAMPLE);
  noExtra.periods[0].metrics.volume_ratio_20d = null;
  noExtra.periods[0].metrics.institutional_5d_billion = null;
  assert.equal(loadRead()(noExtra).periods[0].extra, null);
});

test("原本那句說明留著（拿掉開頭的結論），畫面退回文字時用", () => {
  const { periods } = loadRead()(SAMPLE);
  assert.equal(periods[2].sentence, "收盤 39,701.64 低於 MA120 45,346.99，MA60 46,415.23／MA120 45,346.99／MA240 38,834.74，近 60 日 −10.2%。");
});

test("買超、站上、零軸上的寫法", () => {
  const trend = clone(SAMPLE);
  Object.assign(trend.periods[1], { state: "bullish", conclusion: "偏多" });
  Object.assign(trend.periods[1].metrics, { close: 47000, ma20: 46133.9585, ma60: 45000, return_20d_pct: 3.21, institutional_20d_billion: 812.34 });
  Object.assign(trend.periods[0], { state: "bullish", conclusion: "偏多" });
  Object.assign(trend.periods[0].metrics, { close: 47000, ma5: 46000, ma10: 45000, macd_hist: 12.5, kd_k: 80.24, kd_d: 70, rsi14: 66.66 });
  const { periods } = loadRead()(trend);
  assert.deepEqual(periods[1].votes.map((vote) => vote.text), [
    "收盤 47,000.00 高於 MA20 46,133.96（+1.9%）",
    "MA20 46,133.96 高於 MA60 45,000.00",
    "近 20 日 +3.2%",
    "近 20 日外資加投信合計買超 812.3 億",
  ]);
  assert.deepEqual(periods[0].votes.map((vote) => vote.text).slice(2), ["MACD 柱 +12.50，在零軸上", "K 80.2 高於 D 70.0", "RSI14 66.7，高於 50"]);
  assert.deepEqual(sides(periods[0]), [1, 1, 1, 1, 1]);
});

test("兩個數字相等或缺資料的那一票沒有方向", () => {
  const trend = clone(SAMPLE);
  trend.periods[1].metrics.institutional_20d_billion = null;   // 缺資料
  trend.periods[1].metrics.return_20d_pct = 0;                 // 剛好等於 0
  const medium = loadRead()(trend).periods[1];
  assert.deepEqual(sides(medium), [-1, -1, 0, 0]);
  assert.equal(medium.votes[2].text, "近 20 日 0.0%");
  assert.equal(medium.votes[3].text, "近 20 日外資加投信合計：沒有資料");
  // 兩票偏空、兩票沒有方向，合計 −2，和資料裡的「偏空」對得上
  assert.equal(medium.state, "bear");
});

test("算出來的票和資料裡的結論對不上：那一期不給票", () => {
  const trend = clone(SAMPLE);
  Object.assign(trend.periods[2], { state: "bullish", conclusion: "偏多" });   // 票是 2 空 2 多，不該是偏多
  const long = loadRead()(trend).periods[2];
  assert.equal(long.votes, null);
  assert.equal(long.state, "bull");
  assert.equal(long.conclusion, "偏多");
  // 退回的文字不帶開頭的結論——那句話開頭寫的結論可能和資料裡的結論欄位不一樣
  assert.equal(long.sentence, "收盤 39,701.64 低於 MA120 45,346.99，MA60 46,415.23／MA120 45,346.99／MA240 38,834.74，近 60 日 −10.2%。");
});

test("資料不足的那一期：沒有票，帶著原本那句說明", () => {
  const trend = clone(SAMPLE);
  trend.periods[2] = { key: "long", label: "長期", state: "insufficient", conclusion: "資料不足",
    sentence: "資料不足：需要至少 240 根完整日 K，目前只有 180 根。", metrics: {} };
  const read = loadRead()(trend);
  assert.equal(read.periods[2].state, "none");
  assert.equal(read.periods[2].votes, null);
  assert.equal(read.periods[2].sentence, "需要至少 240 根完整日 K，目前只有 180 根。");
});

test("最上面那一句：三期一不一致", () => {
  const read = loadRead();
  const withStates = (...states) => {
    const trend = clone(SAMPLE);
    states.forEach(([state, conclusion], index) => Object.assign(trend.periods[index], { state, conclusion }));
    const { title, note } = read(trend);
    return [title, note];
  };
  const BULL = ["bullish", "偏多"], BEAR = ["bearish", "偏空"], FLAT = ["neutral", "盤整"], NONE = ["insufficient", "資料不足"];
  assert.deepEqual([read(SAMPLE).title, read(SAMPLE).note], ["三期不同調", "短線偏空、中線偏空、長線盤整"]);
  assert.deepEqual(withStates(BEAR, BEAR, BEAR), ["三期同調", "短中長線都偏空"]);
  assert.deepEqual(withStates(BULL, BULL, BULL), ["三期同調", "短中長線都偏多"]);
  assert.deepEqual(withStates(FLAT, FLAT, FLAT), ["三期都在盤整", ""]);
  assert.deepEqual(withStates(BULL, FLAT, BEAR), ["三期不同調", "短線偏多、中線盤整、長線偏空"]);
  assert.deepEqual(withStates(BEAR, BEAR, NONE), ["長期資料不足", "短線偏空、中線偏空"]);
  assert.deepEqual(withStates(NONE, NONE, NONE), ["資料不足", ""]);
});

test("不是走勢資料就回 null", () => {
  const read = loadRead();
  assert.equal(read(null), null);
  assert.equal(read({ schema: "something-else", periods: [] }), null);
});
