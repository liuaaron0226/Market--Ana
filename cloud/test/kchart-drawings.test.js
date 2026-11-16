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
  const strokes = [], texts = [], fills = [];
  let path = [], arcs = [];
  const context2d = new Proxy({}, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "beginPath") return () => { path = []; arcs = []; };
      if (name === "arc") return (x, y, radius) => { arcs.push([x, y, radius]); };
      if (name === "moveTo" || name === "lineTo") return (x, y) => { path.push([x, y]); };
      if (name === "setLineDash") return dash => { target.dash = dash; };
      if (name === "stroke") return () => { strokes.push({ color: target.strokeStyle, points: path.slice(), arcs: arcs.slice(), alpha: target.globalAlpha, dash: target.dash || [], width: target.lineWidth }); };
      if (name === "fillRect") return (x, y, width, height) => { fills.push({ color: target.fillStyle, alpha: target.globalAlpha, rect: [x, y, width, height] }); };
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
  return { KChart: sandbox.window.KChart, FakeElement, strokes, texts, fills };
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
  const { KChart, FakeElement, strokes, texts, fills } = load();
  const changes = [], tools = [], selections = [];
  const chart = new KChart(new FakeElement(), {
    macd: false, height: () => 340,
    drawings: {
      onChange: change => changes.push(JSON.parse(JSON.stringify(change))), onTool: tool => tools.push(tool),
      onSelect: drawing => selections.push(drawing ? drawing.id : null),
    },
  });
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  const reset = () => { strokes.length = 0; texts.length = 0; fills.length = 0; };
  reset();
  const tap = (x, y, pointerType = "mouse") => {
    chart.canvas.dispatch("pointerdown", { clientX: x, clientY: y, pointerType });
    chart.canvas.dispatch("pointerup", { clientX: x, clientY: y, pointerType });
  };
  const drag = (from, to, { pointerType = "mouse", release = "pointerup" } = {}) => {
    chart.canvas.dispatch("pointerdown", { clientX: from[0], clientY: from[1], pointerType });
    chart.canvas.dispatch("pointermove", { clientX: to[0], clientY: to[1], pointerType });
    if (release) chart.canvas.dispatch(release, { clientX: to[0], clientY: to[1], pointerType });
  };
  const key = name => chart.canvas.dispatch("keydown", { key: name });
  // 最後一次重畫時，用畫線顏色畫出來的線段、文字，以及選取把手（圓）的圓心。
  const drawnLines = () => { reset(); chart.draw(); return strokes.filter(stroke => stroke.color === DRAW && stroke.points.length).map(stroke => stroke.points); };
  const drawnLabels = () => { reset(); chart.draw(); return texts.filter(text => text.color === DRAW).map(text => text.text); };
  const drawnHandles = () => { reset(); chart.draw(); return strokes.filter(stroke => stroke.color === DRAW).flatMap(stroke => stroke.arcs.map(([x, y]) => [x, y])); };
  // 最後一次重畫時，用畫線顏色填滿的矩形（[x, y, 寬, 高]）。
  const drawnFills = () => { reset(); chart.draw(); return fills.filter(fill => fill.color === DRAW).map(fill => fill.rect); };
  return { chart, changes, tools, selections, strokes, texts, fills, reset, tap, drag, key, drawnLines, drawnLabels, drawnHandles, drawnFills };
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
  tap(150, 120); // 離線 44px：沒點到線，照舊選 K 棒
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
  chart.deleteSelectedDrawing();
  chart.cancelDrawing();
  chart.canvas.dispatch("keydown", { key: "Delete" });
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 87 });
  chart.canvas.dispatch("pointerup", { clientX: 150, clientY: 87 });
  assert.equal(chart.selection, 58);
  strokes.length = 0; chart.draw();
  assert.deepEqual(strokes.filter(stroke => stroke.color === DRAW), []);
});

// ---------- 票 02：選取、拖動、刪除 ----------
// 兩條已存的線：100 在 y=164；100.37 → 10 + 5.33/11.4×308 = 154。繪圖區 x 4～326，正中央 x=165。

test("一般模式下點在線的可點選範圍內就選取它：線上出現把手並對外通知，這一下不拿去選 K 棒", () => {
  const { chart, changes, selections, tap, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  assert.deepEqual(drawnHandles(), [], "還沒選取時沒有把手");

  tap(150, 164);
  assert.deepEqual(selections, ["saved-line-01"]);
  assert.deepEqual(drawnHandles(), [[165, 164.5]], "把手在線的正中央");
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]], "線還在原位");
  assert.equal(chart.selection, -1, "點到線時不選 K 棒");
  assert.deepEqual(changes, [], "選取不是資料變動，不通知儲存");

  tap(150, 164);
  assert.deepEqual(selections, ["saved-line-01"], "再點同一條不重複通知");
});

test("可點選範圍比線本身寬，手指又比滑鼠寬；範圍外的點擊照舊選 K 棒，不選線", () => {
  const { chart, selections, tap } = createChart();
  chart.setDrawings([saved(100)]);

  tap(150, 171); // 滑鼠離線 7px：點不到
  assert.deepEqual(selections, []);
  assert.equal(chart.selection, 58, "沒點到線：照舊選到第 58 根 K 棒");
  tap(150, 158); // 滑鼠離線 6px：點得到
  assert.deepEqual(selections, ["saved-line-01"]);

  tap(150, 250); // 點空白處取消
  assert.deepEqual(selections, ["saved-line-01", null]);
  tap(150, 176, "touch"); // 手指離線 12px：點得到
  assert.deepEqual(selections, ["saved-line-01", null, "saved-line-01"]);
  tap(150, 250, "touch");
  tap(150, 179, "touch"); // 手指離線 15px：點不到
  assert.deepEqual(selections, ["saved-line-01", null, "saved-line-01", null]);

  tap(340, 164); // 右側價格軸上、與線同高：不算點到線
  tap(2, 164);   // 繪圖區左緣之外
  assert.deepEqual(selections, ["saved-line-01", null, "saved-line-01", null]);
});

test("兩條線的可點選範圍重疊時，選到離點擊位置最近的那一條", () => {
  const { chart, selections, tap, drawnHandles } = createChart();
  chart.setDrawings([saved(100), saved(100.37, "saved-line-02")]); // y=164 與 y=154
  tap(150, 160); // 離 100 那條 4px、離 100.37 那條 6px
  assert.deepEqual(selections, ["saved-line-01"]);
  assert.deepEqual(drawnHandles(), [[165, 164.5]]);
  tap(150, 158); // 離 100 那條 6px、離 100.37 那條 4px：改選另一條
  assert.deepEqual(selections, ["saved-line-01", "saved-line-02"]);
  assert.deepEqual(drawnHandles(), [[165, 154.5]], "把手只在選取的那一條上");
});

test("點空白處取消選取：把手消失，同一下照舊選到 K 棒；線本身不動也不通知儲存", () => {
  const { chart, changes, selections, tap, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  tap(150, 250);
  assert.deepEqual(selections, ["saved-line-01", null]);
  assert.deepEqual(drawnHandles(), []);
  assert.equal(chart.selection, 58);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  assert.deepEqual(changes, []);
});

test("拖動已選取的線：線跟著移到新價位、圖表不平移，放開才通知儲存一次", () => {
  const { chart, changes, selections, tap, drawnLines, drawnLabels, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  const end = chart.view.end;

  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 166 }); // 抓在線下方 2px
  chart.canvas.dispatch("pointermove", { clientX: 230, clientY: 89 });  // 往上 77px、往右 80px
  assert.deepEqual(drawnLines(), [[[4, 87.5], [326, 87.5]]], "拖的時候線就跟著走");
  assert.deepEqual(drawnLabels(), ["102.85"]);
  assert.deepEqual(changes, [], "還沒放開不儲存");
  chart.canvas.dispatch("pointerup", { clientX: 230, clientY: 89 });

  assert.equal(chart.view.end, end, "圖表沒有跟著平移");
  assert.deepEqual(changes, [{ type: "update", drawing: { id: "saved-line-01", kind: "hline", anchors: [{ date: "2026-02-28", price: 102.85 }] } }]);
  assert.deepEqual(drawnLines(), [[[4, 87.5], [326, 87.5]]]);
  assert.deepEqual(drawnHandles(), [[165, 87.5]], "放開後仍選取著");
  assert.deepEqual(selections, ["saved-line-01"]);
  assert.equal(chart.selection, -1, "拖線不會順便選到 K 棒");
});

test("拖出繪圖區時線停在邊緣不消失；按在選取的線上沒有拖就放開，不算移動", () => {
  const { chart, changes, tap, drag, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);

  drag([150, 164], [151, 166]); // 手抖 2px：不到拖動門檻
  assert.deepEqual(changes, []);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);

  drag([150, 164], [150, -40]); // 拖到圖的上方之外：停在上緣（y=10 → 105.7）
  assert.deepEqual(changes.map(change => [change.type, change.drawing.anchors[0].price]), [["update", 105.7]]);
  assert.deepEqual(drawnLines(), [[[4, 10.5], [326, 10.5]]]);
  assert.deepEqual(drawnHandles(), [[165, 10.5]]);

  drag([150, 10], [150, 400]);  // 拖到時間軸之下：停在下緣（y=318 → 94.3）
  assert.deepEqual(changes.map(change => change.drawing.anchors[0].price), [105.7, 94.3]);
  assert.deepEqual(drawnLines(), [[[4, 318.5], [326, 318.5]]]);
});

test("拖曳起點不在已選取的線上時照舊平移圖表：沒選取的線拖不動，選取後從別處拖也只是平移", () => {
  const { chart, changes, selections, tap, drag, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100), saved(100.37, "saved-line-02")]);
  const end = chart.view.end;

  drag([150, 164], [230, 120]); // 線還沒選取：從線上拖也是平移
  assert.equal(chart.view.end, end - 10);
  assert.deepEqual(selections, []);

  chart.pan(10);
  tap(150, 164);
  drag([150, 250], [230, 200]); // 選取了，但起點在空白處
  assert.equal(chart.view.end, end - 10);
  drag([230, 154], [150, 100]); // 起點在另一條沒選取的線上（離選取的那條 10px）
  assert.equal(chart.view.end, end);
  assert.deepEqual(changes, []);
  assert.deepEqual(selections, ["saved-line-01"], "拖曳平移不會取消選取");
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]], [[4, 154.5], [326, 154.5]]]);
  assert.deepEqual(drawnHandles(), [[165, 164.5]]);
});

test("拖線途中變成雙指縮放或手勢被系統取消：線回到原位，不儲存", () => {
  const { chart, changes, tap, drag, drawnLines } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164, "touch");

  drag([150, 164], [150, 100], { pointerType: "touch", release: "pointercancel" });
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);

  const count = chart.view.count;
  chart.canvas.dispatch("pointerdown", { pointerId: 1, pointerType: "touch", clientX: 150, clientY: 164 });
  chart.canvas.dispatch("pointermove", { pointerId: 1, pointerType: "touch", clientX: 150, clientY: 100 });
  chart.canvas.dispatch("pointerdown", { pointerId: 2, pointerType: "touch", clientX: 250, clientY: 100 });
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]], "第二指一放上來，線就回原位");
  chart.canvas.dispatch("pointermove", { pointerId: 2, pointerType: "touch", clientX: 350, clientY: 100 });
  chart.canvas.dispatch("pointerup", { pointerId: 2, pointerType: "touch", clientX: 350, clientY: 100 });
  chart.canvas.dispatch("pointerup", { pointerId: 1, pointerType: "touch", clientX: 150, clientY: 100 });
  assert.ok(chart.view.count < count, "雙指拉開照舊是放大");
  assert.deepEqual(changes, []);
});

test("刪除選取的線：線消失、通知儲存端刪除、選取解除；沒有選取時刪除不做事", () => {
  const { chart, changes, selections, tap, drawnLines, drawnLabels, drawnHandles } = createChart();
  chart.setDrawings([saved(100), saved(102.85, "saved-line-02")]);
  chart.deleteSelectedDrawing();
  assert.deepEqual(changes, [], "沒選取時按刪除：什麼都不發生");
  assert.equal(drawnLines().length, 2);

  tap(150, 87);
  chart.deleteSelectedDrawing();
  assert.deepEqual(changes, [{ type: "remove", drawing: { id: "saved-line-02", kind: "hline", anchors: [{ date: "2026-02-28", price: 102.85 }] } }]);
  assert.deepEqual(selections, ["saved-line-02", null]);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]], "另一條不受影響");
  assert.deepEqual(drawnLabels(), ["100"]);
  assert.deepEqual(drawnHandles(), []);

  chart.deleteSelectedDrawing();
  assert.equal(changes.length, 1, "刪完再按一次不會多刪");
  tap(150, 87);
  assert.equal(chart.selection, 58, "原本線的位置現在是空白：點下去選的是 K 棒");
});

test("桌機按 Delete 刪除選取的線；沒有選取時 Delete 不做事", () => {
  const { changes, chart, tap, key, drawnLines } = createChart();
  chart.setDrawings([saved(100), saved(102.85, "saved-line-02")]);
  key("Delete");
  assert.deepEqual(changes, []);
  tap(150, 164);
  key("Delete");
  assert.deepEqual(changes.map(change => [change.type, change.drawing.id]), [["remove", "saved-line-01"]]);
  assert.deepEqual(drawnLines(), [[[4, 87.5], [326, 87.5]]]);
});

test("按 Esc 取消選取（線留著）；選了工具還沒點下去時按 Esc 則放下工具，之後點圖不會畫出線", () => {
  const { chart, changes, tools, selections, tap, key, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  key("Escape");
  assert.deepEqual(selections, ["saved-line-01", null]);
  assert.deepEqual(drawnHandles(), []);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);

  chart.setTool("hline");
  key("Escape");
  assert.deepEqual(tools, ["hline", null]);
  tap(150, 87);
  assert.deepEqual(changes, [], "工具已放下：這一下不畫線");
  assert.equal(chart.selection, 58, "而是照舊選 K 棒");

  chart.setTool("hline");
  chart.cancelDrawing(); // 焦點在工具列時由頁面呼叫，效果與在圖上按 Esc 相同
  assert.deepEqual(tools, ["hline", null, "hline", null]);
});

test("選取一條線之後改選畫線工具：先放下選取，新畫的線與原本那條各自獨立", () => {
  const { chart, changes, selections, tap, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  chart.setTool("hline");
  assert.deepEqual(selections, ["saved-line-01", null]);
  tap(150, 164); // 有工具時點在舊線上：是在同一個價位再畫一條，不是選取
  assert.deepEqual(changes.map(change => [change.type, change.drawing.anchors[0].price]), [["add", 100]]);
  assert.deepEqual(drawnHandles(), []);
  assert.equal(drawnLines().length, 2);
});

test("平移、縮放、切換日週月、即時更新重畫、重新載入同一組線之後，選取的仍是同一條", () => {
  const { chart, changes, selections, tap, drag, drawnHandles } = createChart();
  chart.setDrawings([saved(100), saved(120, "saved-line-02")]);
  tap(150, 164);

  chart.zoom(0.5);
  assert.deepEqual(drawnHandles(), [[165, 164.5]]);
  chart.zoom(2);
  chart.pan(-10); // 畫面含前段：100 → y=241
  assert.deepEqual(drawnHandles(), [[165, 241.5]]);
  chart.pan(10);

  const weekly = Array.from({ length: 16 }, (_, index) => ({
    t: new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10), label: `第${index + 1}週`, open: 100, close: 100, high: 105, low: 95, volume: 1,
  }));
  chart.setData(weekly, { count: 16, unit: "週" });
  assert.deepEqual(drawnHandles(), [[165, 164.5]]);
  const candles = dailyCandles();
  chart.setData([...candles.slice(0, -1), { ...candles.at(-1), close: 101 }], { count: 40, unit: "日" }); // 即時更新整組重設
  assert.deepEqual(drawnHandles(), [[165, 164.5]]);

  chart.setDrawings([saved(120, "saved-line-02"), saved(100)]); // 雲端那份晚一步讀回來，順序不同
  assert.deepEqual(drawnHandles(), [[165, 164.5]]);
  assert.deepEqual(selections, ["saved-line-01"], "從頭到尾沒有換過選取");

  drag([150, 164], [150, 87]); // 仍然拖得動原本選的那一條
  assert.deepEqual(changes.map(change => [change.type, change.drawing.id, change.drawing.anchors[0].price]), [["update", "saved-line-01", 102.85]]);
});

test("選取的線不在了就解除選取：換了一組沒有它的線、或切到不能畫線的分 K", () => {
  const { chart, changes, selections, tap, key, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  chart.setDrawings([saved(100, "saved-line-09")]); // 換股票：同價位但不是同一條
  assert.deepEqual(selections, ["saved-line-01", null]);
  assert.deepEqual(drawnHandles(), []);

  tap(150, 164);
  assert.deepEqual(selections, ["saved-line-01", null, "saved-line-09"]);
  const minutes = Array.from({ length: 40 }, (_, index) => ({
    t: `2026-03-20T09:${String(index).padStart(2, "0")}:00+08:00`, label: `第${index + 1}根`, open: 100, close: 100, high: 105, low: 95, volume: 1,
  }));
  chart.setData(minutes, { count: 40, unit: "5分" });
  assert.deepEqual(selections, ["saved-line-01", null, "saved-line-09", null]);
  key("Delete");
  chart.deleteSelectedDrawing();
  assert.deepEqual(changes, [], "分 K 上看不到線，也刪不到線");
  tap(150, 164);
  assert.deepEqual(selections.length, 4, "分 K 上點不到線");

  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  assert.deepEqual(drawnHandles(), [], "切回日 K：線還在，但不會自己恢復選取");
});

test("價格座標的上下緣不是整數分時，畫到或拖到邊緣的線仍在畫面內（價位取畫面內最靠邊的一分）", () => {
  const { chart, changes, tap, drag, drawnLines, drawnLabels } = createChart();
  // 高 19.40、低 19.00、收 19.20：價格座標是 18.972～19.428，上下緣都不在 0.01 的格點上。
  const candles = dailyCandles().map(candle => ({ ...candle, open: 19.2, close: 19.2, high: 19.4, low: 19 }));
  chart.setData(candles, { count: 40, unit: "日" });
  chart.setDrawings([saved(19.2)]); // y = 10 + 0.228/0.456×308 = 164
  tap(150, 164);

  drag([150, 164], [150, -40]); // 上緣之外：停在 19.42 → y = 10 + 0.008/0.456×308 = 15.4
  assert.deepEqual(changes.map(change => change.drawing.anchors[0].price), [19.42]);
  assert.deepEqual(drawnLines(), [[[4, 15.5], [326, 15.5]]]);
  assert.deepEqual(drawnLabels(), ["19.42"]);

  drag([150, 15], [150, 400]);  // 下緣之外：停在 18.98 → y = 10 + 0.448/0.456×308 = 312.6
  assert.deepEqual(changes.map(change => change.drawing.anchors[0].price), [19.42, 18.98]);
  assert.deepEqual(drawnLines(), [[[4, 313.5], [326, 313.5]]]);

  chart.setTool("hline");
  tap(150, 10); // 正好點在繪圖區上緣：新畫的線也要看得到
  assert.deepEqual(changes.at(-1).drawing.anchors[0].price, 19.42);
  assert.equal(drawnLines().length, 2);
});

test("選取一條線之後改選畫線工具：圖表當下就重畫，把手不會殘留到下一次重畫", () => {
  const { chart, strokes, tap, reset } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  reset();
  chart.setTool("hline"); // 這裡不另外呼叫 chart.draw()
  assert.ok(strokes.length > 0, "選工具會重畫");
  assert.deepEqual(strokes.flatMap(stroke => stroke.arcs), [], "把手已經不在");
});

test("選取的線移出畫面就解除選取：看不到的線不會被 Delete 或刪除鈕刪掉，移回來時線還在", () => {
  const { chart, changes, selections, tap, key, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(120)]);
  chart.pan(-10); // 畫面含前段（88.6～134.2）：120 在 y=106
  tap(150, 106);
  assert.deepEqual(selections, ["saved-line-01"]);
  assert.deepEqual(drawnHandles(), [[165, 106.5]]);

  chart.pan(10);  // 回到後 40 根（94.3～105.7）：120 不在畫面內
  assert.deepEqual(selections, ["saved-line-01", null], "平移當下就解除，工具列的刪除跟著收起");
  key("Delete");
  chart.deleteSelectedDrawing();
  assert.deepEqual(changes, []);

  chart.pan(-10);
  assert.deepEqual(drawnLines(), [[[4, 106.5], [326, 106.5]]], "線還在");
  assert.deepEqual(drawnHandles(), [], "但要再點一次才選取");
});

test("拖到一半時重新載入同一組線（雲端那份晚一步讀回來）：接著拖，放開照樣移到新位置並儲存", () => {
  const { chart, changes, selections, tap, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 164 });
  chart.canvas.dispatch("pointermove", { clientX: 150, clientY: 120 });
  chart.setDrawings([saved(120, "saved-line-02"), saved(100)]);
  assert.deepEqual(drawnLines(), [[[4, 120.5], [326, 120.5]]], "線留在手上的位置，不跳回原價位");
  chart.canvas.dispatch("pointermove", { clientX: 150, clientY: 87 });
  chart.canvas.dispatch("pointerup", { clientX: 150, clientY: 87 });
  assert.deepEqual(changes.map(change => [change.type, change.drawing.id, change.drawing.anchors[0].price]), [["update", "saved-line-01", 102.85]]);
  assert.deepEqual(drawnHandles(), [[165, 87.5]]);
  assert.deepEqual(selections, ["saved-line-01"]);
  assert.equal(chart.selection, -1, "放開不會被當成點擊去選 K 棒");
});

test("拖到一半時那條線不在了（換了股票）：這次拖動作廢，放開不儲存、也不當成點擊", () => {
  const { chart, changes, selections, tap, drawnLines } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 164 });
  chart.canvas.dispatch("pointermove", { clientX: 150, clientY: 120 });
  chart.setDrawings([saved(102.85, "saved-line-09")]);
  chart.canvas.dispatch("pointermove", { clientX: 150, clientY: 87 });
  chart.canvas.dispatch("pointerup", { clientX: 150, clientY: 87 });
  assert.deepEqual(changes, []);
  assert.deepEqual(selections, ["saved-line-01", null], "沒有因為放開在另一條線上就選到它");
  assert.deepEqual(drawnLines(), [[[4, 87.5], [326, 87.5]]]);
  assert.equal(chart.selection, -1);
});

test("拖動中按 Esc：線回到拖動前的位置並取消選取，放開時不儲存", () => {
  const { chart, changes, selections, tap, key, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([saved(100)]);
  tap(150, 164);
  chart.canvas.dispatch("pointerdown", { clientX: 150, clientY: 164 });
  chart.canvas.dispatch("pointermove", { clientX: 150, clientY: 87 });
  key("Escape");
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  chart.canvas.dispatch("pointermove", { clientX: 150, clientY: 60 });
  chart.canvas.dispatch("pointerup", { clientX: 150, clientY: 60 });
  assert.deepEqual(changes, []);
  assert.deepEqual(selections, ["saved-line-01", null]);
  assert.deepEqual(drawnLines(), [[[4, 164.5], [326, 164.5]]]);
  assert.deepEqual(drawnHandles(), []);
});

test("兩條線離點擊位置一樣近時選後畫的那一條（疊在上面的）；Mac 鍵盤的 delete（Backspace）也能刪", () => {
  const { chart, changes, selections, tap, key } = createChart();
  chart.setDrawings([saved(100), saved(100, "saved-line-02")]); // 同一個價位疊了兩條
  tap(150, 164);
  assert.deepEqual(selections, ["saved-line-02"]);
  key("Backspace");
  assert.deepEqual(changes.map(change => [change.type, change.drawing.id]), [["remove", "saved-line-02"]]);
  tap(150, 164);
  assert.deepEqual(selections, ["saved-line-02", null, "saved-line-01"], "底下那一條還在，選得到");
});

// ---------- 票 03：趨勢線與射線 ----------
// 只看後 40 根時每根寬 322/40＝8.05，第 k 根（從 0 起算）的中心在 x＝4＋8.05×(k＋0.5)：
// 第 10 根＝88.525、第 20 根＝169.025、第 30 根＝249.525。價位 97.15／100／102.85 在 y＝241／164／87。
// 斜線的座標不落在整數像素上，比對時容許 0.01 像素的誤差。
function assertLines(actual, expected, message) {
  const flat = lines => lines.flat(2), note = `${message || "位置"}：${JSON.stringify(actual)} 應為 ${JSON.stringify(expected)}`;
  assert.equal(actual.length, expected.length, note);
  assert.equal(flat(actual).length, flat(expected).length, note);
  flat(actual).forEach((value, index) => assert.ok(Math.abs(value - flat(expected)[index]) <= 0.01, note));
}
// 把手（圓心）的位置，同樣容許 0.01 像素。
const assertHandles = (actual, expected, message) => assertLines([actual], [expected], message);
// 一串數字（例如各個標示的位置），同樣容許 0.01。
const assertNumbers = (actual, expected, message) => assertLines([actual], [expected], message);

test("選了趨勢線工具後點兩下：兩點之間出現一條線，兩端各自對在所點的交易日與價位，工具自動回到一般模式", () => {
  const { chart, changes, tools, tap, drawnLines, drawnLabels } = createChart();
  chart.setTool("trend");
  tap(90, 241);  // x=90 → 可見的第 10 根＝第 50 根＝2026-02-20；y=241 → 97.15
  assert.deepEqual(changes, [], "只點了第一下：還沒有線");
  assert.deepEqual(tools, ["trend"], "工具留著等第二下");

  tap(250, 87);  // x=250 → 可見的第 30 根＝第 70 根＝2026-03-12；y=87 → 102.85
  assert.equal(changes.length, 1);
  const [{ type, drawing }] = changes;
  assert.equal(type, "add");
  assert.match(drawing.id, ID_RE);
  assert.deepEqual(drawing, { id: drawing.id, kind: "trend", anchors: [{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-12", price: 102.85 }] });
  assert.deepEqual(tools, ["trend", null], "畫完自動回一般模式");

  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]]);
  assert.deepEqual(drawnLabels(), [], "趨勢線不標價位");
});

const savedLine = (kind, from, to, id = "saved-two-01") => ({
  id, kind, anchors: [{ date: from[0], price: from[1] }, { date: to[0], price: to[1] }], text: "",
  created_at: "2026-11-16T06:00:00.000Z", updated_at: "2026-11-16T06:00:00.000Z",
});

test("射線穿過兩點一路往右延伸到繪圖區的邊緣為止：到圖的右緣、不進價格軸；太陡的在上緣就停；先點右邊再點左邊也一樣往右", () => {
  const { chart, changes, tools, tap, drawnLines } = createChart();
  chart.setTool("ray");
  tap(90, 241);
  tap(250, 164); // 第 30 根、100
  assert.deepEqual(changes.map(change => [change.type, change.drawing.kind, change.drawing.anchors]),
    [["add", "ray", [{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-12", price: 100 }]]]);
  assert.deepEqual(tools, ["ray", null]);
  // 每往右 161px 上升 77px；起點到右緣 326 還有 237.475px → 上升 113.575 → y＝127.425。
  assertLines(drawnLines(), [[[88.525, 241], [326, 127.425]]]);

  // 起點到第二點每往右 80.5px 上升 154px：再上升 231px 就到上緣 y=10，那時 x＝88.525＋120.75＝209.275。
  chart.setDrawings([savedLine("ray", ["2026-02-20", 97.15], ["2026-03-02", 102.85])]);
  assertLines(drawnLines(), [[[88.525, 241], [209.275, 10]]]);

  // 先點右邊、再點左邊（本人 2026-11-16 選樣本 B）：仍然從比較早的那一點穿過比較晚的那一點往右延伸，和第一條一模一樣。
  chart.setDrawings([]);
  chart.setTool("ray");
  tap(250, 164);
  tap(90, 241);
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-03-12", price: 100 }, { date: "2026-02-20", price: 97.15 }], "錨點照點的順序存");
  assertLines(drawnLines(), [[[88.525, 241], [326, 127.425]]]);
  // 預覽線也是往右：起點在右、游標在左。
  chart.setDrawings([]);
  chart.setTool("ray");
  tap(250, 164);
  chart.canvas.dispatch("pointermove", { clientX: 90, clientY: 241 });
  assertLines(drawnLines(), [[[88.525, 241], [326, 127.425]]]);
});

test("端點在畫面外時，畫面內的那一段仍在原本的斜率上；整條都在畫面外就不畫", () => {
  const { chart, drawnLines } = createChart();
  // 起點是第 20 根（2026-01-21，在畫面左邊之外，x＝−152.975）、終點第 70 根：線在左緣 x=4 的高度是 241−154×156.975/402.5＝180.94。
  chart.setDrawings([savedLine("trend", ["2026-01-21", 97.15], ["2026-03-12", 102.85])]);
  assertLines(drawnLines(), [[[4, 180.94], [249.525, 87]]]);

  // 終點的價位 111.4 在畫面上方之外（y＝−144）：線走到一半就碰到上緣，那時 x＝88.525＋80.5＝169.025。
  chart.setDrawings([savedLine("trend", ["2026-02-20", 100], ["2026-03-12", 111.4])]);
  assertLines(drawnLines(), [[[88.525, 164], [169.025, 10]]]);

  // 兩端都在畫面左邊之外的趨勢線：不畫。同樣兩點的射線則會穿進畫面（y＝241 的水平線，從左緣到右緣）。
  chart.setDrawings([savedLine("trend", ["2026-01-11", 97.15], ["2026-01-21", 97.15])]);
  assertLines(drawnLines(), []);
  chart.setDrawings([savedLine("ray", ["2026-01-11", 97.15], ["2026-01-21", 97.15])]);
  assertLines(drawnLines(), [[[4, 241], [326, 241]]]);
  // 整條在畫面上方之外。
  chart.setDrawings([savedLine("trend", ["2026-02-20", 110], ["2026-03-12", 111.4])]);
  assertLines(drawnLines(), []);
});

test("平移、縮放後趨勢線的兩端仍在原本的交易日與價位", () => {
  const { chart, drawnLines } = createChart();
  chart.setDrawings([savedLine("trend", ["2026-02-20", 97.15], ["2026-03-12", 102.85])]);
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]]);

  // 放大成最後 20 根（第 60～79 根，每根 16.1px）：起點第 50 根在左邊之外（x＝−148.95），終點第 70 根在 x＝173.05。
  // 線在左緣的高度：241−154×152.95/322＝167.85。
  chart.zoom(0.5);
  assertLines(drawnLines(), [[[4, 167.85], [173.05, 87]]]);

  // 縮回 40 根再往左平移 10 根（第 30～69 根，價格座標 88.6～134.2）：97.15 → y＝260.25、102.85 → y＝221.75。
  // 起點第 50 根在 x＝169.025，終點第 70 根在右緣之外（x＝330.025）：線在右緣的高度是 260.25−38.5×156.975/161＝222.7125。
  chart.zoom(2);
  chart.pan(-10);
  assertLines(drawnLines(), [[[169.025, 260.25], [326, 222.7125]]]);

  chart.pan(10);
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]]);
});

test("在日 K 畫的線切到週 K、月 K：兩端落在包含該日期的那一根（正中央）與同一價位，切回日 K 回到原位", () => {
  const { chart, changes, tap, drawnLines } = createChart();
  // 兩端都不是週五、也不是月底：2026-02-18 是週三，2026-03-12 是週四。
  chart.setDrawings([savedLine("trend", ["2026-02-18", 97.15], ["2026-03-12", 102.85])]);
  const bars = (count, time) => Array.from({ length: count }, (_, index) => ({ t: time(index), label: `第${index + 1}根`, open: 100, close: 100, high: 105, low: 95, volume: 1 }));
  // 週 K：每根的日期是那一週的週五（2026-01-02 起）。02-18 那一週的週五是 02-20（第 7 根），03-12 那一週是 03-13（第 10 根）。
  // 16 根、每根寬 20.125：第 7 根中心 x＝4＋20.125×7.5＝154.9375，第 10 根＝215.3125。
  chart.setData(bars(16, index => new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10)), { count: 16, unit: "週" });
  assertLines(drawnLines(), [[[154.9375, 241], [215.3125, 87]]], "週 K");
  // 月 K：每根的日期是當月 28 日（2025-01 起）。2026-02 是第 13 根（x＝275.6875），2026-03 是第 14 根（x＝295.8125）。
  chart.setData(bars(16, index => new Date(Date.UTC(2025, index, 28)).toISOString().slice(0, 10)), { count: 16, unit: "月" });
  assertLines(drawnLines(), [[[275.6875, 241], [295.8125, 87]]], "月 K");

  // 在月 K 上畫：兩端的日期各取點到的那一根（x=150 → 第 7 根＝2025-08-28；x=250 → 第 12 根＝2026-01-28）。
  chart.setTool("trend");
  tap(150, 241);
  tap(250, 87);
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2025-08-28", price: 97.15 }, { date: "2026-01-28", price: 102.85 }]]);

  chart.setDrawings([savedLine("trend", ["2026-02-18", 97.15], ["2026-03-12", 102.85])]);
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  // 日 K：02-18 是第 48 根（可見的第 8 根，x＝4＋8.05×8.5＝72.425）。
  assertLines(drawnLines(), [[[72.425, 241], [249.525, 87]]], "切回日 K");
});

test("五年歷史接到圖前面後線仍對在原本的日期與價位；起點比圖上第一根還早時先不畫，歷史接上才出現", () => {
  const { chart, drawnLines } = createChart();
  const recent = savedLine("trend", ["2026-02-20", 97.15], ["2026-03-12", 102.85]);
  const old = savedLine("trend", ["2025-12-20", 97.15], ["2026-03-12", 102.85], "saved-two-02"); // 起點在還沒載入的歷史裡
  chart.setDrawings([recent, old]);
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]], "歷史還沒接上：位置算不出來的那一條不畫");

  const older = Array.from({ length: 30 }, (_, index) => {
    const date = new Date(Date.UTC(2025, 11, 2 + index)).toISOString().slice(0, 10);
    return { t: date, label: date, open: 100, close: 100, high: 105, low: 95, volume: 1 };
  });
  chart.setData([...older, ...dailyCandles()], { count: 40, unit: "日" });
  // 接上 30 根之後畫面仍是最後 40 根：近期那條原地不動。
  // 較早那條的起點 2025-12-20 是第 18 根，在畫面第一根（第 70 根）左邊 52 根：x＝4−8.05×51.5＝−410.575。
  // 到終點 249.525 共 660.1px、上升 154px；在左緣 x=4 的高度是 241−154×414.575/660.1＝144.28。
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]], [[4, 144.28], [249.525, 87]]], "歷史接上後");
});

test("點了第一下之後：起點有記號，預覽線從起點跟到游標所在的那一根與價位；還沒點第二下不算畫完、不儲存", () => {
  const { chart, changes, tools, tap, drawnLines, drawnHandles } = createChart();
  chart.setTool("trend");
  assertHandles(drawnHandles(), [], "還沒點：沒有記號");
  tap(90, 241);
  assertHandles(drawnHandles(), [[88.525, 241]], "起點記號在所點的那一根正中央與價位");
  assertLines(drawnLines(), [], "游標還在起點上：沒有預覽線");

  chart.canvas.dispatch("pointermove", { clientX: 250, clientY: 87 });
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]], "預覽線的終點就是點下去會落的位置");
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 164 });
  assertLines(drawnLines(), [[[88.525, 241], [169.025, 164]]], "跟著游標走");
  chart.canvas.dispatch("pointermove", { clientX: 350, clientY: 164 }); // 游標移到右側價格軸上
  assertLines(drawnLines(), [], "游標不在繪圖區內：不預覽");
  assertHandles(drawnHandles(), [[88.525, 241]]);
  assert.deepEqual(changes, []);
  assert.deepEqual(tools, ["trend"]);

  tap(250, 87);
  assert.equal(changes.length, 1);
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]], "畫完只剩那一條線，預覽不留");
  assertHandles(drawnHandles(), [], "起點記號收掉，新畫的線沒有被選取");
});

test("射線的預覽也一路延伸到邊緣；手指按下去（還沒放開）預覽線就跟到手指的位置", () => {
  const { chart, changes, strokes, reset, tap, drawnLines } = createChart();
  chart.setTool("ray");
  tap(90, 241, "touch");
  reset();
  chart.canvas.dispatch("pointerdown", { clientX: 250, clientY: 164, pointerType: "touch" });
  assertLines(strokes.filter(stroke => stroke.color === DRAW && stroke.points.length).map(stroke => stroke.points),
    [[[88.525, 241], [326, 127.425]]], "按下去當下就重畫出預覽");
  assert.deepEqual(changes, [], "還沒放開：還沒畫完");
  chart.canvas.dispatch("pointerup", { clientX: 250, clientY: 164, pointerType: "touch" });
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-12", price: 100 }]]);
  assertLines(drawnLines(), [[[88.525, 241], [326, 127.425]]]);
});

test("點了第一下後按 Esc、再點一次同一個工具、或改選別的工具：都不留下半條線", () => {
  const { chart, changes, tools, tap, key, drawnLines, drawnHandles } = createChart();
  chart.setTool("trend");
  tap(90, 241);
  chart.canvas.dispatch("pointermove", { clientX: 250, clientY: 87 });
  key("Escape");
  assert.deepEqual(tools, ["trend", null], "Esc：工具放下");
  assertLines(drawnLines(), []);
  assertHandles(drawnHandles(), []);
  tap(250, 87);
  assert.deepEqual(changes, [], "這一下不會接著畫完");
  assert.equal(chart.selection, 70, "而是照舊選 K 棒");

  chart.setTool("trend");
  tap(90, 241);
  chart.setTool(null); // 頁面在使用者再點一次同一個工具時這樣呼叫
  assertHandles(drawnHandles(), []);
  chart.setTool("trend");
  tap(250, 87);
  assert.deepEqual(changes, [], "重新選工具後這是新的第一下，不會接到上次的起點");
  assertHandles(drawnHandles(), [[249.525, 87]]);

  chart.setTool("ray"); // 點到一半改選射線
  assertHandles(drawnHandles(), [], "換工具：原本點的起點不留");
  tap(90, 241);
  assert.deepEqual(changes, []);
  tap(250, 164);
  assert.deepEqual(changes.map(change => [change.drawing.kind, change.drawing.anchors[0].date]), [["ray", "2026-02-20"]]);

  chart.setTool("trend");
  tap(90, 241);
  chart.cancelDrawing(); // 焦點在工具列時由頁面呼叫
  assertHandles(drawnHandles(), []);
  assert.equal(changes.length, 1);
});

test("點到一半時切換日週月，起點留在原本的日期與價位；切到分 K 則連同工具一起放下", () => {
  const { chart, changes, tools, tap, drawnHandles } = createChart();
  chart.setTool("trend");
  tap(90, 241); // 2026-02-20、97.15
  const weekly = Array.from({ length: 16 }, (_, index) => ({
    t: new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10), label: `第${index + 1}週`, open: 100, close: 100, high: 105, low: 95, volume: 1,
  }));
  chart.setData(weekly, { count: 16, unit: "週" });
  assertHandles(drawnHandles(), [[154.9375, 241]], "週 K：起點在 02-20 那一根");
  tap(250, 87); // 週 K 每根 20.125：x=250 → 第 12 根＝2026-03-27
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-27", price: 102.85 }]]);

  chart.setTool("ray");
  tap(90, 241);
  const minutes = Array.from({ length: 40 }, (_, index) => ({
    t: `2026-03-20T09:${String(index).padStart(2, "0")}:00+08:00`, label: `第${index + 1}根`, open: 100, close: 100, high: 105, low: 95, volume: 1,
  }));
  chart.setData(minutes, { count: 40, unit: "5分" });
  assert.deepEqual(tools.slice(-2), ["ray", null]);
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  assertHandles(drawnHandles(), [], "切回日 K：半條線沒有留下來");
  assert.equal(changes.length, 1);
});

test("兩點落在同一位置（或近到看不出是一條線）時不畫：第二下不算，工具與起點留著等下一下", () => {
  const { chart, changes, tools, tap, drawnLines, drawnHandles } = createChart();
  chart.setTool("trend");
  tap(90, 241);
  tap(90, 241);  // 同一點再點一次（例如手機上連點兩下）
  tap(92, 243);  // 同一根、差 2px
  assert.deepEqual(changes, []);
  assert.deepEqual(tools, ["trend"]);
  assertHandles(drawnHandles(), [[88.525, 241]], "起點還在");

  tap(90, 233);  // 同一根、往上 8px：是一條看得到的直線。y=233 → 97.446… 取到分＝97.45 → y＝232.9
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-20", price: 97.15 }, { date: "2026-02-20", price: 97.45 }]]);
  assertLines(drawnLines(), [[[88.525, 241], [88.525, 232.895]]]);

  // 已存的資料裡兩點完全相同的線（看不見也選不到）：載入時略過。
  chart.setDrawings([savedLine("trend", ["2026-02-20", 100], ["2026-02-20", 100]), savedLine("ray", ["2026-02-20", 100], ["2026-02-20", 100], "saved-two-02")]);
  assertLines(drawnLines(), []);
});

// 選取與拖動用的那條趨勢線：(88.525, 241) → (249.525, 87)，長 222.79px，中點 (169.025, 164)。
// 中點正下方 d 像素的點，離線 d×161/222.79 像素：d=6 → 4.3、d=11 → 7.9、d=18 → 13.0、d=21 → 15.2。
const TREND = () => savedLine("trend", ["2026-02-20", 97.15], ["2026-03-12", 102.85]);

test("點在趨勢線的可點選範圍內就選取：兩個端點各出現一個把手；超出線段兩端的延長線上點不到，射線則點得到", () => {
  const { chart, changes, selections, tap, drawnHandles } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 175); // 滑鼠離線 7.9px：點不到
  assert.deepEqual(selections, []);
  assert.equal(chart.selection, 60, "照舊選 K 棒");
  tap(169, 170); // 滑鼠離線 4.3px
  assert.deepEqual(selections, ["saved-two-01"]);
  assertHandles(drawnHandles(), [[88.525, 241], [249.525, 87]]);
  assert.deepEqual(changes, [], "選取不是資料變動");

  tap(169, 280);
  tap(169, 182, "touch"); // 手指離線 13px：點得到
  tap(169, 280, "touch");
  tap(169, 185, "touch"); // 手指離線 15.2px：點不到
  assert.deepEqual(selections, ["saved-two-01", null, "saved-two-01", null]);

  tap(270, 68); // 線往右上延長 28px 的位置：趨勢線只到第二點為止
  assert.deepEqual(selections.length, 4);
  chart.setDrawings([savedLine("ray", ["2026-02-20", 97.15], ["2026-03-12", 102.85], "saved-ray-01")]);
  tap(270, 68); // 同樣兩點的射線會延伸過去
  assert.deepEqual(selections.at(-1), "saved-ray-01");
  assertHandles(drawnHandles(), [[88.525, 241], [249.525, 87]], "射線的把手在兩個錨點上，不在延伸到邊緣的那一端");
});

test("端點在畫面外時只有畫面內的那個端點有把手；平移回來兩個都在，選取的仍是同一條", () => {
  const { chart, selections, tap, drawnHandles } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 164);
  assert.equal(chart.selection, -1, "點到線時不選 K 棒");
  chart.zoom(0.5); // 最後 20 根：起點在左邊之外，終點在 x=173.05
  assertHandles(drawnHandles(), [[173.05, 87]]);
  chart.zoom(2);
  assertHandles(drawnHandles(), [[88.525, 241], [249.525, 87]]);
  assert.deepEqual(selections, ["saved-two-01"]);
});

test("拖已選取的趨勢線的線身：整條平移，兩端一起移動同樣的根數與價差，圖表不平移，放開才儲存一次", () => {
  const { chart, changes, tap, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 164);
  const end = chart.view.end;

  chart.canvas.dispatch("pointerdown", { clientX: 169, clientY: 164 });
  chart.canvas.dispatch("pointermove", { clientX: 129, clientY: 87 }); // 往左 40px＝5 根，往上 77px＝＋2.85
  assertLines(drawnLines(), [[[48.275, 164], [209.275, 10]]], "拖的時候線就跟著走");
  assert.deepEqual(changes, [], "還沒放開不儲存");
  chart.canvas.dispatch("pointerup", { clientX: 129, clientY: 87 });

  assert.equal(chart.view.end, end, "圖表沒有跟著平移");
  assert.deepEqual(changes, [{ type: "update", drawing: { id: "saved-two-01", kind: "trend", anchors: [{ date: "2026-02-15", price: 100 }, { date: "2026-03-07", price: 105.7 }] } }]);
  assertHandles(drawnHandles(), [[48.275, 164], [209.275, 10]], "放開後仍選取著");
  assert.equal(chart.selection, -1);
});

test("整條拖到超過最後一根 K 棒或拖出繪圖區：停在最後一根，線的長度與斜率不變", () => {
  const { chart, changes, tap, drag, drawnLines } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 164);
  drag([169, 164], [500, 164]); // 終點原本在第 70 根，最多只能再往右 9 根（第 79 根＝2026-03-21）
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-03-01", price: 97.15 }, { date: "2026-03-21", price: 102.85 }]]);
  assertLines(drawnLines(), [[[160.975, 241], [321.975, 87]]]);
});

test("拖已選取的線的端點：只有那一端跟著走（改變角度與長度），另一端不動；放開後儲存", () => {
  const { chart, changes, tap, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 164);
  const end = chart.view.end;

  chart.canvas.dispatch("pointerdown", { clientX: 250, clientY: 88 });  // 抓在終點把手上
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 165 }); // 往左 80px、往下 77px → 第 20 根（2026-03-02）、100
  assertLines(drawnLines(), [[[88.525, 241], [169.025, 164]]]);
  assert.deepEqual(changes, []);
  chart.canvas.dispatch("pointerup", { clientX: 170, clientY: 165 });
  assert.deepEqual(changes, [{ type: "update", drawing: { id: "saved-two-01", kind: "trend", anchors: [{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-02", price: 100 }] } }]);
  assert.equal(chart.view.end, end, "圖表沒有跟著平移");

  chart.canvas.dispatch("pointerdown", { clientX: 88, clientY: 241 });  // 換抓起點把手
  chart.canvas.dispatch("pointermove", { clientX: 48, clientY: 164 });  // 往左 40px、往上 77px → 第 5 根（2026-02-15）、100
  chart.canvas.dispatch("pointerup", { clientX: 48, clientY: 164 });
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-15", price: 100 }, { date: "2026-03-02", price: 100 }]);
  assertLines(drawnLines(), [[[48.275, 164], [169.025, 164]]]);
  assertHandles(drawnHandles(), [[48.275, 164], [169.025, 164]]);
  assert.equal(changes.length, 2);
});

test("把端點拖到和另一端同一位置：那一端停在上一個還成線的位置，不會變成看不見的線", () => {
  const { chart, changes, tap, drawnLines } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 164);
  chart.canvas.dispatch("pointerdown", { clientX: 250, clientY: 88 });
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 165 }); // 先到第 20 根、100
  chart.canvas.dispatch("pointermove", { clientX: 89, clientY: 242 });  // 再拖到起點上
  assertLines(drawnLines(), [[[88.525, 241], [169.025, 164]]]);
  chart.canvas.dispatch("pointerup", { clientX: 89, clientY: 242 });
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-02", price: 100 }]]);
});

test("拖端點途中按 Esc 或手勢被系統取消：線回到原本的樣子，不儲存", () => {
  const { chart, changes, tap, key, drag, drawnLines } = createChart();
  chart.setDrawings([TREND()]);
  tap(169, 164);
  chart.canvas.dispatch("pointerdown", { clientX: 250, clientY: 88 });
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 165 });
  key("Escape");
  chart.canvas.dispatch("pointerup", { clientX: 170, clientY: 165 });
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]]);

  tap(169, 164, "touch");
  drag([250, 88], [170, 165], { pointerType: "touch", release: "pointercancel" });
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]]]);
  assert.deepEqual(changes, []);
});

test("射線：點在延伸出去的那一段也選得到、拖得動（整條平移）；拖錨點則改變方向", () => {
  const { chart, changes, tap, drag, drawnLines } = createChart();
  chart.setDrawings([savedLine("ray", ["2026-02-20", 97.15], ["2026-03-12", 100], "saved-ray-01")]); // (88.525, 241) → (249.525, 164) → 右緣 (326, 127.425)
  tap(300, 140); // 射線在 x=300 的高度是 139.86
  drag([300, 140], [300, 217]); // 往下 77px＝−2.85
  assert.deepEqual(changes.map(change => [change.type, change.drawing.anchors]),
    [["update", [{ date: "2026-02-20", price: 94.3 }, { date: "2026-03-12", price: 97.15 }]]]);
  assertLines(drawnLines(), [[[88.525, 318], [326, 204.425]]]);

  drag([250, 241], [250, 164]); // 第二個錨點現在在 (249.525, 241)：往上拖 77px → 100
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-20", price: 94.3 }, { date: "2026-03-12", price: 100 }]);
  // 每往右 161px 上升 154px：從起點 (88.525, 318) 到右緣 237.475px → 上升 227.15 → y＝90.85。
  assertLines(drawnLines(), [[[88.525, 318], [326, 90.85]]]);
});

test("很短的線用手指也分得出拖線身與拖端點：線身的中段一定留給整條平移", () => {
  const { chart, changes, tap, drag } = createChart();
  // 第 10 根到第 12 根、同一價位：長 16.1px，比手指的可點選範圍（14px）的兩倍還短。
  chart.setDrawings([savedLine("trend", ["2026-02-20", 100], ["2026-02-22", 100])]); // (88.525, 164) → (104.625, 164)
  tap(96, 164, "touch");
  drag([96, 164], [177, 164], { pointerType: "touch" }); // 抓中間往右 81px＝10 根：兩端一起走
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-03-02", price: 100 }, { date: "2026-03-04", price: 100 }]);

  drag([185, 164], [185, 87], { pointerType: "touch" }); // 抓右端（現在在 x=185.125）往上 77px：只有那一端走
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-03-02", price: 100 }, { date: "2026-03-04", price: 102.85 }]);
  assert.equal(changes.length, 2);
});

test("刪除選取的趨勢線（Delete 或刪除鈕）：線消失並通知儲存端刪除；切換日週月後選取還在同一條", () => {
  const { chart, changes, selections, tap, key, drawnLines, drawnHandles } = createChart();
  chart.setDrawings([TREND(), saved(100)]);
  tap(169, 170);
  const weekly = Array.from({ length: 16 }, (_, index) => ({
    t: new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10), label: `第${index + 1}週`, open: 100, close: 100, high: 105, low: 95, volume: 1,
  }));
  chart.setData(weekly, { count: 16, unit: "週" }); // 02-20 是第 7 根（154.9375），03-12 那一週是第 10 根（215.3125）
  assertHandles(drawnHandles(), [[154.9375, 241], [215.3125, 87]]);
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  assert.deepEqual(selections, ["saved-two-01"]);

  key("Delete");
  assert.deepEqual(changes, [{ type: "remove", drawing: { id: "saved-two-01", kind: "trend", anchors: [{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-12", price: 102.85 }] } }]);
  assertLines(drawnLines(), [[[4, 164.5], [326, 164.5]]], "水平線不受影響");
  assert.deepEqual(selections, ["saved-two-01", null]);
});

test("斜線和水平線交叉的地方：選到離點擊位置最近的那一條", () => {
  const { chart, selections, tap } = createChart();
  chart.setDrawings([saved(100), TREND()]); // 兩條在 (169.025, 164) 交叉
  tap(120, 166); // 離水平線 2px；斜線在 x=120 的高度是 210.9，遠得多
  tap(140, 190); // 斜線在 x=140 的高度是 191.8：離斜線 1.3px、離水平線 26px
  assert.deepEqual(selections, ["saved-line-01", "saved-two-01"]);
});

test("在週 K 上只上下拖整條：兩端的日期維持原本的交易日（不會被換成那一週的最後一天），切回日 K 仍在原本那兩根", () => {
  const { chart, changes, tap, drag, drawnLines } = createChart();
  chart.setDrawings([savedLine("trend", ["2026-02-18", 97.15], ["2026-03-12", 102.85])]); // 週三、週四
  const weekly = Array.from({ length: 16 }, (_, index) => ({
    t: new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10), label: `第${index + 1}週`, open: 100, close: 100, high: 105, low: 95, volume: 1,
  }));
  chart.setData(weekly, { count: 16, unit: "週" }); // 線在 (154.9375, 241) → (215.3125, 87)，中點 (185.125, 164)
  tap(185, 164);
  drag([185, 164], [185, 87]); // 往上 77px＝＋2.85，左右沒有動
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-18", price: 100 }, { date: "2026-03-12", price: 105.7 }]]);

  drag([185, 87], [205, 87]); // 往右 20px＝1 根：兩端各往後一週，日期是那一根週 K 的日期（02-27、03-20）
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-27", price: 100 }, { date: "2026-03-20", price: 105.7 }]);
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  // 日 K：02-27 是第 57 根（可見的第 17 根，x＝144.875），03-20 是第 78 根（x＝313.925）。
  assertLines(drawnLines(), [[[144.875, 164], [313.925, 10]]]);
});

// ---------- 票 03：審查後補的邊界情況 ----------
const WEEKLY = () => Array.from({ length: 16 }, (_, index) => ({
  t: new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10), label: `第${index + 1}週`, open: 100, close: 100, high: 105, low: 95, volume: 1,
}));

test("日 K 上同一週內、同一價位的短線切到週 K：兩端落在同一根，仍畫成看得到的一小段，也點得到（趨勢線與射線都一樣）", () => {
  for (const kind of ["trend", "ray"]) {
    const { chart, changes, selections, tap, drag, drawnLines } = createChart();
    chart.setDrawings([savedLine(kind, ["2026-02-16", 100], ["2026-02-19", 100])]); // 週一到週四
    chart.setData(WEEKLY(), { count: 16, unit: "週" }); // 兩端都在 02-20 那一根：(154.9375, 164)
    assertLines(drawnLines(), [[[152.9375, 164], [156.9375, 164]]], kind);
    tap(155, 164);
    assert.deepEqual(selections, ["saved-two-01"], kind);
    drag([155, 164], [175, 164]); // 在週 K 上往右拖一根：兩端會變成同一天同一價位，這一步不成立，線留在原位
    assert.deepEqual(changes, [], kind);
  }
});

test("在週 K 上只把端點上下拖：那一端的日期維持原本的交易日，只有價位變", () => {
  const { chart, changes, tap, drag } = createChart();
  chart.setDrawings([savedLine("trend", ["2026-02-18", 97.15], ["2026-03-12", 102.85])]);
  chart.setData(WEEKLY(), { count: 16, unit: "週" }); // (154.9375, 241) → (215.3125, 87)
  tap(185, 164);
  drag([215, 87], [215, 164]); // 抓終點往下 77px
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-18", price: 97.15 }, { date: "2026-03-12", price: 100 }]]);
});

test("點了第一下之後圖上的資料換成不含起點那一天的範圍：下一下重新當第一下，不會之後怎麼點都沒反應", () => {
  const { chart, changes, tap } = createChart();
  chart.setTool("trend");
  tap(90, 241); // 2026-02-20
  chart.setData(dailyCandles().slice(60), { count: 20, unit: "日" }); // 只剩 2026-03-02 起的 20 根，每根 16.1px
  tap(90, 241);  // 第 5 根＝2026-03-07
  assert.deepEqual(changes, []);
  tap(250, 87);  // 第 15 根＝2026-03-17
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-03-07", price: 97.15 }, { date: "2026-03-17", price: 102.85 }]]);
});

// ---------- 票 04：矩形 ----------
// 兩個對角：(88.525, 87)＝2026-02-20、102.85 與 (249.525, 241)＝2026-03-12、97.15。寬 161、高 154。
// 邊框一筆畫完，四個邊依序是上、右、下、左，每個邊兩個點。
const BOX = [[88.525, 87], [249.525, 87], [249.525, 87], [249.525, 241], [249.525, 241], [88.525, 241], [88.525, 241], [88.525, 87]];
const RECT = (id = "saved-rect-01") => savedLine("rect", ["2026-02-20", 102.85], ["2026-03-12", 97.15], id);

test("選了矩形工具後點兩下對角：框出那段日期與價位的區間（邊框加底色），工具自動回一般模式；不論先點哪一角結果都一樣", () => {
  for (const [first, second] of [[[90, 87], [250, 241]], [[250, 241], [90, 87]], [[90, 241], [250, 87]], [[250, 87], [90, 241]]]) {
    const { chart, changes, tools, tap, drawnLines, drawnFills, drawnLabels } = createChart();
    chart.setTool("rect");
    tap(...first);
    assert.deepEqual(changes, [], "只點了第一下：還沒有矩形");
    tap(...second);
    assert.equal(changes.length, 1);
    const { type, drawing } = changes[0];
    assert.equal(type, "add");
    // 存的一律是「左上、右下」：日期較早＋價位較高，日期較晚＋價位較低。
    assert.deepEqual(drawing, { id: drawing.id, kind: "rect", anchors: [{ date: "2026-02-20", price: 102.85 }, { date: "2026-03-12", price: 97.15 }] });
    assert.deepEqual(tools, ["rect", null]);
    assertLines(drawnLines(), [BOX]);
    assertLines(drawnFills(), [[[88.525, 87], [161, 154]]], "底色");
    assert.deepEqual(drawnLabels(), []);
  }
});

test("矩形點了第一下之後有預覽：起點有記號，框跟著游標；Esc 取消不留東西", () => {
  const { chart, changes, tap, key, drawnLines, drawnFills, drawnHandles } = createChart();
  chart.setTool("rect");
  tap(250, 241);
  assertHandles(drawnHandles(), [[249.525, 241]]);
  chart.canvas.dispatch("pointermove", { clientX: 90, clientY: 87 });
  assertLines(drawnLines(), [BOX]);
  assertLines(drawnFills(), [[[88.525, 87], [161, 154]]]);
  key("Escape");
  assertLines(drawnLines(), []);
  assertLines(drawnFills(), []);
  assert.deepEqual(changes, []);
});

test("矩形的底色與邊框都畫在布林通道、K 棒、均線之前：不蓋住它們", () => {
  const { chart, strokes, reset } = createChart();
  chart.setDrawings([RECT()]);
  reset(); chart.draw();
  const first = color => strokes.findIndex(stroke => stroke.color === color);
  assert.ok(first(DRAW) >= 0);
  for (const color of ["--dim", "--up", "--accent"]) assert.ok(first(color) > first(DRAW), `${color} 疊在矩形上面`);
});

test("矩形部分在畫面外：只畫畫面內的邊與底色，不沿著繪圖區邊緣多畫一條；整個在畫面外就不畫", () => {
  const { chart, drawnLines, drawnFills } = createChart();
  chart.setDrawings([RECT()]);
  chart.zoom(0.5); // 最後 20 根：左邊在 x＝−148.95（畫面外），右邊在 x＝173.05
  assertLines(drawnLines(), [[[4, 87], [173.05, 87], [173.05, 87], [173.05, 241], [173.05, 241], [4, 241]]], "上、右、下三個邊，左邊在畫面外");
  assertLines(drawnFills(), [[[4, 87], [169.05, 154]]]);
  chart.zoom(2);

  // 上緣 111.4 在畫面上方之外（y＝−144）、下緣 100（y＝164）：上邊不畫，左右兩邊從上緣 y=10 開始。
  chart.setDrawings([savedLine("rect", ["2026-02-20", 111.4], ["2026-03-12", 100])]);
  assertLines(drawnLines(), [[[249.525, 10], [249.525, 164], [249.525, 164], [88.525, 164], [88.525, 164], [88.525, 10]]]);
  assertLines(drawnFills(), [[[88.525, 10], [161, 154]]]);

  chart.setDrawings([savedLine("rect", ["2026-01-11", 102.85], ["2026-01-21", 97.15])]); // 整個在左邊之外
  assertLines(drawnLines(), []);
  assertLines(drawnFills(), []);
});

test("矩形切到週 K、月 K：起訖落在包含那兩天的那兩根，上下價位不變", () => {
  const { chart, drawnLines } = createChart();
  chart.setDrawings([savedLine("rect", ["2026-02-18", 102.85], ["2026-03-12", 97.15])]);
  const bars = (count, time) => Array.from({ length: count }, (_, index) => ({ t: time(index), label: `第${index + 1}根`, open: 100, close: 100, high: 105, low: 95, volume: 1 }));
  chart.setData(bars(16, index => new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10)), { count: 16, unit: "週" });
  assertLines(drawnLines(), [[[154.9375, 87], [215.3125, 87], [215.3125, 87], [215.3125, 241], [215.3125, 241], [154.9375, 241], [154.9375, 241], [154.9375, 87]]], "週 K");
  chart.setData(bars(16, index => new Date(Date.UTC(2025, index, 28)).toISOString().slice(0, 10)), { count: 16, unit: "月" });
  assertLines(drawnLines(), [[[275.6875, 87], [295.8125, 87], [295.8125, 87], [295.8125, 241], [295.8125, 241], [275.6875, 241], [275.6875, 241], [275.6875, 87]]], "月 K");
});

// 本人 2026-11-16 看過樣本後選 B：只有點邊線才選取矩形，框裡的 K 棒照常可以點。
test("點矩形的邊線才選取（四個角各一個把手）；點框的裡面照舊選 K 棒；框裡有線時點在線旁選到的是線", () => {
  const { chart, selections, tap, drawnHandles } = createChart();
  chart.setDrawings([saved(100), RECT()]); // 水平線 y=164 穿過矩形
  tap(169, 200); // 矩形內部，離四個邊都很遠
  assert.deepEqual(selections, []);
  assert.equal(chart.selection, 60, "框裡的 K 棒照常選得到");
  tap(169, 89);  // 上邊線內側 2px
  assert.deepEqual(selections, ["saved-rect-01"]);
  assertHandles(drawnHandles(), [[88.525, 87], [249.525, 241], [88.525, 241], [249.525, 87]]);
  tap(169, 166); // 矩形內部，離水平線 2px
  assert.deepEqual(selections, ["saved-rect-01", "saved-line-01"]);
  tap(252, 200); // 右邊線外側 2.5px
  assert.deepEqual(selections.at(-1), "saved-rect-01");
  tap(258, 200); // 右邊框外側 8.5px：點不到，取消選取
  assert.deepEqual(selections.at(-1), null);
  tap(262, 200, "touch"); // 手指 12.5px：點得到
  assert.deepEqual(selections.at(-1), "saved-rect-01");
});

test("拖已選取的矩形內部：整個平移，大小不變；拖任一個角：只有那個角移動，對角不動；放開各儲存一次；沒選取的框從裡面拖只是平移圖表", () => {
  const { chart, changes, tap, drag, drawnLines } = createChart();
  chart.setDrawings([RECT()]);
  const before = chart.view.end;
  drag([169, 200], [249, 200]); // 還沒選取：從框裡拖是平移圖表（10 根）
  assert.equal(chart.view.end, before - 10);
  assert.deepEqual(changes, []);
  chart.pan(10);
  tap(169, 88);
  const end = chart.view.end;
  drag([169, 200], [129, 123]); // 往左 40px＝5 根、往上 77px＝＋2.85
  assert.deepEqual(changes, [{ type: "update", drawing: { id: "saved-rect-01", kind: "rect", anchors: [{ date: "2026-02-15", price: 105.7 }, { date: "2026-03-07", price: 100 }] } }]);
  assert.equal(chart.view.end, end, "圖表沒有跟著平移");
  // 現在是 (48.275, 10) ～ (209.275, 164)。

  drag([209, 163], [129, 86]); // 抓右下角往左 80px、往上 77px → 第 15 根（2026-02-25）、102.85
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-15", price: 105.7 }, { date: "2026-02-25", price: 102.85 }]);

  drag([129, 11], [209, 11]); // 抓右上角（x 在第二個錨點、y 在第一個錨點）往右 80px → 第 25 根（2026-03-07），價位不變
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-15", price: 105.7 }, { date: "2026-03-07", price: 102.85 }]);

  drag([48, 87], [48, 164]); // 抓左下角（x 在第一個錨點、y 在第二個錨點）往下 77px → 100，日期不變
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-15", price: 105.7 }, { date: "2026-03-07", price: 100 }]);
  assertLines(drawnLines(), [[[48.275, 10], [209.275, 10], [209.275, 10], [209.275, 164], [209.275, 164], [48.275, 164], [48.275, 164], [48.275, 10]]]);
  assert.equal(changes.length, 4);
});

test("刪除選取的矩形：框與底色都消失並通知儲存端刪除", () => {
  const { chart, changes, tap, key, drawnLines, drawnFills } = createChart();
  chart.setDrawings([RECT()]);
  tap(169, 88);
  key("Delete");
  assert.deepEqual(changes.map(change => [change.type, change.drawing.id]), [["remove", "saved-rect-01"]]);
  assertLines(drawnLines(), []);
  assertLines(drawnFills(), []);
});

// ---------- 票 04：審查後補的邊界情況 ----------

test("矩形的底色很淡（不透明度不超過一成五），選取與預覽時也一樣：不會把後面畫的 K 棒與通道蓋到看不清", () => {
  const { chart, fills, reset, tap } = createChart();
  chart.setDrawings([RECT()]);
  const alphas = () => { reset(); chart.draw(); return fills.filter(fill => fill.color === DRAW).map(fill => fill.alpha); };
  assert.deepEqual(alphas().map(alpha => alpha > 0 && alpha <= 0.15), [true]);
  tap(169, 88);
  assert.deepEqual(alphas().map(alpha => alpha > 0 && alpha <= 0.15), [true], "選取時");
  chart.setTool("rect");
  tap(90, 87);
  chart.canvas.dispatch("pointermove", { clientX: 250, clientY: 241 });
  assert.deepEqual(alphas().map(alpha => alpha > 0 && alpha <= 0.15), [true, true], "預覽時");
});

test("矩形要有寬也有高：兩下在同一根或同一價位時不算；把角落拖到沒有寬度的位置時停在上一個位置", () => {
  const { chart, changes, tools, tap, drag } = createChart();
  chart.setTool("rect");
  tap(90, 87);
  tap(90, 241);  // 同一根：沒有寬度
  tap(250, 87);  // 同一價位：沒有高度
  assert.deepEqual(changes, []);
  assert.deepEqual(tools, ["rect"], "工具與第一下留著");
  tap(250, 241);
  assert.equal(changes.length, 1);

  tap(169, 88);
  drag([250, 240], [90, 240]); // 右下角往左拖到左邊那一根
  assert.equal(changes.length, 1, "沒有寬度的框不成立，不儲存");
});

test("手指點在矩形裡面、離線 10px：選到的是線；手指點在邊線內側 12px：選到矩形", () => {
  const { chart, selections, tap } = createChart();
  chart.setDrawings([saved(100), RECT()]);
  tap(169, 174, "touch");
  assert.deepEqual(selections, ["saved-line-01"]);
  tap(169, 229, "touch"); // 下邊線（y=241）內側 12px
  assert.deepEqual(selections, ["saved-line-01", "saved-rect-01"]);
  tap(169, 205, "touch"); // 離下邊線 36px、離水平線 41px：都點不到
  assert.deepEqual(selections.at(-1), null);
});

test("矩形比畫面還大（四個邊都在畫面外）：底色照畫，但點圖不會選到它，照舊選 K 棒、拖曳照舊平移", () => {
  const { chart, selections, tap, drag, drawnLines, drawnFills } = createChart();
  chart.setDrawings([savedLine("rect", ["2026-01-21", 140], ["2026-03-21", 80])]);
  chart.pan(-10); // 畫面是第 30～69 根、88.6～134.2：矩形的四個邊都在外面
  assertLines(drawnLines(), []);
  assertLines(drawnFills(), [[[4, 10], [322, 308]]]);
  tap(150, 150);
  assert.deepEqual(selections, []);
  assert.equal(chart.selection, 48);
  const end = chart.view.end;
  drag([150, 150], [230, 150]);
  assert.equal(chart.view.end, end - 10);
});

// ---------- 票 05：黃金分割 ----------
// 第一點是 1、第二點是 0。由低往高拉：第一點 (88.525, 241)＝2026-02-20、97.15，第二點 (249.525, 87)＝2026-03-12、102.85；
// 兩點相差 5.7 元＝154px。各比例的價位＝102.85 − 比例×5.7，y＝87＋比例×154：
//   0 → 102.85（87）、0.236 → 101.5048（123.34）、0.382 → 100.6726（145.83）、0.5 → 100（164）、
//   0.618 → 99.3274（182.17）、0.786 → 98.3698（208.04）、1 → 97.15（241）。
// 七條橫線和水平線一樣對在整數像素的正中央（y 取整數＋0.5），一筆畫完，由 0 排到 1；每條從靠左的那一點畫到繪圖區右緣 x=326。
// 兩點之間另有一條連線（看得出量的是哪一段），畫在七條橫線之前。
const fibLevels = (from, ys, to = 326) => ys.flatMap(y => [[from, y], [to, y]]);
const FIB_UP_YS = [87.5, 123.5, 146.5, 164.5, 182.5, 208.5, 241.5];
const FIB_UP_LABELS = ["0 (102.85)", "0.236 (101.5)", "0.382 (100.67)", "0.5 (100)", "0.618 (99.33)", "0.786 (98.37)", "1 (97.15)"];
const FIB = (id = "saved-fib-01") => savedLine("fib", ["2026-02-20", 97.15], ["2026-03-12", 102.85], id);
// 1 在 97.15（y=241）、0 在 101.63（y＝10＋4.07/11.4×308＝119.96）時的七條線。
const FIB_SHORT_YS = [120.5, 149.5, 166.5, 180.5, 195.5, 215.5, 241.5];

test("選了黃金分割工具後點兩下：畫出 0 到 1 七條橫線（0 在第二點、1 在第一點），每條標出比例與價位，工具自動回一般模式", () => {
  const { chart, changes, tools, tap, drawnLines, drawnLabels } = createChart();
  chart.setTool("fib");
  tap(90, 241);
  assert.deepEqual(changes, [], "只點了第一下：還沒有東西");
  assert.deepEqual(tools, ["fib"]);
  tap(250, 87);
  assert.equal(changes.length, 1);
  const [{ type, drawing }] = changes;
  assert.equal(type, "add");
  assert.match(drawing.id, ID_RE);
  assert.deepEqual(drawing, { id: drawing.id, kind: "fib", anchors: [{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-12", price: 102.85 }] });
  assert.deepEqual(tools, ["fib", null], "畫完自動回一般模式");

  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]], fibLevels(88.525, FIB_UP_YS)]);
  assert.deepEqual(drawnLabels().sort(), FIB_UP_LABELS);
});

test("由高往低拉：比例方向跟著拉的方向（1 在高點、0 在低點）；由右往左拉：錨點照點的順序存，橫線仍從靠左的那一點畫到右緣", () => {
  const { chart, changes, tap, drawnLines, drawnLabels } = createChart();
  chart.setTool("fib");
  tap(90, 87);    // 第一點：2026-02-20、102.85（1）
  tap(250, 241);  // 第二點：2026-03-12、97.15（0）
  assert.deepEqual(changes[0].drawing.anchors, [{ date: "2026-02-20", price: 102.85 }, { date: "2026-03-12", price: 97.15 }]);
  // 價位＝97.15＋比例×5.7，y＝241−比例×154：0.236 → 98.4952（204.66）、0.382 → 99.3274（182.17）、0.5 → 100（164）、
  // 0.618 → 100.6726（145.83）、0.786 → 101.6302（119.96）。
  assertLines(drawnLines(), [[[88.525, 87], [249.525, 241]], fibLevels(88.525, [241.5, 205.5, 182.5, 164.5, 146.5, 120.5, 87.5])]);
  assert.deepEqual(drawnLabels().sort(), ["0 (97.15)", "0.236 (98.5)", "0.382 (99.33)", "0.5 (100)", "0.618 (100.67)", "0.786 (101.63)", "1 (102.85)"]);

  // 由右往左拉（先點右下、再點左上）：1 在右下那一點（97.15）、0 在左上（102.85），和由低往高拉的七條線一樣。
  chart.setDrawings([]);
  chart.setTool("fib");
  tap(250, 241);
  tap(90, 87);
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-03-12", price: 97.15 }, { date: "2026-02-20", price: 102.85 }], "不重排");
  assertLines(drawnLines(), [[[249.525, 241], [88.525, 87]], fibLevels(88.525, FIB_UP_YS)]);
  assert.deepEqual(drawnLabels().sort(), FIB_UP_LABELS);
});

test("黃金分割點了第一下之後有預覽：起點有記號，七條線跟著游標，還不標文字、不儲存；Esc 取消不留東西", () => {
  const { chart, changes, tools, tap, key, drawnLines, drawnLabels, drawnHandles } = createChart();
  chart.setTool("fib");
  tap(90, 241);
  assertHandles(drawnHandles(), [[88.525, 241]]);
  assertLines(drawnLines(), [], "游標還在起點上：沒有預覽");
  chart.canvas.dispatch("pointermove", { clientX: 250, clientY: 87 });
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]], fibLevels(88.525, FIB_UP_YS)]);
  // 游標移到第 20 根、y=120（101.6286 取到分＝101.63 → y＝119.96）：兩點相差 121.04px，
  // y＝119.96＋比例×121.04 → 119.96、148.53、166.20、180.48、194.76、215.10、241。
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 120 });
  assertLines(drawnLines(), [[[88.525, 241], [169.025, 119.96]], fibLevels(88.525, FIB_SHORT_YS)]);
  assert.deepEqual(drawnLabels(), []);
  assert.deepEqual(changes, []);
  key("Escape");
  assertLines(drawnLines(), []);
  assertHandles(drawnHandles(), []);
  assert.deepEqual(tools, ["fib", null]);
  assert.deepEqual(changes, []);
});

test("兩點在同一個價位（或近到七條線疊在一起）時不成立：第二下不算，工具與起點留著；同一根、不同價位則可以", () => {
  const { chart, changes, tools, tap, drawnLines } = createChart();
  chart.setTool("fib");
  tap(90, 241);
  tap(250, 241);  // 同一個價位
  tap(250, 243);  // 只差 2px
  assert.deepEqual(changes, []);
  assert.deepEqual(tools, ["fib"]);
  tap(90, 120);   // 同一根、往上 121px（101.63）：七條線從那一根往右畫
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-20", price: 97.15 }, { date: "2026-02-20", price: 101.63 }]]);
  assertLines(drawnLines(), [[[88.525, 241], [88.525, 119.96]], fibLevels(88.525, FIB_SHORT_YS)]);
});

test("黃金分割的線畫在布林通道、K 棒、均線之前，不擋住它們；橫線到繪圖區右緣為止，不進價格軸", () => {
  const { chart, strokes, reset } = createChart();
  chart.setDrawings([FIB()]);
  reset(); chart.draw();
  const first = color => strokes.findIndex(stroke => stroke.color === color);
  assert.ok(first(DRAW) >= 0);
  for (const color of ["--dim", "--up", "--accent"]) assert.ok(first(color) > first(DRAW), `${color} 疊在黃金分割上面`);
  assert.ok(strokes.filter(stroke => stroke.color === DRAW).every(stroke => stroke.points.every(([x]) => x >= 4 && x <= 326)));
});

// 標示用的假字寬是每個字 7px；墊底那一塊左右各多 4px、高 14px，文字的 x 是那一塊的左緣＋4、y 是正中央＋0.5。
function fibLabels({ chart, texts, reset }) {
  reset(); chart.draw();
  return texts.filter(text => text.color === DRAW).map(({ text, x, y }) => ({ text, left: x - 4, right: x + text.length * 7 + 4, cy: y - 0.5 }));
}

test("每條線的標示貼在線的左端外側；左邊放不下就留在繪圖區內；都不進右側價格軸、不出繪圖區上下緣，彼此不重疊", () => {
  const context = createChart();
  // 起點在第 30 根（x＝249.525）：左邊有位置，標示整個在線的左端之外。
  context.chart.setDrawings([savedLine("fib", ["2026-03-12", 97.15], ["2026-03-17", 102.85])]);
  let labels = fibLabels(context);
  assert.deepEqual(labels.map(label => label.text).sort(), FIB_UP_LABELS);
  for (const label of labels) assert.ok(label.left >= 4 && label.right <= 249.525 - 4 + 0.01, `${label.text} 在線的左端外側：${JSON.stringify(label)}`);
  // 依 FIB_UP_LABELS 的順序（0 到 1）。字數 10、13、14、9、13、13、9 → 墊底那一塊寬 78、99、106、71、99、99、71。
  const positions = key => FIB_UP_LABELS.map(text => labels.find(label => label.text === text)[key]);
  assertNumbers(positions("cy"), [87, 123.344, 145.828, 164, 182.172, 208.044, 241], "在外側時上下對齊各自的線");
  assertNumbers(positions("right"), Array(7).fill(245.525), "右緣都貼在線的左端外 4px");
  assertNumbers(positions("left"), [167.525, 146.525, 139.525, 174.525, 146.525, 146.525, 174.525]);

  // 起點在第 10 根（x＝88.525）：左邊放不下最長的標示（寬 106px），整組改貼繪圖區左緣（x=6）、
  // 標在各條線的上方（正中央在線上方 9px，和水平線的價位一樣），不壓住線頭與把手。
  context.chart.setDrawings([FIB()]);
  labels = fibLabels(context);
  assert.equal(labels.length, 7);
  for (const label of labels) assert.ok(label.left >= 4 && label.right <= 326, `${label.text} 在繪圖區內：${JSON.stringify(label)}`);
  assertNumbers(positions("cy"), [78, 114.344, 136.828, 155, 173.172, 199.044, 232], "放不下時標在線的上方");
  assertNumbers(positions("left"), Array(7).fill(6), "整組對齊左緣");
  // 最上面那條貼著繪圖區上緣（105.7 → y=10）：上方沒有位置，改標在線的下方（正中央在線下方 10px）。
  context.chart.setDrawings([savedLine("fib", ["2026-02-20", 100], ["2026-03-12", 105.7])]);
  assert.equal(fibLabels(context).find(label => label.text === "0 (105.7)").cy, 20);

  // 起點在最後一根（第 39 根，x＝321.975）而且貼著上下緣：標示仍在繪圖區內。
  context.chart.setDrawings([savedLine("fib", ["2026-03-21", 94.3], ["2026-03-21", 105.7])]);
  labels = fibLabels(context);
  assert.equal(labels.length, 7);
  for (const label of labels) {
    assert.ok(label.left >= 4 && label.right <= 326, `${label.text} 不進價格軸：${JSON.stringify(label)}`);
    assert.ok(label.cy - 7 >= 10 && label.cy + 7 <= 318, `${label.text} 不出上下緣：${JSON.stringify(label)}`);
  }
  for (const one of labels) for (const other of labels) assert.ok(one === other || Math.abs(one.cy - other.cy) >= 14, `${one.text} 與 ${other.text} 不重疊`);
});

test("七條線擠在一起時只標放得下的：先標 0 與 1，再來 0.618、0.382，然後才是其他；線本身七條都還在", () => {
  const { chart, drawnLines, drawnLabels } = createChart();
  // 100 → y=164、100.74 → y=144：七條線擠在 20px 內，只放得下頭尾兩個標示。
  chart.setDrawings([savedLine("fib", ["2026-02-20", 100], ["2026-03-12", 100.74])]);
  assert.deepEqual(drawnLabels().sort(), ["0 (100.74)", "1 (100)"]);
  assert.equal(drawnLines().at(-1).length, 14, "七條線都畫");
  // 100 → y=164、101.48 → y=124：40px，中間多放得下一個，給 0.618（101.48−0.618×1.48＝100.5654，y=148.7）。
  chart.setDrawings([savedLine("fib", ["2026-02-20", 100], ["2026-03-12", 101.48])]);
  assert.deepEqual(drawnLabels().sort(), ["0 (101.48)", "0.618 (100.57)", "1 (100)"]);
  // 100 → y=164、103.7 → y=64：100px，各條相距 23.6、14.6、11.8、11.8、16.8、21.4px。
  // 0.618（y=125.8）與 0.382（y=102.2）相距 23.6px 都放得下；0.5（y=114）夾在中間放不下；0.236（y=87.6）、0.786（y=142.6）放得下。
  chart.setDrawings([savedLine("fib", ["2026-02-20", 100], ["2026-03-12", 103.7])]);
  assert.deepEqual(drawnLabels().sort(), ["0 (103.7)", "0.236 (102.83)", "0.382 (102.29)", "0.618 (101.41)", "0.786 (100.79)", "1 (100)"]);
});

test("平移、縮放後七條線仍在各自的價位；起點在畫面左邊之外時從左緣畫起；價位在畫面外的那幾條不畫；整組在畫面外就不畫", () => {
  const { chart, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([FIB()]);
  // 放大成最後 20 根（每根 16.1px）：第一點在左邊之外（x＝−148.95）、第二點在 x＝173.05；價格範圍不變。
  // 兩點的連線在左緣的高度是 241−154×152.95/322＝167.85。
  chart.zoom(0.5);
  assertLines(drawnLines(), [[[4, 167.85], [173.05, 87]], fibLevels(4, FIB_UP_YS)]);
  assert.deepEqual(drawnLabels().sort(), FIB_UP_LABELS);

  // 縮回 40 根再往左平移 10 根（第 30～69 根，價格座標 88.6～134.2，每元 6.754px）：102.85 → y＝221.75、97.15 → y＝260.25，相差 38.5px。
  // y＝221.75＋比例×38.5 → 221.75、230.84、236.46、241、245.54、252.01、260.25。第一點在 x＝169.025，第二點在右緣之外（330.025）。
  chart.zoom(2);
  chart.pan(-10);
  assertLines(drawnLines(), [[[169.025, 260.25], [326, 222.7125]], fibLevels(169.025, [222.5, 231.5, 236.5, 241.5, 246.5, 252.5, 260.5])]);
  assert.deepEqual(drawnLabels().sort(), ["0 (102.85)", "0.618 (99.33)", "1 (97.15)"], "擠在 38.5px 內：只標放得下的");

  // 再往左平移 20 根（第 10～49 根）：兩點都在畫面右邊之外，整組不畫。
  chart.pan(-20);
  assertLines(drawnLines(), []);
  assert.deepEqual(drawnLabels(), []);
  chart.pan(30);
  assertLines(drawnLines(), [[[88.525, 241], [249.525, 87]], fibLevels(88.525, FIB_UP_YS)]);

  // 1 在 100、0 在 111.4（畫面上方之外）：0、0.236（108.71）、0.382（107.05）都在畫面外，不畫；
  // 0.5 → 105.7（y=10，正好在上緣）、0.618 → 104.3548（46.34）、0.786 → 102.4396（98.09）、1 → 100（164）。連線到上緣為止。
  chart.setDrawings([savedLine("fib", ["2026-02-20", 100], ["2026-03-12", 111.4])]);
  assertLines(drawnLines(), [[[88.525, 164], [169.025, 10]], fibLevels(88.525, [10.5, 46.5, 98.5, 164.5])]);
  assert.deepEqual(drawnLabels().sort(), ["0.5 (105.7)", "0.618 (104.35)", "0.786 (102.44)", "1 (100)"]);

  chart.setDrawings([savedLine("fib", ["2026-02-20", 110], ["2026-03-12", 111.4])]); // 整組在畫面上方之外
  assertLines(drawnLines(), []);
});

test("黃金分割切到週 K、月 K：七條線的價位不變，起點落在包含那一天的那一根；切回日 K 回到原位", () => {
  const { chart, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([savedLine("fib", ["2026-02-18", 97.15], ["2026-03-12", 102.85])]);
  const bars = (count, time) => Array.from({ length: count }, (_, index) => ({ t: time(index), label: `第${index + 1}根`, open: 100, close: 100, high: 105, low: 95, volume: 1 }));
  chart.setData(bars(16, index => new Date(Date.UTC(2026, 0, 2 + index * 7)).toISOString().slice(0, 10)), { count: 16, unit: "週" });
  assertLines(drawnLines(), [[[154.9375, 241], [215.3125, 87]], fibLevels(154.9375, FIB_UP_YS)], "週 K");
  assert.deepEqual(drawnLabels().sort(), FIB_UP_LABELS, "週 K");
  chart.setData(bars(16, index => new Date(Date.UTC(2025, index, 28)).toISOString().slice(0, 10)), { count: 16, unit: "月" });
  assertLines(drawnLines(), [[[275.6875, 241], [295.8125, 87]], fibLevels(275.6875, FIB_UP_YS)], "月 K");
  assert.deepEqual(drawnLabels().sort(), FIB_UP_LABELS, "月 K");
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  assertLines(drawnLines(), [[[72.425, 241], [249.525, 87]], fibLevels(72.425, FIB_UP_YS)], "切回日 K");
});

test("點在任一條橫線或兩點的連線上就選取黃金分割：兩個點各出現一個把手；線與線之間、起點左邊點不到", () => {
  const { chart, changes, selections, tap, drawnHandles } = createChart();
  chart.setDrawings([FIB()]);
  tap(300, 174);  // 0.5 那條（y=164）下方 10px、0.618 那條（y=182.17）上方 8px：滑鼠點不到
  assert.deepEqual(selections, []);
  assert.equal(chart.selection, 76, "照舊選 K 棒");
  tap(40, 164);   // 0.5 那條的高度，但在起點（x=88.525）左邊 48px
  assert.deepEqual(selections, []);
  tap(300, 168);  // 0.5 那條下方 4px
  assert.deepEqual(selections, ["saved-fib-01"]);
  assertHandles(drawnHandles(), [[88.525, 241], [249.525, 87]]);
  assert.deepEqual(changes, [], "選取不是資料變動");

  tap(300, 280);
  tap(300, 174, "touch"); // 手指離 0.618 那條 8px：點得到
  tap(300, 280, "touch");
  tap(200, 135);  // 兩點的連線在 x=200 的高度是 134.4；上下兩條橫線（123.34、145.83）都在 10px 以外：點到的是連線
  assert.deepEqual(selections, ["saved-fib-01", null, "saved-fib-01", null, "saved-fib-01"]);
});

test("拖已選取的黃金分割的線身：整組平移（兩點移動同樣的根數與價差）；拖任一端的把手：只有那一端移動，比例跟著重算；放開各儲存一次", () => {
  const { chart, changes, tap, drag, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([FIB()]);
  tap(300, 164);
  const end = chart.view.end;
  drag([300, 172], [260, 95], { pointerType: "touch" }); // 手指抓在 0.5 那條下方 8px：往左 40px＝5 根、往上 77px＝＋2.85
  assert.deepEqual(changes, [{ type: "update", drawing: { id: "saved-fib-01", kind: "fib", anchors: [{ date: "2026-02-15", price: 100 }, { date: "2026-03-07", price: 105.7 }] } }]);
  assert.equal(chart.view.end, end, "圖表沒有跟著平移");
  // 現在是 (48.275, 164) → (209.275, 10)：七條線整組往上 77px。
  assertLines(drawnLines(), [[[48.275, 164], [209.275, 10]], fibLevels(48.275, [10.5, 46.5, 69.5, 87.5, 105.5, 131.5, 164.5])]);

  drag([209, 11], [129, 44]); // 抓第二點（0）往左 80px、往下 33px → 第 15 根（2026-02-25）、y=43（104.4786 取到分＝104.48 → y＝42.96）
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-15", price: 100 }, { date: "2026-02-25", price: 104.48 }]);
  // 1 在 100（y=164）、0 在 104.48（y=42.96），相差 121.04px：y＝42.96＋比例×121.04 → 42.96、71.53、89.20、103.48、117.76、138.10、164。
  // 價位＝104.48−比例×4.48 → 104.48、103.4227、102.7686、102.24、101.7114、100.9587、100。
  assertLines(drawnLines(), [[[48.275, 164], [128.775, 42.96]], fibLevels(48.275, [43.5, 72.5, 89.5, 103.5, 118.5, 138.5, 164.5])]);
  assert.deepEqual(drawnLabels().sort(), ["0 (104.48)", "0.236 (103.42)", "0.382 (102.77)", "0.5 (102.24)", "0.618 (101.71)", "0.786 (100.96)", "1 (100)"]);

  drag([48, 164], [48, 241]); // 抓第一點（1）往下 77px → 97.15，日期不變
  assert.deepEqual(changes.at(-1).drawing.anchors, [{ date: "2026-02-15", price: 97.15 }, { date: "2026-02-25", price: 104.48 }]);
  assert.equal(changes.length, 3);
});

test("把一端拖到和另一端同一個價位：那一端停在上一個還分得出七條線的位置；拖動中按 Esc 回到原樣不儲存", () => {
  const { chart, changes, tap, key, drawnLines } = createChart();
  chart.setDrawings([FIB()]);
  tap(300, 164);
  chart.canvas.dispatch("pointerdown", { clientX: 250, clientY: 88 });
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 121 }); // 先往左 80px、往下 33px：第 20 根、101.63
  chart.canvas.dispatch("pointermove", { clientX: 170, clientY: 242 }); // 再往下拖到 97.15：和第一點同價位，不成立
  chart.canvas.dispatch("pointerup", { clientX: 170, clientY: 242 });
  assert.deepEqual(changes.map(change => change.drawing.anchors), [[{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-02", price: 101.63 }]]);

  chart.canvas.dispatch("pointerdown", { clientX: 170, clientY: 121 });
  chart.canvas.dispatch("pointermove", { clientX: 250, clientY: 88 });
  key("Escape");
  chart.canvas.dispatch("pointerup", { clientX: 250, clientY: 88 });
  assert.equal(changes.length, 1, "Esc：不儲存");
  assertLines(drawnLines(), [[[88.525, 241], [169.025, 119.96]], fibLevels(88.525, FIB_SHORT_YS)]);
});

test("刪除選取的黃金分割：七條線與標示都消失並通知儲存端刪除，別的線不受影響", () => {
  const { chart, changes, selections, tap, key, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([FIB(), saved(105)]); // 水平線 105 → y＝10＋0.7/11.4×308＝28.9
  tap(300, 241);
  assert.deepEqual(selections, ["saved-fib-01"]);
  key("Delete");
  assert.deepEqual(changes, [{ type: "remove", drawing: { id: "saved-fib-01", kind: "fib", anchors: [{ date: "2026-02-20", price: 97.15 }, { date: "2026-03-12", price: 102.85 }] } }]);
  assertLines(drawnLines(), [[[4, 29.5], [326, 29.5]]]);
  assert.deepEqual(drawnLabels(), ["105"]);
});

// ---------- 票 05：審查後補的 ----------

test("兩點的連線是襯在後面的細虛線，比七條橫線淡、比它們細；橫線是實線。選取時兩者都變粗；預覽時橫線也是虛線", () => {
  const { chart, strokes, reset, tap } = createChart();
  const styles = () => {
    reset(); chart.draw();
    return strokes.filter(stroke => stroke.color === DRAW && stroke.points.length)
      .map(stroke => ({ points: stroke.points.length, dashed: stroke.dash.length > 0, alpha: stroke.alpha, width: stroke.width }));
  };
  chart.setDrawings([FIB()]);
  const [guide, levels] = styles();
  assert.deepEqual([guide.points, levels.points], [2, 14], "先畫連線，再畫七條橫線");
  assert.deepEqual([guide.dashed, levels.dashed], [true, false]);
  assert.ok(guide.alpha < levels.alpha && guide.width < levels.width, JSON.stringify({ guide, levels }));

  tap(300, 164);
  const [pickedGuide, pickedLevels] = styles();
  assert.ok(pickedGuide.width > guide.width && pickedLevels.width > levels.width, "選取時連線與橫線都變粗");
  assert.deepEqual([pickedGuide.dashed, pickedLevels.dashed], [true, false]);

  chart.setDrawings([]);
  chart.setTool("fib");
  tap(90, 241);
  chart.canvas.dispatch("pointermove", { clientX: 250, clientY: 87 });
  assert.deepEqual(styles().map(style => [style.points, style.dashed]), [[2, true], [14, true]], "預覽");
});

test("黃金分割的起點比圖上第一根還早時先不畫；五年歷史接上後出現，七條線在原本的價位、從繪圖區左緣畫起", () => {
  const { chart, drawnLines, drawnLabels } = createChart();
  chart.setDrawings([savedLine("fib", ["2025-12-20", 97.15], ["2026-03-12", 102.85])]); // 第一點在還沒載入的歷史裡
  assertLines(drawnLines(), [], "歷史還沒接上：位置算不出來，先不畫");
  assert.deepEqual(drawnLabels(), []);

  const older = Array.from({ length: 30 }, (_, index) => {
    const date = new Date(Date.UTC(2025, 11, 2 + index)).toISOString().slice(0, 10);
    return { t: date, label: date, open: 100, close: 100, high: 105, low: 95, volume: 1 };
  });
  chart.setData([...older, ...dailyCandles()], { count: 40, unit: "日" });
  // 第一點 2025-12-20 是第 18 根，在畫面第一根（第 70 根）左邊：x＝−410.575。連線在左緣的高度是 241−154×414.575/660.1＝144.28。
  assertLines(drawnLines(), [[[4, 144.28], [249.525, 87]], fibLevels(4, FIB_UP_YS)]);
  assert.deepEqual(drawnLabels().sort(), FIB_UP_LABELS);
});
