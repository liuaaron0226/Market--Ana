// 首頁「各產業強勢股」的排法（本人 2026-11-20 選排法 A：一次看一種資金狀態）。
// 只驗對外的輸入輸出：給產業資金流向的產業清單與各產業的強勢股，回傳四個資金狀態各自要畫哪些卡、哪些產業收成一行字。不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/rotation-strong.js", ROOT), "utf8"), { window, Number, Object, Array, Map, Set, String, JSON });
  // 頁面腳本在另一個執行環境跑，回傳的陣列和這裡的不是同一種原型；轉一次 JSON 再比，順便確認結果是純資料。
  return {
    plan: (bubbles, groups) => JSON.parse(JSON.stringify(window.RotationStrong.plan(bubbles, groups))),
    caption: (strong) => window.RotationStrong.caption(strong),
  };
}

const { plan, caption } = load();
const industry = (name, quadrant) => ({ name, quadrant });
const stock = (code, name, gain_pct) => ({ code, name, gain_pct, today_pct: 1, limit_up: false });

test("四個資金狀態固定照 領先、減速、回補、落後 排，各自帶產業數", () => {
  const result = plan([
    industry("半導體業", "leading"), industry("光電業", "weakening"), industry("金融保險業", "improving"),
    industry("生技醫療業", "improving"), industry("水泥工業", "lagging"),
  ], {});
  assert.deepEqual(result.map((state) => [state.quadrant, state.count]),
    [["leading", 1], ["weakening", 1], ["improving", 2], ["lagging", 1]]);
});

test("有強勢股的產業各一張卡，照產業資金流向原本的順序", () => {
  const groups = {
    "半導體業": [stock("6788", "華景電", 9.6), stock("6708", "天擎", 8.23)],
    "電腦及週邊設備業": [stock("5490", "同亨", 14.35)],
  };
  const [leading] = plan([industry("半導體業", "leading"), industry("電腦及週邊設備業", "leading")], groups);
  assert.deepEqual(leading.cards, [
    { name: "半導體業", stocks: groups["半導體業"] },
    { name: "電腦及週邊設備業", stocks: groups["電腦及週邊設備業"] },
  ]);
  assert.deepEqual(leading.quiet, []);
});

test("沒有上漲股的產業不畫卡，名稱收在 quiet（清單是空的，或這一批根本沒有這個產業）", () => {
  const result = plan(
    [industry("航運業", "lagging"), industry("水泥工業", "lagging"), industry("造紙工業", "lagging")],
    { "航運業": [stock("2610", "華航", 6.1)], "水泥工業": [] },
  );
  const lagging = result.find((state) => state.quadrant === "lagging");
  assert.deepEqual(lagging.cards.map((card) => card.name), ["航運業"]);
  assert.deepEqual(lagging.quiet, ["水泥工業", "造紙工業"]);
  assert.equal(lagging.count, 3);
});

test("資金狀態算不出來的產業不放進任何一種", () => {
  const result = plan([industry("農業科技業", null), industry("半導體業", "leading")], { "農業科技業": [stock("1", "甲", 1)] });
  assert.deepEqual(result.map((state) => state.count), [1, 0, 0, 0]);
});

test("說明文字照這一批帶來的規則數字寫", () => {
  assert.equal(caption({ window_days: 3, limit: 3, min_avg_turnover: 50_000_000 }),
    "近 3 日漲最多的前 3 檔・平均每天成交 5,000 萬元以上");
});
