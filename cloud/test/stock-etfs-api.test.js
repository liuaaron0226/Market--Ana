// 個股頁五塊新功能 12 票（規格 .scratch/stock-signals/spec.md「ETF 納入」）的網站入口：
// ETF 自己一個開關三種狀態（關、只開站主、開給全部獲准使用者）下，站主與其他獲准使用者對
// 名單內 ETF（0056、主動式 00981A）、名單外 ETF、槓桿型 ETF（00631L）、0050 各自的搜尋結果、個股入口與加自選。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const EXAMPLE = JSON.parse(readFileSync(new URL("./fixtures/stock-analysis-v2.example.json", import.meta.url), "utf8"));
const FRIEND = "friend@x.com";
const VERSION = "liquid-20261109-etf-v2";
const etfPayload = (code, name) => ({ ...EXAMPLE, code, name, kind: "etf", as_of: "2026-11-09" });

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function run(sql, ...args) {
  await env.DB.prepare(sql).bind(...args).run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    // Functions 內建的快取子集合只有舊代號與 0050；ETF 的分析都在 D1。
    stockPack: { as_of: "2026-11-09", analysis_schema: "stock-analysis/v2", results: {} },
    now: () => Date.parse("2026-11-10T10:00:05Z"),
    quoteFetch: async () => new Response(JSON.stringify({ msgArray: [] }), { status: 200 }),
  };
  await run("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)", FRIEND, "n", "2026-11-07T00:00:00Z");
  await run("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) " +
    "VALUES (?, '2026-11-08', 'https://isin.twse.com.tw', ?, '2026-11-09T12:00:00Z', 1, 5)", VERSION, "a".repeat(64));
  await run("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, '2026-11-09T12:00:00Z')", VERSION);
  await run("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, " +
    "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '2330', '台積電', '上市', '普通股', 'TW0002330008', '1994/09/05', '半導體業', 'ESVUFR', '2026-11-09', 500, 'available', 1, 'liquid-v1')", VERSION);
  for (const [code, name] of [["2330", "台積電"], ["0050", "元大台灣50"], ["0056", "元大高股息"], ["00981A", "主動統一台股增長"]]) {
    const payload = code === "2330" ? { ...EXAMPLE, as_of: "2026-11-09" } : etfPayload(code, name);
    await run("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, '2026-11-09', ?)", VERSION, code, JSON.stringify(payload));
  }
  // ETF 名錄：名單內的股票型 ETF（含 0050），類別 ETF，和普通股名錄分開放。
  for (const [code, name, rank, type] of [["0050", "元大台灣50", 4, "股票型"], ["0056", "元大高股息", 56, "股票型"], ["00981A", "主動統一台股增長", 41, "主動式股票型"]]) {
    await run("INSERT INTO etf_securities (version, code, name, market, etf_type, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
      "VALUES (?, ?, ?, '上市', ?, '2026-11-09', 300, 'available', ?, 'liquid-v1')", VERSION, code, name, type, rank);
  }
});

async function call(path, email, init = {}) {
  const h = new Headers(init.headers || {});
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { ...init, headers: h }), env, next, deps);
}
async function searchCodes(email, q) {
  const response = await call(`/api/stocks/search?q=${encodeURIComponent(q)}`, email);
  assert.equal(response.status, 200);
  return (await response.json()).items.map((item) => item.code);
}

test("只開站主：站主搜尋 0056 找得到，類別是 ETF、帶名次", async () => {
  env.MARKET_ANA_STOCK_ETFS = "owner";
  const response = await call("/api/stocks/search?q=0056", OWNER);
  const body = await response.json();
  assert.equal(body.items.length, 1);
  assert.deepEqual({ code: body.items[0].code, type: body.items[0].security_type, rank: body.items[0].universe_rank, ok: body.items[0].analysis_available },
    { code: "0056", type: "ETF", rank: 56, ok: true });
});

// 三種狀態 × 兩種人：開的人搜得到名單內 ETF（含主動式、0050）；關的人和現在一樣只搜得到普通股。
// 槓桿型（00631L）與名單外的 ETF 不在 ETF 名錄裡，誰都搜不到。
const SWITCH_CASES = [
  [undefined, { [OWNER]: false, [FRIEND]: false }],
  ["off", { [OWNER]: false, [FRIEND]: false }],
  ["Owner", { [OWNER]: false, [FRIEND]: false }],
  ["owner", { [OWNER]: true, [FRIEND]: false }],
  ["all", { [OWNER]: true, [FRIEND]: true }],
];
const setSwitch = (value) => {
  if (value === undefined) delete env.MARKET_ANA_STOCK_ETFS; else env.MARKET_ANA_STOCK_ETFS = value;
};

test("搜尋：開關三種狀態 × 站主與其他獲准使用者，對名單內 ETF、主動式、0050、槓桿型、普通股", async () => {
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const on = open[email];
      assert.deepEqual(await searchCodes(email, "0056"), on ? ["0056"] : [], label);
      assert.deepEqual(await searchCodes(email, "主動統一"), on ? ["00981A"] : [], label);
      assert.deepEqual(await searchCodes(email, "0050"), on ? ["0050"] : [], label);
      assert.deepEqual(await searchCodes(email, "00631L"), [], label);
      assert.deepEqual(await searchCodes(email, "台積電"), ["2330"], label);
    }
  }
});

test("搜尋：普通股和 ETF 混在一起照「代號完全相同 → 名稱完全相同 → 代號」排序與分頁", async () => {
  env.MARKET_ANA_STOCK_ETFS = "all";
  await run("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, " +
    "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '2884', '元大金', '上市', '普通股', 'TW0002884004', '2002/02/04', '金融保險業', 'ESVUFR', '2026-11-09', 500, 'available', 40, 'liquid-v1')", VERSION);
  const first = await (await call("/api/stocks/search?q=%E5%85%83%E5%A4%A7&limit=2", FRIEND)).json();
  assert.deepEqual(first.items.map((item) => [item.code, item.security_type]), [["0050", "ETF"], ["0056", "ETF"]]);
  const second = await (await call(`/api/stocks/search?q=%E5%85%83%E5%A4%A7&limit=2&cursor=${first.next_cursor}`, FRIEND)).json();
  assert.deepEqual(second.items.map((item) => [item.code, item.security_type]), [["2884", "普通股"]]);
  assert.equal(second.next_cursor, null);
});

test("ETF 名錄的表還沒建（0018 未套）：開關開著，搜尋照常只回普通股、不出錯", async () => {
  env.MARKET_ANA_STOCK_ETFS = "all";
  env.DB._raw.exec("DROP TABLE etf_securities");
  assert.deepEqual(await searchCodes(OWNER, "0056"), []);
  assert.deepEqual(await searchCodes(OWNER, "台積電"), ["2330"]);
});

test("個股入口：開的人打開名單內 ETF 拿到它的分析結果；關的人和現在一樣查不到；0050 誰都打得開；槓桿型誰都查不到", async () => {
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      for (const code of ["0056", "00981A"]) {
        const response = await call(`/api/stocks/${code}/analysis`, email);
        if (open[email]) {
          assert.equal(response.status, 200, label);
          const body = await response.json();
          assert.deepEqual([body.code, body.kind], [code, "etf"], label);
        } else {
          assert.equal(response.status, 404, label);
          assert.equal((await response.json()).error, "not-in-cloud", label);
        }
      }
      const fixed = await call("/api/stocks/0050/analysis", email);
      assert.equal(fixed.status, 200, label);
      assert.equal((await fixed.json()).kind, "etf", label);
      const leveraged = await call("/api/stocks/00631L/analysis", email);
      assert.equal(leveraged.status, 404, label);
      assert.equal((await leveraged.json()).error, "not-in-cloud", label);
      assert.equal((await call("/api/stocks/2330/analysis", email)).status, 200, label);
    }
  }
});

// 名錄查不到的代號，加自選時現在的做法是去官方即時行情查名稱、記成「不在本站分析範圍」；關的人照舊走這條。
const NAMES = { "0056": "元大高股息", "00981A": "主動統一台股增長", "00631L": "元大台灣50正2" };
const officialQuote = async (url) => {
  const codes = [...new URL(url).searchParams.get("ex_ch").matchAll(/tse_([0-9A-Z]+)\.tw/g)].map((m) => m[1]);
  const items = codes.filter((code) => NAMES[code]).map((code) => ({ c: code, n: NAMES[code], ex: "tse", z: "38.5", y: "38.1" }));
  return new Response(JSON.stringify({ msgArray: items }), { status: 200 });
};

async function addToWatchlist(email, code) {
  const response = await call("/api/watchlist", email, {
    method: "POST", headers: { "content-type": "application/json", origin: "https://market-ana.pages.dev" },
    body: JSON.stringify({ code, horizon: "short" }),
  });
  assert.ok([200, 201].includes(response.status), `${code} ${response.status}`);
  return response.json();
}

test("加自選：開的人加名單內 ETF 記成在分析範圍內；關的人和現在一樣記成不在範圍；槓桿型誰加都不在範圍", async () => {
  deps.quoteFetch = officialQuote;
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const etf = await addToWatchlist(email, "0056");
      assert.deepEqual([etf.name, etf.in_universe], ["元大高股息", open[email]], label);
      assert.equal((await addToWatchlist(email, "00981A")).in_universe, open[email], label);
      assert.equal((await addToWatchlist(email, "00631L")).in_universe, false, label);
      assert.equal((await addToWatchlist(email, "2330")).in_universe, true, label);
    }
  }
});

test("ETF 開關是獨立的：打開它只多 stock_etfs_v1，不連帶打開五塊、事實區塊、策略或其他功能；別的開關也打不開它", () => {
  const base = releasePolicy({}).features;
  assert.equal(base.stock_etfs_v1, false);
  for (const value of ["owner", "all"]) {
    const features = releasePolicy({ MARKET_ANA_STOCK_ETFS: value }).features;
    assert.equal(features.stock_etfs_v1, true, value);
    assert.deepEqual({ ...features, stock_etfs_v1: false }, base, value);
  }
  for (const other of [{ MARKET_ANA_STOCK_SIGNALS: "all" }, { MARKET_ANA_STOCK_FACTS: "all" }, { MARKET_ANA_STRATEGIES_ENABLED: "true" },
    { MARKET_ANA_ENABLED_FEATURES: "stock_etfs_v1" }]) {
    assert.equal(releasePolicy(other).features.stock_etfs_v1, false, JSON.stringify(other));
  }
});

test("功能清單：只開站主時站主看到 stock_etfs_v1 開、其他獲准使用者看到關；開給全部時都開", async () => {
  env.MARKET_ANA_STOCK_ETFS = "owner";
  const features = async (email) => (await (await call("/api/features", email)).json()).features.stock_etfs_v1;
  assert.equal(await features(OWNER), true);
  assert.equal(await features(FRIEND), false);
  env.MARKET_ANA_STOCK_ETFS = "all";
  assert.equal(await features(FRIEND), true);
});

test("正式環境設定（wrangler.jsonc）裡 ETF 只開站主：其他獲准使用者看不到，也不連帶打開策略與 AI", () => {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  const vars = JSON.parse(text).vars;
  assert.equal(vars.MARKET_ANA_STOCK_ETFS, "owner");
  const policy = releasePolicy(vars);
  assert.equal(policyForViewer(policy, true, vars).features.stock_etfs_v1, true);
  assert.equal(policyForViewer(policy, false, vars).features.stock_etfs_v1, false);
  for (const feature of ["strategy_v1", "level_advice", "ai_digest", "ai_stock_advice"]) {
    assert.equal(policy.features[feature], false, feature);
  }
});

test("持股：關的人持有 ETF，價格照舊用官方行情；開的人才用本站盤後資料", async () => {
  deps.quoteFetch = officialQuote;
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const saved = await call("/api/holdings", email, {
        method: "POST", headers: { "content-type": "application/json", origin: "https://market-ana.pages.dev" },
        body: JSON.stringify({ code: "0056", shares: 1000, average_cost: 35 }),
      });
      assert.ok([200, 201].includes(saved.status), `${label} ${saved.status}`);
      const listed = await (await call("/api/holdings", email)).json();
      const item = listed.items.find((row) => row.code === "0056");
      assert.equal(item.price_source === "本站盤後資料", open[email], `${label} ${item.price_source}`);
    }
  }
});

test("即時行情（站主才有）：ETF 開關關著時，0050 以外的 ETF 和現在一樣沒有分析資料可對", async () => {
  deps.liveQuoteService = { snapshot: async (code) => ({ ok: true, code, live: true }) };
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    const etf = await call("/api/stocks/0056/live", OWNER);
    assert.equal(etf.status, open[OWNER] ? 200 : 404, String(value));
    assert.equal((await call("/api/stocks/0050/live", OWNER)).status, 200, String(value));
  }
});

test("長日線：關的人打 0050 以外的 ETF 和現在一樣沒有；開的人拿得到；0050 與普通股誰都拿得到", async () => {
  for (const code of ["0050", "0056", "2330"]) {
    await run("INSERT INTO stock_history (code, data_date, payload) VALUES (?, '2026-11-09', ?)", code,
      JSON.stringify({ schema: "stock-history/v1", code, as_of: "2026-11-09", d: [20261109], o: [1], h: [1], l: [1], c: [1], v: [1] }));
  }
  for (const [value, open] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      const label = `${value}/${email}`;
      const etf = await call("/api/stocks/0056/history", email);
      assert.equal(etf.status, open[email] ? 200 : 404, label);
      if (!open[email]) assert.equal((await etf.json()).error, "no-history", label);
      assert.equal((await call("/api/stocks/0050/history", email)).status, 200, label);
      assert.equal((await call("/api/stocks/2330/history", email)).status, 200, label);
    }
  }
});

test("名單外的股票型 ETF（00878 不在這一版名單）：開關怎麼設都和現在一樣搜不到、打不開", async () => {
  for (const [value] of SWITCH_CASES) {
    setSwitch(value);
    for (const email of [OWNER, FRIEND]) {
      assert.deepEqual(await searchCodes(email, "00878"), [], `${value}/${email}`);
      const response = await call("/api/stocks/00878/analysis", email);
      assert.equal(response.status, 404);
      assert.equal((await response.json()).error, "not-in-cloud");
    }
  }
});
