// C1（2026-11-09）：雲端籌碼選股／產業資金流向／長期核心持有 API。
// 雲端不能跑 Python：build 前由 build_screens_pack.py 用本機 API 的同一組函式預算成資料包，gate 只原樣回傳。
// fixture 由 make_screens_fixture.py 產生：screens-pack.json 是資料包，screens-local.json 是同一批封存在本機 live_server 的實際回應。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const PACK = JSON.parse(readFileSync(new URL("./fixtures/screens-pack.json", import.meta.url), "utf8"));
const LOCAL = JSON.parse(readFileSync(new URL("./fixtures/screens-local.json", import.meta.url), "utf8"));
const MARKET = { version: "v1", overview: {}, details: {} };
// 畫面不得出現原站命名（本人 11-08「不用沿用他們原本取的名字」）。
const BANNED = ["Jimmy", "私藏", "甜價", "甜甜價", "主力", "觀望", "退潮", "CP值", "CP 值", "抄底", "金融獵場", "漲停寶寶"];

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: MARKET, screensPack: PACK, now: () => Date.parse("2026-11-07T10:00:05Z") };
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

const ENDPOINTS = ["/api/screens", "/api/screens?key=foreign_net_rank", "/api/screens/membership?code=6488", "/api/rotation", "/api/core-holdings"];

test("未登入、未核准、停用都拿不到選股、資金流向與核心持有", async () => {
  await seed("p@x.com", "pending");
  await seed("d@x.com", "disabled");
  for (const path of ENDPOINTS) {
    assert.equal((await req(path, null)).status, 401, `${path} 未登入`);
    for (const who of ["p@x.com", "d@x.com"]) {
      const r = await req(path, who);
      assert.equal(r.status, 403, `${path} ${who}`);
      assert.doesNotMatch(await r.text(), /外資|產業資金流向|核心持有|6488/);
    }
  }
});

test("每個本機錄到的路徑，雲端狀態碼與 JSON 欄位逐一相同（核准使用者）", async () => {
  await seed("ok@x.com", "approved");
  deps.rate = { market: 10_000, detail: 10_000 };
  for (const [path, local] of Object.entries(LOCAL)) {
    const r = await req(path, "ok@x.com");
    assert.equal(r.status, local.status, `${path} 狀態碼`);
    const cloud = await r.json();
    const expected = JSON.parse(local.body);
    if (local.status === 200) assert.deepEqual(cloud, expected, `${path} 內容`);
    else assert.equal(cloud.error, expected.error, `${path} 錯誤代碼`);
  }
});

test("回應是私有、不快取；不含原站命名；站主同樣可讀", async () => {
  for (const path of ENDPOINTS) {
    const r = await req(path, OWNER);
    assert.equal(r.status, 200, path);
    assert.match(r.headers.get("cache-control"), /private/);
    assert.match(r.headers.get("cache-control"), /no-store/);
    assert.match(r.headers.get("content-type"), /application\/json/);
    const text = await r.text();
    for (const word of BANNED) assert.ok(!text.includes(word), `${path} 含原站命名「${word}」`);
  }
});

test("沒列在名單但有產業的代號，回空名單並帶產業象限；兩者都沒有回 null", async () => {
  const pack = structuredClone(PACK);
  pack.industry_by_code = { "2303": { name: "半導體業", quadrant: "leading", label: "資金領先", date: "2026-11-06", relative_pct: 1, y: 3 } };
  deps.screensPack = pack;
  const r = await req("/api/screens/membership?code=2303");
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { code: "2303", data_date: null, memberships: [], industry: pack.industry_by_code["2303"] });
  assert.deepEqual(await (await req("/api/screens/membership?code=2330")).json(), { code: "2330", data_date: null, memberships: [], industry: null });
});

test("限速：與其他市場 API 共用每分鐘 30 次，超限 429 並只記一筆", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 30; i++) assert.equal((await req(ENDPOINTS[i % ENDPOINTS.length], "ok@x.com")).status, 200, `第 ${i + 1} 次`);
  for (const path of ENDPOINTS) {
    const r = await req(path, "ok@x.com");
    assert.equal(r.status, 429, path);
    assert.ok(Number(r.headers.get("retry-after")) > 0);
  }
  assert.equal(auditRows("rate_limited").length, 1);
});

test("參數錯誤不消耗額度；方法不是 GET 回 405", async () => {
  await seed("ok@x.com", "approved");
  for (let i = 0; i < 40; i++) assert.equal((await req("/api/screens?key=nope", "ok@x.com")).status, 400);
  assert.equal((await req("/api/rotation", "ok@x.com")).status, 200);
  const h = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(OWNER)) });
  const r = await handle(new Request("https://market-ana.pages.dev/api/rotation", { method: "POST", headers: h }), env, next, deps);
  assert.equal(r.status, 405);
});

test("資料包缺漏或格式不符回 503，不回半套資料；限速計數壞掉改用記憶體計數照常回（2026-11-24 事故）", async () => {
  deps.screensPack = undefined;
  for (const path of ENDPOINTS) assert.equal((await req(path)).status, 503, path);
  deps.screensPack = { ...PACK, schema: "old" };
  assert.equal((await req("/api/rotation")).status, 503);
  deps.screensPack = PACK;
  const real = env.DB;
  env.DB = { prepare: (sql) => (/api_hits/.test(sql) ? (() => { throw new Error("d1 down"); })() : real.prepare(sql)) };
  assert.equal((await req("/api/core-holdings")).status, 200);
});

test("未發布前照本機回未就緒；資料包缺某一組合（不一致的包）回 503 而不是猜", async () => {
  deps.screensPack = { ...PACK, catalog: JSON.stringify({ status: "not_ready", data_date: null, tabs: [] }),
    rotation: JSON.stringify({ status: "not_ready", title: "產業資金流向", latest: null }), screens: {}, membership: {}, industry_by_code: {} };
  assert.equal((await (await req("/api/screens")).json()).status, "not_ready");
  assert.equal((await (await req("/api/rotation")).json()).status, "not_ready");
  const r = await req("/api/screens?key=foreign_net_rank");
  assert.equal(r.status, 503);
});
