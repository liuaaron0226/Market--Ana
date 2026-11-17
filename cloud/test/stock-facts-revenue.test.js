// 個股頁「基本面」的月營收圖（本人 2026-11-17：「營收長條圖 x 軸我不知道是什麼，看不懂圖」）。
// 圖要一眼看懂：橫軸標月份（26/03 這種短格式、隔幾個月標一個）、長條是每月營收（億元）、最新一個月標數值、
// 點某一根顯示那個月的營收與年增。這裡只驗 web/stock-facts.js 的 _logic.revenueChart（輸入資料 → 要畫什麼），畫面由截圖驗。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/stock-facts.js", ROOT), "utf8"), { window, document: {}, console });
  return window.StockFacts._logic;
}
const { revenueChart } = load();
const plain = (value) => JSON.parse(JSON.stringify(value));

// 2025-09～2026-09 共 13 個月，由舊到新（和 twon_analysis/facts.py 給的一樣）
const PERIODS = ["2025-09", "2025-10", "2025-11", "2025-12", ...Array.from({ length: 9 }, (_, i) => `2026-${String(i + 1).padStart(2, "0")}`)];
const series = (revenues, yoys = revenues.map(() => 10)) => PERIODS.map((period, i) => ({ period, yoy: yoys[i], revenue_100m: revenues[i] }));

test("長條畫每月營收（億元）；最高的那一根頂到圖頂、從底部長上來", () => {
  const chart = revenueChart(series([100, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 300]));
  assert.equal(chart.kind, "revenue");
  assert.equal(chart.title, "每月營收（億元）");
  assert.equal(chart.bars.length, 13);
  const last = chart.bars.at(-1), first = chart.bars[0];
  assert.equal(last.top, 0);
  assert.equal(last.height, 1);
  assert.ok(Math.abs(first.top + first.height - 1) < 1e-9, "從底部長上來");
  assert.ok(Math.abs(first.height - 100 / 300) < 1e-9);
});

test("橫軸：短格式月份，從最新一個月往回每 3 個月標一個（最新的一定有標）", () => {
  const chart = revenueChart(series(PERIODS.map(() => 50)));
  const ticks = chart.bars.map((bar) => bar.tick).filter(Boolean);
  assert.deepEqual(ticks, ["25/09", "25/12", "26/03", "26/06", "26/09"]);
  assert.equal(chart.bars.at(-1).tick, "26/09");
  assert.equal(chart.bars[1].tick, "");
});

test("預設選最新一個月：長條上標數值，讀數寫哪年哪月、營收幾億、年增多少", () => {
  const chart = revenueChart(series([...PERIODS.slice(1).map(() => 100), 2365.43], [...PERIODS.slice(1).map(() => 5), 36.4]));
  assert.equal(chart.selected, 12);
  const last = chart.bars[12];
  assert.equal(last.label, "2,365");
  assert.equal(last.read, "2026 年 9 月　營收 2,365 億元　年增 +36.4%");
  assert.equal(last.aria, "2026 年 9 月營收 2,365 億元，年增 +36.4%");
});

test("最新一個月有月增時讀數一起寫（圖上方原本那一列「9 月營收 月增 年增」併進讀數，不重複兩次）", () => {
  const data = series(PERIODS.map(() => 100), PERIODS.map(() => 36.4));
  const chart = revenueChart(data, { period: "2026-09", mom: 15.4, yoy: 36.4 });
  assert.equal(chart.bars[12].read, "2026 年 9 月　營收 100 億元　月增 +15.4%　年增 +36.4%");
  assert.equal(chart.bars[11].read, "2026 年 8 月　營收 100 億元　年增 +36.4%", "其他月份沒有月增");
});

test("億元的位數：百億以上取整數、十億以上一位小數、更小兩位小數；年增 0 寫 0.0%、負的帶負號", () => {
  const chart = revenueChart(series([0.35, 12.345, 999.6, ...PERIODS.slice(3).map(() => 1)], [0, -3.21, null, ...PERIODS.slice(3).map(() => 1)]));
  assert.equal(chart.bars[0].label, "0.35");
  assert.equal(chart.bars[0].read, "2025 年 9 月　營收 0.35 億元　年增 0.0%");
  assert.equal(chart.bars[1].label, "12.3");
  assert.equal(chart.bars[1].read, "2025 年 10 月　營收 12.3 億元　年增 -3.2%");
  assert.equal(chart.bars[2].label, "1,000");
  assert.equal(chart.bars[2].read, "2025 年 11 月　營收 1,000 億元　年增：去年同月沒有資料", "沒有年增就講明");
});

test("缺資料的月份留空、不能選；預設選的是最後一個有資料的月份", () => {
  const revenues = PERIODS.map(() => 80);
  revenues[5] = null;
  revenues[12] = null;
  const chart = revenueChart(series(revenues));
  assert.equal(chart.bars[5].missing, true);
  assert.equal(chart.bars[5].height, 0);
  assert.equal(chart.bars[5].read, "2026 年 2 月　沒有營收資料");
  assert.equal(chart.selected, 11);
});

test("舊資料包沒有營收金額時，照舊畫年增率，但同樣標月份、零軸上下長、讀數講明是年增", () => {
  const yoys = [10, -20, 30, 0, 5, 5, 5, 5, 5, 5, 5, 5, -40];
  const chart = revenueChart(PERIODS.map((period, i) => ({ period, yoy: yoys[i] })));
  assert.equal(chart.kind, "yoy");
  assert.equal(chart.title, "每月營收年增率（%）");
  assert.equal(chart.bars.at(-1).tick, "26/09");
  // 最大 30、最小 -40：零軸在 30/70 的位置；正的往上、負的往下
  assert.ok(Math.abs(chart.zero - 30 / 70) < 1e-9);
  const up = chart.bars[2], down = chart.bars[12];
  assert.equal(up.top, 0);
  assert.ok(Math.abs(up.top + up.height - chart.zero) < 1e-9);
  assert.ok(Math.abs(down.top - chart.zero) < 1e-9);
  assert.ok(Math.abs(down.top + down.height - 1) < 1e-9);
  assert.equal(down.label, "-40.0%");
  assert.equal(down.read, "2026 年 9 月　年增 -40.0%");
});

test("什麼都沒有（金額與年增全缺）就不畫", () => {
  assert.equal(revenueChart(PERIODS.map((period) => ({ period, yoy: null, revenue_100m: null }))), null);
  assert.equal(revenueChart([]), null);
  assert.equal(revenueChart(undefined), null);
});

test("營收全部一樣多：每一根一樣高，不會除以零", () => {
  const chart = plain(revenueChart(series(PERIODS.map(() => 7))));
  assert.ok(chart.bars.every((bar) => bar.height === 1 && bar.top === 0));
});

test("位數照捨入後的值決定：99.996 億寫 100、9.996 億寫 10.0（審查抓到的邊界）", () => {
  const chart = revenueChart(series([99.996, 9.996, ...PERIODS.slice(2).map(() => 1)]));
  assert.equal(chart.bars[0].label, "100");
  assert.equal(chart.bars[1].label, "10.0");
});

test("最新一個有資料的月份帶 latest（數值一直標著）；年增率畫法的負值帶 negative（數值標在長條下方）", () => {
  const revenues = PERIODS.map(() => 80);
  revenues[12] = null;
  const chart = revenueChart(series(revenues));
  assert.deepEqual(chart.bars.map((bar, i) => (bar.latest ? i : null)).filter((i) => i !== null), [11]);
  const yoy = revenueChart(PERIODS.map((period, i) => ({ period, yoy: i === 12 ? -5 : 5 })));
  assert.equal(yoy.bars[12].negative, true);
  assert.equal(yoy.bars[11].negative, false);
});
