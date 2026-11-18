// 60 分 K 也能畫線（本人 2026-11-18）：各畫各的——60 分畫的線只在 60 分出現，錨點記到那一根 60 分 K 開始的時分；
// 日／週／月畫的照舊只在日／週／月。錨點的日期本身就分得出週期：日線「YYYY-MM-DD」、60 分「YYYY-MM-DDTHH:MM」；
// 資料表多一欄 frame（day／hour，migration 0028），舊資料一律算日線。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { handle } from "../lib/gate.js";
import { fakeD1, makeIssuer, claims, fetchJwks, TEAM, MAIN_AUD, APPLY_AUD, OWNER, MIGRATION_FILES, migrationSql } from "./helpers.js";

let issuer, env, deps;
const ORIGIN = "https://market-ana.pages.dev";
const next = async () => new Response("static-ok");

beforeEach(async () => {
  issuer = await makeIssuer();
  env = { DB: fakeD1(), TEAM_DOMAIN: TEAM, MAIN_AUD, APPLY_AUD, OWNER_EMAIL: OWNER };
  deps = { fetchImpl: fetchJwks(issuer.jwks), now: () => Date.parse("2026-11-18T06:00:00Z") };
});

async function req(path, { method = "GET", body } = {}) {
  const headers = new Headers({ "cf-access-jwt-assertion": await issuer.sign(claims(OWNER)) });
  if (body !== undefined) headers.set("content-type", "application/json");
  if (method !== "GET") headers.set("origin", ORIGIN);
  return handle(new Request(ORIGIN + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }), env, next, deps);
}
const items = async () => (await (await req("/api/drawings/2330")).json()).items;
const frames = () => env.DB._raw.prepare("SELECT id, frame, date1, date2 FROM user_drawings ORDER BY id").all();

test("60 分的趨勢線：兩個錨點記到時分，存得住、讀回來一模一樣；資料表記成 hour", async () => {
  const body = { kind: "trend", anchors: [{ date: "2026-11-13T09:00", price: 2210 }, { date: "2026-11-17T13:00", price: 2265.5 }] };
  assert.equal((await req("/api/drawings/2330/hour-trend-1", { method: "PUT", body })).status, 201);
  assert.equal((await req("/api/drawings/2330/day-hline-1", { method: "PUT", body: { kind: "hline", anchors: [{ date: "2026-11-13", price: 2200 }] } })).status, 201);
  const list = await items();
  assert.deepEqual(list.find((item) => item.id === "hour-trend-1").anchors, body.anchors);
  assert.deepEqual(list.find((item) => item.id === "day-hline-1").anchors, [{ date: "2026-11-13", price: 2200 }]);
  assert.deepEqual(frames().map((row) => [row.id, row.frame]), [["day-hline-1", "day"], ["hour-trend-1", "hour"]]);
});

test("60 分的錨點格式不對、或兩個錨點一個日線一個 60 分，都被拒", async () => {
  const bad = [
    { kind: "hline", anchors: [{ date: "2026-11-13T24:00", price: 100 }] },
    { kind: "hline", anchors: [{ date: "2026-11-13T09:60", price: 100 }] },
    { kind: "hline", anchors: [{ date: "2026-11-13T09:00:00", price: 100 }] },
    { kind: "hline", anchors: [{ date: "2026-11-13 09:00", price: 100 }] },
    { kind: "hline", anchors: [{ date: "2026-02-30T09:00", price: 100 }] },
    { kind: "trend", anchors: [{ date: "2026-11-13", price: 100 }, { date: "2026-11-14T09:00", price: 110 }] },
    { kind: "trend", anchors: [{ date: "2026-11-13T09:00", price: 100 }, { date: "2026-11-13T09:00", price: 100 }] },
  ];
  for (const body of bad) assert.equal((await req("/api/drawings/2330/bad-line-01", { method: "PUT", body })).status, 400, JSON.stringify(body));
  assert.deepEqual(frames(), []);
});

test("同一條線拖到另一個週期的格式（日線改成 60 分）就是另一種線：以同一個識別碼覆寫時跟著換", async () => {
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: { kind: "hline", anchors: [{ date: "2026-11-13", price: 2200 }] } });
  await req("/api/drawings/2330/line-0001", { method: "PUT", body: { kind: "hline", anchors: [{ date: "2026-11-13T10:00", price: 2200 }] } });
  assert.deepEqual(frames().map((row) => [row.frame, row.date1]), [["hour", "2026-11-13T10:00"]]);
});

test("資料表本身也擋：frame 和錨點格式要對得上；沒寫 frame 的照舊是日線", async () => {
  const insert = (frame, date) => env.DB.prepare(
    `INSERT INTO user_drawings (email, code, id, kind, ${frame ? "frame, " : ""}date1, price1, created_at, updated_at) VALUES ('a@example.com', '2330', ?, 'hline', ${frame ? "?, " : ""}?, 100, 't', 't')`,
  ).bind(...[`row-${date}`, ...(frame ? [frame] : []), date]).run();
  await insert(null, "2026-11-13");
  assert.equal(frames()[0].frame, "day");
  await assert.rejects(insert("day", "2026-11-13T09:00"));
  await assert.rejects(insert("hour", "2026-11-13"));
  await assert.rejects(insert("minute", "2026-11-13"));
  await insert("hour", "2026-11-13T09:00");
});

test("migration 0028：已經存在的畫線原樣搬過去、一律算日線；每一檔 200 條的上限照舊", () => {
  const db = new DatabaseSync(":memory:");
  const before = MIGRATION_FILES.slice(0, MIGRATION_FILES.indexOf("0028_user_drawings_frame.sql"));
  db.exec(before.map(migrationSql).join("\n"));
  const old = db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, date2, price2, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
  old.run("a@example.com", "2330", "old-trend-1", "trend", "2026-11-10", 2100, "2026-11-13", 2200, "", "2026-11-14T00:00:00Z", "2026-11-14T01:00:00Z");
  old.run("a@example.com", "BLKTENAU", "old-note-01", "text", "2026-11-10", 21.5, null, null, "看這裡", "2026-11-14T00:00:00Z", "2026-11-14T00:00:00Z");
  db.exec(migrationSql("0028_user_drawings_frame.sql"));
  assert.deepEqual(db.prepare("SELECT email, code, id, kind, frame, date1, price1, date2, price2, text, created_at, updated_at FROM user_drawings ORDER BY code, id").all().map((row) => ({ ...row })), [
    { email: "a@example.com", code: "2330", id: "old-trend-1", kind: "trend", frame: "day", date1: "2026-11-10", price1: 2100, date2: "2026-11-13", price2: 2200, text: "", created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T01:00:00Z" },
    { email: "a@example.com", code: "BLKTENAU", id: "old-note-01", kind: "text", frame: "day", date1: "2026-11-10", price1: 21.5, date2: null, price2: null, text: "看這裡", created_at: "2026-11-14T00:00:00Z", updated_at: "2026-11-14T00:00:00Z" },
  ]);
  const add = db.prepare("INSERT INTO user_drawings (email, code, id, kind, date1, price1, created_at, updated_at) VALUES ('b@example.com', '2330', ?, 'hline', '2026-11-13', 100, 't', 't')");
  for (let index = 0; index < 200; index += 1) add.run(`line-${String(index).padStart(4, "0")}`);
  assert.throws(() => add.run("line-0200"), /drawings limit reached/);
});
