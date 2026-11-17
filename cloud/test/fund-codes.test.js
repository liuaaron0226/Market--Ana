// 基金當成一般個股 01 票（本人 2026-11-18）：全站代號放寬到 4～12 碼，基金代碼 BLKTENAU（8 碼）走得過所有吃股票代號的地方；
// 名錄還沒有基金之前，這些路由對它回「查無」而不是「代號格式不正確」。4～6 碼股票的行為不變（其他測試涵蓋）。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER } from "./helpers.js";

const MIGRATIONS = new URL("../migrations/", import.meta.url);
const migrationsUpTo = (last) => readdirSync(MIGRATIONS).filter((f) => /^\d{4}_.*\.sql$/.test(f) && f <= last).sort();
const exec = (db, files) => files.forEach((f) => db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8")));
const fundMigration = () => readdirSync(MIGRATIONS).find((f) => /_fund_codes\.sql$/.test(f));

test("遷移：使用者的自選、庫存、搜尋紀錄、畫線原樣保留；之後收 8 碼代碼與市場「境外基金」，太短太長照樣擋", () => {
  const migration = fundMigration();
  assert.ok(migration, "要有一個 *_fund_codes.sql 遷移");
  const db = new DatabaseSync(":memory:");
  exec(db, migrationsUpTo("0024_zzz"));
  const at = "2026-11-18T00:00:00Z";
  db.prepare("INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching, watch_since, group_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)")
    .run("a@x.com", "2330", "台積電", "上市", "long", 1, at, at, 1, at, "半導體");
  db.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, note, in_universe, source, created_at, updated_at, last_price) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    .run("a@x.com", "2330", "台積電", "上市", 1000, 980, "核心", 1, "manual", at, at, 1100);
  db.prepare("INSERT INTO user_search_history (email, code, name, market, viewed_at) VALUES (?,?,?,?,?)").run("a@x.com", "2330", "台積電", "上市", at);
  db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, text, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)")
    .run("a@x.com", "2330", "drawing-1", "hline", "2026-11-17", 1000, "", at, at);
  const tables = ["user_watchlist", "user_holdings", "user_search_history", "user_drawings"];
  const before = Object.fromEntries(tables.map((t) => [t, db.prepare(`SELECT * FROM ${t}`).all()]));
  const schemaObjects = () => db.prepare(`SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('index','trigger') AND tbl_name IN (${tables.map(() => "?").join(",")}) ORDER BY name`).all(...tables);
  const objectsBefore = schemaObjects();

  exec(db, [migration]);

  for (const t of tables) assert.deepEqual(db.prepare(`SELECT * FROM ${t}`).all(), before[t], t);
  db.prepare("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at) VALUES (?,?,?,?,?,?,?)")
    .run("a@x.com", "BLKTENAU", "貝萊德世界科技 A10", "境外基金", 1, at, at);
  db.prepare("INSERT INTO user_holdings (email, code, name, market, shares, average_cost, in_universe, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)")
    .run("a@x.com", "BLKTENAU", "貝萊德世界科技 A10", "境外基金", 123.4567, 545.2, 1, at, at);
  db.prepare("INSERT INTO user_search_history (email, code, name, market, viewed_at) VALUES (?,?,?,?,?)").run("a@x.com", "BLKTENAU", "貝萊德世界科技 A10", "境外基金", at);
  db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, text, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)")
    .run("a@x.com", "BLKTENAU", "drawing-2", "hline", "2026-11-17", 17.1, "", at, at);
  assert.equal(Number(db.prepare("SELECT shares FROM user_holdings WHERE code = 'BLKTENAU'").get().shares), 123.4567);
  const insert = (code, market) => () => db.prepare("INSERT INTO user_watchlist (email, code, name, market, in_universe, created_at, updated_at) VALUES (?,?,?,?,?,?,?)")
    .run("b@x.com", code, "x", market, 1, at, at);
  assert.throws(insert("ABC", "上市"), /CHECK/);
  assert.throws(insert("ABCDEFGHIJKLM", "境外基金"), /CHECK/);
  assert.throws(insert("1234", "美股"), /CHECK/);
  // 上限觸發器、索引（含主鍵）和遷移前一模一樣
  assert.deepEqual(schemaObjects(), objectsBefore);
  assert.ok(objectsBefore.some((o) => o.name === "user_watchlist_watch_limit"));
});

let issuer, env, deps, misCalls;
const next = async () => new Response("static-ok", { status: 200 });
beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  misCalls = 0;
  const BASE = JSON.parse(readFileSync(new URL("./fixtures/compare-pack.json", import.meta.url), "utf8"));
  deps = {
    fetchImpl: fetchJwks(issuer.jwks), marketPack: { version: "v1", overview: {}, details: {} },
    stockPack: { as_of: "2026-11-09", analysis_schema: "stock-analysis/v2", results: {} }, comparePack: BASE,
    now: () => Date.parse("2026-11-18T10:00:05Z"),
    quoteFetch: async () => { misCalls += 1; return new Response(JSON.stringify({ msgArray: [] }), { status: 200 }); },
  };
  await env.DB.prepare("INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count) " +
    "VALUES ('v1', '2026-11-08', 'https://isin.twse.com.tw', ?, '2026-11-09T12:00:00Z', 0, 0)").bind("a".repeat(64)).run();
  await env.DB.prepare("INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) VALUES ('current', 'v1', NULL, '2026-11-09T12:00:00Z')").run();
});
async function call(path, init = {}) {
  const h = new Headers(init.headers || {});
  h.set("cf-access-jwt-assertion", await issuer.sign(claims(OWNER)));
  if (init.body) { h.set("content-type", "application/json"); h.set("origin", "https://market-ana.pages.dev"); }
  return handle(new Request("https://market-ana.pages.dev" + path, { ...init, headers: h }), env, next, deps);
}
const notFormatError = async (r, label) => {
  const text = await r.text();
  assert.notEqual(r.status, 400, `${label} 不該回格式錯誤：${text}`);
  assert.doesNotMatch(text, /格式不正確/, label);
};

test("個股各路由收 8 碼代碼：名錄還沒有它時回查無，不是格式錯誤", async () => {
  for (const path of ["/api/stocks/BLKTENAU/analysis", "/api/stocks/BLKTENAU/history", "/api/stocks/BLKTENAU/announcements",
    "/api/stocks/BLKTENAU/live", "/api/stocks/BLKTENAU/ticks", "/api/stocks/BLKTENAU/vs-0050", "/api/drawings/BLKTENAU"]) {
    await notFormatError(await call(path), path);
  }
  const compare = await call("/api/compare-0050?codes=2330,BLKTENAU");
  assert.equal(compare.status, 200);
  assert.equal((await compare.json()).items.find((item) => item.code === "BLKTENAU").status, "not-found");
});

test("加自選、新增庫存、搜尋紀錄收 8 碼代碼：回查無，而且不拿去問證交所即時行情（那裡只有上市櫃股票）", async () => {
  for (const [path, body] of [["/api/watchlist", { code: "BLKTENAU" }], ["/api/holdings", { code: "blktenau", shares: 10, average_cost: 500 }],
    ["/api/search-history", { code: "BLKTENAU" }]]) {
    const r = await call(path, { method: "POST", body: JSON.stringify(body) });
    assert.equal(r.status, 404, path);
    assert.doesNotMatch(await r.text(), /格式不正確/, path);
  }
  assert.equal(misCalls, 0);
});

test("太短、太長、有符號的代號照樣是格式錯誤", async () => {
  for (const code of ["ABC", "ABCDEFGHIJKLM", "BLK-TENAU"]) {
    const r = await call("/api/watchlist", { method: "POST", body: JSON.stringify({ code }) });
    assert.equal(r.status, 400, code);
  }
  assert.equal((await call("/api/compare-0050?codes=ABCDEFGHIJKLM")).status, 400);
});
