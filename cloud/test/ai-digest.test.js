import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { onRequest } from "../functions/api/ai-digest.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, OWNER } from "./helpers.js";

let issuer, env;

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, OWNER_EMAIL: OWNER,
    MARKET_ANA_STRATEGIES_ENABLED: "true" };
  env.DB._raw.exec(readFileSync(new URL("../migrations/0007_ai_daily.sql", import.meta.url), "utf8"));
  const rows = [
    ["market", "market", { stance: "觀望", conclusion: "觀望：量價仍待確認。", points: [{ text: "指數收在 23280。", codes: [] }] }],
    ["flow", "market", { signals: [{ text: "2330 外資買超 12000 張。", codes: ["2330"] }] }],
    ["stock", "*", { count: 1 }],
    ["stock", "2330", { code: "2330", name: "台積電", status: "收盤 1000。", action: "續抱", reason: "仍在支撐 980 之上。" }],
  ];
  for (const [type, subject, content] of rows) {
    await env.DB.prepare("INSERT INTO ai_daily (data_date, digest_type, subject, content_json, input_sha256, model, input_tokens, output_tokens, total_tokens, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?, ?)")
      .bind("2026-11-10", type, subject, JSON.stringify(content), "a".repeat(64), "test-model", 100, 20, 120, "2026-11-10T09:00:00Z", "2026-11-10T09:00:00Z").run();
  }
});

async function request(email, query = "") {
  const token = await issuer.sign(claims(email));
  return onRequest({
    request: new Request(`https://market-ana.pages.dev/api/ai-digest${query}`, { headers: { "cf-access-jwt-assertion": token } }),
    env,
    fetch: fetchJwks(issuer.jwks),
  });
}

test("核准使用者可讀首頁大盤與籌碼，但看不到本人個股判斷", async () => {
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', ?)")
    .bind("a@example.com", "2026-11-10T00:00:00Z").run();
  const response = await request("a@example.com");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  const body = await response.json();
  assert.equal(body.data_date, "2026-11-10");
  assert.equal(body.market.content.stance, "觀望");
  assert.equal(body.flow.content.signals[0].codes[0], "2330");
  assert.equal(body.stocks, undefined);
});

test("只有 OWNER_EMAIL 可取得自選股與庫存逐檔判斷", async () => {
  const response = await request(OWNER, "?scope=portfolio&code=2330");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.stocks.status, "generated");
  assert.deepEqual(body.stocks.items.map(item => item.code), ["2330"]);
  assert.equal(body.stocks.items[0].status, "收盤 1000。");
  assert.equal(body.stocks.items[0].generation_status, "generated");

  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', ?)")
    .bind("a@example.com", "2026-11-10T00:00:00Z").run();
  assert.equal((await request("a@example.com", "?scope=portfolio")).status, 403);
});

test("今天未產生時如實回傳未產生，不沿用前一天冒充", async () => {
  await env.DB.prepare("INSERT INTO ai_daily (data_date, digest_type, subject, content_json, input_sha256, model, status, error_code, created_at, updated_at) VALUES (?, 'market', 'market', NULL, ?, ?, 'unproduced', 'agy_failed', ?, ?)")
    .bind("2026-11-11", "b".repeat(64), "test-model", "2026-11-11T09:00:00Z", "2026-11-11T09:00:00Z").run();
  const body = await (await request(OWNER)).json();
  assert.equal(body.data_date, "2026-11-11");
  assert.equal(body.market.status, "unproduced");
  assert.equal(body.market.content, null);
});

test("核心版預設在查 ai_daily 前關閉 AI 摘要，query 不能自行啟用", async () => {
  delete env.MARKET_ANA_STRATEGIES_ENABLED;
  env.DB = { prepare() { throw new Error("ai schema must not be queried"); } };
  const response = await request(OWNER, "?scope=portfolio&MARKET_ANA_STRATEGIES_ENABLED=1");
  assert.equal(response.status, 403);
  const body = await response.json();
  assert.equal(body.error, "feature-disabled");
  assert.equal(body.features.ai_stock_advice, false);
});

test("端點拒絕未登入、非 GET 與不合法代號", async () => {
  assert.equal((await onRequest({ request: new Request("https://market-ana.pages.dev/api/ai-digest"), env, fetch: fetchJwks(issuer.jwks) })).status, 401);
  const token = await issuer.sign(claims(OWNER));
  assert.equal((await onRequest({ request: new Request("https://market-ana.pages.dev/api/ai-digest", { method: "POST", headers: { "cf-access-jwt-assertion": token } }), env, fetch: fetchJwks(issuer.jwks) })).status, 405);
  assert.equal((await request(OWNER, "?scope=portfolio&code=2330%27%20OR%201=1")).status, 400);
});

test("portfolio AI 映射保留資料日、generated 與 unproduced，不把舊內容補成今天", () => {
  const source = readFileSync(new URL("../../web/portfolio.js", import.meta.url), "utf8");
  const window = {};
  vm.runInNewContext(source, { window, console });
  const mapped = window.PortfolioAi.normalize({
    data_date: "2026-11-11",
    stocks: { status: "partial", items: [
      { code: "2330", generation_status: "generated", status: "量價仍穩定。", action: "續抱", reason: "收盤仍在支撐之上。" },
      { code: "0050", generation_status: "unproduced", content: null },
    ] },
  });
  assert.equal(mapped.dataDate, "2026-11-11");
  assert.equal(mapped.byCode.get("2330").text, "量價仍穩定。 · 續抱：收盤仍在支撐之上。");
  assert.equal(mapped.byCode.get("0050").text, "今日未產生");
  assert.equal(window.PortfolioAi.entryFor(mapped, "9999").text, "今日未產生");
});
