// 大盤事前預測（規格 .scratch/forecast-ledger/spec.md，B 票第 4、5 片）。
// 首屏只多兩樣東西：日 K 上一版的轉強線／轉弱線，和 K 線控制列一枚「預測」小鍵。
// 小鍵點開的浮板有三頁：預測（落點、理由、看錯條件…）、對帳（猜中／照歷史亂猜／固定規則並排）、
// 回看（過去任一份預測的兩條線畫在當時的 K 線上，加上到期的實際收盤）。
// 線取自 /api/forecast/latest（預測帳本發布到資料庫的那一筆），和對帳用的是同一筆數值。
// 只有站主、而且功能開關打開時 API 才有內容；沒有內容、載入失敗或本機版沒有這個 API 時，
// 這個檔案什麼都不加，首頁和原本一模一樣。
// 對外只有 window.MarketForecast.mount({ host, onChange, getDaily }) 與 overlays(period)。
(() => {
  const HORIZONS = ['1', '5', '20'];
  const ZONE = { above: '線上', between: '兩線之間', below: '線下' };
  const EDITION = { evening: '晚間版', morning: '晨間版' };
  const VIEWS = { forecast: '預測', summary: '對帳', history: '回看' };
  const MISS = {
    input_not_ready: '資料沒到齊', deadline_passed: '過了期限', model_failed: '模型失敗',
    model_timeout: '模型逾時', output_garbled: '回答亂碼', answer_invalid: '回答不合格',
  };
  const MA = { 5: '五日線', 10: '十日線', 20: '月線', 60: '季線', 120: '半年線', 240: '年線' };
  // 回看一次取最近六十天的預測（API 上限四百天）。
  const HISTORY_DAYS = 60;
  const state = {
    latest: null, horizon: '1', edition: null, view: 'forecast', onChange: null, getDaily: null, root: null,
    summary: undefined, history: undefined, historyFrom: null, picked: null,
  };

  // 顏色一律取首頁的設計變數；取不到時的備用色只寫這一份（淺色主題的值）。
  const FALLBACK = { '--up': '#c8262c', '--down': '#0f8058', '--faint': '#6d6874', '--ink': '#1d1b20' };
  const color = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || FALLBACK[name];
  const fmt = (value) => Number(value).toLocaleString('zh-TW', { maximumFractionDigits: 2 });
  const pct = (value) => (value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`);
  const mmdd = (day) => (day ? `${day.slice(5, 7)}/${day.slice(8, 10)}` : '—');
  const el = (tag, attrs = {}, text) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = (tag, attrs = {}) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };

  // 價籤只放得下兩三個字：用關卡的短名，全名在明細裡。
  function shortName(line) {
    const id = line.id || '';
    let m = /^ma(\d+)$/.exec(id);
    if (m) return MA[m[1]] || `${m[1]}日線`;
    if ((m = /^(high|low)(\d+)$/.exec(id))) return m[1] === 'high' ? '前高' : '前低';
    if (id === 'prev_high') return '昨高';
    if (id === 'prev_low') return '昨低';
    if ((m = /^gap\d+_(upper|lower)$/.exec(id))) return m[1] === 'upper' ? '缺口上' : '缺口下';
    if (id.startsWith('dist_up')) return '距離上';
    if (id.startsWith('dist_down')) return '距離下';
    return line.name || '';
  }

  function forecastOf(edition) {
    const record = state.latest && state.latest.editions[edition];
    return record && record.kind === 'forecast' ? record : null;
  }

  function current() {
    return state.edition ? forecastOf(state.edition) : null;
  }

  function overlays(period) {
    const record = current();
    if (period !== 'daily' || !record) return [];
    const cell = record.cells[state.horizon];
    if (!cell) return [];
    return [
      { value: cell.strong_line.value, label: `轉強線 ${cell.strong_line.name}`, short: shortName(cell.strong_line), color: color('--up'), pos: 'above' },
      { value: cell.weak_line.value, label: `轉弱線 ${cell.weak_line.name}`, short: shortName(cell.weak_line), color: color('--down'), pos: 'below' },
    ];
  }

  function segmented(label, items, key, chosen) {
    const group = el('div', { class: 'forecast-seg', role: 'group', 'aria-label': label });
    for (const [value, text] of items) {
      group.append(el('button', { type: 'button', 'aria-pressed': String(value === chosen), [`data-${key}`]: value }, text));
    }
    return group;
  }

  function facts(pairs) {
    const list = el('dl', { class: 'forecast-facts' });
    for (const [term, value, tone] of pairs) list.append(el('dt', {}, term), el('dd', tone ? { class: tone } : {}, value));
    return list;
  }

  // ---- 預測頁 ----
  function forecastView(panel) {
    const record = current();
    const chosen = state.latest.editions[state.edition];
    if (!record) {
      panel.append(facts([
        [EDITION[state.edition] || '預測', `漏記：${MISS[chosen?.miss_reason] || '沒有寫成'}`],
        ['基準日', mmdd(state.latest.base_date)],
      ]));
      return;
    }
    const cell = record.cells[state.horizon];
    const cutoffs = [...new Set(Object.values(record.data_cutoffs || {}))].map(mmdd).join('、') || '—';
    panel.append(facts([
      ['落點', ZONE[cell.zone] || cell.zone],
      ['轉強線', `${cell.strong_line.name} ${fmt(cell.strong_line.value)}`, 'is-up'],
      ['轉弱線', `${cell.weak_line.name} ${fmt(cell.weak_line.value)}`, 'is-down'],
      ['理由', cell.reason],
      ['看錯條件', cell.invalidation],
      ['盤勢', record.regime],
      ['紀錄', `${record.run_mode === 'official' ? '正式' : '試跑'} · ${EDITION[record.edition]} · 基準日 ${mmdd(record.base_date)}`],
      ['資料截止', cutoffs],
    ]));
  }

  // ---- 對帳頁 ----
  function bar(label, value, tone) {
    const row = el('div', { class: `forecast-bar ${tone}` });
    const track = el('span', { class: 'forecast-bar-track' });
    const fill = el('span', { class: 'forecast-bar-fill' });
    fill.style.transform = `scaleX(${value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value))})`;
    track.append(fill);
    row.append(el('span', { class: 'forecast-bar-label' }, label), track, el('span', { class: 'forecast-bar-value num' }, pct(value)));
    return row;
  }

  // 這一版、同方法與輸入版本的系列；試跑與正式各自一組，正式在前。
  function seriesByRunMode(summary) {
    const record = current() || Object.values(state.latest.editions).find(Boolean);
    const sameSeries = (g) => g.edition === state.edition && (!record
      || (g.method_version === record.method_version && g.input_version === record.input_version));
    const groups = (summary.groups || []).filter(sameSeries);
    return ['official', 'trial'].map(mode => groups.filter(g => g.run_mode === mode)).filter(list => list.length);
  }

  function summaryView(panel) {
    if (state.summary === undefined) {
      panel.append(el('p', { class: 'forecast-note' }, '載入中'));
      loadSummary();
      return;
    }
    const series = state.summary ? seriesByRunMode(state.summary) : [];
    if (!series.length) {
      panel.append(el('p', { class: 'forecast-note' }, '還沒有對帳紀錄'));
      return;
    }
    for (const groups of series) seriesView(panel, groups, series.length > 1);
  }

  function seriesView(panel, groups, titled) {
    const official = groups[0].run_mode === 'official';
    if (titled) panel.append(el('h4', { class: 'forecast-series' }, official ? '正式' : '試跑'));
    for (const horizon of HORIZONS) {
      const group = groups.find(g => String(g.horizon) === horizon);
      if (!group) continue;
      const block = el('section', { class: 'forecast-score' });
      const head = el('header', { class: 'forecast-score-head' });
      head.append(el('strong', {}, `${horizon}日`), el('span', { class: 'forecast-tag' }, group.verdict),
        el('span', { class: 'forecast-count num' }, `${group.reconciled} 筆`));
      block.append(head, bar('猜中', group.hit_rate, 'is-hit'), bar('照歷史亂猜', group.baseline_rate, 'is-base'),
        bar('固定規則', group.fixed_rule_rate, 'is-rule'));
      if (group.pullback && group.pullback.reconciled) {
        block.append(el('p', { class: 'forecast-note' }, `說是回檔 ${group.pullback.reconciled} 筆：猜中 ${pct(group.pullback.hit_rate)}`));
      }
      panel.append(block);
    }
    panel.append(el('p', { class: 'forecast-note' },
      `${official ? '正式' : '試跑'} · 漏記 ${groups[0].misses} 次 · 還沒到期 ${groups.reduce((n, g) => n + g.pending, 0)} 格`));
  }

  // ---- 回看頁 ----
  function historyView(panel) {
    if (state.history === undefined) {
      panel.append(el('p', { class: 'forecast-note' }, '載入中'));
      loadHistory();
      return;
    }
    const items = (state.history || []).filter(item => item.kind === 'forecast' && item.edition === state.edition).reverse();
    if (!items.length) {
      panel.append(el('p', { class: 'forecast-note' }, '還沒有過去的預測'));
      return;
    }
    const picked = items.find(item => item.forecast_id === state.picked) || items[0];
    const list = el('div', { class: 'forecast-days', role: 'group', 'aria-label': '基準日' });
    for (const item of items) {
      const outcome = item.reconciliations && item.reconciliations[state.horizon];
      const mark = !outcome ? '' : outcome.actual_zone === item.cells[state.horizon].zone ? ' ✓' : ' ✗';
      list.append(el('button', { type: 'button', 'aria-pressed': String(item === picked), 'data-pick': item.forecast_id }, `${mmdd(item.base_date)}${mark}`));
    }
    // 一次載六十天；要回到更早的任一份就再往前載一段。
    list.append(el('button', { type: 'button', class: 'forecast-more', 'data-more': '1' }, '更早'));
    panel.append(list, revisitChart(picked));
    const cell = picked.cells[state.horizon];
    const outcome = picked.reconciliations && picked.reconciliations[state.horizon];
    panel.append(facts([
      ['預測', `${ZONE[cell.zone]}（${mmdd(picked.base_date)} ${EDITION[picked.edition]}）`],
      ['實際', outcome ? `${mmdd(outcome.expiry_date)} 收 ${fmt(outcome.expiry_close)}，${ZONE[outcome.actual_zone]}` : '還沒到期'],
      ['結果', outcome ? (outcome.actual_zone === cell.zone ? '猜中' : '沒中') : '—', outcome ? (outcome.actual_zone === cell.zone ? 'is-up' : 'is-down') : ''],
    ]));
  }

  // 當時的兩條線畫在當時的 K 線上：基準日前十五根到到期日（或目前最新）為止，到期收盤畫一個點。
  function revisitChart(item) {
    const cell = item.cells[state.horizon];
    const outcome = item.reconciliations && item.reconciliations[state.horizon];
    const daily = (state.getDaily && state.getDaily()) || [];
    const baseIndex = daily.findIndex(row => row.date === item.base_date);
    const W = 316; const H = 150; const pad = { l: 4, r: 4, t: 10, b: 18 };
    const box = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'forecast-revisit', role: 'img',
      'aria-label': `${mmdd(item.base_date)} 的 ${state.horizon} 日預測與之後走勢` });
    if (baseIndex < 0) return box;
    const expiryIndex = outcome ? daily.findIndex(row => row.date === outcome.expiry_date) : -1;
    const end = Math.min(daily.length - 1, expiryIndex >= 0 ? expiryIndex : baseIndex + Number(state.horizon));
    const rows = daily.slice(Math.max(0, baseIndex - 15), end + 1);
    const slots = Math.max(rows.length, baseIndex - Math.max(0, baseIndex - 15) + Number(state.horizon) + 1);
    const values = rows.flatMap(row => [row.high, row.low]).concat([cell.strong_line.value, cell.weak_line.value]);
    if (outcome) values.push(outcome.expiry_close);
    const lo = Math.min(...values); const hi = Math.max(...values); const span = hi - lo || 1;
    const y = (v) => pad.t + (hi - v) / span * (H - pad.t - pad.b);
    const step = (W - pad.l - pad.r) / slots;
    const x = (i) => pad.l + step * (i + 0.5);
    const up = color('--up'); const down = color('--down'); const faint = color('--faint');
    rows.forEach((row, i) => {
      const color = row.close >= row.open ? up : down;
      box.append(svg('line', { x1: x(i), x2: x(i), y1: y(row.high), y2: y(row.low), stroke: color, 'stroke-width': 1 }));
      box.append(svg('rect', { x: x(i) - step * 0.3, width: step * 0.6, y: y(Math.max(row.open, row.close)),
        height: Math.max(1, Math.abs(y(row.open) - y(row.close))), fill: color }));
    });
    const baseX = x(baseIndex - Math.max(0, baseIndex - 15));
    const endX = W - pad.r;
    box.append(svg('line', { x1: baseX, x2: baseX, y1: pad.t, y2: H - pad.b, stroke: faint, 'stroke-dasharray': '2 3' }));
    // 基準日之前畫淡虛線（關卡當時已在圖上），之後畫實線（預測期間）。
    for (const [value, color] of [[cell.strong_line.value, up], [cell.weak_line.value, down]]) {
      box.append(svg('line', { x1: pad.l, x2: baseX, y1: y(value), y2: y(value), stroke: color, 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.5 }));
      box.append(svg('line', { x1: baseX, x2: endX, y1: y(value), y2: y(value), stroke: color, 'stroke-width': 1.5 }));
    }
    if (outcome) {
      const ex = expiryIndex >= 0 ? x(expiryIndex - Math.max(0, baseIndex - 15)) : x(slots - 1);
      box.append(svg('circle', { cx: ex, cy: y(outcome.expiry_close), r: 4, fill: color('--ink') }));
    }
    const label = svg('text', { x: baseX + 3, y: H - 5, fill: faint, 'font-size': 10 });
    label.textContent = mmdd(item.base_date);
    box.append(label);
    return box;
  }

  async function getJson(url) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { accept: 'application/json' } });
    if (!response.ok) return null;
    const body = await response.json();
    return body && body.available === true ? body : null;
  }

  async function loadSummary() {
    try { state.summary = (await getJson('/api/forecast/summary'))?.summary || null; } catch { state.summary = null; }
    render();
  }

  const shiftDays = (day, days) => new Date(Date.parse(`${day}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);

  // 第一次載基準日往前六十天；之後每按一次「更早」就把下一段六十天接在前面。
  async function loadHistory() {
    const to = state.historyFrom ? shiftDays(state.historyFrom, -1) : state.latest.base_date;
    const from = shiftDays(to, -HISTORY_DAYS);
    try {
      const items = (await getJson(`/api/forecast/history?from=${from}&to=${to}`))?.items || [];
      state.history = [...items, ...(state.history || [])];
      state.historyFrom = from;
    } catch {
      state.history = state.history || null;
    }
    render();
  }

  function renderPanel(panel) {
    panel.replaceChildren(segmented('檢視', Object.entries(VIEWS), 'view', state.view));
    const editions = Object.keys(EDITION).filter(key => state.latest.editions[key]);
    if (editions.length > 1) panel.append(segmented('版本', editions.map(key => [key, EDITION[key]]), 'edition', state.edition));
    if (state.view === 'summary') return summaryView(panel);
    if (current()) panel.append(segmented('期間', HORIZONS.map(key => [key, `${key}日`]), 'horizon', state.horizon));
    if (state.view === 'history') return historyView(panel);
    return forecastView(panel);
  }

  function render() {
    const { chip, panel } = state;
    chip.textContent = current() ? `預測 ${state.horizon}日` : '預測 漏記';
    if (!panel.hidden) renderPanel(panel);
  }

  // 手機寬度時浮板是貼底的固定面板；首頁區塊有進場位移（transform），固定定位會被它困住，
  // 所以手機打開時把浮板移到 body 底下，桌機留在小鍵旁。
  const narrow = () => window.matchMedia('(max-width: 720px)').matches;

  function setOpen(open) {
    const { chip, panel } = state;
    if (open) (narrow() ? document.body : state.root).append(panel);
    panel.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
    if (open) renderPanel(panel);
  }

  function build(host) {
    const root = el('div', { class: 'forecast-control' });
    const chip = el('button', { type: 'button', class: 'forecast-chip', 'aria-expanded': 'false', 'aria-controls': 'forecastPanel' });
    const panel = el('div', { class: 'forecast-pop', id: 'forecastPanel', role: 'dialog', 'aria-label': '大盤預測' });
    panel.hidden = true;
    root.append(chip, panel);
    chip.addEventListener('click', () => setOpen(panel.hidden));
    panel.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) return;
      const { horizon, edition, view, pick, more } = button.dataset;
      if (more) { loadHistory(); return; }
      if (view) state.view = view;
      if (pick) state.picked = pick;
      if (horizon) state.horizon = horizon;
      if (edition) state.edition = edition;
      render();
      if (horizon || edition) state.onChange && state.onChange();
    });
    // 點選項會重畫浮板，被點的按鈕當下已不在畫面上；用事件路徑判斷是不是點在外面（浮板在手機會移到 body）。
    document.addEventListener('click', (event) => {
      const path = event.composedPath();
      if (!path.includes(root) && !path.includes(panel)) setOpen(false);
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !panel.hidden) { setOpen(false); chip.focus(); } });
    host.prepend(root);
    Object.assign(state, { root, chip, panel });
  }

  async function mount({ host, onChange, getDaily }) {
    if (!host) return;
    try {
      const latest = await getJson('/api/forecast/latest');
      if (!latest || !latest.editions || !Object.values(latest.editions).some(Boolean)) return;
      state.latest = latest;
      // 本人 2026-11-15 選 A：一次一版，預設看最後寫成的那一版（晨間版有預測就看晨間版）。
      state.edition = forecastOf('morning') ? 'morning' : forecastOf('evening') ? 'evening'
        : (latest.editions.morning ? 'morning' : 'evening');
      state.onChange = onChange;
      state.getDaily = getDaily;
      build(host);
      render();
      onChange && onChange();
    } catch {
      // 預測是附加功能：任何失敗都不影響首頁其他部分。
    }
  }

  window.MarketForecast = { mount, overlays };
})();
