(() => {
  'use strict';

  const A = window.MARKET_ANALYSIS;
  const D = window.MARKET_DASHBOARD;
  const root = document.documentElement;
  const $ = id => document.getElementById(id);
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isMobile = () => matchMedia('(max-width: 760px)').matches;
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

  const els = {
    price: $('currentPrice'),
    change: $('currentChange'),
    state: $('marketState'),
    stateButton: $('statusTrigger'),
    time: $('marketTime'),
    analysisDate: $('analysisDate'),
    scenarioDate: $('scenarioDate'),
    zone: $('zoneLabel'),
    conclusion: $('conclusionText'),
    service: $('serviceNote'),
    chartContext: $('chartContext'),
    chartStage: $('chartStage'),
    chartHost: $('mainChart'),
    chartSummary: $('chartSummary'),
    period: $('periodSelect'),
    reset: $('resetView'),
    forming: $('formingBadge'),
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
    get nextPollDelay() { return nextPollDelay; }
  };
  window.MinimalApp = api;

  if (!A || !D || !A.backtest || !D.thresholds || !window.KChart) {
    els.service.hidden = false;
    els.service.textContent = '資料驗證失敗：缺少盤後分析、劇本或圖表元件。';
    return;
  }

  const UPPER = Number(D.thresholds.upper);
  const LOWER = Number(D.thresholds.lower);
  const AS_OF = A.backtest.as_of || {};
  const ACTIONS = D.actions || (D.forecast && D.forecast.actions) || {};
  const CLOSE = Number(AS_OF.close || (D.latest && D.latest.close));
  const HEADLINE = D.headline || (D.forecast && D.forecast.headline) || {};
  const DEFAULT_SAMPLE = D.default_sample || AS_OF.default_sample || (A.backtest.samples[0] && A.backtest.samples[0].key);
  const scenarioDate = (() => {
    const horizon = String(D.forecast && D.forecast.horizon || '');
    const match = horizon.match(/\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : String(D.expires_at || D.forecast && D.forecast.expiry || '').slice(0, 10);
  })();
  const TARGET_LABEL = scenarioDate ? `${scenarioDate.slice(5, 7)}/${scenarioDate.slice(8, 10)}` : '下一交易日';

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

  function headlineSummary() {
    const title = HEADLINE.title || '方向待確認';
    return HEADLINE.tone ? `${title}｜${HEADLINE.tone}` : title;
  }

  function headlineDetail() {
    const summary = headlineSummary();
    return HEADLINE.lede ? `${summary}：${HEADLINE.lede}` : summary;
  }

  function zoneFor(price) {
    if (!Number.isFinite(Number(price))) return { id: 'unknown', label: '尚未取得' };
    if (price >= UPPER) return { id: 'up', label: '站回收復線' };
    if (price >= LOWER) return { id: 'mid', label: '兩線之間' };
    return { id: 'down', label: '跌破防守線' };
  }

  function fallbackMarketData() {
    const latest = D.latest || {};
    return {
      price: Number(latest.close),
      previous_close: Number(latest.close) - Number(latest.change || 0),
      change: Number(latest.change),
      change_percent: Number(latest.change_percent),
      open: Number(AS_OF.open),
      high: Number(latest.high ?? AS_OF.high),
      low: Number(latest.low ?? AS_OF.low),
      turnover_billion: Number(latest.turnover)
    };
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
    els.time.textContent = stamp || D.market_date || A.meta.cutoff_date || '—';
    els.zone.textContent = zoneFor(price).label;
    const shortDate = value => (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) ? `${value.slice(5, 7)}/${value.slice(8, 10)}` : (value || '—');
    els.analysisDate.textContent = shortDate(A.meta.cutoff_date || D.market_date);
    els.scenarioDate.textContent = shortDate(scenarioDate);
    els.analysisDate.title = A.meta.cutoff_date || D.market_date || '';
    els.scenarioDate.title = scenarioDate || '';
    els.conclusion.textContent = headlineSummary();
  }

  function setOffline(message) {
    latestPayload = null;
    latestLive = false;
    els.service.hidden = false;
    els.service.textContent = message;
    setMarketHeader(fallbackMarketData(), { id: 'closed', label: '即時服務未啟動' }, D.market_date || A.meta.cutoff_date);
    setIntradayOptions(false);
    loadPeriod(currentPeriod.startsWith('m') ? 'daily' : currentPeriod, true);
  }

  function setIntradayOptions(enabled) {
    [...els.period.options].forEach(option => {
      if (option.value.startsWith('m')) option.disabled = !enabled;
    });
  }

  function toChartCandle(row, unit) {
    const date = row.date || row.timestamp || row.end;
    const label = unit === '60分'
      ? `${datePart(date).slice(5).replace('-', '/')} ${String(row.time || row.timestamp || '').slice(11, 16)}`
      : row.label || String(date || '').slice(5).replace('-', '/');
    const rawAmount = Number(row.amount ?? row.turnover);
    return {
      t: date,
      label,
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
      amount: Number.isFinite(rawAmount) ? (rawAmount > 10_000_000 ? rawAmount / 100_000_000 : rawAmount) : null
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

  function dailyWithLive() {
    const candles = A.daily.map(row => toChartCandle(row, '日'));
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
      amount: Number(data.turnover_billion)
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

  function overlays() {
    return [
      { value: UPPER, label: '', color: css('--gold', '#d9a94b'), pos: 'above' },
      { value: LOWER, label: '', color: css('--down', '#2fd09a'), pos: 'below' }
    ];
  }

  function ensureChart() {
    if (chart) return chart;
    chart = new window.KChart(els.chartHost, {
      liveLast: false,
      onView: (_first, _last, atEnd) => {
        els.reset.hidden = atEnd;
        requestAnimationFrame(updateOverlayControls);
      },
      height: () => Math.max(250, els.chartStage.clientHeight || 360)
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
    if (!candles.length && period.startsWith('m')) {
      currentPeriod = 'daily';
      els.period.value = 'daily';
      return loadPeriod('daily', reset);
    }
    currentPeriod = period;
    els.period.value = period;
    selectedIndex = -1;
    els.mobileDetails.hidden = true;
    els.marker.hidden = true;
    const unit = period === 'h60' ? '60分' : period === 'weekly' ? '週' : period === 'monthly' ? '月' : period.startsWith('m') ? `${period.slice(1)}分` : '日';
    const liveLast = latestLive && (period.startsWith('m') || period === 'daily');
    const instance = ensureChart();
    instance.opts.liveLast = liveLast;
    const desiredCount = period === 'monthly' ? 60 : period === 'weekly' ? 70 : period === 'h60' ? 80 : period.startsWith('m') ? Math.min(candles.length, 120) : 70;
    const plotWidth = Math.max(0, els.chartStage.clientWidth - 68);
    const touchSafeCount = Math.max(15, Math.floor(plotWidth / 44));
    const count = Math.min(candles.length, isMobile() ? desiredCount : Math.min(desiredCount, touchSafeCount));
    if (reset || !instance.candles.length) instance.setData(candles, { count, overlays: overlays(), unit });
    else instance.update(candles, overlays());
    els.forming.hidden = !liveLast;
    els.estimate.hidden = !period.startsWith('m');
    els.chartContext.textContent = `${periodLabels[period]} · ${liveLast ? '最右一根形成中' : `資料截至 ${A.meta.cutoff_date}`}`;
    els.chartSummary.textContent = `${periodLabels[period]}，共 ${candles.length} 根。圖上有收復線 ${fmt(UPPER)} 與防守線 ${fmt(LOWER)}。`;
    requestAnimationFrame(updateOverlayControls);
  }

  function enforceCandleHitTarget() {
    if (!chart || !chart.candles.length || isMobile()) return;
    const maxCount = Math.max(15, Math.floor(Math.max(0, chart.canvas.clientWidth - 68) / 44));
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
    const l = 4;
    const r = 64;
    const t = 10;
    const b = 22;
    const macdH = chart.showMacd ? Math.round(H * .24) : 0;
    const gapH = chart.showMacd ? 10 : 0;
    const pw = W - l - r;
    const ph = H - t - b - macdH - gapH;
    const start = chart.view.end - chart.view.count;
    const end = chart.view.end;
    const step = pw / chart.view.count;
    const visible = chart.candles.slice(start, end);
    let low = Math.min(...visible.map(candle => candle.low), LOWER, UPPER);
    let high = Math.max(...visible.map(candle => candle.high), LOWER, UPPER);
    const pad = (high - low) * .07 || 10;
    low -= pad;
    high += pad;
    const y = value => t + (high - value) / (high - low) * ph;
    const x = index => l + step * (index - start + .5);
    return { W, H, l, pw, ph, start, end, step, y, x };
  }

  function setPosition(element, x, y) {
    element.style.left = `${x}px`;
    element.style.top = `${y}px`;
  }

  function updateOverlayControls() {
    const g = visibleGeometry();
    if (!g) return;
    let upperTop = Math.max(48, Math.min(g.H - 50, g.y(UPPER) - 22));
    let lowerTop = Math.max(48, Math.min(g.H - 50, g.y(LOWER) - 22));
    if (Math.abs(lowerTop - upperTop) < 54) {
      const middle = (upperTop + lowerTop) / 2;
      upperTop = Math.max(48, Math.min(g.H - 104, middle - 27));
      lowerTop = Math.min(g.H - 50, upperTop + 54);
    }
    els.lineUpper.style.top = `${upperTop}px`;
    els.lineLower.style.top = `${lowerTop}px`;
    const lastIndex = chart.candles.length - 1;
    const currentVisible = chart.view.end === chart.candles.length && lastIndex >= g.start;
    els.point.hidden = !currentVisible;
    if (currentVisible) {
      const last = chart.candles[lastIndex];
      setPosition(els.point, Math.max(150, g.W - 64 - 22), Math.max(32, Math.min(g.H - 55, g.y(last.close) + 4)));
    }
    if (selectedIndex >= g.start && selectedIndex < g.end) {
      const candle = chart.candles[selectedIndex];
      els.marker.hidden = false;
      setPosition(els.marker, Math.max(0, Math.min(g.W - 44, g.x(selectedIndex) - 22)), Math.max(0, Math.min(g.H - 44, g.y(candle.close) - 22)));
    } else {
      els.marker.hidden = true;
    }
  }

  function bindCandleSelection() {
    let start = null;
    chart.canvas.addEventListener('pointerdown', event => {
      start = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
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
    chart.hover = index;
    chart.draw();
    updateOverlayControls();
    if (isMobile() && !open) {
      els.mobileDetails.hidden = false;
      els.mobileDetails.textContent = `${chart.candles[index].label} 的詳情 ›`;
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
    return `<div class="plain"><section class="plain-group"><h4>這代表</h4>${list(meaning)}</section>${watch && watch.length ? `<section class="plain-group plain-watch"><h4>要注意</h4>${list(watch)}</section>` : ''}</div>`;
  }

  function moreDetail(html) {
    return `<details class="more-detail"><summary>看詳細數字</summary>${html}<p class="deep-links">更深入：<button type="button" data-deep="method">方法</button><button type="button" data-deep="sources">來源</button><button type="button" data-deep="samples">換一組比較樣本</button><button type="button" data-deep="table">資料表</button><button type="button" data-deep="news">新聞</button></p></details>`;
  }


  // 籌碼卡與直白分析（本人 2026-11-06：選 B、多一點數據、直白分析、要提空單與融資）
  const CH = window.MARKET_CHIPS || null;
  const PL = window.MARKET_PLAIN || null;
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
    const mLast = last(M), mPeak = M.reduce((a, b) => (b.margin_balance_billion > a.margin_balance_billion ? b : a));
    const sNow = mLast.short_units, s20 = ago(M, 20).short_units;
    const amounts = A.daily.slice(-20).map(r => r.amount / 1e8), amtRatio = amounts.at(-1) / (amounts.reduce((s, v) => s + v, 0) / amounts.length);
    const pick = key => (PL && PL.points.find(p => p.key === key)) || { lines: [] };
    const md = d => d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : '';
    return [
      { key: 'foreign', name: '外資現貨', value: `${last(F).net_billion < 0 ? '賣' : '買'} ${fmt(Math.abs(last(F).net_billion), 0)} 億`, tone: last(F).net_billion < 0 ? 'down' : 'up', read: last(F).net_billion < 0 ? `連 ${streak} 天賣，一個月 ${fmt(Math.abs(f20) / 10000, 1)} 兆` : '今天轉為買超', chart: spark(F.slice(-20).map(r => r.net_billion), '', true), date: last(F).date, plain: pick('foreign') },
      { key: 'futures', name: '外資期貨空單', value: `${fmt(Math.abs(fut), 0)} 口`, tone: fut < futWeek ? 'down' : 'up', read: fut < futWeek ? `比一週前多 ${fmt(Math.abs(fut - futWeek), 0)} 口，還在加` : `比一週前少 ${fmt(Math.abs(fut - futWeek), 0)} 口`, chart: spark(U.slice(-20).map(r => -r.net_contracts), 'var(--down)'), date: last(U).date, plain: pick('foreign') },
      { key: 'margin', name: '融資餘額', value: `${fmt(mLast.margin_balance_billion, 0)} 億`, tone: 'mid', read: `比 ${md(mPeak.date)} 高點少 ${fmt(mPeak.margin_balance_billion - mLast.margin_balance_billion, 0)} 億`, chart: spark(M.slice(-20).map(r => r.margin_balance_billion), 'var(--gold)'), date: mLast.date, plain: pick('margin') },
      { key: 'short', name: '融券餘額', value: `${fmt(sNow / 10000, 1)} 萬張`, tone: sNow > s20 ? 'down' : 'up', read: `比 20 天前${sNow > s20 ? '多' : '少'} ${fmt(Math.abs(sNow / s20 - 1) * 100, 0)}%`, chart: spark(M.slice(-20).map(r => r.short_units), 'var(--accent)'), date: mLast.date, plain: pick('short') },
      { key: 'trust', name: '投信', value: `${t5 >= 0 ? '買' : '賣'} ${fmt(Math.abs(t5), 0)} 億`, tone: t5 >= 0 ? 'up' : 'down', read: '近一週合計', chart: spark(F.slice(-20).map(r => r.trust_billion), '', true), date: last(F).date, plain: pick('trust') },
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
      $('readList').innerHTML = PL.points.slice(0, 4).map(p => `<li><b>${esc(p.title)}</b>${esc(p.lines[0] || '')}</li>`).join('');
      $('readDate').textContent = `股價、外資、期貨：${PL.data_dates.price_and_foreign.slice(5).replace('-', '/')}；融資融券：${PL.data_dates.margin.slice(5).replace('-', '/')}${PL.data_dates.margin < PL.data_dates.price_and_foreign ? '（官方還沒公布最新一天）' : ''}`;
    }
    $('readCard').addEventListener('click', () => openPanel('plain', {}, $('readCard')));
  }
  const DT = window.MARKET_DETAIL || null;
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
    const lo = signedAxis ? Math.min(0, ...vals) : Math.min(...vals) * .995, hi = signedAxis ? Math.max(0, ...vals) : Math.max(...vals) * 1.002;
    const y = v => H - 22 - (v - lo) / ((hi - lo) || 1) * (H - 30);
    const step = (W - pad * 2) / rows.length;
    const zero = signedAxis ? y(0) : H - 22;
    const bars = rows.map((r, i) => {
      const top = Math.min(y(r.value), zero), h = Math.max(2, Math.abs(zero - y(r.value)));
      const tone = signedAxis ? (r.value >= 0 ? 'var(--up)' : 'var(--down)') : key === 'margin' ? 'var(--gold)' : key === 'futures' ? 'var(--down)' : 'var(--accent)';
      const has = DT && DT.by_date[r.date];
      const on = r.date === selected;
      return `<g class="bar-hit${on ? ' is-on' : ''}" data-day="${r.date}" tabindex="0" role="button" aria-label="${esc(r.date)} ${esc(r.label)}${has ? '，點看明細' : ''}"><rect x="${pad + i * step}" y="0" width="${step}" height="${H}" fill="transparent"/><rect x="${pad + i * step + step * .14}" y="${top}" width="${step * .72}" height="${h}" rx="2" fill="${tone}" opacity="${on ? 1 : .72}"/></g>`;
    }).join('');
    const ticks = [0, Math.floor(rows.length / 2), rows.length - 1].map(i => `<text x="${pad + i * step + step / 2}" y="${H - 6}" text-anchor="middle">${esc(rows[i].date.slice(5).replace('-', '/'))}</text>`).join('');
    return `<svg class="drill-chart" viewBox="0 0 ${W} ${H}" role="group" aria-label="近 20 個交易日，點任一天看明細">${signedAxis ? `<line x1="0" x2="${W}" y1="${zero}" y2="${zero}" stroke="var(--line-strong)"/>` : ''}${bars}${ticks}</svg>`;
  }
  function detailTable(title, rows, cols) {
    if (!rows || !rows.length) return `<section class="drill-table"><h4>${esc(title)}</h4><p class="chip-date">這天沒有資料。</p></section>`;
    return `<section class="drill-table"><h4>${esc(title)}</h4><table><thead><tr><th>#</th><th>股票</th>${cols.map(c => `<th class="num">${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map((r, i) => `<tr><td class="num">${i + 1}</td><td><a class="stock-link" href="stock.html?code=${encodeURIComponent(r.code)}"><span class="num">${esc(r.code)}</span> ${esc(r.name)}</a></td>${cols.map(c => `<td class="num ${c[2] ? c[2](r) : ''}">${esc(c[1](r))}</td>`).join('')}</tr>`).join('')}</tbody></table></section>`;
  }
  function dayDetail(key, day) {
    const d = DT && DT.by_date[day];
    const point = chipSeries(key).find(r => r.date === day);
    const head = `<div class="drill-day"><b>${esc(day.slice(5).replace('-', '/'))}</b><span>${esc(point ? point.label : '')}</span></div>`;
    if (!d || !Object.keys(d).length) return head + `<p class="chip-date">這一天的個股明細還沒抓到（目前有 ${DT ? DT.days.length : 0} 天）。</p>`;
    const lots = v => `${v >= 0 ? '+' : '−'}${fmt(Math.abs(v), 0)} 張`;
    const bil = v => v == null ? '—' : `${v >= 0 ? '+' : '−'}${fmt(Math.abs(v), 1)} 億`;
    const tone = v => (v >= 0 ? 'upc' : 'dn');
    if (key === 'trust') return head + detailTable('投信買超前 20', d.trust_buy, [['金額（估）', r => bil(r.trust_billion), r => tone(r.trust_billion)], ['張數', r => lots(r.trust_lots)]]) + detailTable('投信賣超前 20', d.trust_sell, [['金額（估）', r => bil(r.trust_billion), r => tone(r.trust_billion)], ['張數', r => lots(r.trust_lots)]]);
    if (key === 'foreign') return head + detailTable('外資買超前 20', d.foreign_buy, [['金額（估）', r => bil(r.foreign_billion), r => tone(r.foreign_billion)], ['張數', r => lots(r.foreign_lots)]]) + detailTable('外資賣超前 20', d.foreign_sell, [['金額（估）', r => bil(r.foreign_billion), r => tone(r.foreign_billion)], ['張數', r => lots(r.foreign_lots)]]);
    if (key === 'margin') return head + detailTable('融資增加最多 20 檔', d.margin_up, [['增加', r => lots(r.margin_change)], ['融資餘額', r => `${fmt(r.margin_balance, 0)} 張`]]) + detailTable('融資減少最多 20 檔', d.margin_down, [['減少', r => lots(r.margin_change)], ['融資餘額', r => `${fmt(r.margin_balance, 0)} 張`]]);
    if (key === 'short') return head + detailTable('融券增加最多 20 檔', d.short_up, [['增加', r => lots(r.short_change)], ['融券餘額', r => `${fmt(r.short_balance, 0)} 張`]]) + detailTable('融券減少最多 20 檔', d.short_down, [['減少', r => lots(r.short_change)], ['融券餘額', r => `${fmt(r.short_balance, 0)} 張`]]);
    if (key === 'volume') return head + detailTable('成交金額前 20', d.turnover_top, [['成交', r => `${fmt(r.amount_billion, 0)} 億`], ['漲跌', r => r.change_pct == null ? '—' : `${r.change_pct >= 0 ? '+' : ''}${fmt(r.change_pct, 2)}%`, r => tone(r.change_pct || 0)]]);
    if (key === 'futures') {
      const change = point && point.prev != null ? (-point.value) - point.prev : null;
      return head + `<div class="plain"><section class="plain-group"><h4>這一天</h4><ul><li>外資台指期淨空單 <b>${fmt(Math.abs(point.value), 0)} 口</b>。</li>${change != null ? `<li>比前一天${change < 0 ? '多' : '少'} <b>${fmt(Math.abs(change), 0)} 口</b>空單。</li>` : ''}</ul></section></div>` + detailTable('外資同日現貨賣超前 20（對照）', d.foreign_sell, [['金額（估）', r => bil(r.foreign_billion), r => tone(r.foreign_billion)]]);
    }
    return head;
  }
  function renderChipPanel(key, day) {
    const card = chipCards().find(item => item.key === key);
    if (!card) return '';
    const rows = chipSeries(key);
    const selected = day || (rows.length ? rows[rows.length - 1].date : '');
    const watch = PL ? PL.worry.filter(w => (key === 'futures' || key === 'foreign') && w.includes('外資')) : [];
    return `${plainBlock(card.plain.lines.map(esc), watch.map(esc))}<p class="drill-hint">點下面任一天，看那天的明細 <span aria-hidden="true">↓</span></p><div class="chip-big">${clickableBars(key, selected)}</div><div class="drill-detail" id="drillDetail">${dayDetail(key, selected)}</div><p class="chip-date">${esc(DT ? DT.notes.money : '')}　${esc(DT ? DT.notes.scope : '')}</p>`;
  }
  function bindChipPanel(key) {
    els.panelBody.querySelectorAll('.bar-hit').forEach(node => {
      const go = () => {
        panelState.payload.day = node.dataset.day;
        els.panelBody.querySelectorAll('.bar-hit').forEach(x => x.classList.toggle('is-on', x === node));
        els.panelBody.querySelector('#drillDetail').innerHTML = dayDetail(key, node.dataset.day);
      };
      node.addEventListener('click', go);
      node.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); go(); } });
    });
  }
  function renderPlainPanel() {
    if (!PL) return '';
    return `<p class="read-line big">${esc(PL.one_line)}</p>${PL.points.map(p => `<section class="plain-group"><h4>${esc(p.title)}</h4><ul>${p.lines.map(l => `<li>${esc(l)}</li>`).join('')}</ul></section>`).join('')}<section class="plain-group plain-watch"><h4>要擔心</h4><ul>${PL.worry.map(l => `<li>${esc(l)}</li>`).join('')}</ul></section><section class="plain-group"><h4>什麼時候比較安心</h4><ul>${PL.relief.map(l => `<li>${esc(l)}</li>`).join('')}</ul></section>`;
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
    ];
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

  function lineOutcomeBar() {
    const result = A.backtest.results[selectedSample] || A.backtest.results[DEFAULT_SAMPLE];
    const o = result.next_day_outcomes;
    const seg = (key, label, tone) => `<button type="button" class="ob-seg" data-outcome-panel="${key}" style="--w:${o.percent[key]}%;--tone:${tone}"><span>${label}</span><b class="num">${fmt(o.percent[key], 0)}%</b></button>`;
    return `<section class="plain-group ob"><h4>過去很像的 ${fmt(o.sample_count, 0)} 次，隔天的結果（不是明天的機率）</h4><div class="ob-bar">${seg('stand_back', '站回', 'var(--up)')}${seg('between', '兩線間', 'var(--gold)')}${seg('continue_break', '跌破', 'var(--down)')}</div></section>`;
  }

  function renderLinePanel(line) {
    const upper = line === 'upper';
    const scenarioName = upper ? '轉強觀察' : '續弱觀察';
    const scenario = scenarioFor(scenarioName);
    const action = ACTIONS[scenarioName] || {};
    const source = upper
      ? `依回測規則，本次收復線取 ${fmt(UPPER)}；原始前日低為 ${fmt(AS_OF.previous_low)}。`
      : `依回測規則，防守線取分析截止日最低 ${fmt(LOWER)}。`;
    const meaning = upper
      ? [`這是<b>止穩的門檻</b>：${esc(TARGET_LABEL)} 收盤站上 ${fmt(UPPER, 0)}，才算短線止跌。`, `它是今天的最高點${AS_OF.previous_low > UPPER ? '（前一天最低點更高，所以取比較近的今天最高點）' : ''}。`]
      : [`這是<b>停損的警戒線</b>：${esc(TARGET_LABEL)} 收盤跌破 ${fmt(LOWER, 0)}，代表今天的低點守不住。`, '它是今天的最低點。'];
    const watch = upper
      ? ['站上只代表<b>短線反彈</b>，不是趨勢翻多。', '要搭配外資賣超縮小才比較可信。']
      : ['只看<b>收盤</b>，盤中跌破又收回來不算。', `這條線離今天收盤約 ${fmt(Math.abs(CLOSE - LOWER), 0)} 點，在兩線之間不代表安全。`];
    return `
      ${plainBlock(meaning, watch)}
      <dl class="action-list">
        <div><dt>手上沒股票</dt><dd id="actionFlat">${esc(action.flat || '尚未取得')}</dd></div>
        <div><dt>已經持有</dt><dd id="actionHolding">${esc(action.holding || '尚未取得')}</dd></div>
      </dl>
      ${lineOutcomeBar()}
      ${moreDetail(`<p class="panel-lede">${source}</p><dl class="action-list"><div><dt>觸發條件</dt><dd>${esc(scenario.trigger || '尚未取得')}</dd></div><div><dt>解讀</dt><dd>${esc(scenario.interpretation || '尚未取得')}</dd></div></dl><p class="panel-callout" style="--tone:${upper ? 'var(--gold)' : 'var(--down)'}">${esc(ACTIONS.basis || '一般情境應對，不是自動下單指示。')}</p>`)}
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
      `過去和今天很像的日子有 <b>${fmt(total, 0)} 次</b>，其中 <b>${fmt(expectedCount, 0)} 次</b>隔天${what}，約佔 ${fmt(share, 0)}%。`,
      Number.isFinite(posShare) ? `這 ${fmt(currentReturns.length, 0)} 次裡，隔天上漲的有 ${fmt(posShare, 0)}%（平常日子約 ${fmt(positivePercent(rows.baseline), 0)}%）。` : '',
      Number.isFinite(currentMedian) ? `一般來說隔天${currentMedian >= 0 ? '漲' : '跌'}約 ${fmt(Math.abs(currentMedian), 2)}%（平常日子約${baselineMedian >= 0 ? '漲' : '跌'} ${fmt(Math.abs(baselineMedian), 2)}%）。` : '',
    ];
    const watch = [
      '這是<b>過去的比例，不是明天的機率</b>。',
      total < 50 ? `次數不多（${fmt(total, 0)} 次），參考就好。` : '',
      outcome === 'between' ? '兩條線相差很大，「留在中間」也可能再跌一千點以上。' : '',
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

  function renderCurrentPanel() {
    const data = currentMarketData();
    const price = Number(data.price);
    const upperGap = price - UPPER;
    const lowerGap = price - LOWER;
    const zone = zoneFor(price).label;
    const meaning = [
      `現在 <b>${fmt(price, 0)}</b>，落在「${esc(zone)}」。`,
      `離收復線還差 <b>${fmt(Math.abs(upperGap), 0)} 點</b>${upperGap >= 0 ? '（已在上方）' : ''}；離防守線還有 <b>${fmt(Math.abs(lowerGap), 0)} 點</b>${lowerGap < 0 ? '（已跌破）' : ''}。`,
    ];
    const watch = ['判斷看<b>收盤</b>，盤中位置會變。', '盤後劇本不會因盤中價格自動改寫。'];
    return `
      ${plainBlock(meaning, watch)}
      <div class="current-grid">
        <div><span>現價</span><strong class="num">${fmt(price)}</strong></div>
        <div><span>漲跌</span><strong class="num">${signed(data.change)}（${signed(data.change_percent)}%）</strong></div>
        <div><span>距收復線</span><strong class="num">${signed(upperGap)} 點</strong><small class="num">${signed(upperGap / UPPER * 100)}%</small></div>
        <div><span>距防守線</span><strong class="num">${signed(lowerGap)} 點</strong><small class="num">${signed(lowerGap / LOWER * 100)}%</small></div>
      </div>
      <dl class="action-list">
        <div><dt>行情狀態</dt><dd>${esc(els.state.textContent)}</dd></div>
        <div><dt>行情時間</dt><dd class="num">${esc(els.time.textContent)}</dd></div>
        <div><dt>現價落點</dt><dd>${esc(zoneFor(price).label)}</dd></div>
      </dl>
    `;
  }

  function evidenceData() {
    const latest = D.latest || {};
    const future = A.futures[A.futures.length - 1] || {};
    const news = Array.isArray(D.news) ? D.news : [];
    const close = Number(latest.close);
    const ma20 = Number(latest.ma20);
    const volumeRatio = Number(latest.turnover_vs_20d);
    const foreign = Number(latest.foreign_net);
    return [
      {
        key: 'trend', label: '趨勢', value: close < ma20 ? '還在下跌趨勢' : '在上升趨勢',
        note: `指數 ${fmt(close, 0)}，${close < ma20 ? '低於' : '高於'}近一個月平均 ${fmt(ma20, 0)}`
      },
      {
        key: 'money', label: '資金', value: foreign < 0 ? '外資還在賣' : '外資在買',
        note: `今天${foreign < 0 ? '賣' : '買'} ${fmt(Math.abs(foreign), 0)} 億；期貨${future.foreign_oi_net_contracts < 0 ? '偏空' : '偏多'}部位 ${fmt(Math.abs(future.foreign_oi_net_contracts), 0)} 口`
      },
      {
        key: 'volume', label: '成交量', value: volumeRatio >= 1.3 ? '比平常熱很多' : volumeRatio <= 0.75 ? '比平常冷' : '和平常差不多',
        note: `成交 ${fmt(latest.turnover, 0)} 億，約平常的 ${fmt(volumeRatio, 1)} 倍`
      },
      {
        key: 'event', label: '國際事件', value: news.length ? `${fmt(news.length, 0)} 件要留意` : '沒有重大事件',
        note: news[0] ? esc((news[0].facts || [])[0] || news[0].source) : '目前沒有已整理的事件'
      }
    ];
  }

  function renderConclusionPanel() {
    const latest = D.latest || {};
    const pos = AS_OF.features && Number.isFinite(AS_OF.features.close_position) ? AS_OF.features.close_position : NaN;
    const meaning = [
      Number.isFinite(pos) && pos >= 0.6 ? '今天一開盤就大跌，但<b>收盤前被買回不少</b>，沒有收在最低點。' : '今天收在當天的低檔附近。',
      Number(latest.foreign_net) < 0 ? '外資<b>還在賣</b>，股票和期貨兩邊都偏向看壞。' : '外資今天轉為買進。',
      `所以下一個交易日先觀察：收盤能不能站回 <b>${fmt(UPPER, 0)}</b>。`,
    ];
    const watch = ['反彈不等於止跌，外資停止賣出才比較可信。', '點下面任一項，看那一項的圖。'];
    return `
      ${plainBlock(meaning, watch)}
      <div class="evidence-list">
        ${evidenceData().map(item => `
          <button class="evidence-row" type="button" data-evidence="${item.key}">
            <span>${esc(item.label)}</span><strong>${esc(item.value)}</strong><small>${esc(item.note)}</small>
          </button>
        `).join('')}
      </div>
    `;
  }

  function panelTitleFor(kind, payload) {
    if (kind === 'candle') return chart.candles[payload.index].label;
    if (kind === 'line') return payload.line === 'upper' ? '收復線' : '防守線';
    if (kind === 'distribution') return `${outcomeLabels[payload.outcome]}的歷史結果`;
    if (kind === 'current') return '現價落點';
    if (kind === 'conclusion') return '結論依據';
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
    else if (panelState.kind === 'current') els.panelBody.innerHTML = renderCurrentPanel();
    else if (panelState.kind === 'plain') els.panelBody.innerHTML = renderPlainPanel();
    else if (panelState.kind === 'chip') { els.panelBody.innerHTML = renderChipPanel(panelState.payload.key, panelState.payload.day); bindChipPanel(panelState.payload.key); }
    else if (panelState.kind === 'conclusion') {
      els.panelBody.innerHTML = renderConclusionPanel();
      els.panelBody.querySelectorAll('[data-evidence]').forEach(button => button.addEventListener('click', () => renderLayerTwo(`evidence:${button.dataset.evidence}`)));
    }
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
    else if (kind.startsWith('evidence:')) renderEvidenceLayer(kind.split(':')[1]);
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
        <div><dt>劇本適用日</dt><dd class="num">${esc(scenarioDate)}</dd></div>
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

  function renderEvidenceLayer(kind) {
    const titles = { trend: '多週期趨勢', money: '現貨與期貨資金', volume: '成交金額', event: '事件時間軸' };
    els.panelTitle.textContent = titles[kind] || '完整依據';
    if (kind === 'event') {
      renderNewsLayer();
      els.panelTitle.textContent = titles[kind];
      return;
    }
    if (kind === 'trend') {
      const periods = ['monthly', 'weekly', 'daily', 'h60'];
      els.panelBody.innerHTML = periods.map(period => {
        const values = (A.indicators[period] || []).slice(-20).map(row => row.hist);
        const rows = period === 'monthly' ? A.monthly : period === 'weekly' ? A.weekly : period === 'h60' ? A.h60 : A.daily;
        const labels = rows.slice(-20).map(row => row.date || row.timestamp);
        const latest = (A.indicators[period] || []).at(-1) || {};
        return `<section class="detail-section"><h3>${periodLabels[period]}</h3><p>MACD 柱 ${signed(latest.hist)}，通用參數 ${A.indicators.parameters.macd.fast}, ${A.indicators.parameters.macd.slow}, ${A.indicators.parameters.macd.signal}。</p><div class="detail-chart">${svgBars(values, labels, 'var(--accent)', true)}</div></section>`;
      }).join('');
      return;
    }
    if (kind === 'money') {
      const institution = A.institutional.slice(-20);
      const futures = A.futures.slice(-20);
      els.panelBody.innerHTML = `
        <section class="detail-section"><h3>外資現貨，億元</h3><div class="detail-chart">${svgBars(institution.map(row => row.foreign.net / 100_000_000), institution.map(row => row.date), 'var(--accent)', true)}</div></section>
        <section class="detail-section"><h3>外資台指期淨部位，口</h3><div class="detail-chart">${svgBars(futures.map(row => row.foreign_oi_net_contracts), futures.map(row => row.date), 'var(--accent)', true)}</div></section>
      `;
      return;
    }
    const rows = A.daily.slice(-20);
    els.panelBody.innerHTML = `<section class="detail-section"><h3>近 20 個交易日成交金額</h3><p>單位：億元。</p><div class="detail-chart">${svgBars(rows.map(row => row.amount / 100_000_000), rows.map(row => row.date), 'var(--accent)', false)}</div></section>`;
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
    placePanel(panelAnchor);
    window.cancelAnimationFrame(openFrame);
    openFrame = requestAnimationFrame(() => {
      openFrame = 0;
      if (els.panel.getAttribute('aria-hidden') === 'true') return;
      els.backdrop.classList.add('is-open');
      els.panel.classList.add('is-open');
      if (isMobile()) keepSelectionAboveSheet();
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

  function keepSelectionAboveSheet() {
    const panelTop = innerHeight - els.panel.offsetHeight;
    const markerRect = !els.marker.hidden ? els.marker.getBoundingClientRect() : null;
    if (markerRect && markerRect.bottom > panelTop - 10) {
      scrollBy({ top: markerRect.bottom - panelTop + 18, behavior: 'auto' });
    }
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
      els.service.textContent = `即時行情暫時無法更新：${payload && payload.error ? payload.error : '未取得資料'}。目前顯示盤後資料。`;
      setMarketHeader(fallbackMarketData(), { id: 'delay', label: '延遲' }, D.market_date || A.meta.cutoff_date);
      setIntradayOptions(false);
      loadPeriod(currentPeriod.startsWith('m') ? 'daily' : currentPeriod, false);
      return;
    }
    const fetched = new Date(payload.fetched_at).getTime();
    const dataStamp = new Date(payload.data_time).getTime();
    const fetchedAge = Date.now() - fetched;
    const dataAge = Date.now() - dataStamp;
    const recent = Number.isFinite(fetched) && fetchedAge >= -5000 && fetchedAge <= 30_000;
    const dataFresh = Number.isFinite(dataStamp) && dataAge >= -5000 && dataAge <= 30_000;
    latestLive = payload.ok === true && payload.market_open === true && recent && dataFresh;
    let state;
    if (latestLive) state = { id: 'live', label: '即時' };
    else if (payload.market_open) state = { id: 'delay', label: '延遲' };
    else state = { id: 'closed', label: '休市' };
    els.service.hidden = true;
    const payloadDay = datePart(payload.data_time);
    const dashboardDay = datePart(D.market_date || A.meta.cutoff_date);
    const officialCloseAvailable = !payload.market_open && dashboardDay && payloadDay && dashboardDay >= payloadDay;
    const displayedData = officialCloseAvailable ? fallbackMarketData() : data;
    const displayedStamp = officialCloseAvailable
      ? formatStamp(D.forecast && D.forecast.market_as_of || D.market_date || A.meta.cutoff_date)
      : formatStamp(payload.data_time);
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
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      renderPayload(payload);
      const retry = Number(payload.retry_after_seconds || 0) * 1000;
      schedulePoll(payload.market_open ? Math.max(7000, retry) : Math.max(60_000, retry));
    } catch (error) {
      latestLive = false;
      els.service.hidden = false;
      els.service.textContent = `即時行情連線中斷：${error.name === 'AbortError' ? '本機服務逾時' : error.message}。目前顯示盤後資料。`;
      setMarketHeader(fallbackMarketData(), { id: 'delay', label: '延遲' }, D.market_date || A.meta.cutoff_date);
      setIntradayOptions(false);
      loadPeriod(currentPeriod.startsWith('m') ? 'daily' : currentPeriod, false);
      schedulePoll(15000);
    } finally {
      window.clearTimeout(timeout);
      polling = false;
    }
  }

  els.upper.textContent = fmt(UPPER);
  els.lower.textContent = fmt(LOWER);
  renderDistribution();
  setMarketHeader(fallbackMarketData(), { id: 'closed', label: '休市' }, D.market_date || A.meta.cutoff_date);

  els.period.addEventListener('change', () => loadPeriod(els.period.value, true));
  $('zoomIn').addEventListener('click', () => chart && chart.zoom(1 / 1.35));
  $('zoomOut').addEventListener('click', () => {
    if (!chart) return;
    chart.zoom(1.35);
    enforceCandleHitTarget();
  });
  els.reset.addEventListener('click', () => {
    if (!chart) return;
    chart.reset();
    enforceCandleHitTarget();
  });
  els.lineUpper.addEventListener('click', () => openPanel('line', { line: 'upper' }, els.lineUpper));
  els.lineLower.addEventListener('click', () => openPanel('line', { line: 'lower' }, els.lineLower));
  $('priceSummary').addEventListener('click', () => openPanel('current', {}, $('priceSummary')));
  els.stateButton.addEventListener('click', () => openPanel('current', {}, els.stateButton));
  els.point.addEventListener('click', () => openPanel('current', {}, els.point));
  $('conclusionTrigger').addEventListener('click', () => openPanel('conclusion', {}, $('conclusionTrigger')));
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

  function updateThemeText() {
    const dark = getComputedStyle(root).colorScheme.includes('dark');
    $('themeText').textContent = dark ? '改用淺色' : '改用深色';
    $('themeToggle').setAttribute('aria-label', dark ? '改用淺色主題' : '改用深色主題');
  }
  $('themeToggle').addEventListener('click', () => {
    const dark = getComputedStyle(root).colorScheme.includes('dark');
    root.dataset.theme = dark ? 'light' : 'dark';
    updateThemeText();
    chart && chart.draw();
    requestAnimationFrame(updateOverlayControls);
  });
  matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (!root.dataset.theme) {
      updateThemeText();
      chart && chart.draw();
      requestAnimationFrame(updateOverlayControls);
    }
  });

  updateThemeText();
  renderChips();
  loadPeriod('daily', true);
  api.ready = true;

  if (location.protocol === 'file:') {
    setOffline('即時服務未啟動，目前仍可使用盤後資料。請執行專案內的 python live_server.py，再開啟 http://127.0.0.1:8876/minimal.html。');
  } else {
    pollLive();
  }
})();
