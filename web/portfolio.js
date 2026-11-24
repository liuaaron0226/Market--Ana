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

  // 開放給朋友 03 票：朋友那份（/api/friend-ai/stocks，他自己的金鑰、他自己的持股）。開關關著、不是朋友、
  // 還沒設定金鑰或要重新同意時回 null：自選股頁不顯示 AI 那一行，和現在一樣。
  function friendAiDigest(payload) {
    if (!payload?.available || !payload.configured || !payload.consented) return null;
    return normalizeAiDigest(payload);
  }

  if (typeof window !== 'undefined') window.PortfolioAi = Object.freeze({ normalize: normalizeAiDigest, entryFor: aiEntryFor, friend: friendAiDigest });
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const money = new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD', maximumFractionDigits: 0 });
  const number = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 });
  const units = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 4 });   // 基金單位數常有 4 位小數
  const CURRENCY_WORDS = { USD: '美元', TWD: '台幣' };
  // watch：站主才有（其他人是 null）——已關注的代號、目前幾檔、上限。
  const state = { watchlist: [], watch: null, holdings: [], level: null, aiDigest: null, aiVisible: false };
  let features = { level_advice: false, ai_digest: false, ai_stock_advice: false, watchlist_watch_v1: false };

  async function loadReleaseFeatures() {
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const payload = response.ok ? await response.json() : null;
      if (payload?.schema === 'market-ana-features/v1' && payload.features) features = payload.features;
    } catch (_) { /* feature gate 讀取失敗時維持 default-off */ }
    $('levelPanel').hidden = !features.level_advice;
  }
  const featuresReady = loadReleaseFeatures();   // 一進頁面就發；要看功能清單的資料都等這一個（讀不到時當作功能都關著，不會丟錯）

  // 主題在設定浮板裡切換（settings.js，本人 11-17 選樣品 A）；這一頁沒有要跟著重畫的圖
  function readSavedTheme() {
    try {
      const saved = localStorage.getItem('market-theme');
      if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
    } catch (_) {}
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

  // 關注幾檔／上限：寫在每一顆關注開關旁邊，開關一動全部一起改
  function renderWatchCount() {
    if (!state.watch) return;
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
    $('levelStatus').textContent = advice.warning || (advice.missing_signals?.length ? `缺 ${advice.missing_signals.length} 項資料` : '資料已到齊');
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
      $('levelAmount').textContent = adjustment.within_tolerance ? '無需調整' : money.format(adjustment.amount_twd);
      $('levelConclusion').textContent = adjustment.within_tolerance
        ? '目前水位在 5 個百分點容忍帶內，今日無需調整。'
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
        (next <= 3 ? `第 ${next} 批${missing.length ? `尚差：${missing.join('、')}` : '條件已到齊'}。` : '三批已用完，本波沒有後手。');
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
  // 算法在 holdings-rows.js（總數、排序、橫條的兩段、每一檔與全部的賺賠）；這裡只畫，不另外算。只呈現現況，不說要調整哪一檔。
  const STILL = window.matchMedia('(prefers-reduced-motion: reduce)');
  const HOLDING_FIRST = 150, HOLDING_GAP = 300, HOLDING_PUSH = 3;   // 進場：第一檔什麼時候開始、一檔比一檔晚多少（毫秒）、被頂或壓一下的幅度（px）
  const GAIN_WORDS = { bull: '獲利', bear: '虧損', flat: '持平', none: '損益' };
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
  // ---------- 自選與庫存：一份清單（本人 2026-11-16 選「併成一份清單」，同日看過手勢版的樣品後說「照這樣做」） ----------
  // 一檔只出現一次：有庫存的那幾檔是橫條與賺賠，只在自選的寫群組。
  // 點名稱進個股頁，點那一列其他地方展開；向左滑露出修改、移出自選、刪除（swipe-rows.js）。
  // 按「自選」時照群組分段（群組是自己取名的，一檔只放一個；短期／長期另外標），點群組的名稱可以改名。
  // 資料整理在 portfolio-list.js（併清單、分段、每一列有哪些動作）與 holdings-rows.js（庫存的總數與橫條），這裡只管畫。
  const HORIZONS = [['none', '不標'], ['short', '短期'], ['long', '長期']];
  const HORIZON_WORDS = { none: '', short: '短期', long: '長期' };
  const GROUP_MAX = 12;   // 群組名稱最多幾個字；伺服器（cloud/lib/user-portfolio.js）也擋同一個數字
  const EMPTY_WORDS = {
    all: '還沒有自選或庫存。按右邊的「＋」，輸入股票代號或名稱。',
    holding: '還沒有庫存。按右邊的「＋」，輸入代號或名稱後按「新增庫存」。',
    watch: '還沒有自選股。按右邊的「＋」，輸入代號或名稱後按「加入自選」。',
  };
  const host = $('holdingRows');
  let listReady = false;     // 第一次要等庫存、自選與 AI 整理都讀到了才畫：進場只播一次，中途重畫會把它打斷
  let listShown = null;      // 畫面上現在是哪一份資料，同一份不重畫
  let listEntered = false;   // 進場的動態播過了沒
  let listFilter = 'all';    // 三顆篩選鍵選的是哪一顆
  let listModel = null;      // PortfolioList.read 的結果
  // 只在畫面上的狀態：開著哪一檔、哪一檔正在就地修改、哪一檔正在取新群組的名字、哪一個群組正在改名
  let openCode = null, editCode = null, namingCode = null, renamingGroup = null;
  let undoLast = null;       // 上面那一行的「還原」要做的事；做下一個動作就沒有了

  // 清單上面那一行：結果，以及可以還原時多一顆「還原」
  function say(message, kind, undo) {
    setStatus($('listStatus'), message, kind);
    undoLast = undo || null;
    if (!undo) return;
    const button = element('button', 'pf-undo', '還原');
    button.type = 'button';
    $('listStatus').append(button);
  }
  $('listStatus').addEventListener('click', async event => {
    const button = event.target.closest('.pf-undo'), undo = undoLast;
    if (!button || !undo) return;
    undoLast = null;
    button.disabled = true;
    try {
      const { code, note } = await undo();
      say(note || '已還原。', note ? 'error' : 'success');
      (rowOf(code)?.querySelector('.hv-open') || $('addToggle')).focus();
    } catch (error) {
      // 沒還原成：「還原」留著，可以再按一次
      say(error.message, 'error', undo);
      $('listStatus').querySelector('.pf-undo')?.focus();
    }
  });

  function renderTotals(summary) {
    $('totalLine').hidden = !summary.count;
    $('totalMarketValue').textContent = summary.complete ? money.format(summary.value) : '—';
    $('totalCost').textContent = summary.count ? money.format(summary.cost) : '—';
    $('totalWord').textContent = GAIN_WORDS[summary.state];
    $('totalPnl').textContent = summary.pnl === null ? '—' : signedMoney(summary.pnl);
    $('totalReturn').textContent = summary.returnPct === null ? '—' : signedPercent(summary.returnPct);
    for (const id of ['totalPnl', 'totalReturn']) $(id).className = `num ${GAIN_TONES[summary.state] || ''}`.trim();
  }

  const rowOf = code => (code ? host.querySelector(`.hv-row[data-code="${CSS.escape(code)}"]`) : null);
  const entryOf = code => listModel?.rows.find(entry => entry.code === code);
  const visibleIndex = row => [...host.querySelectorAll('.hv-row')].indexOf(row);
  // 刪掉一列之後：焦點放到補上來的那一列（沒有了就放到「＋」），不要掉回頁面最上面
  function focusAfterRemoval(at) {
    const opens = [...host.querySelectorAll('.hv-open')];
    (opens[Math.min(at, opens.length - 1)] || $('addToggle')).focus();
  }
  // 下一次讀回來就算資料一樣也重畫（畫面上的狀態變了，例如修改的表單要收起來）
  const redrawNext = () => { listShown = null; };
  // 基金（07 票）送單位數＋台幣總成本（照基富通對帳單抄）；股票送股數＋平均成本
  // 庫存兩格各叫什麼、收什麼：股票是股數（整數）與平均成本；基金是單位數（可小數）與台幣總成本。
  // 上下限和伺服器（cloud/lib/user-portfolio.js）擋的一樣
  const HOLDING_FIELDS = {
    stock: { units: { label: '股數', name: 'shares', attributes: { inputmode: 'numeric', min: '1', max: '1000000000', step: '1' } },
      cost: { label: '平均成本', name: 'average_cost', attributes: { inputmode: 'decimal', min: '0.01', max: '10000000', step: 'any' } } },
    fund: { units: { label: '單位數', name: 'shares', attributes: { inputmode: 'decimal', min: '0.0001', max: '1000000000', step: 'any' } },
      cost: { label: '台幣總成本', name: 'total_cost_twd', attributes: { inputmode: 'decimal', min: '0.01', max: '1000000000000', step: 'any' } } },
  };
  const holdingKind = item => (item?.kind === 'fund' ? 'fund' : 'stock');
  const saveHolding = item => {
    const { cost } = HOLDING_FIELDS[holdingKind(item)];
    return api('/api/holdings', { method: 'POST', body: { code: item.code, shares: item.shares, [cost.name]: item[cost.name], note: item.note || '' } });
  };
  const reloadHoldings = () => Promise.all([loadHoldings(), ...(features.level_advice ? [loadLevel()] : [])]);
  // 「還原」要做的事：把庫存存回原本的那一筆
  const restoreHolding = before => async () => { await saveHolding(before); await reloadHoldings(); return { code: before.code }; };
  // 向左滑露出的動作各叫什麼（有哪些、怎麼排由 PortfolioList.actions 決定）。鍵上的字：這一列只有一邊時只寫前兩個字（刪除、移出），兩邊都有才寫清楚是哪一邊
  const ACT_NAMES = { edit: '修改庫存', unwatch: '移出自選', delete: '刪除庫存' };
  const actWords = (kind, both) => (kind === 'edit' ? ['修改'] : both ? [ACT_NAMES[kind].slice(0, 2), ACT_NAMES[kind].slice(2)] : [ACT_NAMES[kind].slice(0, 2)]);

  // 向左滑露出的動作：修改（就地改）、移出自選、刪除庫存。刪除與移出不再多問一次，做完上面一行可以還原。
  async function runAct(button, row) {
    const code = row.dataset.code, entry = entryOf(code), kind = button.dataset.act;
    if (!entry) return;
    swipe.close();
    if (kind === 'edit') {
      openCode = editCode = code;
      namingCode = null;
      drawList();
      rowOf(code)?.querySelector('.pf-edit [name="shares"]')?.focus();
      return;
    }
    const at = visibleIndex(row);
    say('');
    try {
      if (kind === 'delete') {
        const before = entry.holding.item;
        await api(`/api/holdings/${encodeURIComponent(code)}`, { method: 'DELETE', body: {} });
        if (editCode === code) editCode = null;
        redrawNext();
        await reloadHoldings();
        say(`已刪除 ${entry.name} 的庫存。`, 'success', restoreHolding(before));
      } else {
        const before = entry.watch, watching = Boolean(state.watch?.codes.has(code));
        await api(`/api/watchlist/${encodeURIComponent(code)}`, { method: 'DELETE', body: {} });
        if (namingCode === code) namingCode = null;
        redrawNext();
        await loadWatchlist();
        say(`已把 ${entry.name} 移出自選。`, 'success', async () => {
          // 帶著原本加入的時間放回去：清單與群組照加入的先後排，放回去才會在原位
          await api('/api/watchlist', { method: 'POST', body: { code, horizon: before.horizon, group: window.PortfolioList.groupOf(before), created_at: before.created_at } });
          // 原本有關注的補回去；補不回去（例如這段時間關注滿了、或這一檔現在不能關注）照樣算放回去了，但要說清楚
          let note = '';
          if (watching) await api(`/api/watch/${encodeURIComponent(code)}`, { method: 'POST', body: {} }).catch(() => { note = `已把 ${entry.name} 放回自選，但關注沒有補回去。`; });
          await loadWatchlist();
          return { code, note };
        });
      }
      const still = rowOf(code);
      if (still) still.querySelector('.hv-open').focus();
      else focusAfterRemoval(at);
    } catch (error) {
      say(error.message, 'error');
      rowOf(code)?.querySelector('.hv-open')?.focus();   // 沒做成：焦點回到那一列（滑開的動作已經收回去了）
    }
  }
  const swipe = window.SwipeRows.bind(host, { onAct: runAct });

  // 點開之後的自選設定：群組小鍵（選現有的或取新的）、短期／長期三選一（按了就存）、關注開關（站主才有）
  function watchSettings(entry, li) {
    const listed = entry.watch, set = element('div', 'pf-set');   // listed：自選的那一筆（state.watch 是站主的關注狀態，不是同一件事）
    const group = window.PortfolioList.groupOf(listed);
    if (namingCode === entry.code) {
      const form = element('form', 'pf-new-group');
      const input = element('input');
      input.maxLength = GROUP_MAX;
      input.placeholder = '群組名稱';
      input.setAttribute('aria-label', `${entry.name} 的新群組名稱`);
      input.setAttribute('enterkeyhint', 'done');
      const ok = element('button', '', '確定');
      ok.type = 'submit';
      form.append(input, ok);
      set.append(form);
    } else {
      const pop = element('span', 'pf-pop'), chip = element('button', 'chart-chip pf-group-chip'), menu = element('div', 'chart-menu pf-group-menu');
      chip.type = 'button';
      chip.setAttribute('aria-expanded', 'false');
      chip.setAttribute('aria-controls', `groupMenu-${entry.code}`);
      chip.setAttribute('aria-label', `${entry.name} 的群組：${group || '未分組'}`);
      chip.append('群組 ', element('b', '', group || '未分組'));
      menu.id = `groupMenu-${entry.code}`;
      menu.setAttribute('role', 'radiogroup');
      menu.setAttribute('aria-label', `${entry.name} 的群組`);
      menu.hidden = true;
      menu.setAttribute('data-menu-autoclose', '');
      [...listModel.groups, ''].forEach(name => {
        const pick = element('button', '', name || '未分組');
        pick.type = 'button';
        pick.setAttribute('role', 'radio');
        pick.setAttribute('aria-checked', String(name === group));
        pick.dataset.group = name;
        menu.append(pick);
      });
      const fresh = element('button', 'pf-group-new', '＋ 新群組');
      fresh.type = 'button';
      menu.append(element('hr'), fresh);
      pop.append(chip, menu);
      set.append(pop);
    }
    const strip = element('div', 'key-strip pf-horizon');
    strip.setAttribute('role', 'group');
    strip.setAttribute('aria-label', `${entry.code} 短期或長期`);
    HORIZONS.forEach(([value, label]) => {
      const cell = element('button', 'cell');
      cell.type = 'button';
      cell.dataset.horizon = value;
      cell.setAttribute('aria-pressed', String(listed.horizon === value));
      cell.append(element('span', 'cell-face'), element('span', 'cell-name', label));
      strip.append(cell);
    });
    set.append(strip);
    // 關注（到價判斷）只有上市櫃股票有；基金的關注不在這次範圍（基金當成一般個股規格 Out of Scope），不放開關
    if (state.watch && listed.market !== '境外基金') {
      const toggle = watchToggle(listed, li);
      toggle.append(element('small', 'pf-watch-count num', `${state.watch.count}／${state.watch.limit}`));
      set.append(toggle);
    }
    return set;
  }

  async function changeHorizon(cell) {
    const strip = cell.parentElement;
    if (cell.getAttribute('aria-pressed') === 'true' || strip.dataset.busy) return;
    const row = cell.closest('.hv-row'), code = row.dataset.code;
    const before = strip.querySelector('[aria-pressed="true"]');
    const press = chosen => strip.querySelectorAll('.cell').forEach(other => other.setAttribute('aria-pressed', String(other === chosen)));
    press(cell);
    strip.dataset.busy = '1';   // 這一筆還沒存完，先不收下一下
    rowNote(row, '');
    say('');
    try {
      // 不帶群組：伺服器保留原本的群組
      await api('/api/watchlist', { method: 'POST', body: { code, horizon: cell.dataset.horizon } });
    } catch (error) {
      press(before);
      delete strip.dataset.busy;
      rowNote(row, error.message);
      return;
    }
    say(`${entryOf(code)?.name || code} 已${cell.dataset.horizon === 'none' ? '不再標短期或長期' : `標成${cell.textContent}`}。`, 'success');
    try { await loadWatchlist(); } catch (error) { say(error.message, 'error'); }
    delete strip.dataset.busy;
    // 重畫之後原本那一顆不在頁面上了：焦點放回新畫的、選中的那一顆
    if (!cell.isConnected) rowOf(code)?.querySelector('.pf-horizon [aria-pressed="true"]')?.focus();
  }

  // 把一檔放進某個群組（空字串是改回未分組）。只送群組：短期／長期伺服器保留原本的。回傳有沒有存成
  async function moveToGroup(code, group) {
    const entry = entryOf(code), row = rowOf(code);
    if (!entry?.watch) return false;
    rowNote(row, '');
    say('');
    try {
      await api('/api/watchlist', { method: 'POST', body: { code, group } });
    } catch (error) {
      rowNote(row, error.message);
      return false;
    }
    namingCode = null;
    redrawNext();
    say(group ? `${entry.name} 放進「${group}」。` : `${entry.name} 改成未分組。`, 'success');
    try { await loadWatchlist(); } catch (error) { say(error.message, 'error'); }
    rowOf(code)?.querySelector('.pf-group-chip')?.focus();
    return true;
  }

  // 收掉兩個就地的輸入框（不改）。只把那一小塊換回來，不重畫整份清單：輸入框是因為按了別處才離開焦點的，
  // 整份重畫會把正被按著的那顆鍵換掉，那一下就落空了。refocus：用鍵盤收掉的（Esc、沒改就按 Enter）把焦點放回去
  function closeRename(refocus) {
    const form = host.querySelector('.pf-rename'), from = renamingGroup;
    renamingGroup = null;
    const section = window.PortfolioList.sections(listModel, listFilter).find(item => item.titled && item.group === from);
    if (!form || !section) return;
    const heading = groupHeading(section);
    form.closest('.pf-group').replaceWith(heading);
    if (refocus) heading.querySelector('.pf-group-name').focus();
  }
  function closeNaming(refocus) {
    const row = rowOf(namingCode), entry = entryOf(namingCode);
    namingCode = null;
    const before = row?.querySelector('.pf-set');
    if (!before || !entry?.watch) return;
    const fresh = watchSettings(entry, row);
    before.replaceWith(fresh);
    if (refocus) fresh.querySelector('.pf-group-chip').focus();
  }

  // 群組改名：這個群組的每一檔一起改。改成已經有的名字就是併進去
  async function renameGroup(form) {
    const from = form.dataset.from, to = form.querySelector('input').value.trim();
    if (form.dataset.busy) return;
    if (!to || to === from) { closeRename(true); return; }
    form.dataset.busy = '1';   // 還在存：這時候輸入框離開焦點不算取消
    try {
      await api('/api/watchlist/groups', { method: 'POST', body: { from, to } });
    } catch (error) {
      say(error.message, 'error');
      delete form.dataset.busy;
      if (form.isConnected) form.querySelector('input').focus();
      return;
    }
    renamingGroup = null;
    redrawNext();
    say(`「${from}」改名為「${to}」。`, 'success');
    try { await loadWatchlist(); } catch (error) { say(error.message, 'error'); }
    host.querySelector(`.pf-group-name[data-group="${CSS.escape(to)}"]`)?.focus();
  }

  // 就地修改庫存：股數、平均成本、備註（基金是單位數、台幣總成本、備註）。同一個代號再存一次就是更新
  function editForm(entry) {
    const item = entry.holding.item, form = element('form', 'pf-form pf-edit'), kind = holdingKind(item);
    form.dataset.code = entry.code;
    const field = (label, name, attributes, value, wide) => {
      const wrap = element('label', wide ? 'pf-wide' : '', label), input = element('input');
      input.name = name;
      Object.entries(attributes).forEach(([key, text]) => input.setAttribute(key, text));
      input.value = value;
      wrap.append(input);
      return wrap;
    };
    const save = element('button', '', '儲存'), cancel = element('button', 'pf-ghost pf-edit-cancel', '取消');
    save.type = 'submit';
    cancel.type = 'button';
    form.append(
      ...[HOLDING_FIELDS[kind].units, HOLDING_FIELDS[kind].cost].map(({ label, name, attributes }) => field(label, name, { type: 'number', ...attributes, required: '' }, String(item[name]))),
      field('備註', 'note', { maxlength: '200', placeholder: '選填，最多 200 字' }, item.note || '', true),
      save, cancel,
    );
    return form;
  }

  async function saveEdit(form) {
    const code = form.dataset.code, entry = entryOf(code), row = form.closest('.hv-row');
    if (!entry?.holding) return;
    const before = entry.holding.item, button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    rowNote(row, '');
    say('');
    try {
      const kind = holdingKind(before), { cost } = HOLDING_FIELDS[kind];
      await saveHolding({ code, kind, shares: Number(form.elements.shares.value), [cost.name]: Number(form.elements[cost.name].value), note: form.elements.note.value.trim() });
    } catch (error) {
      rowNote(row, error.message);
      button.disabled = false;
      return;
    }
    editCode = null;
    redrawNext();
    try {
      await reloadHoldings();
      say(`已更新 ${entry.name} 的庫存。`, 'success', restoreHolding(before));
    } catch (error) { say(error.message, 'error'); }
    rowOf(code)?.querySelector('.hv-open')?.focus();
  }

  function listRow(entry, order, complete, titled) {
    const held = entry.holding, watch = entry.watch, item = held ? held.item : watch;
    const priced = Boolean(held) && held.state !== 'none', word = held ? GAIN_WORDS[held.state] : '', tone = held ? GAIN_TONES[held.state] || '' : '';
    const group = window.PortfolioList.groupOf(watch), horizon = watch ? HORIZON_WORDS[watch.horizon] : '';
    const editing = editCode === entry.code;
    const li = element('li', `hv-row depth-row on-page swipe-row${held ? '' : ' pf-only-watch'}`);
    li.dataset.code = entry.code;
    li.dataset.kinds = entry.kinds.join(' ');
    li.dataset.state = held ? held.state : 'none';
    li.style.setProperty('--at', `${HOLDING_FIRST + order * HOLDING_GAP}ms`);
    const panelId = `holdingPanel-${entry.code}`;

    // 滑開才看得到的動作
    const acts = element('div', 'swipe-acts');
    acts.inert = true;
    window.PortfolioList.actions(entry).forEach((kind, index, kinds) => {
      // 最右邊那一顆是把這一檔拿掉的（刪除庫存；只在自選的是移出），紅色
      const button = element('button', `swipe-act${kind !== 'edit' && index === kinds.length - 1 ? ' is-danger' : ''}`);
      button.type = 'button';
      button.dataset.act = kind;
      button.setAttribute('aria-label', `${ACT_NAMES[kind]} ${entry.code} ${entry.name}`);
      actWords(kind, Boolean(held && watch)).forEach(text => button.append(element('span', '', text)));
      acts.append(button);
    });

    // 上面那一條：名稱（連到個股頁）疊在鋪滿整條的展開鍵上面
    const face = element('div', 'hv-top swipe-face');
    const name = element(entry.inUniverse ? 'a' : 'span', 'hv-name'), under = element('span', '', entry.code);
    if (entry.inUniverse) {
      name.href = `stock.html?code=${encodeURIComponent(entry.code)}`;
      name.draggable = false;
      name.setAttribute('aria-label', `${entry.name} ${entry.code} 的個股頁`);
    }
    // 名稱下面的小字：有庫存的那幾列寫群組（沒有群組寫「自選」），照群組分段時群組已經在標題上，改寫短期／長期；只在自選的寫短期／長期
    const mark = !watch ? '' : held && !titled ? group || '自選' : horizon;
    if (mark) under.append(element('em', '', mark));
    name.append(element('strong', '', entry.name), under);
    const open = element('button', 'hv-open');
    open.type = 'button';
    open.setAttribute('aria-expanded', 'false');
    open.setAttribute('aria-controls', panelId);
    const listedAs = watch ? `自選${group ? `「${group}」` : ''}${horizon ? ` ${horizon}` : ''}` : '';
    const said = !held ? listedAs
      : priced ? `${complete ? `占庫存 ${held.share.toFixed(1)}%，` : ''}${word}${held.gain && Number.isFinite(item.return_pct) ? ` ${signedPercent(item.return_pct)}` : ''}${watch ? `，${listedAs}` : ''}`
        : `沒有價格${watch ? `，${listedAs}` : ''}`;
    open.setAttribute('aria-label', `${entry.name} ${entry.code} 的明細：${said}`);
    const chev = element('span', 'chev');
    chev.setAttribute('aria-hidden', 'true');
    const more = element('button', 'swipe-more', '⋯');
    more.type = 'button';
    more.setAttribute('aria-expanded', 'false');
    more.setAttribute('aria-label', `${entry.name} 的動作`);
    if (held) {
      const bar = element('span', 'hv-bar');
      bar.setAttribute('aria-hidden', 'true');
      if (priced) {
        const segment = (kind, width) => {
          const part = element('i', kind);
          part.style.setProperty('--w', width.toFixed(2));
          bar.append(part);
        };
        segment('base', held.base);
        if (held.edgeKind) segment(held.edgeKind, held.edge);
      }
      face.append(name, open, bar, element('span', `hv-ret num ${tone}`.trim(), priced ? returnText(item) : '—'), chev, more);
    } else face.append(name, open, element('span', 'pf-tag', titled ? '' : group), chev, more);
    // 關注中的那幾檔：名稱下面多一行判斷（狀態點＋狀態、觸發價、對了幾次；自選股關注 E 票，本人選 A）。只有站主、功能打開時才有
    const judgments = state.watch?.judgments;
    const judgment = watch && judgments && state.watch.codes.has(entry.code) && window.WatchJudgments
      ? window.WatchJudgments.listLine(judgments.items[entry.code], judgments.base_date) : null;
    if (judgment) { li.classList.add('has-judgment'); face.append(judgment); }
    const clipTop = element('div', 'swipe-clip');
    clipTop.append(acts, face);

    const panel = element('div', 'hv-panel depth-panel');
    panel.id = panelId;
    panel.inert = true;
    const clip = element('div', 'hv-clip'), inner = element('div', 'hv-inner');
    if (held && editing) inner.append(editForm(entry));
    else if (held) {
      const facts = element('ul', 'hv-facts'), fund = holdingKind(item) === 'fund';
      [
        [window.HoldingsRows.valueLabel(item), priced ? money.format(item.market_value) : '—', ''],
        [word, priced ? signedMoney(held.gain) : '—', tone],
        fund ? ['單位數・台幣成本', `${units.format(item.shares)}・${money.format(item.total_cost_twd)}`, '']
          : ['股數・均價', `${number.format(item.shares)}・${number.format(item.average_cost)}`, ''],
      ].forEach(([label, text, factTone], index) => {
        const fact = element('li');
        fact.style.setProperty('--order', String(index));   // 展開時一格一格浮出來
        fact.append(element('span', '', label), element('b', factTone, text));
        facts.append(fact);
      });
      inner.append(facts);
      // 基金的現值怎麼來的：淨值（原幣）×匯率，各自的日期；不含已領的配息（本人 11-18 同意）
      if (fund) {
        inner.append(element('p', 'hv-note', Number.isFinite(item.nav) && Number.isFinite(item.fx_rate)
          ? `淨值 ${item.nav} ${CURRENCY_WORDS[item.currency] || item.currency || ''}（${item.nav_date}）× 匯率 ${item.fx_rate}（${item.fx_date}），不含已領配息`
          : '淨值或匯率還沒有，暫時算不出現值'));
      }
      if (item.note) inner.append(element('p', 'hv-note', `備註：${item.note}`));
    }
    if (watch && !editing) inner.append(watchSettings(entry, li));
    if (!entry.inUniverse) inner.append(element('span', 'pf-scope outside', '不在本站分析範圍，沒有個股頁'));
    // 這一列的訊息區在畫出來時就放好（空的），之後只改文字：輔助工具才會念出來
    const note = element('p', 'pf-row-note');
    note.setAttribute('role', 'status');
    note.hidden = true;
    inner.append(note);
    const digest = stockAiDigest(entry.code);
    if (digest) inner.append(digest);
    clip.append(inner);
    panel.append(clip);
    li.append(clipTop, panel);
    return li;
  }

  // 按「自選」時每一段的標題：群組的名稱（點了就地改名）與檔數；未分組不能改名
  function groupHeading(section) {
    const li = element('li', 'pf-group');
    if (!section.group) li.append(element('span', 'pf-group-word', '未分組'));
    else if (renamingGroup === section.group) {
      const form = element('form', 'pf-rename'), input = element('input');
      form.dataset.from = section.group;
      input.maxLength = GROUP_MAX;
      input.value = section.group;
      input.setAttribute('aria-label', `「${section.group}」的新名稱`);
      input.setAttribute('enterkeyhint', 'done');
      form.append(input);
      li.append(form);
    } else {
      const button = element('button', 'pf-group-name', section.group);
      button.type = 'button';
      button.dataset.group = section.group;
      button.setAttribute('aria-label', `群組「${section.group}」，${section.rows.length} 檔。按了改名稱`);
      li.append(button);
    }
    li.append(element('small', 'num', String(section.rows.length)));
    return li;
  }

  // 收起來的那一列，裡面的設定鍵盤走不到。展開完之後（is-settled）群組的選單才可以超出這一列的框；
  // instant：重畫時直接畫成開著的，不播展開
  function setRowOpen(row, open, instant) {
    const settled = open && (instant || STILL.matches);
    row.classList.toggle('is-open', open);
    row.classList.toggle('is-settled', Boolean(settled));
    row.querySelector('.hv-open').setAttribute('aria-expanded', String(open));
    row.querySelector('.hv-panel').inert = !open;
  }
  host.addEventListener('transitionend', event => {
    if (event.propertyName !== 'grid-template-rows' || !event.target.matches('.hv-panel')) return;
    const row = event.target.closest('.hv-row');
    row.classList.toggle('is-settled', row.classList.contains('is-open'));
  });

  // 進場（只播一次）。時間都寫在樣式裡（portfolio.css），這裡只跟著樣式的動畫走，不另外記一份時間：
  // 賺的（或還差的）那一段開始長，這一列被往上頂（或往下壓）一下；最後一列定位完，把進場的記號拿掉
  let stopListEntrance = () => {};
  function playListEntrance() {
    const onStart = event => {
      if (!event.target.matches('.hv-bar .gain, .hv-bar .lack')) return;
      const row = event.target.closest('.hv-row');
      row.animate({ translate: ['0 0', `0 ${row.dataset.state === 'bull' ? -HOLDING_PUSH : HOLDING_PUSH}px`, '0 0'], offset: [0, .3, 1] }, { duration: 260, delay: 120, easing: 'ease-out' });
    };
    stopListEntrance = () => {
      host.classList.remove('is-entering');
      host.removeEventListener('animationstart', onStart);
      host.removeEventListener('animationend', onEnd);
      stopListEntrance = () => {};
    };
    const onEnd = event => { if (event.animationName === 'hv-row-settle' && event.target === host.lastElementChild) stopListEntrance(); };
    host.addEventListener('animationstart', onStart);
    host.addEventListener('animationend', onEnd);
    host.classList.add('is-entering');
  }

  // 照現在的篩選與畫面上的狀態把清單畫出來。一列都沒有時寫一句怎麼新增
  function drawList() {
    if (!listModel) return;
    stopListEntrance();   // 重畫就不再播進場；新的列直接在定位
    const nodes = [];
    let order = 0;
    window.PortfolioList.sections(listModel, listFilter).forEach(section => {
      if (section.titled) nodes.push(groupHeading(section));
      section.rows.forEach(entry => nodes.push(listRow(entry, order++, listModel.summary.complete, section.titled)));
    });
    if (!nodes.length) nodes.push(element('li', 'hv-empty pf-none', EMPTY_WORDS[listModel.rows.length ? listFilter : 'all']));
    host.replaceChildren(...nodes);
    const again = rowOf(openCode);
    if (again) setRowOpen(again, true, true);
    else openCode = editCode = namingCode = null;   // 開著的那一檔不在這個篩選裡（或已經刪掉）
    if (renamingGroup && !host.querySelector('.pf-rename')) renamingGroup = null;
  }

  function renderList() {
    if (!listReady) return;
    // 同一份資料（含關注的名單與 AI 整理的內容）不重畫，開著的那一檔與正在播的進場才不會被打斷
    const watch = state.watch ? [[...state.watch.codes], state.watch.limit, [...state.watch.unwatchable]] : null;
    const key = JSON.stringify([state.holdings, state.watchlist, watch, state.aiVisible, state.aiDigest?.dataDate || null, state.aiVisible ? [...(state.aiDigest?.byCode || [])] : null]);
    if (key === listShown) return;
    listShown = key;
    listModel = window.PortfolioList.read(state.holdings, state.watchlist);
    renderTotals(listModel.summary);
    $('countAll').textContent = String(listModel.counts.all);
    $('countHolding').textContent = String(listModel.counts.holding);
    $('countWatch').textContent = String(listModel.counts.watch);
    drawList();
    if (listEntered) return;
    listEntered = true;   // 進場只在進頁面時播；一開始是空的，之後新增第一檔也不播
    if (listModel.rows.length && !STILL.matches) playListEntrance();
  }

  // 清單裡的點擊：點一檔展開（一次只開一檔）；點開之後的群組、短期／長期、修改的取消；群組的名稱
  host.addEventListener('click', event => {
    const target = event.target, row = target.closest('.hv-row');
    const cell = target.closest('.pf-horizon .cell');
    if (cell) { changeHorizon(cell); return; }
    const pick = target.closest('.pf-group-menu [data-group]');
    if (pick) { moveToGroup(row.dataset.code, pick.dataset.group); return; }
    if (target.closest('.pf-group-new')) {
      namingCode = row.dataset.code;
      drawList();
      rowOf(namingCode)?.querySelector('.pf-new-group input')?.focus();
      return;
    }
    if (target.closest('.pf-edit-cancel')) {
      editCode = null;
      drawList();
      rowOf(row.dataset.code)?.querySelector('.hv-open')?.focus();
      return;
    }
    const title = target.closest('.pf-group-name');
    if (title) {
      renamingGroup = title.dataset.group;
      drawList();
      const input = host.querySelector('.pf-rename input');
      input.focus();
      input.select();
      return;
    }
    const open = target.closest('.hv-open');
    if (!open) return;
    const opening = openCode !== row.dataset.code, hadForm = editCode || namingCode;
    openCode = opening ? row.dataset.code : null;
    editCode = namingCode = null;
    if (hadForm) { drawList(); rowOf(row.dataset.code)?.querySelector('.hv-open')?.focus(); return; }   // 表單要收掉：整份重畫
    host.querySelectorAll('.hv-row').forEach(other => setRowOpen(other, other === row && opening));
  });
  host.addEventListener('submit', event => {
    event.preventDefault();
    const form = event.target;
    if (form.matches('.pf-edit')) saveEdit(form);
    else if (form.matches('.pf-new-group')) {
      const name = form.querySelector('input').value.trim(), code = form.closest('.hv-row').dataset.code;
      if (form.dataset.busy) return;
      if (!name) { closeNaming(true); return; }
      form.dataset.busy = '1';   // 還在存：這時候輸入框離開焦點不算取消
      moveToGroup(code, name).then(saved => { if (!saved) delete form.dataset.busy; });
    } else if (form.matches('.pf-rename')) renameGroup(form);
  });
  // 兩個就地的輸入框（群組改名、取新群組的名字）：按 Enter（或「確定」）才算數；離開焦點或按 Esc 是不改（照本人看過的樣品）
  // iPhone Safari 按按鈕不會把焦點移過去（relatedTarget 是空的）：按「確定」時輸入框先離開焦點，
  // 原本會被當成「按了別處」收掉，確定就落空了——本人 11-18「新增群組卻沒有顯示出來，一樣顯示未分組」。
  // 手指或滑鼠是按在這個表單裡面的，就不算離開。
  host.addEventListener('pointerdown', event => {
    const form = event.target.closest('.pf-rename, .pf-new-group');
    if (form) form.dataset.pressing = '1';
  });
  host.addEventListener('pointerup', () => setTimeout(() => host.querySelectorAll('[data-pressing]').forEach(form => delete form.dataset.pressing), 400));
  host.addEventListener('focusout', event => {
    const form = event.target.closest('.pf-rename, .pf-new-group');
    if (!form || !form.isConnected || form.dataset.busy || form.dataset.pressing || form.contains(event.relatedTarget)) return;
    if (form.matches('.pf-rename')) closeRename(false);
    else closeNaming(false);
  });
  host.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !event.target.matches('.pf-rename input, .pf-new-group input')) return;
    if (event.target.closest('.pf-rename')) closeRename(true);
    else closeNaming(true);
  });

  function setFilter(value) {
    listFilter = value;
    $('listFilter').querySelectorAll('button').forEach(key => key.setAttribute('aria-pressed', String(key.dataset.filter === value)));
    renamingGroup = null;
    drawList();
  }
  $('listFilter').addEventListener('click', event => {
    const chosen = event.target.closest('button[data-filter]');
    if (!chosen) return;
    if (!undoLast) say('');   // 還可以還原的時候留著：刪完想換一顆鍵看看再決定要不要還原
    setFilter(chosen.dataset.filter);
  });
  // 返回原處（nav-return.js，本人 2026-11-24）：從清單點進個股頁、按返回回來時，照原本的篩選（全部／庫存／自選，自選照群組分段）
  // 與展開的那一檔畫出來；整頁捲回原處由 nav-return.js 在清單畫好之後做
  window.NavReturn?.remember('list', {
    save: () => (listModel ? { filter: listFilter, open: openCode || '' } : null),
    anchor: () => host,
    restore: async saved => {
      if (!(await window.NavReturn.until(() => listReady && listModel))) return;
      if (saved.open && listModel.rows.some(entry => entry.code === saved.open)) openCode = saved.open;
      setFilter(['all', 'holding', 'watch'].includes(saved.filter) ? saved.filter : listFilter);
    },
  });
  // 新增了某一種之後，目前的篩選如果看不到它就切回全部
  const showKind = kind => { if (listFilter !== 'all' && listFilter !== kind) setFilter('all'); };

  // 「＋」：平常收著，按了才展開。一格代號、兩顆鍵：「加入自選」直接加；「新增庫存」才多出股數、平均成本、備註
  function setAddMore(open) {
    $('addMore').hidden = !open;
    $('addHolding').setAttribute('aria-expanded', String(open));
  }
  function setAddOpen(open) {
    $('addToggle').setAttribute('aria-expanded', String(open));
    $('addPanel').classList.toggle('is-open', open);
    $('addClip').inert = !open;
    if (!open) setAddMore(false);
  }
  $('addToggle').addEventListener('click', () => {
    const open = $('addToggle').getAttribute('aria-expanded') !== 'true';
    if (open) { say(''); setStatus($('addStatus'), ''); }
    setAddOpen(open);
  });
  $('addHolding').addEventListener('click', () => {
    const open = $('addMore').hidden;
    setAddMore(open);
    if (open && $('addCode').value.trim()) $('holdingShares').focus();
  });

  async function loadWatch() {
    let watch = null;
    await featuresReady;
    if (features.watchlist_watch_v1) {
      try {
        // 關注名單與每一檔最新一份判斷（watch-judgments.js；E 票）一起讀，不排隊。判斷讀不到時是 null，清單照原樣。
        const [payload, judgments] = await Promise.all([api('/api/watch'), window.WatchJudgments ? window.WatchJudgments.loadList() : null]);
        if (payload?.available) {
          watch = {
            count: payload.count, limit: payload.limit,
            codes: new Set((payload.items || []).map(item => item.code)),
            unwatchable: new Set(payload.unwatchable || []),
            judgments,
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
    if (listReady) showFreshness();
  }

  // 價格是哪一天、哪一種（holdings-rows.js priceLine）。13:30 剛過、收盤價還沒確認時，15 秒後再讀一次（和個股頁一樣），
  // 庫存與水位一起讀，兩邊的價才一致。最多再讀 4 次（上游一直失敗就停，等使用者自己重新整理）；正在改庫存時先不讀，免得表單被重畫。
  const PENDING_RELOADS = 4;
  let pendingReload = null, pendingTries = 0;
  function showFreshness() {
    $('portfolioFreshness').textContent = window.HoldingsRows.priceLine(state.holdings);
    const pending = state.holdings.some(item => item.price_session === 'close_pending');
    if (!pending) pendingTries = 0;
    if (!pending || pendingReload !== null || pendingTries >= PENDING_RELOADS) return;
    const retry = () => {
      if (editCode !== null) { pendingReload = setTimeout(retry, 15_000); return; }
      pendingReload = null;
      pendingTries += 1;
      reloadHoldings().catch(() => {});
    };
    pendingReload = setTimeout(retry, 15_000);
  }

  async function loadFriendAiDigest() {
    try {
      state.aiDigest = friendAiDigest(await api('/api/friend-ai/stocks'));
    } catch (_) {
      state.aiDigest = null; // 站主（403）、未核准、讀不到：不顯示
    }
    state.aiVisible = Boolean(state.aiDigest);
  }

  async function loadAiDigest() {
    // 站主那份（agy）只給站主；其他人的功能清單裡 ai_digest 一律是關的，改讀自己那份。
    if (!(features.ai_digest && features.ai_stock_advice)) { await loadFriendAiDigest(); return; }
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

  // 新增庫存的兩格：選到基金時換成「單位數」「台幣總成本」（07 票，本人 11-17 定案：照基富通對帳單抄），換的時候清掉已經填的數字
  let addKind = 'stock', addPicked = '';
  function setAddKind(kind) {
    if (kind === addKind) return;
    addKind = kind;
    [[$('holdingSharesLabel'), $('holdingShares'), 'units'], [$('holdingCostLabel'), $('holdingCost'), 'cost']].forEach(([label, input, slot]) => {
      const { label: text, name, attributes } = HOLDING_FIELDS[kind][slot];
      label.textContent = text;
      input.name = name;
      input.value = '';
      Object.entries(attributes).forEach(([key, value]) => input.setAttribute(key, value));
    });
  }
  // 「＋」的兩種新增共用一格。代號、名稱都能打（stock-picker.js，本人 11-17）；基金（07 票）也收，加進來就是一般的一列。
  // 會存資料：打的名稱符合好幾檔又沒有完全相同的，攤開下拉要本人挑，不默默存第一個（strict）。
  const addPicker = window.StockPicker.attach($('addCode'), { inline: true, strict: true,
    onPick: option => { addPicked = $('addCode').value; setAddKind(option.fund ? 'fund' : 'stock'); } });
  // 選完又改了框裡的字：先回到股票的兩格，按儲存時再照實際選到的那一檔對一次
  $('addCode').addEventListener('input', () => { if ($('addCode').value !== addPicked) setAddKind('stock'); });
  // 做完把表單收回去，結果寫在清單上面那一行；存不成的原因留在表單裡。
  // 按下去就先鎖住鍵（找代號也要時間），連按兩下不會送兩次。
  async function addFromForm(button, send, done, reload, kind, { skip } = {}) {
    if (button.disabled) return;
    button.disabled = true;
    try {
      const { option, message } = await addPicker.choose();
      if (message) { setStatus($('addStatus'), message, 'error'); $('addCode').focus(); return; }
      const code = option.code;
      const reason = skip?.(code, option);
      if (reason) { setStatus($('addStatus'), reason); return; }
      setStatus($('addStatus'), '正在儲存…');
      const saved = await send(code);
      $('addForm').reset();
      setAddKind('stock');
      setStatus($('addStatus'), '');
      setAddOpen(false);
      $('addToggle').focus({ preventScroll: true });
      say(done(saved?.name || code), 'success');
      await reload();
      showKind(kind);
    } catch (error) { setStatus($('addStatus'), error.message, 'error'); }
    finally { button.disabled = false; }
  }
  // 加入自選：先不分組、不標短期長期，之後點開那一檔再設。已經在自選的那一檔不重寫，只在表單裡說一聲
  const addWatch = () => addFromForm($('addWatch'), code => api('/api/watchlist', { method: 'POST', body: { code } }), name => `已把 ${name} 加入自選。`, loadWatchlist, 'watch', {
    skip: code => { const existing = state.watchlist.find(item => item.code === code); return existing ? `${existing.name} 已經在自選了。` : ''; },
  });
  const addHolding = () => addFromForm($('saveHolding'),
    code => saveHolding({ code, kind: addKind, shares: Number($('holdingShares').value), [HOLDING_FIELDS[addKind].cost.name]: Number($('holdingCost').value), note: $('holdingNote').value.trim() }),
    name => `已儲存 ${name} 的庫存。`, reloadHoldings, 'holding', {
      // 沒從下拉選、直接打字就按儲存：選到的是基金（或股票）但兩格還是另一種，先換成對的兩格請本人重填，不存錯的數字
      skip: (code, option) => {
        const kind = option.fund ? 'fund' : 'stock';
        if (kind === addKind) return '';
        setAddKind(kind);
        $('holdingShares').focus();
        return kind === 'fund' ? `${option.name} 是基金：請填單位數與台幣總成本（照基富通對帳單）。` : `${option.name} 是股票：請填股數與平均成本。`;
      },
    });
  $('addForm').addEventListener('submit', event => { event.preventDefault(); addWatch(); });
  $('saveHolding').addEventListener('click', addHolding);
  // 表單預設的送出是「加入自選」。股數那幾格開著的時候按 Enter 是存庫存：在代號那一格是跳到股數，在其他格是儲存
  $('addForm').addEventListener('keydown', event => {
    if (event.key !== 'Enter' || !event.target.matches('input') || $('addMore').hidden) return;
    event.preventDefault();
    if (event.target === $('addCode')) $('holdingShares').focus();
    else addHolding();
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

  // 進頁面要讀的資料同時發出去，不一支等一支（本人 2026-11-20「從主要切換到自選股會卡頓」：
  // 正式站實測原本排四層——功能清單 → 自選／庫存／關注 → 關注判斷 → AI 整理，清單要等最後一支回來才畫）。
  // 自選與庫存不用等功能清單；要看功能清單才知道讀不讀的（關注、水位、AI 整理）等它回來就一起發。
  // 清單仍然等全部到齊才畫第一次（進場只播一次，見 listReady）。
  // 到價推播（F 票）在頁首齒輪的設定浮板裡（settings.js，本人 11-17 選樣品 A）
  Promise.all([
    loadWatchlist(), loadHoldings(),
    featuresReady.then(() => (features.level_advice ? loadLevel() : null)),
    featuresReady.then(loadAiDigest),
  ]).then(() => {
    listReady = true;
    renderList();
    showFreshness();
  }).catch(error => {
    listReady = true;   // 這次沒讀到就不畫；之後新增或刪除成功再讀一次時照常畫
    $('portfolioFreshness').textContent = '資料讀取失敗';
    say(error.message, 'error');
  });
})();

// 開放給朋友 04 票：朋友今天的 AI 解讀沒產生（額度用完、金鑰失效…）時，在自選股頁也寫出原因並附回設定頁的入口。
// 開關關著、站主或沒設定金鑰的人，入口不會回 notice，這裡什麼都不做。
(() => {
  'use strict';
  if (typeof document === 'undefined') return;
  const box = document.getElementById('friendAiNotice');
  const text = document.getElementById('friendAiNoticeText');
  if (!box || !text) return;
  fetch('/api/friend-ai/digest', { cache: 'no-store', headers: { Accept: 'application/json' } })
    .then(response => (response.ok ? response.json() : null))
    .then(payload => {
      if (!payload?.available || !payload.notice?.text) return;
      text.textContent = payload.notice.text;
      box.hidden = false;
    })
    .catch(() => { /* 讀不到就不顯示，不影響自選股本身 */ });
})();
