// 本人 11-17「搜尋股票只能透過代號很不方便」：全站股票輸入框共用一個選股下拉（web/stock-picker.js），代號、名稱都能打。
// 這裡只驗兩個純函式：打的字裡有沒有代號（有就照舊直接用），以及 /api/stocks/search 的回應變成哪些選項。畫面在瀏覽器驗。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const window = {};
vm.runInNewContext(readFileSync(new URL("../../web/stock-picker.js", import.meta.url), "utf8"), { window });
const { codeOf, options, decide, fitQuery } = window.StockPicker;
const plain = (value) => JSON.parse(JSON.stringify(value));

test("打代號（或選了之後框裡是「代號 名稱」）就直接拿代號，大小寫不拘；打名稱、基金代碼不是股票代號", () => {
  assert.equal(codeOf("2330"), "2330");
  assert.equal(codeOf(" 2330 台積電 "), "2330");
  assert.equal(codeOf("00981a"), "00981A");
  for (const text of ["台積電", "積電", "A10", "BLKTENAU", "2330台積電", "23", ""]) assert.equal(codeOf(text), null, text);
});

// 基金當成一般個股 03 票：基金（貝萊德世界科技 A10）是 items 裡的一般一筆（類別「基金」、市場「境外基金」），
// 每個框都收（加自選、加入比較也可以）；名稱搜尋分支的 funds 欄退場，回應裡就算還有也不讀。
const PAYLOAD = {
  items: [
    { code: "2330", name: "台積電", market: "上市", security_type: "普通股", universe_rank: 1 },
    { code: "4441", name: "振大環球", market: "上櫃", security_type: "普通股", universe_rank: null },
    { code: "BLKTENAU", name: "貝萊德世界科技 A10", market: "境外基金", security_type: "基金", universe_rank: null },
  ],
  funds: [{ code: "OLDFUND", name: "舊的 funds 欄" }],
};

test("搜尋結果照順序變成選項；基金是一般的一筆，標「基金」", () => {
  assert.deepEqual(plain(options(PAYLOAD)), [
    { kind: "stock", code: "2330", name: "台積電", title: "2330 台積電", meta: "上市普通股" },
    { kind: "stock", code: "4441", name: "振大環球", title: "4441 振大環球", meta: "上櫃普通股" },
    { kind: "stock", code: "BLKTENAU", name: "貝萊德世界科技 A10", title: "BLKTENAU 貝萊德世界科技 A10", meta: "基金", fund: true },
  ]);
});

test("accept 篩掉這個框不能用的那幾筆", () => {
  const picked = options(PAYLOAD, { accept: (item) => item.universe_rank !== null });
  assert.deepEqual(plain(picked).map((option) => option.code), ["2330"]);
});

test("回應壞掉或空的：沒有選項", () => {
  assert.deepEqual(plain(options(null)), []);
  assert.deepEqual(plain(options({ items: "x" })), []);
});

const choice = (code, name) => ({ kind: "stock", code, name, title: `${code} ${name}`, meta: "上市普通股" });
const 長榮 = choice("2603", "長榮"), 長榮航 = choice("2618", "長榮航"), 長榮鋼 = choice("2211", "長榮鋼");

test("按查詢／加入沒選下拉：名稱完全相同的那檔優先，否則取第一個；找不到說查無", () => {
  assert.equal(decide("長榮", [長榮航, 長榮]).option.code, "2603");
  assert.equal(decide("榮航", [長榮航]).option.code, "2618");
  assert.equal(decide("長榮", [長榮航, 長榮鋼]).option.code, "2618");
  assert.deepEqual(plain(decide("查無XYZ", [])), { message: "查無「查無XYZ」，請改打代號或從清單選擇。" });
});

test("會存資料的框（strict，加自選、新增庫存）：好幾檔符合又沒有完全相同的名稱，要本人從下拉挑，不默默存第一個", () => {
  assert.deepEqual(plain(decide("長榮", [長榮航, 長榮鋼], { strict: true })), { choose: true, message: "有好幾檔符合「長榮」，請從下面選一檔。" });
  assert.equal(decide("長榮", [長榮航, 長榮], { strict: true }).option.code, "2603");
  assert.equal(decide("榮航", [長榮航], { strict: true }).option.code, "2618");
});

test("搜尋字太長（API 上限 48 個 UTF-8 位元組，約 16 個中文字）就截短再查，不讓整個搜尋失敗", () => {
  assert.equal(fitQuery("台積電"), "台積電");
  const full = "貝萊德世界科技基金 A10美元(總報酬穩定配息)";
  const fitted = fitQuery(full);
  assert.ok(new TextEncoder().encode(fitted).length <= 48, fitted);
  assert.ok(full.startsWith(fitted) && fitted.length >= 14, fitted);
});

test("全站代號規則（基金當成一般個股 01 票）：4～12 碼大寫英數都是合法代號，給網址與加入比較用", () => {
  const { isCode } = window.StockPicker;
  for (const code of ["2330", "00981A", "BLKTENAU", "ABCDEFGHIJKL"]) assert.equal(isCode(code), true, code);
  for (const code of ["ABC", "ABCDEFGHIJKLM", "blktenau", "BLK-TENAU", ""]) assert.equal(isCode(code), false, code);
});

test("打的字剛好是某一檔的代碼（例如基金 BLKTENAU，不分大小寫）就用那一檔", () => {
  const fund = { kind: "stock", code: "BLKTENAU", name: "貝萊德世界科技 A10", title: "BLKTENAU 貝萊德世界科技 A10", meta: "境外基金基金" };
  assert.equal(decide("blktenau", [長榮航, fund], { strict: true }).option.code, "BLKTENAU");
});

test("照集保寫法打基金代碼（AGIF-FVM，站上代碼 AGIFFVM）也算完全相同", () => {
  const allianz = { kind: "stock", code: "AGIFFVM", name: "安聯收益成長 AM 穩定月收", title: "AGIFFVM 安聯收益成長 AM 穩定月收", meta: "基金", fund: true };
  for (const term of ["AGIF-FVM", "agif-fvm", "AGIFFVM"]) {
    assert.equal(decide(term, [長榮航, allianz], { strict: true }).option?.code, "AGIFFVM", term);
  }
});

