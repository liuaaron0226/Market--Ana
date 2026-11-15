// 個股頁重新整理（本人 2026-11-15：太長、一進去全部攤開、沒辦法選現在要看什麼；看樣品後答「照這樣做」）：
// 報價與 K 線以外的收進三顆分類鍵；「對照 0050」的五個期間變成一排鍵。
// 這裡驗兩件事：頁面的結構（哪一塊在哪一類）、五個期間整理出來的資料。畫面上的操作由旅程檢查驗。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, ROOT), "utf8");

// 和 stock-ui2b.test.js 同一種載法：給最少的假頁面，只拿 window.StockComparison
function loadComparison() {
  const element = () => ({
    textContent: "", hidden: false, value: "", dataset: {}, style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, setAttribute() {}, removeAttribute() {}, append() {}, replaceChildren() {},
    contains: () => false, querySelectorAll: () => [], scrollIntoView() {},
  });
  const elements = new Map();
  const document = {
    documentElement: { dataset: {} }, title: "",
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element, createElementNS: element, querySelectorAll: () => [], addEventListener() {},
  };
  const window = {
    Intraday: { createPoller: () => ({ start() {}, stop() {} }), projectAnalysis: (value) => value },
    KChartMath: { normalizePeriods: (values) => values },
    addEventListener() {},
  };
  const context = {
    window, document, location: { search: "", protocol: "file:", href: "file:///stock.html" },
    history: { pushState() {} }, localStorage: { getItem: () => null, setItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => "" }), matchMedia: () => ({ matches: false }),
    fetch: async () => ({}), innerWidth: 1440, URL, URLSearchParams, AbortController, console,
    setTimeout, clearTimeout, requestAnimationFrame: () => 0,
  };
  vm.runInNewContext(read("web/stock.js"), context);
  return (items, benchmark) => JSON.parse(JSON.stringify(window.StockComparison.periods(items, benchmark)));
}

const BENCHMARK = { code: "0050", dividends_included: true };
// 樣本資料 2330（資料日 2026-11-09）的五個期間
const TSMC = {
  code: "2330", name: "台積電", status: "ok", dividends_included: true,
  periods: [
    { period: "1m", label: "近 1 個月", status: "ok", stock_return_pct: -12.4, benchmark_return_pct: -15.2, excess_pct: 2.8, stock_max_drawdown_pct: -19.5, benchmark_max_drawdown_pct: -16.3 },
    { period: "3m", label: "近 3 個月", status: "ok", stock_return_pct: -5.4, benchmark_return_pct: -5.3, excess_pct: -0.1, stock_max_drawdown_pct: -20.6, benchmark_max_drawdown_pct: -17.4 },
    { period: "1y", label: "近 1 年", status: "ok", stock_return_pct: 55.0, benchmark_return_pct: 58.9, excess_pct: -3.9, stock_max_drawdown_pct: -20.6, benchmark_max_drawdown_pct: -17.4 },
    { period: "3y", label: "近 3 年", status: "ok", stock_return_pct: 320.4, benchmark_return_pct: 228.1, excess_pct: 92.3, stock_max_drawdown_pct: -27.5, benchmark_max_drawdown_pct: -30.5 },
    { period: "5y", label: "近 5 年", status: "ok", stock_return_pct: 299.6, benchmark_return_pct: 219.4, excess_pct: 80.2, stock_max_drawdown_pct: -33.9, benchmark_max_drawdown_pct: -44.8 },
  ],
};

test("五個期間照固定的順序各一顆鍵：領先 0050 的記號在上、落後的在下，寫差多少", () => {
  const [stock] = loadComparison()([TSMC], BENCHMARK);
  assert.deepEqual([stock.code, stock.name, stock.note], ["2330", "台積電", ""]);
  assert.deepEqual(stock.periods.map((period) => [period.key, period.label, period.short, period.kind, period.gap]), [
    ["1m", "近 1 個月", "近1月", "bull", "+2.8%"],
    ["3m", "近 3 個月", "近3月", "bear", "-0.1%"],
    ["1y", "近 1 年", "近1年", "bear", "-3.9%"],
    ["3y", "近 3 年", "近3年", "bull", "+92.3%"],
    ["5y", "近 5 年", "近5年", "bull", "+80.2%"],
  ]);
});

test("每個期間帶著點開要看的四個數字：個股報酬、0050 報酬、兩邊的最大回撤", () => {
  const [stock] = loadComparison()([TSMC], BENCHMARK);
  assert.deepEqual(stock.periods[2].facts, [
    ["個股報酬", "+55.0%"], ["0050 報酬", "+58.9%"], ["個股最大回撤", "-20.6%"], ["0050 最大回撤", "-17.4%"],
  ]);
  assert.deepEqual(stock.periods[3].facts[0], ["個股報酬", "+320.4%"]);
});

test("差距剛好是 0：沒有方向，照樣寫 0.0%", () => {
  const flat = { ...TSMC, periods: [{ ...TSMC.periods[2], excess_pct: 0 }] };
  const [stock] = loadComparison()([flat], BENCHMARK);
  assert.deepEqual([stock.periods[2].kind, stock.periods[2].gap], ["none", "0.0%"]);
});

test("某個期間沒有資料或歷史不夠：那一顆寫資料不足、沒有方向、四個數字都是一槓，名稱照樣是中文", () => {
  const short = { ...TSMC, periods: [TSMC.periods[0], { period: "3y", label: "近 3 年", status: "insufficient_history" }] };
  const [stock] = loadComparison()([short], BENCHMARK);
  assert.deepEqual(stock.periods.map((period) => [period.key, period.short, period.kind, period.gap]), [
    ["1m", "近1月", "bull", "+2.8%"], ["3m", "近3月", "none", "資料不足"], ["1y", "近1年", "none", "資料不足"],
    ["3y", "近3年", "none", "資料不足"], ["5y", "近5年", "none", "資料不足"],
  ]);
  assert.equal(stock.periods[1].label, "近 3 個月");   // 資料裡沒有這個期間時，不能把代號（3m）當名稱寫出來
  assert.deepEqual(stock.periods[3].facts.map(([, text]) => text), ["—", "—", "—", "—"]);
});

test("配息資料不完整：不宣告領先落後，那一顆寫不可比；有完整配息的那一邊照樣給數字", () => {
  const etf = { ...TSMC, code: "00919", name: "群益台灣精選高息", dividends_included: false };
  const [stock] = loadComparison()([etf], BENCHMARK);
  assert.equal(stock.note, "配息未完整");
  assert.ok(stock.periods.every((period) => period.kind === "none" && period.gap === "不可比"));
  assert.deepEqual(stock.periods[2].facts, [
    ["個股報酬", "—"], ["0050 報酬", "+58.9%"], ["個股最大回撤", "—"], ["0050 最大回撤", "-17.4%"],
  ]);
  const [unknown] = loadComparison()([TSMC], { code: "0050" });
  assert.equal(unknown.note, "配息狀態未知");
  assert.deepEqual(unknown.periods[2].facts.map(([, text]) => text), ["+55.0%", "—", "-20.6%", "—"]);
});

test("加了別的比較股票：每一檔各一份，順序照傳進來的", () => {
  const stocks = loadComparison()([TSMC, { ...TSMC, code: "2317", name: "鴻海" }], BENCHMARK);
  assert.deepEqual(stocks.map((stock) => stock.code), ["2330", "2317"]);
  assert.ok(stocks.every((stock) => stock.periods.length === 5));
});

// ---- 頁面的結構 ----
const html = read("web/stock.html").replace(/<!--[\s\S]*?-->/g, "");
const between = (startMark, endMark) => html.slice(html.indexOf(startMark), html.indexOf(endMark, html.indexOf(startMark)));

test("K 線下面一排三顆分類鍵：價位、籌碼、對照 0050，一開始都收著", () => {
  const bar = between('id="stockCats"', "</div>");
  const keys = [...bar.matchAll(/<button type="button" aria-expanded="(\w+)" aria-controls="(\w+)">([^<]+)<\/button>/g)].map((m) => [m[1], m[2], m[3]]);
  assert.deepEqual(keys, [["false", "levelsPanel", "價位"], ["false", "chipsPanel", "籌碼"], ["false", "comparePanel", "對照 0050"]]);
  assert.match(html, /class="hm-cats" id="stockCats"/);
  assert.ok(html.indexOf('id="stockCats"') > html.indexOf('id="chartStudies"'));
  assert.doesNotMatch(html, /data-cat-panel="[^"]+"[^>]*data-open/);
});

test("三類各自裝的東西：價位階梯、籌碼名單與大戶持股、和 0050 對照", () => {
  const levels = between('id="levelsPanel"', 'id="chipsPanel"');
  const chips = between('id="chipsPanel"', 'id="comparePanel"');
  const compare = html.slice(html.indexOf('id="comparePanel"'));
  assert.match(levels, /id="ladder"/);
  assert.match(chips, /id="candidateReasons"[\s\S]*id="bigHolders"/);
  assert.match(compare, /id="compareBlock"/);
  for (const id of ["levelsPanel", "chipsPanel", "comparePanel"]) assert.match(html, new RegExp(`id="${id}" data-cat-panel=`));
  // K 線那一塊裡不再有價位階梯（電腦上也收進「價位」）
  assert.doesNotMatch(between('class="bd-stage"', 'id="stockCats"'), /bd-rail|id="ladder"/);
});

test("對照 0050 裡面的順序：一句結論、報酬走勢圖、期間的鍵、加入比較；完整的表留給螢幕閱讀器", () => {
  const compare = html.slice(html.indexOf('id="compareBlock"'));
  const order = ['id="compareConclusion"', 'id="compareChart"', 'id="comparePeriods"', 'id="compareForm"'].map((mark) => compare.indexOf(mark));
  assert.ok(order.every((at) => at > 0) && order.every((at, index) => index === 0 || at > order[index - 1]), String(order));
  assert.match(compare, /<div class="bd-compare-table-wrap sr-only">\s*<table class="bd-compare-table" id="compareTable">/);
});

test("分類鍵的開合與首頁共用同一支程式，個股頁有載入它", () => {
  assert.match(html, /<script src="home-fold\.js\?v=/);
  assert.match(read("web/home-fold.js"), /document\.querySelector\('\.hm-cats'\)/);
  assert.match(read("web/minimal.html"), /class="hm-cats" id="homeCats"/);
});
