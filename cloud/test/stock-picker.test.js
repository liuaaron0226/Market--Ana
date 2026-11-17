// 本人 11-17「搜尋股票只能透過代號很不方便」：全站股票輸入框共用一個選股下拉（web/stock-picker.js），代號、名稱都能打。
// 這裡只驗兩個純函式：打的字裡有沒有代號（有就照舊直接用），以及 /api/stocks/search 的回應變成哪些選項。畫面在瀏覽器驗。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const window = {};
vm.runInNewContext(readFileSync(new URL("../../web/stock-picker.js", import.meta.url), "utf8"), { window });
const { codeOf, options } = window.StockPicker;
const plain = (value) => JSON.parse(JSON.stringify(value));

test("打代號（或選了之後框裡是「代號 名稱」）就直接拿代號，大小寫不拘；打名稱、基金代碼不是股票代號", () => {
  assert.equal(codeOf("2330"), "2330");
  assert.equal(codeOf(" 2330 台積電 "), "2330");
  assert.equal(codeOf("00981a"), "00981A");
  for (const text of ["台積電", "積電", "A10", "BLKTENAU", "2330台積電", "23", ""]) assert.equal(codeOf(text), null, text);
});

const PAYLOAD = {
  items: [
    { code: "2330", name: "台積電", market: "上市", security_type: "普通股", universe_rank: 1 },
    { code: "4441", name: "振大環球", market: "上櫃", security_type: "普通股", universe_rank: null },
  ],
  funds: [{ code: "BLKTENAU", name: "貝萊德世界科技 A10", full_name: "貝萊德世界科技基金 A10美元(總報酬穩定配息)" }],
};

test("股票在前、基金在後；基金點了去基金頁", () => {
  assert.deepEqual(plain(options(PAYLOAD, { funds: true })), [
    { kind: "stock", code: "2330", name: "台積電", title: "2330 台積電", meta: "上市普通股" },
    { kind: "stock", code: "4441", name: "振大環球", title: "4441 振大環球", meta: "上櫃普通股" },
    { kind: "fund", code: "BLKTENAU", name: "貝萊德世界科技 A10", title: "貝萊德世界科技 A10", meta: "基金 · 和 0050 比", href: "fund.html?code=BLKTENAU" },
  ]);
});

test("不收基金的框（加自選、加入比較）只留股票；accept 篩掉不能用的股票", () => {
  const picked = options(PAYLOAD, { accept: (item) => item.universe_rank !== null });
  assert.deepEqual(plain(picked).map((option) => option.code), ["2330"]);
});

test("回應壞掉或空的：沒有選項", () => {
  assert.deepEqual(plain(options(null, { funds: true })), []);
  assert.deepEqual(plain(options({ items: "x" }, { funds: true })), []);
});
