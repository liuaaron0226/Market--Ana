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

const snap = (task, runId, takenAt, calls, buckets, model = null) => env.DB
  .prepare("INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, calls, model, buckets_json) VALUES (?, ?, ?, ?, ?, ?)")
  .bind(task, runId, takenAt, calls, model, buckets === null ? null : JSON.stringify(buckets)).run();

async function get(email = OWNER) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  return handle(new Request(ORIGIN + PATH, { headers }), env, next, deps);
}

test("站主看到 Flash 那組的 5 小時與本週、Argon 的本週，和讀到的時間", async () => {
  await snap("watch-ledger", "1", "2026-11-17T08:05:00Z", 38, READING);
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
  await snap("ai-digest", "1", "2026-11-17T06:00:00Z", 1, READING);
  await snap("watch-ledger", "2", "2026-11-17T08:05:00Z", 38, null);
  const body = await (await get()).json();
  assert.equal(body.read_at, "2026-11-17T06:00:00Z");
  assert.equal(body.last_run_at, "2026-11-17T08:05:00Z");
  assert.equal(body.flash.five_hour.remaining, 0.8861);
});

test("今天各任務：台灣時間今天跑了幾次、呼叫加總、最近一次用的模型；昨天的不算", async () => {
  await snap("watch-ledger", "1", "2026-11-16T15:59:00Z", 40, READING, "gemini-4-argon-high"); // 台灣 11-16 23:59 → 昨天
  await snap("watch-ledger", "2", "2026-11-16T16:00:00Z", 30, READING, "gemini-3.8-flash-high"); // 台灣 11-17 00:00 → 今天
  await snap("watch-ledger", "3", "2026-11-17T08:05:00Z", 8, READING, "gemini-3.8-flash-medium");
  await snap("forecast-ledger", "4", "2026-11-17T07:00:00Z", null, READING);
  const body = await (await get()).json();
  assert.deepEqual(body.today, [
    { task: "forecast-ledger", runs: 1, calls: null, model: null },
    { task: "watch-ledger", runs: 2, calls: 38, model: "gemini-3.8-flash-medium" },
  ]);
});

test("還沒有任何紀錄：可以用，但沒有數字", async () => {
  const body = await (await get()).json();
  assert.deepEqual(body, { available: true, read_at: null, last_run_at: null, flash: null, argon: null, today: [] });
});

test("其他使用者拿不到站主的用量", async () => {
  await snap("watch-ledger", "1", "2026-11-17T08:05:00Z", 38, READING);
  const res = await get("a@example.com");
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { available: false });
});

test("只能讀", async () => {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(OWNER)), origin: ORIGIN, "content-type": "application/json" });
  const res = await handle(new Request(ORIGIN + PATH, { method: "POST", headers, body: "{}" }), env, next, deps);
  assert.equal(res.status, 405);
});
