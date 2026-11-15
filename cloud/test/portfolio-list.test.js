// 自選與庫存併成一份清單（本人 2026-11-16：頁面太長、要整合；看樣品後選「併成一份清單」）：
// 把庫存（/api/holdings 的 items）與自選（/api/watchlist 的 items）整理成一檔一列。只驗對外的輸入輸出，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadRead() {
  const window = {};
  for (const file of ["web/holdings-rows.js", "web/portfolio-list.js"]) vm.runInNewContext(readFileSync(new URL(file, ROOT), "utf8"), { window });
  // 頁面腳本在另一個執行環境跑；轉一次 JSON 再比，順便確認結果是純資料。
  return (holdings, watchlist) => JSON.parse(JSON.stringify(window.PortfolioList.read(holdings, watchlist)));
}

const holding = (code, name, shares, cost, price, extra = {}) => ({
  code, name, market: "上市", in_universe: true, shares, average_cost: cost, cost_basis: shares * cost,
  latest_price: price, market_value: price === null ? null : shares * price,
  unrealized_pnl: price === null ? null : shares * (price - cost), return_pct: price === null ? null : (price / cost - 1) * 100,
  price_stale: false, note: "", ...extra,
});
const watch = (code, name, horizon, extra = {}) => ({ code, name, market: "上市", in_universe: true, horizon, ...extra });

// 預覽樣本：庫存三檔、自選三檔，其中台積電與長榮兩邊都有
const HOLDINGS = [holding("2603", "長榮", 2000, 215, 245.5), holding("2330", "台積電", 1000, 980, 2235), holding("0050", "元大台灣50", 3000, 58.4, 97.45)];
const WATCHLIST = [watch("6669", "緯穎", "none"), watch("2603", "長榮", "short"), watch("2330", "台積電", "long")];

test("一檔只出現一次：庫存照市值由大到小排前面，只在自選的排後面（照自選原本的先後）", () => {
  const { rows } = loadRead()(HOLDINGS, WATCHLIST);
  assert.deepEqual(rows.map((row) => [row.code, row.name, row.kinds]), [
    ["2330", "台積電", ["holding", "watch"]],
    ["2603", "長榮", ["holding", "watch"]],
    ["0050", "元大台灣50", ["holding"]],
    ["6669", "緯穎", ["watch"]],
  ]);
});

test("三顆篩選鍵上的數字：全部是不重複的檔數，庫存與自選各算各的", () => {
  assert.deepEqual(loadRead()(HOLDINGS, WATCHLIST).counts, { all: 4, holding: 3, watch: 3 });
});

test("每一列帶著庫存那一邊（橫條、賺賠）與自選那一邊（分類），沒有的那一邊是 null", () => {
  const { rows } = loadRead()(HOLDINGS, WATCHLIST);
  const [tsmc, , etf, wiwynn] = rows;
  assert.equal(tsmc.holding.state, "bull");
  assert.equal(tsmc.holding.item.shares, 1000);
  assert.equal(tsmc.watch.horizon, "long");
  assert.deepEqual([etf.holding.code, etf.watch], ["0050", null]);
  assert.deepEqual([wiwynn.holding, wiwynn.watch.horizon], [null, "none"]);
});

test("上面那一行總數就是庫存總覽的總數（不因為併了自選而改變）", () => {
  const { summary } = loadRead()(HOLDINGS, WATCHLIST);
  assert.deepEqual([summary.count, summary.complete, summary.value, summary.cost, summary.state], [3, true, 3018350, 1585200, "bull"]);
  assert.equal(summary.rows, undefined);   // 每一檔的列在外面那一份 rows，不重複放
});

test("名稱與能不能開個股頁：兩邊都有時用庫存那一筆；只在自選就用自選那一筆", () => {
  const outside = watch("1232", "大統益", "none", { in_universe: false, market: "上市" });
  const { rows } = loadRead()([holding("2330", "台積電", 1, 1, 2)], [outside, watch("2330", "TSMC", "long")]);
  assert.deepEqual(rows.map((row) => [row.code, row.name, row.inUniverse]), [["2330", "台積電", true], ["1232", "大統益", false]]);
});

test("只有庫存、只有自選、兩邊都沒有", () => {
  const read = loadRead();
  assert.deepEqual(read(HOLDINGS, []).counts, { all: 3, holding: 3, watch: 0 });
  const onlyWatch = read([], WATCHLIST);
  assert.deepEqual([onlyWatch.counts, onlyWatch.rows.map((row) => row.code), onlyWatch.summary.count], [{ all: 3, holding: 0, watch: 3 }, ["6669", "2603", "2330"], 0]);
  const empty = read([], []);
  assert.deepEqual([empty.counts, empty.rows], [{ all: 0, holding: 0, watch: 0 }, []]);
});

test("沒有價格的庫存排在有價格的後面、只在自選的前面", () => {
  const { rows } = loadRead()([holding("9999", "沒有價", 10, 10, null), ...HOLDINGS], WATCHLIST);
  assert.deepEqual(rows.map((row) => row.code), ["2330", "2603", "0050", "9999", "6669"]);
  assert.equal(rows[3].holding.state, "none");
});

test("傳進來的不是陣列（還沒讀到）當成沒有", () => {
  assert.deepEqual(loadRead()(undefined, null).counts, { all: 0, holding: 0, watch: 0 });
});
