import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

let issuer, env, deps;
const next = async () => new Response("static-ok");

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER,
    MARKET_ANA_STRATEGIES_ENABLED: "true" };
  env.DB._raw.exec(readFileSync(new URL("../migrations/0006_cash_and_level.sql", import.meta.url), "utf8"));
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-10T00:00:00Z") };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, 'A', 'approved', ?)")
    .bind("a@example.com", "2026-11-01T00:00:00Z").run();
});

async function req(path, { method = "GET", body } = {}) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims("a@example.com")) });
  if (body !== undefined) {
    headers.set("content-type", "application/json");
    headers.set("origin", "https://market-ana.pages.dev");
  }
  return handle(new Request("https://market-ana.pages.dev" + path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  }), env, next, deps);
}

async function seedHolding({ price = 600 } = {}) {
  await env.DB.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, last_price, price_as_of, price_source, created_at, updated_at) VALUES (?, '2330', '台積電', '上市', 1000, 500, '', 1, 'manual', ?, '2026-11-09', '本站盤後資料', ?, ?)")
    .bind("a@example.com", price, "2026-11-09T14:00:00Z", "2026-11-09T14:00:00Z").run();
}

async function seedLevel(level = 80) {
  const payload = JSON.stringify({
    rule_version: "cash-level-v2.1", data_date: "2026-11-10", recommended_level: level,
    raw_level: level, score: { total: 4, groups: { trend: 2, volatility: 1, warning: 1, taiwan_funding: 0 }, signals: [] },
    confirmation: { direction: "down", days: 2, required: 2, target: level },
    wave: { active: false, used_tranches: [] }, lock: { remaining_days: 0 }, missing_signals: [], warning: null,
    state: { internal: "not-for-api" }, source_manifest: { files: [{ source_url: "private-source" }] },
  });
  await env.DB.prepare("INSERT INTO level_daily (data_date, version, rule_version, source_cutoff, input_sha256, payload, created_at, is_current) VALUES (?, 1, ?, ?, ?, ?, ?, 1)")
    .bind("2026-11-10", "cash-level-v2.1", "2026-11-10T00:30:00Z", "a".repeat(64), payload, "2026-11-10T00:31:00Z").run();
}

test("本人申報現金會保留版本歷史，30 天後只提示過期不改計算", async () => {
  let response = await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 400000 } });
  assert.equal(response.status, 201);
  response = await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 500000 } });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).version, 2);
  assert.equal(env.DB._raw.prepare("SELECT COUNT(*) AS n FROM user_cash_history WHERE email = ?").get("a@example.com").n, 2);

  deps.now = () => Date.parse("2026-12-11T00:00:01Z");
  const cash = await (await req("/api/holdings/cash")).json();
  assert.equal(cash.amount_twd, 500000);
  assert.equal(cash.stale, true);
  assert.match(cash.warning, /30 天/);
});

test("現金 current 不會被較舊版本的晚到寫入覆蓋", async () => {
  await env.DB.prepare("INSERT INTO user_cash_history (email,version,amount_twd,effective_at,recorded_at) VALUES (?,2,200000,?,?)")
    .bind("a@example.com", "2026-11-10T00:00:02Z", "2026-11-10T00:00:02Z").run();
  await env.DB.prepare("INSERT INTO user_cash_history (email,version,amount_twd,effective_at,recorded_at) VALUES (?,1,100000,?,?)")
    .bind("a@example.com", "2026-11-10T00:00:01Z", "2026-11-10T00:00:03Z").run();
  const current = await env.DB.prepare("SELECT amount_twd,version FROM user_cash_current WHERE email=?").bind("a@example.com").first();
  assert.deepEqual({ ...current }, { amount_twd: 200000, version: 2 });
});

test("現金只接受有限數字，拒絕 null、布林、字串與負數", async () => {
  for (const amount_twd of [null, false, true, "1000", -1]) {
    assert.equal((await req("/api/holdings/cash", { method: "POST", body: { amount_twd } })).status, 400);
  }
});

test("零現金且沒有持股時不產生零元調整建議", async () => {
  await seedLevel(80);
  await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 0 } });
  const body = await (await req("/api/holdings/level")).json();
  assert.equal(body.current.total_assets, 0);
  assert.equal(body.current.level, null);
  assert.equal(body.adjustment, null);
});

test("水位端點分開回傳建議與本人目前水位、差額及保守稅費估計", async () => {
  await seedHolding();
  await seedLevel(80);
  await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 400000 } });

  const response = await req("/api/holdings/level");
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.current.market_value, 600000);
  assert.equal(body.current.cash_twd, 400000);
  assert.equal(body.current.total_assets, 1000000);
  assert.equal(body.current.level, 60);
  assert.equal(body.advice.recommended_level, 80);
  assert.equal(body.adjustment.percentage_points, 20);
  assert.equal(body.adjustment.amount_twd, 200000);
  assert.equal(body.adjustment.action, "increase");
  assert.equal(body.adjustment.estimated_commission_twd, 285);
  assert.equal(body.adjustment.estimated_tax_twd, 0);
  assert.equal(body.adjustment.within_tolerance, false);
  assert.equal("state" in body.advice, false);
  assert.equal("source_manifest" in body.advice, false);
});

test("差距不到五個百分點顯示不用動", async () => {
  await seedHolding({ price: 780 });
  await seedLevel(80);
  await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 220000 } });
  const body = await (await req("/api/holdings/level")).json();
  assert.equal(body.current.level, 78);
  assert.equal(body.adjustment.within_tolerance, true);
  assert.equal(body.adjustment.message, "不用動");
});

test("差距恰好五個百分點也在容忍帶內", async () => {
  await seedHolding({ price: 750 });
  await seedLevel(80);
  await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 250000 } });
  const body = await (await req("/api/holdings/level")).json();
  assert.equal(body.current.level, 75);
  assert.equal(body.adjustment.percentage_points, 5);
  assert.equal(body.adjustment.within_tolerance, true);
  assert.equal(body.adjustment.message, "不用動");
});

test("庫存缺價時不把可估部分冒充完整水位", async () => {
  await seedHolding({ price: null });
  await seedLevel(80);
  await req("/api/holdings/cash", { method: "POST", body: { amount_twd: 400000 } });
  const body = await (await req("/api/holdings/level")).json();
  assert.equal(body.current.complete, false);
  assert.equal(body.current.level, null);
  assert.deepEqual(body.current.missing_price_codes, ["2330"]);
  assert.equal(body.adjustment, null);
});
