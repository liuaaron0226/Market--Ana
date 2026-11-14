// 個股 5 年歷史日線接到圖上（本人 2026-11-14：先做 5 年；畫面不加新按鈕或說明，圖本來就能拖曳縮放）。
// 個股分析帶最近 120 根；歷史另外載入，只取「比那 120 根更早」的部分接在前面。兩邊對不上就不接。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function math() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("../../web/kchart.js", import.meta.url), "utf8"),
    { window, getComputedStyle: () => ({ getPropertyValue: () => "" }), console });
  return window.KChartMath;
}

const bars = [
  { date: "2026-11-05", open: 2190, high: 2210, low: 2180, close: 2200, volume: 20 },
  { date: "2026-11-06", open: 2200, high: 2240, low: 2195, close: 2235, volume: 30 },
];
const history = { schema: "stock-history/v1", code: "2330", as_of: "2026-11-06",
  d: [20211116, 20211117, 20261105, 20261106], o: [600, 603, 2190, 2200], h: [605, 610, 2210, 2240],
  l: [598, 601, 2180, 2195], c: [603, 608, 2200, 2235], v: [1, 2, 20, 30] };

test("歷史只補上比個股分析更早的 K 棒，重疊的部分以個股分析為準", () => {
  const older = math().olderBars(bars, history);
  assert.deepEqual(JSON.parse(JSON.stringify(older)), [
    { date: "2021-11-16", open: 600, high: 605, low: 598, close: 603, volume: 1 },
    { date: "2021-11-17", open: 603, high: 610, low: 601, close: 608, volume: 2 },
  ]);
});

test("歷史晚一天更新也照樣能接（只需要涵蓋個股分析的第一根）", () => {
  const stale = { ...history, as_of: "2026-11-05", d: history.d.slice(0, 3), o: history.o.slice(0, 3),
    h: history.h.slice(0, 3), l: history.l.slice(0, 3), c: history.c.slice(0, 3), v: history.v.slice(0, 3) };
  assert.equal(math().olderBars(bars, stale).length, 2);
});

test("銜接處價格對不上就不接：還原係數不同步時，寧可只顯示 120 根也不畫出斷崖", () => {
  const unadjusted = { ...history, c: [2412, 2432, 8800, 2235] }; // 歷史還沒套用今天新的還原係數
  assert.deepEqual([...math().olderBars(bars, unadjusted)], []);
});

test("歷史沒有涵蓋到個股分析的第一根、格式不對或是別檔的資料，一律不接", () => {
  const gap = { ...history, d: [20211116, 20211117], o: [600, 603], h: [605, 610], l: [598, 601], c: [603, 608], v: [1, 2] };
  assert.deepEqual([...math().olderBars(bars, gap)], []);
  assert.deepEqual([...math().olderBars(bars, { schema: "other" })], []);
  assert.deepEqual([...math().olderBars(bars, null)], []);
  assert.deepEqual([...math().olderBars(bars, { ...history, c: history.c.slice(1) })], []);
  assert.deepEqual([...math().olderBars([], history)], []);
});
