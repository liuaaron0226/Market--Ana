// 自選的群組（本人 2026-11-16 定案）：自己取名、一檔只放一個群組；短期／長期照舊另外標。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });
const SITE = "https://market-ana.pages.dev";

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-16T14:00:00Z") };
  for (const [email, nickname] of [["a@example.com", "A"], ["b@example.com", "B"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)").bind(email, nickname, "2026-11-09T00:00:00Z").run();
  }
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("v1", "2026-11-16", "https://example.invalid/catalog", "a".repeat(64), "2026-11-16T12:00:00Z", 3, 0, "liquid-v1").run();
  let rank = 0;
  for (const [code, name] of [["2330", "台積電"], ["2603", "長榮"], ["2609", "陽明"]]) {
    await env.DB.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES (?, ?, ?, '上市', '普通股', ?, '2000/01/01', '測試', 'ESVUFR', '2026-11-16', 300, 'available', ?, 'liquid-v1')")
      .bind("v1", code, name, `TW000${code}000`, ++rank).run();
  }
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, ?)").bind("v1", "2026-11-16T12:01:00Z").run();
});

async function req(path, { email = "a@example.com", method = "GET", body, origin = SITE } = {}) {
  const headers = new Headers();
  headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (body !== undefined) { headers.set("content-type", "application/json"); headers.set("origin", origin); }
  return handle(new Request(SITE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }), env, next, deps);
}
const save = (body, options) => req("/api/watchlist", { method: "POST", body, ...options });
const groupsOf = async (email) => Object.fromEntries((await (await req("/api/watchlist", { email })).json()).items.map((item) => [item.code, item.group]));

test("加入自選時可以帶群組，清單讀得回來；沒帶就是未分組", async () => {
  const created = await save({ code: "2603", horizon: "short", group: "航運" });
  assert.equal(created.status, 201);
  assert.equal((await created.json()).group, "航運");
  assert.equal((await save({ code: "2330", horizon: "long" })).status, 201);
  assert.deepEqual(await groupsOf("a@example.com"), { "2603": "航運", "2330": "" });
});

test("只改短期／長期（沒帶群組）不會把群組洗掉", async () => {
  await save({ code: "2603", horizon: "short", group: "航運" });
  const updated = await save({ code: "2603", horizon: "long" });
  assert.equal(updated.status, 200);
  const item = await updated.json();
  assert.equal(item.horizon, "long");
  assert.equal(item.group, "航運");
});

test("帶空字串是改回未分組；換群組就是換（一檔只在一個群組）", async () => {
  await save({ code: "2603", horizon: "short", group: "航運" });
  assert.equal((await (await save({ code: "2603", horizon: "short", group: "貨櫃三雄" })).json()).group, "貨櫃三雄");
  assert.equal((await (await save({ code: "2603", horizon: "short", group: "" })).json()).group, "");
  assert.deepEqual(await groupsOf("a@example.com"), { "2603": "" });
});

test("群組名稱：前後空白去掉、中間連續空白併成一個；太長或不是文字就拒絕", async () => {
  assert.equal((await (await save({ code: "2603", group: "  AI   伺服器 " })).json()).group, "AI 伺服器");
  const long = await save({ code: "2330", group: "一二三四五六七八九十一二三" });
  assert.equal(long.status, 400);
  assert.match((await long.json()).error, /群組名稱最多 12 個字/);
  assert.equal((await save({ code: "2330", group: 5 })).status, 400);
  assert.equal((await save({ code: "2330", group: "有\n換行" })).status, 400);
  assert.deepEqual(await groupsOf("a@example.com"), { "2603": "AI 伺服器" });
});

test("群組改名：自己這個群組裡的每一檔一起改，別人的同名群組不動", async () => {
  await save({ code: "2603", group: "航運" });
  await save({ code: "2609", group: "航運" });
  await save({ code: "2330", group: "半導體" });
  await save({ code: "2603", group: "航運" }, { email: "b@example.com" });

  const renamed = await req("/api/watchlist/groups", { method: "POST", body: { from: "航運", to: " 貨櫃 " } });
  assert.equal(renamed.status, 200);
  assert.deepEqual(await renamed.json(), { from: "航運", to: "貨櫃", changed: 2 });
  assert.deepEqual(await groupsOf("a@example.com"), { "2603": "貨櫃", "2609": "貨櫃", "2330": "半導體" });
  assert.deepEqual(await groupsOf("b@example.com"), { "2603": "航運" });
});

test("群組改名：改成已經有的名字就是併進去；沒有這個群組回 404；名稱規則同上", async () => {
  await save({ code: "2603", group: "航運" });
  await save({ code: "2330", group: "半導體" });
  assert.equal((await req("/api/watchlist/groups", { method: "POST", body: { from: "航運", to: "半導體" } })).status, 200);
  assert.deepEqual(await groupsOf("a@example.com"), { "2603": "半導體", "2330": "半導體" });
  assert.equal((await req("/api/watchlist/groups", { method: "POST", body: { from: "不存在", to: "甲" } })).status, 404);
  assert.equal((await req("/api/watchlist/groups", { method: "POST", body: { from: "半導體", to: "一二三四五六七八九十一二三" } })).status, 400);
  assert.equal((await req("/api/watchlist/groups", { method: "POST", body: { from: "", to: "甲" } })).status, 400);
  assert.equal((await req("/api/watchlist/groups", { method: "POST", body: { from: "半導體", to: "" } })).status, 400);
});

test("群組改名擋跨站請求，也只接受 POST", async () => {
  await save({ code: "2603", group: "航運" });
  assert.equal((await req("/api/watchlist/groups", { method: "POST", body: { from: "航運", to: "貨櫃" }, origin: "https://evil.example" })).status, 403);
  assert.equal((await req("/api/watchlist/groups")).status, 405);
  assert.deepEqual(await groupsOf("a@example.com"), { "2603": "航運" });
});

test("只改群組（沒帶短期／長期）不會把短期／長期洗掉；新加的沒帶就是不標", async () => {
  await save({ code: "2603", horizon: "long", group: "航運" });
  const moved = await (await save({ code: "2603", group: "貨櫃" })).json();
  assert.deepEqual([moved.horizon, moved.group], ["long", "貨櫃"]);
  assert.equal((await (await save({ code: "2330" })).json()).horizon, "none");
  assert.equal((await save({ code: "2330", horizon: "weekly" })).status, 400);
});

test("還原移出的那一檔：帶著原本加入的時間放回去，順序才不會跑掉；已經在的那一檔不能改加入的時間", async () => {
  deps.now = () => Date.parse("2026-11-10T00:00:00Z");
  await save({ code: "2603", group: "航運" });
  deps.now = () => Date.parse("2026-11-16T14:00:00Z");
  const before = (await (await req("/api/watchlist")).json()).items[0].created_at;
  assert.equal((await req("/api/watchlist/2603", { method: "DELETE", body: {} })).status, 204);
  const back = await save({ code: "2603", horizon: "short", group: "航運", created_at: before });
  assert.equal(back.status, 201);
  assert.equal((await back.json()).created_at, "2026-11-10T00:00:00.000Z");

  await save({ code: "2603", created_at: "2020-01-01T00:00:00.000Z" });
  assert.equal((await (await req("/api/watchlist")).json()).items[0].created_at, "2026-11-10T00:00:00.000Z");
  for (const bad of ["昨天", 5, "2030-01-01T00:00:00.000Z"]) {
    assert.equal((await save({ code: "2609", created_at: bad })).status, 400, String(bad));
  }
});

test("關注（另一個開關）不受改群組影響", async () => {
  await save({ code: "2603", group: "航運" });
  await env.DB.prepare("UPDATE user_watchlist SET watching = 1, watch_since = ? WHERE email = ? AND code = ?").bind("2026-11-16T00:00:00Z", "a@example.com", "2603").run();
  await save({ code: "2603", horizon: "long", group: "貨櫃" });
  const row = await env.DB.prepare("SELECT watching, group_name FROM user_watchlist WHERE email = ? AND code = ?").bind("a@example.com", "2603").first();
  assert.deepEqual({ ...row }, { watching: 1, group_name: "貨櫃" });
});
