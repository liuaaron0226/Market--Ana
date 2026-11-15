// 大盤事前預測（規格 .scratch/forecast-ledger/spec.md，B 票第 4 片）：首頁 K 線上的轉強線與轉弱線。
// 線取自 /api/forecast/latest（預測帳本發布到資料庫的那一筆），和對帳用的是同一筆數值。
// 只有站主、而且功能開關打開時 API 才有內容；沒有內容、載入失敗或本機版沒有這個 API 時，
// 這個檔案什麼都不加，首頁和原本一模一樣。
// 對外只有 window.MarketForecast.mount({ host, onChange }) 與 overlays(period)。
(() => {
  const HORIZONS = ['1', '5', '20'];
  const ZONE = { above: '線上', between: '兩線之間', below: '線下' };
  const EDITION = { evening: '晚間版', morning: '晨間版' };
  const MISS = {
    input_not_ready: '資料沒到齊', deadline_passed: '過了期限', model_failed: '模型失敗',
    model_timeout: '模型逾時', output_garbled: '回答亂碼', answer_invalid: '回答不合格',
  };
  const MA = { 5: '五日線', 10: '十日線', 20: '月線', 60: '季線', 120: '半年線', 240: '年線' };
  const state = { latest: null, horizon: '1', edition: null, onChange: null, root: null };

  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const fmt = (value) => Number(value).toLocaleString('zh-TW', { maximumFractionDigits: 2 });
  const mmdd = (day) => (day ? `${day.slice(5, 7)}/${day.slice(8, 10)}` : '—');
  const el = (tag, attrs = {}, text) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
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
      { value: cell.strong_line.value, label: `轉強線 ${cell.strong_line.name}`, short: shortName(cell.strong_line), color: css('--up', '#ff6868'), pos: 'above' },
      { value: cell.weak_line.value, label: `轉弱線 ${cell.weak_line.name}`, short: shortName(cell.weak_line), color: css('--down', '#2fd09a'), pos: 'below' },
    ];
  }

  function chipText() {
    const record = current();
    if (record) return `預測 ${state.horizon}日`;
    return '預測 漏記';
  }

  function row(list, term, value) {
    list.append(el('dt', {}, term), el('dd', {}, value));
  }

  function renderPanel(panel) {
    panel.replaceChildren();
    const editions = Object.keys(EDITION).filter(key => state.latest.editions[key]);
    if (editions.length > 1) {
      const switcher = el('div', { class: 'forecast-seg', role: 'group', 'aria-label': '版本' });
      for (const key of editions) {
        const button = el('button', { type: 'button', 'aria-pressed': String(key === state.edition), 'data-edition': key }, EDITION[key]);
        switcher.append(button);
      }
      panel.append(switcher);
    }
    const record = current();
    const chosen = state.latest.editions[state.edition];
    if (!record) {
      const list = el('dl', { class: 'forecast-facts' });
      row(list, EDITION[state.edition] || '預測', `漏記：${MISS[chosen?.miss_reason] || '沒有寫成'}`);
      row(list, '基準日', mmdd(state.latest.base_date));
      panel.append(list);
      return;
    }
    const horizons = el('div', { class: 'forecast-seg', role: 'group', 'aria-label': '期間' });
    for (const key of HORIZONS) {
      horizons.append(el('button', { type: 'button', 'aria-pressed': String(key === state.horizon), 'data-horizon': key }, `${key}日`));
    }
    panel.append(horizons);
    const cell = record.cells[state.horizon];
    const list = el('dl', { class: 'forecast-facts' });
    row(list, '落點', ZONE[cell.zone] || cell.zone);
    const strong = el('dd', { class: 'is-up' }, `${cell.strong_line.name} ${fmt(cell.strong_line.value)}`);
    const weak = el('dd', { class: 'is-down' }, `${cell.weak_line.name} ${fmt(cell.weak_line.value)}`);
    list.append(el('dt', {}, '轉強線'), strong, el('dt', {}, '轉弱線'), weak);
    row(list, '理由', cell.reason);
    row(list, '看錯條件', cell.invalidation);
    row(list, '盤勢', record.regime);
    row(list, '紀錄', `${record.run_mode === 'official' ? '正式' : '試跑'} · ${EDITION[record.edition]} · 基準日 ${mmdd(record.base_date)}`);
    const cutoffs = record.data_cutoffs || {};
    row(list, '資料截止', [...new Set(Object.values(cutoffs))].map(mmdd).join('、') || '—');
    panel.append(list);
  }

  function render() {
    const { root } = state;
    const chip = root.querySelector('.forecast-chip');
    const panel = root.querySelector('.forecast-pop');
    chip.textContent = chipText();
    if (!panel.hidden) renderPanel(panel);
  }

  function setOpen(open) {
    const chip = state.root.querySelector('.forecast-chip');
    const panel = state.root.querySelector('.forecast-pop');
    panel.hidden = !open;
    chip.setAttribute('aria-expanded', String(open));
    if (open) renderPanel(panel);
  }

  function build(host) {
    const root = el('div', { class: 'forecast-control' });
    const chip = el('button', { type: 'button', class: 'forecast-chip', 'aria-expanded': 'false', 'aria-controls': 'forecastPanel' });
    const panel = el('div', { class: 'forecast-pop', id: 'forecastPanel', role: 'dialog', 'aria-label': '大盤預測明細' });
    panel.hidden = true;
    root.append(chip, panel);
    chip.addEventListener('click', () => setOpen(panel.hidden));
    panel.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.dataset.horizon) state.horizon = button.dataset.horizon;
      if (button.dataset.edition) state.edition = button.dataset.edition;
      render();
      state.onChange && state.onChange();
    });
    // 點選項會重畫浮板，被點的按鈕當下已不在畫面上；用事件路徑判斷是不是點在外面。
    document.addEventListener('click', (event) => { if (!event.composedPath().includes(root)) setOpen(false); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !panel.hidden) { setOpen(false); chip.focus(); } });
    host.prepend(root);
    state.root = root;
  }

  async function load() {
    const response = await fetch('/api/forecast/latest', { credentials: 'same-origin', headers: { accept: 'application/json' } });
    if (!response.ok) return null;
    const body = await response.json();
    if (!body || body.available !== true || !body.editions) return null;
    if (!Object.values(body.editions).some(Boolean)) return null;
    return body;
  }

  async function mount({ host, onChange }) {
    if (!host) return;
    try {
      const latest = await load();
      if (!latest) return;
      state.latest = latest;
      // 預設看最後寫成的那一版：晨間版有預測就看晨間版，否則看晚間版。
      state.edition = forecastOf('morning') ? 'morning' : forecastOf('evening') ? 'evening'
        : (latest.editions.morning ? 'morning' : 'evening');
      state.onChange = onChange;
      build(host);
      render();
      onChange && onChange();
    } catch {
      // 預測是附加功能：任何失敗都不影響首頁其他部分。
    }
  }

  window.MarketForecast = { mount, overlays };
})();
