/* 個股分析頁：只向後端 API 取結果並畫圖，不在前端算分數或價位。 */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const fmt = n => n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const DIRECTION = { bullish: '偏多', bearish: '偏空', neutral: '中性' };
  const TREND = { strong: '強', watch: '觀察', weak: '弱', risk: '風險' };
  const DIMS = [['T', '趨勢'], ['C', '籌碼'], ['V', '量價'], ['I', '法人'], ['F', '基本面']];
  const DEFAULT_PRESET = '2TON-FIT-V1';
  let chart = null;
  let lastChartKey = '';
  let lastResult = null;
  let requestSequence = 0;
  let pendingRequest = null;
  const baseline = new Map(); // 代號:資料日 → 綜合觀察結果，用來標出「換策略後哪裡變了」

  function syncTabs(preset) {
    document.querySelectorAll('#presetTabs [data-preset]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.preset === preset)));
  }

  function diffText(now, base) {
    if (now === null || now === undefined || base === null || base === undefined || now === base) return '';
    const d = now - base;
    return `比綜合觀察${d > 0 ? '高' : '低'} ${fmt(Math.abs(d))}`;
  }

  function renderCompare(r, base) {
    const isBase = r.preset.id === DEFAULT_PRESET;
    const pairs = [['pressure1', r.levels.pressure1, base && base.levels.pressure1], ['support1', r.levels.support1, base && base.levels.support1],
      ['defense', r.levels.defense, base && base.levels.defense], ['score', r.score, base && base.score]];
    pairs.forEach(([id, now, was]) => { $(`${id}Diff`).textContent = isBase || !base ? '' : diffText(now, was); });
    const line = $('compareLine');
    if (isBase || !base) { line.hidden = true; return; }
    const changed = pairs.filter(([, now, was]) => now !== was).length;
    line.hidden = false;
    line.textContent = changed
      ? `目前是「${r.preset.name}」：和綜合觀察相比，有 ${changed} 項數字不同（標在各數字下方）。`
      : `目前是「${r.preset.name}」：這檔在這個策略下，壓力、支撐、防守與總分都和綜合觀察相同。`;
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('market-theme', theme); } catch (_) { /* 私密視窗可能無法存 */ }
    $('themeText').textContent = theme === 'dark' ? '淺色' : '深色';
    if (lastResult) drawChart(lastResult);
  }

  function codeFromUrl() {
    const code = new URLSearchParams(location.search).get('code');
    return code && /^[0-9A-Z]{4,6}$/.test(code) ? code : null;
  }

  function presetFromUrl() {
    return new URLSearchParams(location.search).get('preset') || DEFAULT_PRESET;
  }

  function updateUrl(code, preset) {
    const url = new URL(location.href);
    url.searchParams.set('code', code);
    url.searchParams.set('preset', preset);
    if (url.href !== location.href) history.pushState(null, '', url);
  }

  function verdictText(r) {
    const p = r.levels.pressure1, s = r.levels.support1;
    const close = r.quote.close;
    const parts = [];
    const t = r.dimensions.T;
    parts.push(t >= 68 ? '趨勢偏多' : t >= 38 ? '趨勢轉弱、還在中期均線之上' : '趨勢偏弱');
    if (r.levels.sweet && r.levels.sweet.formed && r.levels.sweet.position !== null) {
      const pos = r.levels.sweet.position;
      parts.push(pos < 0 ? '跌到觀察區下方' : pos < 0.5 ? '在觀察區下半' : pos <= 1 ? '在觀察區上半' : '已高於觀察區');
    }
    if (p) parts.push(`上方 ${fmt(p)} 有壓力（距 ${((p / close - 1) * 100).toFixed(1)}%）`);
    if (s) parts.push(`下方 ${fmt(s)} 有支撐`);
    return `${r.preset.name}：${parts.join('，')}。`;
  }

  const FIELD = {
    close: '收盤', ma5: 'MA5', ma10: 'MA10', ma20: 'MA20', ma60: 'MA60', structure: '結構', ma20_slope5: 'MA20 五日斜率%',
    dif: 'DIF', dea: 'DEA', hist: '柱', prev_hist: '前一柱', k: 'K', d: 'D', prev_k: '前 K', prev_d: '前 D', rsi14: 'RSI14',
    ref_high: '前高', prior_high: '前 20 日高', vol_ratio: '量比', chg: '漲跌%', upper_shadow: '上影比', bias20: 'MA20 乖離',
    valid_bars: '有效日線', margin_change_today: '融資今日增減（張）', margin_diff5: '融資五日增減（張）', m5_pct: '融資五日變化%',
    short_balance: '融券餘額（張）', today: '三大法人今日（股）', foreign: '外資', trust: '投信', dealer: '自營', d5: '五日合計',
    d10: '十日合計', d20: '二十日合計', days: '資料天數', inst_cost: '法人估計成本',
    borrow_sold: '借券賣出（股）', borrow_balance: '借券餘額（股）', borrow_diff5: '借券五日增減（股）',
    yoy: '營收年增%', cum_yoy: '累計營收年增%', period: '資料期別', eps: 'EPS', basis: '口徑', estimated_available_at: '估計可用日', per: '本益比', pbr: '淨值比', side: '估值'
  };
  const STREAK_KEYS = ['foreign', 'trust', 'dealer'];

  function evidenceText(s) {
    const e = s.evidence || {};
    if (e.reason) return e.reason;
    if (s.id === 'I02') return STREAK_KEYS.map(k => `${FIELD[k]}${e[k] > 0 ? '連買' : e[k] < 0 ? '連賣' : '持平'} ${Math.abs(e[k] || 0)} 日`).join('、');
    if (s.id === 'I03') return e.turned && e.turned.length ? `${e.turned.map(k => FIELD[k]).join('、')}今日方向與前五日累計相反` : '外資與投信沒有明顯轉向';
    return Object.entries(e).filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
      .map(([k, v]) => `${FIELD[k] || k} ${typeof v === 'number' ? fmt(v) : v}`).join('、');
  }

  function render(r) {
    document.title = `${r.name} ${r.code}｜${r.preset.name}`;
    $('content').dataset.preset = r.preset.id;
    $('content').dataset.paramsHash = r.params_hash;
    $('presetSelect').value = r.preset.id;
    syncTabs(r.preset.id);
    $('appliedPreset').textContent = `${r.preset.name} · 本站自有分析`;
    $('presetDescription').textContent = r.preset.description;
    lastResult = r;
    $('stockName').textContent = r.name;
    $('stockCode').textContent = r.code;
    $('asOf').textContent = r.as_of;
    $('close').textContent = fmt(r.quote.close);
    const chg = r.quote.chg_pct;
    $('chg').textContent = `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`;
    $('chg').className = `num ${chg > 0 ? 'up' : chg < 0 ? 'down' : ''}`;
    $('trendBadge').textContent = r.trend ? `趨勢分類：${TREND[r.trend]}` : '趨勢分類：總分未到齊';
    $('trendBadge').dataset.trend = r.trend || '';
    $('verdict').textContent = verdictText(r);
    $('support1').textContent = fmt(r.levels.support1);
    $('pressure1').textContent = fmt(r.levels.pressure1);
    $('defense').textContent = fmt(r.levels.defense);
    $('scoreValue').textContent = r.score === null ? '未到齊' : String(r.score);
    if (r.preset.id === DEFAULT_PRESET) baseline.set(`${r.code}:${r.as_of}`, r);
    renderCompare(r, baseline.get(`${r.code}:${r.as_of}`));

    $('dims').replaceChildren(...DIMS.map(([key, label]) => {
      const value = r.dimensions[key];
      const gaps = r.coverage && r.coverage[key] ? r.coverage[key].missing_sources : [];
      const box = document.createElement('div');
      box.className = `sk-dim${value === null ? ' missing' : ''}${value !== null && gaps.length ? ' partial' : ''}`;
      const reason = key === 'F' && r.kind === 'etf' ? 'ETF 不適用' : '尚無資料';
      box.innerHTML = `<span>${label}</span><b>${value === null ? reason : value}</b><i style="--w:${value || 0}%"></i>`;
      if (value !== null && gaps.length) box.append(Object.assign(document.createElement('small'), { textContent: `不完整：缺${gaps.join('、')}` }));
      return box;
    }));
    $('scoreLine').textContent = r.score === null ? `總分：${r.score_note}，先不給總分。` : `總分 ${r.score}（${TREND[r.trend]}）`;
    $('flags').replaceChildren(...r.risk_flags.map(f => Object.assign(document.createElement('li'), { textContent: `${f.id}：${f.text}` })));
    const c = r.strategy_counts;
    $('counts').textContent = `${r.strategies.length} 招中：成立 ${c.triggered}、醞釀中 ${c.confirming}、未成立 ${c.off}、資料不足 ${c.unavailable}。「成立」只代表條件符合，不是買賣訊號。`;
    $('strategyRows').replaceChildren(...r.strategies.map(s => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><b>${s.id}</b> ${s.name}</td><td><span class="sk-pill" data-s="${s.status}">${s.status_label}</span></td><td>${DIRECTION[s.direction]}</td><td></td>`;
      tr.lastElementChild.textContent = evidenceText(s);
      return tr;
    }));
    const lv = r.levels;
    $('levelSources').innerHTML = '';
    const lines = [
      `${r.preset.name}：${r.preset.description} 價位依所選方案的來源與窗口重新計算，不同方案可能得到相同價位。`,
      lv.sweet && lv.sweet.formed ? `觀察區 ${fmt(lv.sweet.low)}～${fmt(lv.sweet.high)}（中心與寬度為本站自有分析，非買進區）。` : '觀察區尚未形成，防守參考只作分析，不是停損指令。',
      `第二支撐 ${fmt(lv.support2)}、第二壓力 ${fmt(lv.pressure2)}。`,
    ];
    lines.forEach(t => $('levelSources').append(Object.assign(document.createElement('p'), { className: 'sk-source', textContent: t })));

    $('notes').replaceChildren(...[
      r.labels.own_formula + '；24 招門檻是本站 2TON-FIT-V1 規則，部分招式與原站行為已知不同。',
      r.labels.not_advice + '；支撐、壓力、防守參考都是分析數字。',
      `籌碼、法人、借券、估值用 ${r.qa.chip_range || '—'} 的官方逐日原件；月營收 ${r.sources && r.sources.revenue_period || '—'}、EPS ${r.sources && r.sources.eps_period || '—'} 當年累計（可用日為估計，不是公司公告日）。缺資料的招式標「資料不足」。`,
      '價格未還原權值，除權息前後的均線會有落差。',
    ].map(t => Object.assign(document.createElement('li'), { textContent: t })));

    $('status').hidden = true;
    $('content').hidden = false;
    drawChart(r);
  }

  function drawChart(r) {
    const candles = r.bars.map(b => ({ t: b.date, label: b.date, open: b.open, high: b.high, low: b.low, close: b.close, amount: b.volume }));
    const overlays = [];
    if (r.levels.pressure1) overlays.push({ value: r.levels.pressure1, label: `壓力 ${fmt(r.levels.pressure1)}`, color: css('--gold', '#94681a'), pos: 'above', align: 'right' });
    if (r.levels.support1) overlays.push({ value: r.levels.support1, label: `支撐 ${fmt(r.levels.support1)}`, color: css('--down', '#0f8058'), pos: 'below', align: 'right' });
    if (r.levels.defense) overlays.push({ value: r.levels.defense, label: `防守 ${fmt(r.levels.defense)}`, color: css('--up', '#c8262c'), pos: 'below', align: 'right' });
    const sweet = r.levels.sweet;
    if (sweet && sweet.formed) {
      overlays.push({ value: sweet.low, label: `觀察下緣 ${fmt(sweet.low)}`, color: css('--dim', '#666666'), pos: 'below', align: 'left' });
      overlays.push({ value: sweet.high, label: `觀察上緣 ${fmt(sweet.high)}`, color: css('--dim', '#666666'), pos: 'above', align: 'left' });
    }
    const key = `${r.code}:${r.as_of}`;
    const savedView = chart && key === lastChartKey ? { ...chart.view } : null;
    if (!chart) chart = new window.KChart($('chartHost'), { macd: false, overlayLabels: 'stacked', height: w => Math.max(300, Math.min(460, w * 0.52)) });
    chart.setData(candles, { count: Math.min(candles.length, innerWidth < 600 ? 50 : 90), overlays, unit: '日' });
    if (savedView) { chart.view = savedView; chart.draw(); }
    lastChartKey = key;
    chart.canvas.setAttribute('aria-label', `${r.preset.name}，${r.name}日K，${candles[0].label}至${candles[candles.length - 1].label}，含支撐、壓力、觀察區與防守參考線。`);
  }

  async function load(code, preset = presetFromUrl()) {
    const sequence = ++requestSequence;
    if (pendingRequest) pendingRequest.abort();
    pendingRequest = new AbortController();
    $('content').hidden = true;
    $('content').setAttribute('aria-busy', 'true');
    $('status').hidden = false;
    $('status').textContent = `重新分析 ${code}…`;
    $('codeInput').value = code;
    $('presetSelect').value = preset;
    syncTabs(preset);
    if (location.protocol === 'file:') {
      $('content').setAttribute('aria-busy', 'false');
      $('status').textContent = '請從本機服務開啟（http://127.0.0.1:8876/stock.html），個股分析需要後端計算。';
      return;
    }
    const params = new URLSearchParams({ preset });
    const asOf = new URLSearchParams(location.search).get('as_of');
    if (asOf) params.set('as_of', asOf);
    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(code)}/analysis?${params}`, { cache: 'no-store', signal: pendingRequest.signal });
      const data = await response.json();
      if (sequence !== requestSequence) return;
      if (!response.ok || data.error || (data.qa && data.qa.blocking)) {
        $('status').textContent = data.qa && data.qa.reason ? `${code}：${data.qa.reason}，不給結論。` : `${code}：${data.message || '查不到資料'}`;
        return;
      }
      if (!data.preset || data.preset.id !== preset) {
        $('status').textContent = '本機服務尚未載入分析方案新版，請更新服務後再使用；不顯示其他方案的結果。';
        return;
      }
      // 第一次看非預設策略時，順便取綜合觀察當比較基準（同一檔同一天只取一次）。
      if (preset !== DEFAULT_PRESET && !baseline.has(`${data.code}:${data.as_of}`)) {
        try {
          const bp = new URLSearchParams(params); bp.set('preset', DEFAULT_PRESET);
          const br = await fetch(`/api/stocks/${encodeURIComponent(code)}/analysis?${bp}`, { cache: 'no-store', signal: pendingRequest.signal });
          const bd = br.ok ? await br.json() : null;
          if (bd && bd.preset && bd.preset.id === DEFAULT_PRESET && !(bd.qa && bd.qa.blocking)) baseline.set(`${bd.code}:${bd.as_of}`, bd);
        } catch (_) { /* 比較基準拿不到就不顯示差異，不影響主結果 */ }
        if (sequence !== requestSequence) return;
      }
      render(data);
    } catch (error) {
      if (sequence !== requestSequence || error.name === 'AbortError') return;
      $('status').textContent = '本機服務沒有回應，請稍後重新選擇分析方案。';
    } finally {
      if (sequence === requestSequence) $('content').setAttribute('aria-busy', 'false');
    }
  }

  $('searchForm').addEventListener('submit', event => {
    event.preventDefault();
    const code = $('codeInput').value.trim().toUpperCase();
    if (!/^[0-9A-Z]{4,6}$/.test(code)) { $('status').hidden = false; $('status').textContent = '代號格式不正確。'; return; }
    const preset = $('presetSelect').value || DEFAULT_PRESET;
    updateUrl(code, preset);
    load(code, preset);
  });
  document.querySelectorAll('#presetTabs [data-preset]').forEach(button => button.addEventListener('click', () => {
    if (button.getAttribute('aria-checked') === 'true') return;
    $('presetSelect').value = button.dataset.preset;
    $('presetSelect').dispatchEvent(new Event('change'));
  }));
  $('presetTabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const buttons = [...document.querySelectorAll('#presetTabs [data-preset]')];
    const i = buttons.findIndex(b => b.getAttribute('aria-checked') === 'true');
    const next = buttons[(i + (event.key === 'ArrowRight' ? 1 : buttons.length - 1)) % buttons.length];
    event.preventDefault(); next.focus(); next.click();
  });
  $('presetSelect').addEventListener('change', () => {
    const code = codeFromUrl() || '2330';
    const preset = $('presetSelect').value;
    updateUrl(code, preset);
    load(code, preset);
  });
  window.addEventListener('popstate', () => load(codeFromUrl() || '2330', presetFromUrl()));
  $('verdictButton').addEventListener('click', () => {
    const open = $('layer1').hidden;
    $('layer1').hidden = !open;
    $('verdictButton').setAttribute('aria-expanded', String(open));
    if (open) $('layer1').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  });
  $('themeToggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  let saved = null;
  try { saved = localStorage.getItem('market-theme'); } catch (_) { /* 私密視窗 */ }
  setTheme(saved === 'dark' ? 'dark' : 'light');
  load(codeFromUrl() || '2330');
})();
