// 畫線工具票 01（2026-11-14 本人「共識達成」）：水平線畫得出來，跟著圖走。
// 接縫：圖表模組的公開介面——載入 kchart.js＋chart-drawings.js，餵 K 棒、送指標事件，
// 檢查畫布收到的繪圖呼叫（顏色用色票名稱辨認）與對外通知。座標與價位的預期值都是手算的固定數字。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const WEB = new URL("../../web/", import.meta.url);
const DRAW = "--draw";

function load() {
  const strokes = [], texts = [];
  let path = [];
  const context2d = new Proxy({}, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "beginPath") return () => { path = []; };
      if (name === "moveTo" || name === "lineTo") return (x, y) => { path.push([x, y]); };
      if (name === "stroke") return () => { strokes.push({ color: target.strokeStyle, points: path.slice() }); };
      if (name === "fillText") return (text, x, y) => { texts.push({ text: String(text), x, y, color: target.fillStyle }); };
      if (name in target) return target[name];
      return () => {};
    },
    set(target, name, value) { target[name] = value; return true; },
  });
  class FakeElement {
    constructor() {
      this.children = []; this.listeners = new Map(); this.style = {}; this.clientWidth = 390; this.offsetWidth = 150; this.innerHTML = "";
      this.classList = { add() {}, remove() {}, contains: () => false };
    }
    append(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener(type, handler) { this.listeners.set(type, [...(this.listeners.get(type) || []), handler]); }
    dispatch(type, init = {}) {
      const event = { type, pointerId: 1, pointerType: "mouse", clientX: 0, clientY: 0, key: "", deltaY: 0, preventDefault() {}, ...init };
      for (const handler of this.listeners.get(type) || []) handler(event);
    }
    setPointerCapture() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 390, height: 340 }; }
    getContext() { return context2d; }
    contains() { return false; }
  }
  const sandbox = {
    window: { devicePixelRatio: 1 },
    document: { documentElement: new FakeElement(), createElement: () => new FakeElement(), addEventListener() {} },
    ResizeObserver: class { observe() {} },
    getComputedStyle: () => ({ getPropertyValue: name => name }),
    console,
  };
  vm.createContext(sandbox);
  for (const file of ["chart-drawings.js", "kchart.js"]) {
    vm.runInContext(readFileSync(new URL(file, WEB), "utf8"), sandbox, { filename: file });
  }
  return { KChart: sandbox.window.KChart, FakeElement, strokes, texts };
}

// 80 根日 K，日期從 2026-01-01 起每天一根（第 n 根＝1 月 1 日後 n 天）。收盤都是 100，布林通道因此是一條平線。
// 前 40 根高 131.4／低 91.4，後 40 根高 105／低 95：只看後 40 根時價格座標是 94.3～105.7，
// 畫面含前段時是 88.6～134.2。圖高 340、上邊界 10、繪圖區高 308、左邊界 4、繪圖區寬 322。
function dailyCandles() {
  return Array.from({ length: 80 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 0, 1 + index)).toISOString().slice(0, 10);
    return { t: date, label: date, open: 100, close: 100, high: index < 40 ? 131.4 : 105, low: index < 40 ? 91.4 : 95, volume: 1000 };
  });
}

function createChart() {
  const { KChart, FakeElement, strokes, texts } = load();
  const changes = [], tools = [];
  const chart = new KChart(new FakeElement(), {
    macd: false, height: () => 340,
    drawings: { onChange: change => changes.push(JSON.parse(JSON.stringify(change))), onTool: tool => tools.push(tool) },
  });
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  const reset = () => { strokes.length = 0; texts.length = 0; };
  reset();
  const tap = (x, y, pointerType = "mouse") => {
    chart.canvas.dispatch("pointerdown", { clientX: x, clientY: y, pointerType });
    chart.canvas.dispatch("pointerup", { clientX: x, clientY: y, pointerType });
  };
  // 最後一次重畫時，用畫線顏色畫出來的線段與文字。
  const drawnLines = () => { reset(); chart.draw(); return strokes.filter(stroke => stroke.color === DRAW).map(stroke => stroke.points); };
  const drawnLabels = () => { reset(); chart.draw(); return texts.filter(text => text.color === DRAW).map(text => text.text); };
  return { chart, changes, tools, strokes, texts, reset, tap, drawnLines, drawnLabels };
}

const ID_RE = /^[A-Za-z0-9_-]{8,40}$/;

test("選了水平線工具後在圖上點一下：那個價位出現橫跨繪圖區的線與價位標示，工具自動回到一般模式", () => {
  const { chart, changes, tools, tap, drawnLines, drawnLabels } = createChart();
  chart.setTool("hline");
  assert.deepEqual(tools, ["hline"]);

  tap(150, 87); // y=87 → 105.7 − (87−10)/308×11.4 = 102.85；x=150 → 可見的第 19 根＝第 58 根＝2026-02-28
  assert.equal(changes.length, 1);
  const [{ type, drawing }] = changes;
  assert.equal(type, "add");
  assert.match(drawing.id, ID_RE);
  assert.deepEqual(drawing, { id: drawing.id, kind: "hline", anchors: [{ date: "2026-02-28", price: 102.85 }] });
  assert.deepEqual(tools, ["hline", null], "畫完自動回一般模式");

  assert.deepEqual(drawnLines(), [[[4, 87.5], [326, 87.5]]]);
  assert.deepEqual(drawnLabels(), ["102.85"]);
});

const saved = (price, id = "saved-line-01") => ({
  id, kind: "hline", anchors: [{ date: "2026-02-28", price }], text: "",
  created_at: "2026-11-14T06:00:00.000Z", updated_at: "2026-11-14T06:00:00.000Z",
});

test("載入已存的畫線就畫在各自的價位；換一組（換股票）時舊的不留，格式不對的略過", () => {
  const { chart, changes, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([saved(100), saved(102.85, "saved-line-02")]);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]], [[4, 87.5], [326, 87.5]]]); // 100 → 10 + 5.7/11.4×308 = 164
  assert.deepEqual(drawnLabels(), ["100", "102.85"]);
  assert.deepEqual(changes, [], "載入不是使用者的變動，不通知儲存");

  chart.setDrawings([saved(100), null, { id: "x", kind: "circle", anchors: [] }, { id: "bad-price", kind: "hline", anchors: [{ date: "2026-02-28", price: 0 }] },
    { id: "bad-date", kind: "hline", anchors: [{ date: "02/28", price: 100 }] }, { id: "two", kind: "hline", anchors: [{ date: "2026-02-28", price: 100 }, { date: "2026-02-28", price: 101 }] }]);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);

  chart.setDrawings([]);
  assert.deepEqual(drawnLines(), []);
  assert.deepEqual(drawnLabels(), []);
});

test("平移、縮放後線仍在同一個價位；價位落在畫面外時整條與標示都不畫", () => {
  const { chart, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([saved(100), saved(120, "saved-line-02")]);
  // 只看後 40 根（94.3～105.7）：120 在畫面外。
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  assert.deepEqual(drawnLabels(), ["100"]);

  chart.zoom(0.5); // 放大成最後 20 根，價格範圍不變
  assert.equal(chart.view.count, 20);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);

  chart.zoom(2);
  chart.pan(-10); // 畫面含前段（88.6～134.2）：100 → 10 + 34.2/45.6×308 = 241；120 → 10 + 14.2/45.6×308 = 105.9
  assert.deepEqual(drawnLines(), [[[4, 241.5], [326, 241.5]], [[4, 106.5], [326, 106.5]]]);
  assert.deepEqual(drawnLabels(), ["100", "120"]);

  chart.pan(10);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
});

test("切到週 K、月 K 線留在同一個價位；秒 K、分 K 不顯示畫線也不能畫，切回日 K 恢復", () => {
  const { chart, changes, tools, tap, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([saved(100)]);
  const bars = (count, time) => Array.from({ length: count }, (_, index) => ({ t: time(index), label: `第${index + 1}根`, open: 100, close: 100, high: 105, low: 95, volume: 1 }));
  const weekly = bars(16, index => new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10));
  const monthly = bars(16, index => new Date(Date.UTC(2025, index, 28)).toISOString().slice(0, 10));
  const minutes = bars(40, index => `2026-03-20T09:${String(index).padStart(2, "0")}:00+08:00`);

  for (const [candles, unit] of [[weekly, "週"], [monthly, "月"]]) {
    chart.setData(candles, { count: 16, unit });
    assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]], unit);
    assert.deepEqual(drawnLabels(), ["100"], unit);
  }

  // 在月 K 上畫：錨點日期取點到的那一根（每根寬 322/16＝20.125，x=150 → 第 8 根＝2025-08-28）。
  chart.setTool("hline");
  tap(150, 87);
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2025-08-28", price: 102.85 }]]);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]], [[4, 87.5], [326, 87.5]]]);
  changes.length = 0; tools.length = 0;
  chart.setDrawings([saved(100)]);

  chart.setTool("hline");
  for (const unit of ["5秒", "1分", "60分"]) {
    chart.setData(minutes, { count: 40, unit });
    assert.deepEqual(drawnLines(), [], unit);
    assert.deepEqual(drawnLabels(), [], unit);
    assert.deepEqual(tools, ["hline", null], `${unit}：切過去時放下選到一半的工具`);
    chart.setTool("hline");
    tap(150, 87);
    assert.deepEqual(changes, [], `${unit}：不能畫`);
    assert.deepEqual(tools, ["hline", null], `${unit}：工具選不起來`);
    assert.ok(chart.selection >= 0, `${unit}：點一下照舊是選 K 棒`);
  }

  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  chart.setTool("hline");
  tap(150, 87);
  assert.equal(changes.length, 1);
});

test("線畫在布林通道、K 棒、均線之前，價位標示留在繪圖區內：不擋住它們，也不進右側價格軸", () => {
  const { chart, strokes, texts, reset } = createChart();
  chart.setDrawings([saved(100), saved(105.5, "saved-line-02")]); // 105.5 貼近頂端：標示改放線的下方
  reset(); chart.draw();
  const first = color => strokes.findIndex(stroke => stroke.color === color);
  const line = first(DRAW);
  assert.ok(line >= 0);
  for (const color of ["--dim", "--up", "--accent"]) { // 布林上下軌、K 棒、MA18
    assert.ok(first(color) > line, `${color} 要畫在線之後（疊在線上面）`);
  }
  assert.ok(strokes.filter(stroke => stroke.color === DRAW).every(stroke => stroke.points.every(([x]) => x >= 4 && x <= 326)));
  const labels = texts.filter(text => text.color === DRAW);
  assert.deepEqual(labels.map(label => label.text), ["100", "105.5"]);
  for (const label of labels) {
    assert.ok(label.x >= 4 && label.x + label.text.length * 7 <= 326, "標示不進價格軸");
    assert.ok(label.y >= 10 && label.y <= 318, "標示不出繪圖區上下緣");
  }
});

test("沒選工具時圖表原本的行為不變：點一下是選 K 棒、拖曳是平移，圖上有線也一樣", () => {
  const { chart, changes, tools, tap } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164); // 正好點在線上：這張票還沒有選取線，照舊選 K 棒
  assert.equal(chart.selection, 58);
  assert.deepEqual(changes, []);
  assert.deepEqual(tools, []);

  const before = chart.view.end;
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 100 });
  chart.canvas.dispatch("pointermove", { clientX: 230, clientY: 100 });
  chart.canvas.dispatch("pointerup", { clientX: 230, clientY: 100 });
  assert.equal(chart.view.end, before - 10); // 往右拖 80px ÷ 每根 8.05px ≈ 10 根
  assert.deepEqual(changes, []);
});

test("選了工具時：點一下只定位、不選 K 棒；拖曳仍是平移、雙指仍是縮放，都不會畫出線，工具留著", () => {
  const { chart, changes, tools, tap } = createChart();
  chart.setTool("hline");

  const end = chart.view.end;
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 100 });
  chart.canvas.dispatch("pointermove", { clientX: 230, clientY: 100 });
  chart.canvas.dispatch("pointerup", { clientX: 230, clientY: 100 });
  assert.equal(chart.view.end, end - 10);

  const count = chart.view.count;
  chart.canvas.dispatch("pointerdown", { pointerId: 1, pointerType: "touch", clientX: 150, clientY: 100 });
  chart.canvas.dispatch("pointerdown", { pointerId: 2, pointerType: "touch", clientX: 250, clientY: 100 });
  chart.canvas.dispatch("pointermove", { pointerId: 2, pointerType: "touch", clientX: 350, clientY: 100 });
  chart.canvas.dispatch("pointerup", { pointerId: 2, pointerType: "touch", clientX: 350, clientY: 100 });
  chart.canvas.dispatch("pointerup", { pointerId: 1, pointerType: "touch", clientX: 150, clientY: 100 });
  assert.ok(chart.view.count < count, "雙指拉開仍是放大");
  assert.deepEqual(changes, []);
  assert.deepEqual(tools, ["hline"]);

  tap(350, 100); // 右側價格軸
  tap(150, 330); // 下方時間軸
  assert.deepEqual(changes, [], "點在繪圖區外不定位");
  assert.deepEqual(tools, ["hline"], "工具留著，可以再點一次");
  assert.equal(chart.selection, -1, "選了工具時不選 K 棒");

  tap(150, 164, "touch"); // 手指點一下也一樣定位
  assert.equal(changes.length, 1);
  assert.equal(changes[0].drawing.anchors[0].price, 100);
  assert.equal(chart.selection, -1);
  assert.deepEqual(tools, ["hline", null]);
});

test("即時更新重畫、五年歷史接到圖前面時，線留在原本的價位，選到一半的工具也不會掉", () => {
  const { chart, changes, tools, tap, drawnLines } = createChart();
  chart.setTool("hline");
  tap(150, 164);
  const id = changes[0].drawing.id;
  chart.setTool("hline");

  const candles = dailyCandles();
  const live = [...candles.slice(0, -1), { ...candles.at(-1), close: 101, high: 105, low: 95 }];
  chart.setData(live, { count: 40, unit: "日" }); // 個股頁每次即時更新都整組重設資料
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  chart.update(live);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);

  const older = Array.from({ length: 30 }, (_, index) => {
    const date = new Date(Date.UTC(2025, 11, 2 + index)).toISOString().slice(0, 10);
    return { t: date, label: date, open: 100, close: 100, high: 105, low: 95, volume: 1 };
  });
  chart.setData([...older, ...live], { count: 40, unit: "日" });
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  assert.deepEqual(tools, ["hline", null, "hline"], "重畫不會放下工具");

  tap(150, 87);
  assert.equal(changes.length, 2);
  assert.notEqual(changes[1].drawing.id, id, "每條線各有自己的識別碼");
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]], [[4, 87.5], [326, 87.5]]]);
});

test("沒有開啟畫線的圖（首頁大盤）不受影響：設工具、載入畫線都不做事，也不出錯", () => {
  const { KChart, FakeElement, strokes } = load();
  const chart = new KChart(new FakeElement(), { macd: false, height: () => 340 });
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  chart.setTool("hline");
  chart.setDrawings([saved(100)]);
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 87 });
  chart.canvas.dispatch("pointerup", { clientX: 150, clientY: 87 });
  assert.equal(chart.selection, 58);
  strokes.length = 0; chart.draw();
  assert.deepEqual(strokes.filter(stroke => stroke.color === DRAW), []);
});
