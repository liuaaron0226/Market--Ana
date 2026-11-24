// 2026-11-24 正式站事故：D1 當天寫入額度用完，限流寫 api_hits 失敗被轉成 503，整站讀不到。
// 寫入失敗時改用記憶體計數：讀的請求照常 200，超過每分鐘額度照樣 429；寫入正常時行為不變。只透過 handle() 驗。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

let issuer, env, deps, db;
const next = async () => new Response("static-ok", { status: 200 });
const MARKET_PACK = { version: "market-v1", overview: { version: "market-v1", analysis: {}, dashboard: { actions: {} } }, details: {} };
const QUOTA = new Error("D1_ERROR: Exceeded maximum daily row writes");

// failWhen(sql)：回 true 的語句執行時丟錯（模擬寫入額度用完），其他照常。
function failingWrites(base, failWhen) {
  const wrap = (sql, statement) => ({
    bind: (...args) => wrap(sql, statement.bind(...args)),
    first: async () => { if (failWhen(sql)) throw QUOTA; return statement.first(); },
    all: async () => { if (failWhen(sql)) throw QUOTA; return statement.all(); },
    run: async () => { if (failWhen(sql)) throw QUOTA; return statement.run(); },
  });
  return { prepare: (sql) => wrap(sql, base.prepare(sql)) };
}
const hitsWrite = (sql) => sql.startsWith("INSERT INTO api_hits");
const anyWrite = (sql) => /^\s*(INSERT|UPDATE|DELETE)\b/i.test(sql);

// 每個測試用自己的固定時間（記憶體計數是模組層級的，不同測試不互相影響）
let clock = Date.UTC(2026, 10, 24, 12, 0, 0);
beforeEach(async () => {
  issuer = await makeIssuer();
  db = fakeD1();
  env = { DB: db, TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  clock += 10 * 60_000;
  const now = clock;
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: MARKET_PACK, now: () => now, rate: { market: 3, userData: 3 } };
});

async function seed(email) {
  await db.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)").bind(email, "n", "2026-11-07T00:00:00Z").run();
}

async function get(path, email) {
  const request = new Request("https://market-ana.pages.dev" + path, { headers: { "cf-access-jwt-assertion": await issuer.sign(claims(email)) } });
  return handle(request, env, next, deps);
}

test("限流寫不進 D1：自選股與大盤照常 200，不再 503", async () => {
  await seed("friend1@x.com");
  env.DB = failingWrites(db, hitsWrite);
  for (const path of ["/api/watchlist", "/api/market/overview"]) {
    const response = await get(path, "friend1@x.com");
    assert.equal(response.status, 200, path);
  }
  assert.equal((await get("/api/watchlist", OWNER)).status, 200, "站主同樣");
});

test("限流寫不進 D1：同一分鐘超過額度改由記憶體計數回 429", async () => {
  await seed("friend2@x.com");
  env.DB = failingWrites(db, hitsWrite);
  for (let i = 0; i < 3; i++) assert.equal((await get("/api/market/overview", "friend2@x.com")).status, 200, `第 ${i + 1} 次`);
  const limited = await get("/api/market/overview", "friend2@x.com");
  assert.equal(limited.status, 429);
  assert.ok(Number(limited.headers.get("retry-after")) > 0);
  // 額度各自分開：自選股（userData）還沒用過
  assert.equal((await get("/api/watchlist", "friend2@x.com")).status, 200);
  // 下一分鐘重新計算
  const later = clock + 60_000;
  deps.now = () => later;
  assert.equal((await get("/api/market/overview", "friend2@x.com")).status, 200);
});

test("計數寫得進去、超限紀錄（:over 與 audit）寫不進去：仍回 429，不是 503", async () => {
  await seed("friend3@x.com");
  env.DB = failingWrites(db, (sql) => sql.includes("VALUES (?, ?, ?, 0)") || sql.startsWith("INSERT INTO audit"));
  for (let i = 0; i < 3; i++) assert.equal((await get("/api/watchlist", "friend3@x.com")).status, 200);
  assert.equal((await get("/api/watchlist", "friend3@x.com")).status, 429);
  env.DB = failingWrites(db, (sql) => sql.startsWith("INSERT INTO audit"));
  assert.equal((await get("/api/market/overview", "friend3@x.com")).status, 200);
  for (let i = 0; i < 2; i++) await get("/api/market/overview", "friend3@x.com");
  assert.equal((await get("/api/market/overview", "friend3@x.com")).status, 429);
});

test("D1 正常：照舊寫 api_hits 計數、超限寫一筆 audit", async () => {
  await seed("friend4@x.com");
  for (let i = 0; i < 3; i++) assert.equal((await get("/api/market/overview", "friend4@x.com")).status, 200);
  assert.equal((await get("/api/market/overview", "friend4@x.com")).status, 429);
  assert.equal((await get("/api/market/overview", "friend4@x.com")).status, 429);
  const hits = await db.prepare("SELECT bucket, count FROM api_hits WHERE email = ? ORDER BY bucket").bind("friend4@x.com").all();
  assert.deepEqual(hits.results.map((row) => [row.bucket, row.count]), [["market", 3], ["market:over", 0]]);
  const audits = await db.prepare("SELECT COUNT(*) AS n FROM audit WHERE actor = ? AND action = 'rate_limited'").bind("friend4@x.com").first();
  assert.equal(audits.n, 1);
});

test("所有寫入都失敗：讀庫存照常 200（官方行情只是存不進快取）", async () => {
  await seed("friend5@x.com");
  await db.prepare(
    "INSERT INTO user_holdings (email, code, name, market, shares, average_cost, in_universe, created_at, updated_at) VALUES (?, '2330', '台積電', '上市', 1000, 500, 0, ?, ?)",
  ).bind("friend5@x.com", "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z").run();
  deps.quoteFetch = async () => Response.json({ msgArray: [{ c: "2330", z: "600", y: "590", d: "20261124" }] });
  env.DB = failingWrites(db, anyWrite);
  const response = await get("/api/holdings", "friend5@x.com");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.items[0].latest_price, 600);
});
