// 基金當成一般個股 07 票（本人 2026-11-17／18 定案，規格 .scratch/fund-as-stock/spec.md）：自選股與庫存收基金。
// 庫存記「單位數＋台幣總成本」（照基富通對帳單抄）；現值＝單位數×淨值×匯率，賺賠用台幣（不含已領配息）。
// 期待值用固定的淨值（21.12 美元）與匯率（32.5）手算，不用程式同一個算法重算。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const FRIEND = "friend@x.com";
const VERSION = "liquid-20261117-fund-v2";
const FUND = { code: "BLKTENAU", name: "貝萊德世界科技 A10", full_name: "貝萊德世界科技基金 A10美元(總報酬穩定配息)" };
const fundAnalysis = (extra = {}) => ({
  schema: "stock-analysis/v2", analysis_version: "fund-nav-v1", code: FUND.code, name: FUND.name, full_name: FUND.full_name,
  kind: "fund", market: "境外基金", currency: "USD", as_of: "2026-11-12", requested_as_of: "9999-12-31",
  quote: { close: 21.12, previous_close: 21.4, change: -0.28, chg_pct: -1.31, change_sign: "-", nav_date: "2026-11-12", currency: "USD" },
  fx: { pair: "USD/TWD", rate: 32.5, date: "2026-11-17", source: "中央銀行" },
  qa: { blocking: false, bars: 120 }, strategy: null, institutional: [],
  levels: { status: "insufficient", resistance: [], support: [] },
  bars: [{ date: "2026-11-11", close: 21.4 }, { date: "2026-11-12", close: 21.12 }],
  ...extra,
});

let issuer, env, deps, quoteCalls;
const next = async () => new Response("static-ok", { status: 200 });
const run = (sql, ...args) => env.DB.prepare(sql).bind(...args).run();

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_STOCK_FUNDS: "all" };
  env.DB._raw.exec(readFileSync(new URL("../migrations/0006_cash_and_level.sql", import.meta.url), "utf8"));   // 現金與水位（和 cash-level 測試一樣另建）
  quoteCalls = [];
  deps = {
    fetchImpl: fetchJwks(issuer.jwks),
    // 證交所即時行情：只認得上市櫃股票；有人拿基金代碼去問就記下來（不該發生）
    quoteFetch: async (url) => {
      quoteCalls.push(url);
      return new Response(JSON.stringify({ msgArray: [{ c: "2330", n: "台積電", ex: "tse", z: "1000", y: "990", d: "20261118" }] }));
    },
    now: () => Date.parse("2026-11-18T10:00:05Z"),
  };
  for (const email of [FRIEND]) {
    await run("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)", email, "n", "2026-11-07T00:00:00Z");
  }
  await run("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) " +
    "VALUES (?, '2026-11-08', 'https://isin.twse.com.tw', ?, '2026-11-17T12:00:00Z', 1, 2)", VERSION, "a".repeat(64));
  await run("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, '2026-11-17T12:00:00Z')", VERSION);
  await run("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, " +
    "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '2330', '台積電', '上市', '普通股', 'TW0002330008', '1994/09/05', '半導體業', 'ESVUFR', '2026-11-17', 500, 'available', 1, 'liquid-v1')", VERSION);
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, '2330', '2026-11-17', ?)", VERSION,
    JSON.stringify({ schema: "stock-analysis/v2", code: "2330", name: "台積電", kind: "common", market: "上市", as_of: "2026-11-17", quote: { close: 1000 } }));
  await run("INSERT INTO fund_securities (version, code, name, full_name, currency, isin, latest_bar_date, available_bars, analysis_status) " +
    "VALUES (?, ?, ?, ?, 'USD', 'LU2533723974', '2026-11-12', 990, 'available')", VERSION, FUND.code, FUND.name, FUND.full_name);
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, '2026-11-12', ?)", VERSION, FUND.code, JSON.stringify(fundAnalysis()));
});

async function req(path, email, { method = "GET", body } = {}) {
  const headers = new Headers();
  headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (body !== undefined) {
    headers.set("content-type", "application/json");
    headers.set("origin", "https://market-ana.pages.dev");
  }
  return handle(new Request("https://market-ana.pages.dev" + path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  }), env, next, deps);
}
const json = async (response) => ({ status: response.status, body: await response.json() });
const fundHolding = async (email) => (await json(await req("/api/holdings", email))).body.items.find((item) => item.code === FUND.code);

test("站主與朋友都能把基金加進自選：清單裡是一般的一列（市場境外基金、在分析範圍內，可以點進個股頁）", async () => {
  for (const email of [OWNER, FRIEND]) {
    const created = await json(await req("/api/watchlist", email, { method: "POST", body: { code: "blktenau" } }));
    assert.equal(created.status, 201, email);
    assert.deepEqual([created.body.code, created.body.name, created.body.market, created.body.in_universe],
      [FUND.code, FUND.name, "境外基金", true], email);
    const listed = await json(await req("/api/watchlist", email));
    assert.deepEqual(listed.body.items.map((item) => [item.code, item.market, item.horizon, item.group, item.in_universe]),
      [[FUND.code, "境外基金", "none", "", true]], email);
    // 設短期長期、分組、移出，都和股票一樣
    assert.equal((await req("/api/watchlist", email, { method: "POST", body: { code: FUND.code, horizon: "long", group: "核心" } })).status, 200);
    const set = (await json(await req("/api/watchlist", email))).body.items[0];
    assert.deepEqual([set.horizon, set.group], ["long", "核心"]);
    assert.equal((await req(`/api/watchlist/${FUND.code}`, email, { method: "DELETE", body: {} })).status, 204);
  }
});

test("站主與朋友都能以單位數＋台幣總成本新增基金庫存；現值＝單位數×淨值×匯率、賺賠用台幣", async () => {
  for (const email of [OWNER, FRIEND]) {
    const created = await json(await req("/api/holdings", email,
      { method: "POST", body: { code: FUND.code, shares: 123.4567, total_cost_twd: 80000, note: "基富通 11 月對帳單" } }));
    assert.equal(created.status, 201, email);
    assert.equal(created.body.shares, 123.4567);
    assert.equal(created.body.total_cost_twd, 80000);
    assert.equal(created.body.market, "境外基金");

    const item = await fundHolding(email);
    // 21.12 美元 × 32.5 ＝ 每單位 686.4 元；123.4567 單位 × 686.4 ＝ 84,740.68 元；成本 80,000 → 賺 4,740.68（5.93%）
    assert.equal(item.shares, 123.4567, "單位數小數存取不失真");
    assert.equal(item.kind, "fund");
    assert.equal(item.total_cost_twd, 80000);
    assert.equal(item.cost_basis, 80000);
    assert.equal(item.latest_price, 686.4);
    assert.equal(item.market_value, 84740.68);
    assert.equal(item.unrealized_pnl, 4740.68);
    assert.equal(item.return_pct, 5.93);
    assert.deepEqual([item.nav, item.currency, item.nav_date, item.fx_rate, item.fx_date],
      [21.12, "USD", "2026-11-12", 32.5, "2026-11-17"]);
    assert.equal(item.price_as_of, "2026-11-12");
    assert.equal(item.price_source, "本站淨值×匯率");
    assert.equal(item.price_stale, false);
  }
  assert.deepEqual(quoteCalls, [], "基金代碼不拿去問證交所即時行情");
});

test("就地修改基金庫存：一樣改單位數與台幣總成本，讀回來是新的", async () => {
  await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, shares: 123.4567, total_cost_twd: 80000 } });
  const updated = await json(await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, shares: 200.5, total_cost_twd: 130000.5, note: "加碼" } }));
  assert.equal(updated.status, 200);
  const item = await fundHolding(OWNER);
  // 200.5 × 686.4 ＝ 137,623.2；成本 130,000.5 → 賺 7,622.7（5.86%）
  assert.deepEqual([item.shares, item.total_cost_twd, item.market_value, item.unrealized_pnl, item.return_pct, item.note],
    [200.5, 130000.5, 137623.2, 7622.7, 5.86, "加碼"]);
});

test("基金庫存的欄位檢查：要台幣總成本（不是平均成本）、單位數要大於 0", async () => {
  const bad = [
    [{ shares: 10, average_cost: 600 }, "基金請填單位數與台幣總成本"],
    [{ shares: 10 }, "基金請填單位數與台幣總成本"],
    [{ shares: 0, total_cost_twd: 1000 }, "單位數須大於 0 且不超過 1,000,000,000"],
    [{ shares: -1, total_cost_twd: 1000 }, "單位數須大於 0 且不超過 1,000,000,000"],
    [{ shares: "10", total_cost_twd: 1000 }, "單位數須大於 0 且不超過 1,000,000,000"],
    [{ shares: 10, total_cost_twd: 0 }, "台幣總成本須大於 0 且不超過 1,000,000,000,000"],
    [{ shares: 0.0001, total_cost_twd: 1_000_000_000 }, "每單位成本超過 10,000,000，請確認單位數與總成本"],
  ];
  for (const [body, message] of bad) {
    const response = await json(await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, ...body } }));
    assert.deepEqual([response.status, response.body.message ?? response.body.error], [400, message], JSON.stringify(body));
  }
  assert.equal(await fundHolding(OWNER), undefined);
});

test("股票庫存不變：股數仍要整數、用平均成本；回應沒有基金的欄位", async () => {
  const decimal = await json(await req("/api/holdings", OWNER, { method: "POST", body: { code: "2330", shares: 1.5, average_cost: 900 } }));
  assert.equal(decimal.status, 400);
  assert.equal((await req("/api/holdings", OWNER, { method: "POST", body: { code: "2330", shares: 1000, average_cost: 900 } })).status, 201);
  const item = (await json(await req("/api/holdings", OWNER))).body.items[0];
  assert.deepEqual([item.latest_price, item.market_value, item.cost_basis], [1000, 1000000, 900000]);
  for (const key of ["kind", "total_cost_twd", "nav", "fx_rate"]) assert.equal(key in item, false, key);
});

test("基金庫存算進總資金與水位", async () => {
  await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, shares: 123.4567, total_cost_twd: 80000 } });
  await req("/api/holdings", OWNER, { method: "POST", body: { code: "2330", shares: 10, average_cost: 900 } });
  await req("/api/holdings/cash", OWNER, { method: "POST", body: { amount_twd: 5259.32 } });
  env.MARKET_ANA_STRATEGIES_ENABLED = "true";   // 水位建議在策略總開關底下
  const level = (await json(await req("/api/holdings/level", OWNER))).body.current;
  // 84,740.68 ＋ 10,000 ＝ 94,740.68；加現金 5,259.32 ＝ 100,000
  assert.deepEqual([level.complete, level.market_value, level.total_assets, level.level, level.missing_price_codes],
    [true, 94740.68, 100000, 94.74, []]);
});

test("淨值或匯率缺的時候不亂算：基金那一列沒有價格、水位列為缺價格", async () => {
  await run("UPDATE stock_analysis SET payload = ? WHERE code = ?", JSON.stringify(fundAnalysis({ fx: null })), FUND.code);
  await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, shares: 10, total_cost_twd: 6000 } });
  const item = await fundHolding(OWNER);
  assert.deepEqual([item.latest_price, item.market_value, item.unrealized_pnl, item.total_cost_twd, item.cost_basis], [null, null, null, 6000, 6000]);
  env.MARKET_ANA_STRATEGIES_ENABLED = "true";   // 水位建議在策略總開關底下
  const level = (await json(await req("/api/holdings/level", OWNER))).body.current;
  assert.deepEqual(level.missing_price_codes, [FUND.code]);
  assert.deepEqual(quoteCalls, []);
});

test("淨值超過 6 個平日沒更新（分析狀態過期）：照樣算現值，但標成舊價格", async () => {
  await run("UPDATE stock_analysis SET payload = ? WHERE code = ?", JSON.stringify(fundAnalysis({ analysis_status: "stale" })), FUND.code);
  await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, shares: 10, total_cost_twd: 6000 } });
  const item = await fundHolding(OWNER);
  assert.deepEqual([item.market_value, item.price_stale], [6864, true]);
  await run("UPDATE stock_analysis SET payload = ? WHERE code = ?", JSON.stringify(fundAnalysis({ analysis_status: "available" })), FUND.code);
  assert.equal((await fundHolding(OWNER)).price_stale, false);
});

// 和 ETF 一樣：開關關著不能新增，之前存的保留；已存的基金庫存照樣算現值，這個人的總資金與水位才不會整個算不出來（審查修正）
test("基金開關對這個人關著：不能新增自選或庫存；之前存的保留、照樣列出並算現值", async () => {
  await req("/api/watchlist", FRIEND, { method: "POST", body: { code: FUND.code } });
  await req("/api/holdings", FRIEND, { method: "POST", body: { code: FUND.code, shares: 10, total_cost_twd: 6000 } });
  for (const value of ["owner", "off", undefined]) {
    if (value === undefined) delete env.MARKET_ANA_STOCK_FUNDS; else env.MARKET_ANA_STOCK_FUNDS = value;
    assert.equal((await req("/api/watchlist", FRIEND, { method: "POST", body: { code: FUND.code, horizon: "long" } })).status, 404, String(value));
    assert.equal((await req("/api/holdings", FRIEND, { method: "POST", body: { code: FUND.code, shares: 20, total_cost_twd: 9000 } })).status, 404, String(value));
    assert.deepEqual((await json(await req("/api/watchlist", FRIEND))).body.items.map((item) => item.code), [FUND.code]);
    const item = await fundHolding(FRIEND);
    assert.deepEqual([item.shares, item.total_cost_twd, item.market_value], [10, 6000, 6864], String(value));
  }
  env.MARKET_ANA_STOCK_FUNDS = "owner";
  assert.equal((await req("/api/holdings", OWNER, { method: "POST", body: { code: FUND.code, shares: 1, total_cost_twd: 700 } })).status, 201);
  assert.equal((await fundHolding(OWNER)).market_value, 686.4);
});

// 名錄多檔基金（2026-11-24 加安聯收益成長 AGIFFVM、摩根多重收益 GLIACU）：每一檔都和第一檔一樣能進自選與庫存。
// 站上代碼不含連字號（集保 AGIF-FVM → 站上 AGIFFVM）；還沒有回補歷史、淨值不足 20 筆的那檔名錄記 insufficient、沒有分析，
// 照樣能加，只是沒有價格（和淨值缺的時候同一個規則）。
test("名錄裡的第二、三檔基金：自選與庫存照第一檔的規則；沒有分析的那檔沒有價格、不問證交所", async () => {
  const more = [
    { code: "AGIFFVM", name: "安聯收益成長 AM 穩定月收", full_name: "安聯收益成長基金-AM穩定月收類股(美元)", isin: "LU0820561818", bars: 1209, status: "available" },
    { code: "GLIACU", name: "摩根多重收益 A股穩定月配", full_name: "摩根投資基金 - 多重收益基金 - JPM多重收益(美元對沖) - A股(穩定月配)", isin: "LU2347655073", bars: 3, status: "insufficient" },
  ];
  for (const fund of more) {
    await run("INSERT INTO fund_securities (version, code, name, full_name, currency, isin, latest_bar_date, available_bars, analysis_status) " +
      "VALUES (?, ?, ?, ?, 'USD', ?, '2026-11-12', ?, ?)", VERSION, fund.code, fund.name, fund.full_name, fund.isin, fund.bars, fund.status);
  }
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, 'AGIFFVM', '2026-11-12', ?)", VERSION,
    JSON.stringify(fundAnalysis({ code: "AGIFFVM", name: more[0].name, full_name: more[0].full_name,
      quote: { close: 6.4, previous_close: 6.41, change: -0.01, chg_pct: -0.16, change_sign: "-", nav_date: "2026-11-12", currency: "USD" } })));
  for (const fund of more) {
    const created = await json(await req("/api/watchlist", FRIEND, { method: "POST", body: { code: fund.code.toLowerCase() } }));
    assert.equal(created.status, 201, fund.code);
    assert.deepEqual([created.body.code, created.body.name, created.body.market], [fund.code, fund.name, "境外基金"], fund.code);
    assert.equal((await req("/api/holdings", FRIEND, { method: "POST", body: { code: fund.code, shares: 100, total_cost_twd: 20000 } })).status, 201, fund.code);
  }
  assert.deepEqual((await json(await req("/api/watchlist", FRIEND))).body.items.map((item) => item.code).sort(), ["AGIFFVM", "GLIACU"]);
  const holdings = Object.fromEntries((await json(await req("/api/holdings", FRIEND))).body.items.map((item) => [item.code, item]));
  // 6.4 美元 × 32.5 ＝ 每單位 208 元；100 單位 ＝ 20,800 元；成本 20,000 → 賺 800（4%）
  assert.deepEqual([holdings.AGIFFVM.kind, holdings.AGIFFVM.latest_price, holdings.AGIFFVM.market_value, holdings.AGIFFVM.return_pct],
    ["fund", 208, 20800, 4]);
  assert.deepEqual([holdings.GLIACU.kind, holdings.GLIACU.latest_price, holdings.GLIACU.market_value, holdings.GLIACU.total_cost_twd],
    ["fund", null, null, 20000]);
  assert.equal((await req("/api/watchlist", FRIEND, { method: "POST", body: { code: "AGIF-FVM" } })).status, 400,
    "連字號不是站上代碼；搜尋把 AGIF-FVM 對到 AGIFFVM，加入時送的是站上代碼");
  assert.deepEqual(quoteCalls, [], "基金代碼不拿去問證交所即時行情");
});
