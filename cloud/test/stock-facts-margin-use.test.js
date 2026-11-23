// 個股頁「融資券」的融資使用率（本人 2026-11-24：「新增融資使用率在我的籌碼」）。
// 融資使用率＝融資餘額 ÷ 融資限額，每日建置時算好放在 facts.credit.margin_utilisation { value, data_date, series }
// （twon_analysis/facts.py）。這裡驗兩件事：
// 1. 個股分析入口把這一項原樣送到頁面（和融資餘額同一條路；舊資料包沒有這一項也照常）。
// 2. web/stock-facts.js 的 _logic.marginUse：值 → 畫面上的字與走勢；沒有值就不出現（不放替代文字）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ROOT = new URL("../../", import.meta.url);
const EXAMPLE = JSON.parse(readFileSync(new URL("./fixtures/stock-analysis-v2.example.json", import.meta.url), "utf8"));
const ROWS = [
  { name: "融資", balance: 35072, change5: 476, series: [34100, 34596, 35072], data_date: "2026-11-06" },
  { name: "融券", balance: 75, change5: null, series: [47, 47, 75], data_date: "2026-11-06" },
];
const USE = { value: 32.4, data_date: "2026-11-06", series: [31.9, null, 32.4] };
const facts = (credit) => ({ data_date: "2026-11-06", today: [], summary: ["一句"], credit, blocks: ["summary", "credit"] });

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_STOCK_FACTS: "owner" };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    stockPack: { as_of: "2026-11-06", analysis_schema: "stock-analysis/v2", results: {
      "2330": { ...EXAMPLE, code: "2330", name: "台積電", facts: facts({ rows: ROWS, margin_utilisation: USE }) },
      "1101": { ...EXAMPLE, code: "1101", name: "台泥", facts: facts({ rows: ROWS }) },   // 舊資料包：沒有這一項
    } },
    now: () => Date.parse("2026-11-07T10:00:05Z"),
  };
});

async function creditOf(code) {
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(OWNER)));
  const response = await handle(new Request(`https://market-ana.pages.dev/api/stocks/${code}/analysis`, { headers: h }), env, next, deps);
  assert.equal(response.status, 200);
  return (await response.json()).facts.credit;
}

test("個股分析入口：融資使用率（最新值、資料日、走勢）跟著融資券那一塊原樣送出", async () => {
  const credit = await creditOf("2330");
  assert.deepEqual(credit.margin_utilisation, USE);
  assert.equal(credit.margin_utilisation.series.length, credit.rows[0].series.length, "走勢和融資那一列同一段日子");
});

test("個股分析入口：舊資料包沒有融資使用率，融資券其他內容照常", async () => {
  const credit = await creditOf("1101");
  assert.equal(Object.hasOwn(credit, "margin_utilisation"), false);
  assert.deepEqual(credit.rows, ROWS);
});

function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/stock-facts.js", ROOT), "utf8"), { window, document: {}, console });
  return window.StockFacts._logic;
}
const { marginUse } = load();
const plain = (value) => JSON.parse(JSON.stringify(value));

test("畫面：最新值寫成一位小數的百分比，走勢照給的畫（缺的那天留空）", () => {
  assert.deepEqual(plain(marginUse({ margin_utilisation: USE })), { text: "32.4%", series: [31.9, null, 32.4] });
  assert.equal(marginUse({ margin_utilisation: { value: 7, series: [6.9, 7] } }).text, "7.0%");
  assert.equal(marginUse({ margin_utilisation: { value: 0, series: [0, 0] } }).text, "0.0%", "真的是 0 才寫 0.0%");
  assert.equal(marginUse({ margin_utilisation: { value: 100, series: [99.9, 100] } }).text, "100.0%");
});

test("畫面：只有最新一天有值時只給數字，不畫只有一個點的走勢", () => {
  for (const series of [[32.4], [null, null, 32.4], [], undefined, "x"]) {
    assert.deepEqual(plain(marginUse({ margin_utilisation: { value: 32.4, series } })), { text: "32.4%", series: null });
  }
});

test("畫面：沒有值就不出現（舊資料包、沒有限額、值不是數字）——不寫 0%、不放替代文字", () => {
  for (const credit of [undefined, null, {}, { rows: ROWS }, { margin_utilisation: null }, { margin_utilisation: {} },
    { margin_utilisation: { value: null, series: [1, 2] } }, { margin_utilisation: { value: "32.4" } },
    { margin_utilisation: { value: NaN } }, { margin_utilisation: { value: Infinity } }, { margin_utilisation: { value: -1 } }]) {
    assert.equal(marginUse(credit), null, JSON.stringify(credit));
  }
});

test("畫面：走勢裡不是數字的值當成缺資料", () => {
  assert.deepEqual(plain(marginUse({ margin_utilisation: { value: 3, series: [1, "2", undefined, 3] } })), { text: "3.0%", series: [1, null, null, 3] });
});
