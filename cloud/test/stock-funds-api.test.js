// 基金當成一般個股 03 票（本人 2026-11-18，規格 .scratch/fund-as-stock/spec.md）的網站入口：
// 基金（貝萊德世界科技 A10，代碼 BLKTENAU）進名錄，搜尋回一般的一筆（類別「基金」、市場「境外基金」），
// 個股入口回 kind: "fund" 的分析、長日線回淨值序列；基金自己一個三態開關（關、只開站主、開給全部獲准使用者），比照 ETF 上站。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const FRIEND = "friend@x.com";
const VERSION = "liquid-20261117-fund-v2";
const FUND = { code: "BLKTENAU", name: "貝萊德世界科技 A10", full_name: "貝萊德世界科技基金 A10美元(總報酬穩定配息)" };
// 分析契約 v2 的基金版：報價是淨值（美元），bars 只有收盤；策略與價位等 04 票，這張先回資料不足。
const FUND_ANALYSIS = {
  schema: "stock-analysis/v2", analysis_version: "fund-nav-v1", code: FUND.code, name: FUND.name, full_name: FUND.full_name,
  kind: "fund", market: "境外基金", currency: "USD", as_of: "2026-11-12", requested_as_of: "9999-12-31",
  quote: { close: 21.12, previous_close: 21.4, change: -0.28, chg_pct: -1.31, change_sign: "-", nav_date: "2026-11-12", currency: "USD" },
  qa: { blocking: false, bars: 120 }, strategy: null, institutional: [],
  levels: { status: "insufficient", resistance: [], support: [] },
  bars: [{ date: "2026-11-11", close: 21.4 }, { date: "2026-11-12", close: 21.12 }],
};

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });
const run = (sql, ...args) => env.DB.prepare(sql).bind(...args).run();

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    stockPack: { as_of: "2026-11-17", analysis_schema: "stock-analysis/v2", results: {} },
    now: () => Date.parse("2026-11-18T10:00:05Z"),
  };
  await run("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)", FRIEND, "n", "2026-11-07T00:00:00Z");
  await run("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) " +
    "VALUES (?, '2026-11-08', 'https://isin.twse.com.tw', ?, '2026-11-17T12:00:00Z', 1, 2)", VERSION, "a".repeat(64));
  await run("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, '2026-11-17T12:00:00Z')", VERSION);
  await run("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, " +
    "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '2330', '台積電', '上市', '普通股', 'TW0002330008', '1994/09/05', '半導體業', 'ESVUFR', '2026-11-17', 500, 'available', 1, 'liquid-v1')", VERSION);
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, '2330', '2026-11-17', ?)", VERSION,
    JSON.stringify({ schema: "stock-analysis/v2", code: "2330", name: "台積電", kind: "common", market: "上市", as_of: "2026-11-17" }));
  await run("INSERT INTO fund_securities (version, code, name, full_name, currency, isin, latest_bar_date, available_bars, analysis_status) " +
    "VALUES (?, ?, ?, ?, 'USD', 'LU2533723974', '2026-11-12', 990, 'available')", VERSION, FUND.code, FUND.name, FUND.full_name);
  await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, '2026-11-12', ?)", VERSION, FUND.code, JSON.stringify(FUND_ANALYSIS));
  await run("INSERT INTO stock_history (code, data_date, payload) VALUES (?, '2026-11-17', ?)", FUND.code,
    JSON.stringify({ schema: "stock-history/v1", code: FUND.code, as_of: "2026-11-12", d: [20221201, 20261111, 20261112], c: [10.02, 21.4, 21.12] }));
});

async function call(path, email, init = {}) {
  const h = new Headers(init.headers || {});
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { ...init, headers: h }), env, next, deps);
}
async function search(email, q) {
  const response = await call(`/api/stocks/search?q=${encodeURIComponent(q)}`, email);
  assert.equal(response.status, 200);
  return response.json();
}

const SWITCH_CASES = [
  [undefined, { [OWNER]: false, [FRIEND]: false }],
  ["off", { [OWNER]: false, [FRIEND]: false }],
  ["All", { [OWNER]: false, [FRIEND]: false }],
  ["owner", { [OWNER]: true, [FRIEND]: false }],
  ["all", { [OWNER]: true, [FRIEND]: true }],
];
const setSwitch = (value) => {
  if (value === undefined) delete env.MARKET_ANA_STOCK_FUNDS; else env.MARKET_ANA_STOCK_FUNDS = value;
};

test("搜尋：貝萊德、世界科技、A10、a10、BLKTENAU、blktenau、世界科技A10 都回基金一筆，類別基金、市場境外基金", async () => {
  env.MARKET_ANA_STOCK_FUNDS = "all";
  for (const q of ["貝萊德", "世界科技", "A10", "a10", "BLKTENAU", "blktenau", "世界科技A10", "總報酬穩定配息"]) {
    const body = await search(FRIEND, q);
    assert.deepEqual(body.items.map((item) => [item.code, item.name, item.security_type, item.market]),
      [[FUND.code, FUND.name, "基金", "境外基金"]], q);
    assert.equal(body.items[0].analysis_available, true, q);
    assert.equal(body.items[0].data_date, "2026-11-12", q);
    assert.equal("funds" in body, false, "名稱搜尋分支的 funds 欄退場");
  }
});

test("搜尋：開關三種狀態 × 站主與其他獲准使用者；關的人搜不到基金，普通股照舊", async () => {
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      assert.deepEqual((await search(email, "貝萊德")).items.map((item) => item.code), open[email] ? [FUND.code] : [], label);
      assert.deepEqual((await search(email, "台積電")).items.map((item) => item.code), ["2330"], label);
    }
  }
});

test("搜尋：基金、ETF 兩個開關都開時三種名錄一起查，各自類別正確", async () => {
  env.MARKET_ANA_STOCK_FUNDS = "all";
  env.MARKET_ANA_STOCK_ETFS = "all";
  await run("INSERT INTO etf_securities (version, code, name, market, etf_type, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '00981A', '主動統一台股增長', '上市', '主動式股票型', '2026-11-17', 300, 'available', 41, 'liquid-v1')", VERSION);
  assert.deepEqual((await search(OWNER, "00981A")).items.map((item) => item.security_type), ["ETF"]);
  assert.deepEqual((await search(OWNER, "貝萊德")).items.map((item) => item.security_type), ["基金"]);
  assert.deepEqual((await search(OWNER, "台積電")).items.map((item) => item.security_type), ["普通股"]);
});

test("基金名錄的表還沒建（0026 未套）：開關開著，搜尋照常只回普通股、不出錯", async () => {
  env.MARKET_ANA_STOCK_FUNDS = "all";
  env.DB._raw.exec("DROP TABLE fund_securities");
  assert.deepEqual((await search(OWNER, "貝萊德")).items, []);
  assert.deepEqual((await search(OWNER, "台積電")).items.map((item) => item.code), ["2330"]);
});

test("個股入口：開的人拿到 kind fund 的分析（淨值、幣別、淨值日）；關的人和沒有這檔一樣", async () => {
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const response = await call(`/api/stocks/${FUND.code}/analysis`, email);
      if (open[email]) {
        assert.equal(response.status, 200, label);
        const body = await response.json();
        assert.deepEqual([body.kind, body.market, body.currency, body.quote.close, body.quote.nav_date],
          ["fund", "境外基金", "USD", 21.12, "2026-11-12"], label);
      } else {
        assert.equal(response.status, 404, label);
        assert.equal((await response.json()).error, "not-in-cloud", label);
      }
      assert.equal((await call("/api/stocks/2330/analysis", email)).status, 200, label);
    }
  }
});

// 04 票：只看淨值算的策略與價位（fund_stock.nav_strategy／nav_levels 的輸出形狀，數字取自 11-16 真淨值的建置結果）。
const FUND_LEVEL = (tick, distance, kinds) => ({ tick, level: tick, low: tick, high: tick, distance_pct: distance, tier: "near",
  signal_count: kinds.length, structural: true, strength_score: null, sources: kinds.map((kind) => ({ kind, label: kind, value: tick, date: "2026-11-05" })) });
const FUND_STRATEGY_LEVELS = {
  strategy: { strategy_version: "fund-nav-close-v1", recommendation: "減碼", reason: "淨值和月線都跌到季線下，趨勢向下；先降低部位，跌破停損價不等反彈。",
    prices: { entry: 17.11, add: null, stop: 15.01, target: 17.76 }, evidence_strength: "完整",
    signals: { close: 17.14, ma20: 18.733, ma60: 20.3378 } },
  levels: { status: "available", method: "fund-nav-close-v1", aggregation_tolerance_pct: 1.31, observable_range_pct: 20,
    resistance: [FUND_LEVEL(17.76, 3.62, ["ma5", "ma10"]), FUND_LEVEL(18.67, 8.93, ["swing_low", "ma20"])],
    support: [FUND_LEVEL(17.11, -0.18, ["low_20", "low_60", "swing_low"]), FUND_LEVEL(15.01, -12.43, ["low_240"])],
    resistance_all: [], support_all: [], no_resistance_above: false, limit_up_reference: null },
};

test("個股入口（04 票）：基金的價位照給；策略和股票一樣看 strategy_v1 開關，開了才有", async () => {
  env.MARKET_ANA_STOCK_FUNDS = "all";
  await run("UPDATE stock_analysis SET payload = ? WHERE version = ? AND code = ?",
    JSON.stringify({ ...FUND_ANALYSIS, ...FUND_STRATEGY_LEVELS }), VERSION, FUND.code);
  for (const email of [OWNER, FRIEND]) {
    delete env.MARKET_ANA_ENABLED_FEATURES;
    let body = await (await call(`/api/stocks/${FUND.code}/analysis`, email)).json();
    assert.equal(Object.hasOwn(body, "strategy"), false, `${email}：核心版不給未驗證的操作建議`);
    assert.deepEqual(body.levels, FUND_STRATEGY_LEVELS.levels, email);
    env.MARKET_ANA_ENABLED_FEATURES = "strategy_v1";
    body = await (await call(`/api/stocks/${FUND.code}/analysis`, email)).json();
    assert.deepEqual([body.kind, body.strategy, body.levels], ["fund", FUND_STRATEGY_LEVELS.strategy, FUND_STRATEGY_LEVELS.levels], email);
  }
  delete env.MARKET_ANA_ENABLED_FEATURES;
});

test("長日線：開的人拿到淨值序列（只有日期與收盤）；關的人和沒有這檔一樣", async () => {
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const response = await call(`/api/stocks/${FUND.code}/history`, email);
      assert.equal(response.status, open[email] ? 200 : 404, label);
      const body = await response.json();
      if (open[email]) assert.deepEqual([body.d.length, body.c.at(-1), "o" in body], [3, 21.12, false], label);
      else assert.equal(body.error, "no-history", label);
    }
  }
});

test("即時行情與逐筆（站主才有）：基金沒有，直接查無、不去問行情來源", async () => {
  env.MARKET_ANA_STOCK_FUNDS = "all";
  let asked = 0;
  deps.liveQuoteService = { snapshot: async () => { asked += 1; return { ok: true, live: true }; } };
  assert.equal((await call(`/api/stocks/${FUND.code}/live`, OWNER)).status, 404);
  assert.equal((await call(`/api/stocks/${FUND.code}/ticks`, OWNER)).status, 404);
  assert.equal(asked, 0);
});

test("基金開關是獨立的：打開它只多 stock_funds_v1，不連帶打開 ETF、五塊、事實區塊或策略；別的開關也打不開它", () => {
  const base = releasePolicy({}).features;
  assert.equal(base.stock_funds_v1, false);
  for (const value of ["owner", "all"]) {
    const features = releasePolicy({ MARKET_ANA_STOCK_FUNDS: value }).features;
    assert.equal(features.stock_funds_v1, true, value);
    assert.deepEqual({ ...features, stock_funds_v1: false }, base, value);
  }
  for (const other of [{ MARKET_ANA_STOCK_ETFS: "all" }, { MARKET_ANA_STOCK_SIGNALS: "all" }, { MARKET_ANA_STRATEGIES_ENABLED: "true" },
    { MARKET_ANA_ENABLED_FEATURES: "stock_funds_v1" }]) {
    assert.equal(releasePolicy(other).features.stock_funds_v1, false, JSON.stringify(other));
  }
  assert.equal(policyForViewer(releasePolicy({ MARKET_ANA_STOCK_FUNDS: "owner" }), false, { MARKET_ANA_STOCK_FUNDS: "owner" }).features.stock_funds_v1, false);
  assert.equal(policyForViewer(releasePolicy({ MARKET_ANA_STOCK_FUNDS: "all" }), false, { MARKET_ANA_STOCK_FUNDS: "all" }).features.stock_funds_v1, true);
});

// 規格第 8 條：首頁搜尋點了基金，和股票一樣記進「最近搜尋」（市場記「境外基金」）。開關對這個人關著時和名錄外代號一樣查無。
// （加自選、庫存收基金是 07 票：要先有單位數＋台幣總成本與淨值×匯率的計價，這張不開。）
test("最近搜尋：開的人點基金會記下來（市場境外基金）；關的人查無", async () => {
  const post = (email) => call("/api/search-history", email, {
    method: "POST", headers: { "content-type": "application/json", origin: "https://market-ana.pages.dev" },
    body: JSON.stringify({ code: FUND.code }),
  });
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const response = await post(email);
      assert.equal(response.ok, open[email], `${label} ${response.status}`);
      const listed = await (await call("/api/search-history", email)).json();
      const row = listed.items.find((item) => item.code === FUND.code);
      if (open[email]) assert.deepEqual([row?.name, row?.market], [FUND.name, "境外基金"], label);
    }
  }
});

test("最近搜尋：基金名錄的表還沒建（0026 未套）時，股票照常記錄", async () => {
  env.MARKET_ANA_STOCK_FUNDS = "all";
  env.DB._raw.exec("DROP TABLE fund_securities");
  const response = await call("/api/search-history", OWNER, {
    method: "POST", headers: { "content-type": "application/json", origin: "https://market-ana.pages.dev" },
    body: JSON.stringify({ code: "2330" }),
  });
  assert.equal(response.ok, true, String(response.status));
});
