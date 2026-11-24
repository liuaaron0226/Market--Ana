// 接縫二（續）：市場資料 API，透過 handle() 驗外部行為。資料包用小型假資料，不依賴 web/ 實檔。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const PACK = {
  version: "abc123def456",
  overview: { version: "abc123def456", analysis: { daily: [{ date: "2026-03-10", close: 1 }] }, detailIndex: { days: ["2026-03-10"] } },
  details: { "2026-03-10": { trust: { trust_buy: [{ code: "2330" }], trust_sell: [] }, foreign: {}, margin: {}, short: {}, volume: {}, futures: {} } },
};

let issuer, env, deps, clock;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  clock = { t: Date.parse("2026-11-07T10:00:05Z") };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: PACK, now: () => clock.t };
});

async function req(path, email = OWNER) {
  const h = new Headers();
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { headers: h }), env, next, deps);
}
async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}
const auditRows = (action) => env.DB._raw.prepare("SELECT * FROM audit WHERE action = ?").all(action);

test("未登入、未核准、停用者打市場 API 拿不到任何資料", async () => {
  await seed("p@x.com", "pending");
  await seed("d@x.com", "disabled");
  for (const path of ["/api/market/overview", "/api/market/detail?kind=trust&date=2026-03-10"]) {
    const anon = await req(path, null);
    assert.equal(anon.status, 401, path);
    for (const e of ["p@x.com", "d@x.com", "nobody@x.com"]) {
      const r = await req(path, e);
      assert.equal(r.status, 403, `${e} ${path}`);
      assert.doesNotMatch(await r.text(), /2330|daily/);
    }
  }
});

test("已核准者拿到總覽與單張明細，帶版本號、不可快取", async () => {
  await seed("ok@x.com", "approved");
  const r = await req("/api/market/overview", "ok@x.com");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  assert.equal(r.headers.get("x-data-version"), PACK.version);
  const body = await r.json();
  assert.equal(body.version, PACK.version);
  assert.deepEqual(body.analysis, PACK.overview.analysis);
  assert.deepEqual(body.detailIndex, PACK.overview.detailIndex);
  assert.deepEqual(body.dashboard, { actions: {} });
  assert.equal(body.features.market_trend_v1, false);
  assert.equal(body.features.ai_digest, false);
  assert.equal(body.features.daily_update, true);
  assert.equal(body.features.charts, true);
  const d = await req("/api/market/detail?kind=trust&date=2026-03-10&v=abc123def456", "ok@x.com");
  assert.equal(d.status, 200);
  assert.deepEqual(await d.json(), { version: PACK.version, kind: "trust", date: "2026-03-10", tables: PACK.details["2026-03-10"].trust });
});

test("明細參數嚴格檢查；版本不符回 409 要求重新載入", async () => {
  for (const q of ["kind=secret&date=2026-03-10", "kind=trust&date=2026-1-1", "kind=trust&date=2026-01-01", "kind=__proto__&date=2026-03-10", "kind=trust"]) {
    const r = await req(`/api/market/detail?${q}`);
    assert.ok([400, 404].includes(r.status), q);
  }
  const stale = await req("/api/market/detail?kind=trust&date=2026-03-10&v=000000000000");
  assert.equal(stale.status, 409);
  assert.match((await stale.json()).error, /重新整理/);
  assert.equal((await req("/api/market/nope")).status, 404);
});

test("舊的整包資料檔已不提供，任何人都拿不到", async () => {
  for (const f of ["analysis-data.js", "chips-data.js", "dashboard-data.js", "detail-data.js", "plain-data.js"]) {
    assert.equal((await req("/" + f)).status, 404, f);
  }
});

test("限速：每人每分鐘市場 API 30 次，超過回 429，下一分鐘恢復；不同人分開計", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 30; i++) assert.equal((await req("/api/market/overview", "ok@x.com")).status, 200, `第 ${i + 1} 次`);
  const over = await req("/api/market/overview", "ok@x.com");
  assert.equal(over.status, 429);
  assert.ok(Number(over.headers.get("retry-after")) > 0);
  assert.doesNotMatch(await over.text(), /daily/);
  assert.equal((await req("/api/market/overview", OWNER)).status, 200, "別人不受影響");
  clock.t += 60_000;
  assert.equal((await req("/api/market/overview", "ok@x.com")).status, 200);
});

test("限速：明細另有較緊的額度（每分鐘 12 次），用完不影響總覽", async () => {
  await seed("ok@x.com", "approved");
  const path = "/api/market/detail?kind=trust&date=2026-03-10";
  for (let i = 0; i < 12; i++) assert.equal((await req(path, "ok@x.com")).status, 200, `第 ${i + 1} 次`);
  assert.equal((await req(path, "ok@x.com")).status, 429);
  assert.equal((await req("/api/market/overview", "ok@x.com")).status, 200);
});

test("超限只記一筆紀錄（同一分鐘不重複寫），站主也受限", async () => {
  for (let i = 0; i < 30; i++) await req("/api/market/overview", OWNER);
  for (let i = 0; i < 5; i++) assert.equal((await req("/api/market/overview", OWNER)).status, 429);
  assert.equal(auditRows("rate_limited").length, 1);
});

// 2026-11-24 起（D1 寫入額度用完、整站 503 的事故）：限速計數寫不進去時改用記憶體計數放行，讀的功能照常；
// 核准檢查照舊在前面。記憶體計數的超限見 rate-limit-d1-write-failure.test.js。
test("限速計數壞掉時改用記憶體計數照常回資料，錯誤細節不外洩", async () => {
  await seed("ok@x.com", "approved");
  const real = env.DB;
  env.DB = { prepare: (sql) => (/api_hits/.test(sql) ? (() => { throw new Error("d1 down"); })() : real.prepare(sql)) };
  const r = await req("/api/market/overview", "ok@x.com");
  assert.equal(r.status, 200);
  assert.doesNotMatch(await r.text(), /d1 down/);
});

test("資料包未設定時市場 API 回 503", async () => {
  deps.marketPack = undefined;
  const r = await req("/api/market/overview");
  assert.equal(r.status, 503);
});

test("首頁即時缺 server key 時回 JSON 503，不落到 HTML 或外部來源", async () => {
  const r = await req("/api/live");
  assert.equal(r.status, 503);
  assert.match(r.headers.get("content-type"), /application\/json/);
  const body = await r.json();
  assert.equal(body.error, "FUGLE_API_KEY 未設定");
  assert.equal(body.live, false);
  assert.equal(body.data, null);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  assert.equal((await req("/api/live", null)).status, 401);
});

test("/api/live 對未核准者同樣先擋 403", async () => {
  await seed("p2@x.com", "pending");
  assert.equal((await req("/api/live", "p2@x.com")).status, 403);
});
