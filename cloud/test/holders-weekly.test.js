// 大戶持股：把每週一筆的集保大戶持股（個股分析的 big_holders）讀成畫面要的東西——一句結論、三個級距這一週的數字、每週增減的小柱。
// 只驗對外的輸入輸出，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadRead() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/holders-weekly.js", ROOT), "utf8"), { window });
  // 頁面腳本在另一個執行環境跑；轉一次 JSON 再比，順便確認結果是純資料。
  return (rows, weeks) => JSON.parse(JSON.stringify(window.HoldersWeekly.read(rows, weeks)));
}

const week = (date, pct1000, n1000, pct400, n400, holders) => ({ date, pct_1000: pct1000, holders_1000: n1000, pct_400: pct400, holders_400: n400, holders });
// 最後兩週是預覽樣本裡 2327 的真數字；前面是為了測試排的
const WEEKS = [
  week("2026-08-21", 57.92, 171, 62.95, 331, 668120), week("2026-08-28", 57.80, 170, 62.90, 330, 669004), week("2026-09-04", 57.86, 170, 62.90, 330, 669410),
  week("2026-09-11", 57.71, 169, 62.84, 329, 670215), week("2026-09-18", 57.65, 168, 62.84, 329, 670980), week("2026-09-25", 57.65, 168, 62.91, 331, 671102),
  week("2026-10-02", 57.83, 170, 63.02, 333, 670644), week("2026-10-09", 58.02, 172, 63.11, 334, 670912), week("2026-10-16", 57.94, 171, 63.11, 334, 671230),
  week("2026-10-23", 58.11, 172, 63.20, 335, 671305), week("2026-10-30", 58.30, 174, 63.27, 336, 671382),
  week("2026-11-06", 58.43, 175, 63.30, 337, 671382), week("2026-11-13", 58.56, 179, 63.30, 337, 671382),
];
const kinds = (level) => level.bars.map((bar) => bar.kind);

test("一句結論：千張以上連續幾週往同一個方向；這一週增加，整張是浮起的", () => {
  const read = loadRead()(WEEKS);
  assert.deepEqual([read.date, read.title, read.note, read.state], ["2026-11-13", "連 4 週增加", "千張以上大戶", "bull"]);
});

test("三個級距這一週的數字與較前一週", () => {
  const { levels } = loadRead()(WEEKS);
  assert.deepEqual(levels.map((level) => [level.key, level.name, level.label, level.value, level.change, level.kind]), [
    ["pct1000", "千張以上", "千張以上大戶", "58.56%", "較前一週 +0.13 個百分點", "bull"],
    ["pct400", "400 張以上", "400 張以上", "63.30%", "和前一週一樣", "none"],
    ["holders", "全部股東", "全部股東", "671,382 人", "和前一週一樣", "none"],
  ]);
  assert.deepEqual(levels.map((level) => level.read), [
    "持股 58.56%，179 人（人數 +4）",
    "持股 63.30%，337 人（人數 0）",
    "12 週前 668,120 人，現在 671,382 人（+3,262）",
  ]);
});

test("每週增減一根小柱（最多 12 根，舊到新）：增加、減少、沒變，長度照那個級距自己最大的一次增減來比", () => {
  const { levels } = loadRead()(WEEKS);
  assert.deepEqual(kinds(levels[0]), ["bear", "bull", "bear", "bear", "none", "bull", "bull", "bear", "bull", "bull", "bull", "bull"]);
  const sizes = levels[0].bars.map((bar) => Math.round(bar.size * 100) / 100);
  assert.equal(Math.max(...sizes), 1);                       // 最大的那一次（+0.19）是 1
  assert.equal(sizes[4], 0);                                 // 沒變的那一週是 0
  assert.equal(sizes.at(-1), Math.round(0.13 / 0.19 * 100) / 100);
  assert.deepEqual(kinds(levels[1]).slice(-3), ["bull", "bull", "none"]);
  assert.deepEqual(kinds(levels[2]).slice(-3), ["bull", "none", "none"]);
});

test("超過 13 週的資料只畫最近 12 根；可以指定要幾週", () => {
  const longer = [week("2026-08-07", 58.5, 175, 63, 330, 667000), week("2026-08-14", 58.2, 173, 63, 330, 667500), ...WEEKS];
  const read = loadRead();
  assert.equal(read(longer).levels[0].bars.length, 12);
  assert.deepEqual(kinds(read(longer).levels[0]), kinds(read(WEEKS).levels[0]));
  assert.equal(read(longer).levels[2].read, "12 週前 668,120 人，現在 671,382 人（+3,262）");
  assert.equal(read(longer, 4).levels[0].bars.length, 4);
});

test("連續減少、只增加一週、和前一週一樣的結論與高低", () => {
  const read = loadRead();
  const falling = [week("2026-10-23", 60, 100, 65, 200, 1000), week("2026-10-30", 59.5, 99, 65, 200, 1010), week("2026-11-06", 59.1, 98, 65, 200, 1020), week("2026-11-13", 58.9, 97, 64.8, 199, 1030)];
  assert.deepEqual([read(falling).title, read(falling).state, read(falling).levels[0].change], ["連 3 週減少", "bear", "較前一週 −0.20 個百分點"]);
  assert.deepEqual([read(falling).levels[2].change, read(falling).levels[2].kind], ["較前一週 +10 人", "bull"]);
  const turned = [...falling.slice(0, 3), week("2026-11-13", 59.4, 99, 65, 200, 1020)];
  assert.deepEqual([read(turned).title, read(turned).state], ["這一週增加", "bull"]);
  const same = [...falling.slice(0, 3), week("2026-11-13", 59.1, 98, 65, 200, 1020)];
  assert.deepEqual([read(same).title, read(same).state, read(same).levels[0].change], ["和前一週一樣", "flat", "和前一週一樣"]);
});

test("連幾週照全部的資料算，不會被只畫 12 根截斷", () => {
  let pct = 50;
  const rising = Array.from({ length: 21 }, (_, index) => week(new Date(Date.UTC(2026, 5, 5 + index * 7)).toISOString().slice(0, 10), (pct += 0.1), 100 + index, 60, 200, 1000));
  const read = loadRead()(rising);
  assert.equal(read.title, "連 20 週增加");
  assert.equal(read.levels[0].bars.length, 12);
});

test("中間缺一週（那一週沒有這一檔的資料）：連幾週數到缺的地方為止，幾週前照日期算", () => {
  // 10-16 那一週沒有資料
  const gapped = [week("2026-10-02", 57.0, 170, 63, 333, 1000), week("2026-10-09", 57.2, 172, 63, 334, 1010), week("2026-10-23", 57.5, 172, 63, 335, 1020),
    week("2026-10-30", 57.8, 174, 63, 336, 1030), week("2026-11-06", 58.0, 175, 63, 337, 1040)];
  const read = loadRead()(gapped);
  assert.equal(read.title, "連 2 週增加");                                     // 11-06、10-30 兩週；10-23 和前一筆隔了兩週，不算連續
  assert.equal(read.levels[2].read, "5 週前 1,000 人，現在 1,040 人（+40）");    // 四筆之前，但日期是五週前
});

test("不到 12 週：有幾週畫幾週", () => {
  const read = loadRead()(WEEKS.slice(-2));
  assert.equal(read.title, "這一週增加");
  assert.deepEqual(read.levels[0].bars, [{ kind: "bull", size: 1 }]);
  assert.equal(read.levels[2].read, "1 週前 671,382 人，現在 671,382 人（0）");
});

test("只有一週：不畫小柱、不寫連幾週、是平的；三個級距的數字照寫", () => {
  const read = loadRead()(WEEKS.slice(-1));
  assert.deepEqual([read.date, read.title, read.note, read.state], ["2026-11-13", null, "千張以上大戶", "flat"]);
  assert.deepEqual(read.levels.map((level) => [level.value, level.change, level.kind, level.bars]), [
    ["58.56%", null, null, []], ["63.30%", null, null, []], ["671,382 人", null, null, []],
  ]);
  assert.deepEqual(read.levels.map((level) => level.read), ["持股 58.56%，179 人", "持股 63.30%，337 人", "全部股東 671,382 人"]);
});

test("沒有資料回 null；缺某個級距的數字就不給那個級距", () => {
  const read = loadRead();
  assert.equal(read([]), null);
  assert.equal(read(null), null);
  const partial = [{ date: "2026-11-06", pct_1000: 58.43, holders_1000: 175 }, { date: "2026-11-13", pct_1000: 58.56, holders_1000: 179 }];
  assert.deepEqual(read(partial).levels.map((level) => level.key), ["pct1000"]);
});
