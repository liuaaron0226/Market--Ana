// 設定浮板（本人 2026-11-17 選樣品 A：齒輪浮板＋三顆籤）的算法：存起來的偏好、Gemini 用量怎麼顯示、哪幾顆籤要出現。
// 畫面與開關另由瀏覽器旅程驗（browser_settings_journey.py）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../web/settings.js", import.meta.url), "utf8");
function load() {
  const window = {};
  vm.runInNewContext(source, { window, Date, Intl, Math, Number, String, JSON });
  // vm 裡做出來的物件原型和這邊不同，比對前轉成一般的值
  const plain = (fn) => (...args) => JSON.parse(JSON.stringify(fn(...args)));
  const S = window.MarketSettings;
  return { ...S, DESKS: JSON.parse(JSON.stringify(S.DESKS)), readPrefs: plain(S.readPrefs), usageView: plain(S.usageView), tabsFor: plain(S.tabsFor) };
}
const S = load();
const store = (values) => ({ getItem: (key) => values[key] ?? null });

// 2026-11-17 實測的用量（/api/settings/gemini-usage 的樣子）。現在：台灣 11-17 16:30
const NOW = Date.parse("2026-11-17T08:30:00Z");
const USAGE = {
  available: true, read_at: "2026-11-17T08:05:00Z", last_run_at: "2026-11-17T08:05:00Z",
  flash: { five_hour: { remaining: 0.8861, reset_time: "2026-11-17T10:21:54Z" }, weekly: { remaining: 0.9282, reset_time: "2026-11-22T03:36:29Z" } },
  argon: { weekly: { remaining: 0.1584, reset_time: "2026-11-24T07:56:20Z" } },
  today: [
    { task: "forecast-ledger", runs: 1, model: "gemini-4-argon" },
    { task: "some-new-task", runs: 2, model: null },
    { task: "watch-ledger", runs: 2, model: "gemini-3.8-flash" },
  ],
};

test("偏好：主題只認淺色、深色；背景只認列出來的那幾種，其他都當成預設", () => {
  assert.deepEqual(S.readPrefs(store({ "market-theme": "dark", "market-desk": "sand" })), { theme: "dark", desk: "sand" });
  assert.deepEqual(S.readPrefs(store({ "market-theme": "blue", "market-desk": "neon" })), { theme: null, desk: "" });
  assert.deepEqual(S.readPrefs(store({})), { theme: null, desk: "" });
  const broken = { getItem() { throw new Error("私密視窗"); } };
  assert.deepEqual(S.readPrefs(broken), { theme: null, desk: "" });
});

test("背景的選項：米白（現在的）排第一，其餘都是淺色看盤桌", () => {
  assert.deepEqual(S.DESKS.map((d) => d.name), ["米白", "暖沙", "紙白", "霧灰", "淡青"]);
  assert.equal(S.DESKS[0].key, "");
});

test("補滿時間：今天的只寫時分，其他天寫月日與星期（台灣時間）", () => {
  assert.equal(S.resetText("2026-11-17T10:21:54Z", NOW), "今天 18:21 補滿");
  assert.equal(S.resetText("2026-11-22T03:36:29Z", NOW), "11-22（日）11:36 補滿");
  assert.equal(S.resetText("2026-11-17T16:30:00Z", NOW), "11-18（三）00:30 補滿");
  assert.equal(S.resetText(null, NOW), "");
});

test("用量：Flash 那組兩條（5 小時、本週），Argon 一條（本週），剩不到兩成標成偏低", () => {
  const view = S.usageView(USAGE, NOW);
  assert.deepEqual(view.groups.map((g) => [g.title, g.meters.map((m) => [m.label, m.percent, m.low])]), [
    ["Flash", [["5 小時", 89, false], ["本週", 93, false]]],
    ["Argon", [["本週", 16, true]]],
  ]);
  assert.equal(view.groups[0].meters[0].reset, "今天 18:21 補滿");
  assert.equal(view.updated, "11-17 16:05 讀取");
});

test("今天各任務：用中文名稱與模型簡稱，認不得的任務照原名；照次數多寡排（一樣多照原本順序）", () => {
  const view = S.usageView(USAGE, NOW);
  assert.deepEqual(view.today, [
    { label: "some-new-task", model: "", runs: 2, note: "" },
    { label: "自選股判斷與提名", model: "Flash", runs: 2, note: "" },
    { label: "晚間事前預測", model: "Argon", runs: 1, note: "" },
  ]);
});

test("還沒有任何紀錄：沒有用量條，說明會在下一次排程跑完後出現", () => {
  const view = S.usageView({ available: true, read_at: null, last_run_at: null, flash: null, argon: null, today: [] }, NOW);
  assert.deepEqual(view.groups, []);
  assert.equal(view.empty, "下一次 Gemini 排程跑完後就會有數字。");
});

test("最新一次排程沒讀到用量：數字照上一筆，並說明讀取時間比最後一次排程早", () => {
  const view = S.usageView({ ...USAGE, read_at: "2026-11-17T06:00:00Z", last_run_at: "2026-11-17T08:05:00Z" }, NOW);
  assert.equal(view.updated, "11-17 14:00 讀取（16:05 那次沒讀到）");
});

test("模型簡稱", () => {
  assert.equal(S.modelLabel("gemini-3.8-flash-medium"), "Flash");
  assert.equal(S.modelLabel("gemini-4-argon"), "Argon");
  assert.equal(S.modelLabel("gemini-3.8-flash"), "Flash");
  assert.equal(S.modelLabel("gemini-4-argon-high"), "Argon");
  assert.equal(S.modelLabel("gemini-4-hydrogen-low"), "Hydrogen");
  assert.equal(S.modelLabel(null), "");
});

test("任務名稱：籌碼與資金白話（每日更新那支排程）與首頁每日解讀分開", () => {
  const view = S.usageView({ ...USAGE, today: [{ task: "daily-update", runs: 1, model: "gemini-3.8-flash" }, { task: "ai-digest", runs: 1, model: null }] }, NOW);
  assert.deepEqual(view.today.map((t) => t.label), ["籌碼與資金白話", "首頁每日解讀"]);
});

test("籌碼與資金白話（本人 11-19）：模型後面標思考程度，失敗與乾跑另外寫，乾跑不算進次數", () => {
  const view = S.usageView({ ...USAGE, today: [
    { task: "daily-update", runs: 2, model: "gemini-4-argon", effort: "medium", failed: 1, dry_runs: 1 },
    { task: "watch-ledger", runs: 3, model: "gemini-3.8-flash", effort: null, failed: 0, dry_runs: 0 },
  ] }, NOW);
  assert.deepEqual(view.today, [
    { label: "自選股判斷與提名", model: "Flash", runs: 3, note: "" },
    { label: "籌碼與資金白話", model: "Argon・思考中", runs: 2, note: "失敗 1・乾跑 1（不計）" },
  ]);
});

test("今天只有乾跑：次數是 0，標乾跑", () => {
  const view = S.usageView({ ...USAGE, today: [{ task: "daily-update", runs: 0, model: "gemini-4-argon", effort: "high", failed: 0, dry_runs: 2 }] }, NOW);
  assert.deepEqual(view.today, [{ label: "籌碼與資金白話", model: "Argon・思考高", runs: 0, note: "乾跑 2（不計）" }]);
});

test("籤：站主看到用量、通知、外觀三顆；推播不能用時少通知；一般使用者只剩外觀、不顯示籤列", () => {
  assert.deepEqual(S.tabsFor({ usage: true, notify: true }), ["usage", "notify", "look"]);
  assert.deepEqual(S.tabsFor({ usage: true, notify: false }), ["usage", "look"]);
  assert.deepEqual(S.tabsFor({ usage: false, notify: false }), ["look"]);
});
