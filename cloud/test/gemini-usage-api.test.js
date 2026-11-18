// 設定頁的 Gemini 剩餘用量（本人 2026-11-17）：只有站主看得到。資料是各排程跑完後寫進 gemini_usage_snapshots 的紀錄
// （gemini_usage.py）。和其他端點同一個接縫：測試用資料庫＋簽章身分直接呼叫請求處理。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const PATH = "/api/settings/gemini-usage";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

// 2026-11-17 實測的回傳（gemini_usage.parse_usage 之後的樣子）
const READING = [
  { id: "gemini-weekly", group: "Gemini Models", window: "weekly", remaining: 0.9282, reset_time: "2026-11-22T03:36:29Z" },
  { id: "gemini-5h", group: "Gemini Models", window: "5h", remaining: 0.8861, reset_time: "2026-11-17T10:21:54Z" },
  { id: "3p-weekly", group: "Claude and GPT models", window: "weekly", remaining: 1, reset_time: "2026-11-24T08:02:09Z" },
  { id: "gemini-argon-wtu-weekly", group: "Gemini 4 Argon", window: "weekly", remaining: 0.9884, reset_time: "2026-11-24T07:56:20Z" },
];

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  // 現在：台灣 11-17 16:30（UTC 08:30）
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-17T08:30:00Z") };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)")
    .bind("a@example.com", "A", "2026-11-09T00:00:00Z").run();
});

const snap = (task, runId, takenAt, buckets, model = null) => env.DB
  .prepare("INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, model, buckets_json) VALUES (?, ?, ?, ?, ?)")
  .bind(task, runId, takenAt, model, buckets === null ? null : JSON.stringify(buckets)).run();

async function get(email = OWNER) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  return handle(new Request(ORIGIN + PATH, { headers }), env, next, deps);
}

test("站主看到 Flash 那組的 5 小時與本週、Argon 的本週，和讀到的時間", async () => {
  await snap("watch-ledger", "1", "2026-11-17T08:05:00Z", READING);
  const res = await get();
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "private, no-store");
  const body = await res.json();
  assert.equal(body.available, true);
  assert.equal(body.read_at, "2026-11-17T08:05:00Z");
  assert.deepEqual(body.flash, {
    five_hour: { remaining: 0.8861, reset_time: "2026-11-17T10:21:54Z" },
    weekly: { remaining: 0.9282, reset_time: "2026-11-22T03:36:29Z" },
  });
  assert.deepEqual(body.argon, { weekly: { remaining: 0.9884, reset_time: "2026-11-24T07:56:20Z" } });
});

test("最新一筆讀不到用量時，顯示上一筆讀得到的，並說最後一次跑是什麼時候", async () => {
  await snap("ai-digest", "1", "2026-11-17T06:00:00Z", READING);
  await snap("watch-ledger", "2", "2026-11-17T08:05:00Z", null);
  const body = await (await get()).json();
  assert.equal(body.read_at, "2026-11-17T06:00:00Z");
  assert.equal(body.last_run_at, "2026-11-17T08:05:00Z");
  assert.equal(body.flash.five_hour.remaining, 0.8861);
});

test("今天各任務：台灣時間今天跑了幾次、最近一次用的模型；昨天的不算", async () => {
  await snap("watch-ledger", "1", "2026-11-16T15:59:00Z", READING, "gemini-4-argon-high"); // 台灣 11-16 23:59 → 昨天
  await snap("watch-ledger", "2", "2026-11-16T16:00:00Z", READING, "gemini-3.8-flash-high"); // 台灣 11-17 00:00 → 今天
  await snap("watch-ledger", "3", "2026-11-17T08:05:00Z", READING, "gemini-3.8-flash-medium");
  await snap("forecast-ledger", "4", "2026-11-17T07:00:00Z", READING);
  const body = await (await get()).json();
  assert.deepEqual(body.today, [
    { task: "forecast-ledger", runs: 1, model: null, effort: null, failed: 0, dry_runs: 0 },
    { task: "watch-ledger", runs: 2, model: "gemini-3.8-flash-medium", effort: null, failed: 0, dry_runs: 0 },
  ]);
});

test("還沒有任何紀錄：可以用，但沒有數字", async () => {
  const body = await (await get()).json();
  assert.deepEqual(body, { available: true, read_at: null, last_run_at: null, flash: null, argon: null, today: [] });
});

test("其他使用者拿不到站主的用量", async () => {
  await snap("watch-ledger", "1", "2026-11-17T08:05:00Z", READING);
  const res = await get("a@example.com");
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { available: false });
});

test("只能讀", async () => {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(OWNER)), origin: ORIGIN, "content-type": "application/json" });
  const res = await handle(new Request(ORIGIN + PATH, { method: "POST", headers, body: "{}" }), env, next, deps);
  assert.equal(res.status, 405);
});
// 本人 11-19：首頁「籌碼與資金」白話（任務 daily-update）呼叫 Gemini 也記進用量，多記思考程度、成功或失敗、乾跑（遷移 0029）
const plainRun = (runId, takenAt, outcome, dryRun, effort = "medium") => env.DB
  .prepare("INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, model, buckets_json, effort, outcome, dry_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
  .bind("daily-update", runId, takenAt, "gemini-4-argon", JSON.stringify(READING), effort, outcome, dryRun).run();

test("白話分析：今天的次數不含乾跑，另外給失敗幾次、乾跑幾次與思考程度", async () => {
  await plainRun("10", "2026-11-17T02:00:00Z", "ok", 0);
  await plainRun("11", "2026-11-17T03:00:00Z", "failed", 0);
  await plainRun("12", "2026-11-17T04:00:00Z", "ok", 1);
  await plainRun("13", "2026-11-16T10:00:00Z", "failed", 0);   // 台灣 11-16 18:00 → 昨天，不算
  const body = await (await get()).json();
  assert.deepEqual(body.today, [
    { task: "daily-update", runs: 2, model: "gemini-4-argon", effort: "medium", failed: 1, dry_runs: 1 },
  ]);
});

test("今天只有乾跑：照樣列出來，正式次數是 0", async () => {
  await plainRun("12", "2026-11-17T04:00:00Z", "ok", 1);
  const body = await (await get()).json();
  assert.deepEqual(body.today, [{ task: "daily-update", runs: 0, model: "gemini-4-argon", effort: "medium", failed: 0, dry_runs: 1 }]);
});

test("遷移 0029 還沒套用到資料庫：照舊列次數與模型，不會讀不到", async () => {
  const { MIGRATION_FILES } = await import("./helpers.js");
  env.DB = fakeD1(MIGRATION_FILES.filter((f) => f !== "0029_gemini_usage_outcome.sql"));
  await snap("watch-ledger", "1", "2026-11-17T08:05:00Z", READING, "gemini-4-argon");
  const res = await get();
  assert.equal(res.status, 200);
  assert.deepEqual((await res.json()).today, [{ task: "watch-ledger", runs: 1, model: "gemini-4-argon", effort: null, failed: 0, dry_runs: 0 }]);
});
