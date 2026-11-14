(() => {
  'use strict';

  function normalizeAiDigest(payload) {
    const byCode = new Map();
    for (const item of payload?.stocks?.items || []) {
      const code = typeof item?.code === 'string' ? item.code.trim().toUpperCase() : '';
      if (!code) continue;
      if (item.generation_status !== 'generated') {
        byCode.set(code, { status: 'unproduced', text: '今日未產生' });
        continue;
      }
      const parts = [item.status, item.action && item.reason ? `${item.action}：${item.reason}` : item.action || item.reason]
        .filter(value => typeof value === 'string' && value.trim());
      byCode.set(code, { status: 'generated', text: parts.join(' · ') || '今日未產生' });
    }
    return {
      dataDate: typeof payload?.data_date === 'string' ? payload.data_date : null,
      status: payload?.stocks?.status || 'unproduced',
      byCode,
    };
  }

  function aiEntryFor(digest, code) {
    return digest?.byCode?.get(String(code).toUpperCase()) || { status: 'unproduced', text: '今日未產生' };
  }

  if (typeof window !== 'undefined') window.PortfolioAi = Object.freeze({ normalize: normalizeAiDigest, entryFor: aiEntryFor });
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const money = new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD', maximumFractionDigits: 0 });
  const number = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 });
  const state = { watchlist: [], holdings: [], level: null, aiDigest: null, aiVisible: false };
  let features = { level_advice: false, ai_stock_advice: false };

  async function loadReleaseFeatures() {
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const payload = response.ok ? await response.json() : null;
      if (payload?.schema === 'market-ana-features/v1' && payload.features) features = payload.features;
    } catch (_) { /* feature gate 讀取失敗時維持 default-off */ }
    $('levelPanel').hidden = !features.level_advice;
  }

  function setThemeLabel() {
    const dark = getComputedStyle(document.documentElement).colorScheme.includes('dark');
    $('portfolioTheme').setAttribute('aria-label', dark ? '改用淺色主題' : '改用深色主題');
  }
  $('portfolioTheme').addEventListener('click', () => {
    const root = document.documentElement;
    const dark = getComputedStyle(root).colorScheme.includes('dark');
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('market-theme', root.dataset.theme); } catch (_) {}
    setThemeLabel();
  });
  function readSavedTheme() {
    try {
      const saved = localStorage.getItem('market-theme');
      if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
    } catch (_) {}
    setThemeLabel();
  }
  readSavedTheme();
  // 按瀏覽器「上一頁」回來時頁面是整頁還原的，要再讀一次存起來的主題（可能在別頁改過）
  window.addEventListener('pageshow', event => { if (event.persisted) readSavedTheme(); });

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      headers: options.body ? { 'content-type': 'application/json' } : undefined,
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.message || payload.error || '暫時無法處理');
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setStatus(node, message, kind = '') {
    node.textContent = message;
    if (kind) node.dataset.state = kind;
    else node.removeAttribute('data-state');
  }

  function stockAiDigest(code) {
    if (!state.aiVisible) return null;
    const entry = aiEntryFor(state.aiDigest, code);
    const host = document.createElement('div');
    host.className = `pf-ai${entry.status === 'generated' ? '' : ' pf-ai-empty'}`;
    const label = document.createElement('span');
    label.textContent = state.aiDigest?.dataDate ? `AI 整理 · ${state.aiDigest.dataDate}` : 'AI 整理';
    const text = document.createElement('p');
    text.textContent = entry.text;
    host.append(label, text);
    return host;
  }

  function stockIdentity(item) {
    const host = document.createElement('div');
    host.className = 'pf-stock';
    const link = document.createElement('a');
    link.href = item.in_universe ? `stock.html?code=${encodeURIComponent(item.code)}` : '#';
    link.textContent = `${item.code} ${item.name}`;
    if (!item.in_universe) {
      link.removeAttribute('href');
      link.setAttribute('aria-disabled', 'true');
    }
    const meta = document.createElement('small');
    meta.textContent = item.market;
    const scope = document.createElement('span');
    scope.className = `pf-scope${item.in_universe ? '' : ' outside'}`;
    scope.textContent = item.in_universe ? '可開啟本站完整分析' : '不在本站分析範圍';
    host.append(link, meta, scope);
    const digest = stockAiDigest(item.code);
    if (digest) host.append(digest);
    return host;
  }

  function deleteButton(label, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pf-delete';
    button.textContent = '刪除';
    button.setAttribute('aria-label', label);
    button.addEventListener('click', action);
    return button;
  }

  function renderWatchlist() {
    const host = $('watchlistRows');
    host.replaceChildren();
    $('watchlistCount').textContent = `${state.watchlist.length} 檔`;
    if (!state.watchlist.length) {
      const empty = document.createElement('p');
      empty.className = 'pf-empty';
      empty.textContent = '還沒有自選股。輸入代號後，可標記為短期、長期或先不分類。';
      host.append(empty);
      return;
    }
    state.watchlist.forEach(item => {
      const row = document.createElement('article');
      row.className = 'pf-row pf-watch-row';
      const select = document.createElement('select');
      select.setAttribute('aria-label', `${item.code} 投資分類`);
      [['none', '未分類'], ['short', '短期'], ['long', '長期']].forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        option.selected = item.horizon === value;
        select.append(option);
      });
      select.addEventListener('change', async () => {
        select.disabled = true;
        try {
          await api('/api/watchlist', { method: 'POST', body: { code: item.code, horizon: select.value } });
          setStatus($('watchlistStatus'), `${item.code} 分類已更新。`, 'success');
          await loadWatchlist();
        } catch (error) {
          setStatus($('watchlistStatus'), error.message, 'error');
          select.value = item.horizon;
        } finally { select.disabled = false; }
      });
      row.append(stockIdentity(item), select, deleteButton(`從自選股刪除 ${item.code} ${item.name}`, async () => {
        if (!confirm(`要從自選股刪除 ${item.code} ${item.name} 嗎？`)) return;
        try {
          await api(`/api/watchlist/${encodeURIComponent(item.code)}`, { method: 'DELETE', body: {} });
          setStatus($('watchlistStatus'), `${item.code} 已從自選股刪除。`, 'success');
          await loadWatchlist();
        } catch (error) { setStatus($('watchlistStatus'), error.message, 'error'); }
      }));
      host.append(row);
    });
  }

  function valueCell(label, value, tone = '') {
    const cell = document.createElement('div');
    cell.className = 'pf-cell';
    const strong = document.createElement('strong');
    strong.textContent = value;
    if (tone) strong.className = tone;
    const small = document.createElement('small');
    small.textContent = label;
    cell.append(strong, small);
    return cell;
  }

  const groupLabels = {
    trend: '台股與美股趨勢',
    volatility: '市場波動',
    warning: '跌前預警',
    taiwan_funding: '台灣資金面',
  };
  const signalLabels = {
    taiex_trend: '加權指數趨勢', sp500_trend: 'S&P 500 趨勢', vix: 'VIX',
    taiex_volatility: '台股 20 日波動', breadth: '上市股票強弱', sox_5d: '費城半導體 5 日跌幅',
    credit_stress: 'OFR FSI Credit 壓力變化', foreign_flow: '外資 5 日累計', twd_depreciation: '新台幣 20 日貶值',
  };

  function signalValueText(signal) {
    if (signal.unavailable) return '未接，固定 0 分';
    if (!signal.available) return '缺資料';
    if (signal.key === 'taiex_trend' || signal.key === 'sp500_trend') {
      return `收盤 ${number.format(signal.raw.close)}，200 日線 ${number.format(signal.raw.ma200)}`;
    }
    if (signal.key === 'breadth') {
      return `20 日下降 ${number.format(signal.raw.drop_pp_20d)} 個百分點，指數${signal.raw.taiex_above_ma200 ? '仍在' : '已跌破'} 200 日線`;
    }
    return typeof signal.raw === 'number' ? `近三年分位 ${number.format(signal.raw * 100)}%` : String(signal.raw);
  }

  function appendLevelPoint(text) {
    const item = document.createElement('li');
    item.textContent = text;
    $('levelPoints').append(item);
  }

  function renderLevel() {
    const payload = state.level;
    $('levelPoints').replaceChildren();
    $('levelBreakdown').replaceChildren();
    if (!payload) return;
    const { current, advice, adjustment } = payload;
    $('cashAmount').value = current.cash_twd === null ? '' : String(current.cash_twd);
    $('cashMeta').textContent = current.cash_twd === null
      ? '尚未申報，不含緊急預備金'
      : `${current.cash_stale ? '現金可能過期 · ' : ''}更新於 ${new Date(current.cash_updated_at).toLocaleString('zh-TW')}`;
    $('currentLevel').textContent = current.level === null ? '—' : `${number.format(current.level)}%`;
    if (!advice) {
      $('recommendedLevel').textContent = '—';
      $('levelGap').textContent = '—';
      $('levelAmount').textContent = '—';
      $('levelDate').textContent = '今日建議尚未產生';
      $('levelStatus').textContent = payload.warning || '等待資料';
      $('levelStatus').dataset.state = 'warning';
      $('levelConclusion').textContent = current.complete ? '目前水位已算好，等待今天的大盤建議。' : '先補齊現金與庫存價格，才能算出完整水位。';
      return;
    }
    $('levelDate').textContent = `資料日 ${advice.data_date}`;
    $('recommendedLevel').textContent = advice.recommended_level === null ? '—' : `${number.format(advice.recommended_level)}%`;
    $('levelStatus').textContent = advice.warning || (advice.missing_signals?.length ? `少看 ${advice.missing_signals.length} 項資料` : '資料已到齊');
    $('levelStatus').dataset.state = advice.warning || advice.missing_signals?.length ? 'warning' : 'ok';
    if (!adjustment) {
      $('levelGap').textContent = '—';
      $('levelAmount').textContent = '—';
      $('levelConclusion').textContent = current.warning || '目前資料不完整，今天先不要依部分數字調整部位。';
      if (current.total_assets !== null) appendLevelPoint(`總資金 ${money.format(current.total_assets)}（股票市值 ${money.format(current.market_value)}＋計入現金 ${money.format(current.cash_twd)}）`);
      else appendLevelPoint(`目前可估股票市值 ${money.format(current.market_value)}；總資金因下列缺口尚無法完整計算`);
      if (current.missing_price_codes.length) appendLevelPoint(`缺少收盤價：${current.missing_price_codes.join('、')}`);
      if (current.cash_twd === null) appendLevelPoint('尚未申報要計入總資金的現金');
    } else {
      const gap = adjustment.percentage_points;
      $('levelGap').textContent = `${gap > 0 ? '+' : ''}${number.format(gap)} 個百分點`;
      $('levelAmount').textContent = adjustment.within_tolerance ? '不用動' : money.format(adjustment.amount_twd);
      $('levelConclusion').textContent = adjustment.within_tolerance
        ? '目前水位在 5 個百分點容忍帶內，今天不用動。'
        : gap > 0
          ? `目前股票水位偏低，可用現金增加約 ${money.format(adjustment.amount_twd)}。`
          : `目前股票水位偏高，由您決定減碼哪些持股，金額約 ${money.format(adjustment.amount_twd)}。`;
      appendLevelPoint(`總資金 ${money.format(current.total_assets)}（股票市值 ${money.format(current.market_value)}＋計入現金 ${money.format(current.cash_twd)}）`);
      if (!adjustment.within_tolerance) appendLevelPoint(`估計手續費 ${money.format(adjustment.estimated_commission_twd)}${adjustment.estimated_tax_twd ? `，證交稅 ${money.format(adjustment.estimated_tax_twd)}` : ''}`);
    }
    if (current.cash_warning) appendLevelPoint(current.cash_warning);
    (advice.missing_signals || []).forEach(key => {
      const source = advice.source_status?.[key];
      appendLevelPoint(`${source?.label || key}今天缺資料${source?.reason ? `：${source.reason}` : ''}`);
    });
    (advice.unavailable_signals || []).forEach(key => {
      const source = advice.source_status?.[key];
      appendLevelPoint(`${source?.label || key}未接，今天固定 0 分${source?.reason ? `：${source.reason}` : ''}`);
    });
    appendLevelPoint('台股趨勢組為 0 分時，其他訊號不會單獨把建議降到 80% 以下');
    const score = advice.score || { groups: {} };
    const grid = document.createElement('div');
    grid.className = 'pf-score-grid';
    Object.entries(groupLabels).forEach(([key, label]) => {
      const section = document.createElement('section');
      const title = document.createElement('h3');
      title.textContent = label;
      const value = document.createElement('strong');
      value.textContent = `${score.groups?.[key] ?? 0} 分`;
      const raw = (score.signals || []).filter(signal => signal.group === key)
        .map(signal => `${signalLabels[signal.key] || signal.key}：${signalValueText(signal)}；門檻 ${signal.threshold}`).join('；');
      const note = document.createElement('p');
      note.textContent = raw || '今天沒有可列的明細';
      section.append(title, value, note);
      grid.append(section);
    });
    $('levelBreakdown').append(grid);
    const progress = document.createElement('p');
    progress.className = 'pf-level-warning';
    const confirmation = advice.confirmation || {};
    const wave = advice.wave || {};
    const direction = confirmation.direction === 'down' ? '降水位' : confirmation.direction === 'up' ? '升水位' : '沒有待確認的升降級';
    let waveText = '目前不在大跌加碼波段。';
    if (wave.active) {
      const next = (wave.used_tranches || []).length + 1;
      const threshold = [null, 15, 25, 35][next];
      const missing = [];
      if (threshold && (wave.drawdown || 0) * 100 < threshold) missing.push(`跌幅達 ${threshold}%`);
      if (!wave.vix_retreated) missing.push('VIX 從近 10 日高點回落至少 20%');
      if (!wave.stable_three_days) missing.push(`台股連續 3 日不破底（目前 ${wave.no_new_low_days || 0} 日）`);
      waveText = `本波從 ${number.format(wave.reference_high)} 點計，跌幅 ${number.format((wave.drawdown || 0) * 100)}%，已用第 ${(wave.used_tranches || []).join('、') || '零'} 批。` +
        (next <= 3 ? `第 ${next} 批${missing.length ? `還差：${missing.join('、')}` : '條件已到齊'}。` : '三批已用完，本波沒有後手。');
    }
    progress.textContent = `${direction}${confirmation.required ? `：${confirmation.days}/${confirmation.required} 個交易日` : ''}。${waveText}` +
      `${advice.lock?.remaining_days ? ` 加碼後降級限制還有 ${advice.lock.remaining_days} 個交易日。` : ''}`;
    $('levelBreakdown').append(progress);
    const monitoring = advice.online_monitoring;
    if (monitoring?.status === 'insufficient-history') {
      const note = document.createElement('p');
      note.className = 'pf-level-warning';
      note.textContent = '上線追蹤時間尚不足，還不能判斷一年期表現。歷史建議仍會持續留存。';
      $('levelBreakdown').append(note);
    }
    if (monitoring?.display_disabled) {
      $('levelConclusion').textContent = `建議已停用${monitoring.disabled_at ? `（${monitoring.disabled_at}）` : ''}；背景仍持續計算與留存。`;
    }
  }

  async function loadLevel() {
    state.level = await api('/api/holdings/level');
    renderLevel();
  }

  function renderSummary() {
    const priced = state.holdings.filter(item => Number.isFinite(item.market_value));
    const marketValue = priced.reduce((sum, item) => sum + item.market_value, 0);
    const cost = state.holdings.reduce((sum, item) => sum + item.cost_basis, 0);
    const pnl = priced.length === state.holdings.length ? marketValue - cost : null;
    const returnPct = pnl === null || !cost ? null : pnl / cost * 100;
    $('holdingCount').textContent = `${state.holdings.length} 檔庫存`;
    $('totalMarketValue').textContent = state.holdings.length && priced.length === state.holdings.length ? money.format(marketValue) : '—';
    $('totalCost').textContent = state.holdings.length ? money.format(cost) : '—';
    $('totalPnl').textContent = pnl === null ? '—' : `${pnl >= 0 ? '+' : '−'}${money.format(Math.abs(pnl))}`;
    $('totalReturn').textContent = returnPct === null ? '—' : `${returnPct >= 0 ? '+' : ''}${number.format(returnPct)}%`;
    for (const id of ['totalPnl', 'totalReturn']) {
      $(id).className = `num${pnl === null ? '' : pnl >= 0 ? ' up' : ' down'}`;
    }
  }

  function renderHoldings() {
    const host = $('holdingRows');
    host.replaceChildren();
    $('holdingsCount').textContent = `${state.holdings.length} 檔`;
    renderSummary();
    if (!state.holdings.length) {
      const empty = document.createElement('p');
      empty.className = 'pf-empty';
      empty.textContent = '還沒有庫存。先輸入代號、股數與平均成本，之後可用同一表單更新。';
      host.append(empty);
      return;
    }
    state.holdings.forEach(item => {
      const row = document.createElement('article');
      row.className = 'pf-row pf-holding-row';
      const price = item.latest_price === null ? '—' : number.format(item.latest_price);
      const marketValue = item.market_value === null ? '—' : money.format(item.market_value);
      const pnl = item.unrealized_pnl;
      const pnlTone = pnl === null ? '' : pnl >= 0 ? 'up' : 'down';
      const pnlText = pnl === null ? '—' : `${pnl >= 0 ? '+' : '−'}${money.format(Math.abs(pnl))}`;
      const returnText = item.return_pct === null ? '—' : `${item.return_pct >= 0 ? '+' : ''}${number.format(item.return_pct)}%`;
      const note = document.createElement('div');
      note.className = 'pf-cell pf-note';
      const strong = document.createElement('strong');
      strong.textContent = item.note || '無備註';
      const small = document.createElement('small');
      small.textContent = `${number.format(item.shares)} 股 · 均價 ${number.format(item.average_cost)}`;
      note.append(strong, small);
      row.append(
        stockIdentity(item),
        valueCell(item.price_stale ? '上次可用收盤' : '最新收盤', price),
        valueCell('目前市值', marketValue),
        valueCell('未實現損益', pnlText, pnlTone),
        valueCell('報酬率', returnText, pnlTone),
        note,
        deleteButton(`刪除庫存 ${item.code} ${item.name}`, async () => {
          if (!confirm(`要刪除 ${item.code} ${item.name} 的庫存資料嗎？`)) return;
          try {
            await api(`/api/holdings/${encodeURIComponent(item.code)}`, { method: 'DELETE', body: {} });
            setStatus($('holdingStatus'), `${item.code} 庫存已刪除。`, 'success');
            await Promise.all([loadHoldings(), ...(features.level_advice ? [loadLevel()] : [])]);
          } catch (error) { setStatus($('holdingStatus'), error.message, 'error'); }
        }),
      );
      row.querySelectorAll('.pf-cell')[1]?.classList.add('pf-secondary');
      row.querySelectorAll('.pf-cell')[2]?.classList.add('pf-secondary');
      host.append(row);
    });
  }

  async function loadWatchlist() {
    const payload = await api('/api/watchlist');
    state.watchlist = payload.items || [];
    renderWatchlist();
  }
  async function loadHoldings() {
    const payload = await api('/api/holdings');
    state.holdings = payload.items || [];
    renderHoldings();
  }

  async function loadAiDigest() {
    try {
      state.aiDigest = normalizeAiDigest(await api('/api/ai-digest?scope=portfolio'));
      state.aiVisible = true;
    } catch (error) {
      if (error.status === 403) {
        state.aiDigest = null;
        state.aiVisible = false;
        return;
      }
      state.aiDigest = normalizeAiDigest(null);
      state.aiVisible = true;
    }
  }

  $('watchlistForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    setStatus($('watchlistStatus'), '正在儲存…');
    try {
      const code = $('watchCode').value.trim().toUpperCase();
      await api('/api/watchlist', { method: 'POST', body: { code, horizon: $('watchHorizon').value } });
      $('watchCode').value = '';
      setStatus($('watchlistStatus'), `${code} 已加入自選股。`, 'success');
      await loadWatchlist();
    } catch (error) { setStatus($('watchlistStatus'), error.message, 'error'); }
    finally { button.disabled = false; }
  });

  $('holdingForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    setStatus($('holdingStatus'), '正在儲存…');
    try {
      const code = $('holdingCode').value.trim().toUpperCase();
      await api('/api/holdings', { method: 'POST', body: {
        code,
        shares: Number($('holdingShares').value),
        average_cost: Number($('holdingCost').value),
        note: $('holdingNote').value.trim(),
      } });
      form.reset();
      setStatus($('holdingStatus'), `${code} 庫存已儲存。`, 'success');
      await Promise.all([loadHoldings(), ...(features.level_advice ? [loadLevel()] : [])]);
    } catch (error) { setStatus($('holdingStatus'), error.message, 'error'); }
    finally { button.disabled = false; }
  });

  $('cashForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    $('cashMeta').textContent = '正在更新…';
    try {
      const cash = await api('/api/holdings/cash', { method: 'POST', body: { amount_twd: Number($('cashAmount').value) } });
      $('cashMeta').textContent = `已更新 ${new Date(cash.updated_at).toLocaleString('zh-TW')} · 第 ${cash.version} 版`;
      await loadLevel();
    } catch (error) {
      $('cashMeta').textContent = error.message;
    } finally { button.disabled = false; }
  });

  loadReleaseFeatures().then(async () => {
    await Promise.all([loadWatchlist(), loadHoldings(), ...(features.level_advice ? [loadLevel()] : [])]);
    if (features.ai_stock_advice) await loadAiDigest();
    renderWatchlist();
    renderHoldings();
    const dates = state.holdings.map(item => item.price_as_of).filter(Boolean).sort();
    const stale = state.holdings.some(item => item.price_stale);
    $('portfolioFreshness').textContent = dates.length
      ? `庫存價格至 ${dates.at(-1)}${stale ? '（部分沿用上次資料）' : ''}`
      : '尚無庫存價格';
  }).catch(error => {
    $('portfolioFreshness').textContent = '資料讀取失敗';
    setStatus($('holdingStatus'), error.message, 'error');
  });
})();
