// 基金當成一般個股 05 票（本人 11-18）：基金在比較資料包裡是 items 的一筆、和股票同形狀（台幣、含息再投入，另帶匯率影響），
// 所以 /api/compare-0050?codes= 與 /api/stocks/<代碼>/vs-0050 照常回基金；首頁核心持有與個股頁「比較」都因此能加基金。
// 基金看基金開關（MARKET_ANA_STOCK_FUNDS 三態）：對這個人關著，就和名錄裡沒有這檔一樣（查無）。
// 退場：資料包的 funds 欄與 fund-vs-0050/v1 形狀、/api/funds/<代碼>/vs-0050。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { validateComparePack } from "../lib/build-compare.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const BASE = JSON.parse(readFileSync(new URL("./fixtures/compare-pack.json", import.meta.url), "utf8"));
const FUND = {
  code: "BLKTENAU", name: "貝萊德世界科技 A10", market: "境外基金", kind: "fund", currency: "USD", status: "ok",
  dividends_included: true, dividend_source: "TDCC 境外基金配息", listed_date: "2022-10-26",
  history: { from: "2022-12-01", to: "2026-11-12", points: 980 },
  periods: [{ period: "1y", label: "近 1 年", status: "ok", from: "2025-11-12", to: "2026-11-12", stock_return_pct: 17.9,
    benchmark_return_pct: 58.9, excess_pct: -41.0, stock_max_drawdown_pct: -22.2, benchmark_max_drawdown_pct: -20.6,
    stock_native_return_pct: 11.3, fx_effect_pct: 6.6 }],
  chart_from: "2022-12-01", chart: [["2022-12-01", 1], ["2026-11-12", 2.5]], benchmark_chart: [["2022-12-01", 1], ["2026-11-12", 3.6]],
};
const PACK = { ...BASE, items: { ...BASE.items, BLKTENAU: JSON.stringify(FUND) } };
const MARKET = { version: "v1", overview: {}, details: {} };
const FRIEND = "ok@x.com";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER, MARKET_ANA_STOCK_FUNDS: "all" };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: MARKET, comparePack: PACK, now: () => Date.parse("2026-11-17T10:00:05Z") };
  await env.DB.prepare("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, ?, ?)").bind(FRIEND, "n", "approved", "2026-11-07T00:00:00Z").run();
});

async function req(path, email = OWNER, method = "GET") {
  const h = new Headers();
  if (email) h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  return handle(new Request("https://market-ana.pages.dev" + path, { headers: h, method }), env, next, deps);
}

test("資料包驗證：基金是 items 的一筆（代碼一致），數得出幾檔基金；舊的 funds 欄不再讀", () => {
  assert.equal(validateComparePack(PACK).funds, 1);
  assert.equal(validateComparePack(BASE).funds, 0);
  assert.equal(validateComparePack({ ...BASE, funds: { BLKTENAU: "{" } }).funds, 0);
  assert.throws(() => validateComparePack({ ...BASE, items: { ...BASE.items, OTHERFUND: JSON.stringify(FUND) } }), /OTHERFUND/);
});

test("多檔比較：股票和基金一起回，基金項目原樣（含匯率影響）", async () => {
  const r = await req("/api/compare-0050?codes=2330,BLKTENAU", FRIEND);
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(body.schema, "compare-0050/v1");
  assert.deepEqual(body.items.map((item) => [item.code, item.status]), [["2330", "ok"], ["BLKTENAU", "ok"]]);
  assert.deepEqual(body.items[1], FUND);
  assert.equal(body.items[1].periods[0].fx_effect_pct, 6.6);
});

test("基金頁的比較：/api/stocks/BLKTENAU/vs-0050 回單檔形狀", async () => {
  const r = await req("/api/stocks/BLKTENAU/vs-0050", FRIEND);
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(body.schema, "vs-0050/v1");
  assert.deepEqual(body.item, FUND);
  assert.equal(body.benchmark.code, "0050");
});

test("基金開關對這個人關著：基金當作查無，股票照常", async () => {
  for (const [mode, email, visible] of [[undefined, OWNER, false], [undefined, FRIEND, false], ["owner", OWNER, true],
    ["owner", FRIEND, false], ["all", FRIEND, true]]) {
    if (mode === undefined) delete env.MARKET_ANA_STOCK_FUNDS; else env.MARKET_ANA_STOCK_FUNDS = mode;
    const multi = await (await req("/api/compare-0050?codes=2330,BLKTENAU", email)).json();
    assert.deepEqual(multi.items.map((item) => item.status), ["ok", visible ? "ok" : "not-found"], `${mode} ${email}`);
    if (!visible) assert.doesNotMatch(JSON.stringify(multi), /貝萊德|fx_effect/);
    const single = await req("/api/stocks/BLKTENAU/vs-0050", email);
    assert.equal(single.status, visible ? 200 : 404, `${mode} ${email}`);
  }
});

test("舊的基金對照路由退場：404，不再回整頁對照", async () => {
  for (const email of [OWNER, FRIEND]) {
    const r = await req("/api/funds/BLKTENAU/vs-0050", email);
    assert.equal(r.status, 404);
    assert.doesNotMatch(await r.text(), /fund-vs-0050|貝萊德/);
  }
});
