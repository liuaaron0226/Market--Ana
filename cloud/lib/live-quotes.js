const MIS_ORIGIN = "https://mis.twse.com.tw";
const QUOTE_PATH = "/stock/api/getStockInfo.jsp";
const FUGLE_ORIGIN = "https://api.fugle.tw";
const FUGLE_BASE = "/marketdata/v1.0/stock/intraday";
const PROBE_CODE = "2330";
const MIS_QUOTE_INTERVAL_MS = 5_000;
const MIS_CHART_INTERVAL_MS = 15_000;
const FUGLE_QUOTE_INTERVAL_MS = 15_000;
const FUGLE_CANDLE_INTERVAL_MS = 60_000;
const FRESH_MS = 30_000;
const MAX_POINTS = 2_000;

const finitePositive = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};
const round = (value, digits = 2) => Number(value.toFixed(digits));

function taipeiIso(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}:${get("second")}+08:00`;
}

function fugleIso(value) {
  const numeric = Number(value);
  const milliseconds = Number.isFinite(numeric) ? (numeric > 100_000_000_000_000 ? numeric / 1000 : numeric) : value;
  return taipeiIso(milliseconds);
}

function quoteUrl(code, market) {
  const url = new URL(QUOTE_PATH, MIS_ORIGIN);
  url.searchParams.set("ex_ch", `${market}_${code}.tw`);
  url.searchParams.set("json", "1");
  url.searchParams.set("delay", "0");
  return url;
}

function fugleUrl(kind, code) {
  const url = new URL(`${FUGLE_BASE}/${kind}/${encodeURIComponent(code)}`, FUGLE_ORIGIN);
  if (kind === "candles") url.searchParams.set("timeframe", "60");
  return url;
}

const OPEN_MINUTES = 9 * 60;
const CLOSE_MINUTES = 13 * 60 + 30;

// 台北時間的星期與分鐘（台灣沒有日光節約，固定 UTC+8）。
function taipeiClock(nowMs) {
  const local = new Date(nowMs + 8 * 60 * 60 * 1000);
  return { weekday: local.getUTCDay(), minutes: local.getUTCHours() * 60 + local.getUTCMinutes() };
}
const isWeekday = ({ weekday }) => weekday >= 1 && weekday <= 5;

function marketWindow(nowMs) {
  const clock = taipeiClock(nowMs);
  return isWeekday(clock) && clock.minutes >= OPEN_MINUTES && clock.minutes < CLOSE_MINUTES;
}

function taipeiDate(nowMs) {
  return taipeiIso(nowMs)?.slice(0, 10) || null;
}

// 收盤後（交易日 13:30 以後）：收盤價定了，抓到今天的收盤就不再隨請求重打上游。
function afterCloseToday(nowMs) {
  const clock = taipeiClock(nowMs);
  return isWeekday(clock) && clock.minutes >= CLOSE_MINUTES;
}

function sessionCloseMs(nowMs) {
  return Date.parse(`${taipeiDate(nowMs)}T13:30:00+08:00`);
}

function parseQuote(payload, code, market) {
  if (String(payload?.rtcode) !== "0000") throw new Error("MIS 回應狀態不正確");
  const row = Array.isArray(payload?.msgArray)
    ? payload.msgArray.find((item) => String(item?.c) === code)
    : null;
  if (!row) throw new Error("MIS 沒有回傳指定股票");
  const price = finitePositive(row.z) ?? finitePositive(row.trade?.z);
  const previousClose = finitePositive(row.y);
  const dataTime = taipeiIso(Number(row.tlong));
  if (price === null || previousClose === null || dataTime === null) {
    throw new Error("MIS 報價缺少現價、前收或時間");
  }
  return {
    ok: true, code, market, price, previous_close: previousClose,
    change: round(price - previousClose), change_percent: round((price - previousClose) / previousClose * 100),
    open: finitePositive(row.o), high: finitePositive(row.h), low: finitePositive(row.l),
    volume_shares: finitePositive(row.v) === null ? null : Math.round(Number(row.v) * 1000),
    data_time: dataTime, data_date: dataTime.slice(0, 10), upstream_delay_ms: Number(payload.userDelay) || null,
  };
}

function parseFugleQuote(payload, code, market) {
  if (String(payload?.symbol || code) !== code) throw new Error("Fugle 沒有回傳指定股票");
  const price = finitePositive(payload.closePrice) ?? finitePositive(payload.lastPrice);
  const previousClose = finitePositive(payload.previousClose);
  const dataTime = fugleIso(payload.lastUpdated ?? payload.total?.time ?? payload.lastTrade?.time);
  if (price === null || previousClose === null || dataTime === null) {
    throw new Error("Fugle 報價缺少現價、前收或時間");
  }
  return {
    ok: true, code, market, price, previous_close: previousClose,
    change: Number.isFinite(Number(payload.change)) ? round(Number(payload.change)) : round(price - previousClose),
    change_percent: Number.isFinite(Number(payload.changePercent))
      ? round(Number(payload.changePercent)) : round((price - previousClose) / previousClose * 100),
    open: finitePositive(payload.openPrice), high: finitePositive(payload.highPrice), low: finitePositive(payload.lowPrice),
    volume_shares: finitePositive(payload.total?.tradeVolume) === null ? null : Math.round(Number(payload.total.tradeVolume) * 1000),
    data_time: dataTime, data_date: dataTime.slice(0, 10), upstream_delay_ms: null,
  };
}

function parseFugleCandles(payload) {
  if (!Array.isArray(payload?.data)) throw new Error("Fugle 60 分K格式不正確");
  return payload.data.map((row) => ({
    t: row.date,
    label: String(row.date).slice(11, 16),
    open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close),
    volume: Number.isFinite(Number(row.volume)) ? Math.round(Number(row.volume) * 1000) : null,
  })).filter((row) => row.t && [row.open, row.high, row.low, row.close].every(Number.isFinite));
}

function aggregateCandles(points, minutes) {
  const buckets = new Map();
  const width = minutes * 60_000;
  for (const point of points) {
    const stamp = Date.parse(point.timestamp);
    if (!Number.isFinite(stamp)) continue;
    const bucket = Math.floor(stamp / width) * width;
    const current = buckets.get(bucket);
    if (!current) {
      buckets.set(bucket, {
        t: taipeiIso(bucket), label: taipeiIso(bucket)?.slice(11, 16),
        open: point.price, high: point.price, low: point.price, close: point.price,
        volume: Math.max(0, point.volume_delta || 0),
      });
    } else {
      current.high = Math.max(current.high, point.price);
      current.low = Math.min(current.low, point.price);
      current.close = point.price;
      current.volume += Math.max(0, point.volume_delta || 0);
    }
  }
  return [...buckets.values()];
}

function retrySeconds(state, nowMs) {
  return Math.max(0, Math.ceil((state.retryAt - nowMs) / 1000));
}

export function createLiveQuoteService({ fetchImpl = fetch, now = Date.now,
  quoteIntervalMs = MIS_QUOTE_INTERVAL_MS, chartIntervalMs = MIS_CHART_INTERVAL_MS,
  fugleQuoteIntervalMs = FUGLE_QUOTE_INTERVAL_MS, fugleCandleIntervalMs = FUGLE_CANDLE_INTERVAL_MS } = {}) {
  const states = new Map();

  const stateFor = (code, market) => {
    const key = `${market}:${code}`;
    if (!states.has(key)) states.set(key, {
      quote: null, fetchedAt: 0, lastAttempt: 0, lastChartAt: 0, lastFugleCandlesAt: 0,
      retryAt: 0, failures: 0, error: null, points: [], candles60: [], lastCumulativeVolume: null, provider: null,
    });
    return states.get(key);
  };

  function rememberQuote(state, quote, nowMs, sampleIntervalMs) {
    state.quote = quote;
    state.fetchedAt = nowMs;
    state.failures = 0;
    state.retryAt = 0;
    state.error = null;
    // 收盤後抓的是收盤報價，不是盤中取樣，不加進分時點。
    if (afterCloseToday(nowMs)) return;
    if (state.lastChartAt === 0 || nowMs - state.lastChartAt >= sampleIntervalMs) {
      const cumulative = quote.volume_shares;
      const volumeDelta = cumulative !== null && state.lastCumulativeVolume !== null
        ? Math.max(0, cumulative - state.lastCumulativeVolume) : 0;
      state.points.push({ timestamp: quote.data_time, price: quote.price, volume_delta: volumeDelta });
      state.points = state.points.slice(-MAX_POINTS);
      state.lastCumulativeVolume = cumulative;
      state.lastChartAt = nowMs;
    }
  }

  function rememberError(state, error, nowMs, provider) {
    state.failures += 1;
    const delaySeconds = error.status === 429
      ? Math.max(60, error.retryAfter || 0)
      : Math.min(120, 5 * 2 ** Math.min(state.failures, 5));
    state.retryAt = nowMs + delaySeconds * 1000;
    state.error = error instanceof Error ? error.message : `${provider} 連線失敗`;
  }

  async function fetchMisQuote(code, market, state, nowMs) {
    state.lastAttempt = nowMs;
    const url = quoteUrl(code, market);
    url.searchParams.set("_", String(nowMs));
    try {
      const response = await fetchImpl(url.toString(), {
        headers: { accept: "application/json,text/plain,*/*", referer: `${MIS_ORIGIN}/stock/`, "user-agent": "Market-Ana/1.0" },
      });
      if (!response.ok) {
        const error = new Error(`MIS HTTP ${response.status}`);
        error.status = response.status;
        error.retryAfter = Number(response.headers.get("retry-after")) || null;
        throw error;
      }
      rememberQuote(state, parseQuote(await response.json(), code, market), nowMs, chartIntervalMs);
      state.provider = "mis";
    } catch (error) {
      rememberError(state, error, nowMs, "MIS");
    }
  }

  async function fugleRequest(url, key) {
    const response = await fetchImpl(url.toString(), { headers: { accept: "application/json", "X-API-KEY": key } });
    if (!response.ok) {
      const error = new Error(`Fugle HTTP ${response.status}`);
      error.status = response.status;
      error.retryAfter = Number(response.headers.get("retry-after")) || null;
      throw error;
    }
    return response.json();
  }

  async function fetchFugleCandles(code, state, nowMs, key) {
    state.lastFugleCandlesAt = nowMs;
    state.candles60 = parseFugleCandles(await fugleRequest(fugleUrl("candles", code), key));
    state.provider = "fugle";
  }

  const quoteDue = (state, nowMs, interval) => state.lastAttempt === 0 || nowMs - state.lastAttempt >= interval;
  const fugleCandlesDue = (state, nowMs) => state.lastFugleCandlesAt === 0 || nowMs - state.lastFugleCandlesAt >= fugleCandleIntervalMs;

  // 收盤後手上這一筆是不是今天的收盤：13:30 之後抓的，而且成交時間到 13:30（收盤集合競價撮合）、
  // 或報價本來就是前一個交易日（今天休市）、或已經是 13:31 以後才抓的（沒有收盤撮合的冷門股，最後成交就是收盤）。
  // 13:30 剛過、撮合結果還沒出來時抓到的是收盤前的價，不算數，之後再抓。
  function closeSettled(state, nowMs) {
    const closeMs = sessionCloseMs(nowMs);
    const quote = state.quote;
    return Boolean(quote && state.fetchedAt >= closeMs && (quote.data_date < taipeiDate(nowMs)
      || Date.parse(quote.data_time) >= closeMs || state.fetchedAt >= closeMs + 60_000));
  }

  async function fetchFugle(code, market, state, nowMs, key, shouldQuote, shouldCandles) {
    try {
      if (shouldQuote) {
        state.lastAttempt = nowMs;
        const payload = await fugleRequest(fugleUrl("quote", code), key);
        rememberQuote(state, parseFugleQuote(payload, code, market), nowMs, fugleQuoteIntervalMs);
        state.provider = "fugle";
      }
      if (shouldCandles) await fetchFugleCandles(code, state, nowMs, key);
    } catch (error) {
      rememberError(state, error, nowMs, "Fugle");
    }
  }

  return {
    async snapshot(code, market, { allowFetch = true, fugleApiKey = null } = {}) {
      const nowMs = now();
      const state = stateFor(code, market);
      const scheduledOpen = marketWindow(nowMs);
      const provider = fugleApiKey ? "fugle" : "mis";
      const interval = fugleApiKey ? fugleQuoteIntervalMs : quoteIntervalMs;
      // 收盤後（交易日 13:30 以後；週末、開盤前不打上游）。
      const afterClose = !scheduledOpen && afterCloseToday(nowMs);
      if (scheduledOpen && allowFetch && nowMs >= state.retryAt) {
        const wantQuote = quoteDue(state, nowMs, interval);
        const wantCandles = Boolean(fugleApiKey) && fugleCandlesDue(state, nowMs);
        if (fugleApiKey && (wantQuote || wantCandles)) await fetchFugle(code, market, state, nowMs, fugleApiKey, wantQuote, wantCandles);
        else if (!fugleApiKey && wantQuote) await fetchMisQuote(code, market, state, nowMs);
      } else if (afterClose && allowFetch && nowMs >= state.retryAt) {
        // 收盤後到每日更新發布前（約 15:30／16:30），日資料還是前一天；今天的收盤報價要照樣給（本人 11-17）。
        // 抓到收盤（closeSettled）之後當天不重抓；之前照報價間隔重抓（lastAttempt 在 await 之前記，同時進來的請求不會一起打上游）。
        const wantQuote = !closeSettled(state, nowMs) && quoteDue(state, nowMs, interval);
        const wantCandles = Boolean(fugleApiKey) && fugleCandlesDue(state, nowMs);
        if (fugleApiKey && (wantQuote || wantCandles)) await fetchFugle(code, market, state, nowMs, fugleApiKey, wantQuote, wantCandles);
        else if (!fugleApiKey && wantQuote) await fetchMisQuote(code, market, state, nowMs);
      }
      const marketOpen = Boolean(scheduledOpen && state.quote?.data_date === taipeiDate(nowMs));
      const quoteAge = state.quote ? Math.max(0, (nowMs - Date.parse(state.quote.data_time)) / 1000) : null;
      const live = Boolean(marketOpen && quoteAge !== null && quoteAge <= FRESH_MS / 1000 && !state.error);
      const candles = {
        "1": aggregateCandles(state.points, 1), "5": aggregateCandles(state.points, 5),
        "15": aggregateCandles(state.points, 15), "60": state.candles60,
      };
      const delays = fugleApiKey
        ? { quote: fugleQuoteIntervalMs / 1000, chart: fugleCandleIntervalMs / 1000 }
        : { quote: quoteIntervalMs / 1000, chart: chartIntervalMs / 1000 };
      if (!scheduledOpen) {
        // quote 是最後一筆報價（交易日收盤後＝今天的收盤）；比日資料新才用，由前端看 data_date 決定。
        // close_pending：收盤後還沒拿到收盤（撮合結果還沒出來或上游失敗），前端照 retry 再問一次。
        return { ok: true, code, market, provider: state.provider || provider, market_open: false, market_phase: "closed",
          live: false, close_pending: Boolean(afterClose && !closeSettled(state, nowMs)),
          fetched_at: state.fetchedAt ? taipeiIso(state.fetchedAt) : null,
          delay_seconds: delays, retry_after_seconds: retrySeconds(state, nowMs), error: state.error,
          quote: state.quote, chart_samples: state.points.length, candles };
      }
      return {
        ok: Boolean(state.quote), code, market, provider: state.provider || provider, market_open: marketOpen,
        // 排定交易時段內卻沒有今天的報價：來源還沒換日，標 unknown 讓前端繼續輪詢。
        market_phase: marketOpen ? "open" : "unknown", live,
        fetched_at: state.fetchedAt ? taipeiIso(state.fetchedAt) : null,
        quote_age_seconds: quoteAge === null ? null : round(quoteAge, 3),
        delay_seconds: delays, retry_after_seconds: retrySeconds(state, nowMs), error: state.error,
        quote: state.quote, chart_samples: state.points.length, candles,
      };
    },
  };
}

// 雲端每分鐘盯價（watch-tick.js）用：查一檔的 Fugle 即時報價。失敗時丟出帶 status 的錯誤（429＝太頻繁）。
export async function fetchFugleQuote(code, market, key, fetchImpl = fetch) {
  const response = await fetchImpl(fugleUrl("quote", code).toString(), { headers: { accept: "application/json", "X-API-KEY": key } });
  if (!response.ok) {
    const error = new Error(`Fugle HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return parseFugleQuote(await response.json(), code, market);
}

export async function probeMis({ fetchImpl = fetch, now = Date.now } = {}) {
  const url = quoteUrl(PROBE_CODE, "tse");
  url.searchParams.set("_", String(now()));
  const response = await fetchImpl(url.toString(), {
    headers: { accept: "application/json,text/plain,*/*", referer: `${MIS_ORIGIN}/stock/`, "user-agent": "Market-Ana/1.0" },
  });
  if (!response.ok) throw new Error(`MIS HTTP ${response.status}`);
  const quote = parseQuote(await response.json(), PROBE_CODE, "tse");
  return { ok: true, code: quote.code, market: quote.market, price: quote.price, previous_close: quote.previous_close,
    data_time: quote.data_time, upstream_delay_ms: quote.upstream_delay_ms };
}

export const defaultLiveQuoteService = createLiveQuoteService();
export const _test = { aggregateCandles, marketWindow, parseQuote, parseFugleQuote, parseFugleCandles, quoteUrl, fugleUrl, taipeiIso };
