/* 個股分析頁：只向後端 API 取個股分析契約 v2 並畫圖，不在前端算價位。每檔一份分析，沒有方案與總分。 */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const fmt = n => n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const SCHEMA = 'stock-analysis/v2';
  const TIER = { near: '近端（10% 內）', mid: '中距（10～20%）', far: '遠端（20% 以上）' };
  let features = { strategy_v1: false };

  async function loadReleaseFeatures() {
    if (location.protocol === 'file:') return;
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const payload = response.ok ? await response.json() : null;
      if (payload?.schema === 'market-ana-features/v1' && payload.features) features = payload.features;
    } catch (_) { /* feature gate 讀取失敗時維持 default-off */ }
  }
  // 研究過程只留在 API 與內部文件；正式頁只呈現策略、價位與行情。
  const STOCK_STATE_MESSAGE = {
    not_found: code => `${code}：查無此股。`,
    missing_data: code => `${code}：缺少分析資料，暫不顯示數值。`,
    suspended: code => `${code}：目前為停牌或行情中斷狀態，暫不顯示過期分析。`,
    just_listed: code => `${code}：剛上市，歷史資料尚不足，暫不顯示分析數值。`,
    stale: (code, dataDate = '未提供') => `${code}：資料至 ${dataDate}，已過期。以下內容只供查看舊資料，不作今日判斷。`,
  };
  // 操作策略：盤後直接使用後端 strategy_v1；盤中以同一組已重算條件更新建議，價位仍只取支撐壓力群。
  function strategyPrices(r) {
    const close = r.quote && r.quote.close;
    const selected = (r.levels?.support || []).map(row => row.tick);
    const all = (r.levels?.support_all || []).map(row => row.tick);
    const supports = [...new Set([...selected, ...all])].filter(value => Number.isFinite(value) && value < close);
    const target = (r.levels?.resistance || []).find(row => Number.isFinite(row.tick) && row.tick > close)?.tick ?? null;
    return {
      entry: supports[0] ?? null,
      add: supports.length >= 3 ? supports[1] : null,
      stop: supports.length >= 3 ? supports[2] : supports.length === 2 ? supports[1] : null,
      target,
    };
  }

  function buildStrategy(r) {
    if (!r._intraday && r.strategy) return r.strategy;
    const confirmed = r.conditions?.confirmed || {};
    const approximate = r.conditions?.approximate || {};
    const state = (group, key) => group[key]?.state;
    const inst = (r.institutional || []).map(row => row.total).filter(Number.isFinite);
    const instDay = inst.length ? inst.at(-1) : null;
    const instFive = inst.length >= 5 ? inst.slice(-5).reduce((sum, value) => sum + value, 0) : null;
    const positive = [state(confirmed, 'ma_trend') === 'triggered', state(confirmed, 'kd_bullish') === 'triggered',
      state(confirmed, 'volume_confirmed') === 'triggered', state(approximate, 'macd_bullish') === 'triggered',
      instDay !== null && instDay > 0, instFive !== null && instFive > 0].filter(Boolean).length;
    const risks = [state(confirmed, 'ma_trend') === 'off', state(confirmed, 'heavy_volume_stall') === 'triggered',
      state(confirmed, 'momentum_overheated') === 'triggered', state(confirmed, 'revenue_decline') === 'triggered',
      state(confirmed, 'securities_lending_sale') === 'triggered', state(approximate, 'macd_bullish') === 'off',
      instFive !== null && instFive < 0].filter(Boolean).length;
    const ma = state(confirmed, 'ma_trend');
    const pullback = state(approximate, 'volume_contraction') === 'triggered';
    const stalled = state(confirmed, 'heavy_volume_stall') === 'triggered';
    const prices = strategyPrices(r);
    let recommendation = '觀望';
    let reason = '多空訊號尚未同向，等待均線、量能與法人方向一致後再採取動作。';
    if (ma === 'off' || stalled && instFive !== null && instFive < 0 || risks >= 3) {
      recommendation = '減碼'; reason = '均線與動能同步轉弱，先降低曝險，跌破停損價不再等待反彈。';
    } else if (['triggered', 'confirming'].includes(ma) && positive >= 3 && risks <= 1 && pullback && prices.entry !== null && prices.target !== null) {
      recommendation = '分批進場'; reason = '均線、量能與動能偏多，量縮回檔時依第一、第二支撐分批，第一壓力作為目標。';
    } else if (ma === 'triggered' && positive >= 3 && risks <= 1) {
      recommendation = '持有'; reason = '均線、量能與動能仍偏多，持有到目標價；跌破停損價即退出。';
    }
    const technicalComplete = ['ma20', 'ma60', 'relative_volume', 'k9', 'd9', 'rsi14', 'macd_hist']
      .every(key => r.indicators?.[key] !== null && r.indicators?.[key] !== undefined);
    const structureComplete = prices.entry !== null && prices.stop !== null && prices.target !== null;
    let evidenceStrength = '有限';
    if (technicalComplete && structureComplete && instDay !== null) evidenceStrength = '完整';
    else if (technicalComplete && (prices.entry !== null || prices.target !== null)) evidenceStrength = '部分';
    return { recommendation, prices, reason, evidence_strength: evidenceStrength };
  }

  function renderStrategy(r) {
    $('strategy').hidden = !features.strategy_v1;
    if (!features.strategy_v1) return;
    const strategy = buildStrategy(r);
    $('strategyConclusion').textContent = `${strategy.recommendation}｜${strategy.reason}`;
    for (const [key, id] of Object.entries({ entry: 'strategyEntry', add: 'strategyAdd', stop: 'strategyStop', target: 'strategyTarget' })) {
      $(id).textContent = fmt(strategy.prices?.[key]);
    }
    $('strategyEvidence').textContent = `證據強度：${strategy.evidence_strength}`;
  }

  window.StockStrategy = { build: buildStrategy };

  // 大戶持股（集保股權分散表，每週一份）：一行講完，沒有資料就不顯示。
  function holdersLine(rows) {
    if (!Array.isArray(rows) || !rows.length) return null;
    const latest = rows[rows.length - 1];
    const parts = [`千張以上大戶持股 ${Number(latest.pct_1000).toFixed(2)}%`, `${fmt(latest.holders_1000, 0)} 人`];
    if (rows.length > 1) {
      const change = Math.round((latest.pct_1000 - rows[rows.length - 2].pct_1000) * 100) / 100;
      parts.push(change === 0 ? '與前一週相同' : `較前一週 ${change > 0 ? '+' : '−'}${Math.abs(change).toFixed(2)} 個百分點`);
    }
    return { date: latest.date, text: parts.join('・') };
  }
  window.StockHolders = { line: holdersLine };

  let chart = null;
  let lastChartKey = '';
  let lastResult = null;
  let baseResult = null;
  let lastLive = null;
  let requestSequence = 0;
  let pendingRequest = null;
  let chartPeriod = 'daily';
  // 5 年歷史日線：個股分析只帶最近 120 根，更早的在背景載入後接在前面。拿不到或對不上就維持原樣，不顯示任何訊息。
  let olderBars = { code: '', firstDate: '', rows: [] };
  let maPeriods = [18, 45];
  const INTRADAY_PERIODS = { '1m': '1', '5m': '5', '15m': '15', '60m': '60' };
  // 秒 K（本人 2026-11-14「可以，先做當天」）：當天逐筆在背景一頁一頁取回，由 Intraday.secondCandles 彙總。
  const SECOND_PERIODS = { '1s': 1, '5s': 5, '10s': 10, '15s': 15, '30s': 30 };
  let tickFeed = { code: '', date: null, ticks: [], offset: 0, timer: null, generation: 0 };
  try {
    const stored = JSON.parse(localStorage.getItem('stock-ma-periods') || 'null');
    if (Array.isArray(stored)) maPeriods = window.KChartMath.normalizePeriods(stored);
  } catch (_) { /* 私密視窗或損壞設定使用預設值 */ }
  const livePoller = window.Intraday.createPoller({
    onData: applyLive,
    onError: () => updateLiveStatus({ ok: false, environment: 'local', message: '暫時無法取得即時資料', live: false }),
  });
  const COMPARE_PERIODS = ['1m', '3m', '1y', '3y', '5y'];
  const COMPARE_COLORS = ['--cmp-series-1', '--cmp-series-2', '--cmp-series-3', '--cmp-series-4', '--cmp-series-5'];
  const COMPARE_DASHES = ['', '10 4', '3 4', '12 4 3 4', '2 5'];
  const compareState = {
    current: '', codes: [], names: new Map(), colors: new Map(), hidden: new Set(), payload: null,
    request: 0, abort: null, searchAbort: null, searchTimer: null, searchOptions: [], activeOption: -1,
  };

  function saveMaPeriods() {
    try { localStorage.setItem('stock-ma-periods', JSON.stringify(maPeriods)); } catch (_) { /* 私密視窗 */ }
  }

  const MA_LIMIT = 4; // 和 kchart.js 的 normalizePeriods 一致：最多四條

  function applyMaPeriods(next) {
    const before = maPeriods.length;
    maPeriods = window.KChartMath.normalizePeriods(next);
    saveMaPeriods();
    // 條數沒變（只是改了某一格的數字）就把數字寫回原本的輸入框，不整組重畫：
    // 重畫會把正在點的那一格換掉，手機上接著點另一格或「新增均線」的那一下會落空（本人 11-15 iPhone 回報）。
    const inputs = [...$('maControls').querySelectorAll('input')];
    if (maPeriods.length === before && inputs.length === before) inputs.forEach((input, index) => { input.value = String(maPeriods[index]); });
    else renderMaControls();
    syncMaLimit();
    if (chart) chart.setMaPeriods(maPeriods);
  }

  // 到四條上限時，「新增均線」停用並寫明原因（原本是按了沒反應）
  function syncMaLimit() {
    const full = maPeriods.length >= MA_LIMIT;
    $('addMa').disabled = full;
    $('addMa').textContent = full ? `最多 ${MA_LIMIT} 條均線` : '新增均線';
  }

  function renderMaControls() {
    $('maControls').replaceChildren(...maPeriods.map((period, index) => {
      const group = node('label', 'bd-ma-control');
      group.append(node('span', '', `MA${index + 1}`));
      const input = node('input');
      input.type = 'number'; input.min = '2'; input.max = '240'; input.value = String(period);
      input.setAttribute('aria-label', `MA${index + 1}期數`);
      input.addEventListener('change', () => applyMaPeriods(maPeriods.map((value, itemIndex) => itemIndex === index ? Number(input.value) : value)));
      group.append(input);
      if (maPeriods.length > 1) {
        const remove = node('button', 'bd-ma-remove', '×');
        remove.type = 'button'; remove.setAttribute('aria-label', `移除 MA${index + 1}`);
        remove.addEventListener('click', () => applyMaPeriods(maPeriods.filter((_, itemIndex) => itemIndex !== index)));
        group.append(remove);
      }
      return group;
    }));
  }

  // 按瀏覽器「上一頁」回來時，頁面是整頁還原的、不會重新載入：這時要再讀一次存起來的主題（可能在別頁改過）
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    let stored = null;
    try { stored = localStorage.getItem('market-theme'); } catch (_) { /* 私密視窗 */ }
    if ((stored === 'light' || stored === 'dark') && stored !== document.documentElement.dataset.theme) setTheme(stored);
  });

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('market-theme', theme); } catch (_) { /* 私密視窗可能無法存 */ }
    $('themeText').textContent = theme === 'dark' ? '淺色' : '深色';
    if (lastResult) drawChart(lastResult);
    if (compareState.payload) drawComparisonChart(compareState.payload);
  }

  function codeFromUrl() {
    const code = new URLSearchParams(location.search).get('code');
    return code && /^[0-9A-Z]{4,6}$/.test(code) ? code : null;
  }

  function canUseLive(result) {
    const historical = new URLSearchParams(location.search).has('as_of');
    return !historical && result?.stock_state !== 'stale' && result?.analysis_status !== 'stale';
  }

  function updateUrl(code) {
    const url = new URL(location.href);
    url.searchParams.set('code', code);
    url.searchParams.delete('preset');
    if (url.href !== location.href) history.pushState(null, '', url);
  }

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function levelMeta(cluster) {
    if (!cluster) return '無結構價位';
    const sign = cluster.distance_pct > 0 ? '+' : '';
    return `${sign}${fmt(cluster.distance_pct)}%・${TIER[cluster.tier] || cluster.tier}`;
  }

  function setLevel(id, cluster, emptyText) {
    $(id).textContent = cluster ? fmt(cluster.tick) : emptyText;
    $(`${id}Meta`).textContent = cluster ? levelMeta(cluster) : '';
  }

  // 價位階梯：壓力二／壓力一／收盤／支撐一／支撐二由上而下，列距依「與收盤相差幾 %」拉開，讓遠近一眼可比。
  function layoutLadder(close, res, sup) {
    $('ladderClose').textContent = fmt(close);
    const items = { resistance2: res[1], resistance1: res[0], close: { tick: close }, support1: sup[0], support2: sup[1] };
    const rows = [...document.querySelectorAll('#ladder > li')];
    let prev = null;
    rows.forEach((li, i) => {
      if (!li.classList.contains('lad')) return;
      const item = items[li.dataset.key];
      // 壓力一沒有結構價位時仍顯示（寫「上方無結構價位」），其他缺的就整列收起。
      const keep = item || li.dataset.key === 'resistance1';
      li.hidden = !keep;
      li.classList.toggle('is-empty', !item);
      if (!keep) return;
      if (prev && item) {
        const pct = Math.abs(prev.tick - item.tick) / close * 100;
        rows[i - 1].style.setProperty('--gap', `${Math.round(Math.min(96, Math.max(8, pct * 8)))}px`);
      } else if (i > 0) rows[i - 1].style.setProperty('--gap', '8px');
      prev = item || prev;
    });
    rows.forEach((li, i) => {
      if (li.classList.contains('lad-gap')) li.hidden = !!(rows[i - 1]?.hidden || rows[i + 1]?.hidden);
    });
  }

  function updateLiveStatus(payload) {
    const badge = $('liveBadge');
    const status = $('liveStatus');
    const cloudUnavailable = payload?.error === 'cloud-live-unavailable' || payload?.environment === 'cloud' && payload?.ok === false;
    badge.hidden = !payload?.live;
    status.dataset.state = cloudUnavailable ? 'error' : payload?.live ? 'live' : 'closed';
    if (cloudUnavailable) status.textContent = '雲端無法取得即時資料';
    else if (payload?.live && payload.quote?.data_time) status.textContent = `${payload.quote.data_time.slice(11, 19)} 更新`;
    else if (payload?.market_open) status.textContent = '即時資料逾 30 秒，顯示最近一筆';
    else status.textContent = '已收盤，顯示收盤資料';
  }

  function applyLive(payload) {
    lastLive = payload;
    updateLiveStatus(payload);
    if (payload?.ok && payload.market_open && payload.quote && baseResult) {
      const projected = window.Intraday.projectAnalysis(baseResult, payload);
      if (features.strategy_v1) projected.strategy = buildStrategy(projected);
      else delete projected.strategy;
      render(projected, false);
    } else if (baseResult) {
      // 13:30 後或休市回到盤後基準；不能只熄掉 LIVE 卻把最後盤中價留在主報價與操作重點。
      render(baseResult, false);
    }
  }

  function render(r, initial = false) {
    if (initial) {
      document.title = `${r.name} ${r.code}｜個股分析`;
      $('content').dataset.schema = r.schema;
      $('stockName').textContent = r.name;
      $('stockCode').textContent = r.code;
      showMarket(r);
      showCandidateReasons(r.code);
    }
    lastResult = r;
    const intraday = Boolean(r._intraday);
    const liveNumbers = ['close', 'ladderClose', 'resistance1', 'resistance2', 'support1', 'support2'];
    if (intraday) {
      // motion.js 的數字補間適合盤後換股，不適合每 5 秒的現價。首次盤中更新改用同元素的乾淨複本，保留樣式但不再顯示過渡中的舊價。
      liveNumbers.forEach(id => {
        const current = $(id);
        if (!current.dataset.liveStatic) {
          const stable = current.cloneNode(true);
          stable.dataset.liveStatic = '1';
          current.replaceWith(stable);
        }
      });
    }
    $('quoteMode').textContent = intraday ? '盤中' : '資料日';
    $('asOf').textContent = intraday && r._intraday.data_time ? r._intraday.data_time.slice(0, 16).replace('T', ' ') : r.as_of;
    $('ladderCurrentLabel').textContent = intraday ? '現價' : '收盤';
    $('ladderCurrentMeta').textContent = intraday ? '盤中更新' : '資料日收盤';
    $('close').textContent = fmt(r.quote.close);
    const chg = r.quote.chg_pct;
    $('chg').textContent = chg === null || chg === undefined ? '' : `${chg > 0 ? '+' : ''}${Number(chg).toFixed(2)}%`;
    $('chg').className = `num ${chg > 0 ? 'up' : chg < 0 ? 'down' : ''}`;

    const lv = r.levels || {};
    const res = lv.resistance || [];
    const sup = lv.support || [];
    const noRes = lv.no_resistance_above ? '上方無結構價位' : '無';
    setLevel('resistance1', res[0], noRes);
    setLevel('resistance2', res[1], '無');
    setLevel('support1', sup[0], '無');
    setLevel('support2', sup[1], '無');
    layoutLadder(r.quote.close, lv.limit_up_reference ? [{ tick: lv.limit_up_reference.value }, res[1]] : res, sup);

    if (lv.limit_up_reference) {
      $('resistance1').textContent = fmt(lv.limit_up_reference.value);
      $('resistance1Meta').textContent = lv.limit_up_reference.label;
    }

    renderStrategy(r);

    if (r.stock_state === 'stale' || r.analysis_status === 'stale') {
      $('status').hidden = false;
      $('status').dataset.stockState = 'stale';
      $('status').textContent = STOCK_STATE_MESSAGE.stale(r.code, r.data_date || r.as_of);
    } else {
      $('status').hidden = true;
      delete $('status').dataset.stockState;
    }
    $('content').hidden = false;
    showBigHolders(r);
    drawChart(r);
    if (initial) syncComparison(r);
  }

  function showOutside(data) {
    $('outside').hidden = false;
    $('outsideTitle').textContent = `${data.name || data.code} ${data.code}：不在本站涵蓋範圍`;
    $('outsideReason').textContent = data.message || '不在本站涵蓋範圍';
    $('outsideMeta').textContent = [data.market, data.universe_rank ? `成交金額排名第 ${data.universe_rank} 名` : '',
      data.data_date ? `資料日 ${data.data_date}` : ''].filter(Boolean).join('・');
    $('status').hidden = true;
  }

  function weekKey(dateText) {
    const date = new Date(`${dateText}T00:00:00Z`);
    const weekday = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() - weekday + 1);
    return date.toISOString().slice(0, 10);
  }

  function aggregateCandles(rows, keyFor, labelFor) {
    const grouped = [];
    rows.forEach(row => {
      const key = keyFor(row.date);
      let bar = grouped[grouped.length - 1];
      if (!bar || bar.periodKey !== key) {
        grouped.push({ periodKey: key, t: row.date, label: labelFor(key), open: row.open, high: row.high, low: row.low,
          close: row.close, volume: row.volume, foreign: row.foreign, trust: row.trust, dealer: row.dealer });
        return;
      }
      bar.t = row.date;
      bar.high = Math.max(bar.high, row.high);
      bar.low = Math.min(bar.low, row.low);
      bar.close = row.close;
      for (const field of ['volume', 'foreign', 'trust', 'dealer']) {
        if (Number.isFinite(row[field])) bar[field] = (Number.isFinite(bar[field]) ? bar[field] : 0) + row[field];
      }
    });
    return grouped;
  }

  function weeklyCandles(rows) { return aggregateCandles(rows, weekKey, key => key); }
  function monthlyCandles(rows) { return aggregateCandles(rows, date => date.slice(0, 7), key => key); }

  function dailyCandles(result) {
    const institutional = new Map((result.institutional || []).map(row => [row.date, row]));
    const older = olderBars.code === result.code && olderBars.firstDate === result.bars[0]?.date ? olderBars.rows : [];
    return [...older, ...result.bars].map(bar => {
      const chip = institutional.get(bar.date) || {};
      return { t: bar.date, label: bar.date, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume,
        foreign: chip.foreign, trust: chip.trust, dealer: chip.dealer };
    });
  }

  function drawChart(r) {
    const daily = dailyCandles(r);
    const intradayKey = INTRADAY_PERIODS[chartPeriod];
    const seconds = SECOND_PERIODS[chartPeriod];
    const intraday = Boolean(intradayKey) || Boolean(seconds);
    const candles = seconds ? window.Intraday.secondCandles(tickFeed.code === r.code ? tickFeed.ticks : [], seconds)
      : intraday ? (lastLive?.candles?.[intradayKey] || [])
      : chartPeriod === 'weekly' ? weeklyCandles(daily.map(row => ({ ...row, date: row.t })))
      : chartPeriod === 'monthly' ? monthlyCandles(daily.map(row => ({ ...row, date: row.t }))) : daily;
    const overlays = [];
    const lv = r.levels || {};
    (lv.resistance || []).forEach((c, i) => overlays.push({ value: c.tick, label: `壓力${i ? '二' : '一'} ${fmt(c.tick)}`, short: '壓力', color: css('--gold', '#94681a'), pos: 'above', align: i ? 'left' : 'right' }));
    (lv.support || []).forEach((c, i) => overlays.push({ value: c.tick, label: `支撐${i ? '二' : '一'} ${fmt(c.tick)}`, short: '支撐', color: css('--down', '#0f8058'), pos: 'below', align: i ? 'left' : 'right' }));
    const key = `${r.code}:${r.as_of}:${chartPeriod}`;
    const savedView = chart && key === lastChartKey ? { ...chart.view } : null;
    const shownBefore = chart ? chart.candles : [];
    // 歷史接上後，日 K 比伺服器預算的 MACD 多出很多根：改由圖表用完整序列自己算。
    const extended = !intraday && daily.length > r.bars.length;
    if (!chart) chart = new window.KChart($('chartHost'), { macd: false, studyHost: $('chartStudies'), studies: ['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'], maPeriods, selectionRoots: [$('chartHost').parentElement], volumeKind: 'volume', overlayLabels: 'tags', height: w => Math.max(300, Math.min(460, w * 0.52)) });
    const expected = { fast: 12, slow: 26, signal: 9, histogram_multiplier: 1, seed: 'first_close', warmup_bars: 34,
      precision: 3, missing_policy: 'omit_missing_price_rows', version: 'macd-first-close-v1' };
    const compatible = !r._intraday && chartPeriod === 'daily' && r.chart && Object.entries(expected).every(([field, value]) => r.chart.parameters?.[field] === value)
      && Array.isArray(r.chart.macd) && r.chart.macd.length === daily.length
      && r.chart.macd.every((row, i) => row.date === daily[i].t);
    const unit = seconds ? `${seconds}秒` : intraday ? `${intradayKey}分` : chartPeriod === 'monthly' ? '月' : chartPeriod === 'weekly' ? '週' : '日';
    const defaultCount = intraday ? 80 : innerWidth < 600 ? 50 : 90;
    chart.setData(candles, { count: Math.min(candles.length, defaultCount), overlays, unit,
      macd: intraday || chartPeriod !== 'daily' || r._intraday || extended ? null : compatible ? r.chart.macd : [],
      macdNotice: compatible || intraday || chartPeriod !== 'daily' || r._intraday || extended ? '' : 'MACD版本或日期不符，暫不顯示' });
    if (savedView) {
      // 歷史接在前面時，原本看的那一段往後移了幾根就跟著移，畫面停在原地。
      const shift = shownBefore.length ? candles.findIndex(candle => candle.t === shownBefore[0].t) : 0;
      if (shift > 0) savedView.end += shift;
      // 原本就看著最新一根：新的 K 棒進來時跟著走，不讓最新價跑出畫面。
      if (savedView.end >= shownBefore.length) savedView.end = candles.length;
      // 剛切過來時資料還沒到齊、畫面只能擠幾根：之後資料補上來，就放寬到平常的根數。
      if (shownBefore.length < defaultCount) savedView.count = Math.max(savedView.count, Math.min(candles.length, defaultCount));
      chart.view = savedView; chart.clamp(); chart.draw();
    }
    lastChartKey = key;
    if (intraday) $('stockChartContext').textContent = `${unit}K · ${candles.length} 根`;
    else $('stockChartContext').textContent = `${unit}K · 資料日 ${r.as_of}`;
    chart.canvas.setAttribute('aria-label', candles.length
      ? `${r.name}${unit}K，${candles[0].label}至${candles[candles.length - 1].label}，含支撐與壓力參考線。`
      : `${r.name}${unit}K，尚無盤中資料。`);
  }

  // 契約 v2 本身帶市場別，不必再打一次搜尋 API。
  function showMarket(r) {
    const badge = $('stockMarket');
    badge.hidden = !r.market;
    badge.textContent = r.market || '';
  }

  // G3：此股若出現在已封存的籌碼選股名單中，顯示在哪些分頁、第幾名，以及所屬產業的資金狀態；不在名單就不顯示。
  const SIDE_TEXT = { buy: '淨買超', sell: '淨賣超' };

  function reasonEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 同一分頁、同一方向合併成一行：例如「外資淨買超排行・淨賣超：當日第 2 名、3 日第 3 名」。
  function membershipLines(memberships) {
    const groups = new Map();
    memberships.forEach(m => {
      const streak = m.streak_days !== null && m.streak_days !== undefined;
      const key = `${m.key}|${m.side}`;
      if (!groups.has(key)) groups.set(key, { title: `${m.display_name}・${streak ? '連續' : ''}${SIDE_TEXT[m.side]}`, parts: [] });
      groups.get(key).parts.push(streak ? `第 ${m.rank} 名（${m.streak_days} 天）` : `${m.days === 1 ? '當日' : `${m.days} 日`}第 ${m.rank} 名`);
    });
    return [...groups.values()].map(g => `${g.title}：${g.parts.join('、')}`);
  }

  function showBigHolders(r) {
    const host = $('bigHolders');
    if (!host) return;
    const line = holdersLine(r.big_holders);
    if (!line) { host.replaceChildren(); return; }
    const section = reasonEl('section', 'sk-card sk-reason sk-membership');  // 與上方「籌碼選股名單」同一套版面
    const head = reasonEl('div', 'sk-reason-head');
    head.append(reasonEl('h2', '', '大戶持股'), reasonEl('span', 'sk-reason-meta', `資料日 ${line.date}`));
    section.append(head, reasonEl('p', 'sk-reason-judgment', line.text));
    host.replaceChildren(section);
  }

  async function showCandidateReasons(code) {
    const host = $('candidateReasons');
    if (!host || host.dataset.code === code) return;
    host.dataset.code = code;
    host.replaceChildren();
    try {
      const response = await fetch(`/api/screens/membership?${new URLSearchParams({ code })}`, { cache: 'no-store' });
      const data = response.ok ? await response.json() : null;
      if (!data || host.dataset.code !== code || !Array.isArray(data.memberships) || data.memberships.length === 0) return;
      const section = reasonEl('section', 'sk-card sk-reason sk-membership');
      const head = reasonEl('div', 'sk-reason-head');
      head.append(reasonEl('h2', '', '出現在籌碼選股名單'), reasonEl('span', 'sk-reason-meta', `資料日 ${data.data_date}`));
      section.append(head);
      const ul = reasonEl('ul', 'sk-reason-invalid');
      membershipLines(data.memberships).forEach(text => ul.append(reasonEl('li', '', text)));
      section.append(ul);
      if (data.industry) section.append(reasonEl('p', 'sk-reason-judgment', `所屬產業：${data.industry.name}｜${data.industry.label}`));
      host.replaceChildren(section);
    } catch (_) { /* 名單讀不到就不顯示，不影響分析 */ }
  }

  // ---------- 和 0050 對照 ----------
  const svgNode = (tag, attrs = {}) => {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
    return el;
  };
  const compareItems = payload => (payload.item ? [payload.item] : (payload.items || [])).filter(item => item.status === 'ok');
  const comparePercent = value => `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}%`;
  const compareDrawdown = value => value === 0 ? '0.0%' : `${Number(value).toFixed(1)}%`;

  function compareColorToken(code) {
    if (!compareState.colors.has(code)) {
      const used = new Set(compareState.colors.values());
      compareState.colors.set(code, COMPARE_COLORS.find(token => !used.has(token)) || COMPARE_COLORS[compareState.colors.size % COMPARE_COLORS.length]);
    }
    return compareState.colors.get(code);
  }

  function compareCss(name, fallback) {
    return getComputedStyle($('compareBlock')).getPropertyValue(name).trim() || fallback;
  }

  function compareColor(code) {
    return compareCss(compareColorToken(code), '#2a78d6');
  }

  function setCompareStatus(state, text) {
    $('compareStatus').dataset.state = state;
    $('compareStatus').textContent = text;
  }

  function renderCompareChips() {
    $('compareChips').replaceChildren(...compareState.codes.map((code, index) => {
      const chip = node('span', `bd-compare-chip${index === 0 ? ' is-current' : ''}`);
      chip.dataset.code = code;
      chip.style.setProperty('--chip-color', compareColor(code));
      const line = node('i');
      line.setAttribute('aria-hidden', 'true');
      const label = node('span', '', `${compareState.names.get(code) || code} ${code}`);
      const remove = node('button', '', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', `移除 ${compareState.names.get(code) || code} ${code}`);
      remove.addEventListener('click', () => {
        compareState.codes = compareState.codes.filter(value => value !== code);
        compareState.hidden.delete(code);
        compareState.colors.delete(code);
        renderCompareChips();
        loadComparison();
      });
      chip.append(line, label, remove);
      return chip;
    }));
  }

  function comparisonBasis(item, benchmark) {
    if (item?.dividends_included === true && benchmark?.dividends_included === true) {
      return { comparable: true, label: '' };
    }
    if (item?.dividends_included === false || benchmark?.dividends_included === false) {
      return { comparable: false, label: '配息未完整' };
    }
    return { comparable: false, label: '配息狀態未知' };
  }

  function comparisonConclusion(items, benchmark) {
    const candidates = items.map(item => ({
      item,
      basis: comparisonBasis(item, benchmark),
      row: (item.periods || []).find(period => period.period === '1y'),
    }));
    const rows = candidates.filter(entry => entry.basis.comparable && entry.row && entry.row.status === 'ok');
    const excluded = candidates.filter(entry => !entry.basis.comparable);
    if (!rows.length) {
      if (excluded.length) {
        const label = excluded.some(entry => entry.basis.label === '配息未完整') ? '配息未完整' : '配息狀態未知';
        return `${label}，和 0050 不可比較。`;
      }
      const item = items[0];
      return `${item ? item.code : compareState.current} 近 1 年資料不足，暫時無法和 0050 比較。`;
    }
    if (items.length === 1) {
      const { item, row } = rows[0];
      const gap = Number(row.excess_pct);
      if (gap === 0) return `${item.name} ${item.code} 近 1 年和 0050 持平。`;
      return `${item.name} ${item.code} 近 1 年${gap > 0 ? '贏過' : '落後'} 0050 ${Math.abs(gap).toFixed(1)} 個百分點。`;
    }
    rows.sort((a, b) => b.row.excess_pct - a.row.excess_pct);
    const { item, row } = rows[0];
    const gap = Number(row.excess_pct);
    const excludedLabels = [...new Set(excluded.map(entry => entry.basis.label))];
    const excludedNote = excludedLabels.length ? `；${excludedLabels.join('、')}標的未納入比較。` : '';
    return `${item.name} ${item.code} 在可比標的中近 1 年表現最佳，${gap >= 0 ? '領先' : '仍落後'} 0050 ${Math.abs(gap).toFixed(1)} 個百分點${excludedNote}`;
  }

  window.StockComparison = { basis: comparisonBasis, conclusion: comparisonConclusion };

  function appendCompareCell(row, text, label, className = '') {
    const cell = node('td', className, text);
    cell.dataset.label = label;
    row.append(cell);
  }

  function renderCompareTable(items) {
    const rows = [];
    items.forEach(item => {
      const periods = new Map((item.periods || []).map(period => [period.period, period]));
      const basis = comparisonBasis(item, compareState.payload?.benchmark);
      COMPARE_PERIODS.forEach((key, index) => {
        const period = periods.get(key) || { period: key, label: key, status: 'insufficient_history' };
        const tr = document.createElement('tr');
        tr.dataset.code = item.code;
        tr.dataset.groupStart = String(index === 0);
        const stock = node('td', 'cmp-stock');
        stock.append(node('span', '', item.name || item.code), node('small', '', item.code));
        if (!basis.comparable) stock.append(node('small', '', basis.label));
        tr.append(stock);
        const periodHead = node('th', 'cmp-period', period.label);
        periodHead.scope = 'row';
        tr.append(periodHead);
        if (period.status !== 'ok') {
          appendCompareCell(tr, '資料不足', '個股報酬', 'cmp-missing');
          appendCompareCell(tr, '—', '0050 報酬', 'cmp-missing');
          appendCompareCell(tr, '—', '差距', 'cmp-missing');
          appendCompareCell(tr, '—', '個股最大回撤', 'cmp-missing');
          appendCompareCell(tr, '—', '0050 最大回撤', 'cmp-missing');
        } else if (!basis.comparable) {
          const itemComplete = item.dividends_included === true;
          const benchmarkComplete = compareState.payload?.benchmark?.dividends_included === true;
          appendCompareCell(tr, itemComplete ? comparePercent(period.stock_return_pct) : '—', '個股報酬', itemComplete ? '' : 'cmp-missing');
          appendCompareCell(tr, benchmarkComplete ? comparePercent(period.benchmark_return_pct) : '—', '0050 報酬', benchmarkComplete ? '' : 'cmp-missing');
          appendCompareCell(tr, '不可比', '差距', 'cmp-missing');
          appendCompareCell(tr, itemComplete ? compareDrawdown(period.stock_max_drawdown_pct) : '—', '個股最大回撤', itemComplete ? '' : 'cmp-missing');
          appendCompareCell(tr, benchmarkComplete ? compareDrawdown(period.benchmark_max_drawdown_pct) : '—', '0050 最大回撤', benchmarkComplete ? '' : 'cmp-missing');
        } else {
          appendCompareCell(tr, comparePercent(period.stock_return_pct), '個股報酬');
          appendCompareCell(tr, comparePercent(period.benchmark_return_pct), '0050 報酬');
          const gapClass = period.excess_pct > 0 ? 'cmp-positive' : period.excess_pct < 0 ? 'cmp-negative' : '';
          appendCompareCell(tr, comparePercent(period.excess_pct), '差距', gapClass);
          appendCompareCell(tr, compareDrawdown(period.stock_max_drawdown_pct), '個股最大回撤');
          appendCompareCell(tr, compareDrawdown(period.benchmark_max_drawdown_pct), '0050 最大回撤');
        }
        rows.push(tr);
      });
    });
    $('compareTableBody').replaceChildren(...rows);
  }

  function comparisonSeries(payload) {
    const items = compareItems(payload);
    const source = [];
    const benchmarkPoints = payload.item ? payload.item.benchmark_chart : payload.benchmark && payload.benchmark.chart;
    if (payload.benchmark?.dividends_included === true) {
      source.push({ key: '0050', label: '0050', color: compareCss('--cmp-benchmark', '#1d1b20'), dash: '8 5', points: benchmarkPoints || [] });
    }
    items.forEach((item, index) => {
      if (!comparisonBasis(item, payload.benchmark).comparable) return;
      source.push({
        key: item.code, label: `${item.name || item.code} ${item.code}`, color: compareColor(item.code),
        dash: COMPARE_DASHES[index] || '', points: item.chart || [],
      });
    });
    const valid = source.filter(series => Array.isArray(series.points) && series.points.length > 1);
    if (!valid.length) return [];
    const commonStart = valid.map(series => series.points[0][0]).sort().at(-1);
    return valid.map(series => {
      const points = series.points.filter(point => point[0] >= commonStart);
      if (!points.length) return { ...series, points: [] };
      const base = Number(points[0][1]);
      return { ...series, points: points.map(point => [point[0], Number(point[1]) / base * 100]) };
    }).filter(series => series.points.length > 1);
  }

  function renderCompareLegend(series) {
    const buttons = series.map(item => {
      const button = node('button');
      button.type = 'button';
      button.dataset.key = item.key;
      button.setAttribute('aria-pressed', String(!compareState.hidden.has(item.key)));
      button.setAttribute('aria-label', `顯示或隱藏 ${item.label}`);
      const line = node('i', item.dash ? 'is-dashed' : '');
      line.style.setProperty('--series-color', item.color);
      button.append(line, node('span', '', item.label));
      button.addEventListener('click', () => {
        if (compareState.hidden.has(item.key)) compareState.hidden.delete(item.key);
        else if (series.length - compareState.hidden.size > 1) compareState.hidden.add(item.key);
        drawComparisonChart(compareState.payload);
      });
      return button;
    });
    $('compareLegend').replaceChildren(...buttons);
  }

  function drawComparisonChart(payload) {
    if (!payload) return;
    const allSeries = comparisonSeries(payload);
    renderCompareLegend(allSeries);
    const series = allSeries.filter(item => !compareState.hidden.has(item.key));
    const host = $('compareChart');
    const tooltip = $('compareTooltip');
    tooltip.hidden = true;
    host.replaceChildren();
    if (!series.length) {
      host.append(node('p', 'bd-compare-empty', '目前沒有足夠的走勢資料。'));
      return;
    }

    const compact = innerWidth <= 700;
    const W = compact ? 620 : 900, H = compact ? 340 : 310, L = compact ? 48 : 54, R = compact ? 24 : 72, T = 20, B = 34;
    const times = series.flatMap(item => item.points.map(point => Date.parse(point[0]))).filter(Number.isFinite);
    const values = series.flatMap(item => item.points.map(point => point[1])).filter(Number.isFinite);
    const minTime = Math.min(...times), maxTime = Math.max(...times);
    let minValue = Math.min(100, ...values), maxValue = Math.max(100, ...values);
    const pad = Math.max(2, (maxValue - minValue) * .08);
    minValue -= pad; maxValue += pad;
    const x = time => L + (time - minTime) / Math.max(1, maxTime - minTime) * (W - L - R);
    const y = value => T + (maxValue - value) / Math.max(1, maxValue - minValue) * (H - T - B);
    const svg = svgNode('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', tabindex: '0', 'aria-describedby': 'compareTooltip',
      'aria-label': `${series.map(item => item.label).join('、')}含息報酬走勢，共同起點為 100` });

    for (let i = 0; i < 5; i += 1) {
      const value = minValue + (maxValue - minValue) * i / 4;
      const yy = y(value);
      svg.append(svgNode('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'cmp-grid' }));
      const label = svgNode('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', class: 'cmp-axis-label' });
      label.textContent = `${Math.round(value - 100)}%`;
      svg.append(label);
    }
    const zeroY = y(100);
    svg.append(svgNode('line', { x1: L, x2: W - R, y1: zeroY, y2: zeroY, class: 'cmp-base' }));
    const zeroLabel = svgNode('text', { x: L - 8, y: zeroY + 4, 'text-anchor': 'end', class: 'cmp-axis-label' });
    zeroLabel.textContent = '0%';
    svg.append(zeroLabel);
    for (let i = 0; i < 4; i += 1) {
      const time = minTime + (maxTime - minTime) * i / 3;
      const label = svgNode('text', { x: x(time), y: H - 8, 'text-anchor': i === 0 ? 'start' : i === 3 ? 'end' : 'middle', class: 'cmp-axis-label' });
      const date = new Date(time);
      label.textContent = `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
      svg.append(label);
    }

    const labelYs = [];
    series.forEach((item, index) => {
      const path = svgNode('path', { class: 'cmp-line', stroke: item.color, 'data-key': item.key,
        d: item.points.map((point, pointIndex) => `${pointIndex ? 'L' : 'M'}${x(Date.parse(point[0])).toFixed(2)},${y(point[1]).toFixed(2)}`).join(' ') });
      if (item.dash) path.setAttribute('stroke-dasharray', item.dash);
      svg.append(path);
      if (index < 2) {
        const end = item.points[item.points.length - 1];
        let labelY = y(end[1]) + 4;
        while (labelYs.some(value => Math.abs(value - labelY) < 13)) labelY += 13;
        labelYs.push(labelY);
        const label = svgNode('text', { x: W - R + 7, y: labelY, fill: item.color, class: 'cmp-direct-label' });
        label.textContent = item.key;
        svg.append(label);
      }
    });

    const crosshair = svgNode('line', { x1: L, x2: L, y1: T, y2: H - B, class: 'cmp-crosshair', hidden: 'hidden' });
    svg.append(crosshair);
    const hit = svgNode('rect', { x: L, y: T, width: W - L - R, height: H - T - B, class: 'cmp-hit' });
    svg.append(hit);
    host.append(svg);

    const availableDates = [...new Set(series.flatMap(item => item.points.map(point => point[0])))].sort();
    let keyboardIndex = availableDates.length - 1;
    const nearestPoint = (points, time) => points.reduce((best, point) => Math.abs(Date.parse(point[0]) - time) < Math.abs(Date.parse(best[0]) - time) ? point : best, points[0]);
    const showAt = (time, clientX = null) => {
      const nearestDate = availableDates.reduce((best, date) => Math.abs(Date.parse(date) - time) < Math.abs(Date.parse(best) - time) ? date : best, availableDates[0]);
      const selectedTime = Date.parse(nearestDate);
      const xx = x(selectedTime);
      crosshair.setAttribute('x1', xx); crosshair.setAttribute('x2', xx); crosshair.removeAttribute('hidden');
      const title = document.createElement('time');
      title.textContent = nearestDate;
      const rows = series.map(item => {
        const point = nearestPoint(item.points, selectedTime);
        const row = document.createElement('p');
        const label = node('span');
        const line = node('i'); line.style.setProperty('--series-color', item.color);
        label.append(line, document.createTextNode(item.label));
        const value = node('b', '', comparePercent(point[1] - 100));
        row.append(label, value);
        return row;
      });
      tooltip.replaceChildren(title, ...rows);
      tooltip.hidden = false;
      const block = tooltip.parentElement.getBoundingClientRect();
      const svgRect = svg.getBoundingClientRect();
      const pointer = clientX === null ? svgRect.left + (xx / W) * svgRect.width : clientX;
      const left = Math.max(0, Math.min(block.width - Math.min(280, block.width - 24), pointer - block.left + 12));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${Math.max(54, svgRect.top - block.top + 28)}px`;
    };
    const showAtPointer = event => {
      const rect = svg.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(W - L - R, (event.clientX - rect.left) / rect.width * W - L)) / (W - L - R);
      showAt(minTime + ratio * (maxTime - minTime), event.clientX);
    };
    hit.addEventListener('pointermove', showAtPointer);
    hit.addEventListener('pointerdown', showAtPointer);
    hit.addEventListener('pointerleave', event => {
      if (event.pointerType === 'touch') return;
      tooltip.hidden = true;
      crosshair.setAttribute('hidden', 'hidden');
    });
    svg.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      keyboardIndex = Math.max(0, Math.min(availableDates.length - 1, keyboardIndex + (event.key === 'ArrowRight' ? 1 : -1)));
      showAt(Date.parse(availableDates[keyboardIndex]));
    });
  }

  function renderComparison(payload) {
    const items = compareItems(payload);
    compareState.payload = payload;
    items.forEach(item => compareState.names.set(item.code, item.name || item.code));
    $('compareDate').textContent = `資料日 ${payload.data_date}`;
    $('compareConclusion').textContent = comparisonConclusion(items, payload.benchmark);
    renderCompareTable(items);
    renderCompareChips();
    drawComparisonChart(payload);
    $('compareBlock').hidden = false;
    setCompareStatus('ok', '對照資料已更新');
  }

  async function loadComparison() {
    const sequence = ++compareState.request;
    if (compareState.abort) compareState.abort.abort();
    compareState.abort = new AbortController();
    setCompareStatus('loading', '讀取對照資料');
    const path = compareState.codes.length === 1
      ? `/api/stocks/${encodeURIComponent(compareState.codes[0])}/vs-0050`
      : `/api/compare-0050?${new URLSearchParams({ codes: compareState.codes.join(',') })}`;
    try {
      const response = await fetch(path, { cache: 'no-store', signal: compareState.abort.signal });
      const data = await response.json();
      if (sequence !== compareState.request) return;
      if (!response.ok || data.error) throw new Error(data.message || '對照資料讀取失敗');
      if (Array.isArray(data.items)) {
        const rejected = data.items.filter(item => item.status !== 'ok');
        if (rejected.length) {
          compareState.codes = compareState.codes.filter(code => !rejected.some(item => item.code === code));
          rejected.forEach(item => compareState.colors.delete(item.code));
          renderCompareChips();
          renderComparison(data);
          setCompareStatus('error', `${rejected.map(item => item.code).join('、')} 不在本站涵蓋的大量股，未加入比較。`);
          return;
        }
      }
      renderComparison(data);
    } catch (error) {
      if (sequence !== compareState.request || error.name === 'AbortError') return;
      setCompareStatus('error', error.message || '對照資料讀取失敗，請稍後再試。');
    }
  }

  function clearComparisonView() {
    $('compareConclusion').textContent = '讀取中';
    $('compareDate').textContent = '';
    $('compareTableBody').replaceChildren();
    $('compareLegend').replaceChildren();
    $('compareChart').replaceChildren(node('p', 'bd-compare-empty', '讀取對照資料'));
    $('compareTooltip').hidden = true;
  }

  function syncComparison(result) {
    if (compareState.current === result.code) return;
    compareState.current = result.code;
    compareState.codes = [result.code];
    compareState.names = new Map([[result.code, result.name || result.code]]);
    compareState.colors = new Map();
    compareState.hidden = new Set();
    compareState.payload = null;
    compareColorToken(result.code);
    clearComparisonView();
    $('compareBlock').hidden = false;
    renderCompareChips();
    loadComparison();
  }

  function closeCompareResults() {
    clearTimeout(compareState.searchTimer);
    if (compareState.searchAbort) compareState.searchAbort.abort();
    compareState.searchAbort = null;
    compareState.searchOptions = [];
    compareState.activeOption = -1;
    $('compareSearchResults').hidden = true;
    $('compareInput').setAttribute('aria-expanded', 'false');
    $('compareInput').removeAttribute('aria-activedescendant');
  }

  function setActiveCompareOption(index) {
    const options = compareState.searchOptions;
    if (!options.length) return;
    compareState.activeOption = (index + options.length) % options.length;
    options.forEach((button, buttonIndex) => button.setAttribute('aria-selected', String(buttonIndex === compareState.activeOption)));
    const active = options[compareState.activeOption];
    $('compareInput').setAttribute('aria-activedescendant', active.id);
    active.scrollIntoView({ block: 'nearest' });
  }

  async function searchComparison(term) {
    if (compareState.searchAbort) compareState.searchAbort.abort();
    const controller = new AbortController();
    compareState.searchAbort = controller;
    try {
      const response = await fetch(`/api/stocks/search?${new URLSearchParams({ q: term, limit: '5' })}`, {
        cache: 'no-store', signal: controller.signal,
      });
      const data = response.ok ? await response.json() : null;
      if ($('compareInput').value.trim() !== term || controller.signal.aborted) return;
      const items = (data && data.items || []).filter(item => item.universe_rank !== null && item.universe_rank !== undefined
        && !compareState.codes.includes(item.code));
      const buttons = items.map((item, index) => {
        const button = node('button');
        button.type = 'button';
        button.id = `compareOption${index}`;
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', 'false');
        button.append(node('span', '', `${item.name} ${item.code}`), node('small', '', item.market));
        button.addEventListener('click', () => {
          $('compareInput').value = item.code;
          closeCompareResults();
          addComparisonCode(item.code, item.name);
        });
        return button;
      });
      compareState.searchAbort = null;
      compareState.searchOptions = buttons;
      compareState.activeOption = -1;
      $('compareSearchResults').replaceChildren(...buttons);
      $('compareSearchResults').hidden = buttons.length === 0;
      $('compareInput').setAttribute('aria-expanded', String(buttons.length > 0));
      $('compareInput').removeAttribute('aria-activedescendant');
    } catch (error) {
      if (error.name !== 'AbortError') closeCompareResults();
    }
  }

  function addComparisonCode(code, name = code) {
    clearTimeout(compareState.searchTimer);
    const normalized = code.trim().toUpperCase();
    if (!/^[0-9A-Z]{4,6}$/.test(normalized)) {
      setCompareStatus('error', '請從搜尋結果選擇股票，或輸入正確代號。');
      return;
    }
    if (normalized === '0050') {
      setCompareStatus('error', '0050 已經是比較基準，不必重複加入。');
      return;
    }
    if (compareState.codes.includes(normalized)) {
      setCompareStatus('error', `${normalized} 已在比較清單中。`);
      return;
    }
    if (compareState.codes.length >= 5) {
      setCompareStatus('error', '最多同時比較 5 檔股票。');
      return;
    }
    compareState.codes.push(normalized);
    compareState.names.set(normalized, name);
    compareColorToken(normalized);
    $('compareInput').value = '';
    closeCompareResults();
    renderCompareChips();
    loadComparison();
  }

  function stopTicks() {
    if (tickFeed.timer) clearTimeout(tickFeed.timer);
    tickFeed.timer = null;
    tickFeed.generation += 1;
  }

  // 只在選了秒 K、頁面看得到的時候才問逐筆：還在補當天稍早的成交時一秒一頁，追上之後每 3 秒問一次新成交。
  async function pollTicks(code, generation) {
    if (generation !== tickFeed.generation) return;
    let delay = 15_000;
    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(code)}/ticks?offset=${tickFeed.offset}`, { cache: 'no-store' });
      if (generation !== tickFeed.generation) return;
      if (response.status === 403 || response.status === 404) return;  // 沒有權限：不再問
      if (response.status === 429) delay = (Number(response.headers.get('retry-after')) || 60) * 1000;
      else if (response.ok) {
        const page = await response.json();
        if (generation !== tickFeed.generation) return;
        if (tickFeed.date && page.date !== tickFeed.date) {
          // 換了交易日：前一天的逐筆不能和今天的接在一起，從頭來。
          tickFeed = { ...tickFeed, date: page.date, ticks: [], offset: 0 };
          delay = 0;
        } else {
          tickFeed.date = page.date;
          tickFeed.offset = page.next_offset;
          if (page.ticks.length) {
            tickFeed.ticks.push(...page.ticks);
            if (lastResult && lastResult.code === code && SECOND_PERIODS[chartPeriod]) drawChart(lastResult);
          }
          delay = !page.complete ? 1000 : lastLive?.market_open ? 3000 : 60_000;
        }
      }
    } catch (_) { /* 連不上：稍後再試，圖維持已取得的部分 */ }
    if (generation === tickFeed.generation) tickFeed.timer = setTimeout(() => pollTicks(code, generation), delay);
  }

  function syncTickFeed() {
    const wanted = Boolean(SECOND_PERIODS[chartPeriod]) && lastResult && canUseLive(lastResult) && !document.hidden;
    stopTicks();
    if (!wanted) return;
    if (tickFeed.code !== lastResult.code) tickFeed = { ...tickFeed, code: lastResult.code, date: null, ticks: [], offset: 0 };
    pollTicks(lastResult.code, tickFeed.generation);
  }

  async function loadHistory(result, sequence) {
    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(result.code)}/history`, { cache: 'no-store' });
      if (!response.ok || sequence !== requestSequence) return;
      const rows = window.KChartMath.olderBars(result.bars, await response.json());
      if (!rows.length || sequence !== requestSequence) return;
      olderBars = { code: result.code, firstDate: result.bars[0].date, rows };
      if (lastResult && lastResult.code === result.code) drawChart(lastResult);
    } catch (_) { /* 歷史是附加的：失敗就維持最近 120 根 */ }
  }

  async function load(code) {
    const sequence = ++requestSequence;
    livePoller.stop();
    baseResult = null;
    lastLive = null;
    olderBars = { code: '', firstDate: '', rows: [] };
    stopTicks();
    if (pendingRequest) pendingRequest.abort();
    pendingRequest = new AbortController();
    $('content').hidden = true;
    $('outside').hidden = true;
    $('content').setAttribute('aria-busy', 'true');
    $('status').hidden = false;
    delete $('status').dataset.stockState;
    $('status').textContent = `讀取 ${code}`;
    $('codeInput').value = code;
    if (location.protocol === 'file:') {
      $('content').setAttribute('aria-busy', 'false');
      $('status').textContent = '請從本機服務開啟（http://127.0.0.1:8876/stock.html），個股分析需要後端計算。';
      return;
    }
    const params = new URLSearchParams();
    const asOf = new URLSearchParams(location.search).get('as_of');
    if (asOf) params.set('as_of', asOf);
    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(code)}/analysis?${params}`, { cache: 'no-store', signal: pendingRequest.signal });
      const data = await response.json();
      if (sequence !== requestSequence) return;
      if (data.error === 'outside-universe') {
        // 和「資料尚未就緒」分開：不畫 K 線、不顯示任何分析數字。
        showOutside(data);
        return;
      }
      if (!response.ok || data.error || (data.qa && data.qa.blocking)) {
        const stockState = data.stock_state || '';
        $('status').dataset.stockState = stockState;
        $('status').textContent = STOCK_STATE_MESSAGE[stockState]
          ? STOCK_STATE_MESSAGE[stockState](code)
          : data.qa && data.qa.reason ? `${code}：${data.qa.reason}，不給結論。` : `${code}：${data.message || '查不到資料'}`;
        return;
      }
      if (data.schema !== SCHEMA) {
        $('status').textContent = '本機服務尚未載入個股分析新版，請更新服務後再使用。';
        return;
      }
      baseResult = data;
      render(data, true);
      loadHistory(data, sequence);
      syncTickFeed();
      if (canUseLive(data)) livePoller.start(code, data.market);
      else updateLiveStatus({ ok: true, market_open: false, live: false });
    } catch (error) {
      if (sequence !== requestSequence || error.name === 'AbortError') return;
      $('status').textContent = '本機服務沒有回應，請稍後再試。';
    } finally {
      if (sequence === requestSequence) $('content').setAttribute('aria-busy', 'false');
    }
  }

  $('compareForm').addEventListener('submit', event => {
    event.preventDefault();
    addComparisonCode($('compareInput').value);
  });
  $('compareInput').addEventListener('input', () => {
    const term = $('compareInput').value.trim();
    closeCompareResults();
    if (term.length < 2) return;
    compareState.searchTimer = setTimeout(() => searchComparison(term), 180);
  });
  $('compareInput').addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeCompareResults(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!compareState.searchOptions.length) return;
      event.preventDefault();
      setActiveCompareOption(compareState.activeOption + (event.key === 'ArrowDown' ? 1 : -1));
      return;
    }
    if (event.key === 'Enter' && compareState.activeOption >= 0) {
      event.preventDefault();
      compareState.searchOptions[compareState.activeOption].click();
    }
  });
  document.addEventListener('click', event => {
    if (!$('compareForm').contains(event.target)) closeCompareResults();
  });

  $('searchForm').addEventListener('submit', event => {
    event.preventDefault();
    const code = $('codeInput').value.trim().toUpperCase();
    if (!/^[0-9A-Z]{4,6}$/.test(code)) { $('status').hidden = false; $('status').textContent = '代號格式不正確。'; return; }
    updateUrl(code);
    load(code);
  });
  $('periodSelect').addEventListener('change', () => {
    const selected = $('periodSelect').value;
    chartPeriod = ['1s', '5s', '10s', '15s', '30s', '1m', '5m', '15m', '60m', 'weekly', 'monthly'].includes(selected) ? selected : 'daily';
    if (lastResult) drawChart(lastResult);
    syncTickFeed();
  });
  window.addEventListener('popstate', () => load(codeFromUrl() || '2330'));
  window.addEventListener('beforeunload', () => { livePoller.stop(); stopTicks(); });
  document.addEventListener('visibilitychange', syncTickFeed);
  $('addMa').addEventListener('click', () => {
    if (maPeriods.length >= MA_LIMIT) return;
    // 從常用的期數裡找第一個還沒用到的（最多四條，六個候選一定找得到），不會加出重複而被併掉
    const next = [60, 120, 240, 20, 10, 5].find(value => !maPeriods.includes(value));
    applyMaPeriods([...maPeriods, next]);
  });
  $('themeToggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  let saved = null;
  try { saved = localStorage.getItem('market-theme'); } catch (_) { /* 私密視窗 */ }
  renderMaControls();
  syncMaLimit();
  setTheme(saved === 'dark' ? 'dark' : 'light');
  window.StockView = { weeklyCandles, monthlyCandles };
  loadReleaseFeatures().then(() => load(codeFromUrl() || '2330'));
})();
