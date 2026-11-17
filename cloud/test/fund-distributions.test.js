// 基金當成一般個股 06 票（本人 2026-11-18，規格 .scratch/fund-as-stock/spec.md 第 28 條）：基金個股頁的「配息」分類。
// 分析契約 kind fund 的 distributions 欄（每日建置由集保配息原件算，見 cloud/test/test_fund_distributions.py）整理成畫面上的字。
// 這裡驗三件事：頁面上有「配息」鍵與區塊（一開始藏著，只有基金才出現）、契約整理成的字、網站入口把這一欄原樣交給基金（股票沒有）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ROOT = new URL("../../", import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), "utf8");

function loadView() {
  const window = {};
  vm.runInNewContext(source("web/fund-distributions.js"), { window });
  return (dist) => JSON.parse(JSON.stringify(window.FundDistributions.read(dist)));
}
const read = loadView();

// 和每日建置用真資料算出來的同一份（2026-11-18 資料日）
const DIST = {
  currency: "USD", count: 47,
  latest: { ex_date: "2026-10-30", record_date: "2026-10-29", pay_date: "2026-11-06", amount: 0.162, capital_pct: null, income_pct: null },
  years: [
    { year: "2026", count: 10, total: 1.4655, all_capital: 0, part_capital: 0, income_only: 9, unknown: 1, source: "9 次來自可分配淨利益、1 次來源未公布" },
    { year: "2025", count: 12, total: 1.553, all_capital: 0, part_capital: 0, income_only: 11, unknown: 1, source: "11 次來自可分配淨利益、1 次來源未公布" },
    { year: "2024", count: 12, total: 1.372, all_capital: 0, part_capital: 0, income_only: 12, unknown: 0, source: "全部來自可分配淨利益" },
    { year: "2023", count: 12, total: 0.857, all_capital: 11, part_capital: 1, income_only: 0, unknown: 0, source: "11 次全部來自本金、1 次部分來自本金" },
    { year: "2022", count: 1, total: 0.057, all_capital: 1, part_capital: 0, income_only: 0, unknown: 0, source: "全部來自本金" },
  ],
  capital_note: "這檔配息可能來自本金：2022 年 1 次配息全部來自本金；2023 年 12 次配息中 11 次全部來自本金；2024 年 1 月起公布的配息都來自可分配淨利益。",
};

test("頁面：「配息」鍵排在「比較」後面、一開始藏著，指到自己的區塊；區塊帶 data-cat-panel 讓分類鍵開合", () => {
  const html = source("web/stock.html");
  const keys = [...html.matchAll(/<button type="button"[^>]*aria-controls="(\w+)"[^>]*>([^<]+)<\/button>/g)]
    .filter((m) => m[1].endsWith("Panel")).map((m) => m[2]);
  assert.deepEqual(keys.slice(keys.indexOf("比較"), keys.indexOf("比較") + 2), ["比較", "配息"]);
  assert.match(html, /<button type="button" aria-expanded="false" aria-controls="distributionsPanel" hidden>配息<\/button>/);
  assert.match(html, /<section class="bd-cat" id="distributionsPanel" data-cat-panel="distributions" aria-label="配息">/);
  assert.match(html, /<script src="fund-distributions\.js\?v=[^"]+"><\/script>\s*(<script[^>]*><\/script>\s*)*<script src="stock\.js/);
});

test("最近一次：每單位配息（美元）、除息日與發放日；來源還沒公布就照實寫", () => {
  const view = read(DIST);
  assert.deepEqual(view.latest, { amount: "0.162", unit: "美元／單位", dates: "除息 2026-10-30 · 發放 2026-11-06", source: "來源未公布" });
  const known = read({ ...DIST, latest: { ...DIST.latest, capital_pct: 0, income_pct: 100 } });
  assert.equal(known.latest.source, "100% 來自可分配淨利益");
  const capital = read({ ...DIST, latest: { ...DIST.latest, capital_pct: 78.56, income_pct: 21.44 } });
  assert.equal(capital.latest.source, "78.56% 來自本金");
});

test("各年：新的在前，次數、合計（美元）與來源；有本金的年份標出來", () => {
  const view = read(DIST);
  assert.deepEqual(view.years.map((y) => [y.year, y.facts, y.source, y.capital]), [
    ["2026", "10 次 · 合計 1.4655 美元", "9 次來自可分配淨利益、1 次來源未公布", false],
    ["2025", "12 次 · 合計 1.553 美元", "11 次來自可分配淨利益、1 次來源未公布", false],
    ["2024", "12 次 · 合計 1.372 美元", "全部來自可分配淨利益", false],
    ["2023", "12 次 · 合計 0.857 美元", "11 次全部來自本金、1 次部分來自本金", true],
    ["2022", "1 次 · 合計 0.057 美元", "全部來自本金", true],
  ]);
});

test("本金註記照契約那一句，另外白話說明「來自本金」是什麼意思", () => {
  const view = read(DIST);
  assert.equal(view.note, DIST.capital_note);
  assert.match(view.hint, /本金.*淨值/);
});

test("沒有配息紀錄或沒有這一欄：寫一句沒有，不畫空的表", () => {
  for (const dist of [{ currency: "USD", count: 0, latest: null, years: [], capital_note: null }, null, undefined]) {
    const view = read(dist);
    assert.deepEqual([view.empty, view.latest, view.years], ["目前沒有這檔基金的配息紀錄。", null, []]);
  }
});

// ---- 網站入口：分析原樣交出這一欄；股票的分析沒有 ----
const VERSION = "liquid-20261118-fund-v2";
let issuer, env, deps;
const run = (sql, ...args) => env.DB.prepare(sql).bind(...args).run();
beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_STOCK_FUNDS: "all" };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    stockPack: { as_of: "2026-11-17", analysis_schema: "stock-analysis/v2", results: {} },
    now: () => Date.parse("2026-11-18T10:00:05Z"),
  };
  await run("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) " +
    "VALUES (?, '2026-11-08', 'https://isin.twse.com.tw', ?, '2026-11-18T12:00:00Z', 1, 2)", VERSION, "a".repeat(64));
  await run("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, '2026-11-18T12:00:00Z')", VERSION);
  await run("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, " +
    "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '2330', '台積電', '上市', '普通股', 'TW0002330008', '1994/09/05', '半導體業', 'ESVUFR', '2026-11-17', 500, 'available', 1, 'liquid-v1')", VERSION);
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, '2330', '2026-11-17', ?)", VERSION,
    JSON.stringify({ schema: "stock-analysis/v2", code: "2330", name: "台積電", kind: "common", market: "上市", as_of: "2026-11-17" }));
  await run("INSERT INTO fund_securities (version, code, name, full_name, currency, isin, latest_bar_date, available_bars, analysis_status) " +
    "VALUES (?, 'BLKTENAU', '貝萊德世界科技 A10', '貝萊德世界科技基金 A10美元(總報酬穩定配息)', 'USD', 'LU2533723974', '2026-11-16', 990, 'available')", VERSION);
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, 'BLKTENAU', '2026-11-16', ?)", VERSION, JSON.stringify({
    schema: "stock-analysis/v2", code: "BLKTENAU", name: "貝萊德世界科技 A10", kind: "fund", market: "境外基金", currency: "USD",
    as_of: "2026-11-16", quote: { close: 17.14, nav_date: "2026-11-16", currency: "USD" }, strategy: null, institutional: [],
    bars: [{ date: "2026-11-16", close: 17.14 }], distributions: DIST,
  }));
});

async function analysis(code) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(OWNER)) });
  const response = await handle(new Request(`https://market-ana.pages.dev/api/stocks/${code}/analysis`, { headers }), env,
    async () => new Response("static-ok"), deps);
  assert.equal(response.status, 200, code);
  return response.json();
}

test("網站入口：基金的分析帶配息欄位；股票的分析沒有這欄", async () => {
  assert.deepEqual((await analysis("BLKTENAU")).distributions, DIST);
  assert.equal("distributions" in (await analysis("2330")), false);
});
