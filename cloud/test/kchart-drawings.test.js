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
  let path = [], arcs = [];
  const context2d = new Proxy({}, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "beginPath") return () => { path = []; arcs = []; };
      if (name === "arc") return (x, y, radius) => { arcs.push([x, y, radius]); };
      if (name === "moveTo" || name === "lineTo") return (x, y) => { path.push([x, y]); };
      if (name === "stroke") return () => { strokes.push({ color: target.strokeStyle, points: path.slice(), arcs: arcs.slice() }); };
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
  const changes = [], tools = [], selections = [];
  const chart = new KChart(new FakeElement(), {
    macd: false, height: () => 340,
    drawings: {
      onChange: change => changes.push(JSON.parse(JSON.stringify(change))), onTool: tool => tools.push(tool),
      onSelect: drawing => selections.push(drawing ? drawing.id : null),
    },
  });
  chart.setData(dailyCandles(), { count: 40, unit: "日" });
  const reset = () => { strokes.length = 0; texts.length = 0; };
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
  return { chart, changes, tools, selections, strokes, texts, reset, tap, drag, key, drawnLines, drawnLabels, drawnHandles };
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
