// B1（2026-11-09）：雲端「和 0050 比」API。本人 11-09：個股頁加上和 0050 的對照；只能從大量股中選，可同時選多檔。
// 雲端不能跑 Python：build 前由 build_compare_pack.py 用 benchmark_compare 預算成資料包，gate 只組回。
// fixture 由 make_compare_fixture.py 產生：compare-pack.json 是資料包，compare-local.json 是同一母體在本機 live_server 的實際回應。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { validateComparePack, compareModuleText } from "../lib/build-compare.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const PACK = JSON.parse(readFileSync(new URL("./fixtures/compare-pack.json", import.meta.url), "utf8"));
const LOCAL = JSON.parse(readFileSync(new URL("./fixtures/compare-local.json", import.meta.url), "utf8"));
const MARKET = { version: "v1", overview: {}, details: {} };
const BANNED = ["Jimmy", "私藏", "甜價", "主力", "CP值", "抄底", "金融獵場", "漲停寶寶"];

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: MARKET, comparePack: PACK, now: () => Date.parse("2026-11-07T10:00:05Z") };
});

async function req(path, email = OWNER, method = "GET") {
  const h = new Headers();
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { headers: h, method }), env, next, deps);
}
async function seed(email, status) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "n", status, "2026-11-07T00:00:00Z").run();
}

const ENDPOINTS = ["/api/stocks/2330/vs-0050", "/api/compare-0050?codes=2330,6488"];

test("未登入、未核准、停用都拿不到對照", async () => {
  await seed("p@x.com", "pending");
  await seed("d@x.com", "disabled");
  for (const path of ENDPOINTS) {
    assert.equal((await req(path, null)).status, 401, `${path} 未登入`);
    for (const who of ["p@x.com", "d@x.com"]) {
      const r = await req(path, who);
      assert.equal(r.status, 403, `${path} ${who}`);
      assert.doesNotMatch(await r.text(), /0050|台積電|return_pct/);
    }
  }
});

test("每個本機錄到的路徑，雲端狀態碼與 JSON 逐一相同（核准使用者）", async () => {
  await seed("ok@x.com", "approved");
  deps.rate = { market: 10_000, detail: 10_000 };
  for (const [path, local] of Object.entries(LOCAL)) {
    const r = await req(path, "ok@x.com");
    assert.equal(r.status, local.status, `${path} 狀態碼`);
    const cloud = await r.json();
    const expected = JSON.parse(local.body);
    if (local.status === 200) assert.deepEqual(cloud, expected, `${path} 內容`);
    else assert.equal(cloud.error, expected.error, `${path} 錯誤代碼`);
    if (local.status === 404) assert.deepEqual(cloud, expected, `${path} 404 內容`);
  }
});

test("回應私有、不快取；不含原站命名", async () => {
  for (const path of ENDPOINTS) {
    const r = await req(path, OWNER);
    assert.equal(r.status, 200, path);
    assert.match(r.headers.get("cache-control"), /private/);
    assert.match(r.headers.get("cache-control"), /no-store/);
    const text = await r.text();
    for (const word of BANNED) assert.ok(!text.includes(word), `${path} 含「${word}」`);
  }
});

test("限速：與其他市場 API 共用每分鐘 30 次；參數錯誤不消耗額度；非 GET 回 405", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 40; i++) assert.equal((await req("/api/compare-0050?codes=x", "ok@x.com")).status, 400);
  for (let i = 0; i < 30; i++) assert.equal((await req(ENDPOINTS[i % 2], "ok@x.com")).status, 200, `第 ${i + 1} 次`);
  assert.equal((await req(ENDPOINTS[0], "ok@x.com")).status, 429);
  assert.equal((await req(ENDPOINTS[1], OWNER, "POST")).status, 405);
});

test("資料包缺漏或格式不符回 503", async () => {
  deps.comparePack = undefined;
  for (const path of ENDPOINTS) assert.equal((await req(path)).status, 503, path);
  deps.comparePack = { ...PACK, schema: "old" };
  assert.equal((await req(ENDPOINTS[0])).status, 503);
});

test("build.mjs 會把對照資料包包進 Functions，中介層會注入給 gate", () => {
  const build = readFileSync(new URL("../build.mjs", import.meta.url), "utf8");
  assert.match(build, /\.compare-pack\.json/);
  assert.match(build, /functions\/_compare-pack\.js/);
  assert.match(build, /validateComparePack/);
  const middleware = readFileSync(new URL("../functions/_middleware.js", import.meta.url), "utf8");
  assert.match(middleware, /import comparePack from "\.\/_compare-pack\.js"/);
  assert.match(middleware, /comparePack/);
  const ignore = readFileSync(new URL("../.gitignore", import.meta.url), "utf8");
  assert.match(ignore, /\.compare-pack\.json/);
  assert.match(ignore, /functions\/_compare-pack\.js/);
});

test("build 驗證：格式不符、item 不是 JSON、含本機路徑都拒絕", () => {
  assert.equal(validateComparePack(PACK).dataDate, PACK.data_date);
  assert.throws(() => validateComparePack({ ...PACK, schema: "x" }), /compare-0050-pack\/v1/);
  assert.throws(() => validateComparePack({ ...PACK, benchmark: "{" }), /benchmark/);
  assert.throws(() => validateComparePack({ ...PACK, items: { "2330": "{" } }), /2330/);
  assert.throws(() => validateComparePack({ ...PACK, notes: ["C:\\Users\\x"] }), /本機路徑/);
  assert.match(compareModuleText(PACK), /^\/\/ build\.mjs 產生/);
});
