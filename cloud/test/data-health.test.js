// 資料更新狀態（2026-11-24）：判斷（data-health.js）、設定頁入口、推播提醒（data-health-alert.js）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { RULES, evaluateHealth, readHealthFacts } from "../lib/data-health.js";
import { HOLIDAYS, tradingDaysBefore } from "../lib/trading-calendar.js";
import { DAILY_PUSH_CAP, healthNotice, runDataHealthAlert } from "../lib/data-health-alert.js";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES } from "./helpers.js";
import { makeVapid, makeDevice, pushRecorder } from "./push-helpers.js";

const at = (taipei) => Date.parse(`${taipei}+08:00`);   // 台北時間 → ms
const TUE_2200 = at("2026-11-24T22:00:00");
const only = (items, key) => items.find((item) => item.key === key);

// ---------- 判斷：純函式 ----------
const CASES = [
  { name: "每日行情當天有更新：正常", now: TUE_2200, facts: { daily: { last_ok_at: "2026-11-24T10:12:00Z", data_date: "2026-11-24" } },
    key: "daily", state: "ok", since: null },
  { name: "還沒過期限（21:30）：前一天的資料照樣算正常", now: at("2026-11-24T21:00:00"),
    facts: { daily: { last_ok_at: "2026-11-23T10:12:00Z", data_date: "2026-11-23" } }, key: "daily", state: "ok", since: null },
  { name: "過了期限還是前一天的：延遲，從期限那一刻算", now: TUE_2200,
    facts: { daily: { last_ok_at: "2026-11-23T10:12:00Z", data_date: "2026-11-23" } }, key: "daily", state: "late",
    since: "2026-11-24T13:30:00.000Z", detail: "應於 11-24 21:30 前更新" },
  { name: "連兩個交易日沒更新：停止更新，從第一次沒更新那班算", now: TUE_2200,
    facts: { daily: { last_ok_at: "2026-11-20T10:12:00Z", data_date: "2026-11-20" } }, key: "daily", state: "failed",
    since: "2026-11-23T13:30:00.000Z", detail: "11-23 21:30 起未更新" },
  { name: "週末：週五的資料就是最新的，不提醒", now: at("2026-11-29T10:00:00"),
    facts: { daily: { last_ok_at: "2026-11-27T10:12:00Z", data_date: "2026-11-27" } }, key: "daily", state: "ok", since: null },
  { name: "國定休市（12-25 行憲紀念日）：前一個交易日的資料就是最新的，不提醒", now: at("2026-12-25T23:00:00"),
    facts: { daily: { last_ok_at: "2026-12-24T10:12:00Z", data_date: "2026-12-24" } }, key: "daily", state: "ok", since: null },
  { name: "休市後第一個交易日過了期限還沒更新：延遲", now: at("2026-12-28T22:00:00"),
    facts: { daily: { last_ok_at: "2026-12-24T10:12:00Z", data_date: "2026-12-24" } }, key: "daily", state: "late",
    since: "2026-12-28T13:30:00.000Z" },
  { name: "盤勢解讀有更新但改用程式套句：部分異常，從那一班算", now: TUE_2200,
    facts: { market_brief: { last_ok_at: "2026-11-24T10:40:00Z", degraded: "改用程式套句", degraded_at: "2026-11-24T10:40:00Z" } },
    key: "market_brief", state: "degraded", since: "2026-11-24T10:40:00.000Z", detail: "改用程式套句" },
  { name: "沒更新比內容降級優先：首頁消息收盤後那班沒來", now: at("2026-11-24T16:00:00"),
    facts: { market_news: { last_ok_at: "2026-11-24T03:40:00Z", degraded: "模型未完成分類，只有標題", degraded_at: "2026-11-24T03:40:00Z" } },
    key: "market_news", state: "late", since: "2026-11-24T07:15:00.000Z" },
  { name: "大盤預測晨間版記成漏記：部分異常", now: at("2026-11-24T09:00:00"),
    facts: { forecast: { last_ok_at: "2026-11-23T23:30:00Z", degraded: "記為漏記", degraded_at: "2026-11-23T23:30:00Z" } },
    key: "forecast", state: "degraded", detail: "記為漏記" },
  { name: "基金淨值本來就晚幾天：往前 4 個交易日內的淨值日都正常", now: TUE_2200,
    facts: { fund_nav: { last_ok_at: "2026-11-18", data_date: "2026-11-18" } }, key: "fund_nav", state: "ok" },
  { name: "基金淨值再舊一天：延遲", now: TUE_2200,
    facts: { fund_nav: { last_ok_at: "2026-11-17", data_date: "2026-11-17" } }, key: "fund_nav", state: "late" },
  { name: "籌碼卡解讀不看時間，只看最後一班有沒有改用程式套句", now: TUE_2200,
    facts: { plain: { last_ok_at: "2026-11-20T10:00:00Z", degraded: "改用程式套句", degraded_at: "2026-11-24T10:05:00Z" } },
    key: "plain", state: "degraded", since: "2026-11-24T10:05:00.000Z" },
];

for (const c of CASES) {
  test(`判斷：${c.name}`, () => {
    const item = only(evaluateHealth(c.facts, c.now), c.key);
    assert.equal(item.state, c.state);
    if ("since" in c) assert.equal(item.since, c.since);
    if ("detail" in c) assert.equal(item.detail, c.detail);
    if (c.state === "ok") assert.equal(item.detail, "");
  });
}

test("判斷：讀不到的那一份（表不存在、功能沒開）不出現；名稱、順序照規則表", () => {
  const items = evaluateHealth({ daily: { last_ok_at: "2026-11-24T10:12:00Z", data_date: "2026-11-24" },
    level: { last_ok_at: "2026-11-23T23:35:00Z", data_date: "2026-11-23" } }, TUE_2200);
  assert.deepEqual(items.map((item) => [item.key, item.label]), [["daily", "每日行情"], ["level", "現金水位"]]);
  assert.deepEqual(evaluateHealth({}, TUE_2200), []);
  for (const rule of RULES) assert.ok(rule.key && rule.label && rule.fresh, rule.key);
});

test("交易日曆：2026 年和 Python 那邊的官方種子同一份（開始、最後交易日不算休市）", () => {
  const seed = JSON.parse(readFileSync(new URL("../../seeds/twse-holiday-schedule.json", import.meta.url), "utf8"));
  const closed = seed.filter((row) => !/開始交易|最後交易/.test(row.Name))
    .map((row) => `${Number(row.Date.slice(0, -4)) + 1911}-${row.Date.slice(-4, -2)}-${row.Date.slice(-2)}`);
  assert.deepEqual([...HOLIDAYS].filter((day) => day.startsWith("2026")).sort(), closed.sort());
  assert.equal(tradingDaysBefore("2026-02-23", 1), "2026-02-11");   // 春節後第一天的前一個交易日
});

// ---------- 讀資料庫 ----------
const SHA = "a".repeat(64);
async function publish(db, version, sourceDate, updatedAt) {
  await db.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) "
    + "VALUES (?, ?, 'x', ?, ?, 1, 1)").bind(version, sourceDate, SHA, updatedAt).run();
  await db.prepare("INSERT INTO stock_data_state (key, active_version, updated_at) VALUES ('current', ?, ?) "
    + "ON CONFLICT (key) DO UPDATE SET active_version = excluded.active_version, updated_at = excluded.updated_at").bind(version, updatedAt).run();
}

test("讀資料庫：每日行情、盤勢解讀、白話分析的用量紀錄；空的表不出現", async () => {
  const db = fakeD1();
  await publish(db, "v1", "2026-11-24", "2026-11-24T10:12:00Z");
  await db.prepare("INSERT INTO market_brief_runs (trade_date, slot, generated_at, source, payload) VALUES ('2026-11-24', 'evening', '2026-11-24T10:40:00Z', 'template', '{}')").run();
  await db.prepare("INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, outcome, dry_run) VALUES ('daily-update', '1', '2026-11-23T10:05:00Z', 'ok', 0), "
    + "('daily-update', '2', '2026-11-24T10:05:00Z', 'failed', 0), ('daily-update', '3', '2026-11-24T11:00:00Z', 'ok', 1)").run();
  const facts = await readHealthFacts(db);
  assert.deepEqual(Object.keys(facts).sort(), ["daily", "market_brief", "plain"]);
  assert.deepEqual(facts.daily, { last_ok_at: "2026-11-24T10:12:00Z", data_date: "2026-11-24" });
  assert.equal(facts.market_brief.degraded, "改用程式套句");
  assert.deepEqual(facts.plain, { last_ok_at: "2026-11-23T10:05:00Z", degraded: "改用程式套句", degraded_at: "2026-11-24T10:05:00Z" }, "乾跑不算");
});

test("讀資料庫：遷移還沒套（沒有那張表、沒有那個欄位）不是錯，那一份不出現", async () => {
  const db = fakeD1(MIGRATION_FILES.filter((f) => !["0035_market_brief.sql", "0029_gemini_usage_outcome.sql", "0036_broker_branches.sql"].includes(f)));
  await publish(db, "v1", "2026-11-24", "2026-11-24T10:12:00Z");
  const facts = await readHealthFacts(db);
  assert.deepEqual(Object.keys(facts), ["daily"]);
});

test("讀資料庫：主力分點、國外行情部分抓取失敗；預測最後一筆是漏記；基金看最舊的那一檔", async () => {
  const db = fakeD1();
  await publish(db, "v1", "2026-11-24", "2026-11-24T10:12:00Z");
  await db.prepare("INSERT INTO broker_branch_stocks (code, updated_through, last_attempt_at, last_error) VALUES "
    + "('2330', '2026-11-24', '2026-11-24T11:00:00Z', NULL), ('1802', '2026-11-21', '2026-11-24T11:01:00Z', 'format-changed')").run();
  await db.prepare("INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at, problem) VALUES "
    + "('NVDA', 'massive', '2026-11-24T11:00:00Z', 1, '2026-11-24T11:00:00Z', NULL), ('TSM', 'eodhd', '2026-11-24T11:02:00Z', 0, '2026-11-20T11:00:00Z', 'quota')").run();
  await db.prepare("INSERT INTO forecast_records (forecast_id, base_date, edition, method_version, input_version, run_mode, kind, record_json, record_sha256, published_at) "
    + "VALUES ('f1', '2026-11-24', 'morning', 'm', 'i', 'official', 'miss', '{}', ?, '2026-11-24T00:50:00Z')").bind(SHA).run();
  for (const [code, date] of [["BLKTENAU", "2026-11-21"], ["GLIACU", "2026-11-19"]]) {
    await db.prepare("INSERT INTO fund_securities (version, code, name, full_name, currency, latest_bar_date, available_bars, analysis_status) "
      + "VALUES ('v1', ?, 'n', 'n', 'USD', ?, 10, 'available')").bind(code, date).run();
  }
  const facts = await readHealthFacts(db);
  assert.deepEqual(facts.broker_branches, { last_ok_at: "2026-11-24", data_date: "2026-11-24", degraded: "1 檔抓取失敗", degraded_at: "2026-11-24T11:01:00Z" });
  assert.equal(facts.overseas.degraded, "1 檔抓取失敗");
  assert.equal(facts.forecast.degraded, "記為漏記");
  assert.equal(facts.fund_nav.data_date, "2026-11-19");
});

// ---------- 設定頁入口 ----------
const ORIGIN = "https://market-ana.pages.dev";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });
async function get(email = OWNER, method = "GET") {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)), origin: ORIGIN });
  return handle(new Request(ORIGIN + "/api/settings/data-health", { method, headers }), env, next, deps);
}

beforeEach(async () => {
  issuer = await makeIssuer();
  const vapid = await makeVapid();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_DATA_HEALTH: "owner",
    VAPID_PUBLIC_KEY: vapid.publicKey, VAPID_PRIVATE_KEY: vapid.privateKey };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => TUE_2200 };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES ('a@example.com', 'A', 'approved', '2026-11-09T00:00:00Z')").run();
});

test("入口：站主即時讀到每一份的狀態（不是建置時寫死的）", async () => {
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  const res = await get();
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "private, no-store");
  const body = await res.json();
  assert.equal(body.available, true);
  assert.deepEqual(body.items, [{ key: "daily", label: "每日行情", last_ok_at: "2026-11-23T10:12:00Z", state: "late",
    since: "2026-11-24T13:30:00.000Z", detail: "應於 11-24 21:30 前更新" }]);
  await publish(env.DB, "v2", "2026-11-24", "2026-11-24T14:05:00Z");
  assert.equal((await (await get()).json()).items[0].state, "ok");
});

test("入口：朋友拿不到；開關關著時站主也拿不到；只能讀", async () => {
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  assert.deepEqual(await (await get("a@example.com")).json(), { available: false });
  env.MARKET_ANA_DATA_HEALTH = "all";
  assert.deepEqual(await (await get("a@example.com")).json(), { available: false }, "all 也只開站主");
  for (const off of [undefined, "off", "on", "Owner"]) {
    env.MARKET_ANA_DATA_HEALTH = off;
    assert.deepEqual(await (await get()).json(), { available: false }, String(off));
  }
  env.MARKET_ANA_DATA_HEALTH = "owner";
  assert.equal((await get(OWNER, "POST")).status, 405);
});

// ---------- 推播提醒 ----------
const PHONE = "https://web.push.apple.com/QGuQyavXutnMei";
let pushes, phone, status;
async function device(endpoint, email = OWNER) {
  const made = await makeDevice(endpoint);
  await env.DB.prepare("INSERT INTO push_subscriptions (endpoint, email, p256dh, auth, label, created_at) VALUES (?, ?, ?, ?, 'x', '2026-11-16T00:00:00Z')")
    .bind(endpoint, email, made.subscription.keys.p256dh, made.subscription.keys.auth).run();
  return made;
}
const alert = (now) => runDataHealthAlert(env, { now: () => now, pushFetch: pushes.fetch });

async function setupPush() {
  status = {};
  pushes = pushRecorder((endpoint) => status[endpoint] || 201);
  phone = await device(PHONE);
}

test("提醒：出問題送一則（名稱與從什麼時候起），問題還在不再送；恢復送一則，之後不再送", async () => {
  await setupPush();
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  assert.deepEqual(await alert(TUE_2200), { problems: 1, notified: 1, recovered: 0 });
  assert.deepEqual(phone.decrypt(pushes.sent[0].body), {
    title: "資料更新異常：每日行情", body: "每日行情：11-24 21:30 起延遲", url: "/minimal.html#data-health", tag: "data-health-start" });
  assert.deepEqual(await alert(at("2026-11-24T23:00:00")), { problems: 1, notified: 0, recovered: 0 });
  assert.equal(pushes.sent.length, 1, "同一個問題只送一次");
  await publish(env.DB, "v2", "2026-11-24", "2026-11-24T15:20:00Z");
  assert.deepEqual(await alert(at("2026-11-25T08:00:00")), { problems: 0, notified: 0, recovered: 1 });
  assert.deepEqual(phone.decrypt(pushes.sent[1].body), {
    title: "資料已恢復：每日行情", body: "每日行情：已恢復，最後更新 11-24 23:20", url: "/minimal.html#data-health", tag: "data-health-end" });
  await alert(at("2026-11-25T11:30:00"));
  assert.equal(pushes.sent.length, 2, "恢復也只送一次");
  // 隔天又出問題：重新開一筆，再送一次
  assert.deepEqual(await alert(at("2026-11-25T22:00:00")), { problems: 1, notified: 1, recovered: 0 });
  assert.equal(pushes.sent.length, 3);
});

test("提醒：兩次排程同時跑，只有一邊送得出去", async () => {
  await setupPush();
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  const [a, b] = await Promise.all([alert(TUE_2200), alert(TUE_2200)]);
  assert.equal(a.notified + b.notified, 1);
  assert.equal(pushes.sent.length, 1);
});

test("提醒：沒有一台收到就放掉認領，下一個時段再送；推播金鑰或裝置都沒有時不認領", async () => {
  await setupPush();
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  status[PHONE] = 500;
  assert.deepEqual(await alert(TUE_2200), { problems: 1, notified: 0, recovered: 0, retry: 1 });
  status[PHONE] = 201;
  assert.equal((await alert(at("2026-11-24T23:00:00"))).notified, 1);
  const noKeys = await runDataHealthAlert({ ...env, VAPID_PRIVATE_KEY: "" }, { now: () => TUE_2200, pushFetch: pushes.fetch });
  assert.equal(noKeys.notified, 0);
});

test("提醒：同一輪新出的幾個問題合成一則；朋友的裝置不送", async () => {
  await setupPush();
  const friend = await device("https://fcm.googleapis.com/fcm/send/friend", "a@example.com");
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  await env.DB.prepare("INSERT INTO market_brief_runs (trade_date, slot, generated_at, source, payload) VALUES ('2026-11-24', 'evening', '2026-11-24T10:40:00Z', 'template', '{}')").run();
  assert.equal((await alert(TUE_2200)).notified, 2);
  assert.equal(pushes.sent.length, 1);
  assert.deepEqual(phone.decrypt(pushes.sent[0].body).body.split("\n"), ["每日行情：11-24 21:30 起延遲", "盤勢解讀：11-24 18:40 起改用程式套句"]);
  assert.equal(phone.decrypt(pushes.sent[0].body).title, "資料更新異常：2 項");
  assert.ok(friend);
});

test(`提醒：每天最多送 ${DAILY_PUSH_CAP} 則，超過的留到隔天（那時還有問題才送）`, async () => {
  await setupPush();
  for (let i = 0; i < DAILY_PUSH_CAP; i += 1) {
    await env.DB.prepare("INSERT INTO data_health_alerts (key, state, opened_at, start_sent_at) VALUES (?, 'late', ?, ?)")
      .bind(`old-${i}`, "2026-11-24T01:00:00Z", `2026-11-24T0${i + 1}:00:00.000Z`).run();
  }
  await publish(env.DB, "v1", "2026-11-23", "2026-11-23T10:12:00Z");
  const capped = await alert(TUE_2200);
  assert.equal(capped.notified, 0);
  assert.equal(capped.capped, true);
  assert.equal(pushes.sent.length, 0);
  assert.equal((await alert(at("2026-11-25T08:00:00"))).notified, 1, "隔天問題還在就送");
});

test("提醒：休市日不提醒；開關關著、0040 還沒套都什麼都不做", async () => {
  await setupPush();
  await publish(env.DB, "v1", "2026-12-24", "2026-12-24T10:12:00Z");
  assert.deepEqual(await alert(at("2026-12-25T23:00:00")), { problems: 0, notified: 0, recovered: 0 });
  assert.equal(pushes.sent.length, 0);
  await publish(env.DB, "v0", "2026-12-23", "2026-12-23T10:12:00Z");
  env.MARKET_ANA_DATA_HEALTH = "off";
  assert.deepEqual(await alert(at("2026-12-28T23:00:00")), { skipped: "off" });
  env.MARKET_ANA_DATA_HEALTH = "owner";
  env.DB = fakeD1(MIGRATION_FILES.filter((f) => f !== "0040_data_health.sql"));
  await publish(env.DB, "v0", "2026-12-23", "2026-12-23T10:12:00Z");
  assert.deepEqual(await alert(at("2026-12-28T23:00:00")), { skipped: "no_table", problems: 1 });
});

test("推播字句：停止更新、部分抓取失敗、只有資料日的恢復", () => {
  assert.equal(healthNotice("start", [{ label: "每日行情", state: "failed", since: "2026-11-23T13:30:00.000Z" }]).body, "每日行情：11-23 21:30 起停止更新");
  assert.equal(healthNotice("start", [{ label: "國外行情", state: "degraded", since: "2026-11-24T11:02:00Z", detail: "1 檔抓取失敗" }]).body,
    "國外行情：11-24 19:02 起 1 檔抓取失敗");
  assert.equal(healthNotice("end", [{ label: "基金淨值", last_ok_at: "2026-11-21" }]).body, "基金淨值：已恢復，最後更新 11-21");
});
