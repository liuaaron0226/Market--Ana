// 接縫二：後端閘門。只透過 handle()（Pages middleware 的實際入口）驗外部行為。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

let issuer, env, deps;
const PASS = new Response("static-ok", { status: 200 });
const next = async () => PASS.clone();

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks) };
});

async function req(path, { email, token, method = "GET", body, headers = {}, claimOver = {} } = {}) {
  const h = new Headers(headers);
  if (token !== undefined) h.set("cf-access-jwt-assertion", token);
  else if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email, claimOver)));
  if (body !== undefined) {
    h.set("content-type", "application/json");
    if (!h.has("origin")) h.set("origin", "https://market-ana.pages.dev");
  }
  const r = new Request("https://market-ana.pages.dev" + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  return handle(r, env, next, deps);
}

async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}

test("未登入（沒有 Access JWT）：靜態檔與 API 都拿不到", async () => {
  for (const p of ["/", "/minimal.html", "/analysis-data.js", "/api/me", "/admin", "/apply", "/apply/api/me"]) {
    const r = await req(p);
    assert.equal(r.status, 401, p);
    assert.notEqual(await r.text(), "static-ok", p);
  }
});

test("偽造或過期的 JWT 一律擋下", async () => {
  const other = await makeIssuer("test-kid");
  const forged = await other.sign(claims("a@x.com"));
  assert.equal((await req("/", { token: forged })).status, 401);
  assert.equal((await req("/", { token: "not.a.jwt" })).status, 401);
  assert.equal((await req("/", { email: OWNER, claimOver: { exp: Math.floor(Date.now() / 1000) - 3600 } })).status, 401);
  assert.equal((await req("/", { email: OWNER, claimOver: { iss: "https://evil.cloudflareaccess.com" } })).status, 401);
  assert.equal((await req("/", { email: OWNER, claimOver: { aud: ["someone-else"] } })).status, 401);
  const none = await issuer.sign(claims(OWNER), { alg: "none" });
  assert.equal((await req("/", { token: none })).status, 401);
});

test("只信 JWT 內的 email，不信 header", async () => {
  await seed("good@x.com", "approved");
  const r = await req("/", { email: "bad@x.com", headers: { "cf-access-authenticated-user-email": "good@x.com" } });
  assert.notEqual(r.status, 200);
});

test("站主可進站與後台", async () => {
  assert.equal(await (await req("/minimal.html", { email: OWNER })).text(), "static-ok");
  assert.equal(await (await req("/admin", { email: OWNER })).text(), "static-ok");
});

test("/api/me 帶登出（換帳號）網址：Access 登入網域的 logout，站主與朋友都有（本人 11-18「沒有登出這個選項」）", async () => {
  await seed("ok@x.com", "approved");
  const logout = `${TEAM}/cdn-cgi/access/logout`;
  assert.deepEqual(await (await req("/api/me", { email: OWNER })).json(), { email: OWNER, status: "approved", owner: true, logout_url: logout });
  assert.deepEqual(await (await req("/api/me", { email: "ok@x.com" })).json(), { email: "ok@x.com", status: "approved", owner: false, logout_url: logout });
});

test("已核准者可進站，但進不了後台", async () => {
  await seed("ok@x.com", "approved");
  // 票 06 起整包資料檔不再提供，改用頁面檔驗證可進站。
  assert.equal(await (await req("/minimal.js", { email: "ok@x.com" })).text(), "static-ok");
  assert.equal((await req("/admin", { email: "ok@x.com" })).status, 403);
  assert.equal((await req("/api/admin/users", { email: "ok@x.com" })).status, 403);
});

test("申請中、被拒、停用、沒申請過：網頁導去申請頁，API 回 403", async () => {
  await seed("p@x.com", "pending");
  await seed("r@x.com", "rejected");
  await seed("d@x.com", "disabled");
  for (const e of ["p@x.com", "r@x.com", "d@x.com", "nobody@x.com"]) {
    const page = await req("/", { email: e });
    assert.equal(page.status, 302, e);
    assert.equal(page.headers.get("location"), "/apply/", e);
    const data = await req("/detail-data.js", { email: e });
    assert.equal(data.status, 403, e);
    const api = await req("/api/me", { email: e });
    assert.equal(api.status, 403, e);
  }
});

test("申請頁只接受申請頁或主站的 Access 憑證", async () => {
  const applyTok = await issuer.sign(claims("new@x.com", { aud: [APPLY_AUD] }));
  assert.equal(await (await req("/apply", { token: applyTok })).text(), "static-ok");
  // 申請頁的憑證不能拿來進主站
  assert.equal((await req("/", { token: applyTok })).status, 401);
});

test("申請：建立申請中紀錄，重複申請不改狀態，站主不用申請", async () => {
  const tok = await issuer.sign(claims("new@x.com", { aud: [APPLY_AUD] }));
  let r = await req("/apply/api/me", { token: tok });
  assert.deepEqual(await r.json(), { email: "new@x.com", status: "none", owner: false });
  r = await req("/apply/api/apply", { token: tok, method: "POST", body: { nickname: "小明" } });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).status, "pending");
  await env.DB.prepare("UPDATE users SET status='disabled' WHERE email=?").bind("new@x.com").run();
  r = await req("/apply/api/apply", { token: tok, method: "POST", body: { nickname: "再一次" } });
  assert.equal((await r.json()).status, "disabled");
  const ownerTok = await issuer.sign(claims(OWNER, { aud: [APPLY_AUD] }));
  r = await req("/apply/api/me", { token: ownerTok });
  assert.deepEqual(await r.json(), { email: OWNER, status: "approved", owner: true });
});

test("申請：暱稱必填且限長，跨站請求被擋", async () => {
  const tok = await issuer.sign(claims("new@x.com", { aud: [APPLY_AUD] }));
  assert.equal((await req("/apply/api/apply", { token: tok, method: "POST", body: { nickname: "" } })).status, 400);
  assert.equal((await req("/apply/api/apply", { token: tok, method: "POST", body: { nickname: "x".repeat(41) } })).status, 400);
  assert.equal((await req("/apply/api/apply", { token: tok, method: "POST", body: { nickname: "ok" }, headers: { origin: "https://evil.example" } })).status, 403);
});

test("申請中名單上限：滿了就不再收新申請", async () => {
  for (let i = 0; i < 30; i++) await seed(`p${i}@x.com`, "pending");
  const tok = await issuer.sign(claims("late@x.com", { aud: [APPLY_AUD] }));
  const r = await req("/apply/api/apply", { token: tok, method: "POST", body: { nickname: "late" } });
  assert.equal(r.status, 429);
});

test("後台：列出名單、核准、拒絕、停用、恢復，立即生效並留紀錄", async () => {
  await seed("a@x.com", "pending");
  await seed("b@x.com", "pending");
  let r = await req("/api/admin/users", { email: OWNER });
  const list = await r.json();
  assert.deepEqual(list.users.map((u) => [u.email, u.status]).sort(), [["a@x.com", "pending"], ["b@x.com", "pending"]]);

  r = await req("/api/admin/users/a%40x.com/approve", { email: OWNER, method: "POST", body: {} });
  assert.equal((await r.json()).status, "approved");
  assert.equal(await (await req("/", { email: "a@x.com" })).text(), "static-ok");

  r = await req("/api/admin/users/b%40x.com/reject", { email: OWNER, method: "POST", body: {} });
  assert.equal((await r.json()).status, "rejected");

  r = await req("/api/admin/users/a%40x.com/disable", { email: OWNER, method: "POST", body: {} });
  assert.equal((await r.json()).status, "disabled");
  assert.equal((await req("/analysis-data.js", { email: "a@x.com" })).status, 403);

  r = await req("/api/admin/users/a%40x.com/approve", { email: OWNER, method: "POST", body: {} });
  assert.equal((await r.json()).status, "approved");

  const audit = (await env.DB.prepare("SELECT action, target FROM audit ORDER BY id").all()).results;
  assert.deepEqual(audit.map((x) => x.action), ["approve", "reject", "disable", "approve"]);
});

test("後台：站主自己不能被停用；不存在的帳號回 404；未知動作回 404；跨站請求被擋", async () => {
  assert.equal((await req(`/api/admin/users/${encodeURIComponent(OWNER)}/disable`, { email: OWNER, method: "POST", body: {} })).status, 400);
  assert.equal((await req("/api/admin/users/ghost%40x.com/approve", { email: OWNER, method: "POST", body: {} })).status, 404);
  await seed("a@x.com", "pending");
  assert.equal((await req("/api/admin/users/a%40x.com/delete", { email: OWNER, method: "POST", body: {} })).status, 404);
  assert.equal((await req("/api/admin/users/a%40x.com/approve", { email: OWNER, method: "POST", body: {}, headers: { origin: "https://evil.example" } })).status, 403);
  assert.equal((await req("/api/admin/users/a%40x.com/approve", { email: OWNER, method: "GET" })).status, 405);
});

test("email 大小寫不同視為同一人", async () => {
  await seed("mix@x.com", "approved");
  assert.equal(await (await req("/", { email: "MIX@X.com" })).text(), "static-ok");
  assert.equal(await (await req("/admin", { email: OWNER.toUpperCase() })).text(), "static-ok");
});

test("設定缺漏或資料庫故障時一律拒絕（fail closed）", async () => {
  await seed("ok@x.com", "approved");
  const tok = await issuer.sign(claims("ok@x.com"));
  const broken = { ...env, DB: { prepare: () => { throw new Error("d1 down"); } } };
  const r1 = await handle(new Request("https://market-ana.pages.dev/", { headers: { "cf-access-jwt-assertion": tok } }), broken, next, deps);
  assert.equal(r1.status, 503);
  const noAud = { ...env, MAIN_AUD: "" };
  const r2 = await handle(new Request("https://market-ana.pages.dev/", { headers: { "cf-access-jwt-assertion": tok } }), noAud, next, deps);
  assert.equal(r2.status, 503);
  const jwksDown = { fetchImpl: async () => { throw new Error("net"); } };
  const r3 = await handle(new Request("https://market-ana.pages.dev/", { headers: { "cf-access-jwt-assertion": tok } }), env, next, jwksDown);
  assert.equal(r3.status, 401);
});

test("/api/features 由 server env 決定，預設關閉且 query 不能自行開啟策略", async () => {
  let response = await req("/api/features?MARKET_ANA_STRATEGIES_ENABLED=1", { email: OWNER });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  let body = await response.json();
  assert.equal(body.core_release, true);
  assert.equal(body.features.live_market, true);
  assert.equal(body.features.strategy_v1, false);
  assert.equal(body.features.market_trend_v1, false);
  assert.equal(body.features.level_advice, false);
  assert.equal(body.features.ai_stock_advice, false);

  env.MARKET_ANA_STRATEGIES_ENABLED = "true";
  response = await req("/api/features", { email: OWNER });
  body = await response.json();
  assert.equal(body.strategies_enabled, true);
  assert.equal(body.features.strategy_v1, true);
});

test("核心版 market overview 移除未驗 market-trend-v1，完整模式才保留", async () => {
  deps.marketPack = {
    version: "market-v1",
    overview: {
      version: "market-v1",
      analysis: { keep: "chart-data", market_trend: { schema: "market-trend/v1" } },
      dashboard: { keep: "quote-data", actions: { market_trend: { schema: "market-trend/v1" }, core: "keep" } },
    },
    details: {},
  };
  let response = await req("/api/market/overview?MARKET_ANA_STRATEGIES_ENABLED=1", { email: OWNER });
  let body = await response.json();
  assert.equal(body.features.market_trend_v1, false);
  assert.equal(Object.hasOwn(body.analysis, "market_trend"), false);
  assert.equal(Object.hasOwn(body.dashboard.actions, "market_trend"), false);
  assert.equal(body.analysis.keep, "chart-data");
  assert.equal(body.dashboard.actions.core, "keep");

  env.MARKET_ANA_STRATEGIES_ENABLED = "true";
  response = await req("/api/market/overview", { email: OWNER });
  body = await response.json();
  assert.equal(body.features.market_trend_v1, true);
  assert.equal(body.analysis.market_trend.schema, "market-trend/v1");
});

test("只開走勢與個股策略時：overview 保留 market_trend，水位與 AI 解讀仍被擋，query 不能加開", async () => {
  deps.marketPack = {
    version: "market-v1",
    overview: {
      version: "market-v1",
      analysis: { keep: "chart-data", market_trend: { schema: "market-trend/v1" } },
      dashboard: { keep: "quote-data", actions: { market_trend: { schema: "market-trend/v1" }, core: "keep" } },
    },
    details: {},
  };
  env.MARKET_ANA_ENABLED_FEATURES = "strategy_v1,market_trend_v1";
  const overview = await (await req("/api/market/overview", { email: OWNER })).json();
  assert.equal(overview.features.market_trend_v1, true);
  assert.equal(overview.features.strategy_v1, true);
  assert.equal(overview.analysis.market_trend.schema, "market-trend/v1");
  assert.equal(overview.dashboard.actions.market_trend.schema, "market-trend/v1");

  const features = await (await req("/api/features?MARKET_ANA_ENABLED_FEATURES=level_advice,ai_digest", { email: OWNER })).json();
  assert.equal(features.strategies_enabled, false);
  assert.equal(features.features.level_advice, false);
  assert.equal(features.features.ai_digest, false);
  assert.equal(features.features.ai_stock_advice, false);

  const level = await req("/api/holdings/level", { email: OWNER });
  assert.equal(level.status, 403);
  assert.equal((await level.json()).error, "feature-disabled");
});

test("核心版在 level schema 前就關閉建議端點，不影響其他個人資料 route", async () => {
  const level = await req("/api/holdings/level?MARKET_ANA_STRATEGIES_ENABLED=1", { email: OWNER });
  assert.equal(level.status, 403);
  const body = await level.json();
  assert.equal(body.error, "feature-disabled");
  assert.equal(body.features.level_advice, false);

  const watchlist = await req("/api/watchlist", { email: OWNER });
  assert.equal(watchlist.status, 200);
  assert.deepEqual((await watchlist.json()).items, []);
});

test("首頁 /api/live 只讓站主經 gate 取得 S1 行情，核准非站主仍不得轉供", async () => {
  let calls = 0;
  env.FUGLE_API_KEY = "server-only-index-key";
  deps.indexLiveService = {
    async snapshot({ fugleApiKey }) {
      calls += 1;
      assert.equal(fugleApiKey, "server-only-index-key");
      return {
        ok: true, source: "fugle-index", symbol: "IX0001", live: true,
        market_open: true, phase: "open", retryable: true, retry_after_seconds: 0,
        data_time: "2026-11-11T10:00:05+08:00", fetched_at: "2026-11-11T10:00:10+08:00",
        data: { price: 28088.25, turnover_billion: null },
        series: [{ timestamp: "2026-11-11T10:00:00+08:00", price: 28088.25 }],
      };
    },
  };

  const owner = await req("/api/live", { email: OWNER });
  assert.equal(owner.status, 200);
  assert.equal(owner.headers.get("cache-control"), "private, no-store");
  const payload = await owner.json();
  assert.equal(payload.source, "fugle-index");
  assert.equal(payload.data.price, 28088.25);
  assert.equal(payload.data.turnover_billion, null);
  assert.equal(payload.series.length, 1);
  assert.doesNotMatch(JSON.stringify(payload), /server-only-index-key/);

  await seed("ok@x.com", "approved");
  const nonOwner = await req("/api/live", { email: "ok@x.com" });
  assert.equal(nonOwner.status, 403);
  assert.equal(calls, 1, "非站主不得觸發 Fugle service");
});

test("首頁 live gate 對缺 key／上游失敗回 503＋Retry-After，有 stale 資料仍回 200", async () => {
  let receivedKey = "not-called";
  deps.indexLiveService = {
    async snapshot({ fugleApiKey }) {
      receivedKey = fugleApiKey;
      return { ok: false, live: false, market_open: true, retryable: true,
        retry_after_seconds: 12, data: null, series: [], error: "FUGLE_API_KEY 未設定" };
    },
  };
  let response = await req("/api/live", { email: OWNER });
  assert.equal(receivedKey, null);
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("retry-after"), "12");
  assert.equal(response.headers.get("cache-control"), "private, no-store");

  deps.indexLiveService = { async snapshot() {
    return { ok: true, live: false, market_open: true, phase: "delayed", retryable: true,
      retry_after_seconds: 5, data: { price: 28000 }, series: [], error: "Fugle HTTP 503" };
  } };
  response = await req("/api/live", { email: OWNER });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).phase, "delayed");

  await seed("pending@x.com", "pending");
  assert.equal((await req("/api/live", { email: "pending@x.com" })).status, 403);
});

test("個股 Fugle 即時行情也只限站主，核准非站主不能耗用 server key", async () => {
  let calls = 0;
  env.FUGLE_API_KEY = "server-only-stock-key";
  deps.stockPack = {
    analysis_schema: "stock-analysis/v2", as_of: "2026-11-10",
    results: { "2330": JSON.stringify({ schema: "stock-analysis/v2", code: "2330", market: "上市" }) },
  };
  deps.liveQuoteService = {
    async snapshot(code, market, { fugleApiKey }) {
      calls += 1;
      assert.deepEqual([code, market, fugleApiKey], ["2330", "tse", "server-only-stock-key"]);
      return { ok: true, live: true, market_open: true, quote: { price: 1000 } };
    },
  };

  assert.equal((await req("/api/stocks/2330/live", { email: OWNER })).status, 200);
  await seed("ok@x.com", "approved");
  assert.equal((await req("/api/stocks/2330/live", { email: "ok@x.com" })).status, 403);
  assert.equal(calls, 1);
});

test("錯誤回應不洩漏內部訊息", async () => {
  const broken = { ...env, DB: { prepare: () => { throw new Error("SECRET-STACK"); } } };
  const tok = await issuer.sign(claims(OWNER));
  const r = await handle(new Request("https://market-ana.pages.dev/api/admin/users", { headers: { "cf-access-jwt-assertion": tok } }), broken, next, deps);
  assert.doesNotMatch(await r.text(), /SECRET-STACK/);
});
