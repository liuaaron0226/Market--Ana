// 首頁大盤 K 線（本人 2026-11-17）：「右手邊只顯示數字即可，幾日線顯示在上方(標示線的顏色以及名稱)……
// 還有我把圖放大的時候k線盡量也往上或往下至中我比較好看圖」。
// numbersOnly：右側價籤只寫數字、欄位窄一點；名稱與顏色由頁面寫在圖上方（legend: false 時畫布不寫左上角的均線名稱）。
// fitCandles：上下的範圍只看畫面上的 K 棒（與布林通道），判斷線不把範圍撐開；在範圍外的判斷線不畫線，價籤貼在上下緣加箭頭。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/kchart.js", import.meta.url);

function load(width) {
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
  return { KChart: sandbox.window.KChart, FakeElement, texts, lines };
}

// 收盤在 40,000 上下、高低在 39,000～41,500 之間的 60 根（大盤的位數）
function candles(count = 60) {
  return Array.from({ length: count }, (_, index) => {
    const open = 40_000 + (index % 9) * 120, close = open + (index % 2 ? 300 : -250);
    return { t: `d${index}`, label: `第${index + 1}根`, open, close, high: Math.max(open, close) + 80, low: Math.min(open, close) - 80, amount: 1, volume: 1 };
  });
}

function draw(options, overlays, width = 390) {
  const view = load(width);
  const chart = new view.KChart(new view.FakeElement(), { macd: false, height: () => 340, overlayLabels: "tags", ...options });
  chart.setData(candles(), { count: 60, overlays });
  const railLeft = chart.geo.l + chart.geo.pw;
  return { ...view, chart, railLeft, rail: view.texts.filter(item => item.x >= railLeft) };
}

const FIVE_DAY = { value: 41_200, label: "轉強線 五日線", short: "五日線", color: "red" };

test("numbersOnly：右側價籤只寫數字，不寫「五日線」「收盤」這些名稱", () => {
  const plain = draw({}, [FIVE_DAY]);
  const lean = draw({ numbersOnly: true }, [FIVE_DAY]);
  assert.ok(plain.rail.some(item => item.text === "五日線") && plain.rail.some(item => item.text === "收盤"), "原本有名稱");
  assert.ok(!lean.rail.some(item => item.text === "五日線" || item.text === "收盤"), "只寫數字時沒有名稱");
  assert.ok(lean.rail.some(item => item.text === "41,200"), "五日線的數字還在");
});

test("numbersOnly：右側欄窄一點，K 線的寬度多出來", () => {
  const plain = draw({}, [FIVE_DAY]);
  const lean = draw({ numbersOnly: true }, [FIVE_DAY]);
  assert.ok(lean.chart.geo.pw >= plain.chart.geo.pw + 20, `K 線區從 ${plain.chart.geo.pw} 變成 ${lean.chart.geo.pw}`);
});

test("numbersOnly：選了某一天，開高低收照樣寫得出名稱（四枚同底色，沒有名稱分不出來）", () => {
  const { chart, texts, railLeft } = draw({ numbersOnly: true }, [FIVE_DAY]);
  chart.hover = chart.selection = 50;
  chart.draw();
  for (const name of ["開", "高", "低", "收"]) assert.ok(texts.some(item => item.text === name), `有「${name}」`);
  assert.ok(texts.filter(item => ["開", "高", "低", "收"].includes(item.text)).every(item => item.x < railLeft + 4), "名稱往左借一點位置，不擠到數字");
});

test("legend: false：畫布左上角不寫均線與布林的名稱（頁面寫在圖上方）；legendItems 給頁面用", () => {
  const drawn = draw({}, []);
  const none = draw({ legend: false }, []);
  assert.ok(drawn.texts.some(item => item.text === "MA18" || item.text.startsWith("MA")), "原本畫布上有");
  assert.ok(!none.texts.some(item => item.text.startsWith("MA") || item.text === "布林20"), "不畫");
  const items = JSON.parse(JSON.stringify(none.chart.legendItems()));
  assert.deepEqual(items.map(item => item.name), [...none.chart.maPeriods.map(period => `MA${period}`), "布林20"]);
  assert.ok(items.every(item => typeof item.color === "string" && item.color));
});

test("fitCandles：上下範圍只看 K 棒，遠在外面的判斷線不把 K 棒擠到一邊", () => {
  const far = { value: 46_000, label: "轉強線 前高", short: "前高", color: "red" };
  const stretched = draw({}, [far]);
  const fitted = draw({ fitCandles: true }, [far]);
  assert.ok(stretched.chart.geo.hi > 45_900, "原本範圍被撐到判斷線");
  assert.ok(fitted.chart.geo.hi < 42_500 && fitted.chart.geo.lo > 38_500, `只看 K 棒：${fitted.chart.geo.lo}～${fitted.chart.geo.hi}`);
});

test("fitCandles：範圍外的判斷線不畫線，價籤貼在上下緣、數字前面加箭頭", () => {
  const { lines, rail, chart } = draw({ fitCandles: true, numbersOnly: true }, [
    { value: 46_000, label: "轉強線 前高", short: "前高", color: "red" },
    { value: 35_000, label: "轉弱線 前低", short: "前低", color: "green" },
    { value: 40_500, label: "收復線", short: "收復", color: "gold", tag: false },
  ]);
  assert.ok(!lines.some(line => line.stroke === "red" || line.stroke === "green"), "範圍外的兩條沒有畫線");
  assert.ok(lines.some(line => line.stroke === "gold"), "範圍內的照畫");
  assert.ok(rail.some(item => item.text === "▲46,000"), "上面那條：往上的箭頭");
  assert.ok(rail.some(item => item.text === "▼35,000"), "下面那條：往下的箭頭");
  const top = rail.find(item => item.text === "▲46,000"), bottom = rail.find(item => item.text === "▼35,000");
  assert.ok(top.y <= chart.geo.t + 20 && bottom.y >= chart.geo.t + chart.geo.ph - 20, "貼在上下緣");
});
