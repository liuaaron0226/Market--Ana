// 自選股關注 E 票第 9–11 片：畫面用的判斷邏輯（web/watch-judgments.js 的 _logic）。
// 清單一行要顯示什麼、觸價亮燈的判定、成績要取哪一組——只驗輸入與輸出，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/watch-judgments.js", ROOT), "utf8"), {
    window, Number, Object, Array, Map, Set, Math, JSON, Date, console,
  });
  return window.WatchJudgments._logic;
}
const logic = load();
// vm 裡建的物件和這邊的原型不同：比內容前先轉成一般物件。
const plain = (value) => (value === undefined ? value : JSON.parse(JSON.stringify(value)));

const line = (value, name = "前波高點（09/21）") => ({ id: "swing_high_20260921", name, value, kind: "swing_high" });
const judgment = (status, extra = {}) => ({
  judgment_id: `2330|2026-10-02|evening|0.4|v`, code: "2330", base_date: "2026-10-02", edition: "evening",
  kind: "judgment", status, trigger_line: line(122), wrong_line: line(103, "波段低點（09/25）"), ...extra,
});
const live = (price, extra = {}) => ({
  ok: true, market_open: true, live: true,
  quote: { price, data_date: "2026-10-03", data_time: "2026-10-03T10:00:00+08:00" }, ...extra,
});

test("清單一行：狀態、觸發價、過去幾次對幾次；可以留意寫「已站上」", () => {
  const entry = { code: "2330", kind: "judgment", status: "接近", trigger_line: line(239.5), record: { reconciled: 5, wins: 3 } };
  assert.deepEqual(plain(logic.listParts(entry, "2026-10-02")),
    { tone: "near", status: "接近", verb: "站上", trigger: 239.5, record: { reconciled: 5, wins: 3 } });
  const ready = { ...entry, status: "可以留意", trigger_line: line(2235) };
  assert.equal(logic.listParts(ready, "2026-10-02").verb, "已站上");
  assert.equal(logic.listParts(ready, "2026-10-02").tone, "ready");
  assert.equal(logic.listParts({ ...entry, status: "還沒到" }, "2026-10-02").tone, "far");
});

test("清單一行：漏記與沒有判斷看得出來；帳本是空的時候什麼都不加", () => {
  assert.deepEqual(plain(logic.listParts({ code: "2317", kind: "miss", status: null, trigger_line: null, record: { reconciled: 2, wins: 1 } }, "2026-10-02")),
    { tone: "miss", status: "缺漏", record: { reconciled: 2, wins: 1 } });
  assert.deepEqual(plain(logic.listParts(undefined, "2026-10-02")), { tone: "none", status: "還沒有判斷" });
  assert.equal(logic.listParts(undefined, null), null);
});

test("觸價：成交價大於等於觸發線才亮；剛好等於也算", () => {
  assert.equal(logic.touched(judgment("接近"), live(121.5)), false);
  assert.equal(logic.touched(judgment("接近"), live(122)), true);
  assert.equal(logic.touched(judgment("還沒到"), live(130)), true);
});

test("觸價：只盯還沒到與接近；可以留意、漏記不盯", () => {
  assert.equal(logic.touched(judgment("可以留意"), live(130)), false);
  assert.equal(logic.touched({ kind: "miss", base_date: "2026-10-02" }, live(130)), false);
  assert.equal(logic.touched(null, live(130)), false);
});

test("觸價：即時報價中斷、不是最新、收盤後或資料是基準日當天的，都不亮", () => {
  assert.equal(logic.touched(judgment("接近"), live(130, { live: false })), false, "報價逾時（不是最新）");
  assert.equal(logic.touched(judgment("接近"), live(130, { ok: false })), false, "報價來源失敗");
  assert.equal(logic.touched(judgment("接近"), live(130, { market_open: false })), false, "收盤後");
  assert.equal(logic.touched(judgment("接近"), { ...live(130), quote: { price: 130, data_date: "2026-10-02" } }), false, "基準日當天的成交");
  assert.equal(logic.touched(judgment("接近"), { ...live(Number.NaN) }), false);
});

const group = (scope, edition, horizon, extra = {}) => ({
  scope, code: scope === "all" ? null : "2330", run_mode: "trial", edition, horizon, method_version: "0.4", input_version: "v",
  judgments: 10, misses: 1, transitions: 4, reconciled: 3, wins: 2, win_rate: 2 / 3, random_rate: 0.45, pending: 1, undeterminable: 0,
  stage: "試跑", ...extra,
});

test("成績：取這一版、正式優先、和最新一份同方法與輸入版本的那一組；合計與這一檔分開", () => {
  const summary = { as_of: "2026-10-29", groups: [
    group("all", "evening", 5), group("all", "evening", 20), group("all", "morning", 5, { wins: 1 }), group("all", "morning", 20),
    group("stock", "evening", 5), group("stock", "evening", 20, { reconciled: 1 }),
    group("all", "evening", 5, { method_version: "0.3", wins: 0 }),
    group("all", "evening", 5, { run_mode: "official", stage: "累積中", wins: 3 }), group("all", "evening", 20, { run_mode: "official", stage: "累積中" }),
  ] };
  const picked = logic.scoreGroups(summary, "evening", { method_version: "0.4", input_version: "v" }, "2330");
  assert.deepEqual(plain(picked.all.map((g) => [g.horizon, g.run_mode, g.wins])), [[5, "official", 3], [20, "official", 2]]);
  assert.equal(picked.stock, null, "這一檔沒有正式的那一組");
  const morning = logic.scoreGroups(summary, "morning", { method_version: "0.4", input_version: "v" }, "2330");
  assert.deepEqual(plain(morning.all.map((g) => [g.horizon, g.run_mode, g.wins])), [[5, "trial", 1], [20, "trial", 2]]);
  const trialOnly = logic.scoreGroups({ groups: summary.groups.filter((g) => g.run_mode === "trial") }, "evening", null, "2330");
  assert.deepEqual(plain(trialOnly.stock.map((g) => [g.horizon, g.reconciled])), [[5, 3], [20, 1]]);
  assert.deepEqual(plain(logic.scoreGroups(null, "evening", null, "2330")), { all: [], stock: null });
});

// 本人 2026-11-16：「到了」不用紅色亮燈（會誤會是漲停），選 A 只換字——小鍵寫「判斷 已到 239.5」，觸發線標「已到」、變粗。
test("到了觸發線只換字：小鍵寫已到與價位，觸發線標「已到」並加粗；任何狀態都不用紅色", () => {
  const near = judgment("接近");
  assert.equal(logic.chipText(near, false), "判斷 接近");
  assert.equal(logic.chipText(near, true), "判斷 已到 122");
  assert.equal(logic.chipText(judgment("可以留意"), false), "判斷 可以留意");
  assert.equal(logic.chipText({ kind: "miss" }, false), "判斷 缺漏");
  assert.equal(logic.chipText(null, false), "判斷");
  const touched = plain(logic.lineSpecs(near, true));
  assert.deepEqual(touched.map((l) => [l.short, l.tone, Boolean(l.strong)]), [["已到", "--gold", true], ["誤判", "--down", false]]);
  assert.deepEqual(plain(logic.lineSpecs(near, false)).map((l) => [l.short, l.tone, Boolean(l.strong)]), [["觸發", "--gold", false], ["誤判", "--down", false]]);
  assert.equal(plain(logic.lineSpecs(judgment("可以留意"), false))[0].tone, "--gold");
  assert.deepEqual(plain(logic.lineSpecs({ kind: "miss" }, false)), []);
});

// 本人 11-17（券商版面）：「你應該是給我完整的趨勢線不是只給一個價格說這是半年線」——
// 觸發線、看錯線是均線（id 是 ma 加天數，例如 MA20）時帶天數，日 K 上畫成整條均線；名稱後面的小字是「觸發」「看錯」
test("觸發線／看錯線是均線時帶天數與角色；不是均線的不帶", () => {
  const ma = { id: "ma20", name: "MA20", value: 118, kind: "ma20" };
  const specs = plain(logic.lineSpecs(judgment("接近", { trigger_line: ma }), false));
  assert.equal(specs[0].ma, 20);
  assert.equal(specs[0].role, "觸發");
  assert.equal(specs[1].ma, undefined, "前波低點不是均線");
  assert.equal(specs[1].role, "誤判");
});

test("週 K、月 K：均線是日 K 算的，不畫成整條，名稱也不寫 MA20（寫角色），免得把一個價格說成均線", () => {
  const source = readFileSync(new URL("../../web/watch-judgments.js", import.meta.url), "utf8");
  const block = source.slice(source.indexOf("function overlays(period) {"), source.indexOf("function onLive("));
  assert.match(block, /period === 'daily'/);
  assert.match(block, /short: .*role/, "非日 K 時名稱退回角色");
});

test("缺了哪幾層：每一層都寫中文名，不出現英文層名（H 票的最後供應點、I 票四層）", () => {
  const record = { missing_layers: ["wyckoff_lpsy", "bollinger", "sector", "margin", "environment", "market"] };
  assert.equal(logic.missingOf(record), "威科夫最後供應點、布林通道、同產業、融資融券、匯率與美債利率、大盤預測");
  assert.equal(logic.missingOf({}), "");
});

test("缺了哪幾層：I 票第二輪三層（估值、借券與季 EPS、個股五塊訊號）也寫中文名", () => {
  const record = { missing_layers: ["valuation", "lending_eps", "signals"] };
  assert.equal(logic.missingOf(record), "估值、借券與季 EPS、個股五塊訊號");
});

test("缺了哪幾層：J 票的富果盤中、新聞標題、國外同類股也寫中文名", () => {
  const record = { missing_layers: ["intraday", "headlines", "peers"] };
  assert.equal(logic.missingOf(record), "富果盤中、新聞標題、國外同類股");
});
