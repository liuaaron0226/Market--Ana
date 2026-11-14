// 選取那一天的指標數值標在指標區右側（本人 2026-11-15：「下方的技術指標的數值也是顯示在右側y軸的刻度即可，選哪一天就顯示哪一天」）。
// 首頁與個股頁的指標區都由 web/chart-studies.js 的 ChartStudies 畫，在這一層驗每一種指標標出哪些數值。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const read = name => readFileSync(new URL(`../../web/${name}`, import.meta.url), "utf8");

function load() {
  const drawn = [];
  const context2d = new Proxy({}, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "fillText") return text => { drawn.push(String(text)); };
      if (name in target) return target[name];
      return () => {};
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  class FakeElement {
    constructor() { this.style = {}; this.dataset = {}; this.clientWidth = 390; }
    append() {}
    setAttribute() {}
    addEventListener() {}
    querySelectorAll() { return []; }
    getBoundingClientRect() { return { left: 0, top: 0, width: 390, height: 116 }; }
    getContext() { return context2d; }
  }
  const sandbox = {
    window: { devicePixelRatio: 1 },
    document: { documentElement: new FakeElement(), createElement: () => new FakeElement(), createTextNode: text => text, addEventListener() {} },
    ResizeObserver: class { observe() {} },
    getComputedStyle: () => ({ getPropertyValue: () => "#666" }),
    console, Number, Math, Map, Object, Array, String, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(read("kchart.js"), sandbox, { filename: "kchart.js" });
  vm.runInContext(read("chart-studies.js"), sandbox, { filename: "chart-studies.js" });
  return { window: sandbox.window, FakeElement, drawn };
}

// 80 根有漲有跌的日線；外資、投信、自營商給固定的買賣超（股）。
function studiesWith(options, hover) {
  const { window, FakeElement, drawn } = load();
  const closes = Array.from({ length: 80 }, (_, index) => 100 + Math.sin(index / 3) * 8 + index * 0.1);
  const candles = closes.map((close, index) => ({
    t: `d${index}`, label: `第${index + 1}根`, open: close - 0.5, high: close + 1, low: close - 1, close,
    amount: 3000 + index * 10, volume: (1000 + index) * 1000, foreign: 2_345_000, trust: -678_000, dealer: 0,
  }));
  const chart = {
    candles, unit: "日", hover, selection: hover, view: { end: 80, count: 60 }, geo: { l: 4, pw: 300, step: 5, start: 20 },
    macd: window.KChartMath.macd(closes), canvas: new FakeElement(),
  };
  const studies = new window.ChartStudies(chart, new FakeElement(), options);
  return { studies, chart, drawn };
}

const picked = studies => JSON.parse(JSON.stringify(studies.debugPicked));
const one = value => value.toLocaleString("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: 1 });

test("選取某一天：MACD 標 DIF、DEA、柱；KD 標 K、D；RSI 與成交金額各標一枚，數值就是那一天的", () => {
  const { studies, chart, drawn } = studiesWith({ volumeKind: "amount", studies: ["volume", "macd", "kd", "rsi"] }, 60);

  studies.select("volume");
  assert.deepEqual(picked(studies), [{ name: "", text: "3,600" }]);

  drawn.length = 0;
  studies.select("macd");
  const point = chart.macd[60];
  assert.deepEqual(picked(studies), [
    { name: "DIF", text: one(point.dif) }, { name: "DEA", text: one(point.dea) },
    { name: "柱", text: `${point.hist < 0 ? "−" : "+"}${one(Math.abs(point.hist))}` },
  ]);
  for (const tag of picked(studies)) assert.ok(drawn.includes(tag.text), `畫布上要有 ${tag.name} 的數值 ${tag.text}`);

  studies.select("kd");
  assert.deepEqual(picked(studies), [{ name: "K", text: one(studies.kd[60].k) }, { name: "D", text: one(studies.kd[60].d) }]);

  studies.select("rsi");
  assert.deepEqual(picked(studies), [{ name: "", text: one(studies.rsi[60].rsi) }]);
});

test("個股頁：成交量以張為單位，外資、投信、自營商標買賣超張數（帶正負號）", () => {
  const { studies } = studiesWith({ volumeKind: "volume", studies: ["volume", "foreign", "trust", "dealer"] }, 60);
  studies.select("volume");
  assert.deepEqual(picked(studies), [{ name: "", text: "1,060" }]);
  studies.select("foreign");
  assert.deepEqual(picked(studies), [{ name: "", text: "+2,345" }]);
  studies.select("trust");
  assert.deepEqual(picked(studies), [{ name: "", text: "−678" }]);
  studies.select("dealer");
  assert.deepEqual(picked(studies), [{ name: "", text: "0" }]);
});

test("沒有選取、或指標還在暖機沒有數值時，不標", () => {
  const none = studiesWith({ volumeKind: "amount", studies: ["volume", "macd", "kd", "rsi"] }, -1);
  none.studies.select("macd");
  assert.deepEqual(picked(none.studies), []);

  const warming = studiesWith({ volumeKind: "amount", studies: ["volume", "macd", "kd", "rsi"] }, 25);
  warming.studies.select("macd");
  assert.deepEqual(picked(warming.studies), [], "MACD 要 34 根才有值，第 26 根不標");
});
