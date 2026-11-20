// 盯項：首頁頁首「在盯 N 件」（本人 2026-11-20「接下來要看的條件由系統盯」，.scratch/impact-watch 票 03）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { releasePolicy, policyForViewer } from "../lib/release-policy.js";
import { fakeD1, rosterOnly, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const NOW = Date.parse("2026-11-20T07:30:00Z");   // 台北 11-20 15:30
const FRIEND = "a@example.com";
const PENDING = "p@example.com";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

async function add(db, id, over = {}) {
  const row = {
    source_block: "market_news", source_date: "2026-11-18", source_ref: "2026-11-18-close#n1", subject: "market", kind: "numeric",
    condition_key: id, params: "{}", label: `條件 ${id}`, note: null, impact: 2, prepare: JSON.stringify(["如果成立，就先檢查持股比重"]),
    created_date: "2026-11-18", expires_on: "2026-12-16", days_left: 18, status: "watching", result_date: null, result_note: null,
    show_until: null, visibility: "all", ...over,
  };
  const names = Object.keys(row);
  await db.prepare(`INSERT INTO impact_watch_items (id, ${names.join(", ")}, created_at) VALUES (?, ${names.map(() => "?").join(", ")}, ?)`)
    .bind(id, ...names.map((name) => row[name]), "2026-11-18T06:05:00Z").run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_IMPACT_WATCH: "all" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => NOW, rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  for (const [email, status] of [[FRIEND, "approved"], [PENDING, "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(email, "A", status, "2026-11-09T00:00:00Z").run();
  }
  await add(env.DB, "below_ma20", { label: "加權指數收盤跌破月線", note: "外資續賣時容易先破月線", impact: 3, days_left: 18 });
  await add(env.DB, "foreign_turn_buy", { label: "外資由賣超轉為買超", impact: 2, created_date: "2026-11-19", days_left: 19, prepare: null });
  await add(env.DB, "above_high", { label: "加權指數收盤突破近 20 日高點（49,967）", impact: 2, created_date: "2026-11-17", days_left: 17 });
  await add(env.DB, "foreign_sell_streak", { label: "外資連續賣超達 9 天", impact: 3, status: "met", result_date: "2026-11-19",
    result_note: "成立：外資連續賣超 9 天，11/19 賣超 912 億。", show_until: "2026-11-24", days_left: null });
  await add(env.DB, "below_low", { label: "跌破近 20 日低點", status: "failed", result_date: "2026-11-13", result_note: "舊的結果", show_until: "2026-11-18", days_left: null });
  await add(env.DB, "above_ma60", { label: "站回季線", status: "expired", result_date: "2026-11-19", days_left: null });
  await add(env.DB, "stock_2330", { subject: "2330", label: "台積電收盤站上月線", visibility: "owner", impact: 1, days_left: 6 });
});

async function req(path = "/api/impact-watch", { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request(ORIGIN + path, { headers, method }), env, next, deps);
}
const labels = (body) => body.items.map((item) => item.label);

test("首頁直白分析挑的條件：出處寫「直白分析」（票 07）", async () => {
  await add(env.DB, "above_ma20", { source_block: "plain_daily", source_ref: "20261118-plain-ai", label: "加權指數收盤站回月線", impact: 2 });
  const body = await (await req()).json();
  assert.deepEqual(body.items.find((item) => item.id === "above_ma20").source, { block: "直白分析", date: "2026-11-18" });
});

test("盯著的與剛有結果的都回：有結果的在前，盯著的依影響力、再依新舊", async () => {
  const r = await req();
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("cache-control"), "private, no-store");
  const body = await r.json();
  assert.equal(body.available, true);
  assert.deepEqual(labels(body), ["外資連續賣超達 9 天", "加權指數收盤跌破月線", "外資由賣超轉為買超", "加權指數收盤突破近 20 日高點（49,967）", "台積電收盤站上月線"]);
  assert.deepEqual(body.items[0], {
    id: "foreign_sell_streak", subject: "market", kind: "numeric", label: "外資連續賣超達 9 天", impact: 3, status: "met",
    prepare: ["如果成立，就先檢查持股比重"], source: { block: "消息", date: "2026-11-18" },
    result: { date: "2026-11-19", note: "成立：外資連續賣超 9 天，11/19 賣超 912 億。" },
  });
  assert.deepEqual(body.items[1], {
    id: "below_ma20", subject: "market", kind: "numeric", label: "加權指數收盤跌破月線", impact: 3, status: "watching",
    note: "外資續賣時容易先破月線", prepare: ["如果成立，就先檢查持股比重"], source: { block: "消息", date: "2026-11-18" }, days_left: 18,
  });
  assert.deepEqual(body.items[2].prepare, [], "沒寫準備的給空清單");
});

test("首頁留到結果日後第 3 個交易日：過了那一天就不回；到期安靜收掉的從不回", async () => {
  const text = await (await req()).text();
  assert.ok(!text.includes("舊的結果") && !text.includes("站回季線"));
  deps.now = () => Date.parse("2026-11-24T15:00:00Z");   // 台北 11-24 23:00：還留著
  assert.ok(labels(await (await req()).json()).includes("外資連續賣超達 9 天"));
  deps.now = () => Date.parse("2026-11-24T16:30:00Z");   // 台北 11-25 00:30：收掉
  assert.ok(!labels(await (await req()).json()).includes("外資連續賣超達 9 天"));
});

test("朋友在 all 時看得到大盤的盯項，看不到只給站主的", async () => {
  const body = await (await req("/api/impact-watch", { email: FRIEND })).json();
  assert.equal(body.available, true);
  assert.equal(body.items.length, 4);
  assert.ok(body.items.every((item) => item.subject === "market"));
  assert.ok(!JSON.stringify(body).includes("台積電"));
});

test("開關只開站主：站主看得到，朋友拿到「沒有這個功能」而且不碰任何資料表", async () => {
  env.MARKET_ANA_IMPACT_WATCH = "owner";
  assert.equal((await (await req()).json()).items.length, 5);
  env.DB = rosterOnly(env.DB);
  const r = await req("/api/impact-watch", { email: FRIEND });
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { available: false });
});

test("開關關著（沒設、寫錯）：站主也拿到「沒有這個功能」", async () => {
  for (const value of [undefined, "off", "on", "true", "ALL"]) {
    env.MARKET_ANA_IMPACT_WATCH = value;
    assert.deepEqual(await (await req()).json(), { available: false }, String(value));
  }
});

test("功能清單：站主看開關有沒有開；朋友只有 all 才是開的", () => {
  for (const [value, owner, friend] of [[undefined, false, false], ["off", false, false], ["owner", true, false], ["all", true, true]]) {
    const policy = releasePolicy({ MARKET_ANA_IMPACT_WATCH: value });
    assert.equal(policy.features.impact_watch_v1, owner, `站主 ${value}`);
    assert.equal(policyForViewer(policy, false, { MARKET_ANA_IMPACT_WATCH: value }).features.impact_watch_v1, friend, `朋友 ${value}`);
  }
  assert.equal(policyForViewer(releasePolicy({ MARKET_ANA_IMPACT_WATCH: "all" }), false).features.impact_watch_v1, false, "env 沒給時對朋友是關");
});

test("還沒核准的人、沒登入的人一律拿不到", async () => {
  const pending = await req("/api/impact-watch", { email: PENDING });
  assert.equal(pending.status, 403);
  assert.ok(!(await pending.text()).includes("月線"));
  const anonymous = await req("/api/impact-watch", { email: null });
  assert.ok([401, 403].includes(anonymous.status), String(anonymous.status));
});

test("只能讀：其他方法不允許", async () => {
  assert.equal((await req("/api/impact-watch", { method: "POST" })).status, 405);
});

test("0034 還沒套：當成沒有盯項，不是錯誤", async () => {
  env.DB = fakeD1(MIGRATION_FILES.filter((file) => !file.startsWith("0034")));
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', ?)").bind(FRIEND, "2026-11-09T00:00:00Z").run();
  for (const email of [OWNER, FRIEND]) {
    const r = await req("/api/impact-watch", { email });
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { available: true, items: [] });
  }
});

test("沒有盯項：空清單", async () => {
  await env.DB.prepare("DELETE FROM impact_watch_items").run();
  assert.deepEqual(await (await req()).json(), { available: true, items: [] });
});

// ---- 站主自選股與庫存的盯項（票 06）：只有站主拿得到 ----
const STOCK = {
  subject: "2454", visibility: "owner", label: "收盤跌破季線", note: "OWNER-NOTE 法說後賣壓還沒消化",
  params: JSON.stringify({ name: "聯發科", ma: 60, as_of: "2026-11-18" }), prepare: JSON.stringify(["如果跌破季線，就先確認 OWNER-PREPARE"]),
};

test("個股盯項：站主拿得到，帶股票名稱；條件的參數不往外送", async () => {
  await add(env.DB, "stock_watch", STOCK);
  await add(env.DB, "stock_done", { ...STOCK, subject: "3008", params: JSON.stringify({ name: "大立光", level: 2500 }), label: "收盤站上壓力（2,500）",
    status: "met", result_date: "2026-11-20", result_note: "成立：11/20 收 2,530，站上壓力 2,500。", show_until: "2026-11-25", days_left: null });
  const body = await (await req()).json();
  const watching = body.items.find((item) => item.id === "stock_watch");
  assert.deepEqual(watching, {
    id: "stock_watch", subject: "2454", subject_name: "聯發科", kind: "numeric", label: "收盤跌破季線", impact: 2, status: "watching",
    note: STOCK.note, prepare: ["如果跌破季線，就先確認 OWNER-PREPARE"], source: { block: "消息", date: "2026-11-18" }, days_left: 18,
  });
  assert.equal(body.items[0].id, "stock_done", "有結果的排最前面");
  assert.equal(body.items[0].subject_name, "大立光");
  assert.ok(!JSON.stringify(body).includes("as_of") && !("params" in watching));
  assert.equal(body.items.find((item) => item.id === "stock_2330").subject_name, undefined, "舊資料沒記名稱：只有代號");
});

test("個股盯項：朋友、還沒核准的人、沒登入的人拿不到任何一筆，也拿不到相關的文字", async () => {
  await add(env.DB, "stock_watch", STOCK);
  await add(env.DB, "stock_done", { ...STOCK, subject: "3008", params: JSON.stringify({ name: "大立光" }), status: "failed", result_date: "2026-11-20",
    result_note: "否定：OWNER-RESULT", show_until: "2026-11-25", days_left: null });
  // 就算排程寫錯、把個股盯項標成所有人可見，也不回給站主以外的人
  await add(env.DB, "stock_mislabelled", { ...STOCK, subject: "2317", visibility: "all", params: JSON.stringify({ name: "鴻海" }) });
  for (const email of [FRIEND, PENDING, null]) {
    for (const path of ["/api/impact-watch", "/api/impact-watch?code=2454", "/api/impact-watch?subject=2454"]) {
      const text = await (await req(path, { email })).text();
      for (const secret of ["2454", "3008", "2317", "聯發科", "大立光", "鴻海", "OWNER-", "季線"]) {
        assert.ok(!text.includes(secret), `${email} ${path} 不該出現 ${secret}`);
      }
    }
  }
  const friend = await (await req("/api/impact-watch", { email: FRIEND })).json();
  assert.equal(friend.items.length, 4, "朋友照舊只有大盤的");
  assert.equal((await (await req()).json()).items.length, 8, "站主全部拿得到");
});
