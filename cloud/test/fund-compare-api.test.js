// 基金對照 0050（2026-11-17 本人定案）：貝萊德世界科技 A10 含息、換台幣，和 0050 比。
// 雲端不能跑 Python：build 前由 build_compare_pack.py 用 fund_compare.precompute 預算成 compare pack 的 funds 欄，gate 只原樣回傳。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { validateComparePack } from "../lib/build-compare.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const BASE = JSON.parse(readFileSync(new URL("./fixtures/compare-pack.json", import.meta.url), "utf8"));
const FUND = {
  schema: "fund-vs-0050/v1",
  fund: { code: "BLKTENAU", isin: "LU2533723974", currency: "USD", short_name: "貝萊德世界科技 A10" },
  data_date: "2026-11-12", periods: [], chart: [], benchmark_chart: [], issues: [], notes: [],
};
const PACK = { ...BASE, funds: { BLKTENAU: JSON.stringify(FUND) } };
const MARKET = { version: "v1", overview: {}, details: {} };
const PATH = "/api/funds/BLKTENAU/vs-0050";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: MARKET, comparePack: PACK, now: () => Date.parse("2026-11-17T10:00:05Z") };
});

async function req(path, email = OWNER, method = "GET") {
  const h = new Headers();
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { headers: h, method }), env, next, deps);
}
async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}

test("資料包驗證：funds 可省略（舊包）；有的話每檔要是 fund-vs-0050/v1、代碼一致", () => {
  assert.doesNotThrow(() => validateComparePack(BASE));
  assert.equal(validateComparePack(PACK).funds, 1);
  assert.throws(() => validateComparePack({ ...BASE, funds: [] }), /funds/);
  assert.throws(() => validateComparePack({ ...BASE, funds: { BLKTENAU: "{" } }), /BLKTENAU/);
  assert.throws(() => validateComparePack({ ...BASE, funds: { OTHER: JSON.stringify(FUND) } }), /OTHER/);
  assert.throws(() => validateComparePack({ ...BASE, funds: { BLKTENAU: JSON.stringify({ ...FUND, schema: "x" }) } }), /BLKTENAU/);
});

test("核准使用者拿到預算好的那份，原樣回傳、私有不快取", async () => {
  await seed("ok@x.com", "approved");
  const r = await req(PATH, "ok@x.com");
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), FUND);
  assert.match(r.headers.get("cache-control"), /private/);
  assert.match(r.headers.get("cache-control"), /no-store/);
});

test("未登入、未核准拿不到", async () => {
  await seed("p@x.com", "pending");
  assert.equal((await req(PATH, null)).status, 401);
  const r = await req(PATH, "p@x.com");
  assert.equal(r.status, 403);
  assert.doesNotMatch(await r.text(), /BLKTENAU|fund_return/);
});

test("沒有這檔或資料包沒有 funds：404 fund-unavailable；代碼格式不對 400；非 GET 405", async () => {
  assert.equal((await req("/api/funds/OTHERFUND/vs-0050")).status, 404);
  deps.comparePack = BASE;
  const r = await req(PATH);
  assert.equal(r.status, 404);
  assert.equal((await r.json()).error, "fund-unavailable");
  assert.equal((await req("/api/funds/bad%20code/vs-0050")).status, 400);
  deps.comparePack = PACK;
  assert.equal((await req(PATH, OWNER, "POST")).status, 405);
});

test("和其他市場 API 共用每分鐘 30 次的額度", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 30; i++) assert.equal((await req(PATH, "ok@x.com")).status, 200, `第 ${i + 1} 次`);
  assert.equal((await req(PATH, "ok@x.com")).status, 429);
});
