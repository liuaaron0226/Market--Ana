// 接縫三：市場資料的欄位允許清單。build 時把 web/*-data.js 轉成雲端資料包，API 只回畫面用到的欄位。
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildMarketPack as build, detailFor, DETAIL_KINDS } from "../lib/market-data.js";
import { hash12 } from "../lib/build-pack.js";

const buildMarketPack = (raw) => build(raw, hash12);

const day = (date, i) => ({ date, open: 100 + i, high: 110 + i, low: 90 + i, close: 105 + i, amount: 1e11 + i });
const rows = (n, f) => Array.from({ length: n }, (_, i) => f(`2026-${String(1 + Math.floor(i / 28)).padStart(2, "0")}-${String(1 + (i % 28)).padStart(2, "0")}`, i));
const stock = { code: "2330", name: "台積電", close: 1000, foreign_lots: 5, foreign_billion: 1.2, trust_lots: 3, trust_billion: 0.4, secret: "x" };
const credit = { code: "2603", name: "長榮", margin_change: 10, margin_balance: 99, short_change: -2, short_balance: 7, secret: "x" };
const top = { code: "2317", name: "鴻海", amount_billion: 50, close: 200, change_pct: 1.5, secret: "x" };

function raw() {
  return {
    analysis: {
      version: "a-v1",
      meta: { generated_at: "g", cutoff_date: "2026-03-10", source_endpoints: { twse: "https://x" }, source_notes: ["n"], source_files: ["C:/secret/path"], calendar_exceptions: [], ranges: {} },
      daily: rows(60, day),
      weekly: rows(30, (date, i) => ({ period: "W", date, start: date, end: date, ...day(date, i), sessions: 5 })),
      monthly: rows(25, (date, i) => ({ period: "M", date, start: date, end: date, ...day(date, i), sessions: 20 })),
      h60: rows(30, (date, i) => ({ timestamp: `${date}T09:00:00+08:00`, date, time: "09:00", ...day(date, i), ticks: 9 })),
      institutional: rows(40, (date) => ({ date, foreign: { buy: 1, sell: 2, net: -1 }, trust: { buy: 3, sell: 1, net: 2 }, dealer: { buy: 1, sell: 1, net: 0 } })),
      futures: rows(30, (date) => ({ date, foreign_oi_net_contracts: -9, foreign_oi_long_contracts: 1 })),
      sectors: [{ name: "電子" }],
      indicators: {
        parameters: { macd: { fast: 12, slow: 26, signal: 9 }, moving_averages: [5, 20], note: "n" },
        daily: rows(60, (date) => ({ date, ma5: 1, dif: 1, dea: 1, hist: 0.5 })),
        weekly: rows(30, (date) => ({ date, hist: 1 })), monthly: rows(25, (date) => ({ date, hist: 1 })), h60: rows(30, (date) => ({ date, hist: 1 })),
      },
      backtest: {
        id: "bt", generated_at: "g", data_range: {}, baseline: { big: [1, 2, 3] },
        method: { lines: "規則", subset_near_low: "0.3%" },
        samples: [{ key: "all_events", label: "全部" }],
        results: { all_events: { event_count: 5, reliability: "r", next_day_outcomes: { sample_count: 5, counts: { stand_back: 1, between: 2, continue_break: 2 }, percent: { stand_back: 20, between: 40, continue_break: 40 } }, five_day_return: { secret: 1 } } },
        as_of: { date: "d", close: 1, high: 2, low: 0, open: 1, previous_low: 1, previous_close: 1, default_sample: "all_events", upper: 2, lower: 0.5, features: { close_position: 0.7, foreign_net: -9 }, atr20: 5, historical_assessment: "內部" },
        limitations: ["l"],
      },
    },
    dashboard: {
      version: "d-v1", generated_at: "g", market_date: "2026-03-10", expires_at: "e", forecast_id: "f",
      candles: [{ x: 1 }], latest: { close: 1, change: 1, change_percent: 1, low: 1, high: 1, previous_low: 1, turnover: 1, turnover_vs_20d: 1, foreign_net: 1, ma20: 1, avg_turnover_20d: 9 },
      thresholds: { upper: 2, lower: 1 },
      forecast: { horizon: "2026-03-11", expiry: "e", market_as_of: "m", scenarios: [{ name: "轉強觀察", trigger: "t", interpretation: "i", probability_note: "內部" }], source_files: ["C:/x"], review: {}, backtest_file: "C:/y", headline: { title: "fh" }, actions: { basis: "fb" } },
      news: [{ published_at: "p", source: "s", facts: ["f"], analyst_inference: "a", url: "https://n", id: "x" }],
      actions: { basis: "b", 轉強觀察: { flat: "f", holding: "h" } }, headline: { title: "t", tone: "o", lede: "l" },
      default_sample: "all_events", method_sources: ["C:/z"], sources: ["s"],
    },
    chips: {
      generated_at: "g", sources: { margin: "https://m" },
      foreign: rows(25, (date) => ({ date, net_billion: -1, trust_billion: 1 })),
      futures: rows(25, (date) => ({ date, net_contracts: -5 })),
      margin: rows(25, (date) => ({ date, margin_balance_billion: 3000, margin_units: 1, short_units: 10, index_close: 1 })),
    },
    plain: { id: "p", for_trading_day: "d", reviewed_by: "logs/x.json", one_line: "一句", points: [{ key: "foreign", title: "外資", lines: ["a"] }], worry: ["w"], relief: ["r"], data_dates: { price_and_foreign: "2026-03-10", margin: "2026-03-09" }, supersedes: "old" },
    detail: {
      generated_at: "g", days: ["2026-03-09", "2026-03-10"], notes: { money: "m", scope: "s", sources: "internal" },
      current: { as_of: "2026-03-10", days_available: 2, futures_short_growing_days: 4, futures_days_available: 1, secret: "x",
        dates: { foreign: "2026-03-10", trust: "2026-03-10", margin: "2026-03-09", short: "2026-03-09", amount: "2026-03-10", extra: "x" },
        trust_buy: [{ code: "2330", name: "台積電", value: 99.7, streak: 20, capped: true, secret: "x" }] },
      by_date: {
        "2026-03-09": {},
        "2026-03-10": {
          turnover_top: [top], foreign_buy: [stock], foreign_sell: [stock], trust_buy: [stock], trust_sell: [stock],
          margin_up: [credit], margin_down: [credit], short_up: [credit], short_down: [credit],
        },
      },
    },
  };
}

const allKeys = (v, out = new Set()) => {
  if (Array.isArray(v)) v.forEach((x) => allKeys(x, out));
  else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) { out.add(k); allKeys(x, out); }
  return out;
};

test("資料包帶同一個版本，總覽不含明細與內部欄位", () => {
  const pack = buildMarketPack(raw());
  assert.match(pack.version, /^[0-9a-f]{12}$/);
  assert.equal(pack.overview.version, pack.version);
  const keys = allKeys(pack.overview);
  // 自營商淨額原本不外送；本人 2026-11-15 要大盤副圖有三大法人後，畫面用得到，只放淨額（buy／sell 仍不送）。
  for (const k of ["source_files", "calendar_exceptions", "sectors", "buy", "sell", "baseline", "five_day_return", "historical_assessment", "atr20",
    "forecast_id", "candles", "method_sources", "review", "backtest_file", "reviewed_by", "supersedes", "margin_units", "index_close", "by_date", "secret",
    "probability_note", "foreign_oi_long_contracts", "avg_turnover_20d", "dif", "dea", "ma5", "ticks", "sessions"]) {
    assert.ok(!keys.has(k), `總覽不應含 ${k}`);
  }
  assert.ok(!JSON.stringify(pack.overview).includes("C:/"), "不得含本機路徑");
});

test("總覽保留畫面計算需要的完整序列（日K、法人、籌碼），指標只給最後 20 筆的柱值", () => {
  const r = raw();
  const { overview: o } = buildMarketPack(r);
  const A = o.analysis;
  assert.equal(A.daily.length, 60);
  assert.deepEqual(A.daily[0], { date: r.analysis.daily[0].date, open: 100, high: 110, low: 90, close: 105, amount: 1e11 });
  assert.equal(A.institutional.length, 40, "劇本的外資賣超樣本與 K 棒細節需要全部法人資料");
  // 本人 2026-11-15：大盤 K 線副圖要有三大法人，自營商淨額也要帶上；買、賣兩個分項仍不外送。
  assert.deepEqual(A.institutional[0], { date: r.analysis.institutional[0].date, foreign: { net: -1 }, trust: { net: 2 }, dealer: { net: 0 } });
  assert.equal(A.weekly.length, 30);
  assert.equal(A.h60[0].time, "09:00");
  assert.equal(A.futures.length, 20);
  for (const p of ["daily", "weekly", "monthly", "h60"]) {
    assert.equal(A.indicators[p].length, 20, p);
    assert.deepEqual(Object.keys(A.indicators[p][0]).sort(), ["date", "hist"]);
  }
  assert.deepEqual(A.indicators.parameters.macd, { fast: 12, slow: 26, signal: 9 });
  assert.deepEqual(A.backtest.results.all_events.next_day_outcomes.counts, { stand_back: 1, between: 2, continue_break: 2 });
  assert.equal(A.backtest.as_of.features.close_position, 0.7);
  // 收復線、防守線每天照規則算在回測的截止日資料裡：要帶給畫面（首頁不再等人寫的劇本才有線，本人 2026-11-15）；其餘內部欄位照舊不給
  assert.deepEqual([A.backtest.as_of.upper, A.backtest.as_of.lower, A.backtest.as_of.default_sample], [2, 0.5, "all_events"]);
  assert.ok(!("atr20" in A.backtest.as_of) && !("historical_assessment" in A.backtest.as_of) && !("previous_close" in A.backtest.as_of));
  assert.equal(A.meta.cutoff_date, "2026-03-10");
  assert.equal(o.chips.foreign.length, 25);
  assert.equal(o.chips.margin[0].short_units, 10);
  assert.equal(o.plain.points[0].lines[0], "a");
  assert.equal(o.dashboard.actions["轉強觀察"].holding, "h");
  assert.equal(o.dashboard.forecast.scenarios[0].trigger, "t");
  assert.deepEqual(o.detailIndex, { days: ["2026-03-09", "2026-03-10"], notes: { money: "m", scope: "s" } });
  // 本人 11-07：每張卡的「目前前 10＋連續天數」放在總覽（小），欄位只留畫面用的。
  assert.deepEqual(o.current.trust_buy[0], { code: "2330", name: "台積電", value: 99.7, streak: 20, capped: true });
  assert.equal(o.current.futures_short_growing_days, 4);
  assert.equal(o.current.as_of, "2026-03-10");
  // 11-17：各項「目前前 10」是自己最新有資料的那天（融資晚上才公布），日期跟著帶
  assert.deepEqual(o.current.dates, { foreign: "2026-03-10", trust: "2026-03-10", margin: "2026-03-09", short: "2026-03-09", amount: "2026-03-10" });
});

test("明細一次只給一張卡需要的表與欄位", () => {
  const pack = buildMarketPack(raw());
  assert.deepEqual([...DETAIL_KINDS].sort(), ["foreign", "futures", "margin", "short", "trust", "volume"]);
  const trust = detailFor(pack, "trust", "2026-03-10");
  assert.deepEqual(Object.keys(trust).sort(), ["trust_buy", "trust_sell"]);
  assert.deepEqual(trust.trust_buy[0], { code: "2330", name: "台積電", trust_billion: 0.4, trust_lots: 3 });
  assert.deepEqual(Object.keys(detailFor(pack, "foreign", "2026-03-10")).sort(), ["foreign_buy", "foreign_sell"]);
  assert.deepEqual(detailFor(pack, "futures", "2026-03-10").foreign_sell[0], { code: "2330", name: "台積電", foreign_billion: 1.2 });
  assert.deepEqual(detailFor(pack, "margin", "2026-03-10").margin_up[0], { code: "2603", name: "長榮", margin_change: 10, margin_balance: 99 });
  assert.deepEqual(detailFor(pack, "short", "2026-03-10").short_down[0], { code: "2603", name: "長榮", short_change: -2, short_balance: 7 });
  assert.deepEqual(detailFor(pack, "volume", "2026-03-10").turnover_top[0], { code: "2317", name: "鴻海", amount_billion: 50, change_pct: 1.5 });
});

test("明細：當天沒抓到回空物件；不在範圍的日期或未知卡片回 null", () => {
  const pack = buildMarketPack(raw());
  assert.deepEqual(detailFor(pack, "trust", "2026-03-09"), {});
  assert.equal(detailFor(pack, "trust", "2026-01-01"), null);
  assert.equal(detailFor(pack, "secret", "2026-03-10"), null);
  assert.equal(detailFor(pack, "__proto__", "2026-03-10"), null);
});

test("版本由內容決定：內容不變版本不變，內容改了版本跟著變", () => {
  const a = buildMarketPack(raw()).version;
  assert.equal(buildMarketPack(raw()).version, a);
  const changed = raw();
  changed.detail.by_date["2026-03-10"].trust_buy[0].trust_lots = 4;
  assert.notEqual(buildMarketPack(changed).version, a);
});

test("每天重寫的白話分析：帶出來源與期貨資料日，模型名稱與內部原因不外送（本人 11-17）", () => {
  const r = raw();
  r.plain = { schema: "market-plain/v2", id: "20260310-plain-ai", source: "ai", model: "gemini-x", generated_at: "g", fallback_reason: "核對沒過",
    one_line: "一句", points: [{ key: "foreign", title: "外資：偏空", lines: ["a"] }], worry: ["w"], relief: ["r"],
    data_dates: { price_and_foreign: "2026-03-10", foreign: "2026-03-10", margin: "2026-03-09", futures: "2026-03-10" } };
  const { overview: o } = buildMarketPack(r);
  assert.equal(o.plain.source, "ai");
  assert.deepEqual(o.plain.data_dates, { price_and_foreign: "2026-03-10", foreign: "2026-03-10", margin: "2026-03-09", futures: "2026-03-10" });
  for (const key of ["model", "generated_at", "fallback_reason", "schema", "id"]) assert.ok(!(key in o.plain), key);
  // 人寫的舊版沒有 source：不補一個假的
  assert.ok(!("source" in buildMarketPack(raw()).overview.plain));
});
