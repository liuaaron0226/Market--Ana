// 開放給朋友 03 票（2026-11-17）：朋友在自選股頁讀到的個股判斷，是用他自己的金鑰、根據他自己的持股寫的。
// 入口「本人只讀本人的」：不收 email 參數，只讀登入者那一列；站主、別的朋友都讀不到。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { CONSENT_VERSION } from "../lib/friend-ai.js";
import { portfoliosSql } from "../friend-ai-schedule.mjs";
import { fakeD1, makeIssuer, claims, fetchJwks, rosterOnly, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const AMY = "amy@example.com";
const BOB = "bob@example.com";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function addUser(email, status = "approved") {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
    .bind(email, email.split("@")[0], status, "2026-11-09T00:00:00Z").run();
}

function addKey(email, { consent = CONSENT_VERSION, status = "active" } = {}) {
  env.DB._raw.prepare("INSERT INTO friend_gemini_keys (email,ciphertext,iv,alg,master_key_id,consent_at,consent_version,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
    .run(email, "c", "i", "AES-256-GCM", "x", "t", consent, status, "t", "t");
}

function addStocks(email, date, { status = "generated", items = [], error = null } = {}) {
  const content = ["generated", "partial"].includes(status) ? JSON.stringify({ items, count: items.length, requested: items.length }) : null;
  env.DB._raw.prepare("INSERT INTO friend_ai_stock_daily (email,data_date,content_json,input_sha256,model,status,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)")
    .run(email, date, content, "d".repeat(64), "gemini-3.8-flash", status, error, "t", "t");
}

const item = (code, name, reason) => ({ code, name, status: `${name}站上支撐`, action: "續抱", reason, generation_status: "generated" });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_FRIEND_AI: "on" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-17T09:00:00Z"),
    rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40, friendAi: 10_000 } };
  await addUser(AMY);
  await addUser(BOB);
});

async function read(email = AMY) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  return handle(new Request(`${ORIGIN}/api/friend-ai/stocks`, { headers }), env, next, deps);
}

test("朋友只讀得到自己的個股判斷，讀不到別的朋友的", async () => {
  addKey(AMY);
  addKey(BOB);
  addStocks(AMY, "2026-11-10", { items: [item("2330", "台積電", "成本有緩衝【資料：平均成本 901.5 元】")] });
  addStocks(BOB, "2026-11-10", { items: [item("2317", "鴻海", "bob 的理由【資料：股數 7,777 股】")] });
  const res = await read(AMY);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "private, no-store");
  const body = await res.json();
  assert.equal(body.data_date, "2026-11-10");
  assert.equal(body.stocks.status, "generated");
  assert.deepEqual(body.stocks.items.map((entry) => entry.code), ["2330"]);
  const text = JSON.stringify(body);
  for (const leaked of ["2317", "鴻海", "7,777", "bob", "@"]) assert.ok(!text.includes(leaked), leaked);

  const bob = await (await read(BOB)).json();
  assert.deepEqual(bob.stocks.items.map((entry) => entry.code), ["2317"]);
  assert.ok(!JSON.stringify(bob).includes("901.5"));
});

test("入口不收別人的 email：帶參數也只回登入者自己的", async () => {
  addKey(AMY);
  addKey(BOB);
  addStocks(BOB, "2026-11-10", { items: [item("2317", "鴻海", "bob 的理由")] });
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(AMY)) });
  const res = await handle(new Request(`${ORIGIN}/api/friend-ai/stocks?email=${encodeURIComponent(BOB)}`, { headers }), env, next, deps);
  const body = await res.json();
  assert.ok(!JSON.stringify(body).includes("2317"));
  assert.deepEqual(body.stocks, { status: "unproduced", items: [], reason: null });
});

test("站主讀不到任何朋友的個股判斷；站主自己那份仍走原本的入口", async () => {
  addKey(AMY);
  addStocks(AMY, "2026-11-10", { items: [item("2330", "台積電", "amy 的理由")] });
  const res = await read(OWNER);
  assert.equal(res.status, 403);
  assert.ok(!(await res.text()).includes("amy 的理由"));
});

test("沒有持股的朋友：當天標「沒有持股」，不是失敗、也沒有原因", async () => {
  addKey(AMY);
  addStocks(AMY, "2026-11-10", { status: "no_holdings" });
  const body = await (await read(AMY)).json();
  assert.deepEqual(body.stocks, { status: "no_holdings", items: [], reason: null });
});

test("今天別人有、自己沒有或未產生：如實回未產生與原因，不拿前一天的冒充", async () => {
  addKey(AMY);
  addKey(BOB);
  addStocks(AMY, "2026-11-10", { items: [item("2330", "台積電", "昨天的理由")] });
  addStocks(AMY, "2026-11-11", { status: "unproduced", error: "quota" });
  addStocks(BOB, "2026-11-11", { items: [item("2317", "鴻海", "bob 的理由")] });
  const body = await (await read(AMY)).json();
  assert.equal(body.data_date, "2026-11-11");
  assert.deepEqual(body.stocks, { status: "unproduced", items: [], reason: "quota" });
  assert.ok(!JSON.stringify(body).includes("昨天的理由"));
});

test("部分個股沒通過檢查：照實列出哪幾檔有、哪幾檔沒有", async () => {
  addKey(AMY);
  addStocks(AMY, "2026-11-10", { status: "partial", items: [
    { code: "2317", generation_status: "unproduced" }, item("2330", "台積電", "amy 的理由")] });
  const body = await (await read(AMY)).json();
  assert.equal(body.stocks.status, "partial");
  assert.deepEqual(body.stocks.items.map((entry) => [entry.code, entry.generation_status]), [["2317", "unproduced"], ["2330", "generated"]]);
});

test("還沒設定金鑰或同意版本換掉：不給內容", async () => {
  addStocks(AMY, "2026-11-10", { items: [item("2330", "台積電", "amy 的理由")] });
  assert.deepEqual(await (await read(AMY)).json(), { available: true, configured: false });
  addKey(AMY, { consent: "2026-01-01" });
  assert.deepEqual(await (await read(AMY)).json(), { available: true, configured: true, consented: false });
});

test("資料表還沒建立（migration 未套）：當成還沒有任何一天，不是伺服器錯誤", async () => {
  addKey(AMY);
  env.DB._raw.exec("DROP TABLE friend_ai_stock_daily");
  const res = await read(AMY);
  assert.equal(res.status, 200);
  assert.deepEqual((await res.json()).stocks, { status: "unproduced", items: [], reason: null });
});

// 一個入口（本人 11-20）之後先確認這個人已核准（查名單），才回「沒有內容」；朋友 AI 自己的資料表還是不碰。
test("開關關著時入口不碰朋友 AI 的資料表就回沒有內容", async () => {
  env.MARKET_ANA_FRIEND_AI = undefined;
  env.DB = rosterOnly(env.DB);
  assert.deepEqual(await (await read(AMY)).json(), { available: false });
});

// 排程讀持股的查詢：只有可用金鑰的朋友，欄位和站主那條線（ai-digest.yml）一模一樣。
function addPortfolio(email, code, name) {
  env.DB._raw.prepare("INSERT INTO user_watchlist (email,code,name,market,horizon,in_universe,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)")
    .run(email, code, name, "上市", "long", 1, "t", "t");
  env.DB._raw.prepare("INSERT INTO user_holdings (email,code,name,market,shares,average_cost,note,in_universe,last_price,price_as_of,price_source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")
    .run(email, code, name, "上市", 1000, 900, "私人筆記", 1, 990, "2026-11-10", "證交所 MIS 最新收盤", "t", "t");
}

test("排程只讀可用金鑰朋友的持股：站主、沒設定、同意過期、停用的人一列都不讀", async () => {
  for (const email of ["old@example.com", "gone@example.com", "none@example.com"]) await addUser(email);
  addKey(AMY);
  addKey("old@example.com", { consent: "2026-01-01" });
  addKey("gone@example.com");
  env.DB._raw.prepare("UPDATE users SET status = 'disabled' WHERE email = 'gone@example.com'").run();
  for (const email of [AMY, OWNER, "old@example.com", "gone@example.com", "none@example.com"]) addPortfolio(email, "2330", "台積電");
  const rows = env.DB._raw.prepare(portfoliosSql()).all();
  assert.deepEqual([...new Set(rows.map((row) => row.email))], [AMY]);
  assert.deepEqual(rows.map((row) => row.kind).sort(), ["holding", "watchlist"]);
  assert.ok(!JSON.stringify(rows).includes("私人筆記"));
  assert.ok(!JSON.stringify(rows).includes("MIS"));
});

test("排程讀持股的欄位和站主那條線逐欄一樣（站主那邊改欄位時這裡會紅燈）", async () => {
  addKey(AMY);
  addPortfolio(AMY, "2330", "台積電");
  addPortfolio(OWNER, "2330", "台積電");
  const workflow = readFileSync(new URL("../../.github/workflows/ai-digest.yml", import.meta.url), "utf8");
  const ownerSql = /"(SELECT 'watchlist' AS kind[^"]+)"/.exec(workflow)[1].replaceAll("$OWNER_EMAIL", OWNER);
  const columns = (sql) => env.DB._raw.prepare(sql).all().map((row) => Object.keys(row).join(","));
  const owner = columns(ownerSql);
  assert.equal(owner.length, 2);
  assert.deepEqual(columns(portfoliosSql()), owner);
});

// 自選股頁：朋友讀到的是自己那份；還沒設定、同意過期或開關關著時不顯示 AI 那一行（和現在一樣）。
test("自選股頁把朋友那份對應到每一檔；沒設定、要重新同意、開關關著都不顯示", async () => {
  const { runInNewContext } = await import("node:vm");
  const window = {};
  runInNewContext(readFileSync(new URL("../../web/portfolio.js", import.meta.url), "utf8"), { window, console });
  const { friend, entryFor } = window.PortfolioAi;
  const mapped = friend({ available: true, configured: true, consented: true, data_date: "2026-11-10",
    stocks: { status: "partial", items: [item("2330", "台積電", "成本有緩衝"), { code: "2317", generation_status: "unproduced" }] } });
  assert.equal(mapped.dataDate, "2026-11-10");
  assert.equal(entryFor(mapped, "2330").text, "台積電站上支撐 · 續抱：成本有緩衝");
  assert.equal(entryFor(mapped, "2317").text, "今日未產生");
  for (const payload of [{ available: false }, { available: true, configured: false },
    { available: true, configured: true, consented: false }, null]) {
    assert.equal(friend(payload), null, JSON.stringify(payload));
  }
});
