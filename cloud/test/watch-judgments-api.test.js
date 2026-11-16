// 自選股關注 E 票第 8 片：網站讀得到判斷、歷史與成績。接縫：站台請求處理入口（gate.handle）＋測試用資料庫與簽章身分，
// 和預測端點、關注開關的測試相同（規格〈Testing Decisions〉接縫 2）。資料只從 migrations/0014 的只能新增的三張表讀。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const ON = { MARKET_ANA_WATCHLIST_WATCH: "owner" };
const ORIGIN = "https://market-ana.pages.dev";
let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

const line = (id, name, value) => ({ id, name, value, kind: id.split("_")[0] });
function judgment(code, base_date, edition, status, extra = {}) {
  const kind = status ? "judgment" : "miss";
  return {
    judgment_id: `${code}|${base_date}|${edition}|0.4|technical+market+news-v1-abcd1234`, code, name: `名稱${code}`,
    base_date, edition, method_version: "0.4", input_version: "technical+market+news-v1-abcd1234",
    run_mode: "trial", kind, locked_at: `${base_date}T${edition === "evening" ? "17" : "23"}:00:00+08:00`,
    layers: ["technical", "market", "news"], missing_layers: [], data_cutoffs: { stock: base_date, index: base_date },
    base_close: 110,
    ...(status ? {
      status, trigger_line: line("swing_high_20260921", "前波高點（09/21）", 122),
      wrong_line: line("swing_low_20260925", "波段低點（09/25）", 103), reason: "量縮整理等待表態", reason_news: [],
      transition: status === "可以留意",
    } : { miss_reason: "model_failed", miss_detail: "模型沒有回應" }),
    record_sha256: createHash("sha256").update(`${code}${base_date}${edition}`).digest("hex"),
    ...extra,
  };
}

async function seedJudgment(record) {
  await env.DB.prepare(
    "INSERT INTO watch_judgments (judgment_id, code, base_date, edition, method_version, input_version, run_mode, kind, status, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
  ).bind(record.judgment_id, record.code, record.base_date, record.edition, record.method_version, record.input_version,
    record.run_mode, record.kind, record.status ?? null, JSON.stringify(record), record.record_sha256, "2026-10-09T18:00:00+08:00").run();
  return record;
}

async function seedReconciliation(record, horizon, expiry_date, beat) {
  const row = { judgment_id: record.judgment_id, horizon, code: record.code, edition: record.edition, expiry_date,
    stock_return: beat ? 0.05 : 0.01, index_return: 0.02, beat_index: beat };
  await env.DB.prepare(
    "INSERT INTO watch_reconciliations (judgment_id, horizon, code, edition, expiry_date, beat_index, record_json, record_sha256, published_at) VALUES (?,?,?,?,?,?,?,?,?)",
  ).bind(record.judgment_id, horizon, record.code, record.edition, expiry_date, beat ? 1 : 0, JSON.stringify(row),
    createHash("sha256").update(`${record.judgment_id}${horizon}`).digest("hex"), "2026-10-29T18:00:00+08:00").run();
}

async function seedSummary(as_of, published_at, summary) {
  const text = JSON.stringify(summary);
  await env.DB.prepare("INSERT INTO watch_summaries (summary_sha256, as_of, summary_json, published_at) VALUES (?,?,?,?)")
    .bind(createHash("sha256").update(text).digest("hex"), as_of, text, published_at).run();
}

async function addSecurity(code) {
  await env.DB.prepare("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) VALUES ('v1', ?, ?, '上市', '普通股', ?, '1994/09/05', '半導體業', 'ESVUFR', '2026-11-13', 300, 'available', 1, 'liquid-v1')")
    .bind(code, `名稱${code}`, `TW000${code}0008`.slice(0, 12)).run();
}

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, ...ON };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-13T14:00:00Z"), rate: { market: 30, detail: 12, userData: 10_000, live: 20, history: 30, ticks: 40 } };
  for (const [email, status] of [["a@example.com", "approved"], ["pending@example.com", "pending"]]) {
    await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)")
      .bind(email, email[0].toUpperCase(), status, "2026-10-01T00:00:00Z").run();
  }
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) VALUES ('v1', '2026-11-13', 'https://example.invalid/catalog', ?, '2026-11-13T12:00:00Z', 1, 0, 'liquid-v1')")
    .bind("a".repeat(64)).run();
  for (const code of ["2330", "2317", "2454", "3231"]) await addSecurity(code);
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', 'v1', NULL, '2026-11-13T12:01:00Z')").run();
});

async function req(path, { email = OWNER, method = "GET" } = {}) {
  const headers = new Headers();
  if (email) headers.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  if (method !== "GET") {
    headers.set("content-type", "application/json");
    headers.set("origin", ORIGIN);
  }
  return handle(new Request(ORIGIN + path, { method, headers, body: method === "GET" ? undefined : "{}" }), env, next, deps);
}
const json = async (path, options) => (await req(path, options)).json();

async function watch(...codes) {
  for (const code of codes) {
    await handle(new Request(`${ORIGIN}/api/watchlist`, {
      method: "POST", body: JSON.stringify({ code, horizon: "none" }),
      headers: { "content-type": "application/json", origin: ORIGIN, "cf-access-jwt-assertion": await issuer.sign(claims(OWNER)) },
    }), env, next, deps);
    assert.equal((await req(`/api/watch/${code}`, { method: "POST" })).status, 200);
  }
}

test("清單：每一檔關注股最新一份的狀態、觸發線、過去幾次對幾次；沒有判斷或漏記的看得出來，不沿用舊的", async () => {
  await watch("2330", "2317", "2454", "3231");
  // 2330：09/30 接近 → 10/01 可以留意（晚間，轉變日，五日與二十日都對過：一勝一負）→ 10/02 晚間、晨間都是可以留意
  await seedJudgment(judgment("2330", "2026-09-30", "evening", "接近"));
  const turned = await seedJudgment(judgment("2330", "2026-10-01", "evening", "可以留意"));
  await seedReconciliation(turned, 5, "2026-10-09", true);
  await seedReconciliation(turned, 20, "2026-10-29", false);
  await seedJudgment(judgment("2330", "2026-10-02", "evening", "可以留意", { transition: false }));
  await seedJudgment(judgment("2330", "2026-10-02", "morning", "可以留意", { transition: true }));
  // 2317：最新一天漏記；前一天的「接近」不沿用
  await seedJudgment(judgment("2317", "2026-10-01", "evening", "接近"));
  await seedJudgment(judgment("2317", "2026-10-02", "evening", null));
  // 2454：只有比較舊的判斷（最新一天沒寫）→ 沒有判斷；3231：從來沒有判斷
  await seedJudgment(judgment("2454", "2026-10-01", "evening", "還沒到"));

  const body = await json("/api/watch/judgments");
  assert.equal(body.available, true);
  assert.equal(body.base_date, "2026-10-02");
  // 預設是最後寫成的那一版（同一天的晨間比晚間晚寫）
  assert.deepEqual(body.items["2330"], {
    code: "2330", base_date: "2026-10-02", edition: "morning", kind: "judgment", status: "可以留意",
    trigger_line: { id: "swing_high_20260921", name: "前波高點（09/21）", value: 122, kind: "swing" },
    // 幾次對幾次：這一版（晨間）的系列裡已對帳的二十日那一格；晨間還沒有對帳
    record: { reconciled: 0, wins: 0 },
  });
  assert.deepEqual(body.items["2317"], { code: "2317", base_date: "2026-10-02", edition: "evening", kind: "miss", status: null, trigger_line: null, record: { reconciled: 0, wins: 0 } });
  assert.equal(body.items["2454"], undefined);
  assert.equal(body.items["3231"], undefined);
});

test("清單的幾次對幾次以二十日那一格、同一版的系列為準", async () => {
  await watch("2330");
  const first = await seedJudgment(judgment("2330", "2026-09-01", "evening", "可以留意"));
  await seedJudgment(judgment("2330", "2026-09-02", "evening", "接近"));
  const second = await seedJudgment(judgment("2330", "2026-09-03", "evening", "可以留意"));
  await seedReconciliation(first, 20, "2026-09-29", true);
  await seedReconciliation(second, 20, "2026-10-01", false);
  await seedReconciliation(second, 5, "2026-09-10", true);
  await seedJudgment(judgment("2330", "2026-10-02", "evening", "還沒到"));
  assert.deepEqual((await json("/api/watch/judgments")).items["2330"].record, { reconciled: 2, wins: 1 });
});

test("只列現在正在關注的股票；帳本是空的時候什麼都不列", async () => {
  await watch("2330");
  assert.deepEqual(await json("/api/watch/judgments"), { available: true, base_date: null, items: {} });
  await seedJudgment(judgment("2317", "2026-10-02", "evening", "接近"));
  await seedJudgment(judgment("2330", "2026-10-02", "evening", "接近"));
  assert.deepEqual(Object.keys((await json("/api/watch/judgments")).items), ["2330"]);
});

test("單檔：最新一天的晚間版與晨間版（兩條線、理由、是否試跑、資料截止日、缺了哪一層）與歷史清單", async () => {
  const old = await seedJudgment(judgment("2330", "2026-10-01", "evening", "可以留意"));
  await seedReconciliation(old, 5, "2026-10-09", true);
  const evening = await seedJudgment(judgment("2330", "2026-10-02", "evening", "可以留意", { missing_layers: ["news"] }));
  const morning = await seedJudgment(judgment("2330", "2026-10-02", "morning", "接近"));
  await seedJudgment(judgment("2317", "2026-10-02", "evening", "接近"));

  const body = await json("/api/watch/judgments/2330");
  assert.equal(body.available, true);
  assert.equal(body.code, "2330");
  assert.equal(body.base_date, "2026-10-02");
  assert.equal(body.default_edition, "morning");
  assert.deepEqual(body.latest.evening, evening);
  assert.deepEqual(body.latest.morning, morning);
  assert.equal(body.latest.evening.missing_layers[0], "news");
  assert.deepEqual(body.history.map((item) => [item.base_date, item.edition, Object.keys(item.reconciliations)]),
    [["2026-10-01", "evening", ["5"]], ["2026-10-02", "evening", []], ["2026-10-02", "morning", []]]);
  assert.equal(body.history[0].reconciliations[5].beat_index, true);
});

test("單檔：其他股票已有更新的一天時，這一檔沒有那一天的判斷就不算最新（不沿用舊的）", async () => {
  await seedJudgment(judgment("2330", "2026-10-01", "evening", "接近"));
  await seedJudgment(judgment("2317", "2026-10-02", "evening", "接近"));
  const body = await json("/api/watch/judgments/2330");
  assert.equal(body.base_date, "2026-10-02");
  assert.deepEqual(body.latest, { evening: null, morning: null });
  assert.equal(body.default_edition, null);
  assert.equal(body.history.length, 1, "舊的那一份仍在歷史裡，回頭看得到");
});

test("成績：讀最新一份摘要；還沒有時是空的", async () => {
  assert.deepEqual(await json("/api/watch/summary"), { available: true, summary: null });
  await seedSummary("2026-10-08", "2026-10-08T18:00:00+08:00", { as_of: "2026-10-08", groups: [] });
  await seedSummary("2026-10-09", "2026-10-09T18:00:00+08:00", { as_of: "2026-10-09", groups: [{ scope: "all", horizon: 5 }] });
  assert.deepEqual(await json("/api/watch/summary"), { available: true, summary: { as_of: "2026-10-09", groups: [{ scope: "all", horizon: 5 }] } });
});

test("其他獲准使用者得到「沒有內容」；未登入與未獲准被擋；開關關著時站主也讀不到", async () => {
  await seedJudgment(judgment("2330", "2026-10-02", "evening", "接近"));
  await seedSummary("2026-10-09", "2026-10-09T18:00:00+08:00", { as_of: "2026-10-09", groups: [] });
  const paths = ["/api/watch/judgments", "/api/watch/judgments/2330", "/api/watch/summary"];
  for (const path of paths) {
    assert.deepEqual(await json(path, { email: "a@example.com" }), { available: false }, path);
    assert.equal((await req(path, { email: null })).status, 401, path);
    assert.equal((await req(path, { email: "pending@example.com" })).status, 403, path);
  }
  env = { ...env, MARKET_ANA_WATCHLIST_WATCH: undefined };
  for (const path of paths) assert.deepEqual(await json(path), { available: false }, path);
  env = { ...env, MARKET_ANA_WATCHLIST_WATCH: "all" };
  for (const path of paths) assert.deepEqual(await json(path), { available: false }, `只認一字不差的 owner：${path}`);
});

test("輸入有驗證：代號格式不對被拒；只能讀，不能寫", async () => {
  assert.equal((await req("/api/watch/judgments/abc!")).status, 400);
  assert.equal((await req("/api/watch/judgments/2330/extra")).status, 404);
  for (const path of ["/api/watch/judgments", "/api/watch/judgments/2330", "/api/watch/summary"]) {
    for (const method of ["POST", "PUT", "DELETE"]) {
      assert.equal((await req(path, { method })).status, 405, `${method} ${path}`);
    }
  }
});
