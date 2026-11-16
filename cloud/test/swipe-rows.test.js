// 向左滑露出動作的列（本人 2026-11-16：刪除庫存改成把那一列向左滑、露出紅色的刪除）：
// 只驗手勢的判斷（跟著手指的位置、這一下是滑還是捲、放開時停在哪），不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("../../web/swipe-rows.js", import.meta.url), "utf8"), { window });
  return window.SwipeRows;
}
const WIDTH = 128;   // 兩個動作的寬度

test("跟著手指：往左拉多少就走多少，往右不會超過原位", () => {
  const { follow } = load();
  assert.equal(follow(0, -40, WIDTH), -40);
  assert.equal(follow(0, 30, WIDTH), 0);
  assert.equal(follow(-WIDTH, 50, WIDTH), -78);     // 已經滑開的那一列往右推回來
  assert.equal(follow(-WIDTH, 200, WIDTH), 0);
});

test("跟著手指：滑過頭之後變重，只跟四分之一", () => {
  const { follow } = load();
  assert.equal(follow(0, -WIDTH, WIDTH), -WIDTH);
  assert.equal(follow(0, -WIDTH - 40, WIDTH), -WIDTH - 10);
});

test("這一下是滑還是捲：橫向動得夠多才算滑；上下動得多就是捲；動一點點還看不出來", () => {
  const { intent } = load();
  assert.equal(intent(-3, 1), null);
  assert.equal(intent(-12, 2), "swipe");
  assert.equal(intent(-6, -40), "scroll");
  assert.equal(intent(-12, -11), null);      // 斜著動：還不算滑
  assert.equal(intent(14, 3), "swipe");      // 往右也算（已經滑開的那一列推回去）
});

test("放開時：滑過一半就停在打開，沒過一半就收回", () => {
  const { settle } = load();
  assert.equal(settle({ at: -70, last: -70, width: WIDTH }), true);
  assert.equal(settle({ at: -50, last: -50, width: WIDTH }), false);
});

test("放開時：還在往左、而且已經滑出一小段，沒過一半也打開（快速一撥）", () => {
  const { settle } = load();
  assert.equal(settle({ at: -30, last: -20, width: WIDTH }), true);
  assert.equal(settle({ at: -10, last: -4, width: WIDTH }), false);     // 只動一點點
  assert.equal(settle({ at: -30, last: -45, width: WIDTH }), false);    // 正在往回推
});

test("放開時：手勢被系統取消（例如變成捲動）一律收回", () => {
  const { settle } = load();
  assert.equal(settle({ at: -120, last: -110, width: WIDTH, cancelled: true }), false);
});
