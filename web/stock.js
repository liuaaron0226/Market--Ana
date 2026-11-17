/* 個股分析頁：只向後端 API 取個股分析契約 v2 並畫圖，不在前端算價位。每檔一份分析，沒有方案與總分。 */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
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
    // 基金的操作策略要用淨值另算（04 票）；還沒有的時候照實寫「資料不足」、不出操作價位，不拿股票的規則猜（規格第 15 條）。
    const pending = isFund(r) && !r.strategy;
    $('strategyDetails').hidden = pending;
    if (!features.strategy_v1) return;
    if (pending) { $('strategyConclusion').textContent = `資料不足｜${r.levels?.reason || '基金的操作價位尚未提供'}`; return; }
    const strategy = buildStrategy(r);
    $('strategyConclusion').textContent = `${strategy.recommendation}｜${strategy.reason}`;
    for (const [key, id] of Object.entries({ entry: 'strategyEntry', add: 'strategyAdd', stop: 'strategyStop', target: 'strategyTarget' })) {
      $(id).textContent = fmt(strategy.prices?.[key]);
    }
    $('strategyEvidence').textContent = `證據強度：${strategy.evidence_strength}`;
  }

  window.StockStrategy = { build: buildStrategy };

  // 基金當成一般個股 03 票（本人 11-18 選樣品 B）：分析契約 v2 的 kind "fund"。基金每天只有一個淨值（約晚 3 個營業日公布），
  // 沒有即時行情、逐筆、當日三格、籌碼、事實整理、大戶持股、國外同類股與公告，這些區塊整個不出現；
  // 分類鍵先只留「比較」（價位、配息、成分股由 04、06、08 票加上）。圖是淨值線，週期只有日／週／月，
  // 指標只留不需要量、高低價與法人的 MACD、RSI。
  const isFund = r => r?.kind === 'fund';
  const FUND_PERIODS = ['daily', 'weekly', 'monthly'];
  const FUND_STUDIES = ['macd', 'rsi'];
  const CURRENCY_NAMES = { USD: '美元', TWD: '台幣' };
  const NAV_LAG_NOTE = '淨值每天一個，約晚 3 個營業日公布';
  let chartKind = '';

  function applyKind(r) {
    const fund = isFund(r);
    document.body.dataset.kind = fund ? 'fund' : 'stock';
    for (const id of ['levelsPanel', 'chipsPanel']) {
      const button = $('stockCats').querySelector(`[aria-controls="${id}"]`);
      if (button) button.hidden = fund;
    }
    for (const group of $('periodSelect').querySelectorAll('optgroup')) {
      const longOnly = [...group.querySelectorAll('option')].every(option => FUND_PERIODS.includes(option.value));
      group.hidden = fund && !longOnly;
      group.querySelectorAll('option').forEach(option => {
        option.disabled = fund && !longOnly;
        // 基金沒有 K 棒：日／週／月不寫「K」（原本的字記在 data-k，換回股票時還原）
        if (longOnly) {
          option.dataset.k ??= option.textContent;
          option.textContent = fund ? option.dataset.k.replace('K', '') : option.dataset.k;
        }
      });
    }
    if (fund && !FUND_PERIODS.includes(chartPeriod)) { chartPeriod = 'daily'; $('periodSelect').value = 'daily'; }
    $('periodSelect').dataset.line = fund ? '1' : '';
    $('periodSelect').dispatchEvent(new Event('periodsync'));  // 週期小鍵與選單跟著改（period-switch.js）
    if (fund) {
      for (const id of ['stockFacts', 'stockFactsBlocks', 'watchPeers', 'liveBadge']) $(id).hidden = true;
      $('liveStatus').dataset.state = 'closed';
      $('liveStatus').textContent = r.notes?.nav_lag || NAV_LAG_NOTE;
    }
  }

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
  // 畫線的存檔與補存（drawing-sync.js）：存不上去時留著、之後自動補存，並告訴這裡有幾筆還沒存上去。
  // 那支沒載入時整個畫線功能收起，個股頁其他內容照常。
  const drawingSync = window.DrawingSync ? window.DrawingSync.create({ onStatus: showUnsavedDrawings }) : null;
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
    period: '1y', stocks: [],   // 期間的鍵選的是哪一個；每一檔每個期間整理好的資料（comparisonPeriods 的結果）
    request: 0, abort: null,
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
    if (lastResult) drawChart(lastResult);
    if (compareState.payload) drawComparisonChart(compareState.payload);
  }

  function codeFromUrl() {
    const code = new URLSearchParams(location.search).get('code');
    return code && window.StockPicker.isCode(code) ? code : null;
  }

  function canUseLive(result) {
    const historical = new URLSearchParams(location.search).has('as_of');
    // 基金沒有即時行情與逐筆（只有每天一個淨值）
    return !historical && result?.kind !== 'fund' && result?.stock_state !== 'stale' && result?.analysis_status !== 'stale';
  }

  // K 線的高度：照寬度算，最矮 300、最高 460。電腦上再多一個上限——視窗高度扣掉 K 線以外要擠進第一個畫面的東西
  // （頂列、報價、圖上的工具列、指標區、三顆分類鍵，量起來約 570px），三顆分類鍵才不用捲就看得到（本人 2026-11-15「改矮一點」）。
  // 視窗夠高時照舊是 460；手機本來就是 300，不受影響。
  // 2026-11-17 重量：當日三格與操作策略搬到「技術」之後，報價卡只剩價格，K 線以外的高度量起來是 500（1440×900），留 4px 餘裕
  const CHART_REST_PX = 504;
  const chartHeight = width => Math.max(300, Math.min(460, width * 0.52, innerHeight - CHART_REST_PX));

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
    if (isFund(baseResult)) return;  // 基金那一行寫的是淨值公布時間（applyKind），不是行情狀態
    const badge = $('liveBadge');
    const status = $('liveStatus');
    const cloudUnavailable = window.Intraday.liveUnavailable(payload);
    badge.hidden = !payload?.live;
    status.dataset.state = cloudUnavailable ? 'error' : payload?.live ? 'live' : 'closed';
    // 收盤後一定寫出顯示的是哪一天的收盤（intraday.js liveStatusText）。
    // 換股票載入中（baseResult 已清空）不拿上一檔的日期來寫。
    status.textContent = window.Intraday.liveStatusText(payload, baseResult && lastResult?.code === baseResult.code ? lastResult : baseResult);
  }

  function applyLive(payload) {
    lastLive = payload;
    // 關注股盤中觸價亮燈：沿用這一份即時報價，不另外取報價。
    if (window.WatchJudgments) window.WatchJudgments.onLive(payload);
    // 盤中用現價投影；收盤後到每日更新發布前，今天的收盤報價比日資料新也投影（本人 11-17：收盤後看到的是昨天的）。
    const projected = payload?.ok && payload.quote && baseResult ? window.Intraday.projectAnalysis(baseResult, payload) : baseResult;
    if (projected && projected !== baseResult) {
      if (features.strategy_v1) projected.strategy = buildStrategy(projected);
      else delete projected.strategy;
      render(projected, false);
    } else if (baseResult) {
      // 13:30 後日資料已是今天、或休市：回到盤後基準；不能只熄掉 LIVE 卻把最後盤中價留在主報價與操作重點。
      render(baseResult, false);
    }
    updateLiveStatus(payload);   // 畫完再寫狀態字：要用畫面上這一份的日期
  }

  function render(r, initial = false) {
    if (initial) {
      document.title = `${r.name} ${r.code}｜個股分析`;
      $('content').dataset.schema = r.schema;
      $('stockName').textContent = r.name;
      $('stockCode').textContent = r.code;
      applyKind(r);
      showMarket(r);
      if (!isFund(r)) showCandidateReasons(r.code);
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
    const caption = window.Intraday.quoteCaption(r);
    $('quoteMode').textContent = caption.mode;
    $('asOf').textContent = caption.stamp;
    $('ladderCurrentLabel').textContent = caption.ladderLabel;
    $('ladderCurrentMeta').textContent = caption.ladderMeta;
    if (isFund(r)) {
      // 基金寫淨值日（不是資料日）；漲跌連金額一起寫，例如「-0.59（-3.33%）」
      $('quoteMode').textContent = '淨值日';
      $('asOf').textContent = r.quote.nav_date || r.as_of;
    }
    $('close').textContent = fmt(r.quote.close);
    const chg = r.quote.chg_pct;
    const pctText = chg === null || chg === undefined ? '' : `${chg > 0 ? '+' : ''}${Number(chg).toFixed(2)}%`;
    const change = r.quote.change;
    $('chg').textContent = isFund(r) && Number.isFinite(change) && pctText
      ? `${change > 0 ? '+' : ''}${fmt(change)}（${pctText}）` : pctText;
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
    if (!isFund(r)) {
      window.StockFacts?.render(r);  // 事實區塊（獨立檔）：沒有 facts 就不出現
      window.WatchPeers?.load(r.code);  // 國外同類股（獨立檔）：只有站主的關注股才出現
      showBigHolders(r);
    }
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
    // 支撐、壓力不再疊在 K 線上（本人 11-17：右側只放簡單的數字，價位看圖下方的價位階梯）；
    // 只剩線沒有名稱會看不懂，所以線也一起拿掉。
    const overlays = [];
    // 自選股關注的觸發線與看錯線（watch-judgments.js；只有站主有判斷時才有）。
    if (window.WatchJudgments) overlays.push(...window.WatchJudgments.overlays(chartPeriod));
    const key = `${r.code}:${r.as_of}:${chartPeriod}`;
    const savedView = chart && key === lastChartKey ? { ...chart.view } : null;
    const shownBefore = chart ? chart.candles : [];
    // 歷史接上後，日 K 比伺服器預算的 MACD 多出很多根：改由圖表用完整序列自己算。
    const extended = !intraday && daily.length > r.bars.length;
    const fund = isFund(r);
    if (!chart) chart = new window.KChart($('chartHost'), { macd: false, studyHost: $('chartStudies'), studies: ['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'], maPeriods, selectionRoots: [$('chartHost').parentElement], volumeKind: 'volume', overlayLabels: 'tags', height: chartHeight,
      drawings: drawingSync && { onChange: drawingSync.record, onTool: showActiveTool, onSelect: showDrawingSelection } });
    // 換了種類（股票↔基金）才重設指標選單：基金只有淨值，藏掉要量、高低價或法人的指標。
    const kind = fund ? 'fund' : 'stock';
    if (chart.studies && chartKind !== kind) chart.studies.setAvailable(fund ? FUND_STUDIES : null);
    chartKind = kind;
    // 畫線只在日、週、月 K：秒 K、分 K 連工具列一起收起。
    $('drawTools').hidden = intraday || !drawingSync;
    const expected = { fast: 12, slow: 26, signal: 9, histogram_multiplier: 1, seed: 'first_close', warmup_bars: 34,
      precision: 3, missing_policy: 'omit_missing_price_rows', version: 'macd-first-close-v1' };
    const compatible = !r._intraday && chartPeriod === 'daily' && r.chart && Object.entries(expected).every(([field, value]) => r.chart.parameters?.[field] === value)
      && Array.isArray(r.chart.macd) && r.chart.macd.length === daily.length
      && r.chart.macd.every((row, i) => row.date === daily[i].t);
    const unit = seconds ? `${seconds}秒` : intraday ? `${intradayKey}分` : chartPeriod === 'monthly' ? '月' : chartPeriod === 'weekly' ? '週' : '日';
    const defaultCount = intraday ? 80 : innerWidth < 600 ? 50 : 90;
    // 基金：淨值畫成一條線（K 線元件的線模式），MACD 由圖用淨值自己算（伺服器沒有預算）。
    chart.setData(candles, { count: Math.min(candles.length, defaultCount), overlays, unit,
      ...(fund ? { mode: 'line', closeLabel: '淨值' } : {}),
      macd: fund || intraday || chartPeriod !== 'daily' || r._intraday || extended ? null : compatible ? r.chart.macd : [],
      macdNotice: fund || compatible || intraday || chartPeriod !== 'daily' || r._intraday || extended ? '' : 'MACD版本或日期不符，暫不顯示' });
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
    if (fund) $('stockChartContext').textContent = `淨值 · ${unit}`;
    else if (intraday) $('stockChartContext').textContent = `${unit}K · ${candles.length} 根`;
    else $('stockChartContext').textContent = `${unit}K · 資料日 ${r.as_of}`;
    const what = fund ? `${r.name}淨值走勢（${unit}）` : `${r.name}${unit}K`;
    chart.canvas.setAttribute('aria-label', candles.length
      ? `${what}，${candles[0].label}至${candles[candles.length - 1].label}。`
      : `${what}，尚無盤中資料。`);
  }

  // 契約 v2 本身帶市場別，不必再打一次搜尋 API。基金寫「基金 · 美元」（計價幣別）。
  function showMarket(r) {
    const badge = $('stockMarket');
    const text = isFund(r) ? `基金 · ${CURRENCY_NAMES[r.currency] || r.currency || ''}` : r.market || '';
    badge.hidden = !text;
    badge.textContent = text;
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

  // 大戶持股（集保股權分散表，每週一份）：算法在 holders-weekly.js，畫面在 holders-card.js；沒有資料整塊不顯示
  function showBigHolders(r) {
    const host = $('bigHolders');
    if (host) window.HoldersCard.render(host, window.HoldersWeekly.read(r.big_holders));
  }

  async function showCandidateReasons(code) {
    const host = $('candidateReasons');
    if (!host || host.dataset.code === code) return;
    host.dataset.code = code;
    host.replaceChildren();
    host.dataset.loading = '';
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
    finally { if (host.dataset.code === code) delete host.dataset.loading; }
  }

  // ---------- 比較（0050 是基準線） ----------
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

  // 每個期間一顆鍵（股票五個、基金多一個「成立以來」）要的東西：每一檔 { code, name, note, periods }，periods 照固定的順序，每個期間
  // { key, label, short 鍵上的短名, kind 'bull' 領先 0050／'bear' 落後／'none' 沒有方向, gap 鍵上寫的差距, facts 點開看的數字 [名稱, 文字]（四個，基金多一行匯率影響） }。
  // 配息資料不完整就不宣告領先落後（note 寫原因、gap 寫「不可比」），有完整配息的那一邊照樣給數字；沒有資料的期間寫「資料不足」。
  // 基金（05 票）：報酬是台幣含息，名稱寫「基金」；股票的五個期間之後多一顆「成立以來」（本人 11-17 選的期間），
  // 每個期間多一行「其中匯率影響 x.x 個百分點」（規格第 27 條）。
  const PERIOD_NAMES = { '1m': ['近 1 個月', '近1月'], '3m': ['近 3 個月', '近3月'], '1y': ['近 1 年', '近1年'], '3y': ['近 3 年', '近3年'], '5y': ['近 5 年', '近5年'], since: ['成立以來', '成立以來'] };
  const FUND_PERIOD_KEYS = [...COMPARE_PERIODS, 'since'];
  const fxEffect = value => (Number.isFinite(value) ? `${value > 0 ? '+' : ''}${Number(value).toFixed(1)} 個百分點` : '—');
  function comparisonPeriods(items, benchmark) {
    return items.map(item => {
      const fund = item.kind === 'fund';
      const facts = (stock, base, stockFall, baseFall, fx) => [
        [fund ? '基金報酬（台幣）' : '個股報酬', stock], ['0050 報酬', base], [fund ? '基金最大回撤' : '個股最大回撤', stockFall], ['0050 最大回撤', baseFall],
        ...(fund ? [['其中匯率影響', fx]] : []),
      ];
      const found = new Map((item.periods || []).map(period => [period.period, period]));
      const basis = comparisonBasis(item, benchmark);
      const itemComplete = item.dividends_included === true, benchmarkComplete = benchmark?.dividends_included === true;
      return {
        code: item.code, name: item.name || item.code, note: basis.label,
        periods: (fund ? FUND_PERIOD_KEYS : COMPARE_PERIODS).map(key => {
          const period = found.get(key) || { status: 'insufficient_history' };
          const [label, short] = PERIOD_NAMES[key];
          const named = { key, label: period.label || label, short };
          if (period.status !== 'ok') return { ...named, kind: 'none', gap: '資料不足', facts: facts('—', '—', '—', '—', '—') };
          const fx = itemComplete ? fxEffect(period.fx_effect_pct) : '—';
          if (!basis.comparable) {
            return { ...named, kind: 'none', gap: '不可比', facts: facts(
              itemComplete ? comparePercent(period.stock_return_pct) : '—', benchmarkComplete ? comparePercent(period.benchmark_return_pct) : '—',
              itemComplete ? compareDrawdown(period.stock_max_drawdown_pct) : '—', benchmarkComplete ? compareDrawdown(period.benchmark_max_drawdown_pct) : '—', fx) };
          }
          return { ...named, kind: period.excess_pct > 0 ? 'bull' : period.excess_pct < 0 ? 'bear' : 'none', gap: comparePercent(period.excess_pct),
            facts: facts(comparePercent(period.stock_return_pct), comparePercent(period.benchmark_return_pct),
              compareDrawdown(period.stock_max_drawdown_pct), compareDrawdown(period.benchmark_max_drawdown_pct), fx) };
        }),
      };
    });
  }

  window.StockComparison = { basis: comparisonBasis, conclusion: comparisonConclusion, periods: comparisonPeriods };

  // 每一檔一排期間的鍵（.key-strip，可以按住左右滑，由 chart-menus.js 處理），下面是選中那個期間的數字
  const GAP_WORDS = { bull: '領先', bear: '落後' };
  // 選的期間這一檔沒有（例如「成立以來」只有基金有）：照樣列名稱、數字都是一槓，不拿別的期間的數字頂替
  function comparePeriodFacts(stock) {
    const period = stock.periods.find(item => item.key === compareState.period)
      || { facts: stock.periods[0].facts.map(([title]) => [title, '—']) };
    return period.facts.map(([title, text]) => {
      const fx = title === '其中匯率影響';
      const item = node('div', fx ? 'cmp-fx' : '');
      const value = node('dd', 'num', fx ? text.replace(/ 個百分點$/, '') : text);
      if (fx && text.endsWith(' 個百分點')) value.append(node('small', '', '個百分點'));
      item.append(node('dt', '', title), value);
      return item;
    });
  }
  function renderComparePeriods() {
    $('comparePeriods').replaceChildren(...compareState.stocks.map(stock => {
      const card = node('article', 'cmp-card');
      card.dataset.code = stock.code;
      const head = node('h3', '', stock.name);
      head.append(node('small', 'num', stock.code));
      if (stock.note) head.append(node('em', '', stock.note));
      const strip = node('div', 'key-strip');
      strip.setAttribute('role', 'group');
      strip.setAttribute('aria-label', `${stock.name}各期間和 0050 的差距`);
      strip.append(...stock.periods.map(period => {
        const cell = node('button', `cell ${period.kind}`);
        cell.type = 'button';
        cell.dataset.period = period.key;
        cell.setAttribute('aria-pressed', String(period.key === compareState.period));
        cell.setAttribute('aria-label', GAP_WORDS[period.kind] ? `${period.label}，${GAP_WORDS[period.kind]} 0050 ${period.gap.replace(/^[+-]/, '')}` : `${period.label}，${period.gap}`);
        const name = node('span', 'cell-name');
        name.append(period.short, node('b', 'num', period.gap));
        cell.append(node('span', 'cell-face'), node('i', 'tag'), name);
        return cell;
      }));
      const facts = node('dl', 'cmp-facts');
      facts.append(...comparePeriodFacts(stock));
      card.append(head, strip, facts);
      return card;
    }));
  }
  // 換一個期間：鍵與數字就地換，不整塊重畫（按住滑過來放開時，選中的框還在滑向那一顆）
  function pickComparePeriod(key) {
    if (!FUND_PERIOD_KEYS.includes(key)) return;
    compareState.period = key;
    $('comparePeriods').querySelectorAll('.cmp-card').forEach(card => {
      const stock = compareState.stocks.find(item => item.code === card.dataset.code);
      card.querySelectorAll('.cell').forEach(cell => cell.setAttribute('aria-pressed', String(cell.dataset.period === key)));
      if (stock) card.querySelector('.cmp-facts').replaceChildren(...comparePeriodFacts(stock));
    });
  }

  // 給螢幕閱讀器的完整表（畫面上看的是那一排期間的鍵）：照同一份整理好的資料畫，一檔五列
  function renderCompareTable() {
    $('compareTableBody').replaceChildren(...compareState.stocks.flatMap(stock => stock.periods.map(period => {
      const row = node('tr');
      row.dataset.code = stock.code;
      const name = node('td', '', stock.note ? `${stock.name} ${stock.code}（${stock.note}）` : `${stock.name} ${stock.code}`);
      const head = node('th', '', period.label);
      head.scope = 'row';
      // 基金的第五個數字（其中匯率影響）併進報酬那一格念出來，表的欄位和股票一樣
      const [stockText, baseText, fallText, baseFallText, fxText] = period.facts.map(([, text]) => text);
      const [stockReturn, baseReturn, stockFall, baseFall] = [fxText && fxText !== '—' ? `${stockText}（其中匯率影響 ${fxText}）` : stockText,
        baseText, fallText, baseFallText].map(text => node('td', '', text));
      row.append(name, head, stockReturn, baseReturn, node('td', '', period.gap), stockFall, baseFall);
      return row;
    })));
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
    // 基金淨值約晚 3 個營業日：基金的數字比到它自己的最後一個淨值日，和資料日不同時寫出來（05 票）
    const fundDates = items.filter(item => item.kind === 'fund' && item.history?.to && item.history.to < payload.data_date)
      .map(item => `${items.length > 1 ? `${item.name} ` : ''}淨值至 ${item.history.to}`);
    $('compareDate').textContent = [`資料日 ${payload.data_date}`, ...fundDates].join('，');
    $('compareConclusion').textContent = comparisonConclusion(items, payload.benchmark);
    compareState.stocks = comparisonPeriods(items, payload.benchmark);
    renderCompareTable();
    renderComparePeriods();
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
    $('comparePeriods').replaceChildren();
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
    compareState.period = '1y';
    compareState.stocks = [];
    const openCat = $('stockCats').querySelector('button[aria-expanded="true"]');
    if (openCat) openCat.click();
    compareColorToken(result.code);
    clearComparisonView();
    $('compareBlock').hidden = false;
    renderCompareChips();
    loadComparison();
  }

  function addComparisonCode(code, name = code) {
    const normalized = code.trim().toUpperCase();
    if (!window.StockPicker.isCode(normalized)) {
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
    comparePicker.close();
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

  // ---------- 畫線 ----------
  const drawToolButtons = () => [...$('drawTools').querySelectorAll('button[data-tool]')];

  function showActiveTool(tool) {
    drawToolButtons().forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tool === tool)));
  }

  // 選取了一條線，工具列才出現刪除。
  function showDrawingSelection(drawing) {
    $('drawDelete').hidden = !drawing;
  }

  // 有變動還沒存上去（存檔失敗、等著補存）時工具列那一行的右邊出現提示（秒 K、分 K 工具列收起時也在），
  // 那幾條線畫得比較淡；都補存上去就消失。
  // 只剩被拒絕、不會再試的（超過上限等）時改說「沒存成」。
  function showUnsavedDrawings({ unsaved, failed, ids }) {
    const hint = $('drawUnsaved');
    hint.hidden = !(unsaved || failed);
    hint.textContent = unsaved ? '未儲存' : failed ? '沒存成' : '';
    if (chart) chart.setUnsavedDrawings(ids);
  }

  // 個股分析畫出來之後才讀；讀不到就當作沒有畫線，不影響其他內容。
  async function loadDrawings(code, sequence) {
    if (!drawingSync) return;
    try {
      const response = await fetch(`/api/drawings/${encodeURIComponent(code)}`, { cache: 'no-store' });
      const data = response.ok ? await response.json() : null;
      if (sequence !== requestSequence || !chart || !Array.isArray(data?.items)) return;
      chart.setDrawings(drawingSync.merge(data.items));
    } catch (_) { /* 畫線是附加的：失敗就維持沒有畫線 */ }
  }

  // 工具列只有圖示：名稱在滑鼠停留與鍵盤聚焦時由樣式顯示，手機則是長按；長按只看名稱，不切換工具、也不刪除。
  function bindDrawTools() {
    // Esc 不管焦點在哪都有效（剛點完工具時焦點在按鈕上，有些瀏覽器則根本不給按鈕焦點）：放下工具、取消選取。
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && chart) chart.cancelDrawing();
    });
    $('drawTools').querySelectorAll('button').forEach(button => {
      let timer = null, held = false;
      const release = () => { clearTimeout(timer); button.classList.remove('is-tip'); };
      button.addEventListener('pointerdown', event => {
        held = false;
        if (event.pointerType === 'mouse') return;
        timer = setTimeout(() => { held = true; button.classList.add('is-tip'); }, 450);
      });
      ['pointerup', 'pointercancel', 'pointerleave'].forEach(type => button.addEventListener(type, release));
      button.addEventListener('contextmenu', event => event.preventDefault());
      // 按工具列不搬走焦點：文字標註寫到一半時再按一次文字工具是「不寫了」，按垃圾桶是刪掉那一則——
      // 焦點要是先被按鈕拿走，輸入框會當成「確定」把寫到一半的存下去。鍵盤（Tab、Enter、空白鍵）不受影響。
      button.addEventListener('mousedown', event => event.preventDefault());
      button.addEventListener('click', () => {
        if (held) { held = false; return; }
        if (!chart) return;
        if (button.dataset.tool) chart.setTool(button.getAttribute('aria-pressed') === 'true' ? null : button.dataset.tool);
        else if (button.id === 'drawDelete' && chart.deleteSelectedDrawing()) {
          chart.canvas.focus({ preventScroll: true }); // 刪除鈕跟著收起：焦點交還圖表，鍵盤操作不中斷
        }
      });
    });
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
    // 換股票：上一檔的畫線、選取與選到一半的工具都放下（還沒存上去的照樣在背景補存到原本那一檔）；
    // 重新載入同一檔時，這次畫過、拖過的先留著。
    const drawn = drawingSync ? drawingSync.open(code) : [];
    if (chart) { chart.setTool(null); chart.setDrawings(drawn); }
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
      loadDrawings(data.code, sequence);
      // 自選股關注的判斷（watch-judgments.js）：只有站主、開關打開而且這一檔有判斷時才加小鍵與兩條線。
      if (features.watchlist_watch_v1 && window.WatchJudgments && !isFund(data)) {
        window.WatchJudgments.mount({ host: document.querySelector('.bd-chart-bar'), code: data.code,
          onChange: () => { if (lastResult) drawChart(lastResult); }, getDaily: () => (baseResult ? dailyCandles(baseResult) : []) });
      }
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

  $('comparePeriods').addEventListener('click', event => {
    const cell = event.target.closest('.cell[data-period]');
    if (cell) pickComparePeriod(cell.dataset.period);
  });
  // 加入比較與頂列都用全站共用的選股下拉（stock-picker.js，本人 11-17：代號、名稱都能打）。
  // 比較只收大量股母體內、名單 ETF 與基金裡還沒加入的；頂列什麼都收，基金也是進個股頁。
  const comparePicker = window.StockPicker.attach($('compareInput'), {
    // 基金沒有名次，但比較資料包有它（05 票）
    accept: item => (item.security_type === '基金' || (item.universe_rank !== null && item.universe_rank !== undefined))
      && !compareState.codes.includes(item.code),
    onPick: option => addComparisonCode(option.code, option.name),
  });
  $('compareForm').addEventListener('submit', async event => {
    event.preventDefault();
    const { option, message } = await comparePicker.choose();
    if (message) { setCompareStatus('error', message); return; }
    addComparisonCode(option.code, option.name);
  });

  const openFromBar = option => {
    updateUrl(option.code);
    load(option.code);
  };
  const barPicker = window.StockPicker.attach($('codeInput'), { onPick: openFromBar });
  $('searchForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!$('codeInput').value.trim()) return;
    const { option, message } = await barPicker.choose();
    if (message) { $('status').hidden = false; $('status').textContent = message; return; }
    openFromBar(option);
  });
  $('periodSelect').addEventListener('change', () => {
    const selected = $('periodSelect').value;
    chartPeriod = ['1s', '5s', '10s', '15s', '30s', '1m', '5m', '15m', '60m', 'weekly', 'monthly'].includes(selected) ? selected : 'daily';
    if (lastResult) drawChart(lastResult);
    syncTickFeed();
  });
  window.addEventListener('popstate', () => load(codeFromUrl() || '2330'));
  // 還有畫線沒存上去時瀏覽器會先問要不要離開（drawing-sync.js）：使用者可能留下來，所以這時不停即時更新。
  window.addEventListener('beforeunload', event => { if (!event.defaultPrevented) { livePoller.stop(); stopTicks(); } });
  document.addEventListener('visibilitychange', syncTickFeed);
  $('addMa').addEventListener('click', () => {
    if (maPeriods.length >= MA_LIMIT) return;
    // 從常用的期數裡找第一個還沒用到的（最多四條，六個候選一定找得到），不會加出重複而被併掉
    const next = [60, 120, 240, 20, 10, 5].find(value => !maPeriods.includes(value));
    applyMaPeriods([...maPeriods, next]);
  });
  // 主題在設定浮板裡切換（settings.js，本人 11-17 選樣品 A）：已經存好、套好了，這裡跟著重畫圖
  document.addEventListener('market:theme', event => setTheme(event.detail?.theme === 'dark' ? 'dark' : 'light'));
  let saved = null;
  try { saved = localStorage.getItem('market-theme'); } catch (_) { /* 私密視窗 */ }
  renderMaControls();
  syncMaLimit();
  bindDrawTools();
  setTheme(saved === 'dark' ? 'dark' : 'light');
  // chart：驗收用（畫線旅程讀選了哪一根 K 棒、右側標了什麼），頁面自己不用
  window.StockView = { weeklyCandles, monthlyCandles, chart: () => chart };
  loadReleaseFeatures().then(() => load(codeFromUrl() || '2330'));
})();
