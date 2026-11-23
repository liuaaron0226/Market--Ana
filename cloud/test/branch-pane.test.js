// 主力分點副圖（本人 2026-11-24，.scratch/broker-branches 票 01）：web/branch-pane.js 裡「算」的那一半——
// 這一段的名單、合計、單一分點、選的分點離開名單時回到合計。照 market-brief-panel.test.js 的做法在另一個執行環境載入來測。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const window = {};
vm.runInNewContext(readFileSync(new URL("../../web/branch-pane.js", import.meta.url), "utf8"), { window });
const { dateInt, fromApi, indexBranches, topBranches, pick, valueAt, TOP } = window.BranchPane.model;

// 十根 K 棒（11-09 到 11-20 的平日）；分點的資料裡多放一天假日（11-15）與一天比 K 棒還早的（11-06），都對不到 K 棒
const CANDLES = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"];
const DATES = CANDLES.map(dateInt);
const series = (id, name, days) => ({ id, name, d: Object.keys(days).map(Number), n: Object.values(days) });
const BRANCHES = [
  series("A", "甲", { 20261106: 999, 20261109: 100, 20261110: 100, 20261111: 100, 20261115: 999, 20261119: -50 }),
  series("B", "乙", { 20261109: -300, 20261118: 20, 20261119: 20, 20261120: 20 }),
  series("C", "丙", { 20261112: 10, 20261113: -10 }),                       // 前五根合計 0
  series("D", "丁", { 20261116: -500, 20261117: -500 }),
  series("E", "戊", { 20261109: 5, 20261116: 40 }),
  series("F", "己", { 20261110: 60, 20261117: 30 }),
  series("G", "庚", { 20261111: -70, 20261118: 300 }),
  series("H", "辛", { 20261120: 0.2 }),                                      // 四捨五入是 0 張
];
const rows = indexBranches(BRANCHES, DATES);
// 模組在另一個執行環境裡，回來的陣列不是這裡的 Array：比對前用 Array.from 換成這裡的
const names = (top) => Array.from(top, (item) => `${item.row.name}${Math.round(item.total)}`);
const bars = (shown, start, end) => { const out = []; for (let i = start; i < end; i += 1) out.push(valueAt(shown, i)); return out; };

test("日期換成整數；認不得的是 0", () => {
  assert.equal(dateInt("2026-11-09"), 20261109);
  assert.equal(dateInt("2026-11-09T09:00:00+08:00"), 20261109);
  for (const bad of ["", null, undefined, "11/09", "abcd-ef-gh"]) assert.equal(dateInt(bad), 0, String(bad));
});

test("API 的回應：是這一檔而且有資料才用；長度對不起來的分點丟掉", () => {
  const payload = { available: true, code: "2330", through: "2026-11-20", branches: [
    { id: "1040", name: "臺銀", d: [20261119, 20261120], n: [5, -6] },
    { id: "1470", name: "", d: [20261120], n: [1] },
    { id: "bad", name: "長度不同", d: [20261120], n: [] },
    { id: "empty", name: "沒有日子", d: [], n: [] },
    null,
  ] };
  const got = fromApi(payload, "2330");
  assert.deepEqual(JSON.parse(JSON.stringify(got)), { code: "2330", through: "2026-11-20", branches: [
    { id: "1040", name: "臺銀", d: [20261119, 20261120], n: [5, -6] }, { id: "1470", name: "1470", d: [20261120], n: [1] }] });
  assert.equal(fromApi(payload, "2454"), null, "別檔的回應");
  assert.equal(fromApi({ available: false }, "2330"), null);
  assert.equal(fromApi({ available: true, code: "2330", branches: [] }, "2330"), null);
  assert.equal(fromApi({ available: true, code: "2330", branches: [{ id: "x", d: [1], n: [] }] }, "2330"), null, "沒有一個分點可用");
  assert.equal(fromApi(null, "2330"), null);
});

test("照 K 棒排好：沒有 K 棒的日子（假日、比第一根還早）不算，沒有資料的那一天是空的", () => {
  const a = rows[0];
  assert.deepEqual([...a.net].map((value) => (Number.isNaN(value) ? null : value)), [100, 100, 100, null, null, null, null, null, -50, null]);
  assert.equal(a.sum[10] - a.sum[0], 250, "999 那兩天沒有算進來");
  assert.equal(a.count[10], 4);
});

test("一段最多列十個分點（本人 2026-11-24：「選的分點有點少可以再多一點」）", () => {
  assert.equal(TOP, 10);
  const many = indexBranches(Array.from({ length: 14 }, (_, b) => ({ id: `M${b}`, name: `多${b}`, d: [20261110], n: [b + 1] })), [20261110]);
  assert.equal(topBranches(many, 0, 1).length, 10);
  assert.deepEqual(Array.from(topBranches(many, 0, 1), (item) => item.total).slice(0, 3), [14, 13, 12]);
});

test("整段：照合計買賣超的絕對值排；合計是 0 張的不算", () => {
  assert.deepEqual(names(topBranches(rows, 0, 10, 5)), ["丁-1000", "甲250", "乙-240", "庚230", "己90"]);
});

test("換一段，名單與各自的合計跟著換", () => {
  assert.deepEqual(names(topBranches(rows, 0, 5, 5)), ["甲300", "乙-300", "庚-70", "己60", "戊5"], "前五根：丙合計 0、丁沒有資料，都不在");
  assert.deepEqual(names(topBranches(rows, 5, 10, 5)), ["丁-1000", "庚300", "乙60", "甲-50", "戊40"], "後五根");
  assert.deepEqual(names(topBranches(rows, 3, 5)), [], "11-12、11-13 只有丙，合計 0");
  assert.deepEqual(names(topBranches(rows, 3, 4)), ["丙10"]);
});

test("合計：名單上那幾個分點有資料的加起來；都沒有資料的那一天不畫；每天加起來等於名單的合計", () => {
  const top = topBranches(rows, 0, 5, 5), picked = pick(top, null);
  assert.equal(picked.selected, null);
  assert.equal(picked.rows.length, 5);
  const values = bars(picked.rows, 0, 5);
  assert.deepEqual(values, [100 - 300 + 5, 100 + 60, 100 - 70, null, null]);
  assert.equal(values.reduce((sum, value) => sum + (value || 0), 0), top.reduce((sum, item) => sum + item.total, 0));
});

test("只看一個分點：畫的就是它自己，每天加起來等於它那一顆鍵上的數字", () => {
  const top = topBranches(rows, 5, 10), picked = pick(top, "G");
  assert.equal(picked.selected, "G");
  assert.deepEqual(Array.from(picked.rows, (row) => row.name), ["庚"]);
  const values = bars(picked.rows, 5, 10);
  assert.deepEqual(values, [null, null, 300, null, null]);
  assert.equal(values.reduce((sum, value) => sum + (value || 0), 0), top.find((item) => item.row.id === "G").total);
});

test("選的分點不在這一段的名單裡了：回到合計", () => {
  const before = pick(topBranches(rows, 5, 10), "D");
  assert.equal(before.selected, "D");
  const after = pick(topBranches(rows, 0, 5, 5), "D");   // 往前拖到前五根：丁沒有資料
  assert.equal(after.selected, null);
  assert.equal(after.rows.length, 5);
  assert.equal(pick([], "D").selected, null);
  assert.equal(pick([], null).rows.length, 0);
});

test("四十個分點、七百五十天：排一次就好，之後每換一段只看分點數，不逐日重算", () => {
  const dates = [], candles = [];
  for (let i = 0; i < 1250; i += 1) { const day = 20200000 + i; candles.push(day); if (i >= 500) dates.push(day); }
  const many = [];
  for (let b = 0; b < 40; b += 1) many.push({ id: `B${b}`, name: `分點${b}`, d: dates, n: dates.map((_, k) => ((k * 7 + b * 13) % 41) - 20) });
  const indexed = indexBranches(many, candles);
  assert.equal(indexed.length, 40);
  assert.equal(indexed[0].count[1250], 750);
  const brute = (row, start, end) => { let sum = 0; for (let i = start; i < end; i += 1) if (!Number.isNaN(row.net[i])) sum += row.net[i]; return sum; };
  for (const [start, end] of [[0, 1250], [400, 600], [1190, 1250], [0, 500]]) {
    const top = topBranches(indexed, start, end);
    assert.ok(top.length <= TOP);
    for (const item of top) assert.equal(item.total, brute(item.row, start, end), `${item.row.id} ${start}-${end}`);
    const totals = indexed.map((row) => Math.abs(brute(row, start, end))).filter((value) => Math.round(value) !== 0).sort((a, b) => b - a).slice(0, TOP);
    assert.deepEqual(Array.from(top, (item) => Math.abs(item.total)), totals, `${start}-${end} 的前十名`);
  }
  assert.equal(topBranches(indexed, 0, 500).length, 0, "還沒有分點資料的那一段");
});
