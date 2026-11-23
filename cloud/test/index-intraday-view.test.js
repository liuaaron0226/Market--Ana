// 海外大盤的分時（本人 2026-11-24）：web/time-share.js 把 /api/indices/<key>/intraday 的回應換成分時圖要的格子。
// 這裡只測純函式：基準（前一日收盤）、午休不連線、時間換成台北時間、時間軸的字、那份資料是不是交易所今天的。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function load() {
  const window = {};
  const context = { window, console };
  vm.runInNewContext(readFileSync(new URL("web/intraday.js", ROOT), "utf8"), { ...context, URL, URLSearchParams, setTimeout() { return 1; }, clearTimeout() {}, AbortController, fetch: async () => ({}) });
  vm.runInNewContext(readFileSync(new URL("web/time-share.js", ROOT), "utf8"), context);
  return window.TimeShare;
}
const plain = (value) => JSON.parse(JSON.stringify(value));

// 日經 11-24：09:00～15:30（日本時間）＝ 08:00～14:30（台北）＝ 00:00～06:30 UTC；11:30～12:25 午休
const OPEN = Date.parse("2026-11-24T00:00:00Z") / 1000;
const CLOSE = OPEN + 6.5 * 3600;
function nikkei(overrides = {}) {
  const points = [];
  for (let i = 0; i <= 78; i += 1) if (i < 30 || i >= 42) points.push([OPEN + i * 300, 50000 + (i - 20) * 10]);
  return { available: true, key: "n225", name: "日經 225", timezone: "Asia/Tokyo", date: "2026-11-24", interval: 300, previous_close: 50000,
    last: 50580, points, session: { start: OPEN, end: CLOSE }, as_of: CLOSE, stale: false, ...overrides };
}
const NOON_TOKYO = Date.parse("2026-11-24T03:00:00Z");

test("格子：開盤到收盤每 5 分鐘一格；午休那幾格留白（不沿用上一個價）；基準是前一日收盤", () => {
  const { sessionModel } = load();
  const model = sessionModel(nikkei(), NOON_TOKYO);
  assert.equal(model.kind, "index");
  assert.equal(model.n, 79);
  assert.equal(model.day.slots.length, 79);
  assert.equal(model.ref, 50000);
  assert.equal(model.day.slots[29].price, 50090);
  assert.deepEqual(plain(model.day.slots.slice(30, 42)), new Array(12).fill(null));
  assert.equal(model.day.slots[42].price, 50220);
  assert.equal(model.day.last, 78);
  assert.equal(model.gap, 3, "連續缺超過 3 格（15 分鐘）線就斷開");
  assert.equal(model.noVolume, true);
  assert.deepEqual(plain([model.open, model.high, model.low, model.day.high, model.day.low]),
    [49800, 50580, 49800, { value: 50580, index: 78 }, { value: 49800, index: 0 }]);
  assert.ok(model.top > 50580 && model.bottom < 49800 && Math.abs((model.top - 50000) - (50000 - model.bottom)) < 0.02, "縱軸以基準上下對稱");
  assert.equal(model.label, "日經 225分時走勢");
});

test("時間是台北時間：日經 08:00～14:30；游標那一行的時間也是", () => {
  const { sessionModel } = load();
  const model = sessionModel(nikkei(), NOON_TOKYO);
  assert.deepEqual(plain(model.ticks), [[0, "08:00", "start"], [24, "10:00", "middle"], [48, "12:00", "middle"], [78, "14:30", "end"]]);
  assert.deepEqual([model.clock(0), model.clock(29), model.clock(42), model.clock(78)], ["08:00", "10:25", "11:30", "14:30"]);
});

test("時間軸的字：美股（台北 22:30～05:00，跨過半夜）與走 24 小時的期貨", () => {
  const { sessionTicks } = load();
  const ny = Date.parse("2026-11-23T14:30:00Z") / 1000;
  assert.deepEqual(plain(sessionTicks(ny, ny + 6.5 * 3600, 300).map((tick) => tick[1])), ["22:30", "00:00", "02:00", "04:00", "05:00"]);
  const midnight = Date.parse("2026-11-24T05:00:00Z") / 1000;
  assert.deepEqual(plain(sessionTicks(midnight, midnight + 86400 - 300, 300).map((tick) => tick[1])), ["13:00", "18:00", "00:00", "06:00", "12:55"]);
});

test("那份資料是不是交易所今天的：照交易所的時區看「今天」", () => {
  const { sessionModel, zoneDate } = load();
  assert.equal(sessionModel(nikkei(), NOON_TOKYO).today, "2026-11-24");
  // 台北 11-25 08:30 ＝ 東京 11-25 09:30：手上還是 11-24 的資料
  assert.equal(sessionModel(nikkei(), Date.parse("2026-11-25T00:30:00Z")).today, "2026-11-25");
  // 台北 11-24 10:00 ＝ 紐約 11-23 21:00：11-23 那一場就是紐約的今天
  assert.equal(zoneDate(Date.parse("2026-11-24T02:00:00Z"), "America/New_York"), "2026-11-23");
  assert.equal(zoneDate(NOON_TOKYO, "Not/AZone"), null);
  assert.equal(sessionModel(nikkei({ timezone: null }), NOON_TOKYO).today, null);
});

test("盤中：最新那一根的時間不在 5 分鐘的刻度上，算進它所在的那一格；還沒走到的留白", () => {
  const { sessionModel } = load();
  const points = [[OPEN, 50010], [OPEN + 300, 50020], [OPEN + 300 + 137, 50033]];
  const model = sessionModel(nikkei({ points, as_of: OPEN + 437 }), NOON_TOKYO);
  assert.equal(model.day.last, 1);
  assert.equal(model.day.slots[1].price, 50033);
  assert.equal(model.day.slots[2], null);
  assert.equal(model.n, 79);
});

test("沒有開收盤時間：照第一根到最後一根；開收盤時間包不住資料時也一樣", () => {
  const { sessionModel } = load();
  const points = [[OPEN + 600, 50010], [OPEN + 1500, 50020]];
  const model = sessionModel(nikkei({ points, session: null }), NOON_TOKYO);
  assert.deepEqual([model.n, model.day.last, model.clock(0)], [4, 3, "08:10"]);
  const outside = sessionModel(nikkei({ points, session: { start: OPEN + 900, end: OPEN + 1200 } }), NOON_TOKYO);
  assert.deepEqual([outside.n, outside.clock(0), outside.clock(3)], [4, "08:10", "08:25"]);
});

test("拿不到、沒有任何一根、沒有前一日收盤：不畫（null）", () => {
  const { sessionModel } = load();
  for (const payload of [null, { available: false }, nikkei({ points: [] }), nikkei({ points: [[OPEN, null]] }), nikkei({ previous_close: null }),
    nikkei({ previous_close: 0 }), nikkei({ available: undefined })]) {
    assert.equal(sessionModel(payload, NOON_TOKYO), null);
  }
});
