// 60 分 K 歷史（本人 11-18 核准）：第一次有人看時向富果抓近 6 個月存進 D1，之後每天只補缺的那幾天，開頁讀 D1 不重抓；
// 今天的那幾根由即時服務合成後接在最後。
import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./helpers.js";
import { loadHourly, fetchRange, withToday } from "../lib/hourly-candles.js";

const at = (iso) => Date.parse(iso);
const bar = (day, hh, close, lots = 1000) => ({ date: `${day}T${hh}:00:00.000+08:00`, open: close - 5, high: close + 5, low: close - 10, close, volume: lots });

function fugle(byRange) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const u = new URL(url);
    calls.push({ path: u.pathname, from: u.searchParams.get("from"), to: u.searchParams.get("to"), timeframe: u.searchParams.get("timeframe"), key: init.headers["X-API-KEY"] });
    const data = byRange(u.searchParams.get("from"), u.searchParams.get("to"));
    if (data === 429) return new Response("slow down", { status: 429 });
    if (!data.length) return new Response(JSON.stringify({ message: "Resource Not Found" }), { status: 404 });
    // 富果預設新到舊
    return new Response(JSON.stringify({ symbol: "2330", type: "EQUITY", timeframe: "60", data: [...data].reverse() }), { status: 200 });
  };
  return { calls, fetchImpl };
}

test("抓的區間：第一次是 6 個月前到昨天（不早於富果分 K 的起點 2023-05-23）；補過的只抓之後到昨天；已經補到昨天就不抓", () => {
  assert.deepEqual(fetchRange({ today: "2026-11-18" }), { from: "2026-05-18", to: "2026-11-17" });
  assert.deepEqual(fetchRange({ today: "2026-11-18", through: "2026-11-13" }), { from: "2026-11-14", to: "2026-11-17" });
  assert.equal(fetchRange({ today: "2026-11-18", through: "2026-11-17" }), null);
  assert.deepEqual(fetchRange({ today: "2023-08-31" }), { from: "2023-05-23", to: "2023-08-30" });
  // 很久沒人看（補到的那天比 6 個月前還早）：從 6 個月前開始，不補中間那一大段
  assert.deepEqual(fetchRange({ today: "2026-11-18", through: "2025-01-02" }), { from: "2026-05-18", to: "2026-11-17" });
});

test("第一次看：抓近 6 個月存進 D1、照時間由舊到新、量換成股、標籤寫日期與時間；同一天再開頁直接讀 D1", async () => {
  const db = fakeD1();
  const { calls, fetchImpl } = fugle(() => [bar("2026-11-16", "09", 2200), bar("2026-11-16", "13", 2210), bar("2026-11-17", "09", 2220)]);
  const first = await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T10:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.deepEqual(calls, [{ path: "/marketdata/v1.0/stock/historical/candles/2330", from: "2026-05-18", to: "2026-11-17", timeframe: "60", key: "k" }]);
  assert.deepEqual(first.map((row) => [row.label, row.close, row.volume]),
    [["11/16 09:00", 2200, 1_000_000], ["11/16 13:00", 2210, 1_000_000], ["11/17 09:00", 2220, 1_000_000]]);
  assert.equal(first[0].t, "2026-11-16T09:00:00.000+08:00");
  const again = await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T13:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.equal(calls.length, 1, "同一天不重抓");
  assert.deepEqual(again, first);
});

test("隔幾天再看：只抓缺的那幾天接上去；區間裡沒有交易（富果回 404）也記成補過", async () => {
  const db = fakeD1();
  const days = { "2026-11-17": [bar("2026-11-17", "09", 2220)], "2026-11-18": [bar("2026-11-18", "09", 2230)], "2026-11-21": [] };
  const { calls, fetchImpl } = fugle((from, to) => Object.entries(days).filter(([day]) => day >= from && day <= to).flatMap(([, rows]) => rows));
  await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T08:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  const later = await loadHourly({ db, code: "2330", nowMs: at("2026-11-19T08:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.deepEqual(calls.at(-1), { path: "/marketdata/v1.0/stock/historical/candles/2330", from: "2026-11-18", to: "2026-11-18", timeframe: "60", key: "k" });
  assert.deepEqual(later.map((row) => row.close), [2220, 2230]);
  await loadHourly({ db, code: "2330", nowMs: at("2026-11-22T08:00:00+08:00"), fugleApiKey: "k", fetchImpl });  // 11-19～11-21 沒資料
  await loadHourly({ db, code: "2330", nowMs: at("2026-11-22T09:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.equal(calls.length, 3, "404 的區間記成補過，不會一直重問");
});

test("富果失敗（429 等）：回 D1 裡已經有的，不記成補過、下次再試；沒有金鑰：只讀 D1", async () => {
  const db = fakeD1();
  const { calls, fetchImpl } = fugle(() => 429);
  const failed = await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T10:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.deepEqual(failed, []);
  await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T10:01:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.equal(calls.length, 2, "失敗不算補過");
  const none = fugle(() => [bar("2026-11-17", "09", 2220)]);
  assert.deepEqual(await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T10:00:00+08:00"), fugleApiKey: null, fetchImpl: none.fetchImpl }), []);
  assert.equal(none.calls.length, 0);
});

test("只回近 6 個月（一次存 140 根）", async () => {
  const db = fakeD1();
  const rows = [];
  for (let d = 1; d <= 28; d += 1) for (const hh of ["09", "10", "11", "12", "13"]) rows.push(bar(`2026-10-${String(d).padStart(2, "0")}`, hh, 2000 + d));
  const { fetchImpl } = fugle(() => rows);
  db.resetMetrics?.();
  const got = await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T10:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.equal(got.length, 140);
  db._raw.prepare("INSERT INTO stock_hourly_candles (code, t, open, high, low, close, volume) VALUES ('2330', '2026-04-01T09:00:00.000+08:00', 1, 1, 1, 1, 1)").run();
  const again = await loadHourly({ db, code: "2330", nowMs: at("2026-11-18T11:00:00+08:00"), fugleApiKey: "k", fetchImpl });
  assert.equal(again.length, 140, "6 個月以前的不回");
});

test("接上今天：歷史裡今天以後的不要，今天的即時 60 分接在最後（標籤補日期）", () => {
  const history = [{ t: "2026-11-17T13:00:00.000+08:00", label: "11/17 13:00", close: 1 }];
  const today = [{ t: "2026-11-18T09:00:00.000+08:00", label: "09:00", open: 1, high: 2, low: 1, close: 2, volume: 5 }];
  assert.deepEqual(withToday(history, today), [history[0], { ...today[0], label: "11/18 09:00" }]);
  assert.deepEqual(withToday(history, [{ ...today[0], t: "2026-11-17T13:00:00.000+08:00" }]).length, 1, "今天的資料其實是歷史已有的那天：不重複");
  assert.deepEqual(withToday(history, []), history);
});
