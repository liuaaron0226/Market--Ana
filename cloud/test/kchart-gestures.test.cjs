'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.resolve(process.env.KCHART_SOURCE || path.join(__dirname, '..', '..', 'web', 'kchart.js'));

function makeContext() {
  const drawnText = [];
  const context2d = {
    setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
    fillRect() {}, strokeRect() {}, setLineDash() {}, rect() {}, fill() {},
    measureText(text) { return { width: String(text).length * 7 }; },
    fillText(text) { drawnText.push(String(text)); }
  };

  class FakeElement {
    constructor(tagName) {
      this.tagName = tagName;
      this.children = [];
      this.listeners = new Map();
      this.style = {};
      this.attributes = new Map();
      this.clientWidth = 390;
      this.offsetWidth = 150;
      this.width = 0;
      this.height = 0;
      const classes = new Set();
      this.classList = {
        add: (...names) => names.forEach(name => classes.add(name)),
        remove: (...names) => names.forEach(name => classes.delete(name)),
        contains: name => classes.has(name)
      };
    }
    append(child) { this.children.push(child); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    addEventListener(type, handler) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(handler);
    }
    dispatch(type, init = {}) {
      const event = {
        type,
        pointerId: 0,
        pointerType: 'touch',
        clientX: 0,
        clientY: 100,
        key: '',
        deltaY: 0,
        preventDefault() {},
        ...init
      };
      for (const handler of this.listeners.get(type) || []) handler(event);
      return event;
    }
    setPointerCapture() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: 340 }; }
    getContext(kind) { assert.equal(kind, '2d'); return context2d; }
    contains(target) { return target === this || this.children.includes(target); }
  }

  const document = {
    documentElement: new FakeElement('html'),
    createElement: tag => new FakeElement(tag),
    addEventListener() {}
  };
  const window = { devicePixelRatio: 1 };
  const sandbox = {
    window,
    document,
    ResizeObserver: class { constructor(callback) { this.callback = callback; } observe() {} },
    getComputedStyle: () => ({ getPropertyValue: () => '#666' }),
    console,
    Number,
    Math,
    Map,
    Object,
    Array,
    String,
    Set
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), sandbox, { filename: sourcePath });
  return { KChart: sandbox.window.KChart, FakeElement, drawnText };
}

function validCandles(count, unit = '日') {
  return Array.from({ length: count }, (_, index) => {
    const close = 100 + index * 0.5;
    const day = String((index % 28) + 1).padStart(2, '0');
    return {
      t: unit === '60分' ? `2026-10-${day} 09:00` : `2026-10-${day}`,
      label: `第${index + 1}根`,
      open: close - 0.4,
      high: close + 1,
      low: close - 1,
      close,
      amount: 10 + index,
      volume: 1000 + index
    };
  });
}

function createChart({ count = 80, unit = '日', minViewCount = 10 } = {}) {
  const { KChart, FakeElement, drawnText } = makeContext();
  const host = new FakeElement('div');
  const chart = new KChart(host, { macd: false, minViewCount, height: () => 340 });
  const candles = validCandles(count, unit);
  chart.setData(candles, { count: Math.min(60, count), unit });
  drawnText.length = 0;
  return { chart, canvas: chart.canvas, candles, drawnText };
}

function pinchToLimitThenContinueWithRemainingFinger(chart, canvas, { releaseId = 2, releaseType = 'pointerup' } = {}) {
  const snapshots = [];
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 190, clientY: 100 });
  canvas.dispatch('pointerdown', { pointerId: 2, clientX: 200, clientY: 102 });
  for (const x of [240, 300, 360, 380]) {
    canvas.dispatch('pointermove', { pointerId: 2, clientX: x, clientY: 102 });
  }
  snapshots.push({ step: 'pinch-at-limit', end: chart.view.end, count: chart.view.count });
  const releaseX = releaseId === 1 ? 190 : 380;
  const remainingId = releaseId === 1 ? 2 : 1;
  const remainingX = releaseId === 1 ? 379 : 191;
  canvas.dispatch(releaseType, { pointerId: releaseId, clientX: releaseX, clientY: releaseId === 1 ? 100 : 102 });
  snapshots.push({ step: 'one-finger-released', end: chart.view.end, count: chart.view.count });
  canvas.dispatch('pointermove', { pointerId: remainingId, clientX: remainingX, clientY: remainingId === 1 ? 100 : 102 });
  snapshots.push({ step: 'remaining-finger-moved', end: Number.isFinite(chart.view.end) ? chart.view.end : 'non-finite', count: chart.view.count });
  return { snapshots, remainingId, remainingX };
}

function pinchThenLiftBothWithoutRemainingMove(canvas, firstReleaseId = 2) {
  const positions = new Map([[1, 150], [2, 350]]);
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 150, clientY: 100 });
  canvas.dispatch('pointerdown', { pointerId: 2, clientX: 250, clientY: 100 });
  canvas.dispatch('pointermove', { pointerId: 2, clientX: 350, clientY: 100 });
  const secondReleaseId = firstReleaseId === 1 ? 2 : 1;
  canvas.dispatch('pointerup', { pointerId: firstReleaseId, clientX: positions.get(firstReleaseId), clientY: 100 });
  canvas.dispatch('pointerup', { pointerId: secondReleaseId, clientX: positions.get(secondReleaseId), clientY: 100 });
}

function assertHealthy(chart, drawnText, label) {
  assert.equal(drawnText.includes('此區間OHLC資料不足'), false, `${label}：全有效 OHLC 不得顯示資料不足`);
  assert.equal(Number.isFinite(chart.view.end), true, `${label}：view.end 必須是有限數值`);
  assert.equal(Number.isFinite(chart.view.count), true, `${label}：view.count 必須是有限數值`);
  assert.ok(chart.view.count >= 1 && chart.view.count <= chart.candles.length, `${label}：顯示根數須在資料界內`);
  assert.ok(chart.view.end >= chart.view.count && chart.view.end <= chart.candles.length, `${label}：顯示終點須在資料界內`);
}

test('手機雙指持續放大至極限後，先放一指再移動剩餘手指，不得把有效 OHLC 畫成空白', () => {
  const { chart, canvas, candles, drawnText } = createChart();
  assert.ok(candles.every(candle => ['open', 'high', 'low', 'close'].every(key => Number.isFinite(candle[key]))));

  const { snapshots } = pinchToLimitThenContinueWithRemainingFinger(chart, canvas);

  const symptom = drawnText.includes('此區間OHLC資料不足');
  if (symptom || !Number.isFinite(chart.view.end)) {
    console.error('ZOOM12_REPRO', JSON.stringify({
      sourcePath,
      validOhlcRows: candles.length,
      snapshots,
      view: { end: Number.isFinite(chart.view.end) ? chart.view.end : 'non-finite', count: chart.view.count },
      exactMessage: symptom ? '此區間OHLC資料不足' : null
    }));
  }
  assert.equal(symptom, false, '有效 OHLC 在手機 pinch 結束轉單指時不應顯示「此區間OHLC資料不足」');
  assert.equal(Number.isFinite(chart.view.end), true, '顯示區間 end 必須保持有限數值');
});

test('先放開第一指、由第二指繼續拖曳也必須保持有效顯示區間', () => {
  const { chart, canvas, drawnText } = createChart();
  pinchToLimitThenContinueWithRemainingFinger(chart, canvas, { releaseId: 1 });
  assertHealthy(chart, drawnText, '先放第一指');
});

test('pointercancel 留下一指時可繼續，全部離開後可重新開始單指手勢', () => {
  const { chart, canvas, drawnText } = createChart();
  const { remainingId, remainingX } = pinchToLimitThenContinueWithRemainingFinger(chart, canvas, { releaseId: 2, releaseType: 'pointercancel' });
  assertHealthy(chart, drawnText, '取消其中一指');
  canvas.dispatch('pointercancel', { pointerId: remainingId, clientX: remainingX, clientY: 100 });
  const before = chart.view.end;
  canvas.dispatch('pointerdown', { pointerId: 3, clientX: 180, clientY: 110 });
  canvas.dispatch('pointermove', { pointerId: 3, clientX: 220, clientY: 110 });
  canvas.dispatch('pointerup', { pointerId: 3, clientX: 220, clientY: 110 });
  assertHealthy(chart, drawnText, '取消全部後重開手勢');
  assert.notEqual(chart.view.end, before, '重開後的單指平移應實際改變顯示終點');
});

test('單指平移維持正常且確實改變顯示區間', () => {
  const { chart, canvas, drawnText } = createChart();
  const before = chart.view.end;
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 180, clientY: 100 });
  canvas.dispatch('pointermove', { pointerId: 1, clientX: 230, clientY: 100 });
  canvas.dispatch('pointerup', { pointerId: 1, clientX: 230, clientY: 100 });
  assertHealthy(chart, drawnText, '單指平移');
  assert.notEqual(chart.view.end, before);
});

test('雙指連續放大再縮回可恢復，不產生空白', () => {
  const { chart, canvas, drawnText } = createChart();
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 150, clientY: 100 });
  canvas.dispatch('pointerdown', { pointerId: 2, clientX: 250, clientY: 100 });
  for (const x of [300, 350, 380]) canvas.dispatch('pointermove', { pointerId: 2, clientX: x, clientY: 100 });
  const zoomedInCount = chart.view.count;
  for (const x of [330, 280, 230, 200]) canvas.dispatch('pointermove', { pointerId: 2, clientX: x, clientY: 100 });
  const zoomedBackCount = chart.view.count;
  canvas.dispatch('pointerup', { pointerId: 2, clientX: 200, clientY: 100 });
  canvas.dispatch('pointerup', { pointerId: 1, clientX: 150, clientY: 100 });
  assertHealthy(chart, drawnText, '雙指放大縮回');
  assert.ok(zoomedInCount < zoomedBackCount, '縮回後顯示根數應增加');
});

test('pinch 後依序抬起兩指且剩餘指未移動，不得在主圖冒充單擊選取', () => {
  for (const firstReleaseId of [1, 2]) {
    const { chart, canvas, drawnText } = createChart();
    const before = chart.selection;
    pinchThenLiftBothWithoutRemainingMove(canvas, firstReleaseId);
    assertHealthy(chart, drawnText, `主圖先放 pointer ${firstReleaseId}`);
    assert.equal(chart.selection, before, `主圖先放 pointer ${firstReleaseId}：pinch 放手不得改變 selection`);
  }
});

test('pinch 後依序抬起兩指且剩餘指未移動，不得在副圖觸發 onSelect 或切換', () => {
  for (const firstReleaseId of [1, 2]) {
    const { chart, canvas, drawnText } = createChart();
    const studyCanvas = new canvas.constructor('canvas');
    const selected = [];
    const switched = [];
    chart.opts.onSelect = (index, anchor) => selected.push({ index, anchor });
    chart.studies = { draw() {}, switchBy(direction) { switched.push(direction); } };
    chart.bindCanvas(studyCanvas);
    const before = chart.selection;
    pinchThenLiftBothWithoutRemainingMove(studyCanvas, firstReleaseId);
    assertHealthy(chart, drawnText, `副圖先放 pointer ${firstReleaseId}`);
    assert.equal(chart.selection, before, `副圖先放 pointer ${firstReleaseId}：pinch 放手不得改變 selection`);
    assert.equal(selected.length, 0, `副圖先放 pointer ${firstReleaseId}：pinch 放手不得觸發 onSelect`);
    assert.equal(switched.length, 0, `副圖先放 pointer ${firstReleaseId}：pinch 放手不得切換 study`);
  }
});

test('真正單指 tap 仍可在主圖選取，並在副圖觸發 onSelect', () => {
  const main = createChart();
  main.canvas.dispatch('pointerdown', { pointerId: 1, clientX: 150, clientY: 100 });
  main.canvas.dispatch('pointerup', { pointerId: 1, clientX: 150, clientY: 100 });
  assert.ok(main.chart.selection >= 0, '主圖真正單指 tap 應保留選取能力');

  const study = createChart();
  const studyCanvas = new study.canvas.constructor('canvas');
  const selected = [];
  study.chart.opts.onSelect = (index, anchor) => selected.push({ index, anchor });
  study.chart.studies = { draw() {}, switchBy() {} };
  study.chart.bindCanvas(studyCanvas);
  studyCanvas.dispatch('pointerdown', { pointerId: 1, clientX: 150, clientY: 100 });
  studyCanvas.dispatch('pointerup', { pointerId: 1, clientX: 150, clientY: 100 });
  assert.equal(selected.length, 1, '副圖真正單指 tap 應觸發一次 onSelect');
  assert.ok(study.chart.selection >= 0, '副圖真正單指 tap 應保留 selection');
});

test('同 X 不同 Y 的雙指仍是 pinch：兩種放手順序下移動或不移動剩餘指都不得空白或選取', () => {
  for (const firstReleaseId of [1, 2]) {
    for (const moveRemaining of [true, false]) {
      const { chart, canvas, drawnText } = createChart();
      const positions = new Map([[1, 200], [2, 200]]);
      canvas.dispatch('pointerdown', { pointerId: 1, clientX: 200, clientY: 80 });
      canvas.dispatch('pointerdown', { pointerId: 2, clientX: 200, clientY: 120 });
      const secondReleaseId = firstReleaseId === 1 ? 2 : 1;
      canvas.dispatch('pointerup', { pointerId: firstReleaseId, clientX: 200, clientY: firstReleaseId === 1 ? 80 : 120 });
      if (moveRemaining) {
        positions.set(secondReleaseId, 220);
        canvas.dispatch('pointermove', { pointerId: secondReleaseId, clientX: 220, clientY: secondReleaseId === 1 ? 80 : 120 });
      }
      canvas.dispatch('pointerup', { pointerId: secondReleaseId, clientX: positions.get(secondReleaseId), clientY: secondReleaseId === 1 ? 80 : 120 });
      assertHealthy(chart, drawnText, `同X先放${firstReleaseId}/剩指${moveRemaining ? '有' : '無'}移動`);
      assert.equal(chart.selection, -1, '同 X 的雙指放手不得冒充 tap');
    }
  }
});

test('兩指完全重合後距離由 0 變正，應進入縮放且保持有限，不得冒充 tap', () => {
  const { chart, canvas, drawnText } = createChart();
  const beforeCount = chart.view.count;
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 200, clientY: 100 });
  canvas.dispatch('pointerdown', { pointerId: 2, clientX: 200, clientY: 100 });
  canvas.dispatch('pointermove', { pointerId: 2, clientX: 300, clientY: 100 });
  const afterSeparationCount = chart.view.count;
  canvas.dispatch('pointerup', { pointerId: 2, clientX: 300, clientY: 100 });
  canvas.dispatch('pointerup', { pointerId: 1, clientX: 200, clientY: 100 });
  assertHealthy(chart, drawnText, '重合後分離');
  assert.ok(afterSeparationCount < beforeCount, '重合後水平距離變正時應實際進入放大路徑');
  assert.equal(chart.selection, -1, '重合後分離仍屬 pinch，不得冒充 tap');
});

test('同 X 的雙指在副圖放手不得觸發 onSelect', () => {
  for (const firstReleaseId of [1, 2]) {
    const { chart, canvas, drawnText } = createChart();
    const studyCanvas = new canvas.constructor('canvas');
    const selected = [];
    chart.opts.onSelect = (index, anchor) => selected.push({ index, anchor });
    chart.studies = { draw() {}, switchBy() {} };
    chart.bindCanvas(studyCanvas);
    studyCanvas.dispatch('pointerdown', { pointerId: 1, clientX: 200, clientY: 80 });
    studyCanvas.dispatch('pointerdown', { pointerId: 2, clientX: 200, clientY: 120 });
    const secondReleaseId = firstReleaseId === 1 ? 2 : 1;
    studyCanvas.dispatch('pointerup', { pointerId: firstReleaseId, clientX: 200, clientY: firstReleaseId === 1 ? 80 : 120 });
    studyCanvas.dispatch('pointerup', { pointerId: secondReleaseId, clientX: 200, clientY: secondReleaseId === 1 ? 80 : 120 });
    assertHealthy(chart, drawnText, `副圖同X先放${firstReleaseId}`);
    assert.equal(chart.selection, -1);
    assert.equal(selected.length, 0, '副圖同 X pinch 放手不得觸發 onSelect');
  }
});

test('MA 設定重算與重繪前後，pinch→單指手勢 view 維持有限且設定不改顯示範圍', () => {
  const { chart, canvas, drawnText } = createChart();
  const initialView = { ...chart.view };
  chart.setMaPeriods([7, 18, 45]);
  assert.deepEqual(Array.from(chart.maPeriods), [7, 18, 45]);
  assert.deepEqual({ ...chart.view }, initialView, '手勢前變更 MA 不應改顯示範圍');
  assertHealthy(chart, drawnText, '手勢前 MA 重繪');

  const { remainingId, remainingX } = pinchToLimitThenContinueWithRemainingFinger(chart, canvas, { releaseId: 2 });
  canvas.dispatch('pointerup', { pointerId: remainingId, clientX: remainingX, clientY: 100 });
  assertHealthy(chart, drawnText, 'MA 設定後 pinch→單指');

  const gestureView = { ...chart.view };
  chart.setMaPeriods([10, 20]);
  assert.deepEqual(Array.from(chart.maPeriods), [10, 20]);
  assert.deepEqual({ ...chart.view }, gestureView, '手勢後變更 MA 不應改顯示範圍');
  assertHealthy(chart, drawnText, '手勢後 MA 重繪');
});

test('短合成序列與週K／月K／60分K標籤共用手勢路徑都不把有效 OHLC 畫成不足', () => {
  const cases = [
    { count: 5, unit: '日' },
    { count: 24, unit: '週' },
    { count: 24, unit: '月' },
    { count: 24, unit: '60分' }
  ];
  for (const scenario of cases) {
    const { chart, canvas, candles, drawnText } = createChart(scenario);
    assert.ok(candles.every(candle => ['open', 'high', 'low', 'close'].every(key => Number.isFinite(candle[key]))));
    pinchToLimitThenContinueWithRemainingFinger(chart, canvas, { releaseId: 2 });
    assertHealthy(chart, drawnText, `${scenario.unit}K/${scenario.count}根`);
    chart.zoom(1.5, 0.5);
    chart.pan(-2);
    assertHealthy(chart, drawnText, `${scenario.unit}K縮回與平移`);
  }
});

// 「取消選取」鍵拿掉之後（本人 2026-11-15），取消的方式是再點一次同一根。
test('再點一次同一根 K 棒就取消選取；點另一根則改選那一根', () => {
  const { chart, canvas } = createChart();
  let cleared = 0;
  chart.opts.onClear = () => { cleared += 1; };
  const tap = x => {
    canvas.dispatch('pointerdown', { pointerId: 1, clientX: x, clientY: 100 });
    canvas.dispatch('pointerup', { pointerId: 1, clientX: x, clientY: 100 });
  };
  tap(150);
  const first = chart.selection;
  assert.ok(first >= 0, '第一次點：選取');
  tap(150);
  assert.equal(chart.selection, -1, '再點同一根：取消選取');
  assert.equal(chart.hover, -1, '十字線跟著收掉');
  assert.equal(cleared, 1, '通知頁面選取已取消');
  tap(150);
  assert.equal(chart.selection, first, '取消之後再點：重新選取');
  tap(220);
  assert.ok(chart.selection >= 0 && chart.selection !== first, '點另一根：改選那一根');
  assert.equal(cleared, 1, '改選不算取消');
});

test('點到圖區以外（右側數值欄）會取消選取，而且通知頁面', () => {
  const { chart, canvas } = createChart();
  let cleared = 0;
  chart.opts.onClear = () => { cleared += 1; };
  const tap = x => {
    canvas.dispatch('pointerdown', { pointerId: 1, clientX: x, clientY: 100 });
    canvas.dispatch('pointerup', { pointerId: 1, clientX: x, clientY: 100 });
  };
  tap(150);
  assert.ok(chart.selection >= 0);
  tap(385);
  assert.equal(chart.selection, -1, '右側數值欄不是 K 棒，點了取消選取');
  assert.equal(cleared, 1, '頁面要收到通知，才會跟著收掉自己的選取狀態');
});

test('觸控拖動平移之後，十字線回到選取的那一天，不停在手指經過的那一天', () => {
  const { chart, canvas } = createChart();
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 150, clientY: 100 });
  canvas.dispatch('pointerup', { pointerId: 1, clientX: 150, clientY: 100 });
  const selected = chart.selection;
  assert.ok(selected >= 0);
  canvas.dispatch('pointerdown', { pointerId: 1, clientX: 200, clientY: 100 });
  canvas.dispatch('pointermove', { pointerId: 1, clientX: 260, clientY: 100 });
  assert.notEqual(chart.hover, selected, '拖動中十字線跟著手指');
  canvas.dispatch('pointerup', { pointerId: 1, clientX: 260, clientY: 100 });
  assert.equal(chart.selection, selected, '拖動不改選取');
  assert.equal(chart.hover, selected, '放手後十字線回到選取的那一天');

  const fresh = createChart();
  fresh.canvas.dispatch('pointerdown', { pointerId: 1, clientX: 200, clientY: 100 });
  fresh.canvas.dispatch('pointermove', { pointerId: 1, clientX: 260, clientY: 100 });
  fresh.canvas.dispatch('pointerup', { pointerId: 1, clientX: 260, clientY: 100 });
  assert.equal(fresh.chart.hover, -1, '沒有選取時，拖完十字線收掉');
});
