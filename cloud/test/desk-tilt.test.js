// 桌面隨游標微傾（立體重設計 D 包第 1 片；本人 2026-11-14 定案「大膽」：桌機上整張桌面隨游標微傾）。
// 只驗對外的輸入輸出：裝置與設定決定開不開、游標位置決定目標角度、每一格往目標靠近多少、什麼時候算停下來。
// 不碰畫面；畫面那一層只把角度寫到 transform 上。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/desk-tilt.js", ROOT), "utf8"), { window, Math, Number, Object });
  return window.DeskTilt;
}

const tilt = load();
const plain = (value) => JSON.parse(JSON.stringify(value));

test("只在可懸停且精細指標、桌機寬度、沒有開減少動態效果、開關開著時啟用", () => {
  assert.equal(tilt.allowed({ fineHover: true, width: 1440, reduceMotion: false }), true);
  assert.equal(tilt.allowed({ fineHover: false, width: 1440, reduceMotion: false }), false, "觸控裝置");
  assert.equal(tilt.allowed({ fineHover: true, width: 860, reduceMotion: false }), false, "手機寬度（860 以下，和全站的手機版面同一條線）");
  assert.equal(tilt.allowed({ fineHover: true, width: 861, reduceMotion: false }), true);
  assert.equal(tilt.allowed({ fineHover: true, width: 1440, reduceMotion: true }), false, "減少動態效果");
  assert.equal(tilt.allowed({ fineHover: true, width: 1440, reduceMotion: false, enabled: false }), false, "開關關掉");
});

// owner 11-17：上線先關（桌機畫線對位差約 5px），等本人決定。打開時改回 true。
test("單一開關：DeskTilt.ENABLED 目前關著", () => {
  assert.equal(tilt.ENABLED, false);
});

test("游標在畫面正中間時不傾斜；四個角落是最大角度（上下 1.6 度、左右 2.2 度）", () => {
  const view = { width: 1440, height: 900 };
  assert.deepEqual(plain(tilt.target({ ...view, x: 720, y: 450 })), { rx: 0, ry: 0 });
  const topLeft = tilt.target({ ...view, x: 0, y: 0 });
  const bottomRight = tilt.target({ ...view, x: 1440, y: 900 });
  assert.ok(Math.abs(topLeft.rx - 0.8) < 1e-9 && Math.abs(topLeft.ry + 1.1) < 1e-9, JSON.stringify(topLeft));
  assert.ok(Math.abs(bottomRight.rx + 0.8) < 1e-9 && Math.abs(bottomRight.ry - 1.1) < 1e-9, JSON.stringify(bottomRight));
  assert.ok(Math.abs(topLeft.rx - bottomRight.rx - 1.6) < 1e-9, "上下合計 1.6 度");
  assert.ok(Math.abs(bottomRight.ry - topLeft.ry - 2.2) < 1e-9, "左右合計 2.2 度");
});

test("游標跑出視窗外也不會超過最大角度", () => {
  const far = tilt.target({ width: 1000, height: 800, x: -500, y: 5000 });
  assert.ok(Math.abs(far.rx) <= 0.8 + 1e-9 && Math.abs(far.ry) <= 1.1 + 1e-9, JSON.stringify(far));
});

test("游標在要精準讀價或拖曳的地方（flat）時目標是平的", () => {
  assert.deepEqual(plain(tilt.target({ width: 1440, height: 900, x: 0, y: 0, flat: true })), { rx: 0, ry: 0 });
});

test("每一格往目標靠近一段（平順），夠近就停下來、落在目標上（游標不動時不再佔用運算）", () => {
  let now = { rx: 0, ry: 0 };
  const goal = { rx: 0.8, ry: -1.1 };
  const first = tilt.step(now, goal);
  assert.ok(first.rx > 0 && first.rx < 0.8 && first.ry < 0 && first.ry > -1.1, JSON.stringify(first));
  assert.equal(first.settled, false);
  let frames = 0;
  while (!now.settled && frames < 500) { now = tilt.step(now, goal); frames += 1; }
  assert.equal(now.settled, true, "有限格數內停下");
  assert.ok(frames < 120, `約兩秒內停下（${frames} 格）`);
  assert.deepEqual([now.rx, now.ry], [0.8, -1.1], "停下時剛好在目標上");
});

test("回平比傾斜快：游標移到 K 線上時盡快回到能精準讀價的狀態", () => {
  const tilted = { rx: 0.8, ry: -1.1 };
  const back = tilt.step(tilted, { rx: 0, ry: 0 });
  const away = tilt.step({ rx: 0, ry: 0 }, tilted);
  assert.ok(Math.abs(tilted.rx - back.rx) > Math.abs(away.rx), "回平那一格走得比較多");
});

test("回到平的、停下來時可以拿掉 transform（不留下堆疊層與模糊）", () => {
  assert.equal(tilt.atRest({ rx: 0, ry: 0, settled: true }), true);
  assert.equal(tilt.atRest({ rx: 0.2, ry: 0, settled: true }), false);
  assert.equal(tilt.atRest({ rx: 0, ry: 0.0001, settled: false }), false);
});

test("transform 字串：繞著畫面正中間的那一點轉，角度到小數三位", () => {
  assert.equal(tilt.transform({ rx: 0.12345, ry: -1.1 }), "perspective(1900px) rotateX(0.123deg) rotateY(-1.100deg)");
});
