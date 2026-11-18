// 分時圖（本人 11-18：1 秒～15 分 K 換成券商 App 的分時）：個股頁、首頁共用 web/time-share.js。
// 這裡只測純函式：分鐘格（09:00～13:30 固定 271 格、沒走到的留白）、縱軸（個股照漲停到跌停、大盤照當日高低加邊界）、內外盤比。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);

function load() {
  const window = {};
  const context = { window, Math, Number, Object, Array, Map, Set, String, Date, JSON, console };
  vm.runInNewContext(readFileSync(new URL("web/intraday.js", ROOT), "utf8"), { ...context, URL, URLSearchParams, setTimeout() { return 1; }, clearTimeout() {}, AbortController, fetch: async () => ({}) });
  vm.runInNewContext(readFileSync(new URL("web/time-share.js", ROOT), "utf8"), context);
  return window.TimeShare;
}
const plain = value => JSON.parse(JSON.stringify(value));
const minute = (hhmm, close, lots, extra = {}) => ({ t: `2026-11-18T${hhmm}:00.000+08:00`, close, volume: lots * 1000, ...extra });

test("分鐘格：09:00～13:30 共 271 格；沒成交的分鐘沿用上一個價、量 0；還沒走到的留白", () => {
  const { slots } = load();
  const day = slots([minute("09:00", 100, 50), minute("09:01", 101, 10), minute("09:03", 99, 20)]);
  assert.equal(day.slots.length, 271);
  assert.equal(day.date, "2026-11-18");
  assert.equal(day.last, 3, "最後一筆在 09:03（第 3 格）");
  assert.deepEqual(plain(day.slots.slice(0, 4).map(s => [s.price, s.volume])), [[100, 50000], [101, 10000], [101, 0], [99, 20000]]);
  assert.equal(day.slots[4], null, "09:04 還沒走到");
  assert.equal(day.slots[270], null);
});

test("均價＝到那一分鐘為止的成交金額÷成交量（手算）", () => {
  const { slots } = load();
  // 100×50 + 101×10 = 6,010 ÷ 60 = 100.1667；再加 99×20 = 7,990 ÷ 80 = 99.875
  const day = slots([minute("09:00", 100, 50), minute("09:01", 101, 10), minute("09:03", 99, 20)]);
  assert.equal(day.slots[0].avg, 100);
  assert.equal(Math.round(day.slots[1].avg * 10000) / 10000, 100.1667);
  assert.equal(Math.round(day.slots[2].avg * 10000) / 10000, 100.1667, "沒成交的分鐘均價不變");
  assert.equal(day.slots[3].avg, 99.875);
});

test("分鐘格：只取最後一天；收盤 13:30 那一根在最後一格；不同天、盤外的時間不算", () => {
  const { slots } = load();
  const day = slots([
    { t: "2026-11-17T13:30:00.000+08:00", close: 50, volume: 1000 },
    minute("08:59", 98, 1), minute("09:00", 100, 5), minute("13:30", 103, 40), minute("14:30", 104, 3),
  ]);
  assert.equal(day.date, "2026-11-18");
  assert.equal(day.last, 270);
  assert.equal(day.slots[0].price, 100);
  assert.equal(day.slots[269].price, 100, "13:25～13:29 收盤集合競價沒成交，沿用前一個價");
  assert.equal(day.slots[270].price, 103);
  assert.equal(slots([]).last, -1, "沒有資料：一格都沒有");
});

test("分鐘格：最高、最低取每分鐘的高低（沒有高低就用收盤），並記在哪一格", () => {
  const { slots } = load();
  const day = slots([minute("09:00", 100, 5, { high: 102, low: 99.5 }), minute("09:01", 101, 5), minute("09:02", 100.5, 5, { high: 101, low: 98 })]);
  assert.deepEqual(plain(day.high), { value: 102, index: 0 });
  assert.deepEqual(plain(day.low), { value: 98, index: 2 });
});

test("縱軸：個股照漲停到跌停（Intraday.limitPrices，ETF 另一套跳動單位）", () => {
  const { range } = load();
  // 3,840 × 1.1 = 4,224 → 4,220；× 0.9 = 3,456 → 3,460（1,000 元以上跳 5 元）
  assert.deepEqual(plain(range({ kind: "stock", ref: 3840 })), { top: 4220, bottom: 3460 });
  // 0050 前收 63.35：ETF 50 元以上跳 0.05 → 69.65／57.05
  assert.deepEqual(plain(range({ kind: "stock", ref: 63.35, etf: true })), { top: 69.65, bottom: 57.05 });
});

test("縱軸：大盤沒有漲跌停，上下對稱照離參考價最遠的高低再多 18%", () => {
  const { range } = load();
  // 參考 39,990；高 40,380.55（+390.55）、低 39,820.30（−169.70）→ 390.55 × 1.18 = 460.849
  assert.deepEqual(plain(range({ kind: "index", ref: 39990, high: 40380.55, low: 39820.3 })), { top: 40450.85, bottom: 39529.15 });
  // 高低都等於參考價（剛開盤）：至少留參考價的 0.5%
  assert.deepEqual(plain(range({ kind: "index", ref: 20000, high: 20000, low: 20000 })), { top: 20100, bottom: 19900 });
});

test("內外盤比：外盤＝在賣價成交（買方主動）放左邊；百分比合起來 100；沒有成交不出現", () => {
  const { flow } = load();
  assert.deepEqual(plain(flow({ at_ask: 8167, at_bid: 6957 })), { out: 8167, in: 6957, outPct: 54, inPct: 46 });
  assert.deepEqual(plain(flow({ at_ask: 1, at_bid: 2 })), { out: 1, in: 2, outPct: 33, inPct: 67 });
  assert.equal(flow(null), null);
  assert.equal(flow({ at_ask: 0, at_bid: 0 }), null);
});

test("大盤：首頁即時服務的 1 分K（timestamp、price、高低、volume_raw）換成分時用的每分鐘", () => {
  const { indexMinutes, slots } = load();
  const minutes = indexMinutes([
    { timestamp: "2026-11-18T09:00:00.000+08:00", price: 40050.12, open: 40000, high: 40060, low: 39990, volume_raw: 1200 },
    { timestamp: "2026-11-18T09:01:00.000+08:00", price: 40080, high: 40090, low: 40040, volume_raw: null },
  ]);
  assert.deepEqual(plain(minutes), [
    { t: "2026-11-18T09:00:00.000+08:00", close: 40050.12, high: 40060, low: 39990, volume: 1200 },
    { t: "2026-11-18T09:01:00.000+08:00", close: 40080, high: 40090, low: 40040, volume: 0 },
  ]);
  assert.equal(slots(minutes).last, 1);
  assert.deepEqual(plain(indexMinutes(null)), []);
});

test("首頁週期也是分時、60 分、日、週、月；1／5／15 分 K 與「分鐘點推估」拿掉", () => {
  const html = readFileSync(new URL("web/minimal.html", ROOT), "utf8");
  const select = html.slice(html.indexOf('<select id="periodSelect">'), html.indexOf("</select>", html.indexOf('<select id="periodSelect">')));
  const options = [...select.matchAll(/<option value="([^"]+)"[^>]*>([^<]+)<\/option>/g)].map(m => [m[1], m[2]]);
  assert.deepEqual(options, [["ts", "分時"], ["h60", "60 分"], ["daily", "日"], ["weekly", "週"], ["monthly", "月"]]);
  assert.doesNotMatch(html, /estimateBadge|分鐘點推估/);
  assert.match(html, /<script src="time-share\.js\?/);
  assert.match(html, /<link rel="stylesheet" href="time-share\.css\?/);
  const js = readFileSync(new URL("web/minimal.js", ROOT), "utf8");
  assert.doesNotMatch(js, /buildIntradayCandles|'m1'|'m5'|'m15'/);
});
