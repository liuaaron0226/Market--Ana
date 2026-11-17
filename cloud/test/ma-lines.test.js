// 自己設定的均線（本人 2026-11-17）：「我希望我可以自己設定均線，不要有上限」；首頁大盤與個股頁「共用一組」；顏色「讓我自己挑」。
// web/ma-lines.js：一組均線 [{ period, color }]，color 是 8 色的代號；存在這台裝置（localStorage 'ma-lines'）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = new URL("../../web/ma-lines.js", import.meta.url);

function load(stored = {}, { broken = false } = {}) {
  const data = { ...stored };
  const localStorage = {
    getItem: key => { if (broken) throw new Error("私密視窗"); return key in data ? data[key] : null; },
    setItem: (key, value) => { if (broken) throw new Error("私密視窗"); data[key] = String(value); },
  };
  const sandbox = { window: {}, localStorage, JSON, Math, Number, Array, Set, Object, String };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(SOURCE, "utf8"), sandbox, { filename: "ma-lines.js" });
  return { MaLines: sandbox.window.MaLines, data };
}
const plain = value => JSON.parse(JSON.stringify(value));

test("沒存過：預設 MA18（橘）、MA45（金），和原本一樣", () => {
  const { MaLines } = load();
  assert.deepEqual(plain(MaLines.read()), [{ period: 18, color: "orange" }, { period: 45, color: "gold" }]);
});

test("8 種顏色，每種有名稱與 CSS 變數；紅綠不在裡面（留給 K 棒與預測線）", () => {
  const { MaLines } = load();
  assert.equal(MaLines.PALETTE.length, 8);
  assert.deepEqual(plain(MaLines.PALETTE.map(item => item.name)), ["橘", "金", "藍", "灰", "紫", "青", "褐", "玫瑰"]);
  assert.ok(MaLines.PALETTE.every(item => item.token.startsWith("--")));
  assert.ok(!MaLines.PALETTE.some(item => ["--up", "--down"].includes(item.token)));
});

test("不設上限：條數、天數都不擋（舊的四條、240 日上限拿掉）", () => {
  const { MaLines } = load();
  const many = [5, 10, 20, 60, 120, 240, 480, 600, 800].map(period => ({ period }));
  const lines = MaLines.normalize(many);
  assert.deepEqual(plain(lines.map(line => line.period)), [5, 10, 20, 60, 120, 240, 480, 600, 800]);
});

test("整理：重複的天數只留第一條、小於 2 或不是數字的丟掉、小數四捨五入", () => {
  const { MaLines } = load();
  const lines = MaLines.normalize([{ period: 20 }, { period: 20 }, { period: 1 }, { period: "abc" }, { period: 9.6 }]);
  assert.deepEqual(plain(lines.map(line => line.period)), [20, 10]);
});

test("顏色：沒給或不認得的，給一個還沒用過的；8 色都用過才從頭輪", () => {
  const { MaLines } = load();
  const lines = MaLines.normalize([{ period: 5, color: "blue" }, { period: 10 }, { period: 20, color: "紅色" }]);
  assert.deepEqual(plain(lines.map(line => line.color)), ["blue", "orange", "gold"]);
  const nine = MaLines.normalize(Array.from({ length: 9 }, (_, index) => ({ period: index + 2 })));
  assert.equal(new Set(nine.slice(0, 8).map(line => line.color)).size, 8, "前 8 條各一色");
  assert.equal(nine[8].color, "orange", "第 9 條從頭輪");
});

test("加一條：天數取 5、10、20、60、120、240 裡還沒有的，顏色取還沒用過的", () => {
  const { MaLines } = load();
  const lines = MaLines.add([{ period: 18, color: "orange" }, { period: 5, color: "gold" }]);
  assert.deepEqual(plain(lines[2]), { period: 10, color: "blue" });
  const full = [5, 10, 20, 60, 120, 240].map((period, index) => ({ period, color: MaLines.PALETTE[index].key }));
  assert.equal(MaLines.add(full)[6].period, 480, "六個常用的都有了：最長那條的兩倍");
});

test("存起來再讀回來一樣；兩頁共用同一組（同一個存放位置）", () => {
  const { MaLines, data } = load();
  MaLines.write([{ period: 7, color: "rose" }, { period: 30, color: "teal" }]);
  assert.ok(data["ma-lines"], "存在 ma-lines");
  const again = load(data).MaLines;
  assert.deepEqual(plain(again.read()), [{ period: 7, color: "rose" }, { period: 30, color: "teal" }]);
});

test("個股頁以前存的天數（stock-ma-periods）接過來，顏色照順序給", () => {
  const { MaLines } = load({ "stock-ma-periods": JSON.stringify([10, 60, 120]) });
  assert.deepEqual(plain(MaLines.read()), [{ period: 10, color: "orange" }, { period: 60, color: "gold" }, { period: 120, color: "blue" }]);
});

test("存不了（私密視窗）或存的東西壞了：用預設值，不出錯", () => {
  assert.deepEqual(plain(load({}, { broken: true }).MaLines.read().map(line => line.period)), [18, 45]);
  assert.doesNotThrow(() => load({}, { broken: true }).MaLines.write([{ period: 5 }]));
  assert.deepEqual(plain(load({ "ma-lines": "{壞掉" }).MaLines.read().map(line => line.period)), [18, 45]);
  assert.deepEqual(plain(load({ "ma-lines": "[]" }).MaLines.read().map(line => line.period)), [18, 45], "空的也用預設");
});

test("代號換成 CSS 變數與中文名稱（畫圖時才取值，換深淺色不用重設）；不認得的當成第一色", () => {
  const { MaLines } = load();
  assert.equal(MaLines.tokenOf("teal"), "--ma-teal");
  assert.equal(MaLines.nameOf("rose"), "玫瑰");
  assert.equal(MaLines.tokenOf("紅色"), "--accent");
});

test("改某一條的天數：和別條重複、小於 2 或不是數字就不改（回傳 null，頁面把格子改回原本的數字），不會默默少一條", () => {
  const { MaLines } = load();
  const lines = [{ period: 18, color: "orange" }, { period: 45, color: "gold" }];
  assert.deepEqual(plain(MaLines.setPeriod(lines, 1, "30")), [{ period: 18, color: "orange" }, { period: 30, color: "gold" }]);
  assert.equal(MaLines.setPeriod(lines, 1, "18"), null, "和第一條重複");
  assert.equal(MaLines.setPeriod(lines, 0, "1"), null);
  assert.equal(MaLines.setPeriod(lines, 0, ""), null);
  assert.deepEqual(plain(MaLines.setPeriod(lines, 0, "18.4")), plain(lines), "四捨五入後和自己一樣：照舊");
});
