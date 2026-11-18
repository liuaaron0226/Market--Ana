// 本人 11-18「幫我加入漲停跟跌停的數字反白（紅綠）」：個股頁的現價碰到漲停價時紅底白字、跌停價時綠底白字。
// 漲跌停價＝前一天收盤 ±10%，照證交所的跳動單位：漲停往下取、跌停往上取（不超過 10%）；ETF 的跳動單位不同。
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

const { limitPrices, limitState } = loadIntraday();

test("股票：前一天收盤 ±10% 照跳動單位取到界內（手算）", () => {
  // 1,000 元以上跳 5 元：2,235 × 1.1 = 2,458.5 → 2,455；× 0.9 = 2,011.5 → 2,015
  assert.deepEqual({ ...limitPrices(2235) }, { up: 2455, down: 2015 });
  // 100～500 跳 0.5：131.6 × 1.1 = 144.76 → 144.5；× 0.9 = 118.44 → 118.5
  assert.deepEqual({ ...limitPrices(131.6) }, { up: 144.5, down: 118.5 });
  // 10～50 跳 0.05：23.45 × 1.1 = 25.795 → 25.75；× 0.9 = 21.105 → 21.15
  assert.deepEqual({ ...limitPrices(23.45) }, { up: 25.75, down: 21.15 });
  // 跨級距：漲停價落在下一級（500 以上跳 1）：460 × 1.1 = 506 → 506；× 0.9 = 414 → 414
  assert.deepEqual({ ...limitPrices(460) }, { up: 506, down: 414 });
});

test("ETF：50 元以下跳 0.01、50 元以上跳 0.05（和股票不同）", () => {
  // 0050 前收 63.35：× 1.1 = 69.685 → 69.65；× 0.9 = 57.015 → 57.05
  assert.deepEqual({ ...limitPrices(63.35, { etf: true }) }, { up: 69.65, down: 57.05 });
  // 00878 前收 21.37：× 1.1 = 23.507 → 23.5；× 0.9 = 19.233 → 19.24
  assert.deepEqual({ ...limitPrices(21.37, { etf: true }) }, { up: 23.5, down: 19.24 });
});

test("現價等於漲停價是 up、等於跌停價是 down，其他（含資料不全）都是空字串", () => {
  assert.equal(limitState(2455, 2235), "up");
  assert.equal(limitState(2015, 2235), "down");
  assert.equal(limitState(2450, 2235), "");
  assert.equal(limitState(69.65, 63.35, { etf: true }), "up");
  assert.equal(limitState(2455, null), "");
  assert.equal(limitState(undefined, 2235), "");
});
