// 液態玻璃與金屬的高光（本人 2026-11-16：自選與庫存頁選了整頁玻璃的版本）：
// 每一條的高光位置、亮度、明暗交界、漂移的速度各自不同、不同步；同一條每次畫出來要一樣（清單會重畫）。只驗算出來的設定，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("../../web/liquid-spots.js", import.meta.url), "utf8"), { window });
  return key => JSON.parse(JSON.stringify(window.LiquidSpots.of(key)));
}
const number = text => Number.parseFloat(text);

test("同一條每次算出來都一樣；不同的條不一樣", () => {
  const of = load();
  assert.deepEqual(of("2330 base"), of("2330 base"));
  assert.notDeepEqual(of("2330 base"), of("2330 gain"));
  assert.notDeepEqual(of("2330 base"), of("2603 base"));
});

test("算出來的是樣式用得到的設定：高光來回的兩個位置、亮度、明暗交界、來回一趟多久、從哪裡開始", () => {
  const spot = load()("2330 base");
  assert.deepEqual(Object.keys(spot).sort(), ["--dd", "--dt", "--e1", "--e2", "--hi", "--hl", "--hs", "--hz"]);
  for (const name of ["--e1", "--e2", "--hl", "--hz"]) assert.match(spot[name], /^\d+(\.\d+)?%$/, name);
  for (const name of ["--dt", "--dd"]) assert.match(spot[name], /^-?\d+(\.\d+)?s$/, name);
});

test("每一項都在範圍裡：高光一端在左邊、一端在右邊；慢慢漂（5.5～11 秒一趟）；開始的時間錯開", () => {
  const of = load();
  const codes = ["2330", "2603", "0050", "6669", "2382", "3017", "3324", "2609", "2454", "1101", "2317", "9958"];
  const spots = codes.flatMap(code => ["base", "gain", "lack"].map(kind => of(`${code} ${kind}`)));
  for (const spot of spots) {
    const [low, high] = [number(spot["--e1"]), number(spot["--e2"])].sort((a, b) => a - b);
    assert.ok(low >= 6 && low <= 38 && high >= 58 && high <= 94, JSON.stringify(spot));
    assert.ok(number(spot["--hi"]) >= 0.45 && number(spot["--hi"]) <= 0.85, spot["--hi"]);
    assert.ok(number(spot["--dt"]) >= 5.5 && number(spot["--dt"]) <= 11, spot["--dt"]);
    assert.ok(number(spot["--dd"]) <= 0 && number(spot["--dd"]) >= -9, spot["--dd"]);
  }
  // 不同步：36 條裡來回一趟的時間至少有 20 種，往左開始與往右開始的都有
  assert.ok(new Set(spots.map(spot => spot["--dt"])).size >= 20);
  const leftFirst = spots.filter(spot => number(spot["--e1"]) < number(spot["--e2"])).length;
  assert.ok(leftFirst > 6 && leftFirst < 30, String(leftFirst));
});
