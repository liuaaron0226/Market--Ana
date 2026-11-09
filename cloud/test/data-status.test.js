// U2（2026-11-09）：每日更新狀態 /api/data-status。
// build 前由 daily_update 寫 status.json（最後成功資料日、本次狀態、原因），build.mjs 包進 Functions；
// gate 沿用登入／核准／停權／私有回應規則原樣回傳，讓前端能顯示「資料至 YYYY-MM-DD（今日更新失敗）」。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { validateDataStatus, dataStatusModuleText } from "../lib/build-status.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const STATUS = {
  schema: "data-status/v1",
  last_success_date: "2026-11-06",
  last_success_at: "2026-11-06T16:42:10+08:00",
  run: { data_date: "2026-11-09", status: "failed", failed_step: "fetch_incremental",
         reason: "官方資料在停止期限前未就緒", finished_at: "2026-11-09T21:00:03+08:00" },
  stale: true,
};

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), dataStatus: STATUS, now: () => Date.parse("2026-11-09T13:00:05Z") };
});

async function req(path, email = OWNER, method = "GET") {
  const h = new Headers();
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { method, headers: h }), env, next, deps);
}
async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}

test("未登入、未核准、停用都拿不到更新狀態", async () => {
  await seed("p@x.com", "pending");
  await seed("d@x.com", "disabled");
  assert.equal((await req("/api/data-status", null)).status, 401);
  for (const who of ["p@x.com", "d@x.com"]) {
    const r = await req("/api/data-status", who);
    assert.equal(r.status, 403, who);
    assert.doesNotMatch(await r.text(), /2026-11-06|fetch_incremental/);
  }
});

test("核准使用者與站主都拿到同一份狀態；私有、不快取", async () => {
  await seed("ok@x.com", "approved");
  for (const who of ["ok@x.com", OWNER]) {
    const r = await req("/api/data-status", who);
    assert.equal(r.status, 200, who);
    assert.match(r.headers.get("cache-control"), /private/);
    assert.match(r.headers.get("cache-control"), /no-store/);
    assert.deepEqual(await r.json(), STATUS);
  }
});

test("狀態不消耗查詢額度（每頁載入都會讀，不能吃掉選股的 30 次）", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 40; i++) assert.equal((await req("/api/data-status", "ok@x.com")).status, 200, `第 ${i + 1} 次`);
});

test("還沒有狀態檔時回 not_ready，不猜日期；方法不是 GET 回 405", async () => {
  deps.dataStatus = undefined;
  const r = await req("/api/data-status");
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { schema: "data-status/v1", status: "not_ready", last_success_date: null, stale: null });
  assert.equal((await req("/api/data-status", OWNER, "POST")).status, 405);
});

test("build 驗證：格式不符、日期格式錯、stale 與日期矛盾、含本機路徑都拒絕", () => {
  assert.equal(validateDataStatus(STATUS).lastSuccessDate, "2026-11-06");
  assert.throws(() => validateDataStatus({ ...STATUS, schema: "x" }), /data-status\/v1/);
  assert.throws(() => validateDataStatus({ ...STATUS, last_success_date: "11/06" }), /last_success_date/);
  assert.throws(() => validateDataStatus({ ...STATUS, run: { ...STATUS.run, status: "weird" } }), /run\.status/);
  assert.throws(() => validateDataStatus({ ...STATUS, run: { ...STATUS.run, reason: "C:\\Users\\x" } }), /本機路徑/);
  assert.throws(() => validateDataStatus({ ...STATUS, stale: false, run: { ...STATUS.run, status: "failed" } }), /stale/);
  const text = dataStatusModuleText(STATUS);
  assert.match(text, /^\/\/ build\.mjs 產生/);
});
