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

// 本人 11-18：分時下面的五檔與內外盤比「大家看到的一樣」；之後再定「分時走勢線開放給所有核准的人」——
// 分時要的 1 分K 與報價（昨收、開高低、總量、日期）也給所有核准使用者。60 分K 與盤中投影用的整份報價照舊只給站主（/live）。
const MINUTES = [{ t: "2026-11-10T09:00:00.000+08:00", label: "09:00", open: 2240, high: 2250, low: 2235, close: 2245, volume: 5000000 }];
const LIVE_QUOTE = { ok: true, code: "2330", market: "tse", price: 2265, previous_close: 2235, change: 30, change_percent: 1.34,
  open: 2240, high: 2280, low: 2235, volume_shares: 14037000, data_time: "2026-11-10T10:22:00+08:00", data_date: "2026-11-10", upstream_delay_ms: null };

test("分時 API：站主與核准使用者都拿得到 1 分K、分時要的報價、五檔與內外盤；沒有 60 分K；待審、停權拿不到", async () => {
  const book = { source: "fugle", time: "2026-11-10T10:22:00+08:00", bids: [[2265, 12]], asks: [[2270, 3]], flow: { at_ask: 10, at_bid: 5 } };
  deps.liveQuoteService = { snapshot: async () => ({ ok: true, code: "2330", market: "tse", market_open: true, live: true, quote: LIVE_QUOTE,
    candles: { "1": MINUTES, "60": [{ close: 1 }] }, book, retry_after_seconds: 0 }) };
  await seed("friend@example.com");
  for (const email of [OWNER, "friend@example.com"]) {
    const response = await req("/api/stocks/2330/timeshare", email);
    assert.equal(response.status, 200, email);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.deepEqual(await response.json(), {
      ok: true, code: "2330", market_open: true, minutes: MINUTES, book,
      quote: { previous_close: 2235, open: 2240, high: 2280, low: 2235, volume_shares: 14037000, data_date: "2026-11-10", data_time: "2026-11-10T10:22:00+08:00" },
    });
  }
  for (const status of ["pending", "disabled"]) {
    await seed(`${status}@example.com`, status);
    assert.equal((await req("/api/stocks/2330/timeshare", `${status}@example.com`)).status, 403);
  }
  assert.equal((await req("/api/stocks/9999/timeshare")).status, 404, "不在涵蓋範圍");
  assert.equal((await req("/api/stocks/2330/book")).status !== 200, true, "舊的 /book 已併進 /timeshare");
});

test("分時 API：還沒有資料時回空的（不是錯誤），有自己的限速", async () => {
  deps.liveQuoteService = { snapshot: async () => ({ ok: false, market_open: false, quote: null, candles: {}, book: null, retry_after_seconds: 0 }) };
  deps.rate = { timeshare: 1 };
  const first = await req("/api/stocks/2330/timeshare");
  assert.equal(first.status, 200);
  assert.deepEqual(await first.json(), { ok: true, code: "2330", market_open: false, minutes: [], quote: null, book: null });
  assert.equal((await req("/api/stocks/2330/timeshare")).status, 429);
});
