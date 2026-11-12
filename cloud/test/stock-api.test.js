// 接縫二（續）：雲端個股分析 API。雲端不能跑 Python，結果由 build 預算好放進資料包；這裡只驗外部行為。
// S2（2026-11-08）：本人定案「自創部分不保留」，個股分析改為契約 v2，每檔一筆、沒有方案（preset）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

// S1 凍結的 2330 真實輸出（bars 只留 3 根）；契約：.scratch/chart-screening-workbench/stock-analysis-contract-v2.md
const EXAMPLE = JSON.parse(readFileSync(new URL("./fixtures/stock-analysis-v2.example.json", import.meta.url), "utf8"));
const RETIRED_KEYS = ["preset", "available_presets", "score", "score_complete", "score_note", "dimensions", "coverage",
  "strategies", "strategy_counts", "risk_flags", "trend", "formula", "params_hash", "sources"];
const REFERENCE_NAMES = ["2TON", "漲停寶寶", "甜甜價", "甜價", "私藏", "CP值", "抄底", "FIT-V1"];

const v2 = (code, name, extra = {}) => ({ ...EXAMPLE, code, name, ...extra });
const result = v2("2330", "台積電");
const STOCKS = {
  as_of: "2026-11-06",
  analysis_schema: "stock-analysis/v2",
  results: {
    "2330": result,
    "9999": { schema: "stock-analysis/v2", code: "9999", as_of: "2026-11-06", qa: { blocking: true, reason: "資料不足" } },
  },
};
const PACK = { version: "v1", overview: {}, details: {} };

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER,
    MARKET_ANA_STRATEGIES_ENABLED: "true" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: PACK, stockPack: STOCKS, now: () => Date.parse("2026-11-07T10:00:05Z") };
});

async function req(path, email = OWNER) {
  const h = new Headers();
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { headers: h }), env, next, deps);
}
async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}
async function seedVersion(version, catalogCount, analysisCount, url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2") {
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind(version, "2026-11-08", url, "5".repeat(64), "2026-11-08T10:12:36Z", catalogCount, analysisCount).run();
}
async function activate(version) {
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, ?)")
    .bind(version, "2026-11-08T10:13:00Z").run();
}
async function seedSecurity(version, item) {
  await env.DB.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES (?, ?, ?, ?, '普通股', ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    .bind(version, item.code, item.name, item.market || "上市", item.isin || `TW${item.code}`, item.listed_date || "2000/01/01", item.industry || "其他",
      item.cfi_code || "ESVUFR", item.latest_bar_date || "2026-11-06", item.available_bars ?? 300, item.analysis_status || "available",
      item.universe_rank ?? null, item.universe_rule ?? null).run();
}
async function seedAnalysis(version, payload) {
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind(version, payload.code, payload.as_of, JSON.stringify(payload)).run();
}
async function seedCatalog(entries, version = "search-v1") {
  await seedVersion(version, entries.length, 0);
  for (const item of entries) await seedSecurity(version, item);
  await activate(version);
}

test("未登入、未核准拿不到個股分析", async () => {
  assert.equal((await req("/api/stocks/2330/analysis", null)).status, 401);
  await seed("p@x.com", "pending");
  const r = await req("/api/stocks/2330/analysis", "p@x.com");
  assert.equal(r.status, 403);
  assert.doesNotMatch(await r.text(), /台積電/);
  await seed("disabled@x.com", "disabled");
  const disabled = await req("/api/stocks/1102/analysis", "disabled@x.com");
  assert.equal(disabled.status, 403);
  assert.doesNotMatch(await disabled.text(), /亞泥/);
});

test("受保護搜尋以代號、完整名稱、部分名稱查到官方上市普通股並可分頁", async () => {
  await seedCatalog([
    { code: "1102", name: "亞泥", isin: "TW0001102002", listed_date: "1962/06/08", industry: "水泥工業", available_bars: 2640 },
    { code: "2330", name: "台積電", isin: "TW0002330008", industry: "半導體業" },
    { code: "6770", name: "力積電", isin: "TW0006770001", industry: "半導體業" },
  ]);
  await seed("ok@x.com", "approved");

  const exact = await req("/api/stocks/search?q=1102", "ok@x.com");
  assert.equal(exact.status, 200);
  assert.deepEqual((await exact.json()).items.map((item) => item.code), ["1102"]);

  const fullName = await req("/api/stocks/search?q=" + encodeURIComponent("台積電"), "ok@x.com");
  assert.deepEqual((await fullName.json()).items.map((item) => item.code), ["2330"]);

  const first = await req("/api/stocks/search?q=" + encodeURIComponent("積電") + "&limit=1", "ok@x.com");
  const firstBody = await first.json();
  assert.deepEqual(firstBody.items.map((item) => item.code), ["2330"]);
  assert.equal(firstBody.next_cursor, "1");
  const second = await req("/api/stocks/search?q=" + encodeURIComponent("積電") + "&limit=1&cursor=1", "ok@x.com");
  assert.deepEqual((await second.json()).items.map((item) => item.code), ["6770"]);
  assert.equal(firstBody.source_date, "2026-11-08");
});

test("stale 標的顯示最後資料日且仍可取得該日分析", async () => {
  await seedCatalog([
    { code: "1589", name: "永冠-KY", latest_bar_date: "2026-04-02", available_bars: 2495, analysis_status: "stale" },
  ]);
  await seedAnalysis("search-v1", v2("1589", "永冠-KY", { as_of: "2026-04-02" }));
  await seed("ok@x.com", "approved");

  const search = await req("/api/stocks/search?q=1589", "ok@x.com");
  const item = (await search.json()).items[0];
  assert.equal(item.analysis_available, true);
  assert.equal(item.data_date, "2026-04-02");
  assert.equal(item.analysis_status, "stale");
  const response = await req("/api/stocks/1589/analysis", "ok@x.com");
  assert.equal(response.status, 200);
  assert.equal((await response.json()).as_of, "2026-04-02");
});

test("0050保留既有分析入口但不混入上市普通股搜尋", async () => {
  await seedCatalog([]);
  const etf = v2("0050", "元大台灣50", { kind: "etf" });
  await seedAnalysis("search-v1", etf);
  await seed("ok@x.com", "approved");

  const search = await req("/api/stocks/search?q=0050", "ok@x.com");
  assert.deepEqual((await search.json()).items, []);
  const analysis = await req("/api/stocks/0050/analysis", "ok@x.com");
  assert.equal(analysis.status, 200);
  assert.deepEqual(await analysis.json(), etf);
});

test("舊309包外的官方上市普通股可由受保護API取得分析（每檔一筆）", async () => {
  const outside = v2("1102", "亞泥");
  await seedVersion("listed-20261108", 1058, 1);
  await seedSecurity("listed-20261108", { code: "1102", name: "亞泥", isin: "TW0001102002", listed_date: "1962/06/08", industry: "水泥工業", available_bars: 2640 });
  await seedAnalysis("listed-20261108", outside);
  await activate("listed-20261108");

  await seed("ok@x.com", "approved");
  const r = await req("/api/stocks/1102/analysis", "ok@x.com");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await r.json(), outside);
});

test("官方上櫃普通股以同一契約搜尋（market=上櫃）並取得分析", async () => {
  const otc = v2("6488", "環球晶", { market: "上櫃" });
  await seedVersion("twomarket-v1", 1, 1, "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4");
  await seedSecurity("twomarket-v1", { code: "6488", name: "環球晶", market: "上櫃", isin: "TW0006488000", listed_date: "2015/09/25", industry: "半導體業", available_bars: 250 });
  await seedAnalysis("twomarket-v1", otc);
  await activate("twomarket-v1");
  await seed("ok@x.com", "approved");

  const search = await req("/api/stocks/search?q=6488", "ok@x.com");
  const body = await search.json();
  assert.equal(search.status, 200);
  assert.deepEqual(Object.keys(body).sort(), ["items", "next_cursor", "source_date"]);
  const item = body.items[0];
  assert.deepEqual(Object.keys(item).sort(), [
    "analysis_available", "analysis_status", "available_bars", "code", "data_date", "latest_bar_date", "market", "name", "security_type", "source_date",
    "universe_rank", "universe_rule",  // V2：只增加欄位
  ].sort());
  assert.equal(item.market, "上櫃");
  assert.equal(item.analysis_available, true);
  assert.equal(item.data_date, "2026-11-06");
  const r = await req("/api/stocks/6488/analysis", "ok@x.com");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  const payload = await r.json();
  assert.deepEqual(payload, otc);
  assert.equal(payload.market, "上櫃");
});

test("已核准者拿到契約 v2 分析：不含方案、總分、24項條件、五維或原站命名，原站未公開項固定標示", async () => {
  await seed("ok@x.com", "approved");
  const r = await req("/api/stocks/2330/analysis", "ok@x.com");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  const text = await r.text();
  const body = JSON.parse(text);
  assert.deepEqual(body, result);
  assert.equal(body.schema, "stock-analysis/v2");
  for (const key of RETIRED_KEYS) assert.equal(Object.hasOwn(body, key), false, key);
  for (const name of REFERENCE_NAMES) assert.doesNotMatch(text, new RegExp(name), name);
  assert.doesNotMatch(text, /"[TCVIF]0[1-6]"/);
  for (const value of Object.values(body.undisclosed)) assert.deepEqual(value, { status: "undisclosed", label: "原站未公開" });
});

test("核心版預設移除 strategy_v1 操作建議但保留支撐壓力，query 不能自行啟用", async () => {
  delete env.MARKET_ANA_STRATEGIES_ENABLED;
  const response = await req("/api/stocks/2330/analysis?MARKET_ANA_STRATEGIES_ENABLED=1");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(Object.hasOwn(body, "strategy"), false);
  const features = await (await req("/api/features?MARKET_ANA_STRATEGIES_ENABLED=1")).json();
  assert.equal(features.features.strategy_v1, false);
  assert.ok(Array.isArray(body.levels.support));
  assert.ok(Array.isArray(body.levels.resistance));
});

test("舊的 ?preset= 參數一律回 400 並說明方案已移除，不靜默改回單一結果", async () => {
  await seed("ok@x.com", "approved");
  for (const path of ["/api/stocks/2330/analysis?preset=2TON-FIT-V1", "/api/stocks/2330/analysis?preset=EVIL", "/api/stocks/2330/analysis?preset="]) {
    const r = await req(path, "ok@x.com");
    assert.equal(r.status, 400, path);
    const text = await r.text();
    assert.match(text, /方案/);
    assert.doesNotMatch(text, /台積電/);
  }
});

test("指定日期只接受雲端預算的那一天，其他日期說明雲端只有最新一天", async () => {
  assert.equal((await req("/api/stocks/2330/analysis?as_of=2026-11-06")).status, 200);
  const old = await req("/api/stocks/2330/analysis?as_of=2026-10-01");
  assert.equal(old.status, 404);
  assert.match((await old.json()).message, /2026-11-06/);
});

test("代號白名單：格式錯誤 400、雲端未預算的代號 404 並說明", async () => {
  assert.equal((await req("/api/stocks/23%2F30/analysis")).status, 400);
  assert.equal((await req("/api/stocks/__proto__/analysis")).status, 400);
  const missing = await req("/api/stocks/1101/analysis");
  assert.equal(missing.status, 404);
  assert.match((await missing.json()).message, /雲端/);
});

test("資料不足的結果照實回傳（由頁面顯示原因），不偽裝成功", async () => {
  const r = await req("/api/stocks/9999/analysis");
  assert.equal(r.status, 200);
  assert.equal((await r.json()).qa.blocking, true);
});

test("個股分析算在市場 API 限速內", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 30; i++) assert.equal((await req("/api/stocks/2330/analysis", "ok@x.com")).status, 200);
  assert.equal((await req("/api/stocks/2330/analysis", "ok@x.com")).status, 429);
});

test("個股資料包未設定時回 503", async () => {
  deps.stockPack = undefined;
  assert.equal((await req("/api/stocks/2330/analysis")).status, 503);
});

// V2（11-09）：本人「整個網站都只做大量股」。母體外的普通股：搜尋不列出；個股分析回 404，
// error=outside-universe，附名次／原因與資料日，讓頁面說「不在涵蓋範圍」而不是「雲端沒資料」。
async function seedOutside(version, item) {
  await env.DB.prepare("INSERT INTO universe_outside (version, code, name, market, universe_rank, reason, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind(version, item.code, item.name, item.market || "上市", item.universe_rank ?? null, item.reason || "below_rank", "liquid-v1").run();
}

test("母體外普通股：搜尋不列出，個股分析回 404 outside-universe 並附名次與資料日", async () => {
  await seedVersion("liquid-v1", 1, 0);
  await seedSecurity("liquid-v1", { code: "2330", name: "台積電", universe_rank: 1 });
  await seedOutside("liquid-v1", { code: "1101", name: "台泥", universe_rank: 512 });
  await activate("liquid-v1");
  await seed("ok@x.com", "approved");

  const search = await req("/api/stocks/search?q=1101", "ok@x.com");
  assert.deepEqual((await search.json()).items, []);
  const r = await req("/api/stocks/1101/analysis", "ok@x.com");
  assert.equal(r.status, 404);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  const body = await r.json();
  assert.equal(body.error, "outside-universe");
  assert.equal(body.code, "1101");
  assert.equal(body.name, "台泥");
  assert.equal(body.universe_rank, 512);
  assert.equal(body.reason, "below_rank");
  assert.equal(body.universe_rule, "liquid-v1");
  assert.equal(body.source_date, "2026-11-08");
  assert.match(body.message, /涵蓋範圍/);
});

test("母體外但停牌或資料不足：原因照實回傳，不說成交量小", async () => {
  await seedVersion("liquid-v1", 0, 0);
  await seedOutside("liquid-v1", { code: "5906", name: "台南-KY", reason: "suspended_on_data_date" });
  await activate("liquid-v1");
  await seed("ok@x.com", "approved");
  const body = await (await req("/api/stocks/5906/analysis", "ok@x.com")).json();
  assert.equal(body.error, "outside-universe");
  assert.equal(body.reason, "suspended_on_data_date");
  assert.equal(body.universe_rank, null);
  assert.doesNotMatch(body.message, /成交/);
});

test("搜尋結果附名次與規則版本（只增加欄位）", async () => {
  await seedVersion("liquid-v1", 1, 0);
  await seedSecurity("liquid-v1", { code: "2330", name: "台積電", universe_rank: 1, universe_rule: "liquid-v1" });
  await activate("liquid-v1");
  await seed("ok@x.com", "approved");
  const item = (await (await req("/api/stocks/search?q=2330", "ok@x.com")).json()).items[0];
  assert.equal(item.universe_rank, 1);
  assert.equal(item.universe_rule, "liquid-v1");
  for (const key of ["analysis_available", "analysis_status", "available_bars", "code", "data_date", "latest_bar_date", "market", "name", "security_type", "source_date"]) {
    assert.ok(Object.hasOwn(item, key), key);
  }
});

test("舊三方案格式的個股資料包被拒絕（503），不把舊 payload 當 v2 回傳", async () => {
  deps.stockPack = { as_of: "2026-11-06", presets: ["2TON-FIT-V1"], default_preset: "2TON-FIT-V1",
    results: { "2330": { "2TON-FIT-V1": { code: "2330", preset: { id: "2TON-FIT-V1" } } } } };
  const r = await req("/api/stocks/2330/analysis");
  assert.equal(r.status, 503);
  assert.doesNotMatch(await r.text(), /2TON/);
});
