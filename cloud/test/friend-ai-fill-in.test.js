// 朋友 AI 解讀補跑（2026-11-19）：11-16 開啟以來每一班都壞在讀金鑰之後、呼叫 Gemini 之前，朋友一列都沒有、頁面只寫「未產生」。
// 1. 同一天後面的時段只替還沒有結果的朋友補跑（friend-ai-schedule.mjs 的 pendingKeysSql）。
// 2. 排程中途壞掉時替還沒有結果的朋友寫下原因（failedRunSql）。
// 3. 金鑰可用但還沒有任何一份時，朋友首頁拿到 pending，寫「還沒有產生」。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { handle } from "../lib/gate.js";
import { CONSENT_VERSION } from "../lib/friend-ai.js";
import { RETRY_GAP_MINUTES, failedRunSql, pendingKeysSql, usableKeysSql } from "../friend-ai-schedule.mjs";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ORIGIN = "https://market-ana.pages.dev";
const SCRIPT = fileURLToPath(new URL("../friend-ai-schedule.mjs", import.meta.url));
const DAY = "2026-11-19";
const [NONE, BUSY_OLD, BUSY_NEW, DONE, QUOTA, PAUSED, YESTERDAY] =
  ["none", "busy-old", "busy-new", "done", "quota", "paused", "yesterday"].map((name) => `${name}@example.com`);
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

// 排程寫的時間格式（friend_ai_digest.py：UTC、到秒、+00:00）。
const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString().replace(/\.\d{3}Z$/, "+00:00");

async function addFriend(email, { status = "active", failure = null } = {}) {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', 't')").bind(email, email).run();
  env.DB._raw.prepare("INSERT INTO friend_gemini_keys (email,ciphertext,iv,alg,master_key_id,consent_at,consent_version,status,last_success_at,last_failure_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    .run(email, "c", "i", "AES-256-GCM", "x", "t", CONSENT_VERSION, status, null, failure, "t", "t");
}

function addDay(email, date, { status = "generated", error = null, at = minutesAgo(120) } = {}) {
  for (const type of ["market", "flow"]) {
    env.DB._raw.prepare("INSERT INTO friend_ai_daily (email,data_date,digest_type,content_json,input_sha256,model,status,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)")
      .run(email, date, type, status === "generated" ? JSON.stringify({ conclusion: "結論", points: [], signals: [] }) : null,
        "d".repeat(64), "gemini-3.8-flash", status, error, at, at);
  }
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_FRIEND_AI: "on" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-19T12:00:00Z"),
    rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40, friendAi: 10_000 } };
});

const picked = (sql) => env.DB._raw.prepare(sql).all().map((row) => row.email);
const reasonOf = (email) => env.DB._raw.prepare("SELECT last_failure_reason FROM friend_gemini_keys WHERE email = ?").get(email).last_failure_reason;

async function digest(email) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(email)) });
  return (await handle(new Request(`${ORIGIN}/api/friend-ai/digest`, { headers }), env, next, deps)).json();
}

test("補跑只挑那一天還沒有結果、或暫時連不到 Google 已經過一陣子的朋友", async () => {
  for (const email of [NONE, BUSY_OLD, BUSY_NEW, DONE, QUOTA, YESTERDAY]) await addFriend(email);
  await addFriend(PAUSED, { status: "paused" });
  addDay(BUSY_OLD, DAY, { status: "unproduced", error: "gemini_unavailable", at: minutesAgo(RETRY_GAP_MINUTES + 5) });
  addDay(BUSY_NEW, DAY, { status: "unproduced", error: "gemini_unavailable", at: minutesAgo(RETRY_GAP_MINUTES - 5) });
  addDay(DONE, DAY);
  addDay(QUOTA, DAY, { status: "unproduced", error: "quota" });
  addDay(YESTERDAY, "2026-11-18");   // 昨天有，今天還沒有
  assert.deepEqual(picked(pendingKeysSql(DAY)), [BUSY_OLD, NONE, YESTERDAY].sort());
  // 發布那一班照舊：所有可用金鑰的朋友。
  assert.deepEqual(picked(usableKeysSql()), [BUSY_NEW, BUSY_OLD, DONE, NONE, QUOTA, YESTERDAY].sort());
  // 補跑拿到的欄位和平常一樣（排程用同一段程式解密）。
  assert.deepEqual(Object.keys(env.DB._raw.prepare(pendingKeysSql(DAY)).get()), Object.keys(env.DB._raw.prepare(usableKeysSql()).get()));
});

test("補跑的日期只收 YYYY-MM-DD，別的字串不會被放進查詢", () => {
  for (const bad of ["", "2026-11-19' OR 1=1 --", "20261119", undefined]) assert.throws(() => pendingKeysSql(bad), /YYYY-MM-DD/);
  assert.throws(() => failedRunSql("x'; DROP TABLE users; --"), /YYYY-MM-DD/);
  assert.equal(execFileSync("node", [SCRIPT, "keys-sql", "--pending", DAY], { encoding: "utf8" }), pendingKeysSql(DAY));
  assert.equal(execFileSync("node", [SCRIPT, "keys-sql"], { encoding: "utf8" }), usableKeysSql());
  const refused = spawnSync("node", [SCRIPT, "keys-sql", "--pending", "2026-11-19' --"], { encoding: "utf8" });
  assert.equal(refused.status, 2);
  assert.equal(refused.stdout, "");
});

test("排程中途壞掉：還沒有結果的朋友拿到原因，已經有當天結果的朋友不動", async () => {
  for (const email of [NONE, DONE]) await addFriend(email);
  await addFriend(QUOTA, { failure: "今天的免費額度用完了，明天會照常再試" });
  await addFriend(PAUSED, { status: "paused", failure: "金鑰無效" });
  addDay(DONE, DAY);
  addDay(QUOTA, DAY, { status: "unproduced", error: "quota" });
  env.DB._raw.exec(failedRunSql(DAY, new Date("2026-11-19T10:21:00Z")));
  assert.match(reasonOf(NONE), /排程今天中途出錯.*稍後的班次會再試/);
  assert.equal(reasonOf(DONE), null);
  assert.equal(reasonOf(QUOTA), "今天的免費額度用完了，明天會照常再試");
  assert.equal(reasonOf(PAUSED), "金鑰無效");
  assert.equal(env.DB._raw.prepare("SELECT updated_at FROM friend_gemini_keys WHERE email = ?").get(NONE).updated_at, "2026-11-19T10:21:00+00:00");
  // 朋友的頁面：未產生＋這個原因；稍後補跑要挑得到他。
  const body = await digest(NONE);
  assert.match(body.notice.text, /排程今天中途出錯/);
  assert.deepEqual(picked(pendingKeysSql(DAY)), [NONE]);
});

test("還沒決定資料日就壞掉：寫給所有可用金鑰的朋友", async () => {
  for (const email of [NONE, DONE]) await addFriend(email);
  await addFriend(PAUSED, { status: "paused", failure: "金鑰無效" });
  env.DB._raw.exec(failedRunSql(""));
  assert.match(reasonOf(NONE), /排程今天中途出錯/);
  assert.match(reasonOf(DONE), /排程今天中途出錯/);
  assert.equal(reasonOf(PAUSED), "金鑰無效");
});

test("金鑰可用但還沒有任何一份：首頁拿到 pending；有結果、有失敗原因或金鑰暫停就不是", async () => {
  for (const email of [NONE, DONE, QUOTA]) await addFriend(email);
  await addFriend(PAUSED, { status: "paused" });
  let body = await digest(NONE);
  assert.equal(body.pending, true);       // 整張表還是空的（2026-11-19 正式站的樣子）
  assert.equal(body.data_date, null);
  assert.equal(body.notice, null);
  addDay(DONE, DAY);
  addDay(QUOTA, DAY, { status: "unproduced", error: "quota" });
  body = await digest(NONE);
  assert.equal(body.pending, true);       // 別人已經有今天的，他還沒有
  assert.equal(body.data_date, DAY);
  assert.equal((await digest(DONE)).pending, false);
  assert.equal((await digest(QUOTA)).pending, false);
  assert.equal((await digest(PAUSED)).pending, false);
});
