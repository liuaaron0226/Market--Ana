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
