// 預測帳本 B 票第 3 片：網站讀得到。接縫：站台請求處理入口（gate.handle）＋測試用資料庫與簽章身分，
// 與個人資料、畫線端點測試相同（規格〈Testing Decisions〉接縫 2）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { handle } from "../lib/gate.js";
import { releasePolicy, strategyWorkflowPolicy } from "../lib/release-policy.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });
const ORIGIN = "https://market-ana.pages.dev";
const ON = { MARKET_ANA_FORECAST_LEDGER: "owner" };

const cell = (strong, weak, zone) => ({
  strong_line: strong, weak_line: weak, zone,
  reason: "站回均線之上偏強【資料：五日線 97.6 點】", invalidation: "跌破前低就代表看錯",
});
const forecast = (base_date, edition, extra = {}) => ({
  forecast_id: `${base_date}|${edition}|0.2|base-v1-abcd1234`, base_date, edition,
  method_version: "0.2", input_version: "base-v1-abcd1234", run_mode: "trial", kind: "forecast",
  regime: "多頭中的回檔", locked_at: `${base_date}T17:00:00+08:00`,
  data_cutoffs: { index: base_date, institutional: base_date, futures: base_date },
  cells: {
    1: cell({ id: "ma5", name: "五日線", value: 97.6 }, { id: "prev_low", name: "前一交易日低點", value: 95 }, "above"),
    5: cell({ id: "ma20", name: "月線", value: 99.4 }, { id: "low20", name: "近二十日低點（09/29）", value: 93 }, "between"),
    20: cell({ id: "high20", name: "近二十日高點（09/22）", value: 108 }, { id: "low20", name: "近二十日低點（09/29）", value: 93 }, "below"),
  },
  record_sha256: "f".repeat(64),
  ...extra,
});

async function seedRecord(record) {
  await env.DB.prepare(
    "INSERT INTO forecast_records (forecast_id, base_date, edition, method_version, input_version, run_mode, kind, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ).bind(record.forecast_id, record.base_date, record.edition, record.method_version, record.input_version,
    record.run_mode, record.kind, JSON.stringify(record), record.record_sha256, "2026-10-08T18:00:00+08:00").run();
}

async function seedReconciliation(forecastId, horizon, expiry_date, expiry_close, actual_zone) {
  const row = { forecast_id: forecastId, horizon, expiry_date, expiry_close, actual_zone };
  await env.DB.prepare(
    "INSERT INTO forecast_reconciliations (forecast_id, horizon, expiry_date, actual_zone, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?)",
  ).bind(forecastId, horizon, expiry_date, actual_zone, JSON.stringify(row), "e".repeat(64), "2026-10-08T18:00:00+08:00").run();
}

async function seedSummary(as_of, published_at, summary) {
  const text = JSON.stringify(summary);
  await env.DB.prepare("INSERT INTO forecast_summaries (summary_sha256, as_of, summary_json, published_at) VALUES (?,?,?,?)")
    .bind(createHash("sha256").update(text).digest("hex"), as_of, text, published_at).run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, ...ON };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-10-08T10:00:00Z") };
  for (const [email, status] of [["a@example.com", "approved"], ["pending@example.com", "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
      .bind(email, email[0].toUpperCase(), status, "2026-10-01T00:00:00Z").run();
  }
  await seedRecord(forecast("2026-09-29", "evening"));
  await seedRecord(forecast("2026-09-30", "evening"));
  await seedRecord(forecast("2026-09-30", "morning", { run_mode: "trial", regime: "盤整" }));
  await seedRecord({ forecast_id: "2026-10-01|evening|0.2|base-v1-abcd1234", base_date: "2026-10-01", edition: "evening",
    method_version: "0.2", input_version: "base-v1-abcd1234", run_mode: "trial", kind: "miss",
    miss_reason: "model_failed", record_sha256: "d".repeat(64) });
  await seedReconciliation("2026-09-30|evening|0.2|base-v1-abcd1234", 1, "2026-10-01", 97.6, "above");
  await seedReconciliation("2026-09-30|evening|0.2|base-v1-abcd1234", 5, "2026-10-08", 93, "between");
  await seedReconciliation("2026-09-29|evening|0.2|base-v1-abcd1234", 1, "2026-09-30", 97, "between");
  await seedSummary("2026-10-07", "2026-10-07T18:00:00+08:00", { as_of: "2026-10-07", groups: [] });
  await seedSummary("2026-10-08", "2026-10-08T18:00:00+08:00", { as_of: "2026-10-08", groups: [{ horizon: 1, verdict: "累積中" }] });
});

async function req(path, { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (method !== "GET") headers.set("origin", ORIGIN);
  return handle(new Request(ORIGIN + path, { method, headers }), env, next, deps);
}
const body = async (path, options) => (await req(path, options)).json();

test("站主讀得到最新基準日的晚間版與晨間版，每一格兩條線與落點、理由、看錯條件都在", async () => {
  const response = await req("/api/forecast/latest");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("cache-control"), /private/);
  assert.match(response.headers.get("cache-control"), /no-store/);
  const latest = await response.json();
  // 最新基準日是 10/01，但那天只有一筆漏記；漏記也照實回傳，畫面自己決定怎麼呈現
  assert.equal(latest.available, true);
  assert.equal(latest.base_date, "2026-10-01");
  assert.equal(latest.editions.evening.kind, "miss");
  assert.equal(latest.editions.evening.miss_reason, "model_failed");
  assert.equal(latest.editions.morning, null);
});

test("站主讀到的最新預測帶著線的名字與數值、落點、盤勢類型、試跑與資料截止日", async () => {
  // 另起一個只有 09/30 兩版預測的資料庫（帳本只能新增，不拿掉漏記，改成不放）
  env = { ...env, DB: fakeD1() };
  await seedRecord(forecast("2026-09-30", "evening"));
  await seedRecord(forecast("2026-09-30", "morning", { run_mode: "trial", regime: "盤整" }));
  const latest = await body("/api/forecast/latest");
  assert.equal(latest.base_date, "2026-09-30");
  const evening = latest.editions.evening;
  assert.deepEqual(evening.cells["1"].strong_line, { id: "ma5", name: "五日線", value: 97.6 });
  assert.deepEqual(evening.cells["1"].weak_line, { id: "prev_low", name: "前一交易日低點", value: 95 });
  assert.equal(evening.cells["20"].zone, "below");
  assert.equal(evening.cells["1"].reason, "站回均線之上偏強【資料：五日線 97.6 點】");
  assert.equal(evening.cells["1"].invalidation, "跌破前低就代表看錯");
  assert.equal(evening.regime, "多頭中的回檔");
  assert.equal(evening.run_mode, "trial");
  assert.deepEqual(evening.data_cutoffs, { index: "2026-09-30", institutional: "2026-09-30", futures: "2026-09-30" });
  assert.equal(latest.editions.morning.regime, "盤整");
});

test("換模型那天同一版有新舊模型各一份時，最新預測取最後鎖定的那一份（本人 11-17 預測換 Argon）", async () => {
  env = { ...env, DB: fakeD1() };
  // 先放 Argon、後放 Flash：不靠寫入先後，靠鎖定時間
  await seedRecord(forecast("2026-09-30", "evening", { forecast_id: "2026-09-30|evening|0.2|v-argon", input_version: "v-argon",
    model: "gemini-4-argon", regime: "盤整", locked_at: "2026-09-30T18:10:00+08:00" }));
  await seedRecord(forecast("2026-09-30", "evening", { forecast_id: "2026-09-30|evening|0.2|v-flash", input_version: "v-flash",
    model: "gemini-3.8-flash", locked_at: "2026-09-30T17:00:00+08:00" }));
  const latest = await body("/api/forecast/latest");
  assert.equal(latest.editions.evening.model, "gemini-4-argon");
  assert.equal(latest.editions.evening.regime, "盤整");
});

test("站主讀得到最新一份對帳摘要", async () => {
  const summary = await body("/api/forecast/summary");
  assert.deepEqual(summary, { available: true, summary: { as_of: "2026-10-08", groups: [{ horizon: 1, verdict: "累積中" }] } });
});

async function seedReplay(as_of, summary) {
  const text = JSON.stringify(summary);
  await env.DB.prepare("INSERT INTO forecast_replay_summaries (summary_sha256, as_of, summary_json, published_at) VALUES (?,?,?,?)")
    .bind(createHash("sha256").update(text).digest("hex"), as_of, text, "2026-11-17T12:00:00+08:00").run();
  const record = forecast("2026-10-30", "evening", { forecast_id: "replay|2026-10-30|evening|0.4|x", run_mode: "replay" });
  await env.DB.prepare(
    "INSERT INTO forecast_replay_records (forecast_id, base_date, edition, method_version, input_version, run_mode, kind, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ).bind(record.forecast_id, record.base_date, record.edition, record.method_version, record.input_version,
    record.run_mode, record.kind, JSON.stringify(record), record.record_sha256, "2026-11-17T12:00:00+08:00").run();
}

test("歷史重播（參考）的成績另外一個入口；還沒重播時是空的", async () => {
  assert.deepEqual(await body("/api/forecast/replay"), { available: true, summary: null });
  const replay = { as_of: "2026-11-13", groups: [{ run_mode: "replay", horizon: 1, verdict: "歷史重播（參考）" }] };
  await seedReplay("2026-11-13", replay);
  assert.deepEqual(await body("/api/forecast/replay"), { available: true, summary: replay });
});

test("重播紀錄不會混進正式的最新預測、對帳摘要與回看", async () => {
  await seedReplay("2026-11-13", { as_of: "2026-11-13", groups: [{ run_mode: "replay" }] });
  assert.equal((await body("/api/forecast/latest")).base_date, "2026-10-01");
  assert.equal((await body("/api/forecast/summary")).summary.as_of, "2026-10-08");
  const history = await body("/api/forecast/history?from=2026-10-01&to=2026-11-13");
  assert.deepEqual(history.items.map(item => item.forecast_id), ["2026-10-01|evening|0.2|base-v1-abcd1234"]);
});

test("歷史清單依日期範圍回傳過去的預測與各格對帳結果", async () => {
  const history = await body("/api/forecast/history?from=2026-09-30&to=2026-10-01");
  assert.equal(history.available, true);
  assert.deepEqual(history.items.map(item => [item.base_date, item.edition, item.kind]), [
    ["2026-09-30", "evening", "forecast"], ["2026-09-30", "morning", "forecast"], ["2026-10-01", "evening", "miss"],
  ]);
  assert.deepEqual(history.items[0].reconciliations, {
    1: { forecast_id: "2026-09-30|evening|0.2|base-v1-abcd1234", horizon: 1, expiry_date: "2026-10-01", expiry_close: 97.6, actual_zone: "above" },
    5: { forecast_id: "2026-09-30|evening|0.2|base-v1-abcd1234", horizon: 5, expiry_date: "2026-10-08", expiry_close: 93, actual_zone: "between" },
  });
  assert.deepEqual(history.items[1].reconciliations, {});
  const earlier = await body("/api/forecast/history?from=2026-09-01&to=2026-09-29");
  assert.deepEqual(earlier.items.map(item => item.base_date), ["2026-09-29"]);
});

test("歷史清單的日期輸入有驗證", async () => {
  for (const query of ["", "?from=2026-09-30", "?to=2026-09-30", "?from=2026/09/30&to=2026-10-01",
    "?from=2026-02-30&to=2026-03-01", "?from=2026-10-02&to=2026-10-01", "?from=2025-01-01&to=2026-10-01",
    "?from=2026-09-30&to=2026-10-01x"]) {
    const response = await req(`/api/forecast/history${query}`);
    assert.equal(response.status, 400, query);
  }
  assert.equal((await req("/api/forecast/history?from=2025-10-01&to=2026-10-01")).status, 200);
});

test("其他獲准的使用者得到「沒有內容」而不是錯誤，三個入口都一樣", async () => {
  for (const path of ["/api/forecast/latest", "/api/forecast/summary", "/api/forecast/history?from=2026-09-01&to=2026-10-01", "/api/forecast/replay"]) {
    const response = await req(path, { email: "a@example.com" });
    assert.equal(response.status, 200, path);
    assert.deepEqual(await response.json(), { available: false }, path);
  }
});

test("未登入被擋、未獲准被擋", async () => {
  assert.equal((await req("/api/forecast/latest", { email: null })).status, 401);
  assert.equal((await req("/api/forecast/latest", { email: "pending@example.com" })).status, 403);
  assert.equal((await req("/api/forecast/latest", { email: "stranger@example.com" })).status, 403);
});

test("功能開關關著時站主也讀不到", async () => {
  for (const value of [undefined, "", "1", "true", "TRUE", "public", "all", " owner "]) {
    env = { ...env, MARKET_ANA_FORECAST_LEDGER: value };
    for (const path of ["/api/forecast/latest", "/api/forecast/summary", "/api/forecast/history?from=2026-09-01&to=2026-10-01", "/api/forecast/replay"]) {
      assert.deepEqual(await body(path), { available: false }, `${value} ${path}`);
    }
  }
});

test("只接受讀取", async () => {
  for (const method of ["POST", "PUT", "DELETE"]) {
    assert.equal((await req("/api/forecast/latest", { method })).status, 405, method);
  }
});

test("正式環境設定（本人 11-15）：預測帳本只對站主打開，其他策略、AI 與總開關維持原狀", async () => {
  const { readFileSync } = await import("node:fs");
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  const vars = JSON.parse(text).vars;
  assert.equal(vars.MARKET_ANA_FORECAST_LEDGER, "owner");
  const policy = releasePolicy(vars);
  assert.equal(policy.features.forecast_ledger_v1, true);
  assert.equal(policy.strategies_enabled, false);
  for (const feature of ["strategy_v1", "level_advice", "ai_digest", "ai_stock_advice"]) {
    assert.equal(policy.features[feature], false, feature);
  }
});

test("預測開關是獨立的：打開它不會連帶打開其他策略或 AI 功能，策略總開關也不會打開它", () => {
  const on = releasePolicy(ON);
  assert.equal(on.features.forecast_ledger_v1, true);
  assert.equal(on.strategies_enabled, false);
  for (const feature of ["strategy_v1", "market_trend_v1", "level_advice", "ai_digest", "ai_stock_advice"]) {
    assert.equal(on.features[feature], false, feature);
  }
  for (const event of ["schedule", "workflow_run"]) {
    assert.deepEqual(strategyWorkflowPolicy({ ...ON, EVENT_NAME: event }), { allowed: false, dry_run: true });
  }
  assert.equal(releasePolicy({ MARKET_ANA_STRATEGIES_ENABLED: "true" }).features.forecast_ledger_v1, false);
  assert.equal(releasePolicy({ MARKET_ANA_ENABLED_FEATURES: "forecast_ledger_v1,market_trend_v1" }).features.forecast_ledger_v1, false);
  assert.equal(releasePolicy({}).features.forecast_ledger_v1, false);
});
