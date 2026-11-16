// 自選股關注：判斷上網站（規格 .scratch/watchlist-watch/spec.md，E 票第 9–11 片）。
// 自選股清單：每一檔關注股名稱下一行——狀態點＋狀態、觸發價、對了幾次／幾次（本人 2026-11-16 選 A）。
// 個股頁：K 線控制列一枚「判斷」小鍵；日／週／月 K 上畫觸發線與看錯線；小鍵點開的浮板三頁——
//   判斷（理由、是否試跑、資料截止日、缺了哪一層）、成績（贏大盤與隨便挑兩條並排，一次一版；本人選 A）、
//   回看（過去任一份判斷的兩條線畫在當時的 K 線上，加上之後的走勢與對帳結果）。
// 開著個股頁時，沿用頁面現有的即時報價：成交價碰到觸發線就把小鍵改寫成「判斷 已到 價位」、觸發線標「已到」並加粗
// （只是提醒，不改已鎖定的判斷）。本人 2026-11-16：不用紅色亮燈，免得誤會是漲停；選 A 只換字。紅綠只留給漲跌。
// 資料來自 /api/watch/judgments*、/api/watch/summary：只有站主、而且功能開關打開時才有內容。
// 沒有內容、載入失敗時這個檔案什麼都不加，頁面和原本一模一樣。
// 對外：window.WatchJudgments = { loadList, listLine, mount, overlays, onLive }。
(() => {
  const WATCHED = new Set(['還沒到', '接近']); // 盤中只盯這兩種；「可以留意」在判斷當下就標著了
  const TONE = { 可以留意: 'ready', 接近: 'near', 還沒到: 'far' };
  const EDITION = { evening: '晚間版', morning: '晨間版' };
  const VIEWS = { judgment: '判斷', score: '成績', history: '回看' };
  const HORIZONS = [5, 20];
  const LAYER = { technical: '技術面', market: '大盤預測', news: '官方公告', peers: '國外同類股', lessons: '過去對帳' };
  const MISS = {
    input_not_ready: '資料沒到齊', deadline_passed: '過了期限', model_failed: '模型失敗', model_timeout: '模型逾時',
    output_garbled: '回答亂碼', answer_invalid: '回答不合格', quota_exhausted: '額度用完',
  };
  // 回看一次列幾份；按「更早」再多列這麼多。
  const HISTORY_PAGE = 20;

  // ---- 純邏輯（不碰畫面；測試直接驗這幾個） ----

  // 清單一行要顯示的東西。entry：/api/watch/judgments 那一檔；ledgerDate：帳本最新的那一天。
  function listParts(entry, ledgerDate) {
    if (!ledgerDate) return null; // 帳本還是空的：什麼都不加
    if (!entry) return { tone: 'none', status: '還沒有判斷' };
    if (entry.kind !== 'judgment') return { tone: 'miss', status: '漏記', record: entry.record };
    return {
      tone: TONE[entry.status] || 'far', status: entry.status,
      verb: entry.status === '可以留意' ? '已站上' : '站上', trigger: entry.trigger_line.value, record: entry.record,
    };
  }

  // 觸價：最新一份判斷是「還沒到／接近」，而即時報價是盤中、新鮮、基準日之後的成交，成交價 ≥ 觸發線。
  function touched(record, payload) {
    if (!record || record.kind !== 'judgment' || !WATCHED.has(record.status) || !record.trigger_line) return false;
    if (!payload || !payload.ok || !payload.market_open || !payload.live || !payload.quote) return false;
    const price = Number(payload.quote.price);
    if (!Number.isFinite(price)) return false;
    if (!payload.quote.data_date || payload.quote.data_date <= record.base_date) return false;
    return price >= record.trigger_line.value;
  }

  // 個股頁小鍵上的字。reached：盤中已碰到觸發線。
  function chipText(record, reached) {
    if (!record) return '判斷';
    if (record.kind !== 'judgment') return '判斷 漏記';
    return reached ? `判斷 已到 ${fmt(record.trigger_line.value)}` : `判斷 ${record.status}`;
  }

  // 圖上的兩條線（tone 是設計變數名）。觸發線一律金色；碰到了只換標籤字並加粗，不換顏色。
  function lineSpecs(record, reached) {
    if (!record || record.kind !== 'judgment') return [];
    return [
      { value: record.trigger_line.value, label: `觸發線 ${record.trigger_line.name}`, short: reached ? '已到' : '觸發',
        tone: '--gold', pos: 'above', ...(reached ? { strong: true } : {}) },
      { value: record.wrong_line.value, label: `看錯線 ${record.wrong_line.name}`, short: '看錯', tone: '--down', pos: 'below' },
    ];
  }

  // 成績要取哪一組：這一版；和最新一份同方法、同輸入版本（沒有最新一份時取最新的方法版本）；正式優先於試跑。
  const newerVersion = (a, b) => (a.method_version.localeCompare(b.method_version, 'en', { numeric: true })
    || a.input_version.localeCompare(b.input_version, 'en', { numeric: true })) >= 0 ? a : b;
  function scoreGroups(summary, edition, record, code) {
    const groups = (summary && summary.groups || []).filter(g => g.edition === edition);
    const all = groups.filter(g => g.scope === 'all');
    if (!all.length) return { all: [], stock: null };
    const version = record && all.some(g => g.method_version === record.method_version && g.input_version === record.input_version)
      ? record : all.reduce(newerVersion);
    const sameVersion = g => g.method_version === version.method_version && g.input_version === version.input_version;
    const mode = all.some(g => sameVersion(g) && g.run_mode === 'official') ? 'official' : 'trial';
    const pick = g => sameVersion(g) && g.run_mode === mode;
    const byHorizon = list => HORIZONS.map(h => list.find(g => g.horizon === h)).filter(Boolean);
    const stock = byHorizon(groups.filter(g => g.scope === 'stock' && g.code === code && pick(g)));
    return { all: byHorizon(all.filter(pick)), stock: stock.length ? stock : null };
  }

  // ---- 共用的小工具 ----

  const FALLBACK = { '--up': '#c8262c', '--down': '#0f8058', '--faint': '#6d6874', '--ink': '#1d1b20', '--gold': '#94681a' };
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

  async function getJson(url) {
    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { accept: 'application/json' } });
    if (!response.ok) return null;
    const body = await response.json();
    return body && body.available === true ? body : null;
  }

  // ---- 自選股清單 ----

  // 一次取回全部關注股最新一份的狀態；沒有內容或失敗時是 null（清單照原樣）。
  async function loadList() {
    try { return await getJson('/api/watch/judgments'); } catch { return null; }
  }

  function listLine(entry, ledgerDate) {
    const parts = listParts(entry, ledgerDate);
    if (!parts) return null;
    const row = el('p', { class: `wj-line is-${parts.tone}` });
    row.append(el('span', { class: 'wj-dot', 'aria-hidden': 'true' }), el('b', {}, parts.status));
    if (parts.trigger !== undefined) {
      const trig = el('span', { class: 'wj-trig' }, `${parts.verb} `);
      trig.append(el('span', { class: 'num' }, fmt(parts.trigger)));
      row.append(trig);
    }
    const record = parts.record;
    if (record && record.reconciled) {
      row.append(el('span', { class: 'wj-rec num', 'aria-label': `過去 ${record.reconciled} 次對 ${record.wins} 次` },
        `${record.wins}/${record.reconciled}`));
    }
    return row;
  }

  // ---- 個股頁 ----

  const state = {
    code: null, data: null, edition: null, view: 'judgment', root: null, chip: null, panel: null,
    onChange: null, getDaily: null, summary: undefined, picked: null, shown: HISTORY_PAGE, reached: new Set(),
  };

  const recordOf = (edition) => (state.data && state.data.latest[edition]) || null;
  const current = () => (state.edition ? recordOf(state.edition) : null);
  // 盯價用最後寫成的那一版（和觸價推播同一個規則），不跟著畫面切換的版本走。
  const newest = () => (state.data && state.data.default_edition ? recordOf(state.data.default_edition) : null);
  const isReached = () => { const record = newest(); return Boolean(record && state.reached.has(record.judgment_id)); };

  function overlays(period) {
    if (!['daily', 'weekly', 'monthly'].includes(period)) return [];
    const record = current();
    return lineSpecs(record, isReached() && record === newest()).map(({ tone, ...line }) => ({ ...line, color: color(tone) }));
  }

  function onLive(payload) {
    const record = newest();
    if (!record || state.reached.has(record.judgment_id) || !touched(record, payload)) return;
    state.reached.add(record.judgment_id);
    render();
    state.onChange && state.onChange();
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
    for (const [term, value, tone] of pairs) if (value) list.append(el('dt', {}, term), el('dd', tone ? { class: tone } : {}, value));
    return list;
  }

  const recordLabel = (record) => `${record.run_mode === 'official' ? '正式' : '試跑'} · ${EDITION[record.edition]} · 基準日 ${mmdd(record.base_date)}`;
  const cutoffsOf = (record) => [...new Set(Object.values(record.data_cutoffs || {}))].sort().map(mmdd).join('、') || '—';
  const missingOf = (record) => (record.missing_layers || []).map(layer => LAYER[layer] || layer).join('、');

  // ---- 判斷頁 ----
  function judgmentView(panel) {
    const record = current();
    if (!record) {
      panel.append(facts([['判斷', `${mmdd(state.data.base_date)} 沒有寫這一檔`]]));
      return;
    }
    if (record.kind !== 'judgment') {
      panel.append(facts([['判斷', `漏記：${MISS[record.miss_reason] || '沒有寫成'}`], ['紀錄', recordLabel(record)]]));
      return;
    }
    const reached = isReached() && record === newest();
    panel.append(facts([
      ['狀態', reached ? `${record.status} · 盤中已到觸發線` : record.status, 'is-strong'],
      ['觸發線', `${record.trigger_line.name} ${fmt(record.trigger_line.value)}`],
      ['看錯線', `${record.wrong_line.name} ${fmt(record.wrong_line.value)}`, 'is-down'],
      ['理由', record.reason],
      ['公告', (record.reason_news || []).map(item => item.title).join('；')],
      ['紀錄', recordLabel(record)],
      ['資料截止', cutoffsOf(record)],
      ['缺了', missingOf(record)],
    ]));
  }

  // ---- 成績頁（本人選 A：一次一版，兩個期間各兩條並排） ----
  function bar(label, value, tone) {
    const row = el('div', { class: `forecast-bar ${tone}` });
    const track = el('span', { class: 'forecast-bar-track' });
    const fill = el('span', { class: 'forecast-bar-fill' });
    fill.style.transform = `scaleX(${value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value))})`;
    track.append(fill);
    row.append(el('span', { class: 'forecast-bar-label' }, label), track, el('span', { class: 'forecast-bar-value num' }, pct(value)));
    return row;
  }

  function scoreView(panel) {
    if (state.summary === undefined) {
      panel.append(el('p', { class: 'forecast-note' }, '載入中'));
      loadSummary();
      return;
    }
    const { all, stock } = scoreGroups(state.summary, state.edition, current(), state.code);
    if (!all.length) {
      panel.append(el('p', { class: 'forecast-note' }, '還沒有對帳紀錄'));
      return;
    }
    for (const group of all) {
      const block = el('section', { class: 'forecast-score' });
      const head = el('header', { class: 'forecast-score-head' });
      head.append(el('strong', {}, `${group.horizon}日`), el('span', { class: 'forecast-tag' }, group.stage),
        el('span', { class: 'forecast-count num' }, `${group.reconciled} 次`));
      block.append(head, bar('贏大盤', group.win_rate, 'is-hit'), bar('隨便挑', group.random_rate, 'is-base'));
      panel.append(block);
    }
    // 還沒到期、無法判定都以轉變日計：二十日那一格還沒有結果的轉變日（五日還沒到期的一定也在裡面）。
    const longest = all[all.length - 1];
    const open = [`漏記 ${longest.misses} 次`, `還沒到期 ${longest.pending} 次`];
    if (longest.undeterminable) open.push(`無法判定 ${longest.undeterminable} 次`);
    panel.append(el('p', { class: 'forecast-note' }, `全部關注股 · ${open.join(' · ')}`));
    const twenty = stock && stock.find(g => g.horizon === 20);
    if (twenty) {
      // 單一檔樣本太少時只標階段，不給比例。
      const rate = twenty.stage === '可下結論' ? `，贏大盤 ${pct(twenty.win_rate)}` : '';
      panel.append(el('p', { class: 'forecast-note' }, `這一檔：${twenty.stage}（20 日 ${twenty.reconciled} 次${rate}）`));
    }
  }

  // ---- 回看頁 ----
  function outcomeText(outcome) {
    // 這一格還沒有對帳結果：可能是還沒到期，也可能是資料缺漏無法判定（合計數字在成績頁）；都不算看錯。
    if (!outcome) return '還沒有結果';
    const sign = (v) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
    return `${mmdd(outcome.expiry_date)} ${sign(outcome.stock_return)}，大盤 ${sign(outcome.index_return)}，${outcome.beat_index ? '贏大盤' : '沒贏'}`;
  }

  function historyView(panel) {
    const items = state.data.history.filter(item => item.kind === 'judgment' && item.edition === state.edition).reverse();
    if (!items.length) {
      panel.append(el('p', { class: 'forecast-note' }, '還沒有過去的判斷'));
      return;
    }
    const picked = items.find(item => item.judgment_id === state.picked) || items[0];
    const list = el('div', { class: 'forecast-days', role: 'group', 'aria-label': '基準日' });
    for (const item of items.slice(0, state.shown)) {
      const outcome = item.reconciliations && (item.reconciliations[20] || item.reconciliations[5]);
      const mark = item.transition ? (outcome ? (outcome.beat_index ? ' ✓' : ' ✗') : ' ·') : '';
      list.append(el('button', { type: 'button', 'aria-pressed': String(item === picked), 'data-pick': item.judgment_id,
        class: `wj-day is-${TONE[item.status]}` }, `${mmdd(item.base_date)}${mark}`));
    }
    if (items.length > state.shown) list.append(el('button', { type: 'button', class: 'forecast-more', 'data-more': '1' }, '更早'));
    panel.append(list, revisitChart(picked));
    const pairs = [
      ['狀態', `${picked.status}（${mmdd(picked.base_date)} ${EDITION[picked.edition]}）`, 'is-strong'],
      ['觸發線', `${picked.trigger_line.name} ${fmt(picked.trigger_line.value)}`],
      ['看錯線', `${picked.wrong_line.name} ${fmt(picked.wrong_line.value)}`, 'is-down'],
      ['理由', picked.reason],
    ];
    if (picked.transition) {
      for (const h of HORIZONS) {
        const outcome = picked.reconciliations && picked.reconciliations[h];
        pairs.push([`${h}日`, outcomeText(outcome)]);
      }
    }
    panel.append(facts(pairs));
  }

  // 當時的兩條線畫在當時的 K 線上：基準日前十五根到第二十個交易日（或目前最新）為止；到期收盤各畫一個點。
  function revisitChart(item) {
    const daily = (state.getDaily && state.getDaily()) || [];
    const baseIndex = daily.findIndex(row => row.t === item.base_date);
    const W = 316; const H = 150; const pad = { l: 4, r: 4, t: 10, b: 18 };
    const box = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'forecast-revisit', role: 'img',
      'aria-label': `${mmdd(item.base_date)} 的判斷與之後走勢` });
    if (baseIndex < 0) return box;
    const start = Math.max(0, baseIndex - 15);
    const rows = daily.slice(start, Math.min(daily.length, baseIndex + 21));
    const slots = Math.max(rows.length, baseIndex - start + 21);
    const outcomes = HORIZONS.map(h => item.reconciliations && item.reconciliations[h]).filter(Boolean);
    const values = rows.flatMap(row => [row.high, row.low]).concat([item.trigger_line.value, item.wrong_line.value]);
    const lo = Math.min(...values); const hi = Math.max(...values); const span = hi - lo || 1;
    const y = (v) => pad.t + (hi - v) / span * (H - pad.t - pad.b);
    const step = (W - pad.l - pad.r) / slots;
    const x = (i) => pad.l + step * (i + 0.5);
    const up = color('--up'); const down = color('--down'); const faint = color('--faint');
    rows.forEach((row, i) => {
      const tone = row.close >= row.open ? up : down;
      box.append(svg('line', { x1: x(i), x2: x(i), y1: y(row.high), y2: y(row.low), stroke: tone, 'stroke-width': 1 }));
      box.append(svg('rect', { x: x(i) - step * 0.3, width: step * 0.6, y: y(Math.max(row.open, row.close)),
        height: Math.max(1, Math.abs(y(row.open) - y(row.close))), fill: tone }));
    });
    const baseX = x(baseIndex - start);
    box.append(svg('line', { x1: baseX, x2: baseX, y1: pad.t, y2: H - pad.b, stroke: faint, 'stroke-dasharray': '2 3' }));
    // 基準日之前畫淡虛線（關卡當時已在圖上），之後畫實線（判斷之後）。
    const trigger = color('--gold');
    for (const [value, tone] of [[item.trigger_line.value, trigger], [item.wrong_line.value, down]]) {
      box.append(svg('line', { x1: pad.l, x2: baseX, y1: y(value), y2: y(value), stroke: tone, 'stroke-width': 1, 'stroke-dasharray': '3 3', opacity: 0.5 }));
      box.append(svg('line', { x1: baseX, x2: W - pad.r, y1: y(value), y2: y(value), stroke: tone, 'stroke-width': 1.5 }));
    }
    for (const outcome of outcomes) {
      const index = rows.findIndex(row => row.t === outcome.expiry_date);
      if (index >= 0) box.append(svg('circle', { cx: x(index), cy: y(rows[index].close), r: 3.5, fill: color('--ink') }));
    }
    const label = svg('text', { x: baseX + 3, y: H - 5, fill: faint, 'font-size': 10 });
    label.textContent = mmdd(item.base_date);
    box.append(label);
    return box;
  }

  async function loadSummary() {
    try { state.summary = (await getJson('/api/watch/summary'))?.summary || null; } catch { state.summary = null; }
    render();
  }

  function renderPanel(panel) {
    panel.replaceChildren(segmented('檢視', Object.entries(VIEWS), 'view', state.view));
    const editions = state.view === 'judgment' ? Object.keys(EDITION).filter(key => recordOf(key)) : Object.keys(EDITION);
    if (editions.length > 1) panel.append(segmented('版本', editions.map(key => [key, EDITION[key]]), 'edition', state.edition));
    if (state.view === 'score') return scoreView(panel);
    if (state.view === 'history') return historyView(panel);
    return judgmentView(panel);
  }

  function render() {
    const { chip, panel } = state;
    if (!chip) return;
    const record = newest();
    const reached = isReached();
    chip.classList.toggle('is-reached', reached);
    chip.classList.toggle('is-ready', Boolean(record && record.status === '可以留意'));
    chip.textContent = chipText(record, reached);
    if (!panel.hidden) renderPanel(panel);
  }

  // 手機寬度時浮板是貼底的固定面板，移到 body 底下，免得被上層的位移困住（和首頁預測同一個做法）。
  const narrow = () => window.matchMedia('(max-width: 720px)').matches;

  function setOpen(open) {
    const { chip, panel } = state;
    if (open) (narrow() ? document.body : state.root).append(panel);
    panel.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
    // 手機貼底的時候底下的頁面鎖住不捲（scroll-lock.js，本人 11-17）；桌機的小浮板掛在小鍵旁、跟著頁面走，不鎖。
    if (open && narrow()) window.ScrollLock?.lock(panel);
    else window.ScrollLock?.unlock(panel);
    if (open) renderPanel(panel);
  }

  let listening = false;
  function build(host) {
    const root = el('div', { class: 'forecast-control wj-control' });
    const chip = el('button', { type: 'button', class: 'forecast-chip wj-chip', 'aria-expanded': 'false', 'aria-controls': 'watchJudgmentPanel' });
    const panel = el('div', { class: 'forecast-pop', id: 'watchJudgmentPanel', role: 'dialog', 'aria-label': '關注判斷' });
    panel.hidden = true;
    root.append(chip, panel);
    chip.addEventListener('click', () => setOpen(panel.hidden));
    panel.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) return;
      const { edition, view, pick, more } = button.dataset;
      if (more) state.shown += HISTORY_PAGE;
      if (view) state.view = view;
      if (pick) state.picked = pick;
      if (edition) state.edition = edition;
      render();
      if (edition) state.onChange && state.onChange();
    });
    if (!listening) {
      listening = true;
      document.addEventListener('click', (event) => {
        if (!state.root) return;
        const path = event.composedPath();
        if (!path.includes(state.root) && !path.includes(state.panel)) setOpen(false);
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && state.panel && !state.panel.hidden) { setOpen(false); state.chip.focus(); }
      });
      // 開著時跨過手機／桌機寬度（拉視窗、手機轉向）：重新打開一次，浮板換到對的地方、底下頁面鎖不鎖也跟著對。
      window.matchMedia('(max-width: 720px)').addEventListener('change', () => { if (state.panel && !state.panel.hidden) setOpen(true); });
    }
    host.append(root);
    Object.assign(state, { root, chip, panel });
  }

  function unmount() {
    if (state.panel) window.ScrollLock?.unlock(state.panel);
    if (state.root) state.root.remove();
    if (state.panel) state.panel.remove();
    Object.assign(state, { code: null, data: null, edition: null, root: null, chip: null, panel: null,
      summary: undefined, picked: null, shown: HISTORY_PAGE });
  }

  // 每次掛載拿一個號碼；同一檔在回應回來之前又載入一次時，只有最後一次會建出小鍵（不會出現兩顆）。
  let mounting = 0;
  async function mount({ host, code, onChange, getDaily }) {
    const ticket = ++mounting;
    unmount();
    if (!host || !code) return;
    state.code = code;
    try {
      const data = await getJson(`/api/watch/judgments/${encodeURIComponent(code)}`);
      if (ticket !== mounting || !data || !data.base_date) return;
      if (!data.history.length) return; // 這一檔從來沒有被判斷過：個股頁和原本一樣
      state.data = data;
      state.edition = data.default_edition || (data.history[data.history.length - 1] || {}).edition || 'evening';
      state.onChange = onChange;
      state.getDaily = getDaily;
      build(host);
      render();
      onChange && onChange();
    } catch {
      // 判斷是附加功能：任何失敗都不影響個股頁其他部分。
    }
  }

  window.WatchJudgments = { loadList, listLine, mount, overlays, onLive, _logic: { listParts, touched, scoreGroups, chipText, lineSpecs } };
})();
