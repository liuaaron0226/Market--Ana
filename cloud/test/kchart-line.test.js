// 基金當成一般個股 03 票（本人 2026-11-18 選樣品 B）：K 線元件的「線」模式——資料只有收盤（基金淨值）時畫一條線，
// 不畫 K 棒；均線、十字線、畫線照常；十字線選到某一天只標那一天的淨值。5 年長日線只有日期與收盤時也接得上。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/kchart.js", import.meta.url);

// 記下每一筆筆畫（strokeStyle、線寬、經過幾個點）與每一塊填色，顏色用樣式變數的名字代替。
function load() {
  const strokes = [];
  const rects = [];
  let path = [];
  const state = { fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1 };
  const context2d = new Proxy(state, {
    get(target, name) {
      if (name === "measureText") return (text) => ({ width: String(text).length * 7 });
      if (name === "beginPath") return () => { path = []; };
      if (name === "moveTo" || name === "lineTo") return (x, y) => path.push([x, y]);
      if (name === "stroke") return () => strokes.push({ color: target.strokeStyle, width: target.lineWidth, points: path.length });
      if (name === "fillRect") return (left, top, width, height) => rects.push({ fill: target.fillStyle, left, top, width, height });
      if (name === "clearRect") return () => { strokes.length = 0; rects.length = 0; };
      if (name in target) return target[name];
      return () => {};
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  class FakeElement {
    constructor() {
      this.children = []; this.style = {}; this.clientWidth = 390; this.innerHTML = "";
      this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
    }
    append(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 390, height: 340 }; }
    getContext() { return context2d; }
    contains() { return false; }
  }
  const sandbox = {
    window: { devicePixelRatio: 1 },
    document: { documentElement: new FakeElement(), createElement: () => new FakeElement(), addEventListener() {} },
    ResizeObserver: class { observe() {} },
    getComputedStyle: () => ({ getPropertyValue: (name) => name }),
    setTimeout: () => 0, clearTimeout() {},
    console, Number, Math, Map, Object, Array, String, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "kchart.js" });
  return { KChart: sandbox.window.KChart, KChartMath: sandbox.window.KChartMath, FakeElement, strokes, rects };
}

// 只有日期與收盤（淨值）的資料，和個股頁餵給圖的形狀一樣（t、label、close）。
const navs = (count) => Array.from({ length: count }, (_, index) => ({ t: `2026-${String(index).padStart(3, "0")}`, label: `第${index + 1}天`,
  close: 17 + Math.sin(index / 5) }));

function lineChart(count = 60) {
  const view = load();
  const chart = new view.KChart(new view.FakeElement(), { macd: false, height: () => 340, maPeriods: [20, 60], overlayLabels: "tags" });
  chart.setData(navs(count), { count, mode: "line", closeLabel: "淨值" });
  return { ...view, chart };
}

test("線模式：淨值畫成一條經過每一天的墨色線，不畫 K 棒", () => {
  const { strokes, rects } = lineChart(60);
  const line = strokes.filter((stroke) => stroke.color === "--ink" && stroke.points === 60);
  assert.equal(line.length, 1, "一條 60 個點的淨值線");
  assert.equal(rects.filter((rect) => rect.fill === "--up" || rect.fill === "--down").length, 0, "沒有 K 棒本體");
});

test("線模式：均線照常用收盤算出來畫（MA20 從第 20 天起有值）", () => {
  const { strokes } = lineChart(60);
  assert.ok(strokes.some((stroke) => stroke.color === "--accent" && stroke.points === 41), "MA20 從第 20 天到第 60 天");
});

test("線模式：十字線選到一天，右側只標那一天的淨值（沒有開高低）", () => {
  const { chart } = lineChart(60);
  chart.hover = 59; chart.draw();
  assert.deepEqual(JSON.parse(JSON.stringify(chart.debugBoxes.picked)),
    { date: "第60天", tags: [{ name: "淨值", text: chart.candles[59].close.toLocaleString("en-US", { maximumFractionDigits: 2 }) }] });
});

test("回到一般模式（換回股票）時照常畫 K 棒", () => {
  const { chart, rects } = lineChart(30);
  const candles = navs(30).map((row) => ({ ...row, open: row.close - 0.1, high: row.close + 0.2, low: row.close - 0.2 }));
  chart.setData(candles, { count: 30 });
  assert.ok(rects.filter((rect) => rect.fill === "--up" || rect.fill === "--down").length >= 30);
});

test("長日線只有日期與收盤（基金淨值）時也接得上，接出來的每一天只有收盤", () => {
  const { KChartMath } = load();
  const bars = [{ date: "2026-11-13", close: 17.73 }, { date: "2026-11-16", close: 17.14 }];
  const history = { schema: "stock-history/v1", code: "BLKTENAU", as_of: "2026-11-16",
    d: [20221201, 20221202, 20261113, 20261116], c: [10.52, 10.31, 17.73, 17.14] };
  assert.deepEqual(JSON.parse(JSON.stringify(KChartMath.olderBars(bars, history))),
    [{ date: "2022-12-01", close: 10.52 }, { date: "2022-12-02", close: 10.31 }]);
  assert.deepEqual([...KChartMath.olderBars(bars, { ...history, c: [10.52, 10.31, 18, 17.14] })], [], "銜接處對不上就不接");
});
