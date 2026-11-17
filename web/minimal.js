(async () => {
  'use strict';

  // 票 06：資料一律經 window.MarketData（本機讀全域、雲端打 API），畫面邏輯不分本機或雲端。
  let OV = null;
  try {
    OV = window.MarketData ? await window.MarketData.overview() : null;
  } catch (error) {
    const note = document.getElementById('serviceNote');
    // 雲端資料層（MarketDataError，帶 kind）的中文說明可以直接顯示；其他原文（例如 JSON 解析錯誤）只進 console。
    console.warn('[資料讀取]', error);
    if (note) { note.hidden = false; note.textContent = error && error.kind ? error.message : '暫時無法取得資料，請稍後再試。'; }
    return;
  }
  const A = OV && OV.analysis;
  const D = OV && OV.dashboard;
  let features = OV?.features || { market_trend_v1: false };
  if (!OV?.features && location.protocol !== 'file:') {
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const payload = response.ok ? await response.json() : null;
      if (payload?.schema === 'market-ana-features/v1' && payload.features) features = payload.features;
    } catch (_) { /* feature gate 讀取失敗時維持 default-off */ }
  }
  const root = document.documentElement;
  const $ = id => document.getElementById(id);
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isMobile = () => matchMedia('(max-width: 760px)').matches;
  // 收復／防守的線畫在 K 線上，名稱與數字在收復線／防守線鍵（本人 11-17 起右側價格欄不掛它們的價籤），左側不再留浮鈕欄。
  const marketLabelRail = () => 4;
  const outcomeLabels = {
    stand_back: '站回收復線',
    between: '兩線之間',
    continue_break: '跌破防守線'
  };
  const periodLabels = {
    m1: '1 分K',
    m5: '5 分K',
    m15: '15 分K',
    h60: '60 分K',
    daily: '日K',
    weekly: '週K',
    monthly: '月K'
  };
  const minutePeriods = new Set(['m1', 'm5', 'm15']);
  const isMinutePeriod = period => minutePeriods.has(period);

  const els = {
    price: $('currentPrice'),
    change: $('currentChange'),
    state: $('marketState'),
    stateButton: $('statusTrigger'),
    time: $('marketTime'),
    analysisDate: $('analysisDate'),
    scenarioDate: $('scenarioDate'),
    zone: $('zoneLabel'),
    trendSection: $('marketTrend'),
    trendDate: $('marketTrendDate'),
    trendRows: $('marketTrendRows'),
    service: $('serviceNote'),
    chartContext: $('chartContext'),
    chartStage: $('chartStage'),
    chartActions: $('chartActions'),
    chartHost: $('mainChart'),
    chartSummary: $('chartSummary'),
    period: $('periodSelect'),
    reset: $('resetView'),
    forming: $('formingBadge'),
    provisional: $('provisionalBadge'),
    estimate: $('estimateBadge'),
    upper: $('upperValue'),
    lower: $('lowerValue'),
    lineUpper: $('lineUpper'),
    lineLower: $('lineLower'),
    point: $('currentPoint'),
    marker: $('selectedCandleMarker'),
    mobileDetails: $('mobileCandleDetails'),
    sampleCount: $('sampleCount'),
    distributionBar: $('distributionBar'),
    backdrop: $('panelBackdrop'),
    panel: $('detailPanel'),
    panelTitle: $('panelTitle'),
    panelPath: $('panelPath'),
    panelBody: $('panelBody'),
    panelNav: $('panelNav'),
    panelBack: $('panelBack'),
    panelClose: $('panelClose')
  };

  const api = {
    ready: false,
    get chart() { return chart; },
    get selectedIndex() { return selectedIndex; },
    get selectedSample() { return selectedSample; },
    get nextPollDelay() { return nextPollDelay; },
    get trend() { return renderedTrend; },
    get marketSnapshot() { return officialMarketSnapshot; }
  };
  window.MinimalApp = api;

  if (!A || !D || !A.backtest || !window.KChart) {
    els.service.hidden = false;
    els.service.textContent = '資料驗證失敗：缺少盤後分析、劇本或圖表元件。';
    return;
  }

  const AS_OF = A.backtest.as_of || {};
  // 收復線、防守線：每日更新照規則從當天的 K 線算出來（回測的截止日資料），每天都有，優先讀這一份；
  // 人寫的劇本裡也抄了一份同樣的數字，只有在資料包還沒有帶算出來的那一份時才退回去讀它（本人 2026-11-15：線與圖不等人寫劇本）。
  const isNumber = value => value !== null && value !== '' && Number.isFinite(Number(value));
  const numberOr = value => (isNumber(value) ? Number(value) : NaN);   // null、空字串不當成 0
  const linesDaily = isNumber(AS_OF.upper) && isNumber(AS_OF.lower);
  const scenarioThresholds = D.thresholds || {};
  const UPPER = numberOr(linesDaily ? AS_OF.upper : scenarioThresholds.upper);
  const LOWER = numberOr(linesDaily ? AS_OF.lower : scenarioThresholds.lower);
  const ACTIONS = D.actions || (D.forecast && D.forecast.actions) || {};
  const CLOSE = Number(AS_OF.close || (D.latest && D.latest.close));
  // 預設用哪一組比較樣本：也是每天照當天的型態定的，先讀算出來的；人寫的那一份可能是好幾天前的型態
  const DEFAULT_SAMPLE = AS_OF.default_sample || D.default_sample || (A.backtest.samples[0] && A.backtest.samples[0].key);
  const scenarioDate = (() => {
    const horizon = String(D.forecast && D.forecast.horizon || '');
    const match = horizon.match(/\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : String(D.expires_at || D.forecast && D.forecast.expiry || '').slice(0, 10);
  })();
  const scenarioExpired = Boolean(scenarioDate && A.meta.cutoff_date && scenarioDate < A.meta.cutoff_date);
  // 本人 2026-11-14「過期就隱藏」：劇本與直白分析是人寫的，不會跟每日資料一起更新，比行情資料日舊時不顯示，不拿舊解讀配新數字。
  // 2026-11-15 本人同意把範圍縮小：過期只藏人寫的文字（scenarioHidden：劇本日期、怎麼應對、觸發條件、解讀）。
  // 人寫的那幾句講的是「它那一天的兩條線」：線已經換成新一天算的（數字和劇本裡抄的不一樣）時，就算劇本的適用日還沒過也不能顯示——
  // 劇本的適用日當天收盤後，資料更新到那一天，線就換了，劇本還要到隔天才算過期。
  const sameLine = (a, b) => isNumber(a) && Math.abs(Number(a) - b) < 0.005;
  const scenarioHidden = scenarioExpired || (linesDaily && !(sameLine(scenarioThresholds.upper, UPPER) && sameLine(scenarioThresholds.lower, LOWER)));
  // 兩條線與靠它們算的東西每天都有。不顯示的情況只有三種：
  // 兩邊都沒有數字（資料包壞了，不畫出「NaN」）；線還是從過期的劇本抄來的（資料包還沒帶算出來的那一份）；
  // 行情已經走到線適用的那一天之後（每日更新沒跑成，見 setMarketHeader 裡的 linesOutdated）。
  const linesMissing = !(Number.isFinite(UPPER) && Number.isFinite(LOWER)) || (!linesDaily && scenarioExpired);
  let linesOutdated = false;
  const linesHidden = () => linesMissing || linesOutdated;
  // 線是哪一天畫的：那一天就是今天才寫「今天」，不然寫日期（line-zones.js 的 dayOf）
  const drawnDay = () => window.LineZones.dayOf(AS_OF.date || A.meta.cutoff_date || '', new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' }));
  const plainSource = OV && OV.plain || null;
  const plainStale = Boolean(plainSource && plainSource.data_dates && A.meta.cutoff_date
    && String(plainSource.data_dates.price_and_foreign || '') < A.meta.cutoff_date);

  let chart = null;
  let currentPeriod = 'daily';
  let latestPayload = null;
  let latestLive = false;
  let selectedIndex = -1;
  let selectedSample = DEFAULT_SAMPLE;
  let activeAnchor = null;
  let panelAnchor = null;
  let closeTimer = 0;
  let openFrame = 0;
  let pollTimer = 0;
  let polling = false;
  let nextPollDelay = 0;
  let renderedTrend = null;
  let panelState = { kind: '', payload: null, layer: 1, subkind: '' };
  let panelTrail = [];
  let panelRestoreFrame = 0;

  const esc = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const fmt = (value, digits = 2) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : '—';

  const liveAmount = value => typeof value === 'number' && Number.isFinite(value) ? value : null;

  const signed = (value, digits = 2) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return `${n > 0 ? '+' : n < 0 ? '−' : ''}${fmt(Math.abs(n), digits)}`;
  };

  const pct = value => Number.isFinite(Number(value)) ? `${fmt(value, 2)}%` : '—';
  const datePart = value => String(value || '').slice(0, 10);
  const periodDataDate = candle => datePart(candle && (candle.t || candle.date));

  function formatStamp(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return String(value);
    return parsed.toLocaleString('zh-TW', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false, timeZone: 'Asia/Taipei'
    });
  }

  function css(name, fallback) {
    return getComputedStyle(root).getPropertyValue(name).trim() || fallback;
  }

  // 現價落在哪一區：規則和收復線／防守線那張圖是同一個（line-zones.js）
  const ZONE_NAMES = { stand_back: { id: 'up', label: '站回收復線' }, between: { id: 'mid', label: '兩線之間' }, continue_break: { id: 'down', label: '跌破防守線' } };
  function zoneFor(price) {
    return ZONE_NAMES[window.LineZones.zoneOf(Number(price), UPPER, LOWER)] || { id: 'unknown', label: '尚未取得' };
  }

  function buildOfficialMarketSnapshot() {
    const rows = Array.isArray(A.daily) ? A.daily : [];
    const latest = rows[rows.length - 1];
    const previous = rows[rows.length - 2];
    const latestValues = latest && [latest.open, latest.high, latest.low, latest.close, latest.amount].map(Number);
    const previousClose = Number(previous && previous.close);
    if (latest && latestValues && latestValues.every(Number.isFinite) && Number.isFinite(previousClose) && previousClose !== 0) {
      const change = latestValues[3] - previousClose;
      return {
        date: datePart(latest.date),
        source: 'analysis-daily',
        data: {
          price: latestValues[3], previous_close: previousClose, change,
          change_percent: change / previousClose * 100,
          open: latestValues[0], high: latestValues[1], low: latestValues[2],
          turnover_billion: latestValues[4] / 100_000_000
        }
      };
    }
    const dashboard = D.latest || {};
    const close = Number(dashboard.close);
    const change = Number(dashboard.change);
    return {
      date: datePart(D.market_date), source: 'dashboard',
      data: {
        price: close,
        previous_close: Number.isFinite(close) && Number.isFinite(change) ? close - change : null,
        change,
        change_percent: Number(dashboard.change_percent),
        open: null,
        high: Number(dashboard.high),
        low: Number(dashboard.low),
        turnover_billion: Number(dashboard.turnover)
      }
    };
  }

  const officialMarketSnapshot = buildOfficialMarketSnapshot();
  const fallbackMarketDate = () => officialMarketSnapshot.date || A.meta.cutoff_date || D.market_date || '—';
  const fallbackMarketData = () => officialMarketSnapshot.data;

  // 圖旁兩個價位按鈕與隔日分布：有線才顯示（線過時與否會跟著即時行情變，所以做成可以重複呼叫的）
  function showLineKeys() {
    for (const node of [els.lineUpper, els.lineLower, $('distribution')]) {
      if (node) node.hidden = linesHidden();
    }
    // 報價與行情狀態點了是開收復線那一張：線收起來時沒有東西可開，「詳情 ›」的提示也跟著收（data-no-detail，樣式在 panel.css）
    for (const node of [$('priceSummary'), els.stateButton]) {
      if (!node) continue;
      node.toggleAttribute('data-no-detail', linesHidden());
      node.setAttribute('aria-disabled', String(linesHidden()));
    }
  }

  let displayedMarketData = null;
  function currentMarketData() {
    if (displayedMarketData) return displayedMarketData;
    return latestPayload && latestPayload.data ? latestPayload.data : fallbackMarketData();
  }

  function setMarketHeader(data, state, stamp) {
    displayedMarketData = data;
    const price = Number(data.price);
    const change = Number(data.change);
    const changePercent = Number(data.change_percent);
    els.price.textContent = fmt(price);
    els.change.textContent = `${signed(change)}（${signed(changePercent)}%）`;
    els.change.className = `price-change num ${change > 0 ? 'is-up' : change < 0 ? 'is-down' : ''}`.trim();
    els.state.textContent = state.label;
    els.stateButton.dataset.state = state.id;
    els.time.textContent = stamp || fallbackMarketDate();
    // 兩條線是照截止日那一天的收盤畫的，給下一個交易日用。即時行情的「昨收」已經不是那一天的收盤，
    // 代表中間又過了交易日、每日更新卻沒跑成：線過時了，先收起來，不拿好幾天前的線配今天的價。
    // （畫面上是盤後資料本身、或即時行情沒給昨收時不判斷。差一點以內當成同一個收盤，各來源的小數位數不同。）
    const outdated = data !== fallbackMarketData() && isNumber(data.previous_close) && Number.isFinite(CLOSE) && Math.abs(Number(data.previous_close) - CLOSE) > 1;
    if (outdated !== linesOutdated) { linesOutdated = outdated; showLineKeys(); }
    els.zone.textContent = linesHidden() ? '' : zoneFor(price).label;
    const shortDate = value => (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) ? `${value.slice(5, 7)}/${value.slice(8, 10)}` : (value || '—');
    els.analysisDate.textContent = shortDate(A.meta.cutoff_date || fallbackMarketDate());
    els.scenarioDate.textContent = shortDate(scenarioDate);
    els.analysisDate.title = A.meta.cutoff_date || fallbackMarketDate();
    els.scenarioDate.title = scenarioDate || '';
  }

  function setOffline(message) {
    latestPayload = null;
    latestLive = false;
    els.service.hidden = false;
    els.service.textContent = message;
    setMarketHeader(fallbackMarketData(), { id: 'closed', label: '即時服務未啟動' }, fallbackMarketDate());
    setIntradayOptions(false);
    loadPeriod(isMinutePeriod(currentPeriod) ? 'daily' : currentPeriod, true);
  }

  function setIntradayOptions(enabled) {
    [...els.period.options].forEach(option => {
      if (isMinutePeriod(option.value)) option.disabled = !enabled;
    });
  }

  function toChartCandle(row, unit) {
    const date = row.date || row.timestamp || row.end;
    const label = unit === '60分'
      ? `${datePart(date).slice(5).replace('-', '/')} ${String(row.time || row.timestamp || '').slice(11, 16)}`
      : row.label || String(date || '').slice(5).replace('-', '/');
    const rawAmount = row.amount ?? row.turnover;
    return {
      t: date,
      label,
      open: Number.isFinite(row.open) ? row.open : null,
      high: Number.isFinite(row.high) ? row.high : null,
      low: Number.isFinite(row.low) ? row.low : null,
      close: Number.isFinite(row.close) ? row.close : null,
      amount: Number.isFinite(rawAmount) ? rawAmount / 100_000_000 : null
    };
  }

  function clock(minute) {
    return `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;
  }

  function buildIntradayCandles(rows, data, size) {
    const startMinute = 9 * 60;
    const points = (Array.isArray(rows) ? rows : [])
      .map(row => {
        const stamp = String(row && row.timestamp || '');
        const match = stamp.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})/);
        const price = Number(row && row.price);
        if (!match || !Number.isFinite(price)) return null;
        const seconds = Number(match[2]) * 3600 + Number(match[3]) * 60 + Number(match[4]);
        return { day: match[1], stamp, endMinute: Math.ceil(seconds / 60), price };
      })
      .filter(Boolean)
      .sort((a, b) => a.stamp.localeCompare(b.stamp));
    if (!points.length) return [];
    const day = points[points.length - 1].day;
    const buckets = new Map();
    points.filter(point => point.day === day).forEach(point => {
      const index = Math.max(0, Math.floor((point.endMinute - startMinute - 1) / size));
      if (!buckets.has(index)) buckets.set(index, []);
      buckets.get(index).push(point.price);
    });
    let previousClose = Number.isFinite(Number(data.open)) ? Number(data.open) : null;
    return [...buckets.keys()].sort((a, b) => a - b).map(index => {
      const prices = buckets.get(index);
      const open = previousClose ?? prices[0];
      const close = prices[prices.length - 1];
      previousClose = close;
      const start = startMinute + index * size;
      const span = size > 1 ? `${clock(start)}–${clock(start + size)}` : clock(start);
      return {
        t: `${day}T${clock(start)}:00+08:00`,
        label: `${day.slice(5).replace('-', '/')} ${span}`,
        open,
        high: Math.max(open, ...prices),
        low: Math.min(open, ...prices),
        close,
        amount: null
      };
    });
  }

  // 收盤後、正式日K還沒更新前：用即時報價（MIS）今天的開高低收暫補一根，並標示是暫用值。
  // 只在 MIS 的日期比正式日K最後一天新時才補；每日更新把正式日K補上後，日期相同就不再補，自然以正式資料為準。
  function provisionalCloseCandle(candles) {
    if (!latestPayload || !latestPayload.ok || latestPayload.market_open || !latestPayload.data) return null;
    const data = latestPayload.data;
    const day = datePart(latestPayload.data_time);
    const lastDay = periodDataDate(candles[candles.length - 1]);
    if (!day || !lastDay || day <= lastDay) return null;
    const ohlc = [data.open, data.high, data.low, data.price].map(Number);
    if (!ohlc.every(Number.isFinite)) return null;
    return {
      t: day,
      label: `${day.slice(5).replace('-', '/')}（盤後暫用即時收盤）`,
      open: ohlc[0], high: ohlc[1], low: ohlc[2], close: ohlc[3],
      amount: liveAmount(data.turnover_billion),
      provisional: true
    };
  }

  function dailyWithLive() {
    const candles = window.ChartStudies.withMarketInstitutional(A.daily.map(row => toChartCandle(row, '日')), A.institutional);
    const provisional = provisionalCloseCandle(candles);
    if (provisional) { candles.push(provisional); return candles; }
    if (!latestPayload || !latestPayload.data || !latestLive) return candles;
    const data = latestPayload.data;
    const day = datePart(latestPayload.data_time);
    if (!day) return candles;
    const live = {
      t: day,
      label: `${day.slice(5).replace('-', '/')}（盤中）`,
      open: Number(data.open),
      high: Number(data.high),
      low: Number(data.low),
      close: Number(data.price),
      amount: liveAmount(data.turnover_billion)
    };
    const lastDay = periodDataDate(candles[candles.length - 1]);
    if (day === lastDay) candles[candles.length - 1] = live;
    else if (!lastDay || day > lastDay) candles.push(live);
    return candles;
  }

  function candlesFor(period) {
    if (period === 'm1' || period === 'm5' || period === 'm15') {
      const minutes = Number(period.slice(1));
      return latestPayload && latestPayload.data
        ? buildIntradayCandles(latestPayload.series, latestPayload.data, minutes)
        : [];
    }
    if (period === 'h60') return A.h60.map(row => toChartCandle(row, '60分'));
    if (period === 'weekly') return A.weekly.map(row => toChartCandle(row, '週'));
    if (period === 'monthly') return A.monthly.map(row => toChartCandle(row, '月'));
    return dailyWithLive();
  }

  // 收復線、防守線只畫線、右側價格欄不掛價籤（tag:false；本人 11-17：右側只放簡單的數字）。
  // 名稱與數字由圖表上的收復線／防守線鍵標示（桌面在圖左上角當圖例、手機在圖下方），顏色和線相同。
  function overlays() {
    if (linesHidden()) return [];
    return [
      { value: UPPER, label: '收復線', short: '收復', color: css('--gold', '#d9a94b'), pos: 'above', tag: false },
      { value: LOWER, label: '防守線', short: '防守', color: css('--down', '#2fd09a'), pos: 'below', tag: false }
    ];
  }

  // 圖上的判斷線：每天算的收復線、防守線，再加預測帳本的轉強線／轉弱線（forecast.js；只有站主有內容時才有）。
  function chartOverlays() {
    return [...overlays(), ...(window.MarketForecast ? window.MarketForecast.overlays(currentPeriod) : [])];
  }

  // 規則的比法只有一份，在 trend-votes.js
  const trendSide = (left, right) => window.TrendVotes.sideOf(left, right);

  function trendState(score) {
    if (score >= 2) return ['bullish', '偏多'];
    if (score <= -2) return ['bearish', '偏空'];
    return ['neutral', '盤整'];
  }

  function trendReturn(values, bars) {
    if (values.length <= bars || !Number.isFinite(values.at(-1)) || !Number.isFinite(values.at(-bars - 1)) || values.at(-bars - 1) === 0) return null;
    return (values.at(-1) / values.at(-bars - 1) - 1) * 100;
  }

  function trendInstitutional(rows, count, cutoff) {
    const values = (rows || []).filter(row => String(row.date || '') <= cutoff).map(row => {
      const foreign = Number(row.foreign && row.foreign.net);
      const trust = Number(row.trust && row.trust.net);
      return (Number.isFinite(foreign) ? foreign : 0) + (Number.isFinite(trust) ? trust : 0);
    });
    return values.length < count ? null : values.slice(-count).reduce((sum, value) => sum + value, 0) / 100_000_000;
  }

  function localMarketTrend(instance) {
    if (window.MarketData && window.MarketData.source !== 'local') return null;
    const candles = A.daily.map(row => toChartCandle(row, '日'));
    const closes = candles.map(row => row.close);
    const ma = Object.fromEntries([5, 10, 20, 60, 120, 240].map(period => [period, window.KChartMath.sma(closes, period)]));
    const macd = window.KChartMath.macd(closes);
    const kd = instance.studies && instance.studies.kd || [];
    const rsi = instance.studies && instance.studies.rsi || [];
    const dataDate = A.meta.cutoff_date || periodDataDate(candles.at(-1));
    const close = closes.at(-1);
    const periods = [];
    const insufficient = (key, label, need) => ({ key, label, state: 'insufficient', conclusion: '資料不足',
      sentence: `資料不足：需要至少 ${need} 根完整日 K，目前只有 ${candles.length} 根。`, metrics: {}, data_date: dataDate });

    if (candles.length < 49 || !macd.at(-1)?.ready || !kd.at(-1)?.ready || !rsi.at(-1)?.ready) {
      periods.push(insufficient('short', '短期', 49));
    } else {
      const ma5 = ma[5].at(-1), ma10 = ma[10].at(-1), hist = macd.at(-1).hist;
      const k = kd.at(-1).k, d = kd.at(-1).d, rsi14 = rsi.at(-1).rsi;
      const amounts = candles.slice(-20).map(row => row.amount).filter(Number.isFinite);
      const volumeRatio = amounts.length === 20 && amounts.reduce((sum, value) => sum + value, 0) > 0
        ? candles.at(-1).amount / (amounts.reduce((sum, value) => sum + value, 0) / 20) : null;
      const inst5 = trendInstitutional(A.institutional, 5, dataDate);
      const [state, conclusion] = trendState([trendSide(close, ma5), trendSide(ma5, ma10), trendSide(hist, 0), trendSide(k, d), trendSide(rsi14, 50)].reduce((a, b) => a + b, 0));
      const rsiText = Number.isFinite(rsi14) ? `RSI ${fmt(rsi14, 1)}` : 'RSI 無方向';
      periods.push({ key: 'short', label: '短期', state, conclusion, data_date: dataDate,
        sentence: `${conclusion}：收盤 ${fmt(close)} ${trendSide(close, ma5) > 0 ? '高於' : trendSide(close, ma5) < 0 ? '低於' : '等於'} MA5 ${fmt(ma5)}，MACD 柱 ${signed(hist)}，KD ${fmt(k, 1)}／${fmt(d, 1)}，${rsiText}；量比 ${fmt(volumeRatio)} 倍，5 日法人 ${signed(inst5, 1)} 億。`,
        metrics: { close, ma5, ma10, macd_hist: hist, kd_k: k, kd_d: d, rsi14, volume_ratio_20d: volumeRatio, institutional_5d_billion: inst5 } });
    }

    if (candles.length < 60 || !Number.isFinite(ma[20].at(-1)) || !Number.isFinite(ma[60].at(-1))) {
      periods.push(insufficient('medium', '中期', 60));
    } else {
      const ma20 = ma[20].at(-1), ma60 = ma[60].at(-1), ret20 = trendReturn(closes, 20);
      const inst20 = trendInstitutional(A.institutional, 20, dataDate);
      const [state, conclusion] = trendState([trendSide(close, ma20), trendSide(ma20, ma60), trendSide(ret20, 0), trendSide(inst20, 0)].reduce((a, b) => a + b, 0));
      periods.push({ key: 'medium', label: '中期', state, conclusion, data_date: dataDate,
        sentence: `${conclusion}：收盤 ${fmt(close)} ${trendSide(close, ma20) > 0 ? '高於' : trendSide(close, ma20) < 0 ? '低於' : '等於'} MA20 ${fmt(ma20)}，MA20 ${trendSide(ma20, ma60) > 0 ? '高於' : trendSide(ma20, ma60) < 0 ? '低於' : '等於'} MA60 ${fmt(ma60)}，近 20 日 ${signed(ret20, 1)}%；20 日法人 ${signed(inst20, 1)} 億。`,
        metrics: { close, ma20, ma60, return_20d_pct: ret20, institutional_20d_billion: inst20 } });
    }

    if (candles.length < 240 || !Number.isFinite(ma[240].at(-1))) {
      periods.push(insufficient('long', '長期', 240));
    } else {
      const ma60 = ma[60].at(-1), ma120 = ma[120].at(-1), ma240 = ma[240].at(-1), ret60 = trendReturn(closes, 60);
      const [state, conclusion] = trendState([trendSide(close, ma120), trendSide(ma60, ma120), trendSide(ma120, ma240), trendSide(ret60, 0)].reduce((a, b) => a + b, 0));
      periods.push({ key: 'long', label: '長期', state, conclusion, data_date: dataDate,
        sentence: `${conclusion}：收盤 ${fmt(close)} ${trendSide(close, ma120) > 0 ? '高於' : trendSide(close, ma120) < 0 ? '低於' : '等於'} MA120 ${fmt(ma120)}，MA60 ${fmt(ma60)}／MA120 ${fmt(ma120)}／MA240 ${fmt(ma240)}，近 60 日 ${signed(ret60, 1)}%。`,
        metrics: { close, ma60, ma120, ma240, return_60d_pct: ret60 } });
    }
    return { schema: 'market-trend/v1', rule_version: 'market-trend-v1', data_date: dataDate, periods };
  }

  function renderMarketTrend(instance) {
    els.trendSection.hidden = !features.market_trend_v1;
    if (!features.market_trend_v1) { renderedTrend = null; return; }
    const packaged = D.actions && D.actions.market_trend || A.market_trend;
    const trend = packaged && packaged.schema === 'market-trend/v1' ? packaged : localMarketTrend(instance);
    renderedTrend = trend;
    if (!els.trendDate || !els.trendRows) return;
    els.trendDate.textContent = `資料日 ${(trend && trend.data_date) || A.meta.cutoff_date || '—'}`;
    // 每一票與最上面那一句由 trend-votes.js 讀好，畫面由 trend-matrix.js 畫；同一份資料重複叫不會重畫
    window.TrendMatrix.render(els.trendSection, trend ? window.TrendVotes.read(trend) : null);
  }

  function ensureChart() {
    if (chart) return chart;
    chart = new window.KChart(els.chartHost, {
      liveLast: false,
      macd: false,
      studyHost: document.getElementById('chartStudies'),
      studies: ['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'],
      selectionRoots: [els.chartStage.parentElement, els.panel],
      volumeKind: 'amount',
      leftRail: marketLabelRail,
      overlayLabels: 'tags',
      minViewCount: 10,
      onSelect: (index, anchor) => selectCandle(index, anchor, false),
      onClear: () => { selectedIndex = -1; clearedByChart = true; els.marker.hidden = true; els.mobileDetails.hidden = true; },
      onView: (_first, _last, atEnd) => {
        els.reset.hidden = atEnd;
        requestAnimationFrame(updateOverlayControls);
      },
      // 扣掉畫布上方保留給圖例的高度（chart-host 的 top）
      height: () => Math.max(250, (els.chartStage.clientHeight || 360) - (els.chartHost.offsetTop || 0))
    });
    chart.canvas.setAttribute('aria-describedby', 'chartSummary');
    bindCandleSelection();
    chart.canvas.addEventListener('wheel', () => requestAnimationFrame(enforceCandleHitTarget));
    chart.canvas.addEventListener('pointerup', () => requestAnimationFrame(enforceCandleHitTarget));
    chart.canvas.addEventListener('keydown', () => requestAnimationFrame(enforceCandleHitTarget));
    return chart;
  }

  function loadPeriod(period, reset = true) {
    const candles = candlesFor(period);
    if (!candles.length && isMinutePeriod(period)) {
      currentPeriod = 'daily';
      els.period.value = 'daily';
      return loadPeriod('daily', reset);
    }
    currentPeriod = period;
    els.period.value = period;
    els.period.dispatchEvent(new Event('periodsync')); // 週期小鍵與選單（period-switch.js）同步
    selectedIndex = -1;
    els.mobileDetails.hidden = true;
    els.marker.hidden = true;
    const unit = period === 'h60' ? '60分' : period === 'weekly' ? '週' : period === 'monthly' ? '月' : isMinutePeriod(period) ? `${period.slice(1)}分` : '日';
    const liveLast = latestLive && (isMinutePeriod(period) || period === 'daily');
    const instance = ensureChart();
    instance.opts.liveLast = liveLast;
    instance.showMacd = period !== 'daily' && !isMobile();
    const desiredCount = period === 'monthly' ? 60 : period === 'weekly' ? 70 : period === 'h60' ? 80 : isMinutePeriod(period) ? Math.min(candles.length, 120) : 70;
    // 右側價格欄寬：軸上價籤模式（收盤與預測線）約 78～100px，取已畫過的實際值；第一次畫之前用 100 保守估。
    const priceRail = instance.geo ? Math.max(64, instance.canvas.clientWidth - instance.geo.l - instance.geo.pw) : 100;
    const plotWidth = Math.max(0, els.chartStage.clientWidth - marketLabelRail() - priceRail);
    const touchSafeCount = Math.max(10, Math.floor(plotWidth / 44));
    const count = Math.min(candles.length, isMobile() ? desiredCount : Math.min(desiredCount, touchSafeCount));
    if (reset || !instance.candles.length) instance.setData(candles, { count, overlays: chartOverlays(), unit });
    else instance.update(candles, chartOverlays());
    if (period === 'daily') renderMarketTrend(instance);
    const lastCandle = candles[candles.length - 1];
    const provisionalLast = period === 'daily' && !liveLast && lastCandle && lastCandle.provisional === true;
    els.forming.hidden = !liveLast;
    els.provisional.hidden = !provisionalLast;
    els.estimate.hidden = !isMinutePeriod(period);
    els.chartContext.textContent = `${periodLabels[period]} · ${liveLast ? '最右一根形成中'
      : provisionalLast ? `最右一根暫用即時收盤，正式日K待更新（目前到 ${A.meta.cutoff_date}）` : `資料截至 ${A.meta.cutoff_date}`}`;
    els.chartSummary.textContent = `${periodLabels[period]}，共 ${candles.length} 根。${linesHidden() ? '' : `圖上有收復線 ${fmt(UPPER)} 與防守線 ${fmt(LOWER)}。`}`;
    requestAnimationFrame(updateOverlayControls);
  }

  function enforceCandleHitTarget() {
    if (!chart || !chart.candles.length || isMobile()) return;
    // 下限跟 minViewCount 一致（10 根）：右側軸上價籤欄較寬時，15 根在 820px 會不足 44px。
    const maxCount = Math.max(10, Math.floor(Math.max(0, chart.geo.pw) / 44));
    if (chart.view.count <= maxCount) return;
    chart.view.count = Math.min(chart.candles.length, maxCount);
    chart.clamp();
    chart.draw();
    chart.emit();
  }

  function visibleGeometry() {
    if (!chart || !chart.candles.length || !chart.geo) return null;
    const canvas = chart.canvas;
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    const { l, pw, start, step } = chart.geo;
    const t = 10;
    const b = 22;
    const macdH = chart.showMacd ? Math.round(H * .24) : 0;
    const gapH = chart.showMacd ? 10 : 0;
    const ph = H - t - b - macdH - gapH;
    const end = chart.view.end;
    const visible = chart.candles.slice(start, end).filter(candle => ['open', 'high', 'low', 'close'].every(key => Number.isFinite(candle[key])));
    const drawnLines = linesHidden() ? [] : [LOWER, UPPER];
    let low = Math.min(...visible.map(candle => candle.low), ...drawnLines);
    let high = Math.max(...visible.map(candle => candle.high), ...drawnLines);
    const pad = (high - low) * .07 || 10;
    low -= pad;
    high += pad;
    // 畫布可能不從 chart-stage 頂端開始（桌面上方留一條收復／防守圖例），浮動元件定位要加上這段位移。
    const offsetY = canvas.getBoundingClientRect().top - els.chartStage.getBoundingClientRect().top;
    const y = value => offsetY + t + (high - value) / (high - low) * ph;
    const x = index => l + step * (index - start + .5);
    return { W, H: H + offsetY, l, pw, ph, start, end, step, y, x };
  }

  function setPosition(element, x, y) {
    element.style.left = `${x}px`;
    element.style.top = `${y}px`;
  }

  // 手機：收復／防守按鈕放到畫布下方操作列（不蓋 K 棒）；桌面維持貼在線旁。
  function placeLineButtons() {
    const target = isMobile() ? els.chartActions : els.chartStage;
    [els.lineUpper, els.lineLower].forEach(button => {
      if (button.parentElement !== target) target.prepend(button);
      if (isMobile()) button.style.top = '';
    });
    if (els.lineUpper.parentElement === els.chartActions && els.lineUpper.nextElementSibling !== els.lineLower) els.lineUpper.after(els.lineLower);
  }

  function updateOverlayControls() {
    placeLineButtons();
    const g = visibleGeometry();
    if (!g) return;
    let upperTop = Math.max(48, Math.min(g.H - 50, g.y(UPPER) - 22));
    let lowerTop = Math.max(48, Math.min(g.H - 50, g.y(LOWER) - 22));
    if (Math.abs(lowerTop - upperTop) < 54) {
      const middle = (upperTop + lowerTop) / 2;
      upperTop = Math.max(48, Math.min(g.H - 104, middle - 27));
      lowerTop = Math.min(g.H - 50, upperTop + 54);
    }
    if (!isMobile()) {
      els.lineUpper.style.top = `${upperTop}px`;
      els.lineLower.style.top = `${lowerTop}px`;
    }
    const lastIndex = chart.candles.length - 1;
    const currentVisible = chart.view.end === chart.candles.length && lastIndex >= g.start && Number.isFinite(chart.candles[lastIndex].close);
    els.point.hidden = !currentVisible;
    if (currentVisible) {
      const last = chart.candles[lastIndex];
      // 現價點貼在最後一根 K 棒上（軸上價籤占用右側欄，舊的右欄推算會蓋到價籤）。
      setPosition(els.point, g.l + g.pw - g.step / 2 - 22, Math.max(32, Math.min(g.H - 55, g.y(last.close) - 22)));
    }
    if (selectedIndex >= g.start && selectedIndex < g.end) {
      const candle = chart.candles[selectedIndex];
      els.marker.hidden = false;
      setPosition(els.marker, Math.max(0, Math.min(g.W - 44, g.x(selectedIndex) - 22)), Math.max(0, Math.min(g.H - 44, g.y(candle.close) - 22)));
    } else {
      els.marker.hidden = true;
    }
  }

  // 這一次點擊裡，圖表是不是自己取消了選取（onClear 會設成 true）
  let clearedByChart = false;

  function bindCandleSelection() {
    let start = null;
    chart.canvas.addEventListener('pointerdown', event => {
      start = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
      clearedByChart = false;
    });
    chart.canvas.addEventListener('pointerup', event => {
      if (!start || start.pointerId !== event.pointerId) return;
      const distance = Math.hypot(event.clientX - start.x, event.clientY - start.y);
      start = null;
      if (distance >= 6) return;
      const g = visibleGeometry();
      if (!g) return;
      const rect = chart.canvas.getBoundingClientRect();
      const localX = event.clientX - rect.left;
      const index = Math.max(g.start, Math.min(g.end - 1, g.start + Math.floor((localX - g.l) / g.step)));
      // 手機：這一下如果是圖表自己取消了選取（再點同一根、或點在圖區以外，kchart.js 會先處理並通知 onClear），不要又選回去。
      // 桌機點 K 棒是開明細板，照舊。
      if (isMobile() && clearedByChart) return;
      selectCandle(index, chart.canvas, !isMobile());
    });
    chart.canvas.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      const g = visibleGeometry();
      const index = selectedIndex >= g.start && selectedIndex < g.end ? selectedIndex : g.end - 1;
      selectCandle(index, chart.canvas, true);
    });
  }

  function selectCandle(index, anchor, open) {
    selectedIndex = index;
    chart.hover = chart.selection = index;
    chart.draw();
    updateOverlayControls();
    if (!open) {
      if (isMobile()) {
        els.mobileDetails.hidden = false;
        els.mobileDetails.textContent = `${chart.candles[index].label} 的詳情 ›`;
      }
      return;
    }
    openPanel('candle', { index }, els.marker, anchor);
  }

  function renderDistribution() {
    const result = A.backtest.results[selectedSample] || A.backtest.results[DEFAULT_SAMPLE];
    if (!result) return;
    const values = result.next_day_outcomes.percent;
    const count = Number(result.next_day_outcomes.sample_count ?? result.event_count);
    els.sampleCount.textContent = fmt(count, 0);
    els.sampleCount.dataset.count = String(count);
    ['stand_back', 'between', 'continue_break'].forEach(key => {
      const button = els.distributionBar.querySelector(`[data-outcome="${key}"]`);
      const value = Number(values[key]);
      button.style.setProperty('--share', Math.max(10, value));
      button.querySelector('[data-share]').textContent = pct(value);
      button.setAttribute('aria-label', `${outcomeLabels[key]} ${pct(value)}，查看歷史分布詳情`);
    });
  }

  function scenarioFor(name) {
    return (D.forecast && D.forecast.scenarios || []).find(item => item.name === name) || {};
  }

  function plainBlock(meaning, watch) {
    const list = items => `<ul>${items.filter(Boolean).map(item => `<li>${item}</li>`).join('')}</ul>`;
    return `<div class="plain">${meaning.filter(Boolean).length ? `<section class="plain-group"><h4>這代表</h4>${list(meaning)}</section>` : ''}${watch && watch.filter(Boolean).length ? `<section class="plain-group plain-watch"><h4>要注意</h4>${list(watch)}</section>` : ''}</div>`;
  }

  // 面板裡點了才展開的一列
  const fold = (summary, html) => `<details class="more-detail"><summary>${summary}</summary>${html}</details>`;
  function moreDetail(html) {
    return fold('看詳細數字', `${html}<p class="deep-links">更深入：<button type="button" data-deep="method">方法</button><button type="button" data-deep="sources">來源</button><button type="button" data-deep="samples">換一組比較樣本</button><button type="button" data-deep="table">資料表</button><button type="button" data-deep="news">新聞</button></p>`);
  }


  // 籌碼卡與直白分析（本人 2026-11-06：選 B、多一點數據、直白分析、要提空單與融資）
  const CH = OV.chips || null;
  // 過期（比行情資料日舊）就整份不用：籌碼卡詳情也不拿舊的解讀配新數字（本人 11-14「過期就隱藏」）。
  const PL = plainStale ? null : (OV.plain || null);
  const NOW = OV.current || null;   // 最新一天各項的前 10 與連續天數（本人 11-07 定案）
  const shortDay = date => (date ? date.slice(5).replace('-', '/') : '');
  function spark(values, tone, signedAxis) {
    const clean = values.map(Number).filter(Number.isFinite);
    if (clean.length < 2) return '';
    const W = 220, H = 56, lo = signedAxis ? Math.min(0, ...clean) : Math.min(...clean), hi = signedAxis ? Math.max(0, ...clean) : Math.max(...clean);
    const y = v => H - 4 - (v - lo) / ((hi - lo) || 1) * (H - 8);
    if (signedAxis) {
      const step = W / clean.length;
      const bars = clean.map((v, i) => `<rect x="${i * step + step * .15}" y="${Math.min(y(v), y(0))}" width="${step * .7}" height="${Math.max(1, Math.abs(y(v) - y(0)))}" fill="${v >= 0 ? 'var(--up)' : 'var(--down)'}" rx="1"/>`).join('');
      return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line-strong)"/>${bars}</svg>`;
    }
    const pts = clean.map((v, i) => `${i / (clean.length - 1) * W},${y(v)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${tone}" stroke-width="2"/><circle cx="${W}" cy="${y(clean.at(-1))}" r="3" fill="${tone}"/></svg>`;
  }
  function chipCards() {
    if (!CH) return [];
    const F = CH.foreign, U = CH.futures, M = CH.margin, last = a => a[a.length - 1], ago = (a, n) => a[Math.max(0, a.length - 1 - n)];
    let streak = 0; for (let i = F.length - 1; i >= 0 && F[i].net_billion < 0; i -= 1) streak += 1;
    const f20 = F.slice(-20).reduce((s, r) => s + r.net_billion, 0);
    const t5 = F.slice(-5).reduce((s, r) => s + r.trust_billion, 0);
    const fut = last(U).net_contracts, futWeek = ago(U, 5).net_contracts;
    const futDays = NOW ? NOW.futures_short_growing_days : 0;
    const futStreak = futDays > 0 ? `，連 ${fmt(futDays, 0)} 天${futDays >= NOW.futures_days_available ? '以上' : ''}增加` : fut < futWeek ? '，還在加' : '';
    const mLast = last(M), mPeak = M.reduce((a, b) => (b.margin_balance_billion > a.margin_balance_billion ? b : a));
    const sNow = mLast.short_units, s20 = ago(M, 20).short_units;
    const amounts = A.daily.slice(-20).map(r => r.amount / 1e8), amtRatio = amounts.at(-1) / (amounts.reduce((s, v) => s + v, 0) / amounts.length);
    const pick = key => (PL && PL.points.find(p => p.key === key)) || { lines: [] };
    const md = d => d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : '';
    return [
      { key: 'foreign', name: '外資現貨', value: `${last(F).net_billion < 0 ? '賣' : '買'} ${fmt(Math.abs(last(F).net_billion), 0)} 億`, tone: last(F).net_billion < 0 ? 'down' : 'up', read: last(F).net_billion < 0 ? `連 ${streak} 天賣，一個月 ${fmt(Math.abs(f20) / 10000, 1)} 兆` : '今天轉為買超', chart: spark(F.slice(-20).map(r => r.net_billion), '', true), date: last(F).date, plain: pick('foreign') },
      { key: 'futures', name: '外資期貨空單', value: `${fmt(Math.abs(fut), 0)} 口`, tone: fut < futWeek ? 'down' : 'up', read: `比一週前${fut < futWeek ? '多' : '少'} ${fmt(Math.abs(fut - futWeek), 0)} 口${futStreak}`, chart: spark(U.slice(-20).map(r => -r.net_contracts), 'var(--down)'), date: last(U).date, plain: pick('foreign') },
      { key: 'margin', name: '融資餘額', value: `${fmt(mLast.margin_balance_billion, 0)} 億`, tone: 'mid', read: `比 ${md(mPeak.date)} 高點少 ${fmt(mPeak.margin_balance_billion - mLast.margin_balance_billion, 0)} 億`, chart: spark(M.slice(-20).map(r => r.margin_balance_billion), 'var(--gold)'), date: mLast.date, plain: pick('margin') },
      { key: 'short', name: '融券餘額', value: `${fmt(sNow / 10000, 1)} 萬張`, tone: sNow > s20 ? 'down' : 'up', read: `比 20 天前${sNow > s20 ? '多' : '少'} ${fmt(Math.abs(sNow / s20 - 1) * 100, 0)}%`, chart: spark(M.slice(-20).map(r => r.short_units), 'var(--accent)'), date: mLast.date, plain: pick('short') },
      { key: 'trust', name: '投信近一週', value: `${t5 >= 0 ? '買' : '賣'} ${fmt(Math.abs(t5), 0)} 億`, tone: t5 >= 0 ? 'up' : 'down', read: `最新一天${last(F).trust_billion < 0 ? '賣' : '買'} ${fmt(Math.abs(last(F).trust_billion), 0)} 億`, chart: spark(F.slice(-20).map(r => r.trust_billion), '', true), date: last(F).date, plain: pick('trust') },
      { key: 'volume', name: '成交金額', value: `${fmt(amounts.at(-1), 0)} 億`, tone: 'mid', read: `平常的 ${fmt(amtRatio, 1)} 倍`, chart: spark(amounts, 'var(--accent)'), date: A.daily.at(-1).date, plain: pick('volume') },
    ];
  }
  function renderChips() {
    const grid = $('chipGrid');
    if (!grid) return;
    const cards = chipCards();
    grid.innerHTML = cards.map(card => `<button type="button" class="chip-card tone-${card.tone}" data-chip="${card.key}"><span class="chip-name">${esc(card.name)}<i aria-hidden="true">›</i></span><strong class="chip-value num">${esc(card.value)}</strong><span class="chip-read">${esc(card.read)}</span>${card.chart}<span class="chip-date num">資料 ${esc(card.date.slice(5).replace('-', '/'))}</span></button>`).join('');
    grid.querySelectorAll('[data-chip]').forEach(button => button.addEventListener('click', () => openPanel('chip', { key: button.dataset.chip }, button)));
    if (PL) {
      $('readLine').textContent = PL.one_line;
      // 每點只留「主角＋一句判斷」；數字細節點開詳情看。標題沒有「主角：」時整句當判斷。
      $('readList').innerHTML = PL.points.slice(0, 4).map(p => {
        const [who, ...rest] = String(p.title).split('：');
        return rest.length ? `<li><b>${esc(who)}</b><span>${esc(rest.join('：'))}</span></li>` : `<li><b>${esc(p.title)}</b><span>${esc(p.lines[0] || '')}</span></li>`;
      }).join('');
      $('readDate').textContent = plainFootnote(PL);
    }
    $('readCard').addEventListener('click', () => openPanel('plain', {}, $('readCard')));
  }
  const DT = OV.detailIndex || null;
  const detailDays = new Set(DT ? DT.days : []);
  function chipSeries(key) {
    if (!CH) return [];
    if (key === 'foreign') return CH.foreign.slice(-20).map(r => ({ date: r.date, value: r.net_billion, label: `${r.net_billion < 0 ? '賣' : '買'} ${fmt(Math.abs(r.net_billion), 0)} 億` }));
    if (key === 'trust') return CH.foreign.slice(-20).map(r => ({ date: r.date, value: r.trust_billion, label: `${r.trust_billion < 0 ? '賣' : '買'} ${fmt(Math.abs(r.trust_billion), 0)} 億` }));
    if (key === 'futures') return CH.futures.slice(-20).map((r, i, a) => ({ date: r.date, value: -r.net_contracts, label: `空單 ${fmt(Math.abs(r.net_contracts), 0)} 口`, prev: i ? a[i - 1].net_contracts : null }));
    if (key === 'margin') return CH.margin.slice(-20).map((r, i, a) => ({ date: r.date, value: r.margin_balance_billion, label: `${fmt(r.margin_balance_billion, 0)} 億`, prev: i ? a[i - 1].margin_balance_billion : null }));
    if (key === 'short') return CH.margin.slice(-20).map((r, i, a) => ({ date: r.date, value: r.short_units, label: `${fmt(r.short_units / 10000, 1)} 萬張`, prev: i ? a[i - 1].short_units : null }));
    if (key === 'volume') return A.daily.slice(-20).map(r => ({ date: r.date, value: r.amount / 1e8, label: `${fmt(r.amount / 1e8, 0)} 億` }));
    return [];
  }
  function clickableBars(key, selected) {
    const rows = chipSeries(key);
    if (!rows.length) return '';
    const signedAxis = key === 'foreign' || key === 'trust';
    const W = 560, H = 170, pad = 6, vals = rows.map(r => r.value);
    const low = Math.min(...vals), high = Math.max(...vals);
    // 只有高低的項目（餘額、成交）：底線放在最低那一天再往下一點，最低的那一根才看得到
    const lo = signedAxis ? Math.min(0, low) : low - (high - low) * .12, hi = signedAxis ? Math.max(0, high) : high;
    const y = v => H - 22 - (v - lo) / ((hi - lo) || 1) * (H - 48);   // 最上面 26 留給選到那一天的字
    const step = (W - pad * 2) / rows.length;
    const zero = signedAxis ? y(0) : H - 22;
    const bars = rows.map((r, i) => {
      const top = Math.min(y(r.value), zero), h = Math.max(2, Math.abs(zero - y(r.value)));
      const tone = signedAxis ? (r.value >= 0 ? 'var(--up)' : 'var(--down)') : key === 'margin' ? 'var(--gold)' : key === 'futures' ? 'var(--down)' : 'var(--accent)';
      const has = detailDays.has(r.date);
      const on = r.date === selected;
      // 選到的那一天，日期與數字寫在圖的最上面、對著那一根；靠邊的幾根改成靠左或靠右對齊，字才不會被切掉
      const edge = i < 4 ? ['start', pad + i * step] : i > rows.length - 5 ? ['end', pad + (i + 1) * step] : ['middle', pad + i * step + step / 2];
      return `<g class="bar-hit${on ? ' is-on' : ''}" data-day="${esc(r.date)}" tabindex="0" role="button" aria-label="${esc(r.date)} ${esc(r.label)}${has ? '，點看那一天的名單' : ''}"><rect x="${pad + i * step}" y="0" width="${step}" height="${H}" fill="transparent"/><rect x="${pad + i * step + step * .14}" y="${top}" width="${step * .72}" height="${h}" rx="2" fill="${tone}" opacity="${on ? 1 : .72}"/><text class="bar-value" x="${edge[1]}" y="17" text-anchor="${edge[0]}">${esc(shortDay(r.date))} ${esc(r.label)}</text></g>`;
    }).join('');
    const ticks = [0, Math.floor(rows.length / 2), rows.length - 1].map(i => `<text x="${pad + i * step + step / 2}" y="${H - 6}" text-anchor="middle">${esc(rows[i].date.slice(5).replace('-', '/'))}</text>`).join('');
    return `<svg class="drill-chart" viewBox="0 0 ${W} ${H}" role="group" aria-label="近 20 個交易日，點任一天看那一天的名單">${signedAxis ? `<line x1="0" x2="${W}" y1="${zero}" y2="${zero}" stroke="var(--line-strong)"/>` : ''}<rect class="bar-thumb" x="0" y="0" width="${step}" height="${H}"/>${bars}${ticks}</svg>`;
  }
  // 籌碼與資金的明細板（本人 2026-11-15 照樣品定案）：今天的數字與一句結論 → 近 20 天的圖 → 注意 → 解讀 → 名單。
  // 名單預設是最新一天的前 10（有連續天數，本人 11-07 定案的資料）；點圖上的另一天，名單換成那一天的排行（按需取）。
  const SIDE_LABELS = { foreign: ['買超', '賣超'], trust: ['買超', '賣超'], margin: ['增加', '減少'], short: ['增加', '減少'] };
  // 每一項的名單：[標題, 資料欄位, 連續天數那一欄的欄名]；有兩組的在標題右邊切換
  const CHIP_LISTS = {
    foreign: [['外資買超前 10', 'foreign_buy', '連買'], ['外資賣超前 10', 'foreign_sell', '連賣']],
    trust: [['投信買超前 10', 'trust_buy', '連買'], ['投信賣超前 10', 'trust_sell', '連賣']],
    margin: [['融資增加前 10', 'margin_up', '連增'], ['融資減少前 10', 'margin_down', '連減']],
    short: [['融券增加前 10', 'short_up', '連增'], ['融券減少前 10', 'short_down', '連減']],
    volume: [['成交金額前 10', 'turnover_top', '連續在前 20']],
    futures: [['外資現貨賣超前 10（對照）', 'foreign_sell', '連賣']]   // 期貨是整個台指、沒有個股，列同一天的外資現貨賣超對照
  };
  const LIST_ROWS = 10, POINT_ROWS = 2;
  const chipSide = () => (panelState.payload && panelState.payload.side) || 0;
  const chipList = key => { const lists = CHIP_LISTS[key] || []; return lists[Math.min(chipSide(), lists.length - 1)]; };
  // 「目前前 10」各項自己最新有資料的那一天（11-17：融資晚上才公布，常比外資晚一天）；舊資料包沒有 dates 就用全體最新一天
  const NOW_KEY = { foreign: 'foreign', futures: 'foreign', trust: 'trust', margin: 'margin', short: 'short', volume: 'amount' };
  const nowDay = key => (!NOW ? null : NOW.dates ? NOW.dates[NOW_KEY[key]] || null : NOW.as_of);
  // 名單現在是哪一天：在圖上點過就是點的那一天，還沒點就是這一項的最新一天
  function chipDay(key) {
    if (panelState.payload && panelState.payload.day) return panelState.payload.day;
    const rows = chipSeries(key), today = nowDay(key);
    return today && rows.some(row => row.date === today) ? today : rows.length ? rows[rows.length - 1].date : '';
  }
  const signedLots = v => `${v >= 0 ? '+' : '−'}${fmt(Math.abs(v), 0)} 張`;
  const signedBillion = v => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${fmt(Math.abs(v), 1)} 億`);
  const signTone = v => (v >= 0 ? 'upc' : 'dn');
  const ESTIMATED = { html: '金額 <small>估</small>' };   // 個股的金額是用張數乘收盤價估的
  // 欄位：[欄名, 取字, 取顏色類別]。欄名是一般的字（放進表格時會跳脫）；要帶標記的寫成 { html }，只能是這裡寫死的常數。
  // 最新一天有連續天數；其他天是那一天的排行，欄位照當天抓到的資料。
  const columnHead = name => (typeof name === 'string' ? esc(name) : name.html);
  function latestColumns(key, word) {
    const value = key === 'volume' ? ['成交', r => `${fmt(r.value, 0)} 億`]
      : key === 'margin' || key === 'short' ? ['增減', r => signedLots(r.value), r => signTone(r.value)]
        : [ESTIMATED, r => signedBillion(r.value), r => signTone(r.value)];
    return [value, [word, r => `${fmt(r.streak, 0)} 天${r.capped ? '以上' : ''}`, () => 'streak']];
  }
  function dayColumns(key) {
    if (key === 'foreign' || key === 'trust') return [[ESTIMATED, r => signedBillion(r[`${key}_billion`]), r => signTone(r[`${key}_billion`])], ['張數', r => signedLots(r[`${key}_lots`])]];
    if (key === 'margin' || key === 'short') return [[SIDE_LABELS[key][chipSide()], r => signedLots(r[`${key}_change`])], ['餘額', r => `${fmt(r[`${key}_balance`], 0)} 張`]];
    if (key === 'volume') return [['成交', r => `${fmt(r.amount_billion, 0)} 億`], ['漲跌', r => (r.change_pct == null ? '—' : `${r.change_pct >= 0 ? '+' : ''}${fmt(r.change_pct, 2)}%`), r => signTone(r.change_pct || 0)]];
    return [[ESTIMATED, r => signedBillion(r.foreign_billion), r => signTone(r.foreign_billion)]];
  }
  // 先收著的內容：收著的項目帶 is-extra，按鈕切換外層（more-wrap）的 is-all
  const moreButton = label => `<button type="button" class="chip-more" data-more="${esc(label)}" aria-expanded="false">${esc(label)}</button>`;
  function bindMore(root) {
    root.querySelectorAll('[data-more]').forEach(button => button.addEventListener('click', () => {
      const open = button.closest('.more-wrap').classList.toggle('is-all');
      button.setAttribute('aria-expanded', String(open));
      button.textContent = open ? '收起' : button.dataset.more;
    }));
  }
  function listTable(rows, columns) {
    if (!rows || !rows.length) return '<p class="chip-date">這一天沒有資料。</p>';
    const body = rows.map((r, i) => `<tr${i >= LIST_ROWS ? ' class="is-extra"' : ''}><td class="num">${i + 1}</td><td><a class="stock-link" href="stock.html?code=${encodeURIComponent(r.code)}"><span class="num">${esc(r.code)}</span> ${esc(r.name)}</a></td>${columns.map(c => `<td class="num ${c[2] ? c[2](r) : ''}">${esc(c[1](r))}</td>`).join('')}</tr>`).join('');
    return `<div class="drill-table chip-table more-wrap"><table><thead><tr><th>#</th><th>股票</th>${columns.map(c => `<th class="num">${columnHead(c[0])}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>${rows.length > LIST_ROWS ? moreButton(`看到前 ${rows.length} ›`) : ''}</div>`;
  }
  // 填名單：其他天的排行要去取，取回後只在使用者仍停在同一張卡、同一天、同一邊時才填入（舊請求不覆蓋新選擇）。
  let drillToken = 0;
  function showChipList(key) {
    const list = chipList(key), day = chipDay(key), token = ++drillToken;
    const target = () => els.panelBody.querySelector('#chipList'), title = els.panelBody.querySelector('#chipListTitle');
    if (!list || !title || !target()) return;
    const name = shown => `${esc(shown)} <small class="num">${esc(shortDay(day))}</small>`;
    title.innerHTML = name(list[0]);
    const fill = html => {
      if (token !== drillToken || !target()) return;
      target().classList.remove('is-loading');
      target().removeAttribute('aria-busy');
      target().innerHTML = html;
      bindMore(target());
      // 展開到全部之後，標題的「前 10」跟著寫成實際列出的檔數；收起再寫回來
      const more = target().querySelector('[data-more]');
      if (more) more.addEventListener('click', () => {
        if (token !== drillToken) return;   // 已經在等另一天的資料：這份名單是舊的，標題不能被寫回舊的那一天
        title.innerHTML = name(more.getAttribute('aria-expanded') === 'true' ? list[0].replace(`前 ${LIST_ROWS}`, `前 ${target().querySelectorAll('tbody tr').length}`) : list[0]);
      });
    };
    if (day && day === nowDay(key)) { fill(listTable(NOW[list[1]], latestColumns(key, list[2]))); return; }
    // 那一天的明細還沒抓到（不在已有的日子裡，或取回來是空的）和官方那天真的沒資料要分開寫（換皮前就是這樣分；D 包核銷補回）
    const notFetched = `<p class="chip-date">這一天的個股明細還沒抓到（目前有 ${DT ? DT.days.length : 0} 天）。</p>`;
    if (!detailDays.has(day)) { fill(notFetched); return; }
    // 取資料的期間原本的名單先留著（變淡），不換成一行「載入中」：不然面板會先變矮再變高，往下捲過的話畫面會跳一下（本人 11-15 回報的抖動）
    if (target().firstElementChild) {
      target().classList.add('is-loading');
      target().setAttribute('aria-busy', 'true');
      target().firstElementChild.setAttribute('data-loading', '');
    } else fill('<p class="chip-date" data-loading>載入明細中…</p>');
    window.MarketData.detail(key, day).then(
      detail => fill(Object.keys(detail || {}).length ? listTable(detail[list[1]], dayColumns(key)) : notFetched),
      error => fill(`<p class="chip-date" role="alert">${esc(error && error.message ? error.message : '暫時無法取得明細。')}</p>`)
    );
  }
  function segmented(name, options, current) {
    return `<div class="chip-seg" role="radiogroup" data-seg="${name}">${options.map(([value, label]) => `<button type="button" role="radio" data-value="${value}" aria-checked="${String(value) === String(current)}">${esc(label)}</button>`).join('')}</div>`;
  }
  function renderChipPanel(key) {
    const card = chipCards().find(item => item.key === key);
    if (!card) return '';
    const watch = PL ? PL.worry.filter(w => (key === 'futures' || key === 'foreign') && w.includes('外資')) : [];
    const lines = card.plain.lines, sides = SIDE_LABELS[key];
    return `<div class="chip-top${card.tone === 'mid' ? '' : ` tone-${card.tone}`}"><strong class="chip-value num">${esc(card.value)}</strong><span>${esc(card.read)}</span></div>
      <div class="chip-big">${clickableBars(key, chipDay(key))}</div>
      ${watch.length ? `<p class="chip-watch"><i>注意</i><span>${watch.map(esc).join(' ')}</span></p>` : ''}
      ${lines.length ? `<div class="chip-points more-wrap"><ul>${lines.map((line, i) => `<li${i >= POINT_ROWS ? ' class="is-extra"' : ''}>${esc(line)}</li>`).join('')}</ul>${lines.length > POINT_ROWS ? moreButton(`還有 ${lines.length - POINT_ROWS} 點 ›`) : ''}<p class="read-date plain-note">${esc(plainFootnote(PL))}</p></div>` : ''}
      <div class="chip-list-head"><h4 id="chipListTitle"></h4>${sides ? segmented('side', sides.map((label, i) => [i, label]), chipSide()) : ''}</div>
      <div id="chipList"></div>`;
  }
  function bindChipPanel(key) {
    bindMore(els.panelBody);
    els.panelBody.querySelectorAll('.chip-seg button').forEach(button => button.addEventListener('click', () => {
      panelState.payload.side = Number(button.dataset.value);
      button.parentElement.querySelectorAll('button').forEach(other => other.setAttribute('aria-checked', String(other === button)));
      showChipList(key);
    }));
    const chart = els.panelBody.querySelector('.drill-chart'), bars = [...els.panelBody.querySelectorAll('.bar-hit')];
    const pickDay = bar => {
      panelState.payload.day = bar.dataset.day;
      bars.forEach(other => other.classList.toggle('is-on', other === bar));
      showChipList(key);
    };
    const justDragged = chart ? bindBarDrag(chart, bars, pickDay) : () => false;
    bars.forEach(bar => {
      bar.addEventListener('click', () => { if (!justDragged()) pickDay(bar); });   // 拖完放開後瀏覽器補發的那一下不算
      bar.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pickDay(bar); } });
    });
    showChipList(key);
  }
  // 柱狀圖按住左右拖：選中的那個直條框跟著手指走，放開時靠近哪一天就選哪一天、名單跟著換（本人 11-15 更正）。點一下照舊。
  // 框是圖裡的一塊（.bar-thumb），位置用圖自己的座標。拖的時候圖帶 is-sliding：原本選中那一根的底色先收掉、由框來畫，
  // 圖上那行字寫手指底下那一天。名單等放開才換（一天一次取資料，不在拖的途中一直取）。回傳「是不是剛拖完」給點擊用。
  const BAR_DRAG_START_PX = 8, BAR_SETTLE_MS = 200, BAR_STRAY_CLICK_MS = 150;
  function bindBarDrag(chart, bars, pickDay) {
    const thumb = chart.querySelector('.bar-thumb');
    if (!bars.length || !thumb) return () => false;
    const columns = bars.map(bar => Number(bar.firstElementChild.getAttribute('x'))), step = Number(thumb.getAttribute('width'));
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
    let drag = null, settling = 0, releasedAt = -Infinity;   // drag：{ x 按下去的位置, id 哪一根手指, moved, hot 手指底下那一天 }
    const place = x => { thumb.style.transform = `translateX(${x}px)`; };
    const stop = () => {
      window.clearTimeout(settling);
      chart.classList.remove('is-sliding', 'is-settling');
      bars.forEach(bar => bar.classList.remove('is-slide-hot'));
    };
    const follow = event => {
      if (!drag.moved && Math.abs(event.clientX - drag.x) < BAR_DRAG_START_PX) return;
      if (!drag.moved) {
        drag.moved = true;
        chart.classList.add('is-sliding');
        try { chart.setPointerCapture(drag.id); } catch (error) { /* 抓不到就照一般的事件走 */ }
      }
      const box = chart.getBoundingClientRect();
      const x = (event.clientX - box.left) / box.width * chart.viewBox.baseVal.width;   // 視窗座標換成圖的座標
      place(Math.min(Math.max(x - step / 2, columns[0]), columns[columns.length - 1]));
      const hot = bars[Math.min(Math.max(Math.floor((x - columns[0]) / step), 0), bars.length - 1)];
      if (hot === drag.hot) return;
      if (drag.hot) drag.hot.classList.remove('is-slide-hot');
      drag.hot = hot;
      hot.classList.add('is-slide-hot');
    };
    const finish = (event, cancelled) => {
      if (!drag || event.pointerId !== drag.id) return;
      if (!cancelled) follow(event);   // 以放開的位置為準
      const { moved, hot } = drag;
      drag = null;
      if (!moved) return;   // 沒有拖：就是點一下
      if (cancelled || !hot) { stop(); return; }
      releasedAt = performance.now();
      pickDay(hot);
      if (calm.matches) { stop(); return; }
      chart.classList.add('is-settling');   // 框滑到那一根的正上方，再交還給那一根自己的底色
      place(columns[bars.indexOf(hot)]);
      settling = window.setTimeout(stop, BAR_SETTLE_MS);
    };
    chart.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0) return;
      stop();
      drag = { x: event.clientX, id: event.pointerId, moved: false, hot: null };
    });
    chart.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      if (event.pointerType === 'mouse' && !(event.buttons & 1)) { drag = null; stop(); return; }   // 滑鼠在圖外放開了：這一次按住已經結束
      follow(event);
    });
    chart.addEventListener('pointerup', event => finish(event, false));
    chart.addEventListener('pointercancel', event => finish(event, true));
    return () => performance.now() - releasedAt < BAR_STRAY_CLICK_MS;
  }
  // 白話分析的小字：照實標資料日與來源（本人 11-17 起每天重寫；ai＝Gemini 寫、數字由程式核對，template＝程式依數字套句）。
  function plainFootnote(pl) {
    if (!pl || !pl.data_dates) return '';
    const day = value => String(value || '').slice(5).replace('-', '/');
    const price = pl.data_dates.price_and_foreign;
    // 資料日以加權指數那天為準；外資、期貨、融資融券哪一份比它晚公布，就另外標它自己的日子
    const late = [['foreign', '外資'], ['futures', '期貨'], ['margin', '融資融券']]
      .filter(([key]) => pl.data_dates[key] && pl.data_dates[key] < price)
      .map(([key, label]) => `${label} ${day(pl.data_dates[key])}`);
    const dates = `資料 ${day(price)}${late.length ? `（${late.join('、')}）` : ''}`;
    const from = pl.source === 'ai' ? 'AI 整理，數字已由程式核對' : pl.source === 'template' ? '依當天數字自動整理' : '';
    return from ? `${dates}・${from}` : dates;
  }
  function renderPlainPanel() {
    if (!PL) return '';
    return `<p class="read-line big">${esc(PL.one_line)}</p>${PL.points.map(p => `<section class="plain-group"><h4>${esc(p.title)}</h4><ul>${p.lines.map(l => `<li>${esc(l)}</li>`).join('')}</ul></section>`).join('')}<section class="plain-group plain-watch"><h4>要擔心</h4><ul>${PL.worry.map(l => `<li>${esc(l)}</li>`).join('')}</ul></section><section class="plain-group"><h4>什麼時候比較安心</h4><ul>${PL.relief.map(l => `<li>${esc(l)}</li>`).join('')}</ul></section><p class="read-date plain-note">${esc(plainFootnote(PL))}</p>`;
  }

  function renderCandlePanel(index) {
    const candle = chart.candles[index];
    const day = periodDataDate(candle);
    const institution = A.institutional.find(row => row.date === day);
    const amounts = chart.candles.slice(Math.max(0, index - 19), index + 1).map(item => Number(item.amount)).filter(Number.isFinite);
    const average = amounts.length ? amounts.reduce((sum, value) => sum + value, 0) / amounts.length : NaN;
    const amount = Number(candle.amount);
    const foreign = institution ? institution.foreign.net / 100_000_000 : NaN;
    const trust = institution ? institution.trust.net / 100_000_000 : NaN;
    const magnitude = Math.max(1, Math.abs(amount || 0), Math.abs(average || 0), Math.abs(foreign || 0), Math.abs(trust || 0));
    const range = candle.high - candle.low;
    const bodyTop = Math.max(candle.open, candle.close);
    const bodyLow = Math.min(candle.open, candle.close);
    const lowerShadow = range > 0 ? (bodyLow - candle.low) / range : 0;
    const upperShadow = range > 0 ? (candle.high - bodyTop) / range : 0;
    const rise = candle.close >= candle.open;
    const prevCandle = chart.candles[index - 1];
    const gapDown = prevCandle && candle.open < prevCandle.low;
    const volumeRatio = Number.isFinite(amount) && average > 0 ? amount / average : NaN;
    const meaning = [
      `這根是<b>${rise ? '紅K' : '黑K'}</b>：收盤${rise ? '比開盤高' : '比開盤低'}${prevCandle ? `，比前一根${candle.close >= prevCandle.close ? '漲' : '跌'} ${fmt(Math.abs(candle.close - prevCandle.close), 0)} 點` : ''}。`,
      gapDown ? '一開盤就<b>跳空</b>跌到前一根最低點以下，代表開盤前就有很大的賣壓。' : '',
      lowerShadow >= 0.35 ? `下影線很長：盤中曾跌到 ${fmt(candle.low, 0)}，後來<b>被買回來</b>，代表低點有人承接。` : '',
      upperShadow >= 0.35 ? `上影線很長：盤中曾漲到 ${fmt(candle.high, 0)}，後來<b>被賣下去</b>，代表高點有賣壓。` : '',
      Number.isFinite(volumeRatio) ? (volumeRatio >= 1.3 ? `成交金額是平常的 ${fmt(volumeRatio, 1)} 倍，<b>交易很熱</b>，這根的意義比較重。` : volumeRatio <= 0.75 ? '成交金額比平常少，這根的意義比較輕。' : '成交金額和平常差不多。') : '',
      Number.isFinite(foreign) ? (foreign < 0 ? `外資賣超 ${fmt(Math.abs(foreign), 0)} 億。` : `外資買超 ${fmt(foreign, 0)} 億。`) : '',
    ];
    const watch = [
      lowerShadow >= 0.35 ? `下影線要<b>隔天不被跌破</b>才算數；收盤跌破 ${fmt(candle.low, 0)} 就失效。` : '',
      Number.isFinite(foreign) && foreign < 0 && Number.isFinite(trust) && trust > 0 ? '外資在賣、投信在買，方向不一致，先別單看一邊。' : '',
      candle.provisional ? '這根是盤後暫用即時收盤（證交所即時報價），正式日K與法人資料還沒更新；更新後以正式資料為準。' : '',
    ].filter(Boolean);
    return `
      ${plainBlock(meaning, watch)}
      ${moreDetail(`<div class="ohlc-grid" aria-label="開高低收">
        ${[['open', '開'], ['high', '高'], ['low', '低'], ['close', '收']].map(([field, label]) => `
          <div><span>${label}</span><strong class="num" data-ohlc="${field}" data-raw="${candle[field]}">${fmt(candle[field])}</strong></div>
        `).join('')}
      </div>
      <div class="metric-bars">
        ${metricBar('成交金額', amount, average, magnitude, 'var(--accent)', Number.isFinite(amount) ? `${fmt(amount, 0)} 億` : '—')}
        ${metricBar('近 20 根平均', average, average, magnitude, 'var(--gold)', Number.isFinite(average) ? `${fmt(average, 0)} 億` : '—')}
        ${metricBar('外資當日', foreign, 0, magnitude, foreign >= 0 ? 'var(--up)' : 'var(--down)', Number.isFinite(foreign) ? `${signed(foreign, 0)} 億` : '尚未取得')}
        ${metricBar('投信當日', trust, 0, magnitude, trust >= 0 ? 'var(--up)' : 'var(--down)', Number.isFinite(trust) ? `${signed(trust, 0)} 億` : '尚未取得')}
      </div>
      <p class="panel-callout" style="--tone:var(--gold)">資料日期：${esc(day || '尚未取得')}。缺少的法人資料不以其他日期補值。</p>`)}
    `;
  }

  function metricBar(label, value, reference, magnitude, tone, display) {
    const size = Number.isFinite(value) ? Math.max(0, Math.min(100, Math.abs(value) / magnitude * 100)) : 0;
    return `<div class="metric-row"><span>${esc(label)}</span><strong class="num">${esc(display)}</strong><div class="metric-track"><i style="--size:${size}%;--tone:${tone}"></i></div></div>`;
  }

  // 收復線／防守線點開之後：一句重點、一張照價位高低排的圖，其餘點了才展開（本人 11-15：文字看不懂、內容太多，要整理過的圖表）。
  // 圖的資料整理在 line-zones.js；兩顆鍵點開是同一張圖，只差上面那一句與哪一條線比較亮。
  const ZONE_TONES = { stand_back: 'var(--up)', between: 'var(--gold)', continue_break: 'var(--down)', upper: 'var(--gold)', lower: 'var(--down)' };
  function renderLinePanel(line) {
    const upper = line === 'upper';
    // 人寫的那幾句（手上沒股票／已經持有、觸發條件、解讀）：劇本過期、或講的不是現在這兩條線時不顯示，線與圖照樣有。
    // 程式給的提醒（要注意）不是人寫的，每天都在：有人寫的那幾句時一起放在「怎麼應對」裡，沒有時自己一列
    const scenarioName = upper ? '轉強觀察' : '續弱觀察';
    const scenario = scenarioFor(scenarioName);
    const action = ACTIONS[scenarioName] || {};
    const outcomes = (A.backtest.results[selectedSample] || A.backtest.results[DEFAULT_SAMPLE]).next_day_outcomes;
    const drawn = drawnDay();
    // 收復線那一句：那一天收盤跌破前一天的最低點時，線是「要收復的價位」；沒有跌破的日子線就是當天的最高點，談不上止跌
    const broke = AS_OF.default_sample !== 'not_event';
    const point = upper
      ? (broke ? `收盤回到 <b class="num">${fmt(UPPER, 0)}</b> 以上，才算止跌。` : `收盤站上 <b class="num">${fmt(UPPER, 0)}</b>，就是越過${esc(drawn.word)}的高點。`)
      : `收盤跌破 <b class="num">${fmt(LOWER, 0)}</b>，就是${esc(drawn.word)}的低點沒守住。`;
    const figure = window.LineZones.read({ upper: UPPER, lower: LOWER, close: CLOSE, percent: outcomes.percent, count: outcomes.sample_count });
    const row = item => (item.kind === 'zone'
      ? `<li><button type="button" class="zone" data-outcome-panel="${item.key}" style="--w:${item.percent}%;--tone:${ZONE_TONES[item.key]}" aria-label="${esc(outcomeLabels[item.key])} ${item.text}${item.here ? `，${drawn.mark}落在這一區` : ''}，查看歷史分布詳情"><span class="zone-name">${item.label}${item.here ? `<em>${drawn.mark}</em>` : ''}</span><span class="zone-track"><i></i></span><b class="num">${item.text}</b></button></li>`
      : `<li class="zone-line${item.key === line ? ' is-this' : ''}" style="--tone:${ZONE_TONES[item.key]}"><i class="tag">${item.label}</i><strong class="num">${fmt(item.value, 0)}</strong><span>${item.gap}</span></li>`);
    // 這條線怎麼來的：規則是跌破前一天最低點時收復線取「前一天最低點、當天最高點」比較低的那一個，防守線取當天最低點。
    // 對得上當天的最高、最低點才寫白話；對不上就照規則的原話，不猜
    const same = (a, b) => Number.isFinite(Number(a)) && Math.abs(Number(a) - b) < 0.005;
    const prior = Number(AS_OF.previous_low), dayName = esc(drawn.word);
    const source = upper
      ? (same(AS_OF.high, UPPER) ? `這條線取${dayName}的最高點 ${fmt(UPPER)}${prior > UPPER ? `（前一天的最低點 ${fmt(prior)} 比較高，所以取比較近的這一個）` : ''}。`
        : same(AS_OF.previous_low, UPPER) ? `這條線取前一天的最低點 ${fmt(UPPER)}。`
          : `依回測規則，本次收復線取 ${fmt(UPPER)}；原始前日低為 ${fmt(AS_OF.previous_low)}。`)
      : (same(AS_OF.low, LOWER) ? `這條線取${dayName}的最低點 ${fmt(LOWER)}。` : `依回測規則，防守線取分析截止日最低 ${fmt(LOWER)}。`);
    const watch = upper
      ? ['站上只是反彈，不代表開始漲回去。', '外資賣得變少，才比較可信。']
      : ['只看收盤，盤中跌破又收回來不算。', window.LineZones.zoneOf(CLOSE, UPPER, LOWER) === 'between' ? '還在兩條線中間，不代表安全。' : ''];
    return `
      <p class="line-point">${point}</p>
      <figure class="zones">
        <figcaption>過去很像的${figure.count === null ? '日子' : ` ${fmt(figure.count, 0)} 次`}，隔天收在哪裡（不是明天的機率）</figcaption>
        <ol class="zone-rows">${figure.rows.map(row).join('')}</ol>
      </figure>
      ${scenarioHidden ? fold('要注意', `<div class="plain"><section class="plain-group plain-watch"><ul>${watch.filter(Boolean).map(item => `<li>${item}</li>`).join('')}</ul></section></div>`) : fold('怎麼應對', `<dl class="action-list">
        <div><dt>手上沒股票</dt><dd id="actionFlat">${esc(action.flat || '尚未取得')}</dd></div>
        <div><dt>已經持有</dt><dd id="actionHolding">${esc(action.holding || '尚未取得')}</dd></div>
      </dl>${plainBlock([], watch)}`)}
      ${moreDetail(`<p class="panel-lede">${source}</p>${scenarioHidden ? '' : `<dl class="action-list"><div><dt>觸發條件</dt><dd>${esc(scenario.trigger || '尚未取得')}</dd></div><div><dt>解讀</dt><dd>${esc(scenario.interpretation || '尚未取得')}</dd></div></dl><p class="panel-callout" style="--tone:${upper ? 'var(--gold)' : 'var(--down)'}">${esc(ACTIONS.basis || '一般情境應對，不是自動下單指示。')}</p>`}`)}
    `;
  }

  function methodNumber(methodKey, unit) {
    const text = String(A.backtest.method && A.backtest.method[methodKey] || '');
    const match = text.match(new RegExp(`([\\d,.]+)\\s*${unit}`));
    return match ? Number(match[1].replaceAll(',', '')) : NaN;
  }

  function calculateReturnRows(sampleKey) {
    const institutional = new Map(A.institutional.map(row => [row.date, row]));
    const nearLowLimit = methodNumber('subset_near_low', '%') / 100;
    const dropLimitPct = methodNumber('subset_drop_over_3pct', '%');
    const lowerShadowLimit = methodNumber('subset_long_lower_shadow', '%') / 100;
    const foreignSellLimit = methodNumber('subset_foreign_sell_over_50b', '億元') * 100_000_000;
    const selected = [];
    const baseline = A.daily.slice(0, -1).map((current, index) => (A.daily[index + 1].close / current.close - 1) * 100);
    for (let index = 1; index < A.daily.length - 1; index += 1) {
      const previous = A.daily[index - 1];
      const current = A.daily[index];
      const next = A.daily[index + 1];
      const isEvent = current.close < previous.low;
      const dropPct = (current.close / previous.close - 1) * 100;
      const closeNearLow = Number.isFinite(nearLowLimit) && (current.close - current.low) / current.low < nearLowLimit;
      const range = current.high - current.low;
      const lowerShadowRatio = range > 0 ? (Math.min(current.open, current.close) - current.low) / range : 0;
      const foreignNet = institutional.get(current.date)?.foreign?.net;
      const matches = sampleKey === 'all_events' ? isEvent
        : sampleKey === 'event_and_close_within_0_3pct_of_low' ? isEvent && closeNearLow
          : sampleKey === 'event_and_daily_drop_over_3pct' ? isEvent && Number.isFinite(dropLimitPct) && dropPct < -dropLimitPct
            : sampleKey === 'event_and_long_lower_shadow' ? isEvent && Number.isFinite(lowerShadowLimit) && lowerShadowRatio >= lowerShadowLimit
              : sampleKey === 'event_and_foreign_net_sell_over_50b' ? isEvent && Number.isFinite(foreignSellLimit) && Number.isFinite(foreignNet) && foreignNet < -foreignSellLimit
                : sampleKey === 'not_event' ? !isEvent
                  : false;
      const upper = isEvent ? Math.min(previous.low, current.high) : current.high;
      const lower = current.low;
      const outcome = next.close >= upper ? 'stand_back' : next.close < lower ? 'continue_break' : 'between';
      const returnPct = (next.close / current.close - 1) * 100;
      if (matches) selected.push({ outcome, returnPct, date: current.date });
    }
    return { selected, baseline };
  }

  function median(values) {
    if (!values.length) return NaN;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  function positivePercent(values) {
    return values.length ? values.filter(value => value > 0).length / values.length * 100 : NaN;
  }

  function histogramSvg(currentValues, baselineValues, outcome) {
    const all = [...currentValues, ...baselineValues].filter(Number.isFinite);
    const edge = Math.max(.5, Math.ceil(Math.max(...all.map(Math.abs), .5) * 2) / 2);
    const binCount = 9;
    const step = edge * 2 / binCount;
    const bin = values => {
      const counts = Array(binCount).fill(0);
      values.forEach(value => {
        const index = Math.max(0, Math.min(binCount - 1, Math.floor((value + edge) / step)));
        counts[index] += 1;
      });
      return counts.map(count => values.length ? count / values.length * 100 : 0);
    };
    const currentBins = bin(currentValues);
    const baselineBins = bin(baselineValues);
    const maxPercent = Math.max(1, ...currentBins, ...baselineBins);
    const chartWidth = 340;
    const left = 34;
    const plotWidth = 294;
    const barStep = plotWidth / binCount;
    const rowHeight = 62;
    const rows = [
      { label: outcomeLabels[outcome], values: currentBins, baseline: 88, color: outcome === 'stand_back' ? 'var(--up)' : outcome === 'continue_break' ? 'var(--down)' : 'var(--gold)' },
      { label: '一般交易日', values: baselineBins, baseline: 174, color: 'var(--faint)' }
    ];
    const bars = rows.map(row => row.values.map((value, index) => {
      const height = value / maxPercent * rowHeight;
      const x = left + index * barStep + 2;
      return `<rect x="${x}" y="${row.baseline - height}" width="${Math.max(2, barStep - 4)}" height="${Math.max(.5, height)}" rx="2" fill="${row.color}" opacity=".78"/>`;
    }).join('')).join('');
    const rowLabels = rows.map(row => `<text x="${left}" y="${row.baseline - rowHeight - 7}">${esc(row.label)}（%）</text><line class="axis" x1="${left}" y1="${row.baseline + .5}" x2="${left + plotWidth}" y2="${row.baseline + .5}"/>`).join('');
    const xLabels = `<text x="${left}" y="205" text-anchor="start">−${fmt(edge, 1)}%</text><text x="${left + plotWidth / 2}" y="205" text-anchor="middle">0%</text><text x="${left + plotWidth}" y="205" text-anchor="end">+${fmt(edge, 1)}%</text>`;
    return `<svg viewBox="0 0 ${chartWidth} 214" role="img" aria-label="${esc(outcomeLabels[outcome])}與一般交易日的隔日報酬直方圖，同一分箱與尺度">${rowLabels}${bars}${xLabels}</svg>`;
  }

  function renderDistributionPanel(outcome) {
    const result = A.backtest.results[selectedSample] || A.backtest.results[DEFAULT_SAMPLE];
    const sample = A.backtest.samples.find(item => item.key === selectedSample) || {};
    const rows = calculateReturnRows(selectedSample);
    const currentReturns = rows.selected.filter(row => row.outcome === outcome).map(row => row.returnPct);
    const expectedCount = Number(result.next_day_outcomes.counts[outcome]);
    const currentMedian = median(currentReturns);
    const baselineMedian = median(rows.baseline);
    const max = Math.max(.01, Math.abs(currentMedian), Math.abs(baselineMedian));
    const total = Number(result.next_day_outcomes.sample_count);
    const share = Number(result.next_day_outcomes.percent[outcome]);
    const what = outcome === 'stand_back' ? `收盤站回收復線 ${fmt(UPPER, 0)}` : outcome === 'continue_break' ? `收盤跌破防守線 ${fmt(LOWER, 0)}` : '收盤留在兩條線之間';
    const posShare = positivePercent(currentReturns);
    const meaning = [
      `過去和${esc(drawnDay().word)}很像的日子有 <b>${fmt(total, 0)} 次</b>，其中 <b>${fmt(expectedCount, 0)} 次</b>隔天${what}，約佔 ${fmt(share, 0)}%。`,
      Number.isFinite(posShare) ? `這 ${fmt(currentReturns.length, 0)} 次裡，隔天上漲的有 ${fmt(posShare, 0)}%（平常日子約 ${fmt(positivePercent(rows.baseline), 0)}%）。` : '',
      Number.isFinite(currentMedian) ? `一般來說隔天${currentMedian >= 0 ? '漲' : '跌'}約 ${fmt(Math.abs(currentMedian), 2)}%（平常日子約${baselineMedian >= 0 ? '漲' : '跌'} ${fmt(Math.abs(baselineMedian), 2)}%）。` : '',
    ];
    const watch = [
      '這是<b>過去的比例，不是明天的機率</b>。',
      total < 50 ? `次數不多（${fmt(total, 0)} 次），參考就好。` : '',
      outcome === 'between' && CLOSE - LOWER >= 1000 ? `兩條線相差很大，「留在中間」也可能再跌 ${fmt(Math.floor((CLOSE - LOWER) / 1000) * 1000, 0)} 點以上。` : '',
    ];
    return `
      ${plainBlock(meaning, watch)}
      ${moreDetail(`<p class="panel-lede">目前查看：${esc(sample.label || selectedSample)}，選取區間為「${esc(outcomeLabels[outcome])}」。</p>
      <div class="histogram" id="histogram" data-sample-count="${currentReturns.length}" data-expected-count="${expectedCount}">
        ${histogramSvg(currentReturns, rows.baseline, outcome)}
      </div>
      <div class="compare-chart">
        <h4>隔日報酬中位數，同一尺度</h4>
        ${returnCompareRow(outcomeLabels[outcome], currentMedian, max, 'var(--accent)')}
        ${returnCompareRow('一般交易日', baselineMedian, max, 'var(--faint)')}
      </div>
      <dl class="action-list">
        <div><dt>${esc(outcomeLabels[outcome])}有效樣本</dt><dd class="num">${fmt(currentReturns.length, 0)} 次</dd></div>
        <div><dt>該區隔日正報酬比例</dt><dd class="num">${pct(positivePercent(currentReturns))}</dd></div>
        <div><dt>一般交易日正報酬比例</dt><dd class="num">${pct(positivePercent(rows.baseline))}</dd></div>
      </dl>
      <p class="panel-callout" style="--tone:var(--gold)">直方圖由日K逐筆套用資料內同一套價位線與樣本規則後計算；兩列共用分箱、百分比尺度與完整報酬範圍。歷史條件統計不是預測機率。</p>`)}
    `;
  }

  function returnCompareRow(label, value, max, tone) {
    const size = Math.abs(value) / max * 46;
    const direction = value < 0 ? '-100%' : '0';
    return `<div class="compare-row"><span>${esc(label)}</span><span class="compare-track"><i class="compare-fill" style="--size:${size}%;--direction:${direction};--tone:${tone}"></i></span><strong class="num">${signed(value)}%</strong></div>`;
  }

  function panelTitleFor(kind, payload) {
    if (kind === 'candle') return chart.candles[payload.index].label;
    if (kind === 'line') return payload.line === 'upper' ? '收復線' : '防守線';
    if (kind === 'distribution') return `${outcomeLabels[payload.outcome]}的歷史結果`;
    if (kind === 'plain') return '直白分析';
    if (kind === 'chip') { const c = chipCards().find(item => item.key === payload.key); return c ? c.name : '籌碼'; }
    return '詳情';
  }

  function rememberPanel() {
    window.cancelAnimationFrame(panelRestoreFrame);
    const focusable = [...els.panelBody.querySelectorAll('button, summary, a, [tabindex]')];
    panelTrail.push({
      state: { ...panelState, payload: { ...panelState.payload } },
      title: els.panelTitle.textContent,
      scroll: els.panelBody.scrollTop,
      details: [...els.panelBody.querySelectorAll('details')].map(element => element.open),
      focusIndex: focusable.indexOf(document.activeElement)
    });
  }

  function updatePanelBack() {
    els.panelBack.hidden = false;
    els.panelBack.textContent = panelTrail.length ? '‹ 返回上一層' : '‹ 回到主圖';
    els.panelBack.setAttribute('aria-label', panelTrail.length ? '返回上一層' : '回到主圖');
  }

  function returnPanel() {
    if (!panelTrail.length) {
      closePanel();
      return;
    }
    const previous = panelTrail.pop();
    panelState = previous.state;
    if (panelState.layer === 2) renderLayerTwo(panelState.subkind, false);
    else renderLayerOne();
    els.panelBody.querySelectorAll('details').forEach((element, index) => {
      element.open = previous.details[index] || false;
    });
    panelRestoreFrame = requestAnimationFrame(() => {
      panelRestoreFrame = 0;
      const focusable = [...els.panelBody.querySelectorAll('button, summary, a, [tabindex]')];
      const target = focusable[previous.focusIndex];
      (target && target.getClientRects().length ? target : els.panelBack).focus({ preventScroll: true });
      els.panelBody.scrollTop = previous.scroll;
    });
  }

  function renderLayerOne() {
    window.cancelAnimationFrame(panelRestoreFrame);
    panelState.layer = 1;
    panelState.subkind = '';
    els.panel.dataset.kind = panelState.kind;
    updatePanelBack();
    els.panelNav.hidden = true;
    if (!els.panelBody.dataset.moreBound) { els.panelBody.dataset.moreBound = '1'; }
    els.panelNav.querySelectorAll('button').forEach(button => button.removeAttribute('aria-current'));
    els.panelPath.textContent = panelTrail.length ? `${panelTrail.at(-1).title} / 細部` : '細部';
    els.panelTitle.textContent = panelTitleFor(panelState.kind, panelState.payload);
    if (panelState.kind === 'candle') els.panelBody.innerHTML = renderCandlePanel(panelState.payload.index);
    else if (panelState.kind === 'line') {
      els.panelBody.innerHTML = renderLinePanel(panelState.payload.line);
      els.panelBody.querySelectorAll('[data-outcome-panel]').forEach(button => button.addEventListener('click', () => {
        rememberPanel();
        panelState = { kind: 'distribution', payload: { outcome: button.dataset.outcomePanel }, layer: 1, subkind: '' };
        renderLayerOne();
        els.panelBack.focus({ preventScroll: true });
      }));
    }
    else if (panelState.kind === 'distribution') els.panelBody.innerHTML = renderDistributionPanel(panelState.payload.outcome);
    else if (panelState.kind === 'plain') els.panelBody.innerHTML = renderPlainPanel();
    else if (panelState.kind === 'chip') { els.panelBody.innerHTML = renderChipPanel(panelState.payload.key); bindChipPanel(panelState.payload.key); }
    els.panelBody.querySelectorAll('[data-deep]').forEach(button => button.addEventListener('click', () => renderLayerTwo(button.dataset.deep)));
    els.panelBody.scrollTop = 0;
  }

  function renderLayerTwo(kind, remember = true) {
    window.cancelAnimationFrame(panelRestoreFrame);
    const entering = panelState.layer === 1 || panelState.subkind !== kind;
    if (remember && entering) rememberPanel();
    panelState.layer = 2;
    panelState.subkind = kind;
    updatePanelBack();
    els.panelPath.textContent = `${panelTitleFor(panelState.kind, panelState.payload)} / 深入`;
    els.panelNav.querySelectorAll('button').forEach(button => button.toggleAttribute('aria-current', button.dataset.layer === kind));
    if (kind === 'method') renderMethodLayer();
    else if (kind === 'sources') renderSourcesLayer();
    else if (kind === 'samples') renderSamplesLayer();
    else if (kind === 'table') renderTableLayer();
    else if (kind === 'news') renderNewsLayer();
    els.panelBody.scrollTop = 0;
    if (remember && entering) els.panelBack.focus({ preventScroll: true });
  }

  function renderMethodLayer() {
    els.panelTitle.textContent = '方法與限制';
    const method = A.backtest.method || {};
    els.panelBody.innerHTML = `
      <section class="detail-section"><h3>回測規則</h3><dl class="kv-list">
        ${Object.entries(method).map(([key, value]) => `<div><dt>${esc(methodLabel(key))}</dt><dd>${esc(value)}</dd></div>`).join('')}
      </dl></section>
      <section class="detail-section"><h3>限制</h3><ul class="limitation-list">
        ${(A.backtest.limitations || []).map(item => `<li>${esc(item)}</li>`).join('')}
      </ul></section>
    `;
  }

  function methodLabel(key) {
    const labels = {
      lines: '價位線', stand_back: '站回', continue_break: '跌破', between: '兩線間',
      next_day_return: '隔日報酬', five_day_return: '五日報酬', subset_near_low: '收在低點附近',
      subset_drop_over_3pct: '跌逾 3%', subset_long_lower_shadow: '長下影',
      subset_foreign_sell_over_50b: '外資賣超', default_sample_rule: '預設樣本', parameter_selection: '參數選擇'
    };
    return labels[key] || key;
  }

  function renderSourcesLayer() {
    els.panelTitle.textContent = '資料來源與時間';
    const endpoints = A.meta.source_endpoints || {};
    els.panelBody.innerHTML = `
      <section class="detail-section"><h3>三個時間</h3><dl class="kv-list">
        <div><dt>行情時間</dt><dd class="num">${esc(els.time.textContent)}</dd></div>
        <div><dt>分析截止日</dt><dd class="num">${esc(A.meta.cutoff_date)}</dd></div>
        ${scenarioHidden ? '' : `<div><dt>劇本適用日</dt><dd class="num">${esc(scenarioDate)}</dd></div>`}
        <div><dt>資料生成時間</dt><dd class="num">${esc(A.meta.generated_at)}</dd></div>
      </dl></section>
      <section class="detail-section"><h3>官方端點</h3><ul class="source-list">
        ${Object.entries(endpoints).map(([key, url]) => `<li><span>${esc(key)}</span><br><a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a></li>`).join('')}
      </ul></section>
      <section class="detail-section"><h3>資料說明</h3><ul class="limitation-list">
        ${(A.meta.source_notes || []).map(note => `<li>${esc(note)}</li>`).join('')}
      </ul></section>
    `;
  }

  // 樣本清單長到面板要捲才看得完的時候（手機橫放），從按鈕開始上下拖要留給捲動，不拿來拖框（樣式看這個記號）。
  // 面板開著時把手機轉向、視窗改大小也要重新判斷，不然橫放之後從按鈕上拖捲不動面板
  function markSampleListScroll() {
    const list = els.panelBody.querySelector('.sample-list');
    if (list) list.classList.toggle('is-scrollable', els.panelBody.scrollHeight > els.panelBody.clientHeight);
  }
  window.addEventListener('resize', markSampleListScroll);

  function renderSamplesLayer() {
    els.panelTitle.textContent = '回測樣本切換';
    els.panelBody.innerHTML = `
      <p class="panel-lede">切換只更新歷史統計，不改寫正式劇本規則。</p>
      <div class="sample-list">
        ${(A.backtest.samples || []).map(sample => {
          const result = A.backtest.results[sample.key];
          const count = result && (result.next_day_outcomes.sample_count ?? result.event_count);
          return `<button type="button" data-sample="${esc(sample.key)}" aria-pressed="${sample.key === selectedSample}"><span>${esc(sample.label)}</span><small class="num">隔日有效 ${fmt(count, 0)} 次</small></button>`;
        }).join('')}
      </div>
    `;
    markSampleListScroll();
    els.panelBody.querySelectorAll('[data-sample]').forEach(button => button.addEventListener('click', () => {
      selectedSample = button.dataset.sample;
      renderDistribution();
      renderSamplesLayer();
      requestAnimationFrame(() => els.panelBody.querySelector(`[data-sample="${CSS.escape(selectedSample)}"]`)?.focus());
    }));
  }

  function renderTableLayer() {
    els.panelTitle.textContent = `${periodLabels[currentPeriod]}資料表`;
    const rows = [...(chart ? chart.candles : candlesFor(currentPeriod))].reverse();
    els.panelBody.innerHTML = `
      <p class="panel-lede">目前週期完整 ${fmt(rows.length, 0)} 根，數值由同一資料來源讀取。</p>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th>日期</th><th>開</th><th>高</th><th>低</th><th>收</th><th>成交億</th></tr></thead>
        <tbody>${rows.map(row => `<tr><td>${esc(row.label)}</td><td>${fmt(row.open)}</td><td>${fmt(row.high)}</td><td>${fmt(row.low)}</td><td>${fmt(row.close)}</td><td>${Number.isFinite(row.amount) ? fmt(row.amount, 0) : '—'}</td></tr>`).join('')}</tbody>
      </table></div>
    `;
  }

  function renderNewsLayer() {
    els.panelTitle.textContent = '新聞事件時間軸';
    const news = Array.isArray(D.news) ? D.news : [];
    els.panelBody.innerHTML = news.length ? `
      <div class="timeline">${news.map(item => `
        <article><time>${esc(item.published_at || '時間未核實')}</time><h3>${esc(item.source || '來源未標')}</h3>
        <p>${esc((item.facts && item.facts[0]) || item.analyst_inference || '內容未提供')}</p>
        <p>${esc(item.analyst_inference || '')}</p>
        ${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noreferrer">查看來源</a>` : ''}</article>
      `).join('')}</div>
    ` : '<p class="panel-lede">目前沒有已核實的新聞事件。</p>';
  }

  function svgBars(values, labels, tone = 'var(--accent)', signedAxis = false) {
    const clean = values.map(value => Number(value) || 0);
    const max = Math.max(1, ...clean.map(Math.abs));
    const width = 344;
    const height = 154;
    const baseline = signedAxis ? 74 : 128;
    const usable = signedAxis ? 54 : 92;
    const step = (width - 34) / Math.max(1, clean.length);
    const bars = clean.map((value, index) => {
      const h = Math.abs(value) / max * usable;
      const x = 22 + index * step + step * .18;
      const y = signedAxis ? (value >= 0 ? baseline - h : baseline) : baseline - h;
      const fill = signedAxis ? (value >= 0 ? 'var(--up)' : 'var(--down)') : tone;
      return `<rect x="${x}" y="${y}" width="${Math.max(2, step * .62)}" height="${Math.max(1, h)}" rx="2" fill="${fill}" opacity=".78"/>`;
    }).join('');
    const labelIndexes = [...new Set([0, Math.floor((clean.length - 1) / 2), clean.length - 1])].filter(index => index >= 0);
    const text = labelIndexes.map(index => `<text x="${22 + index * step + step * .49}" y="148" text-anchor="middle">${esc(String(labels[index] || '').slice(5))}</text>`).join('');
    return `<svg viewBox="0 0 ${width} ${height}" role="img"><line class="axis" x1="18" y1="${baseline + .5}" x2="338" y2="${baseline + .5}"/>${bars}${text}</svg>`;
  }

  function openPanel(kind, payload, anchor, focusAnchor = anchor) {
    window.clearTimeout(closeTimer);
    panelTrail = [];
    panelState = { kind, payload, layer: 1, subkind: '' };
    activeAnchor = focusAnchor instanceof HTMLElement ? focusAnchor : chart.canvas;
    panelAnchor = anchor instanceof HTMLElement ? anchor : activeAnchor;
    els.panel.dataset.kind = kind;
    renderLayerOne();
    els.backdrop.hidden = false;
    els.panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('panel-open');
    window.ScrollLock?.lock(els.panel);   // 底下的頁面不跟著手指捲（scroll-lock.js）
    placePanel(panelAnchor);
    window.cancelAnimationFrame(openFrame);
    openFrame = requestAnimationFrame(() => {
      openFrame = 0;
      if (els.panel.getAttribute('aria-hidden') === 'true') return;
      els.backdrop.classList.add('is-open');
      els.panel.classList.add('is-open');
      // 11-07 起手機也置中浮窗，不再需要把選取的 K 棒捲到底部面板上方。
      els.panelClose.focus({ preventScroll: true });
    });
  }

  function placePanel(anchor) {
    if (isMobile()) {
      root.style.removeProperty('--panel-x');
      root.style.removeProperty('--panel-y');
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(440, innerWidth - 24);
    const estimatedHeight = Math.min(680, innerHeight * .78);
    let left = rect.right + 14;
    if (left + width > innerWidth - 12) left = rect.left - width - 14;
    left = Math.max(12, Math.min(innerWidth - width - 12, left));
    const top = Math.max(12, Math.min(innerHeight - estimatedHeight - 12, rect.top - 12));
    root.style.setProperty('--panel-x', `${left}px`);
    root.style.setProperty('--panel-y', `${top}px`);
  }


  function closePanel() {
    if (els.panel.getAttribute('aria-hidden') === 'true') return;
    window.cancelAnimationFrame(openFrame);
    window.cancelAnimationFrame(panelRestoreFrame);
    panelTrail = [];
    openFrame = 0;
    els.backdrop.classList.remove('is-open');
    els.panel.classList.remove('is-open');
    els.panel.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('panel-open');
    window.ScrollLock?.unlock(els.panel);
    const target = activeAnchor;
    activeAnchor = null;
    panelAnchor = null;
    if (target && typeof target.focus === 'function') target.focus({ preventScroll: true });
    closeTimer = window.setTimeout(() => {
      els.backdrop.hidden = true;
      els.panel.dataset.kind = '';
    }, reducedMotion ? 0 : 220);
  }

  function trapFocus(event) {
    if (event.key !== 'Tab' || els.panel.getAttribute('aria-hidden') === 'true') return;
    const focusable = [...els.panel.querySelectorAll('button:not([hidden]), a[href], select, [tabindex]:not([tabindex="-1"])')]
      .filter(element => !element.disabled && element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!els.panel.contains(document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function renderPayload(payload) {
    latestPayload = payload;
    const data = payload && payload.data;
    if (!data) {
      latestLive = false;
      els.service.hidden = false;
      // 伺服器錯誤原文（可能是英文的執行環境訊息）只進 console，畫面只放固定中文說明。
      console.warn('[即時行情]', payload && payload.error);
      els.service.textContent = '即時行情暫時無法更新，目前顯示盤後資料。';
      setMarketHeader(fallbackMarketData(), { id: 'delay', label: '延遲' }, fallbackMarketDate());
      setIntradayOptions(false);
      loadPeriod(isMinutePeriod(currentPeriod) ? 'daily' : currentPeriod, false);
      return;
    }
    const fetched = new Date(payload.fetched_at).getTime();
    const dataStamp = new Date(payload.data_time).getTime();
    const fetchedAge = Date.now() - fetched;
    const dataAge = Date.now() - dataStamp;
    const recent = Number.isFinite(fetched) && fetchedAge >= -5000 && fetchedAge <= 30_000;
    const dataFresh = Number.isFinite(dataStamp) && dataAge >= -5000 && dataAge <= 30_000;
    latestLive = payload.ok === true && payload.live === true && payload.market_open === true && recent && dataFresh;
    let state;
    if (latestLive) state = { id: 'live', label: '即時' };
    else if (payload.market_open || payload.retryable || ['unknown', 'delayed'].includes(payload.phase)) state = { id: 'delay', label: '延遲' };
    else state = { id: 'closed', label: '休市' };
    els.service.hidden = true;
    const payloadDay = datePart(payload.data_time);
    const officialDay = datePart(fallbackMarketDate());
    const officialCloseAvailable = !payload.market_open && officialDay && payloadDay && officialDay >= payloadDay;
    const displayedData = officialCloseAvailable ? fallbackMarketData() : data;
    const displayedStamp = officialCloseAvailable ? officialDay : formatStamp(payload.data_time);
    setMarketHeader(displayedData, state, displayedStamp);
    setIntradayOptions(Array.isArray(payload.series) && payload.series.length > 0);
    loadPeriod(currentPeriod, false);
  }

  function schedulePoll(delay = 7000) {
    window.clearTimeout(pollTimer);
    nextPollDelay = delay;
    if (document.hidden || location.protocol === 'file:') return;
    pollTimer = window.setTimeout(pollLive, delay);
  }

  async function pollLive() {
    if (polling || document.hidden || location.protocol === 'file:') return;
    polling = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 6500);
    try {
      const response = await fetch('/api/live', { cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal });
      // 先確認是 JSON 再解析：伺服器回 HTML（例如登入頁或首頁）時，Safari 的解析錯誤訊息看不懂。
      const isJson = (response.headers.get('content-type') || '').includes('application/json');
      const body = isJson ? await response.json() : null;
      if (body && body.error === 'live-not-available') {
        latestLive = false;
        els.service.hidden = false;
        els.service.textContent = `${body.message}。`;
        setMarketHeader(fallbackMarketData(), { id: 'closed', label: '盤後' }, fallbackMarketDate());
        setIntradayOptions(false);
        return; // 雲端沒有即時行情，不再輪詢
      }
      if (!isJson) throw Object.assign(new Error('回應不是行情資料'), { note: '即時行情無法更新：登入可能已失效，請重新整理。目前顯示盤後資料。' });
      const payload = body;
      if (!response.ok && !payload) throw new Error(`HTTP ${response.status}`);
      renderPayload(payload);
      const headerRetry = Number(response.headers.get('retry-after') || 0) * 1000;
      const retry = Number(payload.retry_after_seconds || 0) * 1000;
      if (payload.market_open || payload.retryable || response.status === 429) schedulePoll(Math.max(7000, retry, headerRetry));
      else { window.clearTimeout(pollTimer); nextPollDelay = 0; }
    } catch (error) {
      latestLive = false;
      els.service.hidden = false;
      // 瀏覽器／伺服器的原文（例如 Failed to fetch）只進 console。
      console.warn('[即時行情]', error);
      els.service.textContent = error.note
        || (error.name === 'AbortError' ? '即時行情連線逾時，目前顯示盤後資料。' : '即時行情連線中斷，目前顯示盤後資料。');
      setMarketHeader(fallbackMarketData(), { id: 'delay', label: '延遲' }, fallbackMarketDate());
      setIntradayOptions(false);
      loadPeriod(isMinutePeriod(currentPeriod) ? 'daily' : currentPeriod, false);
      schedulePoll(15000);
    } finally {
      window.clearTimeout(timeout);
      polling = false;
    }
  }

  els.upper.textContent = fmt(UPPER);
  els.lower.textContent = fmt(LOWER);
  renderDistribution();
  // 人寫的劇本過期：標頭的劇本日期收起來。圖旁兩個價位按鈕與隔日分布每天都有，只有線還是從過期的劇本抄來時才跟著收。
  // 直白分析另看它自己的資料日。
  for (const node of [els.scenarioDate.parentElement, els.scenarioDate.parentElement.previousElementSibling]) {
    if (node) node.hidden = scenarioHidden;
  }
  showLineKeys();
  $('readCard').hidden = plainStale;
  setMarketHeader(fallbackMarketData(), { id: 'closed', label: '休市' }, fallbackMarketDate());

  els.period.addEventListener('change', () => loadPeriod(els.period.value, true));
  els.reset.addEventListener('click', () => {
    if (!chart) return;
    chart.reset();
    enforceCandleHitTarget();
  });
  els.lineUpper.addEventListener('click', () => openPanel('line', { line: 'upper' }, els.lineUpper));
  els.lineLower.addEventListener('click', () => openPanel('line', { line: 'lower' }, els.lineLower));
  // 點報價、行情狀態、K 線上的現價點：直接打開收復線那一張（本人 2026-11-17 看樣品後選「點報價直接開收復線那張」，
  // 原本另一張全是字的「現價落點」拿掉）。兩條線收起來的時候（劇本過期、線過時）沒有東西可開，點了不做事
  for (const trigger of [$('priceSummary'), els.stateButton, els.point]) {
    trigger.addEventListener('click', () => { if (!linesHidden()) openPanel('line', { line: 'upper' }, trigger); });
  }
  els.mobileDetails.addEventListener('click', () => {
    if (selectedIndex >= 0) openPanel('candle', { index: selectedIndex }, els.marker, els.mobileDetails);
  });
  els.distributionBar.querySelectorAll('[data-outcome]').forEach(button => button.addEventListener('click', () => openPanel('distribution', { outcome: button.dataset.outcome }, button)));
  els.panelClose.addEventListener('click', closePanel);
  els.backdrop.addEventListener('click', closePanel);
  els.panelBack.addEventListener('click', returnPanel);
  els.panelNav.querySelectorAll('[data-layer]').forEach(button => button.addEventListener('click', () => renderLayerTwo(button.dataset.layer)));

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closePanel();
    trapFocus(event);
  });
  addEventListener('resize', () => {
    chart && chart.draw();
    updateOverlayControls();
    if (els.panel.getAttribute('aria-hidden') === 'false' && panelAnchor) placePanel(panelAnchor);
  });
  document.addEventListener('visibilitychange', () => {
    window.clearTimeout(pollTimer);
    if (!document.hidden) pollLive();
  });

  // 主題的選擇存在瀏覽器裡，和個股頁、自選庫存頁用同一個名字：換頁之後才會是同一種背景色（本人 11-15 回報換頁會跑掉）。
  const THEME_KEY = 'market-theme';
  function readSavedTheme() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved === 'light' || saved === 'dark') root.dataset.theme = saved;
    } catch (_) { /* 私密視窗可能讀不到 */ }
  }
  readSavedTheme();
  // 按瀏覽器「上一頁」回來時，頁面是整頁還原的、不會重新載入：這時要再讀一次（可能在別頁改過），圖也要用新的顏色重畫
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    readSavedTheme();
    chart && chart.draw();
    requestAnimationFrame(updateOverlayControls);
  });

  // 主題在設定浮板裡切換（settings.js，本人 11-17 選樣品 A），切換後發 market:theme：圖用新的顏色重畫
  document.addEventListener('market:theme', () => {
    chart && chart.draw();
    requestAnimationFrame(updateOverlayControls);
  });
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (!root.dataset.theme) {
      chart && chart.draw();
      requestAnimationFrame(updateOverlayControls);
    }
  });

  renderChips();
  loadPeriod('daily', true);
  api.ready = true;
  if (window.MarketForecast) {
    window.MarketForecast.mount({
      host: document.querySelector('.chart-controls'),
      onChange: () => loadPeriod(currentPeriod, false),
      getDaily: () => A.daily,
    });
  }

  if (location.protocol === 'file:') {
    setOffline('即時服務未啟動，目前仍可使用盤後資料。請執行專案內的 python live_server.py，再開啟 http://127.0.0.1:8876/minimal.html。');
  } else {
    pollLive();
  }
})();

// 票 05／06：首頁上市／上櫃普通股搜尋獨立於大盤資料初始化，搜尋失敗不影響既有首頁。
(() => {
  'use strict';
  const form = document.getElementById('stockSearchForm');
  const input = document.getElementById('stockSearchInput');
  const clear = document.getElementById('stockSearchClear');
  const status = document.getElementById('stockSearchStatus');
  const results = document.getElementById('stockSearchResults');
  const more = document.getElementById('stockSearchMore');
  const suggestions = document.getElementById('stockSearchSuggestions');
  if (!form || !input || !clear || !status || !results || !more || !suggestions) return;

  let controller = null;
  let sequence = 0;
  let debounceTimer = 0;
  let activeQuery = '';
  let nextCursor = null;
  let suggestionRequest = 0;

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      cache: 'no-store',
      headers: options.body ? { 'content-type': 'application/json' } : undefined,
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || '暫時無法讀取');
    return payload;
  }

  async function recordAndOpen(event, code, href) {
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button > 0)) return;
    if (event) event.preventDefault();
    let navigated = false;
    const go = () => {
      if (navigated) return;
      navigated = true;
      location.assign(href);
    };
    const fallback = window.setTimeout(go, 900);
    try { await requestJson('/api/search-history', { method: 'POST', body: { code } }); } catch (_) {}
    window.clearTimeout(fallback);
    go();
  }

  function suggestionRow(item, { removable = false } = {}) {
    const row = document.createElement('li');
    row.className = 'stock-suggest-row';
    const link = document.createElement('a');
    link.className = 'stock-suggest-link';
    link.href = `stock.html?code=${encodeURIComponent(item.code)}`;
    const title = document.createElement('strong');
    title.textContent = `${item.code} ${item.name || ''}`.trim();
    const meta = document.createElement('small');
    meta.textContent = [item.market, item.horizon === 'short' ? '本人標記短期' : item.horizon === 'long' ? '本人標記長期' : ''].filter(Boolean).join(' · ');
    link.append(title, meta);
    link.addEventListener('click', event => recordAndOpen(event, item.code, link.href));
    row.append(link);
    if (removable) {
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'stock-suggest-remove';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `刪除 ${item.code} ${item.name || ''} 的搜尋紀錄`);
      remove.addEventListener('click', async () => {
        try {
          await requestJson(`/api/search-history/${encodeURIComponent(item.code)}`, { method: 'DELETE', body: {} });
          await loadSuggestions();
        } catch (_) { setStatus('搜尋紀錄暫時無法刪除。', 'error'); }
      });
      row.append(remove);
    }
    return row;
  }

  function suggestionGroup(title, items, { clearAll = false, removable = false, notice = '' } = {}) {
    const section = document.createElement('section');
    section.className = 'stock-suggest-group';
    const head = document.createElement('div');
    head.className = 'stock-suggest-head';
    const heading = document.createElement('h3');
    heading.textContent = title;
    head.append(heading);
    if (clearAll && items.length) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'stock-suggest-clear';
      button.textContent = '清除全部';
      button.addEventListener('click', async () => {
        if (!confirm('要清除全部搜尋紀錄嗎？')) return;
        try {
          await requestJson('/api/search-history', { method: 'DELETE', body: {} });
          await loadSuggestions();
        } catch (_) { setStatus('搜尋紀錄暫時無法清除。', 'error'); }
      });
      head.append(button);
    }
    const list = document.createElement('ul');
    list.className = 'stock-suggest-list';
    if (notice) {
      const warning = document.createElement('li');
      warning.className = 'stock-suggest-empty stock-suggest-error';
      warning.textContent = notice;
      list.append(warning);
    }
    if (items.length) list.append(...items.map(item => suggestionRow(item, { removable })));
    else if (!notice) {
      const empty = document.createElement('li');
      empty.className = 'stock-suggest-empty';
      empty.textContent = title === '最近搜尋' ? '還沒有搜尋紀錄' : '目前沒有可顯示的股票';
      list.append(empty);
    }
    section.append(head, list);
    return section;
  }

  function uniqueStocks(...groups) {
    const seen = new Set();
    return groups.flat().filter(item => item?.code && !seen.has(item.code) && seen.add(item.code));
  }

  async function loadSuggestions() {
    if (input.value.trim()) { suggestions.hidden = true; return; }
    const request = ++suggestionRequest;
    suggestions.hidden = false;
    suggestions.replaceChildren();
    const loading = document.createElement('p');
    loading.className = 'stock-suggest-empty';
    loading.textContent = '正在讀取最近搜尋與分類…';
    suggestions.append(loading);
    try {
      const settled = await Promise.allSettled([
        requestJson('/api/search-history'), requestJson('/api/watchlist'), requestJson('/api/screens'), requestJson('/api/core-holdings'),
      ]);
      const value = (index, fallback) => settled[index].status === 'fulfilled' ? settled[index].value : fallback;
      const history = value(0, { items: [] });
      const watchlist = value(1, { items: [] });
      const catalog = value(2, { tabs: [] });
      const core = value(3, { holdings: [] });
      const historyFailed = settled[0].status === 'rejected';
      const watchlistFailed = settled[1].status === 'rejected';
      let shortSystemFailed = settled[2].status === 'rejected';
      const coreFailed = settled[3].status === 'rejected';
      const firstShort = (catalog.tabs || []).find(tab => ['rank', 'streak', 'relative_value'].includes(tab.kind));
      let shortPayload = { items: [] };
      if (firstShort) {
        try { shortPayload = await requestJson(`/api/screens?key=${encodeURIComponent(firstShort.key)}`); }
        catch (_) { shortSystemFailed = true; }
      }
      if (request !== suggestionRequest || input.value.trim()) return;
      const ownShort = (watchlist.items || []).filter(item => item.horizon === 'short');
      const ownLong = (watchlist.items || []).filter(item => item.horizon === 'long');
      const systemShort = (shortPayload.items || []).filter(item => item.security_type !== 'ETF' || item.code === '0050').slice(0, 5);
      const shortItems = uniqueStocks(ownShort, systemShort);
      const longItems = uniqueStocks(ownLong, core.holdings || []);
      const shortNotice = watchlistFailed && shortSystemFailed ? '短期分類暫時無法讀取'
        : watchlistFailed ? '本人標記的短期分類暫時無法讀取'
          : shortSystemFailed ? '系統短期名單暫時無法讀取' : '';
      const longNotice = watchlistFailed && coreFailed ? '長期分類暫時無法讀取'
        : watchlistFailed ? '本人標記的長期分類暫時無法讀取'
          : coreFailed ? '系統長期名單暫時無法讀取' : '';
      suggestions.replaceChildren(
        suggestionGroup('最近搜尋', history.items || [], {
          clearAll: true, removable: true, notice: historyFailed ? '最近搜尋暫時無法讀取' : '',
        }),
        suggestionGroup('短期', shortItems, { notice: shortNotice }),
        suggestionGroup('長期', longItems, { notice: longNotice }),
      );
    } catch (_) {
      if (request !== suggestionRequest) return;
      suggestions.replaceChildren();
      const failed = document.createElement('p');
      failed.className = 'stock-suggest-empty';
      failed.textContent = '搜尋建議暫時無法讀取，仍可直接輸入代號或名稱。';
      suggestions.append(failed);
    }
  }

  function setStatus(message, state) {
    status.textContent = message;
    status.dataset.state = state;
    status.hidden = !message;
  }

  function reset(message = '') {
    sequence += 1;
    if (controller) controller.abort();
    controller = null;
    activeQuery = '';
    nextCursor = null;
    results.replaceChildren();
    results.removeAttribute('aria-busy');
    more.hidden = true;
    setStatus(message, 'empty');
  }

  // 基金（基金當成一般個股 03 票，本人 11-18）是搜尋結果裡的一般一筆：點了進個股頁、記進最近搜尋，和股票一樣；
  // 只是寫「基金」而不是市場別，資料日是淨值日（基金一天一個淨值、約晚 3 個營業日公布）。
  function resultItem(item) {
    const fund = item.security_type === '基金';
    const row = document.createElement('li');
    row.className = 'stock-search-result';
    const link = document.createElement('a');
    link.href = `stock.html?code=${encodeURIComponent(item.code)}`;
    link.dataset.code = item.code;
    link.addEventListener('click', event => recordAndOpen(event, item.code, link.href));

    const title = document.createElement('strong');
    title.textContent = `${item.code} ${item.name}`;
    const market = document.createElement('small');
    market.className = 'stock-search-market';
    market.textContent = fund ? '基金 · 境外基金' : `${item.market}${item.security_type} · 名錄資料日 ${item.source_date}`;
    const availability = document.createElement('small');
    availability.className = 'stock-search-meta';
    const dataWord = fund ? '淨值' : '資料';
    if (item.analysis_status === 'stale') {
      availability.textContent = `可分析 · ${dataWord}至 ${item.data_date || '未提供'}，已過期`;
    } else if (item.analysis_status === 'insufficient') {
      availability.textContent = fund ? `無法分析 · 淨值資料不足（${item.available_bars || 0} 筆）` : `無法分析 · 歷史資料不足（${item.available_bars || 0} 根）`;
    } else if (item.analysis_status === 'unavailable') {
      availability.textContent = '無法分析 · 最新交易日停牌或無有效行情';
    } else {
      availability.textContent = `可分析 · ${dataWord}至 ${item.data_date || '未提供'}`;
    }
    link.append(title, market, availability);
    row.append(link);
    return row;
  }

  async function search(query, cursor = null) {
    const normalized = query.trim();
    if (!normalized) { reset(); return; }
    suggestions.hidden = true;
    if (controller) controller.abort();
    controller = new AbortController();
    const request = ++sequence;
    const append = cursor !== null;
    if (!append) {
      activeQuery = normalized;
      nextCursor = null;
      results.replaceChildren();
    }
    results.setAttribute('aria-busy', 'true');
    more.hidden = true;
    setStatus(append ? '載入更多結果中…' : `正在搜尋「${normalized}」…`, 'loading');
    const params = new URLSearchParams({ q: normalized, limit: '8' });
    if (cursor !== null) params.set('cursor', cursor);
    try {
      const response = await fetch(`/api/stocks/search?${params}`, {
        cache: 'no-store',
        signal: controller.signal,
      });
      const payload = await response.json();
      if (request !== sequence) return;
      if (!response.ok || payload.error) throw new Error(payload.message || '搜尋暫時無法使用');
      const items = Array.isArray(payload.items) ? payload.items : [];
      results.append(...items.map(resultItem));
      nextCursor = payload.next_cursor || null;
      more.hidden = nextCursor === null;
      const shown = results.children.length;
      setStatus(
        shown === 0 ? `查無「${normalized}」的上市或上櫃股票。` : `已顯示 ${shown} 檔結果，名錄資料日 ${payload.source_date || '未提供'}。`,
        shown === 0 ? 'no-results' : 'ready'
      );
    } catch (error) {
      if (request !== sequence || error.name === 'AbortError') return;
      results.replaceChildren();
      nextCursor = null;
      more.hidden = true;
      setStatus('搜尋暫時無法使用，請稍後再試。', 'error');
    } finally {
      if (request === sequence) results.removeAttribute('aria-busy');
    }
  }

  function scheduleSearch() {
    window.clearTimeout(debounceTimer);
    const query = input.value.trim();
    if (!query) { reset(); return; }
    debounceTimer = window.setTimeout(() => search(query), 220);
  }

  input.addEventListener('input', () => {
    if (input.value.trim()) suggestions.hidden = true;
    else loadSuggestions();
    scheduleSearch();
  });
  input.addEventListener('focus', () => { if (!input.value.trim()) loadSuggestions(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { suggestions.hidden = true; input.blur(); }
  });
  input.addEventListener('blur', () => window.setTimeout(() => {
    if (!suggestions.contains(document.activeElement)) suggestions.hidden = true;
  }, 120));
  form.addEventListener('submit', event => {
    event.preventDefault();
    window.clearTimeout(debounceTimer);
    search(input.value);
  });
  clear.addEventListener('click', () => {
    window.clearTimeout(debounceTimer);
    input.value = '';
    reset('搜尋已清空。');
    input.focus();
    loadSuggestions();
  });
  more.addEventListener('click', () => {
    if (nextCursor !== null && activeQuery) search(activeQuery, nextCursor);
  });
})();

// G3：首頁三區塊（籌碼選股／產業資金流向／長期核心持有），只讀已封存的發布結果。
// 本人 2026-11-14：搜尋改在浮起的面板裡，不再佔首頁版面，原本「搜尋時讓位」的行為取消。
// 本人 2026-11-08：自創候選退役；演算法依參考網站反推結果，畫面名稱為本站用語。
(() => {
  'use strict';
  const host = document.getElementById('marketScreens');
  if (!host) return;
  const $ = id => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const MARKET_TEXT = { 上市: '上市', 上櫃: '上櫃' };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function svg(tag, attrs) {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  }
  async function getJson(url) {
    const response = await fetch(url, { cache: 'no-store' });
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.message || '資料暫時無法讀取');
    return payload;
  }
  function fmtNum(value, digits = 0) {
    return Number(value).toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function signed(value, digits = 1) {
    if (value === null || value === undefined) return '—';
    return `${value > 0 ? '+' : ''}${fmtNum(value, digits)}`;
  }
  function tooltip(container) {
    let tip = container.querySelector('.chart-tip');
    if (!tip) { tip = el('div', 'chart-tip'); tip.hidden = true; container.append(tip); }
    return {
      show(lines, x, y) {
        tip.replaceChildren(...lines.map((line, i) => (i === 0 ? el('strong', '', line) : el('div', '', line))));
        tip.hidden = false;
        const box = container.getBoundingClientRect();
        const w = tip.offsetWidth;
        tip.style.left = `${Math.max(0, Math.min(x + 12, box.width - w))}px`;
        // 上方放不下就放到點的下方，不蓋住被說明的泡泡本身
        const above = y - tip.offsetHeight - 10;
        tip.style.top = `${above >= 0 ? above : y + 24}px`;
      },
      hide() { tip.hidden = true; },
    };
  }

  // ---- 籌碼選股 ----
  const chip = {
    block: $('chipScreens'), meta: $('chipScreensMeta'), tabs: $('chipScreenTabs'), controls: $('chipScreenControls'),
    days: $('chipScreenDays'), side: $('chipScreenSide'), inflow: $('chipScreenInflow'),
    status: $('chipScreenStatus'), list: $('chipScreenList'), more: $('chipScreenMore'), panel: $('chipScreenPanel'),
  };
  const view = { key: null, days: 1, side: 'buy', inflow: false, expanded: false, catalog: null, sequence: 0 };
  const SIDE_TEXT = { buy: '淨買超', sell: '淨賣超' };

  function setChipState(state, message) {
    chip.block.dataset.state = state;
    chip.status.textContent = message;
  }

  function renderControls(tab) {
    const computable = tab.kind === 'rank' || tab.kind === 'streak';
    chip.controls.hidden = !computable;
    chip.days.replaceChildren();
    chip.days.hidden = tab.kind !== 'rank';
    if (tab.kind === 'rank') {
      tab.day_options.forEach(days => {
        const button = el('button', '', days === 1 ? '當日' : `${days} 日`);
        button.type = 'button';
        button.dataset.days = String(days);
        button.setAttribute('aria-pressed', String(days === view.days));
        button.addEventListener('click', () => { view.days = days; view.expanded = false; load(); });
        chip.days.append(button);
      });
    }
    chip.side.querySelectorAll('button').forEach(button => {
      button.textContent = tab.kind === 'streak' ? (button.dataset.side === 'buy' ? '連續淨買超' : '連續淨賣超') : SIDE_TEXT[button.dataset.side];
      button.setAttribute('aria-pressed', String(button.dataset.side === view.side));
    });
    chip.inflow.closest('label').hidden = !tab.inflow_filter;
    chip.inflow.checked = view.inflow;
  }

  function row(item, index, tab) {
    const li = document.createElement('li');
    // 排行母體含名次前 300 的 ETF（照參考站）；個股分析只涵蓋普通股與核心持有 0050，其他 ETF 不連到個股頁。
    const isEtf = item.security_type === 'ETF';
    const linkable = !isEtf || item.code === '0050';
    const link = el(linkable ? 'a' : 'div', linkable ? '' : 'screen-static');
    if (linkable) link.href = `stock.html?code=${encodeURIComponent(item.code)}`;
    link.dataset.code = item.code;
    const title = el('strong', '', `${item.code} ${item.name || ''}`);
    if (isEtf) title.append(el('span', 'screen-flag screen-etf', 'ETF'));
    if (item.rotation_star) title.append(el('span', 'screen-flag', '資金流入產業'));
    const meta = el('small', '', [MARKET_TEXT[item.market] || item.market,
      item.close !== null && item.close !== undefined ? `收 ${fmtNum(item.close, 2)}` : '',
      item.change_pct !== null && item.change_pct !== undefined ? `${signed(item.change_pct, 2)}%` : '']
      .filter(Boolean).join(' · '));
    let value;
    if (tab.kind === 'streak') {
      value = `${item.streak_days}${item.streak_is_lower_bound ? '+' : ''} 天`;
    } else if (tab.kind === 'relative_value') {
      // 相對低位階：個股 20 日乖離 < 所屬市場指數 20 日乖離（R1 會員 127 檔驗證）；顯示乖離而不是張數。
      value = item.bias20 === null || item.bias20 === undefined ? '—' : `乖離 ${signed(item.bias20 * 100, 1)}%`;
      if (item.index_bias20 !== null && item.index_bias20 !== undefined) {
        meta.textContent = [meta.textContent, item.group, `${item.index_name || '指數'} ${signed(item.index_bias20 * 100, 1)}%`]
          .filter(Boolean).join(' · ');
      }
    } else {
      value = `${signed(item.metric_lots, 0)} 張`;
    }
    link.append(el('span', 'screen-rank', String(index + 1)), title, el('span', 'screen-value', value), meta);
    li.append(link);
    return li;
  }

  function stateMessage(payload, tab) {
    const dated = payload.data_date ? `資料日 ${payload.data_date}` : '';
    if (payload.status === 'not_ready') return ['not_ready', `${tab.display_name}尚未就緒：還沒有已封存的發布結果。`];
    if (payload.state === 'unknown') return ['unknown', `定義未公開：參考網站沒有公開第二個條件的算法，本站不自訂，因此不提供名單。`];
    if (payload.state === 'unavailable') return ['unavailable', '無合法資料來源：需要逐日券商分點資料，目前沒有涵蓋全市場的合法免費來源，因此不提供名單。'];
    if (payload.state === 'missing') return ['empty', `${dated}：交易日不足或資料缺漏，本頁沒有可排序的結果。`];
    const extra = [];
    if (payload.uncovered_markets && payload.uncovered_markets.length) extra.push(`${payload.uncovered_markets.join('、')}未涵蓋`);
    if (payload.missing_count) extra.push(`${fmtNum(payload.missing_count)} 檔當期缺法人資料，未列入（不當成 0）`);
    const stale = payload.status === 'stale' ? `已過期（最新行情 ${payload.latest_market_data_date}），僅供查看舊結果。` : '';
    const shown = payload.items.length ? `共 ${payload.items.length} 檔` : '沒有符合條件的個股';
    return [payload.status === 'stale' ? 'stale' : (payload.state === 'partial' ? 'partial' : 'ok'),
      `${stale}${dated} · ${shown}${extra.length ? `；${extra.join('；')}` : ''}。`];
  }

  async function load() {
    const tab = view.catalog.tabs.find(t => t.key === view.key);
    renderControls(tab);
    chip.list.replaceChildren();
    chip.more.hidden = true;
    chip.panel.querySelector('.screen-conditions')?.remove();
    const request = ++view.sequence;
    setChipState('loading', `正在讀取${tab.display_name}…`);
    const params = new URLSearchParams({ key: tab.key });
    if (tab.kind === 'rank') params.set('days', String(view.days));
    if (tab.kind === 'rank' || tab.kind === 'streak') params.set('side', view.side);
    if (tab.inflow_filter && view.inflow) params.set('only_inflow', '1');
    try {
      const payload = await getJson(`/api/screens?${params}`);
      if (request !== view.sequence) return;
      const [state, message] = stateMessage(payload, tab);
      setChipState(state, message);
      if (state === 'unknown' || state === 'unavailable') {
        const ul = el('ul', 'screen-conditions');
        (payload.display_conditions || []).forEach(text => ul.append(el('li', '', text)));
        chip.status.after(ul);
        return;
      }
      const items = payload.items || [];
      const visible = view.expanded ? items : items.slice(0, 5);
      chip.list.append(...visible.map((item, index) => row(item, index, tab)));
      chip.more.hidden = view.expanded || items.length <= 5;
      chip.more.textContent = `展開到 ${items.length} 名`;
    } catch (error) {
      if (request !== view.sequence) return;
      setChipState('error', `${tab.display_name}暫時無法讀取，請稍後再試。`);
    }
  }

  function selectTab(key) {
    view.key = key;
    view.expanded = false;
    const tab = view.catalog.tabs.find(t => t.key === key);
    if (tab.kind !== 'rank') view.days = 1;
    chip.tabs.querySelectorAll('[role="tab"]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.key === key)));
    chip.panel.setAttribute('aria-labelledby', `chipTab-${key}`);
    load();
  }

  async function initChips() {
    try {
      view.catalog = await getJson('/api/screens');
    } catch (error) {
      chip.meta.textContent = '';
      setChipState('error', '籌碼選股暫時無法讀取，請稍後再試。');
      return;
    }
    chip.meta.textContent = view.catalog.data_date ? `資料日 ${view.catalog.data_date}` : '尚無已發布結果';
    // 九頁都列在第一枚小鍵的選單裡（本人 11-15 改成收進選單）：可算的排前面，沒有名單的（定義未公開、無合法資料來源）集中在後面一組。
    const listable = tab => ['rank', 'streak', 'relative_value'].includes(tab.kind);
    const ordered = [...view.catalog.tabs.filter(listable), ...view.catalog.tabs.filter(tab => !listable(tab))];
    let dividerAdded = false;
    ordered.forEach(tab => {
      if (!listable(tab) && !dividerAdded && ordered.some(listable)) {
        dividerAdded = true;
        const cap = el('span', 'hm-tab-cap', '目前沒有名單');
        cap.setAttribute('role', 'presentation');
        chip.tabs.append(cap);
      }
      const button = el('button', listable(tab) ? '' : 'is-unlisted', tab.display_name);
      button.type = 'button';
      button.id = `chipTab-${tab.key}`;
      button.setAttribute('role', 'tab');
      button.dataset.key = tab.key;
      button.dataset.kind = tab.kind === 'rotation_unknown' ? 'unknown' : tab.kind;
      button.setAttribute('aria-controls', 'chipScreenPanel');
      button.addEventListener('click', () => selectTab(tab.key));
      chip.tabs.append(button);
    });
    chip.side.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
      view.side = button.dataset.side; view.expanded = false; load();
    }));
    chip.inflow.addEventListener('change', () => { view.inflow = chip.inflow.checked; view.expanded = false; load(); });
    chip.more.addEventListener('click', () => { view.expanded = true; load(); });
    selectTab(view.catalog.tabs[0].key);
  }

  // ---- 產業資金流向 ----
  // 首屏是資金地形（terrain.js）；資金效率排行、低基期回溫訊號與表格在點開的面板裡（本人 2026-11-14 定案）。
  const rot = {
    block: $('rotationBlock'), meta: $('rotationMeta'), status: $('rotationStatus'), body: $('rotationBody'),
    terrain: $('rotationTerrain'), efficiency: $('rotationEfficiency'),
    recovery: $('rotationRecovery'), table: $('rotationTable'),
  };

  function renderRotation(payload) {
    if (payload.status === 'not_ready') {
      rot.block.dataset.state = 'not_ready';
      rot.meta.textContent = '';
      rot.status.textContent = '產業資金流向尚未就緒：還沒有已封存的發布結果。';
      return;
    }
    const latest = payload.latest;
    rot.block.dataset.state = payload.status;
    rot.meta.textContent = `資料日 ${latest.date}`;
    const stale = payload.status === 'stale' ? `已過期（最新行情 ${payload.latest_market_data_date}），僅供查看舊結果。` : '';
    rot.status.textContent = `${stale}${latest.summary ? latest.summary.text : ''}`;
    window.Terrain.render(rot.terrain, { bubbles: latest.bubbles, quadrants: payload.quadrants });
    rot.efficiency.replaceChildren(...(latest.efficiency_ranking || []).map(r => {
      const li = el('li', '', `${r.name} `);
      li.append(el('small', '', fmtNum(r.score, 1)));
      return li;
    }));
    if (!rot.efficiency.children.length) rot.efficiency.append(el('li', '', '今日沒有成交額高於平均的產業'));
    rot.recovery.replaceChildren(...(latest.recovery_signals || []).map(r => {
      const li = el('li', '', `${r.name} `);
      li.append(el('small', '', `${signed(r.y)} 億元/日`));
      return li;
    }));
    if (!rot.recovery.children.length) rot.recovery.append(el('li', '', '今日沒有低於平均但加速的產業'));
    const table = el('table');
    const head = el('tr');
    ['產業', '資金狀態', '相對平均', '加速度（億元/日）', '20 日成交額（十億元）'].forEach(t => head.append(el('th', '', t)));
    table.append(head);
    latest.bubbles.forEach(b => {
      const tr = el('tr');
      tr.append(el('td', '', b.name), el('td', '', b.quadrant ? payload.quadrants[b.quadrant].label : '—'),
        el('td', 'num', b.relative_pct === null ? '—' : `${signed(b.relative_pct, 0)}%`), el('td', 'num', signed(b.y)),
        el('td', 'num', fmtNum(b.amount_20d, 1)));
      table.append(tr);
    });
    rot.table.replaceChildren(table);
    rot.body.hidden = false;
  }

  async function initRotation() {
    try {
      renderRotation(await getJson('/api/rotation'));
    } catch (error) {
      // 地形與「排行與表格」的入口只在整段都畫好之後才顯示；中途出錯就收起來，不留一個點開是空的面板
      rot.body.hidden = true;
      rot.block.dataset.state = 'error';
      rot.status.textContent = '產業資金流向暫時無法讀取，請稍後再試。';
    }
  }

  // ---- 長期核心持有：最多五個代號＋固定 0050 基準 ----
  const core = {
    block: $('coreHoldings'), meta: $('coreHoldingsMeta'), cards: $('coreCards'), chart: $('coreChart'),
    form: $('coreCompareForm'), input: $('coreCompareInput'), status: $('coreStatus'), selection: $('coreSelection'),
    period: $('corePeriodSelect')
  };
  const CORE_VAR = ['--compare-0', '--compare-1', '--compare-2', '--compare-3', '--compare-4', '--compare-5'];
  const CORE_DASH = ['', '7 4', '2 3', '10 4 2 4', '1 3', '12 3'];
  let coreReport = null;
  let coreCodes = ['2330'];
  let coreRequest = 0;

  function corePeriodRow(entity, key) {
    return (entity.periods || []).find(row => row.period === key) || null;
  }

  function coreEntities(report) {
    const benchmark = { ...report.benchmark, role: '0050 基準', isBenchmark: true };
    // 基金淨值約晚 3 個營業日，報酬比到它自己的最後淨值日（0050 也截在同一天）；和資料日不同時寫在名稱旁（05 票）
    const role = item => item.kind === 'fund' && item.history?.to && item.history.to < report.data_date
      ? `${item.market} · 淨值至 ${item.history.to}` : item.market || '比較標的';
    return [benchmark, ...(report.items || []).filter(item => item.status === 'ok' && item.code !== '0050').map(item => ({ ...item, role: role(item) }))];
  }

  function periodAnchor(end, key) {
    const months = { '1m': 1, '3m': 3, '1y': 12, '3y': 36, '5y': 60 }[key] || 60;
    const day = new Date(`${end}T00:00:00Z`);
    day.setUTCMonth(day.getUTCMonth() - months);
    return day.toISOString().slice(0, 10);
  }

  function normalizedCoreSeries(report) {
    const entities = coreEntities(report).filter(entity => Array.isArray(entity.chart) && entity.chart.length);
    if (!entities.length) return [];
    const requested = periodAnchor(report.data_date, core.period.value);
    const commonStart = entities.reduce((latest, entity) => entity.chart[0][0] > latest ? entity.chart[0][0] : latest, requested);
    return entities.map(entity => {
      const points = entity.chart.filter(point => point[0] >= commonStart);
      if (!points.length || !Number.isFinite(Number(points[0][1])) || Number(points[0][1]) === 0) return { ...entity, plot: [] };
      const base = Number(points[0][1]);
      return { ...entity, plot: points.map(point => [point[0], Number(point[1]) / base * 100]) };
    }).filter(entity => entity.plot.length);
  }

  function drawCore(report) {
    const series = normalizedCoreSeries(report);
    core.chart.replaceChildren();
    if (!series.length) {
      core.chart.append(el('p', 'core-empty', '這個期間沒有足夠的共同歷史。'));
      return;
    }
    const width = Math.max(280, Math.round(core.chart.clientWidth || 640));
    const height = width < 520 ? 210 : 260;
    const pad = { l: 46, r: 16, t: 16, b: 28 };
    const all = series.flatMap(item => item.plot);
    const dates = all.map(point => point[0]).sort();
    const start = Date.parse(dates[0]), end = Date.parse(dates.at(-1));
    const rawMin = Math.min(...all.map(point => point[1])), rawMax = Math.max(...all.map(point => point[1]));
    const spread = rawMax - rawMin || 10;
    const vMin = rawMin - spread * .08, vMax = rawMax + spread * .08;
    const sx = day => pad.l + (Date.parse(day) - start) / Math.max(1, end - start) * (width - pad.l - pad.r);
    const sy = value => pad.t + (vMax - value) / (vMax - vMin) * (height - pad.t - pad.b);
    const root = svg('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', tabindex: '0',
      'aria-label': `${series.map(item => item.code).join('、')} 含息報酬走勢，期初為 100` });
    for (let index = 0; index < 4; index += 1) {
      const value = vMin + spread * index / 3;
      root.append(svg('line', { class: 'chart-grid', x1: pad.l, x2: width - pad.r, y1: sy(value), y2: sy(value) }));
      const label = svg('text', { class: 'chart-label', x: pad.l - 6, y: sy(value) + 4, 'text-anchor': 'end' });
      label.textContent = fmtNum(value, 0);
      root.append(label);
    }
    [dates[0], dates.at(-1)].forEach((day, index) => {
      const label = svg('text', { class: 'chart-label', x: sx(day), y: height - 6, 'text-anchor': index ? 'end' : 'start' });
      label.textContent = day.slice(0, 7);
      root.append(label);
    });
    series.forEach((entity, index) => {
      const path = entity.plot.map((point, pointIndex) => `${pointIndex ? 'L' : 'M'}${sx(point[0]).toFixed(1)},${sy(point[1]).toFixed(1)}`).join('');
      root.append(svg('path', { d: path, fill: 'none', stroke: `var(${CORE_VAR[index]})`, 'stroke-width': index ? '1.8' : '2.6',
        'stroke-dasharray': CORE_DASH[index], 'stroke-linejoin': 'round', 'data-code': entity.code }));
    });
    const cross = svg('line', { class: 'chart-axis', y1: pad.t, y2: height - pad.b, visibility: 'hidden' });
    root.append(cross);
    const tip = tooltip(core.chart);
    const hit = svg('rect', { x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b, fill: 'transparent' });
    const showAt = event => {
      const box = root.getBoundingClientRect();
      const x = (event.clientX - box.left) / box.width * width;
      const moment = start + (x - pad.l) / (width - pad.l - pad.r) * (end - start);
      const nearest = series.map(entity => entity.plot.reduce((best, point) => Math.abs(Date.parse(point[0]) - moment) < Math.abs(Date.parse(best[0]) - moment) ? point : best));
      cross.setAttribute('x1', sx(nearest[0][0])); cross.setAttribute('x2', sx(nearest[0][0]));
      cross.setAttribute('visibility', 'visible');
      tip.show([nearest[0][0], ...series.map((entity, index) => `${entity.code} ${fmtNum(nearest[index][1] - 100, 1)}%`)],
        event.clientX - core.chart.getBoundingClientRect().left, event.clientY - core.chart.getBoundingClientRect().top);
    };
    hit.addEventListener('pointermove', showAt);
    hit.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); tip.hide(); });
    root.append(hit);
    core.chart.append(root);
  }

  function renderCoreSelection() {
    core.selection.replaceChildren(...coreCodes.map(code => {
      const button = el('button', 'core-selection-chip', code);
      button.type = 'button';
      button.dataset.code = code;
      button.setAttribute('aria-label', `移除 ${code}`);
      button.addEventListener('click', () => { coreCodes = coreCodes.filter(item => item !== code); loadCore(); });
      return button;
    }));
  }

  function renderCore(report) {
    coreReport = report;
    core.block.dataset.state = 'ok';
    core.meta.textContent = `資料日 ${report.data_date}`;
    renderCoreSelection();
    const failed = (report.items || []).filter(item => item.status !== 'ok');
    core.status.hidden = failed.length === 0;
    core.status.textContent = failed.map(item => `${item.code}：${item.message || '無法比較'}`).join('；');
    const periodKey = core.period.value;
    const entities = coreEntities(report);
    core.cards.replaceChildren(...entities.map((entity, index) => {
      const period = corePeriodRow(entity, periodKey);
      const card = el('article', 'core-card');
      const header = el('header');
      const swatch = el('i'); swatch.style.background = `var(${CORE_VAR[index]})`;
      const name = entity.name || '名稱待資料補齊';
      header.append(swatch, el('strong', '', `${entity.code} ${name}`), el('span', '', entity.role));
      const dl = el('dl');
      const returnLabel = !entity.dividends_included ? '報酬（配息未完整）' : entity.kind === 'fund' ? '含息報酬（台幣）' : '含息報酬';
      if (!period || period.status !== 'ok') {
        dl.append(el('dt', '', returnLabel), el('dd', '', '歷史不足'), el('dt', '', '最大回撤'), el('dd', '', '—'));
      } else if (entity.isBenchmark) {
        dl.append(el('dt', '', returnLabel), el('dd', '', `${signed(period.return_pct, 1)}%`),
          el('dt', '', '最大回撤'), el('dd', '', `${fmtNum(period.max_drawdown_pct, 1)}%`));
      } else {
        dl.append(el('dt', '', returnLabel), el('dd', '', `${signed(period.stock_return_pct, 1)}%`),
          el('dt', '', '最大回撤'), el('dd', '', `${fmtNum(period.stock_max_drawdown_pct, 1)}%`),
          el('dt', '', '相對 0050'), el('dd', '', `${signed(period.excess_pct, 1)} 個百分點`));
      }
      card.append(header, dl);
      return card;
    }));
    drawCore(report);
  }

  async function loadCore() {
    const request = ++coreRequest;
    core.block.dataset.state = 'loading';
    core.status.hidden = true;
    try {
      const requestedCodes = coreCodes.length ? coreCodes : ['0050'];
      const report = await getJson(`/api/compare-0050?codes=${encodeURIComponent(requestedCodes.join(','))}`);
      if (request !== coreRequest) return;
      renderCore(report);
    } catch (error) {
      if (request !== coreRequest) return;
      core.block.dataset.state = 'error';
      core.status.hidden = false;
      core.status.textContent = error.message || '比較資料暫時無法讀取。';
    }
  }

  // 加入比較：代號、名稱都能打（stock-picker.js，本人 11-17）；下拉排在浮出的加入選單裡、輸入框下面。
  function addCoreCode(code) {
    core.status.hidden = true;
    if (!window.StockPicker.isCode(code)) {
      core.status.hidden = false; core.status.textContent = '代號格式不正確。'; return;
    }
    if (code === '0050') {
      core.status.hidden = false; core.status.textContent = '0050 已固定為比較基準。'; return;
    }
    if (coreCodes.includes(code)) {
      core.status.hidden = false; core.status.textContent = `${code} 已在比較中。`; return;
    }
    if (coreCodes.length >= 5) {
      core.status.hidden = false; core.status.textContent = '最多加入 5 檔比較標的。'; return;
    }
    coreCodes.push(code); core.input.value = ''; loadCore();
  }

  function initCore() {
    // 基金也收（05 票：基金在比較資料包裡和股票同形狀，台幣、含息再投入）
    const picker = window.StockPicker.attach(core.input, { host: core.input.closest('.core-add-menu'), inline: true,
      onPick: option => addCoreCode(option.code) });
    core.form.addEventListener('submit', async event => {
      event.preventDefault();
      const { option, message } = await picker.choose();
      if (message) { core.status.hidden = false; core.status.textContent = message; return; }
      addCoreCode(option.code);
    });
    core.period.addEventListener('change', () => { if (coreReport) renderCore(coreReport); });
    renderCoreSelection();
    loadCore();
  }

  // 寬度改變時依新容器寬度重畫兩張圖（文字大小不隨縮放）。
  let resizeTimer = null;
  let lastWidth = host.clientWidth;
  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (host.clientWidth === lastWidth) return;
      lastWidth = host.clientWidth;
      if (coreReport) drawCore(coreReport);
    }, 150);
  });

  initChips();
  initRotation();
  initCore();
})();

// G4-2R：每日 AI 解讀獨立載入；失敗不影響原本行情與搜尋。
(() => {
  'use strict';
  const card = document.getElementById('aiDigestCard');
  const date = document.getElementById('aiDigestDate');
  const conclusion = document.getElementById('aiDigestConclusion');
  const preview = document.getElementById('aiDigestPreview');
  const details = document.getElementById('aiDigestDetails');
  const marketPoints = document.getElementById('aiDigestMarketPoints');
  const flowPoints = document.getElementById('aiDigestFlowPoints');
  const setup = document.getElementById('aiDigestSetup');
  const reason = document.getElementById('aiDigestReason');
  if (!card || !date || !conclusion || !preview || !details || !marketPoints || !flowPoints) return;

  const listItem = text => {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
  };

  function showUnproduced(dataDate = null) {
    card.dataset.state = 'unproduced';
    date.textContent = dataDate ? `資料日 ${dataDate}` : '資料日待更新';
    conclusion.textContent = '今日未產生';
    preview.replaceChildren();
    marketPoints.replaceChildren();
    flowPoints.replaceChildren();
    details.hidden = true;
  }

  function renderDigest(payload) {
    const market = payload.market?.status === 'generated' ? payload.market.content : null;
    const flow = payload.flow?.status === 'generated' ? payload.flow.content : null;
    if (!market && !flow) { showUnproduced(payload.data_date); return; }

    card.dataset.state = 'generated';
    date.textContent = payload.data_date ? `資料日 ${payload.data_date}` : '資料日待更新';
    conclusion.textContent = market?.conclusion || '大盤解讀今日未產生';
    const signals = Array.isArray(flow?.signals) ? flow.signals : [];
    preview.replaceChildren(...signals.slice(0, 2).map(item => listItem(item.text)));
    marketPoints.replaceChildren(...(market?.points || []).map(item => listItem(item.text)));
    flowPoints.replaceChildren(...signals.map(item => listItem(item.text)));
    details.hidden = marketPoints.children.length === 0 && flowPoints.children.length === 0;
  }

  async function loadAiDigest(url) {
    try {
      const response = await fetch(url, { cache: 'no-store', headers: { Accept: 'application/json' } });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      renderDigest(payload);
    } catch (_) {
      showUnproduced();
    }
  }

  // 開放給朋友 02 票：朋友的首頁放他用自己 Gemini 金鑰產生的那兩份。
  // 開關關著（available=false）或不是朋友（站主、本機版）時什麼都不做，首頁和以前一樣。
  function showSetup(message) {
    showUnproduced();
    date.textContent = '';
    conclusion.textContent = message;
    if (setup) setup.hidden = false;
  }

  async function initFriendDigest() {
    const response = await fetch('/api/friend-ai/digest', { cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) return;
    const payload = await response.json();
    if (!payload?.available) return;
    card.hidden = false;
    if (!payload.configured) { showSetup('尚未設定 Gemini'); return; }
    if (!payload.consented) { showSetup('同意內容已更新，請回設定頁重新同意'); return; }
    renderDigest(payload);
    // 04 票：當天沒產生（額度用完、金鑰失效…）時寫出原因，附回設定頁的入口。
    if (payload.notice?.text) {
      if (reason) { reason.textContent = payload.notice.text; reason.hidden = false; }
      if (setup) setup.hidden = false;
    }
  }

  async function initAiDigest() {
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const policy = response.ok ? await response.json() : null;
      if (!policy?.features?.ai_digest) { await initFriendDigest(); return; }
      card.hidden = false;
      await loadAiDigest('/api/ai-digest');
    } catch (_) { /* feature gate 讀取失敗時不查 AI schema、不顯示卡片 */ }
  }

  initAiDigest();
})();
