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
  assert.deepEqual([read.count, read.complete, read.value, read.cost, read.pnl, read.state], [3, true, 3018350, 1585200, 1433150, "bull"]);
  assert.equal(Math.round(read.returnPct * 100) / 100, 90.41);
});

test("一列一檔，照市值由大到小", () => {
  const { rows } = loadRead()(SAMPLE);
  assert.deepEqual(rows.map((row) => row.code), ["2330", "2603", "0050"]);
  assert.deepEqual(rows.map((row) => row.state), ["bull", "bull", "bull"]);
  assert.deepEqual(rows.map((row) => row.gain), [1255000, 61000, 117150]);   // 每一檔賺多少由這裡給，畫面不另外算
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
  assert.deepEqual([loser.state, loser.gain], ["bear", -21000]);
  assert.equal(loser.edgeKind, "lack");
  const total = 3018350 + 189000;
  assert.equal(round1(loser.base), round1(189000 / total * 100));
  assert.equal(round1(loser.edge), round1(21000 / total * 100));
  assert.equal(round1(loser.share), round1(189000 / total * 100));
  assert.equal(pnl, 1433150 - 21000);
});

test("賠很多而且占比大的一檔：橫條不會畫出去（整條的寬度改用它的成本，大家一起等比例縮短）", () => {
  const read = loadRead();
  const alone = read([item("2317", "鴻海", 1000, 200, 100)]).rows[0];          // 只有一檔，賠一半
  assert.deepEqual([round1(alone.base), round1(alone.edge), round1(alone.base + alone.edge), alone.share], [50, 50, 100, 100]);
  const { rows } = read([item("2317", "鴻海", 1000, 200, 100), item("2330", "台積電", 10, 980, 2235)]);   // 市值 100,000＋22,350；鴻海的成本 200,000
  for (const row of rows) assert.ok(row.base + row.edge <= 100.0001, `${row.code} ${row.base + row.edge}`);
  assert.equal(round1(rows[0].base + rows[0].edge), 100);                       // 最長的那一條剛好到底
  assert.equal(round1(rows[1].base + rows[1].edge), round1(22350 / 200000 * 100));
  assert.equal(round1(rows[1].share), round1(22350 / 122350 * 100));           // 占庫存照樣是對全部的市值算
});

test("剛好打平的一檔：平的，橫條只有一段", () => {
  const { rows } = loadRead()([item("1101", "台泥", 1000, 40, 40)]);
  assert.deepEqual([rows[0].state, rows[0].gain, rows[0].edgeKind, rows[0].edge, rows[0].base], ["flat", 0, null, 0, 100]);
});

test("沒有價格的那一檔：排最後、不畫橫條；總市值與賺賠不給數字，成本照算", () => {
  const read = loadRead()([item("9999", "沒有價格", 500, 100, null), ...SAMPLE]);
  assert.deepEqual(read.rows.map((row) => row.code), ["2330", "2603", "0050", "9999"]);
  const last = read.rows.at(-1);
  assert.deepEqual([last.state, last.gain, last.share, last.base, last.edge, last.edgeKind], ["none", null, null, null, null, null]);
  assert.deepEqual([read.complete, read.pnl, read.returnPct, read.state], [false, null, null, "none"]);
  assert.equal(read.cost, 1585200 + 50000);
  // 有價格的那幾檔照樣有橫條（比的是有價格的那幾檔合起來），但「占庫存」不給數字——全部是多少不知道
  assert.equal(read.rows[0].share, null);
  assert.equal(round1(read.rows[0].base + read.rows[0].edge), 74.0);
});

test("還沒有庫存", () => {
  const read = loadRead()([]);
  assert.deepEqual([read.count, read.complete, read.value, read.cost, read.pnl, read.returnPct, read.state, read.rows], [0, false, 0, 0, null, null, "none", []]);
  assert.deepEqual(loadRead()(null).rows, []);
});

test("市值一樣大的維持原本的先後", () => {
  const { rows } = loadRead()([item("1111", "甲", 100, 10, 20), item("2222", "乙", 100, 10, 20), item("3333", "丙", 100, 10, 30)]);
  assert.deepEqual(rows.map((row) => row.code), ["3333", "1111", "2222"]);
});

// ---- 收盤後到日資料發布前（本人 11-19）：價格是哪一天、哪一種，標示和個股頁一致 ----
function loadRows() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/holdings-rows.js", ROOT), "utf8"), { window });
  return window.HoldingsRows;
}

test("收盤後用今天的收盤：寫「今日收盤（日資料稍後更新）」，明細寫「今日收盤市值」", () => {
  const rows = loadRows();
  const items = SAMPLE.map(entry => ({ ...entry, price_session: "close", price_as_of: "2026-11-10" }));
  assert.equal(rows.priceLine(items), "庫存價格：今日收盤（日資料稍後更新）");
  assert.equal(rows.valueLabel(items[0]), "今日收盤市值");
});

test("收盤價還沒確認：寫「今日最後成交（收盤價更新中）」", () => {
  const rows = loadRows();
  const items = [{ ...SAMPLE[0], price_session: "close" }, { ...SAMPLE[1], price_session: "close_pending" }];
  assert.equal(rows.priceLine(items), "庫存價格：今日最後成交（收盤價更新中）");
  assert.equal(rows.valueLabel(items[1]), "最後成交市值");
});

test("有幾檔沒拿到今天的收盤、還是日資料：寫出那幾檔停在哪一天", () => {
  const rows = loadRows();
  const items = [{ ...SAMPLE[0], price_session: "close", price_as_of: "2026-11-10" }, { ...SAMPLE[1], price_session: "daily", price_as_of: "2026-11-09" }];
  assert.equal(rows.priceLine(items), "庫存價格：今日收盤（日資料稍後更新），部分仍是 2026-11-09");
});

test("盤中、日資料發布後：照舊寫「庫存價格至 日期」", () => {
  const rows = loadRows();
  const items = SAMPLE.map(entry => ({ ...entry, price_session: "daily", price_as_of: "2026-11-10" }));
  assert.equal(rows.priceLine(items), "庫存價格至 2026-11-10");
  assert.equal(rows.priceLine([{ ...items[0], price_stale: true }]), "庫存價格至 2026-11-10（部分沿用上次資料）");
  assert.equal(rows.priceLine([]), "尚無庫存價格");
  assert.equal(rows.valueLabel(items[0]), "市值");
  assert.equal(rows.valueLabel({ price_stale: true }), "市值（上次收盤）");
});
