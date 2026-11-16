// 資金地形的版面計算（立體重設計 B 包第 1 片；規格指定唯一新增的測試接縫）。
// 只驗對外的輸入輸出：給產業資金流向的產業清單，回傳每棟樓屬於哪個街區、排在第幾格、佔地與樓高。
// 單位都是「格」，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function loadLayout() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/terrain-layout.js", ROOT), "utf8"), { window, Math, Number, Object, Array, Map, Set, String, JSON });
  // 頁面腳本在另一個執行環境跑，回傳的陣列和這裡的不是同一種原型；轉一次 JSON 再比，順便確認結果是純資料。
  return (bubbles) => JSON.parse(JSON.stringify(window.TerrainLayout.layout(bubbles)));
}

const layout = loadLayout();
const industry = (name, quadrant, amount_today, member_count = 10) => ({ name, quadrant, amount_today, member_count });
const towerOf = (result, name) => result.towers.find((tower) => tower.name === name);

test("每個產業落在自己資金狀態的街區", () => {
  const result = layout([
    industry("甲", "leading", 100), industry("乙", "weakening", 80),
    industry("丙", "improving", 60), industry("丁", "lagging", 40),
  ]);
  assert.deepEqual(result.towers.map((tower) => [tower.name, tower.quadrant]).sort(),
    [["丁", "lagging"], ["丙", "improving"], ["乙", "weakening"], ["甲", "leading"]]);
  for (const block of result.blocks) {
    assert.ok(block.towers.every((tower) => tower.quadrant === block.quadrant), block.quadrant);
  }
});

test("四個街區的方位：資金回補左上、資金領先右上、資金減速右下、資金落後左下", () => {
  const sides = Object.fromEntries(layout([]).blocks.map((block) => [block.quadrant, [block.side.x, block.side.y]]));
  assert.deepEqual(sides, { improving: [-1, 1], leading: [1, 1], weakening: [1, -1], lagging: [-1, -1] });
});

test("沒有象限的產業不出現在結果裡", () => {
  const result = layout([industry("有", "leading", 10), industry("沒有", null, 999), { name: "缺欄位", amount_today: 5, member_count: 3 }]);
  assert.deepEqual(result.towers.map((tower) => tower.name), ["有"]);
});

test("區內依今日成交額由大到小排列，最大的排在最靠近中央十字的那一格", () => {
  const result = layout([
    industry("小", "improving", 5), industry("最大", "improving", 500), industry("中", "improving", 50),
    industry("次大", "improving", 200), industry("最小", "improving", 1),
  ]);
  const block = result.blocks.find((item) => item.quadrant === "improving");
  assert.deepEqual(block.towers.map((tower) => tower.name), ["最大", "次大", "中", "小", "最小"]);
  assert.deepEqual([block.towers[0].col, block.towers[0].row], [0, 0]);
  // 一格只放一棟，都落在這個街區的格數之內；越後面的離十字越遠（先排滿靠近十字的那一列）。
  const slots = block.towers.map((tower) => tower.row * block.cols + tower.col);
  assert.deepEqual(slots, [0, 1, 2, 3, 4]);
  assert.ok(block.towers.every((tower) => tower.col < block.cols && tower.row < block.rows));
});

test("樓高隨今日成交額遞增，最大的有上限，最小的仍有看得見的高度", () => {
  const amounts = [0.5, 3, 40, 900, 3500];
  const heights = (scale) => {
    const result = layout(amounts.map((amount, index) => industry(`產業${index}`, "leading", amount * scale)));
    return amounts.map((_, index) => towerOf(result, `產業${index}`).height);
  };
  const base = heights(1);
  for (let i = 1; i < base.length; i += 1) assert.ok(base[i] > base[i - 1], `${base[i]} > ${base[i - 1]}`);
  assert.ok(base[0] > 0.1, `最小的樓高 ${base[0]} 格`);
  // 上限：樓高看的是「相對最大的那個產業」，整體金額放大一千倍，樓不會跟著長高。
  assert.deepEqual(heights(1000), base);
  assert.ok(base.at(-1) < 5, `最高的樓 ${base.at(-1)} 格`);
});

test("佔地隨成員檔數變大，但不會佔滿一格（相鄰的樓不會黏在一起）", () => {
  const result = layout([industry("多", "lagging", 10, 200), industry("少", "lagging", 10, 5)]);
  assert.ok(towerOf(result, "多").footprint > towerOf(result, "少").footprint);
  assert.ok(result.towers.every((tower) => tower.footprint > 0.3 && tower.footprint < 1));
});

test("缺今日成交額或成員檔數的產業，以最小樓高或最小佔地呈現，不出錯也不消失", () => {
  const result = layout([
    industry("正常", "weakening", 100, 50),
    { name: "缺成交額", quadrant: "weakening", member_count: 50 },
    { name: "成交額不是數字", quadrant: "weakening", amount_today: null, member_count: 50 },
    { name: "缺檔數", quadrant: "weakening", amount_today: 100 },
    industry("零成交", "weakening", 0, 0),
  ]);
  assert.equal(result.towers.length, 5);
  const floor = towerOf(result, "零成交");
  assert.equal(towerOf(result, "缺成交額").height, floor.height);
  assert.equal(towerOf(result, "成交額不是數字").height, floor.height);
  assert.equal(towerOf(result, "缺檔數").footprint, floor.footprint);
  assert.equal(towerOf(result, "缺檔數").height, towerOf(result, "正常").height);
  assert.ok(result.towers.every((tower) => Number.isFinite(tower.height) && Number.isFinite(tower.footprint)));
  // 全部都沒有成交額也不能出錯
  assert.ok(layout([{ name: "只有名字", quadrant: "leading" }]).towers[0].height > 0);
});

test("同一份輸入永遠得到同一份輸出，和輸入的順序無關，也不改動輸入", () => {
  const input = [
    industry("甲", "leading", 30), industry("乙", "leading", 30), industry("丙", "lagging", 7),
    industry("丁", "improving", 30), industry("戊", "leading", 2),
  ];
  const before = JSON.stringify(input);
  const first = layout(input);
  assert.deepEqual(layout(input), first);
  assert.deepEqual(layout([...input].reverse()), first);
  assert.equal(JSON.stringify(input), before);
});

test("全體依今日成交額排名（名稱牌要標前幾大用的）", () => {
  const result = layout([industry("第三", "lagging", 5), industry("第一", "improving", 90), industry("第二", "leading", 40)]);
  assert.deepEqual(result.towers.map((tower) => [tower.name, tower.rank]).sort((a, b) => a[1] - b[1]),
    [["第一", 0], ["第二", 1], ["第三", 2]]);
});

test("已發布的 34 個產業跑得過：每個產業一棟樓，數值都合理", () => {
  const fixture = JSON.parse(readFileSync(new URL("cloud/test/fixtures/rotation-terrain.json", ROOT), "utf8"));
  assert.equal(fixture.bubbles.length, 34);
  const result = layout(fixture.bubbles);
  assert.equal(result.towers.length, 34);
  assert.deepEqual(result.towers.map((tower) => tower.name).sort(), fixture.bubbles.map((bubble) => bubble.name).sort());
  const count = (quadrant) => fixture.bubbles.filter((bubble) => bubble.quadrant === quadrant).length;
  for (const block of result.blocks) assert.equal(block.towers.length, count(block.quadrant), block.quadrant);
  const biggest = [...fixture.bubbles].sort((a, b) => b.amount_today - a.amount_today)[0];
  assert.equal(result.towers.find((tower) => tower.rank === 0).name, biggest.name);
  for (const tower of result.towers) {
    assert.ok(tower.height > 0.1 && tower.height < 5 && tower.footprint > 0.3 && tower.footprint < 1, JSON.stringify(tower));
  }
  // 街區內沒有兩棟樓排在同一格
  for (const block of result.blocks) {
    const slots = new Set(block.towers.map((tower) => `${tower.col},${tower.row}`));
    assert.equal(slots.size, block.towers.length, block.quadrant);
  }
});
