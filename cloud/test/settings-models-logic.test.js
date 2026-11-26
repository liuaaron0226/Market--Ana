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
    { key: "news", model: FLASH, effort: "high", saved: false },
  ],
  quota: { argon: 0.18, flash: 0.7149 },
  unread: [],
};

test("依目錄顯示可選強度與替代原因，手動切換仍可使用", () => {
  const catalog = { models: [
    { id: 'gemini-4-neon', label: 'Gemini 4 Neon', efforts: ['high', 'xhigh', 'medium', 'low'] },
    { id: 'gemini-pro-orchestrator-neon', label: 'Orchestrator (Neon)', efforts: ['default'] },
  ] };
  const data = { ...DATA, catalog, resolutions: { forecast: { state: 'replaced', requested: { model: ARGON, effort: 'medium' }, actual: { model: 'gemini-4-neon', effort: 'medium' }, reason: 'officially_unavailable' } } };
  const view = S.modelsView(data, {});
  assert.deepEqual(view.modelChoices, [['gemini-4-neon', 'Neon 4']]);
  assert.deepEqual(view.advancedModelChoices, [['gemini-pro-orchestrator-neon', 'Orchestrator (Neon)']]);
  assert.match(view.rows[0].warning, /Argon.*停用.*Neon/);
  assert.equal(view.rows[0].model, ARGON);
  assert.deepEqual(S.nextChoice({ model: ARGON, effort: 'medium' }, { model: 'gemini-pro-orchestrator-neon' }, catalog), { model: 'gemini-pro-orchestrator-neon', effort: 'default' });
  const changed = S.modelsView(data, { plain: { model: 'gemini-4-neon', effort: 'xhigh' } });
  assert.deepEqual(changed.rows.find(r => r.key === 'plain').effortChoices, [['high','高'],['xhigh','極高'],['medium','中'],['low','低']]);
  assert.match(changed.rows.find(r => r.key === 'plain').quotaNote, /未知/);
  assert.match(S.modelsView({ ...data, catalog_status: { state: 'failed' } }, {}).catalogWarning, /保留/);
});

test("官方目錄五款一般模型與三個進階變體各出現一次，不改已有選擇", () => {
  const catalog = JSON.parse(readFileSync(new URL('../lib/gemini-catalog-seed.json', import.meta.url), 'utf8'));
  const data = { ...DATA, catalog, tasks: [{ key: 'digest', model: 'gemini-pro-orchestrator', effort: 'default', saved: true }] };
  const original = JSON.stringify(data);
  const view = S.modelsView(data, {});
  assert.deepEqual(view.modelChoices, [
    ['gemini-4-neon', 'Neon 4'], ['gemini-4-carbon', 'Carbon 4'], ['gemini-4-argon', 'Argon 4'],
    ['gemini-4-hydrogen', 'Hydrogen 4'], ['gemini-3.8-flash', 'Flash 3.8'],
  ]);
  assert.deepEqual(view.advancedModelChoices, [
    ['gemini-pro-orchestrator-neon', 'Orchestrator (Neon)'], ['gemini-pro-orchestrator', 'Orchestrator (Carbon)'],
    ['gemini-pro-orchestrator-argon', 'Orchestrator (Argon)'],
  ]);
  assert.equal(view.rows[0].value, 'Orchestrator (Carbon)');
  assert.equal(view.rows[0].effort, 'default');
  assert.equal(view.dirty, false);
  assert.equal(JSON.stringify(data), original);
});

test("版本由官方標籤與既有模型ID取得，不寫死目前版本", () => {
  const catalog = { models: [{ id: 'gemini-5.1-neon', label: 'Gemini 5.1 Neon', efforts: ['medium'] }] };
  const data = { ...DATA, catalog, tasks: [{ key: 'digest', model: 'gemini-5.1-neon', effort: 'medium' }] };
  assert.deepEqual(S.modelsView(data, {}).modelChoices, [['gemini-5.1-neon', 'Neon 5.1']]);
  assert.equal(S.modelsView(data, {}).rows[0].value, 'Neon 5.1・中');
  assert.equal(S.modelsView({ ...data, catalog: undefined }, {}).rows[0].value, 'Neon 5.1・中');
});

test("站主多一顆「AI 模型」籤，排在用量後面", () => {
  assert.deepEqual(S.tabsFor({ usage: true, models: true, notify: true }), ["usage", "models", "notify", "look"]);
  assert.deepEqual(S.tabsFor({ usage: true, notify: true }), ["usage", "notify", "look"]);
});

test("每列寫任務名稱與現值；大盤預測註明晚間、晨間、重播一起", () => {
  const view = S.modelsView(DATA, {});
  assert.deepEqual(view.rows.map((r) => [r.key, r.name, r.value]), [
    ["forecast", "大盤預測", "Argon 4・中"],
    ["watch_judge", "自選股每日判斷", "Flash 3.8・高"],
    ["watch_nominate", "提名國外同類股", "Flash 3.8・高"],
    ["plain", "盤勢解讀", "Argon 4・中"],
    ["digest", "今日 AI 解讀", "Argon 4・中"],
    ["news", "首頁消息", "Flash 3.8・高"],
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
  assert.equal(view.rows[0].value, "Flash 3.8・中");
  assert.equal(view.rows[0].changed, true);
  assert.equal(view.dirty, true);
  // 改回原值就不算改動
  assert.equal(S.modelsView(DATA, { forecast: { model: ARGON, effort: "medium" } }).dirty, false);
});

test("說明列：生效時間與上次修改時間（台灣時間）", () => {
  assert.equal(S.modelsView(DATA, {}).foot, "存檔後下一時段排程開始用（大盤預測從下一個晚間版起）。上次修改 11-19 15:20。");
  assert.equal(S.modelsView({ ...DATA, updated_at: null }, {}).foot, "存檔後下一時段排程開始用（大盤預測從下一個晚間版起）。");
});

test("最近一班沒讀到設定的排程列出來", () => {
  const view = S.modelsView({ ...DATA, unread: [{ task: "forecast-ledger", read_at: "2026-11-19T13:05:00Z" }] }, {});
  assert.deepEqual(view.unread, ["晚間事前預測上一時段（11-19 21:05）沒讀到你的設定，用了預設。"]);
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
    "大盤預測改成 Flash 3.8・中：成績另開一組從頭累積，正式起算要重數連續 5 天。從下一個晚間版開始用。",
  ]);
  assert.deepEqual(S.confirmNotes(DATA, { watch_judge: { model: FLASH, effort: "low" }, digest: { model: FLASH, effort: "low" } }), [
    "自選股每日判斷改成 Flash 3.8・低：成績另開一組從頭累積。下一時段開始用。",
  ]);
  // 改了又改回來不算
  assert.deepEqual(S.confirmNotes(DATA, { forecast: { model: ARGON, effort: "medium" } }), []);
});
