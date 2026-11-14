// 布林通道 20（本人 2026-11-14：「在所有k線圖加上布林通道20」）。
// 首頁大盤與個股頁的 K 線都由 web/kchart.js 的 KChart 畫，所以在這一層驗一次就涵蓋所有週期。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/kchart.js", import.meta.url);

function load() {
  const calls = [];
  const context2d = new Proxy({}, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name in target) return target[name];
      return (...args) => { calls.push([name, ...args]); };
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  class FakeElement {
    constructor() {
      this.children = []; this.style = {}; this.clientWidth = 390; this.offsetWidth = 150; this.innerHTML = "";
      this.classList = { add() {}, remove() {}, contains: () => false };
    }
    append(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener() {}
    setPointerCapture() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 390, height: 340 }; }
    getContext() { return context2d; }
    contains() { return false; }
  }
  const sandbox = {
    window: { devicePixelRatio: 1 },
    document: { documentElement: new FakeElement(), createElement: () => new FakeElement(), addEventListener() {} },
    ResizeObserver: class { observe() {} },
    getComputedStyle: () => ({ getPropertyValue: () => "#666" }),
    console, Number, Math, Map, Object, Array, String, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "kchart.js" });
  return { KChart: sandbox.window.KChart, math: sandbox.window.KChartMath, tags: sandbox.window.KChartTags, FakeElement, calls };
}

function candles(closes) {
  return closes.map((close, index) => ({
    t: `d${index}`, label: `第${index + 1}根`, open: close, high: close + 1, low: close - 1, close, amount: 1, volume: 1,
  }));
}

function chartWith(closes, count) {
  const { KChart, FakeElement, calls } = load();
  const chart = new KChart(new FakeElement(), { macd: false, height: () => 340 });
  chart.setData(candles(closes), { count: count ?? Math.min(60, closes.length) });
  return { chart, calls };
}

test("布林通道 20：中線是 20 期均價，上下各 2 個母體標準差，不足 20 期不畫", () => {
  const { math } = load();
  const closes = Array.from({ length: 25 }, (_, index) => index + 1);
  const bands = math.bollinger(closes);
  assert.equal(bands.length, 25);
  for (let index = 0; index < 19; index++) assert.equal(bands[index], null, `第 ${index + 1} 根`);
  const sd = Math.sqrt((20 * 20 - 1) / 12); // 1..20 的母體標準差
  assert.ok(Math.abs(bands[19].mid - 10.5) < 1e-9);
  assert.ok(Math.abs(bands[19].upper - (10.5 + 2 * sd)) < 1e-9);
  assert.ok(Math.abs(bands[19].lower - (10.5 - 2 * sd)) < 1e-9);
  assert.ok(Math.abs(bands[24].mid - 15.5) < 1e-9, "視窗往後滑動");

  const flat = math.bollinger(Array(30).fill(50));
  assert.deepEqual({ ...flat[29] }, { mid: 50, upper: 50, lower: 50 });
});

test("布林通道遇到缺值就重新累積 20 期，不拿缺值前的資料湊數", () => {
  const { math } = load();
  const closes = Array.from({ length: 45 }, (_, index) => (index === 22 ? null : 100 + index));
  const bands = math.bollinger(closes);
  assert.ok(bands[21]);
  for (let index = 22; index < 42; index++) assert.equal(bands[index], null, `第 ${index + 1} 根`);
  assert.ok(bands[42]);
  assert.ok(Math.abs(bands[42].mid - (123 + 142) / 2) < 1e-9);
});

test("K 線圖一律算出並畫出布林通道；不足 20 根時照常畫 K 線、不出錯", () => {
  const { chart, calls } = chartWith(Array.from({ length: 80 }, (_, index) => 100 + Math.sin(index / 3) * 8));
  assert.equal(chart.boll.length, 80);
  assert.equal(chart.boll[18], null);
  assert.ok(chart.boll[79].upper > chart.boll[79].mid && chart.boll[79].mid > chart.boll[79].lower);
  assert.ok(calls.some(call => call[0] === "fill"), "通道要有底色帶");

  const short = chartWith(Array.from({ length: 12 }, (_, index) => 100 + index));
  assert.ok(short.chart.boll.every(value => value === null));
  assert.ok(!short.calls.some(call => call[0] === "fillText" && call[1] === "此區間OHLC資料不足"));
});

test("通道比 K 棒寬時，價格座標要把通道算進去，不能畫到圖外", () => {
  const closes = Array.from({ length: 60 }, (_, index) => (index % 2 ? 120 : 100));
  const { calls } = chartWith(closes, 30);
  const ys = calls.filter(call => call[0] === "moveTo" || call[0] === "lineTo").map(call => call[2]);
  assert.ok(ys.length > 0);
  assert.ok(ys.every(Number.isFinite));
  assert.ok(Math.min(...ys) >= 0, `最上方 y=${Math.min(...ys)}`);
  assert.ok(Math.max(...ys) <= 340, `最下方 y=${Math.max(...ys)}`);
});

// 選取那一天的數值標在右側（本人 2026-11-15）：不再有浮窗，所以浮窗裡的布林、均線數字也不再顯示。
function drawnTexts(calls) {
  return calls.filter(call => call[0] === "fillText").map(call => String(call[1]));
}

test("選取某一根時，右側標出那一天的開高低收，時間軸標出日期，不再有浮窗", () => {
  const { chart, calls } = chartWith(Array.from({ length: 80 }, (_, index) => 100 + Math.sin(index / 3) * 8));
  assert.equal(chart.tip, undefined, "浮窗的元素不再建立");
  calls.length = 0;
  chart.draw();
  const plain = drawnTexts(calls);
  assert.ok(!plain.includes("開") && !plain.includes("第71根"), "沒有選取時不標");

  chart.hover = 70;
  calls.length = 0;
  chart.draw();
  const texts = drawnTexts(calls), candle = chart.candles[70];
  const price = value => value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  for (const [name, value] of [["開", candle.open], ["高", candle.high], ["低", candle.low], ["收", candle.close]]) {
    assert.ok(texts.includes(name), `右側要有「${name}」`);
    assert.ok(texts.includes(price(value)), `「${name}」的數值 ${price(value)}`);
  }
  assert.ok(texts.includes("第71根"), "時間軸標出選取那一天的日期");
  assert.deepEqual(JSON.parse(JSON.stringify(chart.debugBoxes.picked)),
    { date: "第71根", tags: ["開", "高", "低", "收"].map(name => ({ name, text: price(candle[{ 開: "open", 高: "high", 低: "low", 收: "close" }[name]]) })) });
});

test("數值籤擠在一起時上下排開：順序照數值高低、彼此不重疊、不超出上下界", () => {
  const { tags } = load();
  const placed = tags.place([{ name: "收", y: 100 }, { name: "高", y: 96 }, { name: "低", y: 104 }, { name: "開", y: 101 }], 9, 300, 20);
  assert.deepEqual(placed.map(item => item.name), ["高", "收", "開", "低"]);
  placed.slice(1).forEach((item, index) => assert.ok(item.slot - placed[index].slot >= 20));
  const low = tags.place([{ name: "a", y: 295 }, { name: "b", y: 299 }], 9, 300, 20);
  assert.deepEqual(low.map(item => item.slot), [280, 300], "貼到下界時往上推");
  assert.deepEqual(tags.place([{ name: "x", y: NaN }, { name: "y", y: 50 }], 9, 300, 20).map(item => item.name), ["y"], "沒有數值的不排");
});

test("欄位太窄時：開高低收的短名照寫（三枚同底色，少了短名分不出來）；指標籤可以整組只留數值", () => {
  const { tags } = load();
  const { chart, calls } = chartWith([100, 101, 102]);
  const x = chart.canvas.getContext("2d");
  const names = () => calls.filter(call => call[0] === "fillText").map(call => String(call[1]));
  const items = () => [{ name: "DIF", text: "1,234.5", slot: 20, bg: "#000", fg: "#fff" }, { name: "柱", text: "+1.0", slot: 40, bg: "#000", fg: "#fff" }];

  calls.length = 0;
  tags.draw(x, 0, 40, 18, items(), true);
  assert.ok(!names().includes("DIF") && !names().includes("柱"), "指標籤：有一枚放不下，整組都不寫短名");
  assert.ok(names().includes("1,234.5") && names().includes("+1.0"), "數值照寫");

  calls.length = 0;
  tags.draw(x, 0, 40, 18, items());
  assert.ok(names().includes("DIF") && names().includes("柱"), "沒有說可以省的（開高低收），短名照寫");
});

// 秒 K（本人 2026-11-14：秒級先做當天）：橫軸要標到秒，不能像日 K 一樣標年月。
test("秒 K 的時間刻度是 時:分:秒", () => {
  const { KChart, FakeElement, calls } = load();
  const chart = new KChart(new FakeElement(), { macd: false, height: () => 340 });
  const rows = Array.from({ length: 30 }, (_, index) => {
    const second = String(index * 5 % 60).padStart(2, "0"), minute = String(Math.floor(index * 5 / 60)).padStart(2, "0");
    return { t: `2026-11-10T09:${minute}:${second}+08:00`, label: `09:${minute}:${second}`, open: 100, high: 101, low: 99, close: 100, volume: 1000 };
  });
  calls.length = 0;
  chart.setData(rows, { count: 30, unit: "5秒" });
  const texts = calls.filter(([name]) => name === "fillText").map(([, text]) => String(text));
  assert.ok(texts.includes("09:00:00"), texts.join(" "));
  assert.ok(!texts.some(text => /^26\/11$/.test(text)), texts.join(" "));
});

// 本人 2026-11-15：「圖表上的均線雖然有均線的顏色區分但我不知道哪個顏色是哪個指標」。
test("圖的左上角寫出每條均線與布林通道的名稱；改了均線期數，名稱跟著換", () => {
  const { chart, calls } = chartWith(Array.from({ length: 80 }, (_, index) => 100 + Math.sin(index / 3) * 8));
  calls.length = 0;
  chart.draw();
  let texts = drawnTexts(calls);
  for (const name of ["MA18", "MA45", "布林20"]) assert.ok(texts.includes(name), `要寫出 ${name}`);

  calls.length = 0;
  chart.setMaPeriods([5, 20, 60]);
  texts = drawnTexts(calls);
  for (const name of ["MA5", "MA20", "MA60"]) assert.ok(texts.includes(name), `要寫出 ${name}`);
  assert.ok(!texts.includes("MA18") && !texts.includes("MA45"), "拿掉的均線不再寫");
});
