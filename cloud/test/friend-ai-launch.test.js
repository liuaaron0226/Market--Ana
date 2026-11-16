// 開放給朋友 05 票（2026-11-17）：上線前把旅程補齊。
// 1. DECISIONS 第 11 題：站主後台每位朋友看得到「最新一天的解讀是否產生」與「自選股、庫存各幾檔」，
//    只有狀態與數字，沒有解讀內容、持有哪些股票、股數或成本。
// 2. 只有個股判斷沒產生（大盤、籌碼都有）時，朋友的頁面也要出現原因與回設定頁的提示。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { CONSENT_VERSION } from "../lib/friend-ai.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const AMY = "amy@example.com";
const BOB = "bob@example.com";
const CARA = "cara@example.com";
const DAN = "dan@example.com";
const EVE = "eve@example.com";
// friend_ai_digest.py 的 FAILURE_TEXT["validation_failed"]
const VALIDATION_TEXT = "今天的解讀沒有通過數字與代號檢查，所以沒有顯示";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function addUser(email, status = "approved") {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
    .bind(email, email.split("@")[0], status, "2026-11-09T00:00:00Z").run();
}

function addKey(email, { status = "active", failure = null, success = null } = {}) {
  env.DB._raw.prepare("INSERT INTO friend_gemini_keys (email,ciphertext,iv,alg,master_key_id,consent_at,consent_version,status,last_success_at,last_failure_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    .run(email, "c", "i", "AES-256-GCM", "x", "t", CONSENT_VERSION, status, success, failure, "t", "t");
}

function addDigest(email, date, type, status = "generated") {
  env.DB._raw.prepare("INSERT INTO friend_ai_daily (email,data_date,digest_type,content_json,input_sha256,model,status,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
    .run(email, date, type, status === "generated" ? JSON.stringify({ conclusion: `${email} 的大盤結論` }) : null,
      "d".repeat(64), "gemini-3.8-flash", status, status === "generated" ? null : "quota", "t", "t");
}

function addStocks(email, date, status = "generated") {
  const content = ["generated", "partial"].includes(status)
    ? JSON.stringify({ items: [{ code: "2330", name: "台積電", reason: `${email} 的個股理由`, generation_status: "generated" }] }) : null;
  env.DB._raw.prepare("INSERT INTO friend_ai_stock_daily (email,data_date,content_json,input_sha256,model,status,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)")
    .run(email, date, content, "d".repeat(64), "gemini-3.8-flash", status, ["generated", "no_holdings"].includes(status) ? null : "validation_failed", "t", "t");
}

const addDay = (email, date, stocks = "generated") => {
  addDigest(email, date, "market");
  addDigest(email, date, "flow");
  if (stocks) addStocks(email, date, stocks);
};

function addWatch(email, code, name) {
  env.DB._raw.prepare("INSERT INTO user_watchlist (email,code,name,market,horizon,in_universe,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)")
    .run(email, code, name, "上市", "long", 1, "t", "t");
}

function addHolding(email, code, name) {
  env.DB._raw.prepare("INSERT INTO user_holdings (email,code,name,market,shares,average_cost,note,in_universe,last_price,price_as_of,price_source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")
    .run(email, code, name, "上市", 7777, 901.5, "私人筆記", 1, 990, "2026-11-10", "證交所 MIS 最新收盤", "t", "t");
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_FRIEND_AI: "on" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-17T09:00:00Z"),
    rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40, friendAi: 10_000 } };
  for (const email of [AMY, BOB, CARA, DAN, EVE]) await addUser(email);
});

async function get(path, email) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  return handle(new Request(ORIGIN + path, { headers }), env, next, deps);
}

const adminUsers = async () => Object.fromEntries((await (await get("/api/admin/users", OWNER)).json()).users.map((u) => [u.email, u]));

test("站主後台：每位朋友最新一天的解讀是否產生，大盤、籌碼、個股三份都有才算產生", async () => {
  addKey(AMY, { success: "2026-11-11T10:00:00Z" });
  addKey(BOB, { failure: VALIDATION_TEXT });
  addKey(CARA, { status: "paused", failure: "金鑰被撤銷或停用" });
  addKey(EVE, { success: "2026-11-11T10:00:00Z" });
  addDay(AMY, "2026-11-10");
  addDay(AMY, "2026-11-11");
  addDay(BOB, "2026-11-11", "partial");
  addDay(CARA, "2026-11-10");
  addDay(EVE, "2026-11-11", "no_holdings");
  const users = await adminUsers();
  assert.deepEqual(users[AMY].gemini.digest, { data_date: "2026-11-11", produced: true });
  assert.deepEqual(users[BOB].gemini.digest, { data_date: "2026-11-11", produced: false }, "個股沒通過檢查＝當天沒產生");
  assert.deepEqual(users[CARA].gemini.digest, { data_date: "2026-11-11", produced: false }, "暫停的人那天沒有任何一列，不拿前一天的冒充");
  assert.deepEqual(users[EVE].gemini.digest, { data_date: "2026-11-11", produced: true }, "沒有持股不算失敗");
  assert.equal(users[DAN].gemini.digest, null, "沒設定金鑰的人沒有這一項");
});

test("站主後台：每位使用者的自選股與庫存各幾檔，只有數字，沒有代號、名稱、股數或成本", async () => {
  addKey(AMY);
  addDay(AMY, "2026-11-11");
  addWatch(AMY, "2330", "台積電");
  addWatch(AMY, "2454", "聯發科");
  addHolding(AMY, "2317", "鴻海");
  addWatch(DAN, "2603", "長榮");
  const users = await adminUsers();
  assert.equal(users[AMY].gemini.watchlist_count, 2);
  assert.equal(users[AMY].gemini.holdings_count, 1);
  assert.equal(users[DAN].gemini.watchlist_count, 1, "沒設定金鑰的朋友也看得到檔數");
  assert.equal(users[DAN].gemini.holdings_count, 0);
  assert.equal(users[BOB].gemini.watchlist_count, 0);
  for (const user of Object.values(users)) {
    assert.deepEqual(Object.keys(user.gemini).sort(),
      ["configured", "digest", "holdings_count", "last_failure_reason", "last_success_at", "paused", "watchlist_count"]);
  }
  const text = JSON.stringify(users);
  for (const banned of ["2330", "2454", "2317", "2603", "台積電", "鴻海", "7777", "901.5", "私人筆記", "大盤結論", "個股理由", "ciphertext"]) {
    assert.ok(!text.includes(banned), banned);
  }
});

test("朋友那兩張解讀表還沒建立（migration 未套）：後台照常列出，解讀一項是空的、檔數照算", async () => {
  addKey(AMY);
  addWatch(AMY, "2330", "台積電");
  env.DB._raw.exec("DROP TABLE friend_ai_stock_daily; DROP TABLE friend_ai_daily;");
  const res = await get("/api/admin/users", OWNER);
  assert.equal(res.status, 200);
  const users = Object.fromEntries((await res.json()).users.map((u) => [u.email, u]));
  assert.equal(users[AMY].gemini.digest, null);
  assert.equal(users[AMY].gemini.watchlist_count, 1);
});

test("只有個股判斷沒產生（大盤、籌碼都有）：朋友拿得到原因與回設定頁的提示", async () => {
  addKey(AMY, { failure: VALIDATION_TEXT });
  addDay(AMY, "2026-11-11", "partial");
  const body = await (await get("/api/friend-ai/digest", AMY)).json();
  assert.equal(body.market.status, "generated");
  assert.equal(body.flow.status, "generated");
  assert.deepEqual(body.notice, { text: VALIDATION_TEXT });
});

test("三份都產生、或沒有持股：沒有提示", async () => {
  addKey(AMY);
  addKey(EVE);
  addDay(AMY, "2026-11-11");
  addDay(EVE, "2026-11-11", "no_holdings");
  for (const email of [AMY, EVE]) {
    assert.equal((await (await get("/api/friend-ai/digest", email)).json()).notice, null, email);
  }
});
