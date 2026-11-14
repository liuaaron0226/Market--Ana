// 資金浮雕的版面計算（立體重設計 E：本人 2026-11-15 選的「資金浮雕」）。
// web/relief-layout.js 只做計算：每條軌道每一天要往上長多高、往下凹多深、哪一根是今天。畫面在 web/relief.js。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function load() {
  const sandbox = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(new URL("../../web/relief-layout.js", import.meta.url), "utf8"), sandbox, { filename: "relief-layout.js" });
  // 跨執行環境的陣列用 JSON 轉一次，deepEqual 才比得了
  return input => JSON.parse(JSON.stringify(sandbox.window.ReliefLayout.layout(input)));
}

const item = (extra = {}) => ({ key: "foreign", name: "外資現貨", value: "賣 912 億", tone: "down", signed: true, values: [10, -20, 5], ...extra });

test("有買有賣的項目：買超往上長、賣超往下凹，幅度以這段期間最大的那一天為 1", () => {
  const [lane] = load()([item({ values: [50, -100, 25, 0] })]);
  assert.equal(lane.signed, true);
  assert.deepEqual(lane.days.map(day => day.direction), ["up", "down", "up", "flat"]);
  assert.deepEqual(lane.days.map(day => day.size), [0.5, 1, 0.25, 0]);
});

test("只有高低的項目：高度是這段期間裡的相對高低，最低的那一天是 0、最高是 1", () => {
  const [lane] = load()([item({ key: "margin", name: "融資餘額", signed: false, values: [5300, 5400, 5350] })]);
  assert.equal(lane.signed, false);
  assert.deepEqual(lane.days.map(day => day.direction), ["level", "level", "level"]);
  assert.deepEqual(lane.days.map(day => day.size), [0, 1, 0.5]);
});

test("最後一天標成今天；名稱、今天的字、顏色原樣帶出", () => {
  const [lane] = load()([item()]);
  assert.deepEqual(lane.days.map(day => day.today), [false, false, true]);
  assert.deepEqual({ key: lane.key, name: lane.name, value: lane.value, tone: lane.tone }, { key: "foreign", name: "外資現貨", value: "賣 912 億", tone: "down" });
});

test("數值都一樣、全部是 0、或夾著缺值時不出錯：缺值那天不畫，其餘照算", () => {
  const layout = load();
  assert.deepEqual(layout([item({ signed: false, values: [7, 7, 7] })])[0].days.map(day => day.size), [1, 1, 1], "都一樣高：畫成一樣高，不是 0");
  assert.deepEqual(layout([item({ values: [0, 0] })])[0].days.map(day => [day.direction, day.size]), [["flat", 0], ["flat", 0]]);
  const [gappy] = layout([item({ values: [10, null, NaN, -10] })]);
  assert.deepEqual(gappy.days.map(day => day.direction), ["up", "none", "none", "down"]);
  assert.deepEqual(gappy.days.map(day => day.size), [1, 0, 0, 1]);
  assert.deepEqual(layout([item({ values: [] })])[0].days, []);
  assert.deepEqual(layout([]), []);
});

test("軌道的順序和輸入一樣；天數不同的項目各算各的", () => {
  const lanes = load()([item({ key: "a", values: [1, 2] }), item({ key: "b", values: [3, 2, 1] })]);
  assert.deepEqual(lanes.map(lane => [lane.key, lane.days.length]), [["a", 2], ["b", 3]]);
});
