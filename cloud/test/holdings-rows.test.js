// 庫存總覽：把庫存清單（/api/holdings 的 items）讀成畫面要的東西——總數、每一檔的順序、橫條的兩段、賺賠的高低。
// 只驗對外的輸入輸出，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadRead() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/holdings-rows.js", ROOT), "utf8"), { window });
  // 頁面腳本在另一個執行環境跑；轉一次 JSON 再比，順便確認結果是純資料。
  return (items) => JSON.parse(JSON.stringify(window.HoldingsRows.read(items)));
}

// 預覽樣本裡的三檔（市值、成本、損益是伺服器算好的）
const item = (code, name, shares, averageCost, price, extra = {}) => {
  const cost = shares * averageCost, value = price === null ? null : shares * price;
  return { code, name, market: "上市", in_universe: true, shares, average_cost: averageCost, cost_basis: cost, latest_price: price, price_stale: false,
    market_value: value, unrealized_pnl: value === null ? null : value - cost, return_pct: value === null ? null : Math.round((value / cost - 1) * 10000) / 100, note: "", ...extra };
};
const SAMPLE = [item("0050", "元大台灣50", 3000, 58.4, 97.45), item("2330", "台積電", 1000, 980, 2235), item("2603", "長榮", 2000, 215, 245.5)];
const round1 = (value) => Math.round(value * 10) / 10;

test("總數：市值、成本、賺賠與報酬率", () => {
  const read = loadRead()(SAMPLE);
  assert.deepEqual([read.count, read.complete, read.value, read.cost, read.pnl], [3, true, 3018350, 1585200, 1433150]);
  assert.equal(Math.round(read.returnPct * 100) / 100, 90.41);
});

test("一列一檔，照市值由大到小", () => {
  const { rows } = loadRead()(SAMPLE);
  assert.deepEqual(rows.map((row) => row.code), ["2330", "2603", "0050"]);
  assert.deepEqual(rows.map((row) => row.state), ["bull", "bull", "bull"]);
  assert.deepEqual(rows.map((row) => round1(row.share)), [74.0, 16.3, 9.7]);
  // 每一列帶著原本那一筆，畫面要用裡面的名稱、股數、備註等等
  assert.equal(rows[0].item.name, "台積電");
});

test("橫條的兩段：賺錢是「成本＋賺的」，加起來就是這一檔占全部市值的比例", () => {
  const { rows } = loadRead()(SAMPLE);
  const tsmc = rows[0];
  assert.equal(tsmc.edgeKind, "gain");
  assert.equal(round1(tsmc.base), round1(980000 / 3018350 * 100));
  assert.equal(round1(tsmc.edge), round1(1255000 / 3018350 * 100));
  assert.equal(round1(tsmc.base + tsmc.edge), round1(tsmc.share));
});

test("賠錢的一檔：凹下；橫條是「現在的市值＋還差成本的那一段」", () => {
  const { rows, pnl } = loadRead()([...SAMPLE, item("2317", "鴻海", 1000, 210, 189)]);
  const loser = rows.find((row) => row.code === "2317");
  assert.equal(loser.state, "bear");
  assert.equal(loser.edgeKind, "lack");
  const total = 3018350 + 189000;
  assert.equal(round1(loser.base), round1(189000 / total * 100));
  assert.equal(round1(loser.edge), round1(21000 / total * 100));
  assert.equal(round1(loser.share), round1(189000 / total * 100));
  assert.equal(pnl, 1433150 - 21000);
});

test("剛好打平的一檔：平的，橫條只有一段", () => {
  const { rows } = loadRead()([item("1101", "台泥", 1000, 40, 40)]);
  assert.deepEqual([rows[0].state, rows[0].edgeKind, rows[0].edge, rows[0].base], ["flat", null, 0, 100]);
});

test("沒有價格的那一檔：排最後、不畫橫條；總市值與賺賠不給數字，成本照算", () => {
  const read = loadRead()([item("9999", "沒有價格", 500, 100, null), ...SAMPLE]);
  assert.deepEqual(read.rows.map((row) => row.code), ["2330", "2603", "0050", "9999"]);
  const last = read.rows.at(-1);
  assert.deepEqual([last.state, last.share, last.base, last.edge, last.edgeKind], ["none", null, null, null, null]);
  assert.deepEqual([read.complete, read.pnl, read.returnPct], [false, null, null]);
  assert.equal(read.cost, 1585200 + 50000);
  // 有價格的那幾檔照樣有橫條（比的是有價格的那幾檔合起來），但「占庫存」不給數字——全部是多少不知道
  assert.equal(read.rows[0].share, null);
  assert.equal(round1(read.rows[0].base + read.rows[0].edge), 74.0);
});

test("還沒有庫存", () => {
  const read = loadRead()([]);
  assert.deepEqual([read.count, read.complete, read.value, read.cost, read.pnl, read.returnPct, read.rows], [0, false, 0, 0, null, null, []]);
  assert.deepEqual(loadRead()(null).rows, []);
});

test("市值一樣大的維持原本的先後", () => {
  const { rows } = loadRead()([item("1111", "甲", 100, 10, 20), item("2222", "乙", 100, 10, 20), item("3333", "丙", 100, 10, 30)]);
  assert.deepEqual(rows.map((row) => row.code), ["3333", "1111", "2222"]);
});
