const FUGLE_ORIGIN = "https://api.fugle.tw";
const FUGLE_BASE = "/marketdata/v1.0/stock/intraday";
const INDEX_SYMBOL = "IX0001";
const QUOTE_TTL_MS = 15_000;
const CANDLES_TTL_MS = 60_000;
const FRESH_MS = 30_000;
const MAX_RETRY_MS = 120_000;
// 別的請求的抓取開始多久內，同時進來的請求先用上一筆、不重打上游；超過就當那次抓取卡住，自己抓。
// （發起的請求中途被取消時 finally 不會跑，pendingSince 會留著；這個上限就是為了讓它自己過期。）
const PENDING_MS = 10_000;

const finite = value => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value))
  ? Number(value) : null;
const finitePositive = value => {
  const parsed = finite(value);
  return parsed !== null && parsed > 0 ? parsed : null;
};
const rawAmount = value => {
  const parsed = finite(value);
  return parsed !== null && parsed > 0 ? parsed : null;
};
const round = (value, digits = 3) => Number(value.toFixed(digits));

function taipeiIso(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const get = type => parts.find(part => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}:${get("second")}+08:00`;
}

function sourceIso(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return null;
  return taipeiIso(numeric > 100_000_000_000_000 ? numeric / 1000 : numeric);
}

function taipeiDate(nowMs) {
  return taipeiIso(nowMs)?.slice(0, 10) || null;
}

function inSession(nowMs) {
  const local = new Date(nowMs + 8 * 60 * 60 * 1000);
  const seconds = local.getUTCHours() * 3600 + local.getUTCMinutes() * 60 + local.getUTCSeconds();
  return seconds >= 9 * 3600 && seconds < 13 * 3600 + 30 * 60;
}

function urlFor(kind) {
  const url = new URL(`${FUGLE_BASE}/${kind}/${INDEX_SYMBOL}`, FUGLE_ORIGIN);
  if (kind === "candles") {
    url.searchParams.set("timeframe", "1");
    url.searchParams.set("sort", "asc");
  }
  return url.toString();
}

function parseQuote(payload) {
  if (payload?.symbol !== INDEX_SYMBOL || payload?.type !== "INDEX") {
    throw new Error("Fugle 未回傳 IX0001 指數報價");
  }
  const isTrial = payload.isTrial === true;
  const price = finitePositive(payload.closePrice) ?? (!isTrial ? finitePositive(payload.lastPrice) : null);
  const previousClose = finitePositive(payload.previousClose);
  const dataTime = [payload.lastUpdated, payload.total?.time, payload.lastTrade?.time]
    .map(sourceIso).find(Boolean) || null;
  const date = typeof payload.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(payload.date)
    ? payload.date : null;
  if (price === null || previousClose === null || dataTime === null || date === null) {
    throw new Error("Fugle 指數報價缺少有效點位、前收、來源日或行情時間");
  }
  const change = finite(payload.change);
  const changePercent = finite(payload.changePercent);
  return {
    date, dataTime, isTrial, isClose: payload.isClose === true,
    data: {
      price, previous_close: previousClose,
      change: change ?? round(price - previousClose),
      change_percent: changePercent ?? round((price - previousClose) / previousClose * 100),
      open: finitePositive(payload.openPrice), high: finitePositive(payload.highPrice), low: finitePositive(payload.lowPrice),
      turnover_raw: rawAmount(payload.total?.tradeValue), turnover_unit: "unknown", turnover_billion: null,
    },
  };
}

function validateCandles(payload) {
  if (!Array.isArray(payload?.data)) throw new Error("Fugle 指數 1 分 K 格式不正確");
  return payload.data;
}

function projectCandles(rows, quoteDate) {
  return rows.map(row => {
    const timestamp = typeof row?.date === "string" && Number.isFinite(Date.parse(row.date)) ? row.date : null;
    const price = finitePositive(row?.close);
    if (!timestamp || price === null || timestamp.slice(0, 10) !== quoteDate) return null;
    return {
      timestamp, time: timestamp.slice(11, 16), price,
      open: finitePositive(row.open), high: finitePositive(row.high), low: finitePositive(row.low),
      volume: null, volume_raw: rawAmount(row.volume), volume_unit: "unknown",
    };
  }).filter(Boolean);
}

function parseRetryAfter(value, nowMs) {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const at = Date.parse(value);
  return Number.isFinite(at) ? Math.max(0, at - nowMs) : null;
}

async function requestJson(fetchImpl, url, key, nowMs) {
  const response = await fetchImpl(url, { headers: { accept: "application/json", "X-API-KEY": key } });
  if (!response.ok) {
    const error = new Error(`Fugle HTTP ${response.status}`);
    error.status = response.status;
    error.retryAfterMs = parseRetryAfter(response.headers.get("retry-after"), nowMs);
    throw error;
  }
  return response.json();
}

function sanitizedError(error, secret) {
  const message = error instanceof Error ? error.message : "Fugle 指數行情暫時無法取得";
  const safe = new Error(secret ? message.split(secret).join("[REDACTED]") : message);
  safe.status = error?.status;
  safe.retryAfterMs = error?.retryAfterMs;
  return safe;
}

function resourceState() {
  // 只放已完成的資料值；不放 promise（見 load 的說明）。
  return { value: null, valueSeq: 0, fetchedAt: 0, attempts: 0, pendingSince: 0, pendingSeq: 0,
    retryAt: 0, failures: 0, error: null };
}

function rememberError(state, error, nowMs) {
  state.failures += 1;
  const exponential = 5_000 * 2 ** Math.min(state.failures - 1, 5);
  const requested = error?.status === 429 && Number.isFinite(error.retryAfterMs)
    ? error.retryAfterMs : exponential;
  const delay = Math.min(MAX_RETRY_MS, Math.max(1_000, requested));
  state.retryAt = nowMs + delay;
  state.error = error instanceof Error ? error.message : "Fugle 指數行情暫時無法取得";
}

function retrySeconds(states, nowMs) {
  return Math.max(0, ...states.map(state => Math.ceil((state.retryAt - nowMs) / 1000)));
}

export function createIndexLiveService({ fetchImpl = fetch, now = Date.now,
  quoteTtlMs = QUOTE_TTL_MS, candlesTtlMs = CANDLES_TTL_MS } = {}) {
  const quoteState = resourceState();
  const candleState = resourceState();

  // 這個 service 是模組層物件，同一個 isolate 的所有請求共用。Workers 不准一個請求去等別的請求建立的
  // promise（建立它的請求一結束，其他人拿到「This promise was created by a request that has since ended」），
  // 所以跨請求只共用已經完成的資料值（value、時間、退避），進行中的抓取永遠只由發起它的請求自己 await。
  async function load(state, kind, ttlMs, key, parser, nowMs) {
    if (state.value !== null && nowMs - state.fetchedAt < ttlMs) return state.value;
    if (nowMs < state.retryAt) {
      if (state.value !== null) return state.value;
      throw new Error(state.error || "Fugle 指數行情退避中");
    }
    // 別的請求正在抓、手上又有上一筆：先用上一筆，不重複打上游（也不去等那個請求的 promise）。
    if (state.value !== null && state.pendingSince && nowMs - state.pendingSince < PENDING_MS) return state.value;
    const seq = ++state.attempts;
    state.pendingSince = nowMs;
    state.pendingSeq = seq;
    try {
      const value = parser(await requestJson(fetchImpl, urlFor(kind), key, nowMs));
      // 較晚發起的抓取已先成功時，不用較舊的結果蓋掉它。
      if (seq > state.valueSeq) {
        state.value = value;
        state.valueSeq = seq;
        state.fetchedAt = now();
        state.retryAt = 0;
        state.failures = 0;
        state.error = null;
      }
      return state.value;
    } catch (error) {
      const safe = sanitizedError(error, key);
      // 較晚發起的抓取已經成功，這次較舊的失敗不必記成錯誤或觸發退避。
      if (state.valueSeq < seq) rememberError(state, safe, nowMs);
      if (state.value !== null) return state.value;
      throw safe;
    } finally {
      if (state.pendingSeq === seq) state.pendingSince = 0;
    }
  }

  function unavailable(nowMs, error, retryAfter = 0) {
    const phase = inSession(nowMs) ? "unknown" : "closed";
    return {
      ok: false, source: "fugle-index", symbol: INDEX_SYMBOL,
      served_at: taipeiIso(nowMs), fetched_at: null, data_time: null,
      market_open: false, market_phase: phase, phase, live: false,
      delay: "unavailable", delay_seconds: { quote: quoteTtlMs / 1000, chart: candlesTtlMs / 1000 },
      retry_after_seconds: retryAfter, cache_age_seconds: null, retryable: phase === "unknown",
      error, warning: null, data: null, series: [],
    };
  }

  return {
    async snapshot({ fugleApiKey, allowFetch = true } = {}) {
      const nowMs = now();
      if (!fugleApiKey) return unavailable(nowMs, "FUGLE_API_KEY 未設定");

      let localError = null;
      if (allowFetch) {
        const [quoteResult, candleResult] = await Promise.allSettled([
          load(quoteState, "quote", quoteTtlMs, fugleApiKey, parseQuote, nowMs),
          load(candleState, "candles", candlesTtlMs, fugleApiKey, validateCandles, nowMs),
        ]);
        localError = [quoteResult, candleResult].find(result => result.status === "rejected")?.reason || null;
      } else if (quoteState.value === null) {
        localError = new Error("本次禁止連線且沒有快取行情");
      }

      const retryAfter = retrySeconds([quoteState, candleState], nowMs);
      if (quoteState.value === null) {
        return unavailable(nowMs,
          localError instanceof Error ? localError.message : quoteState.error || "Fugle 指數行情暫時無法取得",
          retryAfter);
      }

      const quote = quoteState.value;
      const series = candleState.value === null ? [] : projectCandles(candleState.value, quote.date);
      const sourceAgeMs = nowMs - Date.parse(quote.dataTime);
      const sessionActive = inSession(nowMs);
      const sourceDateCurrent = quote.date === taipeiDate(nowMs);
      const marketOpen = sessionActive && sourceDateCurrent && !quote.isClose;
      const errors = [localError?.message, quoteState.error, candleState.error].filter(Boolean);
      const hasError = errors.length > 0;
      const live = marketOpen && !quote.isTrial && !hasError && sourceAgeMs >= -5_000 && sourceAgeMs <= FRESH_MS;
      const phase = marketOpen ? (live ? "open" : "delayed")
        // 來源日期不是今天時，isClose 講的是前一個交易日，不能拿來判定今天休市。
        : sessionActive && !sourceDateCurrent ? "unknown" : "closed";
      const warnings = [];
      if (quote.isTrial) warnings.push("Fugle 試撮資料，不標示即時");
      if (sourceAgeMs < -5_000) warnings.push("來源行情時間位於未來，不標示即時");
      if (sourceAgeMs > FRESH_MS && marketOpen) warnings.push("來源行情已延遲");
      if (hasError) warnings.push("上游更新失敗，保留最後成功資料並繼續重試");
      return {
        ok: true, source: "fugle-index", symbol: INDEX_SYMBOL,
        served_at: taipeiIso(nowMs), fetched_at: taipeiIso(quoteState.fetchedAt), data_time: quote.dataTime,
        chart_fetched_at: candleState.fetchedAt ? taipeiIso(candleState.fetchedAt) : null,
        market_open: marketOpen, market_phase: phase, phase, live,
        delay: live ? "live" : phase, delay_seconds: { quote: quoteTtlMs / 1000, chart: candlesTtlMs / 1000 },
        retry_after_seconds: retryAfter,
        cache_age_seconds: round(Math.max(0, nowMs - quoteState.fetchedAt) / 1000),
        retryable: Boolean(marketOpen || phase === "unknown" || (sessionActive && (hasError || retryAfter > 0))),
        error: errors[0] || null, warning: warnings.join("；") || null,
        data: quote.data, series,
      };
    },
  };
}

export const defaultIndexLiveService = createIndexLiveService();
