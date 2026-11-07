// 接縫二（續）：雲端個股分析 API。雲端不能跑 Python，結果由 build 預算好放進資料包；這裡只驗外部行為。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const result = (preset) => ({ code: "2330", name: "台積電", as_of: "2026-11-06", preset: { id: preset }, dimensions: { T: 20 } });
const STOCKS = {
  as_of: "2026-11-06",
  presets: ["2TON-FIT-V1", "MOMENTUM-V1", "SWING-SAFE-V1"],
  default_preset: "2TON-FIT-V1",
  results: {
    "2330": { "2TON-FIT-V1": result("2TON-FIT-V1"), "MOMENTUM-V1": result("MOMENTUM-V1"), "SWING-SAFE-V1": result("SWING-SAFE-V1") },
    "9999": { "2TON-FIT-V1": { code: "9999", qa: { blocking: true, reason: "資料不足" }, preset: { id: "2TON-FIT-V1" } } },
  },
};
const PACK = { version: "v1", overview: {}, details: {} };

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
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

test("未登入、未核准拿不到個股分析", async () => {
  assert.equal((await req("/api/stocks/2330/analysis", null)).status, 401);
  await seed("p@x.com", "pending");
  const r = await req("/api/stocks/2330/analysis", "p@x.com");
  assert.equal(r.status, 403);
  assert.doesNotMatch(await r.text(), /台積電/);
});

test("已核准者依方案拿到預算結果；沒帶方案用預設；結果帶不可快取標頭", async () => {
  await seed("ok@x.com", "approved");
  for (const preset of STOCKS.presets) {
    const r = await req(`/api/stocks/2330/analysis?preset=${preset}`, "ok@x.com");
    assert.equal(r.status, 200, preset);
    assert.equal(r.headers.get("cache-control"), "private, no-store");
    assert.deepEqual(await r.json(), result(preset));
  }
  assert.equal((await (await req("/api/stocks/2330/analysis", "ok@x.com")).json()).preset.id, "2TON-FIT-V1");
});

test("指定日期只接受雲端預算的那一天，其他日期說明雲端只有最新一天", async () => {
  assert.equal((await req("/api/stocks/2330/analysis?as_of=2026-11-06")).status, 200);
  const old = await req("/api/stocks/2330/analysis?as_of=2026-10-01");
  assert.equal(old.status, 404);
  assert.match((await old.json()).message, /2026-11-06/);
});

test("方案與代號白名單：未知方案 400、格式錯誤 400、雲端未預算的代號 404 並說明", async () => {
  assert.equal((await req("/api/stocks/2330/analysis?preset=EVIL")).status, 400);
  assert.equal((await req("/api/stocks/2330/analysis?preset=2TON-FIT-V1&preset=MOMENTUM-V1")).status, 400);
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
