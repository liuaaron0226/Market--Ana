// 本人 2026-11-15：「要把三大法人加進大盤 K 線的副圖」。
// 大盤的法人是金額（億元），個股的是股數（張）；同一組副圖要照圖的單位說話，缺資料的日子不拿別天補。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);
const read = path => readFileSync(new URL(path, ROOT), "utf8");

function loadStudies() {
  const window = {};
  vm.runInNewContext(read("web/chart-studies.js"), {
    window, document: { documentElement: {} }, getComputedStyle: () => ({ getPropertyValue: () => "" }),
  });
  return window.ChartStudies;
}

function pane() {
  const labels = [];
  return { readout: { textContent: "" }, canvas: { labels, setAttribute: (name, value) => labels.push(value) } };
}

function readouts(volumeKind, candle) {
  const ChartStudies = loadStudies();
  const quiet = { setAttribute() {} };
  const studies = {
    volumeKind, chart: { macd: [] }, kdPane: null, rsiPane: null,
    institutionalPanes: { foreign: pane(), trust: pane(), dealer: pane() },
    volumeReadout: {}, macdReadout: {}, volumeCanvas: quiet, macdCanvas: quiet,
  };
  ChartStudies.prototype._updateReadouts.call(studies, { candles: [candle] }, 0);
  return studies.institutionalPanes;
}

test("大盤的法人副圖用億元讀數，個股維持張數", () => {
  const market = readouts("amount", { label: "11/06", amount: 13125.17, foreign: -911.88, trust: 266.58, dealer: -494.76 });
  assert.equal(market.foreign.readout.textContent, "11/06｜外資 −911.9 億元");
  assert.equal(market.trust.readout.textContent, "11/06｜投信 +266.6 億元");
  assert.equal(market.dealer.readout.textContent, "11/06｜自營商 −494.8 億元");
  assert.match(market.foreign.canvas.labels.at(-1), /賣超 911\.9 億元/);

  const stock = readouts("volume", { label: "2026-11-06", volume: 30_000_000, foreign: -5_000_000, trust: 1_200_000, dealer: 0 });
  assert.equal(stock.foreign.readout.textContent, "2026-11-06｜外資 −5,000 張");
  assert.equal(stock.trust.readout.textContent, "2026-11-06｜投信 +1,200 張");
});

test("沒有法人資料的那一天寫資料不足，不拿別天的數字", () => {
  const market = readouts("amount", { label: "11/13（盤中）", amount: 5000 });
  assert.equal(market.foreign.readout.textContent, "11/13（盤中）｜資料不足");
});

test("大盤法人副圖的刻度也是億元", () => {
  const ChartStudies = loadStudies();
  const texts = [];
  const context = new Proxy({ fillText: text => texts.push(String(text)) }, {
    get: (target, key) => key in target ? target[key] : () => {},
    set: () => true,
  });
  const range = { start: 0, end: 2, geo: { l: 0, pw: 200, step: 100 },
    candles: [{ foreign: -911.88 }, { foreign: 300 }] };
  ChartStudies.prototype._drawInstitutional.call({ volumeKind: "amount", chart: {}, _drawCrosshair() {} }, context, 260, 120, range, "foreign");
  assert.deepEqual(texts, ["912億", "0", "−912億"]);
  texts.length = 0;
  ChartStudies.prototype._drawInstitutional.call({ volumeKind: "volume", chart: {}, _drawCrosshair() {} }, context, 260, 120,
    { ...range, candles: [{ foreign: -5_000_000 }, { foreign: 1_000_000 }] }, "foreign");
  assert.deepEqual(texts, ["5,000張", "0", "−5,000張"]);
});

test("把官方三大法人淨額接到同一天的大盤日 K 上，單位換成億元", () => {
  const ChartStudies = loadStudies();
  const candles = [{ t: "2026-11-05", close: 1 }, { t: "2026-11-06", close: 2 }, { t: "2026-11-09", close: 3 }];
  const rows = [
    { date: "2026-11-06", foreign: { net: -91_188_249_259 }, trust: { net: 26_657_595_282 }, dealer: { net: -49_476_204_430 } },
    { date: "2026-11-09", foreign: { net: 1_000_000_000 }, trust: {}, dealer: null },
  ];
  const out = ChartStudies.withMarketInstitutional(candles, rows);
  assert.equal(out.length, 3);
  assert.equal(out[0].foreign, undefined, "官方沒有那一天就留空");
  assert.ok(Math.abs(out[1].foreign - -911.88249259) < 1e-9);
  assert.ok(Math.abs(out[1].trust - 266.57595282) < 1e-9);
  assert.ok(Math.abs(out[1].dealer - -494.7620443) < 1e-9);
  assert.equal(out[2].foreign, 10);
  assert.equal(out[2].trust, undefined);
  assert.equal(out[2].dealer, undefined);
  assert.equal(out[1].close, 2);
  assert.equal(candles[1].foreign, undefined, "不改動傳入的 K 棒");
});

test("首頁大盤圖把外資、投信、自營商接進副圖", () => {
  const source = read("web/minimal.js");
  assert.match(source, /studies: \['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'\]/);
  assert.match(source, /ChartStudies\.withMarketInstitutional\(A\.daily\.map\(row => toChartCandle\(row, '日'\)\), A\.institutional\)/);
});

test("副圖按鈕排成兩列時，選取底塊跟到選中的那一列", () => {
  for (const file of ["web/home-motion.js", "web/motion.js"]) {
    const source = read(file);
    assert.match(source, /translate\(\$\{r\.left - box\.left\}px, \$\{r\.top \+ r\.height \/ 2 - box\.top - box\.height \/ 2\}px\)/, file);
    assert.match(source, /order = \['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'\]/, file);
  }
});
