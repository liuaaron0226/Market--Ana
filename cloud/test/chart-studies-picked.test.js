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
// 切到某一種指標，回傳標出來的數值；同時確認每一個數值真的畫到了畫布上。
function pickedAfter(studies, drawn, kind) {
  drawn.length = 0;
  studies.select(kind);
  const tags = picked(studies);
  for (const tag of tags) assert.ok(drawn.includes(tag.text), `${kind}：畫布上要有數值 ${tag.text}`);
  return tags;
}
const one = value => value.toLocaleString("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: 1 });

test("選取某一天：MACD 標 DIF、DEA、柱；KD 標 K、D；RSI 與成交金額各標一枚，數值就是那一天的", () => {
  const { studies, chart, drawn } = studiesWith({ volumeKind: "amount", studies: ["volume", "macd", "kd", "rsi"] }, 60);

  assert.deepEqual(pickedAfter(studies, drawn, "volume"), [{ name: "", text: "3,600" }]);

  const point = chart.macd[60];
  assert.deepEqual(pickedAfter(studies, drawn, "macd"), [
    { name: "DIF", text: one(point.dif) }, { name: "DEA", text: one(point.dea) },
    { name: "柱", text: `${point.hist < 0 ? "−" : "+"}${one(Math.abs(point.hist))}` },
  ]);
  for (const name of ["DIF", "DEA", "柱"]) assert.ok(drawn.includes(name), `欄位夠寬時要寫出短名 ${name}`);

  assert.deepEqual(pickedAfter(studies, drawn, "kd"), [{ name: "K", text: one(studies.kd[60].k) }, { name: "D", text: one(studies.kd[60].d) }]);
  assert.deepEqual(pickedAfter(studies, drawn, "rsi"), [{ name: "", text: one(studies.rsi[60].rsi) }]);
});

test("個股頁：成交量以張為單位，外資、投信、自營商標買賣超張數（帶正負號）", () => {
  const { studies, drawn } = studiesWith({ volumeKind: "volume", studies: ["volume", "foreign", "trust", "dealer"] }, 60);
  assert.deepEqual(pickedAfter(studies, drawn, "volume"), [{ name: "", text: "1,060" }]);
  assert.deepEqual(pickedAfter(studies, drawn, "foreign"), [{ name: "", text: "+2,345" }]);
  assert.deepEqual(pickedAfter(studies, drawn, "trust"), [{ name: "", text: "−678" }]);
  assert.deepEqual(pickedAfter(studies, drawn, "dealer"), [{ name: "", text: "0" }]);
});

test("沒有選取、或指標還在暖機沒有數值時，不標", () => {
  const none = studiesWith({ volumeKind: "amount", studies: ["volume", "macd", "kd", "rsi"] }, -1);
  none.studies.select("macd");
  assert.deepEqual(picked(none.studies), []);

  const warming = studiesWith({ volumeKind: "amount", studies: ["volume", "macd", "kd", "rsi"] }, 25);
  warming.studies.select("macd");
  assert.deepEqual(picked(warming.studies), [], "MACD 要 34 根才有值，第 26 根不標");
});

// 基金當成一般個股 03 票：基金只有淨值，指標選單藏掉要量（成交量）、高低價（KD）或法人的指標，只留 MACD、RSI；換回股票時全部回來。
test("只開放部分指標（基金）：目前看的指標不在名單就換成名單第一個，名單外的選不到；放寬後恢復", () => {
  const { studies } = studiesWith({ volumeKind: "volume", studies: ["volume", "macd", "kd", "rsi", "foreign", "trust", "dealer"] }, 70);
  assert.equal(studies.active, "volume");
  studies.setAvailable(["macd", "rsi"]);
  assert.equal(studies.active, "macd");
  for (const kind of ["volume", "kd", "foreign"]) {
    studies.select(kind);
    assert.equal(studies.active, "macd", kind);
  }
  studies.select("rsi");
  assert.equal(studies.active, "rsi");
  studies.setAvailable(null);
  studies.select("foreign");
  assert.equal(studies.active, "foreign");
});
