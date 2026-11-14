import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);
const read = path => readFileSync(new URL(path, ROOT), "utf8");

function loadStockWindow() {
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
    Intraday: { createPoller: () => ({ start() {}, stop() {} }), projectAnalysis: value => value },
    KChartMath: { normalizePeriods: values => [...new Set(values.map(Number).filter(value => value >= 2).map(value => Math.min(240, Math.round(value))))].slice(0, 4) },
    addEventListener() {},
  };
  const context = {
    window, document, location: { search: "", protocol: "file:", href: "file:///stock.html" },
    history: { pushState() {} }, localStorage: { getItem: () => null, setItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => "" }), matchMedia: () => ({ matches: false }),
    fetch: async () => ({}), innerWidth: 1440, URL, URLSearchParams, AbortController, console,
  };
  vm.runInNewContext(read("web/stock.js"), context);
  return window;
}

test("個股頁週期有週K與當日60分K，均線預設18／45並可增減", () => {
  const html = read("web/stock.html");
  assert.match(html, /<option value="60m">60分K<\/option>/);
  assert.match(html, /<option value="weekly">週K<\/option>/);
  assert.match(html, /id="maControls"/);
  assert.match(html, /id="addMa"/);

  const source = read("web/kchart.js");
  const window = {};
  vm.runInNewContext(source, { window, getComputedStyle: () => ({ getPropertyValue: () => "" }), console });
  assert.deepEqual([...window.KChartMath.normalizePeriods([18, 45, 45, 1, 241])], [18, 45, 240]);
});

test("核心版讓策略與水位 UI 預設隱藏，前端只依 server features 開啟", () => {
  const stockHtml = read("web/stock.html").replace(/<!--[\s\S]*?-->/g, "");
  const portfolioHtml = read("web/portfolio.html").replace(/<!--[\s\S]*?-->/g, "");
  const minimalHtml = read("web/minimal.html").replace(/<!--[\s\S]*?-->/g, "");
  assert.match(stockHtml, /<section[^>]+id="strategy"[^>]+hidden/);
  assert.match(portfolioHtml, /<section[^>]+id="levelPanel"[^>]+hidden/);
  assert.match(minimalHtml, /<section[^>]+id="marketTrend"[^>]+hidden/);
  assert.match(minimalHtml, /<section[^>]+id="aiDigestCard"[^>]+hidden/);

  const stockSource = read("web/stock.js");
  const portfolioSource = read("web/portfolio.js");
  const minimalSource = read("web/minimal.js");
  for (const source of [stockSource, portfolioSource]) assert.match(source, /\/api\/features/);
  assert.match(stockSource, /features\.strategy_v1/);
  assert.match(portfolioSource, /features\.level_advice/);
  assert.match(portfolioSource, /features\.ai_stock_advice/);
  assert.match(minimalSource, /features\.market_trend_v1/);
  assert.match(minimalSource, /features\?\.ai_digest/);
});

test("完整模式保留策略結論與收合細節，0050對照位於最底", () => {
  const html = read("web/stock.html").replace(/<!--[\s\S]*?-->/g, "");
  assert.match(html, /id="strategyConclusion"/);
  assert.match(html, /<details[^>]+id="strategyDetails"/);
  assert.ok(html.indexOf('id="compareBlock"') > html.indexOf('id="chartStudies"'));
  assert.ok(html.indexOf('id="compareBlock"') > html.indexOf('id="candidateReasons"'));
  for (const removed of ["左右滑動切換", "價位怎麼來的", "支撐、壓力只描述", "非買賣建議", "訊號明細", "技術指標數值", "方法說明", "資料範圍與注意事項"]) {
    assert.ok(!html.includes(removed), removed);
  }
});

test("策略理由只在結論出現一次，展開後不再重複同一句（本人 11-14：讓版面說明，不加重複描述）", () => {
  const html = read("web/stock.html");
  const js = read("web/stock.js");
  assert.doesNotMatch(html, /id="strategyReason"/);
  assert.equal([...js.matchAll(/strategy\.reason/g)].length, 1);
});

test("首頁人寫的劇本與直白分析比資料日舊時整組隱藏，不留「已過期」字樣配新數字（本人 11-14）", () => {
  const source = read("web/minimal.js");
  assert.match(source, /function overlays\(\) \{\s+if \(scenarioExpired\) return \[\];/);
  assert.match(source, /\$\('readCard'\)\.hidden = plainStale;/);
  assert.match(source, /plainSource\.data_dates\.price_and_foreign \|\| ''\) < A\.meta\.cutoff_date/);
  const hiddenGroup = source.slice(source.indexOf("for (const node of [els.scenarioDate.parentElement"));
  for (const target of ["$('conclusionTrigger')", "els.lineUpper", "els.lineLower", "$('distribution')"]) {
    assert.ok(hiddenGroup.slice(0, 400).includes(target), target);
  }
  assert.match(hiddenGroup.slice(0, 400), /node\.hidden = scenarioExpired/);
  for (const removed of ["' 已過期'", "舊劇本", "未冒充當日分析"]) {
    assert.ok(!source.includes(removed), removed);
  }
  assert.doesNotMatch(source, /Math\.min\(\.\.\.visible\.map\(candle => candle\.low\), LOWER, UPPER\)/);
});

test("首頁即時 consumer 以 source time／live／retryable 判斷，未知成交金額不轉成 0", () => {
  const source = read("web/minimal.js");
  assert.match(source, /payload\.live === true/);
  assert.match(source, /payload\.market_open \|\| payload\.retryable/);
  assert.match(source, /response\.status === 429/);
  assert.match(source, /features\.market_trend_v1/);
  assert.doesNotMatch(source, /Number\(data\.turnover_billion\)/);
});

test("首頁與個股沒有區間表 DOM 時，副圖更新不拋錯", () => {
  const window = {};
  vm.runInNewContext(read("web/chart-studies.js"), { window, document: {}, getComputedStyle: () => ({ getPropertyValue: () => "" }) });
  const update = window.ChartStudies.prototype._updateTable;
  assert.doesNotThrow(() => update.call({ chart: { unit: "日" } }), "個股日K沒有 tableDetails");
  assert.doesNotThrow(() => update.call({ chart: { unit: "週" } }), "首頁非日K沒有 tableDetails");
});

test("副圖提供外資、投信、自營商個別買賣超，不提供無來源的主力序列", () => {
  const studies = read("web/chart-studies.js");
  assert.match(studies, /'foreign'/);
  assert.match(studies, /'trust'/);
  assert.match(studies, /'dealer'/);
  assert.doesNotMatch(studies, /['"]main_force['"]/);
  assert.doesNotMatch(studies, /左右滑動切換/);
});

test("個股頁在分析畫出來之後才背景載入 5 年歷史，接不上就維持原樣、不加任何提示文字（本人 11-14）", () => {
  const js = read("web/stock.js");
  const html = read("web/stock.html");
  assert.match(js, /render\(data, true\);\s*loadHistory\(data, sequence\);/);
  assert.match(js, /fetch\(`\/api\/stocks\/\$\{encodeURIComponent\(result\.code\)\}\/history`/);
  assert.match(js, /window\.KChartMath\.olderBars\(result\.bars, await response\.json\(\)\)/);
  assert.match(js, /olderBars = \{ code: '', firstDate: '', rows: \[\] \};\s*stopTicks\(\);\s*if \(pendingRequest\)/);  // 換股票就丟掉上一檔的歷史、停掉上一檔的逐筆
  assert.doesNotMatch(html, /5 ?年|歷史日線|載入更多/);  // 圖本來就能拖曳、縮放、切換日週月，不需要新的按鈕或說明
});

test("個股頁週期多出 1／5／10／15／30 秒，只在選了秒 K 時才向站台要當天逐筆（本人 11-14：秒級先做當天）", () => {
  const html = read("web/stock.html");
  const js = read("web/stock.js");
  for (const value of ["1s", "5s", "10s", "15s", "30s"]) assert.match(html, new RegExp(`<option value="${value}">${parseInt(value, 10)}秒</option>`));
  assert.doesNotMatch(html, /<optgroup label="[^"]+"/);  // 分組只靠間隔，不加小標題
  assert.match(js, /const wanted = Boolean\(SECOND_PERIODS\[chartPeriod\]\) && lastResult && canUseLive\(lastResult\) && !document\.hidden;/);
  assert.match(js, /fetch\(`\/api\/stocks\/\$\{encodeURIComponent\(code\)\}\/ticks\?offset=\$\{tickFeed\.offset\}`/);
  assert.match(js, /window\.Intraday\.secondCandles\(tickFeed\.code === r\.code \? tickFeed\.ticks : \[\], seconds\)/);
  assert.match(js, /if \(response\.status === 403 \|\| response\.status === 404\) return;/);  // 沒有權限就不再問
});

test("MA設定使用try catch保護localStorage", () => {
  const stock = read("web/stock.js");
  assert.match(stock, /localStorage\.getItem\(['"]stock-ma-periods['"]\)/);
  assert.match(stock, /localStorage\.setItem\(['"]stock-ma-periods['"]/);
  assert.match(stock, /try\s*\{[\s\S]*stock-ma-periods[\s\S]*\}\s*catch/);
});

test("盤中瀏覽器策略與 Python 共用完整／部分／有限 fixture 保持一致", () => {
  const fixture = JSON.parse(read("cloud/test/fixtures/strategy-v1-parity.json"));
  const strategy = loadStockWindow().StockStrategy;
  for (const item of fixture.cases) {
    const actual = strategy.build({ ...item.analysis, conditions: fixture.conditions });
    for (const [key, expected] of Object.entries(item.expected)) {
      assert.deepEqual(JSON.parse(JSON.stringify(actual[key])), expected, `${item.name}/${key}`);
    }
  }
});

test("0050 比較只在雙方配息完整時宣告領先落後", () => {
  const comparison = loadStockWindow().StockComparison;
  const complete = {
    benchmark: { code: "0050", dividends_included: true },
    items: [{
      code: "2330", name: "台積電", status: "ok", dividends_included: true,
      periods: [{ period: "1y", status: "ok", excess_pct: 12.3 }],
    }],
  };
  assert.match(comparison.conclusion(complete.items, complete.benchmark), /領先|落後|贏過|持平/);

  const frozen00919 = {
    benchmark: { code: "0050", dividends_included: true },
    items: [{
      code: "00919", name: "群益台灣精選高息", status: "ok", dividends_included: false,
      periods: [{ period: "1y", status: "ok", stock_return_pct: 8.1, benchmark_return_pct: 12.4, excess_pct: -4.3 }],
    }],
  };
  const incomplete = comparison.conclusion(frozen00919.items, frozen00919.benchmark);
  assert.match(incomplete, /配息未完整/);
  assert.match(incomplete, /不可比較/);
  assert.doesNotMatch(incomplete, /領先|落後|贏過|百分點/);
  assert.deepEqual(JSON.parse(JSON.stringify(comparison.basis(frozen00919.items[0], frozen00919.benchmark))), {
    comparable: false, label: "配息未完整",
  });

  const incompleteBenchmark = comparison.conclusion(complete.items, { code: "0050", dividends_included: false });
  assert.match(incompleteBenchmark, /配息未完整/);
  assert.match(incompleteBenchmark, /不可比較/);
  assert.doesNotMatch(incompleteBenchmark, /領先|落後|贏過|百分點/);

  const unknownBenchmark = comparison.conclusion(complete.items, { code: "0050" });
  assert.match(unknownBenchmark, /配息狀態未知/);
  assert.match(unknownBenchmark, /不可比較/);
  assert.doesNotMatch(unknownBenchmark, /領先|落後|贏過|百分點/);
});
