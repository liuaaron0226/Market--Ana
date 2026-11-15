// 收復線／防守線點開之後那張圖：把兩條線、收盤、過去隔天收在三區的比例，整理成由上到下的五列。
// 只驗對外的輸入輸出，不碰畫面（本人 2026-11-15 看樣品後答「照這樣做」）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadRead() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/line-zones.js", ROOT), "utf8"), { window });
  // 頁面腳本在另一個執行環境跑；轉一次 JSON 再比，順便確認結果是純資料。
  return (input) => JSON.parse(JSON.stringify(window.LineZones.read(input)));
}

// 樣本資料（資料日 2026-11-06）
const SAMPLE = {
  upper: 40435.83, lower: 37837.05, close: 39701.64, count: 35,
  percent: { stand_back: 14.3, between: 45.7, continue_break: 40 },
};

test("由上到下五列：站回、收復線、兩線間、防守線、跌破——和 K 線圖上的高低一樣", () => {
  const { rows, count } = loadRead()(SAMPLE);
  assert.equal(count, 35);
  assert.deepEqual(rows.map((row) => [row.kind, row.key, row.label]), [
    ["zone", "stand_back", "站回"],
    ["line", "upper", "收復線"],
    ["zone", "between", "兩線間"],
    ["line", "lower", "防守線"],
    ["zone", "continue_break", "跌破"],
  ]);
});

test("三區各有過去隔天收在那一區的比例；收盤落在哪一區只標一區", () => {
  const zones = loadRead()(SAMPLE).rows.filter((row) => row.kind === "zone");
  assert.deepEqual(zones.map((zone) => [zone.key, zone.percent, zone.text, zone.here]), [
    ["stand_back", 14.3, "14%", false],
    ["between", 45.7, "46%", true],
    ["continue_break", 40, "40%", false],
  ]);
});

test("兩條線各寫離收盤多遠：收復線還差幾點、防守線還有幾點", () => {
  const lines = loadRead()(SAMPLE).rows.filter((row) => row.kind === "line");
  assert.deepEqual(lines.map((line) => [line.key, line.value, line.gap]), [
    ["upper", 40435.83, "還差 734 點"],
    ["lower", 37837.05, "還有 1,865 點"],
  ]);
});

test("收盤已經站上收復線：標在站回那一區，收復線寫已站上", () => {
  const { rows } = loadRead()({ ...SAMPLE, close: 40500 });
  assert.deepEqual(rows.filter((row) => row.kind === "zone").map((zone) => zone.here), [true, false, false]);
  assert.equal(rows[1].gap, "已站上 64 點");
  assert.equal(rows[3].gap, "還有 2,663 點");
});

test("收盤剛好在收復線上算站回（和首頁判斷落點的規則一樣：大於等於）", () => {
  const { rows } = loadRead()({ ...SAMPLE, close: SAMPLE.upper });
  assert.equal(rows[0].here, true);
  assert.equal(rows[1].gap, "剛好在線上");
});

test("收盤已經跌破防守線：標在跌破那一區，防守線寫已跌破；剛好在防守線上還算兩線間", () => {
  const read = loadRead();
  const broken = read({ ...SAMPLE, close: 37800 }).rows;
  assert.deepEqual(broken.filter((row) => row.kind === "zone").map((zone) => zone.here), [false, false, true]);
  assert.equal(broken[3].gap, "已跌破 37 點");
  assert.equal(broken[1].gap, "還差 2,636 點");
  const onLine = read({ ...SAMPLE, close: SAMPLE.lower }).rows;
  assert.equal(onLine[2].here, true);
  assert.equal(onLine[3].gap, "剛好在線上");
});

test("沒有收盤價：不標哪一區、不寫距離；比例照樣有", () => {
  const { rows } = loadRead()({ ...SAMPLE, close: NaN });
  assert.ok(rows.every((row) => row.kind !== "zone" || row.here === false));
  assert.deepEqual([rows[1].gap, rows[3].gap], ["", ""]);
  assert.equal(rows[4].text, "40%");
});

test("比例缺資料或不是數字：寫一槓、橫條長度是 0，不寫成 0%", () => {
  const { rows, count } = loadRead()({ ...SAMPLE, count: undefined, percent: { stand_back: null, between: "x" } });
  assert.equal(count, null);
  assert.deepEqual(rows.filter((row) => row.kind === "zone").map((zone) => [zone.percent, zone.text]), [[0, "—"], [0, "—"], [0, "—"]]);
});

test("比例超出 0 到 100 時橫條不會畫出界", () => {
  const { rows } = loadRead()({ ...SAMPLE, percent: { stand_back: -5, between: 130, continue_break: 0 } });
  assert.deepEqual(rows.filter((row) => row.kind === "zone").map((zone) => zone.percent), [0, 100, 0]);
});

test("資料裡的數字寫成字串也認得；null、空字串不會被當成 0", () => {
  const { rows, count } = loadRead()({ upper: "40435.83", lower: "37837.05", close: "39701.64", count: "35",
    percent: { stand_back: "14.3", between: "", continue_break: null } });
  assert.equal(count, 35);
  assert.deepEqual(rows.filter((row) => row.kind === "zone").map((zone) => [zone.text, zone.here]), [["14%", false], ["—", true], ["—", false]]);
  assert.equal(rows[1].gap, "還差 734 點");
});

test("差不到半點寫剛好在線上，不寫還差 0 點", () => {
  const { rows } = loadRead()({ ...SAMPLE, close: SAMPLE.upper - 0.3 });
  assert.equal(rows[1].gap, "剛好在線上");
  assert.equal(rows[2].here, true);   // 落在哪一區照實際的數字，不照四捨五入
});

test("兩條線有一條不是數字：不標哪一區（不能因為比不出來就算成跌破）", () => {
  const { rows } = loadRead()({ ...SAMPLE, lower: NaN });
  assert.ok(rows.every((row) => row.kind !== "zone" || row.here === false));
});

function loadModule() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/line-zones.js", ROOT), "utf8"), { window });
  return window.LineZones;
}

test("zoneOf：首頁標頭判斷現價落點用的同一條規則", () => {
  const { zoneOf } = loadModule();
  assert.deepEqual([40436, 40435.83, 39000, 37837.05, 37837].map((price) => zoneOf(price, 40435.83, 37837.05)),
    ["stand_back", "stand_back", "between", "between", "continue_break"]);
  assert.deepEqual([zoneOf(NaN, 40435.83, 37837.05), zoneOf(39000, NaN, 37837.05), zoneOf(39000, 40435.83, NaN)], [null, null, null]);
});

test("dayOf：線是今天畫的才寫今天；隔天再看，記號寫收盤、句子裡寫日期", () => {
  const { dayOf } = loadModule();
  assert.deepEqual({ ...dayOf("2026-11-06", "2026-11-06") }, { mark: "今天", word: "今天" });
  assert.deepEqual({ ...dayOf("2026-11-06", "2026-11-09") }, { mark: "收盤", word: " 11/06 " });
  assert.deepEqual({ ...dayOf("", "2026-11-09") }, { mark: "收盤", word: "當天" });
  assert.deepEqual({ ...dayOf(undefined, undefined) }, { mark: "收盤", word: "當天" });
});
