// K 線浮雕落影（立體重設計 C 包第 1 片；規格 .scratch/3d-redesign/spec.md 第 12、13、18 條）。
// 只改畫法：每根蠟燭的本體底下多一塊往右下偏的影子；蠟燭本身的位置、寬度、顏色一律不變。
// 拖曳、縮放進行中不畫影子（手機上一直放大、一直拖也不掉幀），放開之後補畫。
// 首頁大盤與個股頁、所有週期的 K 線都由 web/kchart.js 的 KChart 畫，所以在這一層驗。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/kchart.js", import.meta.url);

// 每個樣式變數回傳自己的名字，畫布上用了哪個顏色一看就知道。shadow 是 --candle-shadow 的值（空字串＝主題沒有給）。
function load({ shadow = "rgba(0,0,0,.2)", rim = "" } = {}) {
  const rects = [];
  let pass = 0;
  const state = { fillStyle: "", globalAlpha: 1 };
  const context2d = new Proxy(state, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "fillRect") return (left, top, width, height) => rects.push({ pass, fill: target.fillStyle, alpha: target.globalAlpha, left, top, width, height });
      if (name === "clearRect") return () => { pass += 1; };
      if (name in target) return target[name];
      return () => {};
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  const listeners = new Map();
  class FakeElement {
    constructor() {
      this.children = []; this.style = {}; this.clientWidth = 390; this.offsetWidth = 150; this.innerHTML = "";
      this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} };
    }
    append(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener(type, handler) { if (!listeners.has(this)) listeners.set(this, {}); listeners.get(this)[type] = handler; }
    setPointerCapture() {}
    releasePointerCapture() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 390, height: 340 }; }
    getContext() { return context2d; }
    contains() { return false; }
    dispatch(type, init) { listeners.get(this)?.[type]?.({ pointerId: 1, pointerType: "mouse", button: 0, preventDefault() {}, ...init }); }
  }
  const sandbox = {
    window: { devicePixelRatio: 1 },
    document: { documentElement: new FakeElement(), createElement: () => new FakeElement(), addEventListener() {} },
    ResizeObserver: class { observe() {} },
    getComputedStyle: () => ({ getPropertyValue: name => (name === "--candle-shadow" ? shadow : name === "--candle-rim" ? rim : name) }),
    setTimeout: () => 0, clearTimeout() {},
    console, Number, Math, Map, Object, Array, String, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "kchart.js" });
  return { KChart: sandbox.window.KChart, FakeElement, rects, lastPass: () => pass };
}

function candles(count) {
  return Array.from({ length: count }, (_, index) => {
    const open = 100 + (index % 7), close = open + (index % 2 ? 3 : -2);
    return { t: `d${index}`, label: `第${index + 1}根`, open, close, high: Math.max(open, close) + 1, low: Math.min(open, close) - 1, amount: 1, volume: 1 };
  });
}

function drawn(options) {
  const view = load(options);
  const chart = new view.KChart(new view.FakeElement(), { macd: false, height: () => 340 });
  chart.setData(candles(40), { count: 40 });
  const latest = view.lastPass();
  const inPass = view.rects.filter(rect => rect.pass === latest);
  return {
    ...view, chart,
    bodies: inPass.filter(rect => rect.fill === "--up" || rect.fill === "--down"),
    shadows: inPass.filter(rect => rect.fill === (options?.shadow ?? "rgba(0,0,0,.2)") && rect.fill !== ""),
  };
}

test("每根蠟燭的本體底下都有一塊同樣大小、往右下偏一點的影子，而且先畫影子再畫蠟燭", () => {
  const { bodies, shadows, rects } = drawn();
  assert.equal(bodies.length, 40, "40 根蠟燭");
  assert.equal(shadows.length, bodies.length, "一根一塊影子");
  bodies.forEach((body, index) => {
    const shadow = shadows[index];
    assert.equal(shadow.width, body.width, `第 ${index + 1} 根：影子和本體一樣寬`);
    assert.equal(shadow.height, body.height, `第 ${index + 1} 根：一樣高`);
    assert.ok(shadow.left > body.left && shadow.left - body.left <= 2, `第 ${index + 1} 根：往右偏一點`);
    assert.ok(shadow.top > body.top && shadow.top - body.top <= 3, `第 ${index + 1} 根：往下偏一點`);
    assert.ok(rects.indexOf(shadow) < rects.indexOf(body), `第 ${index + 1} 根：影子在蠟燭底下`);
  });
});

test("加了影子之後蠟燭本身的位置、寬度、高度、顏色和沒有影子時逐根相同", () => {
  const plain = drawn({ shadow: "" });
  const relief = drawn();
  assert.equal(plain.shadows.length, 0, "主題沒有給影子的顏色就不畫影子");
  assert.deepEqual(relief.bodies.map(({ pass, ...rect }) => rect), plain.bodies.map(({ pass, ...rect }) => rect));
});

test("拖曳或雙指縮放進行中不畫影子，放開之後補畫", () => {
  const view = drawn();
  const canvas = view.chart.canvas;
  canvas.dispatch("pointerdown", { clientX: 200, clientY: 150 });
  canvas.dispatch("pointermove", { clientX: 140, clientY: 150 });
  const during = view.rects.filter(rect => rect.pass === view.lastPass());
  assert.ok(during.some(rect => rect.fill === "--up" || rect.fill === "--down"), "拖曳中蠟燭照畫");
  assert.equal(during.filter(rect => rect.fill === "rgba(0,0,0,.2)").length, 0, "拖曳中沒有影子");

  canvas.dispatch("pointerup", { clientX: 140, clientY: 150 });
  const after = view.rects.filter(rect => rect.pass === view.lastPass());
  assert.ok(after.filter(rect => rect.fill === "rgba(0,0,0,.2)").length > 0, "放開之後影子補回來");
});

test("深色主題給了亮邊顏色時：每根本體的上緣外面多一條 1px 的亮邊，不蓋到本體；淺色（沒給）就沒有", () => {
  const view = drawn({ rim: "rgba(255,255,255,.2)" });
  const latest = view.lastPass();
  const rims = view.rects.filter(rect => rect.pass === latest && rect.fill === "rgba(255,255,255,.2)");
  assert.equal(rims.length, view.bodies.length, "一根一條");
  view.bodies.forEach((body, index) => {
    const rim = rims[index];
    assert.equal(rim.left, body.left, `第 ${index + 1} 根：和本體同一個左緣`);
    assert.equal(rim.width, body.width, `第 ${index + 1} 根：一樣寬`);
    assert.equal(rim.height, 1, `第 ${index + 1} 根：1px`);
    assert.equal(rim.top, body.top - 1, `第 ${index + 1} 根：貼在本體上緣外面`);
  });
  assert.deepEqual(view.bodies.map(({ pass, ...rect }) => rect), drawn().bodies.map(({ pass, ...rect }) => rect), "本體和沒有亮邊時相同");
  assert.equal(drawn().rects.filter(rect => rect.fill === "rgba(255,255,255,.2)").length, 0, "沒給亮邊顏色就沒有");
});
