// 設定頁「AI 模型」籤（本人 2026-11-19 晚，樣品 C：每列寫現值、點開才選）的算法：列怎麼寫、頂端剩餘那一行、
// 存檔前要不要先問（只有改到大盤預測或每日判斷才問）。畫面另由瀏覽器旅程驗（browser_settings_journey.py）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../web/settings.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, Date, Intl, Math, Number, String, JSON });
const plain = (fn) => (...args) => JSON.parse(JSON.stringify(fn(...args)));
const S = { modelsView: plain(window.MarketSettings.modelsView), confirmNotes: plain(window.MarketSettings.confirmNotes),
  tabsFor: plain(window.MarketSettings.tabsFor), nextChoice: plain(window.MarketSettings.nextChoice) };

const ARGON = "gemini-4-argon", FLASH = "gemini-3.8-flash";
const DATA = {
  available: true, ready: true, updated_at: "2026-11-19T07:20:00Z",
  tasks: [
    { key: "forecast", model: ARGON, effort: "medium", saved: false },
    { key: "watch_judge", model: FLASH, effort: "high", saved: true },
    { key: "watch_nominate", model: FLASH, effort: "high", saved: false },
    { key: "plain", model: ARGON, effort: "medium", saved: false },
    { key: "digest", model: ARGON, effort: "medium", saved: false },
  ],
  quota: { argon: 0.18, flash: 0.7149 },
  unread: [],
};

test("站主多一顆「AI 模型」籤，排在用量後面", () => {
  assert.deepEqual(S.tabsFor({ usage: true, models: true, notify: true }), ["usage", "models", "notify", "look"]);
  assert.deepEqual(S.tabsFor({ usage: true, notify: true }), ["usage", "notify", "look"]);
});

test("每列寫任務名稱與現值；大盤預測註明晚間、晨間、重播一起", () => {
  const view = S.modelsView(DATA, {});
  assert.deepEqual(view.rows.map((r) => [r.key, r.name, r.value]), [
    ["forecast", "大盤預測", "Argon・中"],
    ["watch_judge", "自選股每日判斷", "Flash・高"],
    ["watch_nominate", "提名國外同類股", "Flash・高"],
    ["plain", "籌碼與資金白話", "Argon・中"],
    ["digest", "站主 AI 解讀", "Argon・中"],
  ]);
  assert.equal(view.rows[0].sub, "晚間・晨間・重播一起");
  assert.equal(view.dirty, false);
});

test("頂端一行：Argon、Flash 本週剩多少，剩不到兩成標低", () => {
  assert.deepEqual(S.modelsView(DATA, {}).quota, [
    { label: "Argon 本週剩", percent: 18, low: true },
    { label: "Flash 本週剩", percent: 71, low: false },
  ]);
  assert.deepEqual(S.modelsView({ ...DATA, quota: { argon: null, flash: null } }, {}).quota, []);
});

test("改了還沒存：那一列換成新值、標有改動", () => {
  const view = S.modelsView(DATA, { forecast: { model: FLASH, effort: "medium" } });
  assert.equal(view.rows[0].value, "Flash・中");
  assert.equal(view.rows[0].changed, true);
  assert.equal(view.dirty, true);
  // 改回原值就不算改動
  assert.equal(S.modelsView(DATA, { forecast: { model: ARGON, effort: "medium" } }).dirty, false);
});

test("說明列：生效時間與上次修改時間（台灣時間）", () => {
  assert.equal(S.modelsView(DATA, {}).foot, "存檔後下一班排程開始用（大盤預測從下一個晚間版起）。上次修改 11-19 15:20。");
  assert.equal(S.modelsView({ ...DATA, updated_at: null }, {}).foot, "存檔後下一班排程開始用（大盤預測從下一個晚間版起）。");
});

test("最近一班沒讀到設定的排程列出來", () => {
  const view = S.modelsView({ ...DATA, unread: [{ task: "forecast-ledger", read_at: "2026-11-19T13:05:00Z" }] }, {});
  assert.deepEqual(view.unread, ["晚間事前預測上一班（11-19 21:05）沒讀到你的設定，用了預設。"]);
});

test("資料庫還沒更新時說明存不了", () => {
  assert.equal(S.modelsView({ ...DATA, ready: false }, {}).notReady, "網站資料庫還沒更新，現在顯示的是預設，暫時不能存。");
  assert.equal(S.modelsView(DATA, {}).notReady, "");
});

test("切到 Flash 時思考強度自動帶「高」（本人 11-19 晚：用 Flash 的任務一律高），之後仍可自己改", () => {
  assert.deepEqual(S.nextChoice({ model: ARGON, effort: "medium" }, { model: FLASH }), { model: FLASH, effort: "high" });
  assert.deepEqual(S.nextChoice({ model: FLASH, effort: "high" }, { effort: "low" }), { model: FLASH, effort: "low" });
  // 切回 Argon 不動強度；已經是 Flash 再按一次 Flash 也不動
  assert.deepEqual(S.nextChoice({ model: FLASH, effort: "low" }, { model: ARGON }), { model: ARGON, effort: "low" });
  assert.deepEqual(S.nextChoice({ model: FLASH, effort: "low" }, { model: FLASH }), { model: FLASH, effort: "low" });
});

test("只有改到大盤預測或每日判斷，存檔前才先問", () => {
  assert.deepEqual(S.confirmNotes(DATA, { plain: { model: FLASH, effort: "low" } }), []);
  assert.deepEqual(S.confirmNotes(DATA, { forecast: { model: FLASH, effort: "medium" } }), [
    "大盤預測改成 Flash・中：成績另開一組從頭累積，正式起算要重數連續 5 天。從下一個晚間版開始用。",
  ]);
  assert.deepEqual(S.confirmNotes(DATA, { watch_judge: { model: FLASH, effort: "low" }, digest: { model: FLASH, effort: "low" } }), [
    "自選股每日判斷改成 Flash・低：成績另開一組從頭累積。下一班開始用。",
  ]);
  // 改了又改回來不算
  assert.deepEqual(S.confirmNotes(DATA, { forecast: { model: ARGON, effort: "medium" } }), []);
});
