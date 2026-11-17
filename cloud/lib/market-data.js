// 票 06：把本機 web/*-data.js 的內容轉成雲端資料包，只留畫面用得到的欄位（允許清單，不是黑名單）。
// 總覽一次給（含公開的加權指數日K，本人 11-07 定案）；個股明細按「卡片＋日期」一次一張。

const pick = (obj, keys) => {
  const out = {};
  if (!obj) return out;
  for (const k of keys) if (obj[k] !== undefined) out[k] = obj[k];
  return out;
};
const pickAll = (rows, keys) => (rows || []).map((r) => pick(r, keys));
const last = (rows, n) => (rows || []).slice(-n);
const pickEntries = (obj, fn) => Object.fromEntries(Object.entries(obj || {}).map(([k, v]) => [k, fn(v)]));

const OHLC = ["date", "open", "high", "low", "close", "amount"];
const PERIOD_OHLC = ["date", "start", "end", "open", "high", "low", "close", "amount"];

function analysisView(a) {
  const bt = a.backtest || {};
  return {
    meta: pick(a.meta, ["generated_at", "cutoff_date", "source_endpoints", "source_notes"]),
    daily: pickAll(a.daily, OHLC),
    weekly: pickAll(a.weekly, PERIOD_OHLC),
    monthly: pickAll(a.monthly, PERIOD_OHLC),
    h60: pickAll(a.h60, ["timestamp", "date", "time", "open", "high", "low", "close"]),
    // 劇本「外資賣超」樣本與 K 棒細節都要逐日法人淨額，不能截短（Astra r1 指出）。
    institutional: (a.institutional || []).map((r) => ({ date: r.date, foreign: pick(r.foreign, ["net"]), trust: pick(r.trust, ["net"]), dealer: pick(r.dealer, ["net"]) })),
    futures: pickAll(last(a.futures, 20), ["date", "foreign_oi_net_contracts"]),
    indicators: {
      parameters: { macd: pick(a.indicators?.parameters?.macd, ["fast", "slow", "signal"]) },
      ...Object.fromEntries(["daily", "weekly", "monthly", "h60"].map((p) => [p, pickAll(last(a.indicators?.[p], 20), ["date", "hist"])])),
    },
    backtest: {
      method: bt.method,
      samples: pickAll(bt.samples, ["key", "label"]),
      results: pickEntries(bt.results, (r) => ({
        event_count: r.event_count,
        next_day_outcomes: pick(r.next_day_outcomes, ["sample_count", "counts", "percent"]),
      })),
      as_of: {
        // upper／lower：收復線、防守線，每天照規則算（logs/build_analysis_data.py 的 day_lines）。首頁的線讀這裡，不等人寫的劇本
        ...pick(bt.as_of, ["date", "close", "high", "low", "open", "previous_low", "default_sample", "upper", "lower"]),
        features: pick(bt.as_of?.features, ["close_position"]),
      },
      limitations: bt.limitations,
    },
  };
}

function dashboardView(d) {
  const f = d.forecast || {};
  return {
    market_date: d.market_date,
    expires_at: d.expires_at,
    latest: pick(d.latest, ["close", "change", "change_percent", "low", "high", "turnover", "turnover_vs_20d", "foreign_net", "ma20"]),
    thresholds: pick(d.thresholds, ["upper", "lower"]),
    forecast: {
      ...pick(f, ["horizon", "expiry", "market_as_of", "headline", "actions"]),
      scenarios: pickAll(f.scenarios, ["name", "trigger", "interpretation"]),
    },
    news: pickAll(d.news, ["published_at", "source", "facts", "analyst_inference", "url"]),
    actions: d.actions,
    headline: pick(d.headline, ["title", "tone", "lede"]),
    default_sample: d.default_sample,
  };
}

const chipsView = (c) => ({
  foreign: pickAll(c.foreign, ["date", "net_billion", "trust_billion"]),
  futures: pickAll(c.futures, ["date", "net_contracts"]),
  margin: pickAll(c.margin, ["date", "margin_balance_billion", "short_units"]),
});

// source：本人 11-17 起每天重寫，ai＝Gemini 寫、數字由程式核對；template＝模型沒寫成時程式依數字套句；人寫的舊版沒有這欄。
const plainView = (p) => ({
  ...pick(p, ["one_line", "worry", "relief", "source"]),
  points: pickAll(p.points, ["key", "title", "lines"]),
  data_dates: pick(p.data_dates, ["price_and_foreign", "margin", "futures"]),
});

// 本人 11-07：每張卡「目前前 10＋連續天數」，資料小，放總覽。
const CURRENT_TABLES = ["foreign_buy", "foreign_sell", "trust_buy", "trust_sell", "margin_up", "margin_down", "short_up", "short_down", "turnover_top"];
function currentView(c) {
  if (!c) return null;
  return {
    ...pick(c, ["as_of", "days_available", "futures_short_growing_days", "futures_days_available"]),
    // 11-17：各項自己的最新一天（融資晚上才公布，常比外資晚一天）；舊資料包沒有就不給
    ...(c.dates ? { dates: pick(c.dates, ["foreign", "trust", "margin", "short", "amount"]) } : {}),
    ...Object.fromEntries(CURRENT_TABLES.map((k) => [k, pickAll(c[k], ["code", "name", "value", "streak", "capped"])])),
  };
}

// 每張籌碼卡下鑽時要的表與欄位（對照 minimal.js dayDetail）。
const DETAIL = {
  foreign: { foreign_buy: ["foreign_billion", "foreign_lots"], foreign_sell: ["foreign_billion", "foreign_lots"] },
  trust: { trust_buy: ["trust_billion", "trust_lots"], trust_sell: ["trust_billion", "trust_lots"] },
  margin: { margin_up: ["margin_change", "margin_balance"], margin_down: ["margin_change", "margin_balance"] },
  short: { short_up: ["short_change", "short_balance"], short_down: ["short_change", "short_balance"] },
  volume: { turnover_top: ["amount_billion", "change_pct"] },
  futures: { foreign_sell: ["foreign_billion"] },
};
export const DETAIL_KINDS = Object.keys(DETAIL);

// hash 由呼叫端提供（build 時用 node:crypto），讓本模組不依賴 Node API、可直接進 Pages Functions。
export function buildMarketPack({ analysis, dashboard, chips, plain, detail }, hash) {
  const content = {
    analysis: analysisView(analysis),
    dashboard: dashboardView(dashboard),
    chips: chipsView(chips),
    plain: plainView(plain),
    detailIndex: { days: [...(detail.days || [])], notes: pick(detail.notes, ["money", "scope"]) },
    current: currentView(detail.current),
  };
  const details = {};
  for (const day of content.detailIndex.days) {
    const src = detail.by_date?.[day] || {};
    details[day] = Object.fromEntries(DETAIL_KINDS.map((kind) => [kind, Object.fromEntries(
      Object.entries(DETAIL[kind]).filter(([table]) => Array.isArray(src[table])).map(([table, cols]) => [table, pickAll(src[table], ["code", "name", ...cols])]),
    )]));
  }
  const version = hash({ content, details });
  return { version, overview: { version, ...content }, details };
}

export function detailFor(pack, kind, date) {
  if (!Object.hasOwn(DETAIL, kind) || !Object.hasOwn(pack.details, date)) return null;
  return pack.details[date][kind];
}
