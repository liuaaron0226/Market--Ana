// 券商看盤版面（本人 2026-11-17，附聯發科 K 線截圖）：「我希望左右可以多一點空間可以放股票的k線，
// 不是被特殊標記的價格所佔用，另外你應該是給我完整的趨勢線不是只給一個價格說這是半年線。」
// inlineAxis：左右都不留價格欄，K 棒畫滿整個寬度；刻度數字小小地寫在圖裡面；判斷線不掛價籤，
// 名稱是均線的（帶 ma: 期數）畫成整條均線；名稱與目前數值由頁面寫在圖上方（legendItems 給）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/kchart.js", import.meta.url);

function load(width) {
  const texts = [], strokes = [];
  let path = [];
  const state = { fillStyle: "", strokeStyle: "", globalAlpha: 1, lineWidth: 1 };
  const context2d = new Proxy(state, {
    get(target, name) {
      if (name === "measureText") return text => ({ width: String(text).length * 7 });
      if (name === "fillText") return (text, x, y) => texts.push({ text: String(text), x, y, fill: target.fillStyle });
      if (name === "clearRect") return () => { texts.length = 0; strokes.length = 0; };
      if (name === "beginPath") return () => { path = []; };
      if (name === "moveTo" || name === "lineTo") return (x, y) => path.push([x, y]);
      if (name === "stroke") return () => strokes.push({ points: path.slice(), stroke: target.strokeStyle });
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
    console, Number, Math, Map, Object, Array, String, Set, JSON,
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "kchart.js" });
  return { KChart: sandbox.window.KChart, FakeElement, texts, strokes };
}

// 收盤在 40,000 上下的 80 根，慢慢往上走（均線有值、方向明確）
function candles(count = 80) {
  return Array.from({ length: count }, (_, index) => {
    const open = 40_000 + index * 15 + (index % 7) * 60, close = open + (index % 2 ? 180 : -140);
    return { t: `2026-0${1 + Math.floor(index / 28)}-${String(1 + (index % 28)).padStart(2, "0")}`, label: `第${index + 1}根`, open, close, high: Math.max(open, close) + 70, low: Math.min(open, close) - 70, amount: 1, volume: 1 };
  });
}

const WIDTH = 390;
function draw(options = {}, overlays = [], data = candles()) {
  const view = load(WIDTH);
  const chart = new view.KChart(new view.FakeElement(), { macd: false, height: () => 340, overlayLabels: "tags", maPeriods: [5, 20], ...options });
  chart.setData(data, { count: 60, overlays });
  return { ...view, chart, data };
}

const sma = (data, period, index) => data.slice(index - period + 1, index + 1).reduce((sum, c) => sum + c.close, 0) / period;
const HALF_YEAR = { value: 40_300, label: "轉強線 半年線", short: "半年線", color: "red", ma: 30 };   // 自設清單（5、20）裡沒有的期數
const PREV_HIGH = { value: 41_100, label: "轉弱線 前高", short: "前高", color: "green" };

test("inlineAxis：右邊不留價格欄，K 線畫滿整個寬度", () => {
  const railed = draw({ numbersOnly: true });
  const inline = draw({ inlineAxis: true });
  assert.ok(inline.chart.geo.pw >= WIDTH - 12, `K 線區 ${inline.chart.geo.pw}px（畫布 ${WIDTH}px）`);
  assert.ok(inline.chart.geo.pw >= railed.chart.geo.pw + 40, `比原本的 ${railed.chart.geo.pw}px 寬`);
});

test("inlineAxis：刻度數字寫在圖裡面，沒有任何字寫到 K 線區外", () => {
  const { chart, texts } = draw({ inlineAxis: true }, [HALF_YEAR, PREV_HIGH]);
  const right = chart.geo.l + chart.geo.pw;
  const ticks = texts.filter(item => /^\d{2},\d{3}$/.test(item.text));
  assert.ok(ticks.length >= 3, `有刻度數字：${ticks.map(item => item.text).join(" ")}`);
  assert.ok(texts.every(item => item.x <= right), "沒有字畫在右側欄");
});

test("inlineAxis：判斷線不掛價籤，名稱與數字不寫在圖上（由頁面寫在圖上方）", () => {
  const { texts } = draw({ inlineAxis: true }, [HALF_YEAR, PREV_HIGH]);
  assert.ok(!texts.some(item => ["半年線", "前高", "收盤"].includes(item.text)), "沒有名稱");
  assert.ok(!texts.some(item => item.text === "41,100" || item.text === "40,300"), "沒有判斷線的價籤數字");
});

test("名稱是均線的判斷線（ma: 30）畫成整條均線，不是一條水平線", () => {
  const { strokes, chart, data } = draw({ inlineAxis: true }, [HALF_YEAR]);
  const own = strokes.filter(item => item.stroke === "red");
  assert.ok(!own.some(item => item.points.length === 2 && Math.abs(item.points[0][1] - item.points[1][1]) < .01 && item.points[1][0] - item.points[0][0] > 100), "不是水平線");
  const curve = own.find(item => item.points.length >= 40);
  assert.ok(curve, "有一條沿著每一根 K 棒的曲線");
  const { t, ph, lo, hi } = chart.geo;
  const lastY = t + (hi - sma(data, 30, data.length - 1)) / (hi - lo) * ph;
  assert.ok(Math.abs(curve.points[curve.points.length - 1][1] - lastY) < .5, "最右一點就是最後一天的 30 日均線");
});

test("不是均線的判斷線（前高）照樣是水平線", () => {
  const { strokes } = draw({ inlineAxis: true }, [PREV_HIGH]);
  assert.ok(strokes.some(item => item.stroke === "green" && item.points.length === 2 && Math.abs(item.points[0][1] - item.points[1][1]) < .01), "有水平線");
});

test("legendItems：均線、布林、判斷線的名稱，加上目前的數值與方向（選了某一天就是那一天的）", () => {
  const { chart, data } = draw({ inlineAxis: true }, [HALF_YEAR, PREV_HIGH]);
  const items = JSON.parse(JSON.stringify(chart.legendItems()));
  assert.deepEqual(items.map(item => item.name), ["MA5", "MA20", "布林20", "半年線", "前高"]);
  const last = data.length - 1;
  const near = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-6, `${message}：${a} ≠ ${b}`);
  near(items[0].value, sma(data, 5, last), "MA5 最後一天");
  near(items[3].value, sma(data, 30, last), "半年線（ma: 30）的數值跟著均線走");
  assert.equal(items[3].dir, "up", "這段在往上");
  assert.equal(items[4].value, 41_100, "前高就是那一個價");
  assert.equal(items[2].value, undefined, "布林只寫名稱");
  chart.hover = chart.selection = 40;
  const picked = JSON.parse(JSON.stringify(chart.legendItems()));
  near(picked[1].value, sma(data, 20, 40), "選了第 41 根就寫那一天的值");
});

test("inlineAxis：選了某一天，開高低收寫在圖裡、十字線的另一邊", () => {
  const { chart, texts } = draw({ inlineAxis: true });
  const { l, pw, step, start } = chart.geo;
  chart.hover = chart.selection = start + 50;            // 右半邊
  chart.draw();
  const cx = l + step * 50.5;
  const names = texts.filter(item => ["開", "高", "低", "收"].includes(item.text));
  assert.equal(names.length, 4);
  assert.ok(names.every(item => item.x < cx && item.x >= l), "十字線在右半邊 → 寫在左邊");
  chart.hover = chart.selection = start + 5;              // 左半邊
  chart.draw();
  const again = texts.filter(item => ["開", "高", "低", "收"].includes(item.text));
  assert.ok(again.every(item => item.x > l + step * 5.5 && item.x <= l + pw), "十字線在左半邊 → 寫在右邊");
});

test("inlineAxis：畫面上最高、最低的那一根旁邊標出最高價、最低價", () => {
  const { chart, texts, data } = draw({ inlineAxis: true });
  const visible = data.slice(chart.geo.start, chart.view.end);
  const high = Math.max(...visible.map(c => c.high)), low = Math.min(...visible.map(c => c.low));
  const fmt = value => value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  assert.ok(texts.some(item => item.text.includes(fmt(high))), `標了最高 ${fmt(high)}`);
  assert.ok(texts.some(item => item.text.includes(fmt(low))), `標了最低 ${fmt(low)}`);
});

test("onDraw：每畫一次通知頁面（圖上方的數值才跟得上十字線）", () => {
  let count = 0;
  const { chart } = draw({ inlineAxis: true, onDraw: () => { count++; } });
  const before = count;
  chart.hover = 30; chart.draw();
  assert.equal(count, before + 1);
});

test("均線資料不夠（例如年線 240 日，只載入 80 根）：退回畫那一個價的水平線，圖上方照樣寫那一個價", () => {
  const YEAR = { value: 40_500, label: "轉弱線 年線", short: "年線", color: "blue", ma: 240 };
  const { strokes, chart } = draw({ inlineAxis: true }, [YEAR]);
  assert.ok(strokes.some(item => item.stroke === "blue" && item.points.length === 2 && Math.abs(item.points[0][1] - item.points[1][1]) < .01), "畫成水平線");
  const item = JSON.parse(JSON.stringify(chart.legendItems())).find(row => row.name === "年線");
  assert.equal(item.value, 40_500, "寫預測給的那一個價");
});

test("inlineAxis：範圍外的判斷線不畫線，在圖的上緣或下緣留一個同色的小三角形（看得出在上面還是下面）", () => {
  const view = load(WIDTH);
  const chart = new view.KChart(new view.FakeElement(), { macd: false, height: () => 340, inlineAxis: true, fitCandles: true });
  chart.setData(candles(), { count: 60, overlays: [{ value: 60_000, label: "前高", short: "前高", color: "purple" }, { value: 20_000, label: "前低", short: "前低", color: "teal" }] });
  const { t, ph } = chart.geo;
  assert.ok(!view.strokes.some(item => (item.stroke === "purple" || item.stroke === "teal") && item.points.length === 2), "沒有畫線");
  const marks = chart.debugBoxes.outside || [];
  assert.deepEqual(marks.map(mark => mark.side), ["up", "down"]);
  assert.ok(marks[0].y <= t + 8 && marks[1].y >= t + ph - 8, "貼在上緣、下緣");
});

test("inlineAxis：收盤不掛數字小塊（只留虛線；現價在頁面的報價上）", () => {
  const { texts, data } = draw({ inlineAxis: true });
  const close = data[data.length - 1].close.toLocaleString("en-US", { maximumFractionDigits: 0 });
  const onLeft = texts.filter(item => item.text === close && item.x < 40);
  assert.equal(onLeft.length, 0, "左邊沒有收盤數字");
});

// 本人 11-17：「五日線不就是ma5?，另外我希望我可以自己設定均線，不要有上限」；顏色自己挑；
// 預測線和自設均線同一期數時「合成一條」：只畫自設那條，名稱後面加「轉強」或「轉弱」
test("均線不設上限：六條、天數超過 240 都照收；顏色照頁面給的", () => {
  const { chart } = draw({ inlineAxis: true });
  chart.setMaPeriods([5, 10, 20, 30, 40, 300], ["c1", "c2", "c3", "c4", "c5", "c6"]);
  assert.deepEqual([...chart.maPeriods], [5, 10, 20, 30, 40, 300]);
  const items = JSON.parse(JSON.stringify(chart.legendItems())).filter(item => item.name.startsWith("MA"));
  assert.deepEqual(items.map(item => item.color), ["c1", "c2", "c3", "c4", "c5", "c6"]);
});

test("預測線和自設均線同一期數：只剩一個名稱、後面帶「轉強」，圖上不另外畫那條虛線", () => {
  const FIVE = { value: 40_300, label: "轉強線 五日線", short: "MA5", color: "red", ma: 5, role: "轉強" };
  const { chart, strokes } = draw({ inlineAxis: true, maPeriods: [5, 20] }, [FIVE]);
  const items = JSON.parse(JSON.stringify(chart.legendItems()));
  assert.equal(items.filter(item => item.name === "MA5").length, 1, "只有一個 MA5");
  assert.deepEqual(items.find(item => item.name === "MA5").tags, [{ text: "轉強", color: "red" }]);
  assert.ok(!strokes.some(item => item.stroke === "red" && item.points.length >= 40), "沒有另外畫紅色那條");
});

test("預測線的期數不在自設清單裡：照舊畫成整條虛線、圖上方另外一個名稱", () => {
  const TEN = { value: 40_300, label: "轉弱線 十日線", short: "MA10", color: "green", ma: 10, role: "轉弱" };
  const { chart, strokes } = draw({ inlineAxis: true, maPeriods: [5, 20] }, [TEN]);
  const items = JSON.parse(JSON.stringify(chart.legendItems()));
  assert.ok(items.some(item => item.name === "MA10" && item.mark), "另外一個 MA10");
  assert.ok(strokes.some(item => item.stroke === "green" && item.points.length >= 40), "畫成整條");
});

test("合成的那條自設均線資料不夠（例如自設 MA240、只載入 80 根）：不合成，預測線退回畫那一個價", () => {
  const YEAR = { value: 40_500, label: "轉弱線 年線", short: "MA240", color: "blue", ma: 240, role: "轉弱" };
  const { chart, strokes } = draw({ inlineAxis: true, maPeriods: [5, 240] }, [YEAR]);
  assert.ok(strokes.some(item => item.stroke === "blue" && item.points.length === 2 && Math.abs(item.points[0][1] - item.points[1][1]) < .01), "畫成水平線");
  const items = JSON.parse(JSON.stringify(chart.legendItems()));
  assert.ok(items.some(item => item.name === "MA240" && item.mark && item.value === 40_500), "圖上方另外寫預測給的那一個價");
});
