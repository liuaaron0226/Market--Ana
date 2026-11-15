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
  // watch：站主才有（其他人是 null）——已關注的代號、目前幾檔、上限。
  const state = { watchlist: [], watch: null, holdings: [], level: null, aiDigest: null, aiVisible: false };
  let features = { level_advice: false, ai_stock_advice: false, watchlist_watch_v1: false };

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

  function deleteButton(text, label, action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pf-delete';
    button.textContent = text;
    button.setAttribute('aria-label', label);
    button.addEventListener('click', action);
    return button;
  }

  // 關注幾檔／上限：寫在每一顆關注開關旁邊，開關一動全部一起改
  function renderWatchCount() {
    document.querySelectorAll('.pf-watch-count').forEach(node => { node.textContent = `${state.watch.count}／${state.watch.limit}`; });
  }

  // 每一列的訊息區在畫出這一列時就放好（空的），之後只改文字：輔助工具才會念出來。
  function rowNote(row, message) {
    const note = row.querySelector('.pf-row-note');
    note.textContent = message;
    note.hidden = !message;
  }

  function watchToggle(item, row) {
    const watch = state.watch; // 請求途中清單可能重新載入；這一顆開關只動它畫出來時的那一份
    const label = document.createElement('label');
    label.className = 'pf-watch-toggle';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('role', 'switch');
    input.setAttribute('aria-label', `關注 ${item.code} ${item.name}`);
    input.checked = watch.codes.has(item.code);
    // 現在不能關注的那幾檔只是變淡、仍然可以按：按了由伺服器說明原因，顯示在這一列。
    const dim = () => label.classList.toggle('is-dim', watch.unwatchable.has(item.code) && !input.checked);
    dim();
    const text = document.createElement('span');
    text.textContent = '關注';
    label.append(input, text);
    input.addEventListener('change', async () => {
      const wanted = input.checked;
      input.disabled = true;
      rowNote(row, '');
      try {
        const path = `/api/watch/${encodeURIComponent(item.code)}`;
        if (wanted) {
          const result = await api(path, { method: 'POST', body: {} });
          if (!result?.watching) throw new Error('暫時無法處理');
          watch.codes.add(item.code);
        } else {
          await api(path, { method: 'DELETE', body: {} });
          watch.codes.delete(item.code);
        }
        watch.count = watch.codes.size;
        renderWatchCount();
      } catch (error) {
        input.checked = !wanted;
        rowNote(row, error.message);
      } finally {
        input.disabled = false;
        dim();
      }
    });
    return label;
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

  // ---------- 庫存總覽：一列一檔（立體重設計，本人 2026-11-15 定案的 ③）----------
  // 算法在 holdings-rows.js（總數、排序、橫條的兩段、每一檔與全部的賺賠）；這裡只畫，不另外算。
  // 每一檔的明細與刪除都在點開的那一列裡，頁面下面只留新增或更新的表單。只呈現現況，不說要調整哪一檔。
  const STILL = window.matchMedia('(prefers-reduced-motion: reduce)');
  const HOLDING_FIRST = 150, HOLDING_GAP = 300, HOLDING_PUSH = 3;   // 進場：第一檔什麼時候開始、一檔比一檔晚多少（毫秒）、被頂或壓一下的幅度（px）
  const GAIN_WORDS = { bull: '賺', bear: '賠', flat: '打平', none: '損益' };
  const GAIN_TONES = { bull: 'up', bear: 'down' };
  const signedMoney = value => `${value > 0 ? '+' : value < 0 ? '−' : ''}${money.format(Math.abs(value))}`;
  const signedPercent = value => `${value > 0 ? '+' : value < 0 ? '−' : ''}${number.format(Math.abs(value))}%`;
  const returnText = item => (Number.isFinite(item.return_pct) ? signedPercent(item.return_pct) : '—');
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  // ---------- 自選與庫存：一份清單（本人 2026-11-16 選「併成一份清單」） ----------
  // 一檔只出現一次：有庫存的那幾檔是橫條與賺賠，只在自選的寫分類。點那一檔才看金額、自選的設定與刪除。
  // 資料整理在 portfolio-list.js（併清單）與 holdings-rows.js（庫存的總數與橫條），這裡只管畫。
  const HORIZONS = [['none', '未分類'], ['short', '短期'], ['long', '長期']];
  const HORIZON_WORDS = Object.fromEntries(HORIZONS);
  const EMPTY_WORDS = {
    all: '還沒有自選或庫存。按右邊的「＋」，加入想追蹤的股票或輸入庫存。',
    holding: '還沒有庫存。按右邊的「＋」選「新增庫存」，輸入代號、股數與平均成本。',
    watch: '還沒有自選股。按右邊的「＋」選「加入自選」，可標記為短期、長期或先不分類。',
  };
  let listReady = false;     // 第一次要等庫存、自選與 AI 整理都讀到了才畫：進場只播一次，中途重畫會把它打斷
  let listShown = null;      // 畫面上現在是哪一份資料，同一份不重畫
  let listEntered = false;   // 進場的動態播過了沒
  let listFilter = 'all';    // 三顆篩選鍵選的是哪一顆
  const setListStatus = (message, kind) => setStatus($('listStatus'), message, kind);

  function renderTotals(summary) {
    $('totalMarketValue').textContent = summary.complete ? money.format(summary.value) : '—';
    $('totalCost').textContent = summary.count ? money.format(summary.cost) : '—';
    $('totalWord').textContent = GAIN_WORDS[summary.state];
    $('totalPnl').textContent = summary.pnl === null ? '—' : signedMoney(summary.pnl);
    $('totalReturn').textContent = summary.returnPct === null ? '—' : signedPercent(summary.returnPct);
    for (const id of ['totalPnl', 'totalReturn']) $(id).className = `num ${GAIN_TONES[summary.state] || ''}`.trim();
  }

  // 刪掉一列之後：焦點放到補上來的那一列（沒有了就放到「＋」），不要掉回頁面最上面
  function focusAfterRemoval(at) {
    const tops = [...$('holdingRows').querySelectorAll('.hv-row:not([hidden]) .hv-top')];
    (tops[Math.min(at, tops.length - 1)] || $('addToggle')).focus();
  }
  const visibleIndex = row => [...$('holdingRows').querySelectorAll('.hv-row:not([hidden])')].indexOf(row);

  // 點開之後的自選設定：分類三選一（按了就存）、關注開關（站主才有）
  function watchSettings(entry, li) {
    const watch = entry.watch, host = element('div', 'pf-set');
    const strip = element('div', 'key-strip pf-horizon');
    strip.setAttribute('role', 'group');
    strip.setAttribute('aria-label', `${entry.code} 自選分類`);
    HORIZONS.forEach(([value, label]) => {
      const cell = element('button', 'cell');
      cell.type = 'button';
      cell.dataset.horizon = value;
      cell.setAttribute('aria-pressed', String(watch.horizon === value));
      cell.append(element('span', 'cell-face'), element('span', 'cell-name', label));
      strip.append(cell);
    });
    host.append(element('span', 'pf-set-name', '自選分類'), strip);
    if (state.watch) {
      const toggle = watchToggle(watch, li);
      toggle.append(element('small', 'pf-watch-count num', `${state.watch.count}／${state.watch.limit}`));
      host.append(toggle);
    }
    return host;
  }

  async function changeHorizon(cell) {
    if (cell.getAttribute('aria-pressed') === 'true') return;
    const row = cell.closest('.hv-row'), strip = cell.parentElement, code = row.dataset.code;
    const before = strip.querySelector('[aria-pressed="true"]');
    const press = chosen => strip.querySelectorAll('.cell').forEach(other => other.setAttribute('aria-pressed', String(other === chosen)));
    press(cell);
    strip.inert = true;
    rowNote(row, '');
    try {
      await api('/api/watchlist', { method: 'POST', body: { code, horizon: cell.dataset.horizon } });
      setListStatus(`${code} 分類已更新。`, 'success');
      await loadWatchlist();
    } catch (error) {
      press(before);
      rowNote(row, error.message);
    } finally { strip.inert = false; }
  }

  function listRow(entry, order, complete) {
    const held = entry.holding, watch = entry.watch, item = held ? held.item : watch;
    const priced = Boolean(held) && held.state !== 'none', word = held ? GAIN_WORDS[held.state] : '', tone = held ? GAIN_TONES[held.state] || '' : '';
    const li = element('li', `hv-row depth-row on-page${held ? '' : ' pf-only-watch'}`);
    li.dataset.code = entry.code;
    li.dataset.kinds = entry.kinds.join(' ');
    li.dataset.state = held ? held.state : 'none';
    li.style.setProperty('--at', `${HOLDING_FIRST + order * HOLDING_GAP}ms`);
    const panelId = `holdingPanel-${entry.code}`;

    const top = element('button', 'hv-top');
    top.type = 'button';
    top.setAttribute('aria-expanded', 'false');
    top.setAttribute('aria-controls', panelId);
    const said = !held ? `自選 ${HORIZON_WORDS[watch.horizon]}`
      : priced ? `${complete ? `占庫存 ${held.share.toFixed(1)}%，` : ''}${word}${held.gain && Number.isFinite(item.return_pct) ? ` ${signedPercent(item.return_pct)}` : ''}${watch ? `，自選 ${HORIZON_WORDS[watch.horizon]}` : ''}`
        : `沒有價格${watch ? `，自選 ${HORIZON_WORDS[watch.horizon]}` : ''}`;
    top.setAttribute('aria-label', `${entry.name} ${entry.code}：${said}`);
    const name = element('span', 'hv-name'), under = element('span', '', entry.code);
    if (held && watch) under.append(element('em', '', `自選・${HORIZON_WORDS[watch.horizon]}`));
    name.append(element('strong', '', entry.name), under);
    const chev = element('span', 'chev');
    chev.setAttribute('aria-hidden', 'true');
    if (held) {
      const bar = element('span', 'hv-bar');
      bar.setAttribute('aria-hidden', 'true');
      if (priced) {
        const base = element('i', 'base');
        base.style.setProperty('--w', held.base.toFixed(2));
        bar.append(base);
        if (held.edgeKind) {
          const edge = element('i', held.edgeKind);
          edge.style.setProperty('--w', held.edge.toFixed(2));
          bar.append(edge);
        }
      }
      top.append(name, bar, element('span', `hv-ret num ${tone}`.trim(), priced ? returnText(item) : '—'), chev);
    } else top.append(name, element('span', 'pf-tag', HORIZON_WORDS[watch.horizon]), chev);

    const panel = element('div', 'hv-panel depth-panel');
    panel.id = panelId;
    panel.inert = true;
    const clip = element('div', 'hv-clip'), inner = element('div', 'hv-inner');
    if (held) {
      const facts = element('ul', 'hv-facts');
      [
        ['市值', priced ? money.format(item.market_value) : '—', ''],
        ['成本', money.format(item.cost_basis), ''],
        [word, priced ? signedMoney(held.gain) : '—', tone],
        ['占庫存', priced && complete ? `${held.share.toFixed(1)}%` : '—', ''],
        [item.price_stale ? '上次可用收盤' : '最新收盤', item.latest_price === null ? '—' : number.format(item.latest_price), ''],
        ['股數・均價', `${number.format(item.shares)} 股 · ${number.format(item.average_cost)}`, ''],
      ].forEach(([label, text, factTone], index) => {
        const fact = element('li');
        fact.style.setProperty('--order', String(index));   // 展開時一格一格浮出來
        fact.append(element('span', '', label), element('b', factTone, text));
        facts.append(fact);
      });
      inner.append(facts);
      if (item.note) inner.append(element('p', 'hv-note', `備註：${item.note}`));
    }
    if (watch) inner.append(watchSettings(entry, li));
    // 這一列的訊息區在畫出來時就放好（空的），之後只改文字：輔助工具才會念出來
    const note = element('p', 'pf-row-note');
    note.setAttribute('role', 'status');
    note.hidden = true;
    inner.append(note);
    const digest = stockAiDigest(entry.code);
    if (digest) inner.append(digest);

    const acts = element('div', 'pf-acts'), buttons = element('span');
    if (entry.inUniverse) {
      const link = element('a', '', `看 ${entry.name} 的個股頁 ›`);
      link.href = `stock.html?code=${encodeURIComponent(entry.code)}`;
      acts.append(link);
    } else acts.append(element('span', 'pf-scope outside', '不在本站分析範圍'));
    if (held) {
      buttons.append(deleteButton('刪除庫存', `刪除庫存 ${entry.code} ${entry.name}`, async () => {
        if (!confirm(`要刪除 ${entry.code} ${entry.name} 的庫存資料嗎？`)) return;
        try {
          const at = visibleIndex(li);
          await api(`/api/holdings/${encodeURIComponent(entry.code)}`, { method: 'DELETE', body: {} });
          setListStatus(`${entry.code} 庫存已刪除。`, 'success');
          await Promise.all([loadHoldings(), ...(features.level_advice ? [loadLevel()] : [])]);
          if (!$('holdingRows').querySelector(`.hv-row[data-code="${CSS.escape(entry.code)}"]:not([hidden])`)) focusAfterRemoval(at);
        } catch (error) { rowNote(li, error.message); }
      }));
    }
    if (watch) {
      buttons.append(deleteButton('移出自選', `從自選股刪除 ${entry.code} ${entry.name}`, async () => {
        if (!confirm(`要從自選股刪除 ${entry.code} ${entry.name} 嗎？`)) return;
        try {
          const at = visibleIndex(li);
          await api(`/api/watchlist/${encodeURIComponent(entry.code)}`, { method: 'DELETE', body: {} });
          setListStatus(`${entry.code} 已從自選股刪除。`, 'success');
          await loadWatchlist();
          if (!$('holdingRows').querySelector(`.hv-row[data-code="${CSS.escape(entry.code)}"]:not([hidden])`)) focusAfterRemoval(at);
        } catch (error) { rowNote(li, error.message); }
      }));
    }
    acts.append(buttons);
    inner.append(acts);
    clip.append(inner);
    panel.append(clip);
    li.append(top, panel);
    return li;
  }

  // 收起來的那一列，裡面的連結、設定與刪除鍵鍵盤走不到
  function setRowOpen(row, open) {
    row.classList.toggle('is-open', open);
    row.querySelector('.hv-top').setAttribute('aria-expanded', String(open));
    row.querySelector('.hv-panel').inert = !open;
  }

  // 進場（只播一次）。時間都寫在樣式裡（portfolio.css），這裡只跟著樣式的動畫走，不另外記一份時間：
  // 賺的（或還差的）那一段開始長，這一列被往上頂（或往下壓）一下；最後一列定位完，把進場的記號拿掉
  function playListEntrance(host) {
    const onStart = event => {
      if (!event.target.matches('.hv-bar .gain, .hv-bar .lack')) return;
      const row = event.target.closest('.hv-row');
      row.animate({ translate: ['0 0', `0 ${row.dataset.state === 'bull' ? -HOLDING_PUSH : HOLDING_PUSH}px`, '0 0'], offset: [0, .3, 1] }, { duration: 260, delay: 120, easing: 'ease-out' });
    };
    const onEnd = event => {
      if (event.animationName !== 'hv-row-settle' || event.target !== host.lastElementChild) return;
      host.classList.remove('is-entering');
      host.removeEventListener('animationstart', onStart);
      host.removeEventListener('animationend', onEnd);
    };
    host.addEventListener('animationstart', onStart);
    host.addEventListener('animationend', onEnd);
    host.classList.add('is-entering');
  }

  // 三顆篩選鍵：不符合的那幾列藏起來（開著的那一列被藏起來就先收合）；一列都不剩時寫一句怎麼新增
  function applyFilter() {
    const host = $('holdingRows');
    host.querySelector('.pf-none')?.remove();
    let shown = 0;
    host.querySelectorAll('.hv-row').forEach(row => {
      const keep = listFilter === 'all' || row.dataset.kinds.split(' ').includes(listFilter);
      row.hidden = !keep;
      if (keep) shown += 1;
      else setRowOpen(row, false);
    });
    if (!shown) host.append(element('li', 'hv-empty pf-none', EMPTY_WORDS[host.querySelector('.hv-row') ? listFilter : 'all']));
  }

  function renderList() {
    if (!listReady) return;
    const host = $('holdingRows'), model = window.PortfolioList.read(state.holdings, state.watchlist);
    // 同一份資料（含關注的名單與 AI 整理的內容）不重畫，開著的那一檔與正在播的進場才不會被打斷
    const watch = state.watch ? [[...state.watch.codes], state.watch.limit, [...state.watch.unwatchable]] : null;
    const key = JSON.stringify([state.holdings, state.watchlist, watch, state.aiVisible, state.aiDigest?.dataDate || null, state.aiVisible ? [...(state.aiDigest?.byCode || [])] : null]);
    if (key === listShown) return;
    listShown = key;
    renderTotals(model.summary);
    $('countAll').textContent = String(model.counts.all);
    $('countHolding').textContent = String(model.counts.holding);
    $('countWatch').textContent = String(model.counts.watch);
    const openCode = host.querySelector('.hv-row.is-open')?.dataset.code;
    host.classList.remove('is-entering');   // 新增、更新、刪除之後重畫就不再播進場；新的列直接在定位
    host.replaceChildren(...model.rows.map((entry, order) => listRow(entry, order, model.summary.complete)));
    applyFilter();
    const again = openCode && [...host.children].find(row => row.dataset.code === openCode && !row.hidden);
    if (again) setRowOpen(again, true);
    if (listEntered) return;
    listEntered = true;   // 進場只在進頁面時播；一開始是空的，之後新增第一檔也不播
    if (model.rows.length && !STILL.matches) playListEntrance(host);
  }

  // 清單裡的點擊：點一檔展開（一次只開一檔）；點開之後換自選的分類
  $('holdingRows').addEventListener('click', event => {
    const cell = event.target.closest('.pf-horizon .cell');
    if (cell) { changeHorizon(cell); return; }
    const top = event.target.closest('.hv-top');
    if (!top) return;
    const row = top.closest('.hv-row'), open = !row.classList.contains('is-open');
    [...$('holdingRows').querySelectorAll('.hv-row')].forEach(other => setRowOpen(other, other === row && open));
  });

  $('listFilter').addEventListener('click', event => {
    const chosen = event.target.closest('button[data-filter]');
    if (!chosen) return;
    listFilter = chosen.dataset.filter;
    $('listFilter').querySelectorAll('button').forEach(other => other.setAttribute('aria-pressed', String(other === chosen)));
    applyFilter();
  });

  // 「＋」：新增的表單平常收著，按了才展開；裡面先選是加入自選還是新增庫存
  function setAddOpen(open) {
    $('addToggle').setAttribute('aria-expanded', String(open));
    $('addPanel').classList.toggle('is-open', open);
    $('addClip').inert = !open;
  }
  function setAddKind(kind) {
    $('addKind').querySelectorAll('.cell').forEach(cell => cell.setAttribute('aria-pressed', String(cell.dataset.kind === kind)));
    for (const form of [$('watchlistForm'), $('holdingForm')]) form.hidden = form.dataset.kind !== kind;
  }
  $('addToggle').addEventListener('click', () => {
    const open = $('addToggle').getAttribute('aria-expanded') !== 'true';
    setAddOpen(open);
    if (open) ($('watchlistForm').hidden ? $('holdingCode') : $('watchCode')).focus({ preventScroll: true });
  });
  $('addKind').addEventListener('click', event => {
    const cell = event.target.closest('.cell[data-kind]');
    if (cell) setAddKind(cell.dataset.kind);
  });

  async function loadWatch() {
    let watch = null;
    if (features.watchlist_watch_v1) {
      try {
        const payload = await api('/api/watch');
        if (payload?.available) {
          watch = {
            count: payload.count, limit: payload.limit,
            codes: new Set((payload.items || []).map(item => item.code)),
            unwatchable: new Set(payload.unwatchable || []),
          };
        }
      } catch (_) { /* 讀不到關注時，自選股照常顯示，只是沒有開關 */ }
    }
    state.watch = watch;
  }

  async function loadWatchlist() {
    const [payload] = await Promise.all([api('/api/watchlist'), loadWatch()]);
    state.watchlist = payload.items || [];
    renderList();
  }
  async function loadHoldings() {
    const payload = await api('/api/holdings');
    state.holdings = payload.items || [];
    renderList();
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
      setStatus($('watchlistStatus'), '');
      setAddOpen(false);
      setListStatus(`${code} 已加入自選股。`, 'success');
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
      setStatus($('holdingStatus'), '');
      setAddOpen(false);
      setListStatus(`${code} 庫存已儲存。`, 'success');
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
    listReady = true;
    renderList();
    const dates = state.holdings.map(item => item.price_as_of).filter(Boolean).sort();
    const stale = state.holdings.some(item => item.price_stale);
    $('portfolioFreshness').textContent = dates.length
      ? `庫存價格至 ${dates.at(-1)}${stale ? '（部分沿用上次資料）' : ''}`
      : '尚無庫存價格';
  }).catch(error => {
    listReady = true;   // 這次沒讀到就不畫；之後新增或刪除成功再讀一次時照常畫
    $('portfolioFreshness').textContent = '資料讀取失敗';
    setListStatus(error.message, 'error');
  });
})();
