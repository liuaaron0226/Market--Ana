import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const STOCKS = {
  as_of: "2026-11-09",
  analysis_schema: "stock-analysis/v2",
  results: { "2330": JSON.stringify({ schema: "stock-analysis/v2", code: "2330", market: "上市" }) },
};

let issuer, env, deps;
const next = async () => new Response("static-ok");

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), stockPack: STOCKS, now: () => Date.parse("2026-11-10T02:22:05Z"),
    liveQuoteService: { snapshot: async () => ({ ok: true, code: "2330", market: "tse", market_open: true, live: true, quote: { price: 2265 }, candles: { "1": [], "5": [], "15": [] }, retry_after_seconds: 0 }) },
  };
});

async function req(path, email = OWNER) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://p3-probe.market-ana.pages.dev" + path, { headers }), env, next, deps);
}
async function seed(email, status = "approved") {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
    .bind(email, "n", status, "2026-11-10T00:00:00Z").run();
}

test("只有站主從個股 live API 取得即時資料，核准非站主與停權者都拿不到", async () => {
  assert.equal((await req("/api/stocks/2330/live", null)).status, 401);
  const response = await req("/api/stocks/2330/live");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  const body = await response.json();
  assert.equal(body.environment, "cloud");
  assert.equal(body.live, true);
  assert.equal(body.quote.price, 2265);
  deps.liveQuoteService = { snapshot: async () => { throw new Error("unauthorized upstream call"); } };
  for (const status of ["approved", "pending", "disabled"]) {
    const email = `${status}@example.com`;
    await seed(email, status);
    assert.equal((await req("/api/stocks/2330/live", email)).status, 403);
  }
});

test("MIS 診斷端點只給站主：核准非站主、待審、停權都不得經它取得即時價", async () => {
  let reached = 0;
  const probeNext = async () => { reached += 1; return new Response("probe-ok"); };
  const call = async email => {
    const headers = new Headers();
    headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
    return handle(new Request("https://p3-probe.market-ana.pages.dev/api/mis-probe", { headers }), env, probeNext, deps);
  };
  assert.equal((await call(OWNER)).status, 200);
  assert.equal(reached, 1);
  for (const status of ["approved", "pending", "disabled"]) {
    const email = `probe-${status}@example.com`;
    await seed(email, status);
    assert.equal((await call(email)).status, 403);
  }
  assert.equal(reached, 1);
});

test("不在內嵌資料包、但在 D1 現行版本的股票也能取得即時行情（市場別取自 D1）", async () => {
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .bind("v-live", "2026-11-08", "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "5".repeat(64), "2026-11-09T10:00:00Z", 0, 1).run();
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, ?)")
    .bind("v-live", "2026-11-10T00:00:00Z").run();
  await env.DB.prepare("INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)")
    .bind("v-live", "6488", "2026-11-09", JSON.stringify({ schema: "stock-analysis/v2", code: "6488", market: "上櫃" })).run();
  const seen = [];
  deps.liveQuoteService = { snapshot: async (code, market) => { seen.push([code, market]);
    return { ok: true, code, market, market_open: true, live: true, quote: { price: 500 }, candles: {}, retry_after_seconds: 0 }; } };
  const response = await req("/api/stocks/6488/live");
  assert.equal(response.status, 200);
  assert.deepEqual(seen, [["6488", "otc"]]);
  assert.equal((await req("/api/stocks/9999/live")).status, 404);
  assert.equal(seen.length, 1);
});

test("live API 429 同時在標頭與 JSON 提供退避秒數", async () => {
  deps.rate = { live: 1 };
  assert.equal((await req("/api/stocks/2330/live")).status, 200);
  const limited = await req("/api/stocks/2330/live");
  assert.equal(limited.status, 429);
  const body = await limited.json();
  assert.equal(body.retry_after_seconds, Number(limited.headers.get("retry-after")));
  assert.ok(body.retry_after_seconds > 0);
});

test("MIS 在 Cloudflare 機房失敗時明確回雲端無法取得，不冒充 LIVE", async () => {
  deps.liveQuoteService = { snapshot: async () => ({ ok: false, market_open: true, live: false, error: "MIS HTTP 520", retry_after_seconds: 10, candles: { "1": [], "5": [], "15": [] } }) };
  const response = await req("/api/stocks/2330/live");
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("retry-after"), "10");
  const body = await response.json();
  assert.equal(body.live, false);
  assert.equal(body.error, "cloud-live-unavailable");
  assert.equal(body.message, "雲端無法取得即時資料");
});
