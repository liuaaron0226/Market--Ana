// 盤勢解讀改版（本人 2026-11-23「共識達成」，.scratch/market-brief 票 02）：/api/market-brief 回最新一班。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, rosterOnly, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-23T10:40:00Z");   // 台北 11-23 18:40
const FRIEND = "a@example.com";
const PENDING = "p@example.com";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

const cat = (key, over = {}) => ({ key, say: `${key} 的一句判斷`, effect: `${key} 的影響`, conditions: [`${key} 要觀察的`],
  prepare: [`如果 ${key} 轉向，就先檢查持股`], data_date: "2026-11-23", quiet: false, ...over });
const brief = (trade_date, slot, over = {}) => ({ schema: "market-brief/v1", trade_date, slot, generated_at: `${trade_date}T18:31:00+08:00`,
  source: "ai", one_line: `${trade_date} ${slot} 的結論`, lead: "chips", cats: [cat("chips"), cat("tech")], ...over });

async function add(db, trade_date, slot, payload = brief(trade_date, slot)) {
  await db.prepare("INSERT OR REPLACE INTO market_brief_runs (trade_date, slot, generated_at, source, model, payload) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(trade_date, slot, `${trade_date}T10:31:00Z`, "ai", "test-model", typeof payload === "string" ? payload : JSON.stringify(payload)).run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_MARKET_BRIEF: "all" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW, rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  for (const [email, status] of [[FRIEND, "approved"], [PENDING, "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "A", status, "2026-11-09T00:00:00Z").run();
  }
});

async function req({ email = OWNER, method = "GET", path = "/api/market-brief" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { headers, method }), env, next, deps);
}
const latest = async (options) => (await (await req(options)).json()).brief;

test("回最新一班：整份照約定的格式，不快取", async () => {
  await add(env.DB, "2026-11-23", "evening");
  const r = await req();
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  assert.deepEqual(await r.json(), { brief: brief("2026-11-23", "evening") });
});

test("最新一班＝交易日最大的；同一天照 盤前 < 盤中 < 收盤 < 晚上 取最後一個（不看寫入先後）", async () => {
  await add(env.DB, "2026-11-20", "evening");
  await add(env.DB, "2026-11-23", "midday");
  await add(env.DB, "2026-11-23", "morning");
  assert.equal((await latest()).one_line, "2026-11-23 midday 的結論");
  await add(env.DB, "2026-11-23", "evening");
  await add(env.DB, "2026-11-23", "close");
  assert.equal((await latest()).one_line, "2026-11-23 evening 的結論");
  await add(env.DB, "2026-11-24", "morning");
  assert.equal((await latest()).one_line, "2026-11-24 morning 的結論", "隔天盤前那一班比前一天晚上的新");
  await add(env.DB, "2026-11-25", "weird");
  assert.equal((await latest()).one_line, "2026-11-24 morning 的結論", "不認得的時段不算");
});

test("同一班重跑：蓋掉舊的那一份", async () => {
  await add(env.DB, "2026-11-23", "evening");
  await add(env.DB, "2026-11-23", "evening", brief("2026-11-23", "evening", { one_line: "重跑之後的結論", source: "template" }));
  const got = await latest();
  assert.equal(got.one_line, "重跑之後的結論");
  assert.equal(got.source, "template");
});

test("還沒有任何一班：brief 是 null", async () => {
  for (const email of [OWNER, FRIEND]) assert.deepEqual(await (await req({ email })).json(), { brief: null });
});

test("存的那一份壞掉（不是 JSON、不是約定的格式、沒有結論、類別不是清單）：當成沒有，不是錯誤", async () => {
  const bad = ["{not json", "null", "[]", JSON.stringify({ ...brief("2026-11-23", "evening"), schema: "market-brief/v0" }),
    JSON.stringify({ ...brief("2026-11-23", "evening"), one_line: "  " }), JSON.stringify({ ...brief("2026-11-23", "evening"), cats: "chips" })];
  for (const payload of bad) {
    await add(env.DB, "2026-11-23", "evening", payload);
    const r = await req();
    assert.equal(r.status, 200, payload);
    assert.deepEqual(await r.json(), { brief: null }, payload);
  }
});

test("類別只留有 key 與一句判斷的；不認得的類別與欄位原樣帶著（前端自己略過）", async () => {
  const extra = cat("sectors", { bars: [1, 2, 3] });
  await add(env.DB, "2026-11-23", "evening", brief("2026-11-23", "evening",
    { cats: [cat("chips", { streak_days: 6 }), null, "tech", { key: "tech" }, { say: "沒有 key" }, extra, cat("tech", { quiet: true })], note: "之後的票加的欄位" }));
  const got = await latest();
  assert.deepEqual(got.cats, [cat("chips", { streak_days: 6 }), extra, cat("tech", { quiet: true })]);
  assert.equal(got.note, "之後的票加的欄位");
});

test("追蹤事項那一類只有站主拿得到；其他類別朋友也拿得到", async () => {
  const tracking = cat("tracking", { say: "OWNER-ONLY 聯發科站上壓力" });
  await add(env.DB, "2026-11-23", "evening", brief("2026-11-23", "evening", { lead: "tracking", cats: [cat("chips"), cat("tech"), tracking] }));
  assert.deepEqual((await latest()).cats.map((item) => item.key), ["chips", "tech", "tracking"]);
  const friend = await req({ email: FRIEND });
  const text = await friend.text();
  assert.ok(!text.includes("OWNER-ONLY") && !text.includes("聯發科"));
  assert.deepEqual(JSON.parse(text).brief.cats, [cat("chips"), cat("tech")]);
});

test("開給全部時朋友拿到和站主一樣的那一份", async () => {
  await add(env.DB, "2026-11-23", "evening");
  assert.deepEqual(await latest({ email: FRIEND }), await latest());
});

test("開關只開站主：站主拿得到；朋友拿到 null 而且不碰任何資料表", async () => {
  await add(env.DB, "2026-11-23", "evening");
  env.MARKET_ANA_MARKET_BRIEF = "owner";
  assert.equal((await latest()).one_line, "2026-11-23 evening 的結論");
  env.DB = rosterOnly(env.DB);
  const r = await req({ email: FRIEND });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { brief: null });
});

test("開關關著（沒設、寫錯）：站主也拿到 null，不碰資料表", async () => {
  await add(env.DB, "2026-11-23", "evening");
  const db = env.DB;
  for (const value of [undefined, "off", "on", "true", "ALL", "Owner"]) {
    env.MARKET_ANA_MARKET_BRIEF = value;
    env.DB = { prepare: (sql) => { if (/market_brief_runs/.test(sql)) throw new Error("開關關著不能讀這張表"); return db.prepare(sql); } };
    const r = await req();
    assert.equal(r.status, 200, String(value));
    assert.deepEqual(await r.json(), { brief: null }, String(value));
  }
});

test("功能清單：站主看開關有沒有開；朋友只有 all 才是開的", () => {
  for (const [value, owner, friend] of [[undefined, false, false], ["off", false, false], ["owner", true, false], ["all", true, true]]) {
    const policy = releasePolicy({ MARKET_ANA_MARKET_BRIEF: value });
    assert.equal(policy.features.market_brief_v1, owner, `站主 ${value}`);
    assert.equal(policyForViewer(policy, false, { MARKET_ANA_MARKET_BRIEF: value }).features.market_brief_v1, friend, `朋友 ${value}`);
  }
  assert.equal(policyForViewer(releasePolicy({ MARKET_ANA_MARKET_BRIEF: "all" }), false).features.market_brief_v1, false, "env 沒給時對朋友是關");
});

test("還沒核准的人、沒登入的人一律拿不到", async () => {
  await add(env.DB, "2026-11-23", "evening");
  const pending = await req({ email: PENDING });
  assert.equal(pending.status, 403);
  assert.ok(!(await pending.text()).includes("結論"));
  const anonymous = await req({ email: null });
  assert.ok([401, 403].includes(anonymous.status), String(anonymous.status));
});

test("只能讀：其他方法不允許", async () => {
  assert.equal((await req({ method: "POST" })).status, 405);
});

test("0035 還沒套：當成沒有資料，不是錯誤", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((file) => !file.startsWith("0035")));
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', ?)").bind(FRIEND, "2026-11-09T00:00:00Z").run();
  for (const email of [OWNER, FRIEND]) {
    const r = await req({ email });
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { brief: null });
  }
});
