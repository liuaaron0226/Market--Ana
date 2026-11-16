// K 線右側價格欄只放簡單的數字（本人 2026-11-17：「右側就只需要顯示簡單的數字就好了不需要再顯示支撐壓力，
// 因為下方的價位其實就有了」）。個股頁不再畫支撐、壓力；首頁的收復線、防守線線條留著（圖例鍵寫著名稱與數字），
// 右側不再掛它們的價籤。刻度數字、收盤價籤、事前預測與自選股關注的判斷線照舊。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/kchart.js", import.meta.url);

// 記下畫布上寫了哪些字（含位置），以及畫了哪些水平線（含顏色）。
function load(width = 1000) {
  const texts = [], lines = [];
  let path = [];
  const state = { fillStyle: "", strokeStyle: "", globalAlpha: 1, lineWidth: 1 };
  const context2d = new Proxy(state, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "fillText") return (text, x, y) => texts.push({ text: String(text), x, y, fill: target.fillStyle });
      if (name === "clearRect") return () => { texts.length = 0; lines.length = 0; };
      if (name === "beginPath") return () => { path = []; };
      if (name === "moveTo" || name === "lineTo") return (x, y) => path.push([x, y]);
      if (name === "stroke") return () => {
        if (path.length === 2 && Math.abs(path[0][1] - path[1][1]) < .01) lines.push({ y: path[0][1], x0: path[0][0], x1: path[1][0], stroke: target.strokeStyle });
      };
      if (name in target) return target[name];
      return () => {};
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  class FakeElement {
    constructor() { this.children = []; this.style = {}; this.clientWidth = width; this.innerHTML = ""; }
    append(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener() {}
    getContext() { return context2d; }
    contains() { return false; }
  }
  const sandbox = {
    window: { devicePixelRatio: 1 },
    document: { documentElement: new FakeElement(), createElement: () => new FakeElement(), addEventListener() {} },
    ResizeObserver: class { observe() {} },
    getComputedStyle: () => ({ getPropertyValue: name => (name === "--candle-shadow" || name === "--candle-rim" ? "" : name) }),
    setTimeout: () => 0, clearTimeout() {},
    console, Number, Math, Map, Object, Array, String, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "kchart.js" });
  return { KChart: sandbox.window.KChart, FakeElement, texts, lines, sandbox };
}

// 收盤在 105 附近、高低在 98～112 之間的 40 根。
function candles(count = 40) {
  return Array.from({ length: count }, (_, index) => {
    const open = 100 + (index % 7), close = open + (index % 2 ? 3 : -2);
    return { t: `d${index}`, label: `第${index + 1}根`, open, close, high: Math.max(open, close) + 1, low: Math.min(open, close) - 1, amount: 1, volume: 1 };
  });
}

function draw(overlays, width = 1000) {
  const view = load(width);
  const chart = new view.KChart(new view.FakeElement(), { macd: false, height: () => 340, overlayLabels: "tags" });
  chart.setData(candles(), { count: 40, overlays });
  const railLeft = chart.geo.l + chart.geo.pw;
  return { ...view, chart, railLeft, rail: view.texts.filter(item => item.x >= railLeft) };
}

test("不掛價籤的判斷線：圖上照畫整條線，右側價格欄沒有它的短名與數字", () => {
  const { rail, lines, railLeft, sandbox, chart } = draw([{ value: 108.5, label: "收復線", short: "收復", color: "gold", tag: false }]);
  assert.ok(lines.some(line => line.stroke === "gold" && line.x0 === chart.geo.l && line.x1 === railLeft), "整條收復線還在（從圖的左緣畫到價格欄）");
  assert.deepEqual(rail.filter(item => item.text === "收復" || item.text === "108.5").map(item => item.text), [], "右側沒有收復的價籤");
  assert.ok(rail.some(item => item.text === "收盤"), "收盤價籤照舊");
  assert.deepEqual(Array.from(sandbox.window.__kchartLabelBoxes), [], "驗收用的價籤框也沒有它");
});

test("不掛價籤的判斷線不再讓刻度數字讓位：同一高度的刻度照寫", () => {
  // 刻度 2.5 一格，110 會是一個刻度；判斷線就壓在 110 上。
  const tagged = draw([{ value: 110, label: "收復線", short: "收復", color: "gold" }]);
  const untagged = draw([{ value: 110, label: "收復線", short: "收復", color: "gold", tag: false }]);
  // 刻度文字寫在價格欄左緣往右 8px；價籤的數字靠右對齊，不在這個位置。
  const tick = view => view.rail.some(item => item.text === "110" && item.x === view.railLeft + 8);
  assert.ok(!tick(tagged), "掛價籤時刻度讓位（原本的樣子）");
  assert.ok(tick(untagged), "不掛價籤時刻度 110 照寫");
});

test("不掛價籤的判斷線不會把右側欄撐寬（欄寬只看要寫的價籤與收盤）", () => {
  const plain = draw([]);
  const untagged = draw([{ value: 10_500, label: "收復線", short: "收復", color: "gold", tag: false, inView: false }]);
  assert.equal(untagged.chart.geo.pw, plain.chart.geo.pw);
});

test("其他判斷線（預測、自選股關注）照舊掛價籤，和不掛的混在一起也一樣", () => {
  const { rail } = draw([
    { value: 108.5, label: "收復線", short: "收復", color: "gold", tag: false },
    { value: 99.5, label: "轉弱線 前低", short: "前低", color: "red" },
  ]);
  assert.ok(rail.some(item => item.text === "前低"), "預測線的短名在");
  assert.ok(rail.some(item => item.text === "99.5"), "預測線的數字在");
  assert.ok(!rail.some(item => item.text === "收復"), "收復沒有價籤");
});

test("個股頁：K 線不再疊支撐、壓力（價位改由圖下方的價位階梯呈現）", () => {
  const source = readFileSync(new URL("../../web/stock.js", import.meta.url), "utf8");
  const drawChart = source.slice(source.indexOf("function drawChart("), source.indexOf("function showMarket("));
  assert.ok(drawChart.length > 100, "找得到 drawChart");
  assert.doesNotMatch(drawChart, /lv\.resistance|lv\.support|short: '壓力'|short: '支撐'/);
  assert.doesNotMatch(drawChart, /含支撐與壓力參考線/, "圖的說明不再提支撐壓力");
  assert.match(drawChart, /WatchJudgments\.overlays\(chartPeriod\)/, "自選股關注的觸發線、看錯線照舊");
});

test("首頁：收復線、防守線只畫線不掛右側價籤；預測的兩條線照舊", () => {
  const source = readFileSync(new URL("../../web/minimal.js", import.meta.url), "utf8");
  const block = source.slice(source.indexOf("function overlays() {"), source.indexOf("function chartOverlays() {"));
  assert.ok(block.length > 50 && block.length < 1500, "找得到首頁的判斷線清單");
  for (const name of ["收復", "防守"]) {
    const entry = block.split("\n").find(line => line.includes(`short: '${name}'`));
    assert.ok(entry, `找得到${name}線`);
    assert.match(entry, /tag: false/, `${name}線不掛價籤`);
  }
  const forecast = readFileSync(new URL("../../web/forecast.js", import.meta.url), "utf8");
  const forecastLines = forecast.slice(forecast.indexOf("function overlays(period) {"), forecast.indexOf("function segmented("));
  assert.match(forecastLines, /轉強線/, "找得到預測的兩條線");
  assert.doesNotMatch(forecastLines, /tag:/, "預測線照舊掛價籤");
});
