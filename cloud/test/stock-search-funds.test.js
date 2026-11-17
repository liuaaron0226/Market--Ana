// 本人 11-17「搜尋股票只能透過代號很不方便」：全站搜尋框都能打名稱；基金對照頁那檔基金（貝萊德世界科技 A10）也搜得到。
// 基金不是股票：放在回應的 funds 欄（有符合才帶），不混進 items（加自選、加入比較只讀 items）。
// 只有已經有基金頁資料的基金（compare pack 的 funds）才出現；入口和自選股頁的基金列一樣只給站主看。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const BASE = JSON.parse(readFileSync(new URL("./fixtures/compare-pack.json", import.meta.url), "utf8"));
const FUND = {
  schema: "fund-vs-0050/v1",
  fund: { code: "BLKTENAU", isin: "LU2533723974", currency: "USD", short_name: "貝萊德世界科技 A10",
          name: "貝萊德世界科技基金 A10美元(總報酬穩定配息)" },
  data_date: "2026-11-12", periods: [], chart: [], benchmark_chart: [], issues: [], notes: [],
};
const PACK = { ...BASE, funds: { BLKTENAU: JSON.stringify(FUND) } };
const FRIEND = "friend@x.com";
const VERSION = "liquid-20261109";

let issuer, env, deps;
const next = async () => new Response("static-ok", { status: 200 });
const run = (sql, ...args) => env.DB.prepare(sql).bind(...args).run();

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} }, comparePack: PACK,
    now: () => Date.parse("2026-11-17T10:00:05Z") };
  await run("INSERT INTO users (email, nickname, status, applied_at) VALUES (?, ?, 'approved', ?)", FRIEND, "n", "2026-11-07T00:00:00Z");
  await run("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) " +
    "VALUES (?, '2026-11-08', 'https://isin.twse.com.tw', ?, '2026-11-09T12:00:00Z', 1, 1)", VERSION, "a".repeat(64));
  await run("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', ?, NULL, '2026-11-09T12:00:00Z')", VERSION);
  await run("INSERT INTO listed_securities (version, code, name, market, security_type, isin, listed_date, industry, cfi_code, " +
    "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) " +
    "VALUES (?, '2330', '台積電', '上市', '普通股', 'TW0002330008', '1994/09/05', '半導體業', 'ESVUFR', '2026-11-09', 500, 'available', 1, 'liquid-v1')", VERSION);
});

async function search(q, email = OWNER, extra = "") {
  const h = new Headers();
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(email)));
  const r = await handle(new Request(`https://market-ana.pages.dev/api/stocks/search?q=${encodeURIComponent(q)}${extra}`, { headers: h }), env, next, deps);
  assert.equal(r.status, 200);
  return r.json();
}

test("站主打基金名稱的一部分、級別或代碼（不分大小寫、空白），funds 帶出這檔；items 不受影響", async () => {
  for (const q of ["貝萊德", "世界科技", "A10", "a10", "BLKTENAU", "blktenau", "世界科技A10", "總報酬"]) {
    const body = await search(q);
    assert.deepEqual(body.funds, [{ code: "BLKTENAU", name: "貝萊德世界科技 A10", full_name: "貝萊德世界科技基金 A10美元(總報酬穩定配息)" }], q);
    assert.deepEqual(body.items, [], q);
  }
});

test("打股票名稱：items 照舊、funds 空的", async () => {
  const body = await search("積電");
  assert.deepEqual(body.items.map((item) => item.code), ["2330"]);
  assert.equal(body.funds, undefined, "沒有符合的基金就不帶 funds 欄，既有契約不變");
});

test("其他獲准使用者看不到基金（和自選股頁的基金列一樣只給站主）", async () => {
  assert.equal((await search("貝萊德", FRIEND)).funds, undefined);
});

test("第二頁以後不重複帶基金；資料包沒有 funds 或壞掉時 funds 空的、搜尋照常", async () => {
  assert.equal((await search("貝萊德", OWNER, "&cursor=20")).funds, undefined);
  deps.comparePack = BASE;
  assert.equal((await search("貝萊德")).funds, undefined);
  deps.comparePack = { ...BASE, funds: { BLKTENAU: "{" } };
  assert.equal((await search("貝萊德")).funds, undefined);
  deps.comparePack = undefined;
  assert.deepEqual((await search("積電")).items.map((item) => item.code), ["2330"]);
});
