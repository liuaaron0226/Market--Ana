/* 主力分點副圖（本人 2026-11-24「共識達成」，.scratch/broker-branches 票 01）：個股頁 K 線下面多一格，
   畫「畫面上這一段」買賣超最大的幾個分點每天的買賣超。
   開關是分類鍵那一排「比較」右邊的「主力分點」；副圖掛在指標區（chart-studies.js 的 extras），
   每次 K 線重畫都跟著畫，日期位置、十字線、拖曳與縮放全部沿用主圖，不另存範圍。只在日 K 顯示。
   資料只經 loadBranchHistory(code, enabled) 進來：功能清單的 broker_branches_v1 開著才問 /api/stocks/<代號>/branch-history
   （只有站主拿得到，約定見 .scratch/broker-branches/contract.md）。讀不到這一檔的資料時「主力分點」不出現，其他都和原本一樣。
   算的部分（model）不碰畫面，測試直接叫：cloud/test/branch-pane.test.js。 */
(function () {
  'use strict';

  const TOP = 5;               // 這一段取買賣超（絕對值）最大的幾個分點
  const CHIP_DELAY_MS = 120;   // 拖曳時名稱那一排晚一點再換，柱子每一格都照畫
  const TAG_HEIGHT_PX = 18;
  const EMPTY_TEXT = '這段期間沒有分點資料';

  // ---------- 算的部分 ----------
  // 'YYYY-MM-DD' → 20261123；認不得的當成 0（排在最前面，對不到任何一天）
  function dateInt(text) {
    const value = String(text || '');
    const number = value.length >= 10 ? Number(value.slice(0, 4) + value.slice(5, 7) + value.slice(8, 10)) : NaN;
    return Number.isFinite(number) ? number : 0;
  }

  // API 的回應 → { code, through, branches: [{ id, name, d: [日期整數，舊到新], n: [那一天的買賣超（張）] }] }；不是這一檔、沒有資料就是 null
  function fromApi(payload, code) {
    if (!payload || payload.available !== true || payload.code !== code || !Array.isArray(payload.branches)) return null;
    const branches = [];
    for (const branch of payload.branches) {
      if (!branch || !Array.isArray(branch.d) || !Array.isArray(branch.n) || !branch.d.length || branch.d.length !== branch.n.length) continue;
      branches.push({ id: String(branch.id), name: String(branch.name || branch.id), d: branch.d, n: branch.n });
    }
    return branches.length ? { code, through: String(payload.through || ''), branches } : null;
  }

  // 每個分點照 K 棒的位置排好（K 棒的日期換一次才重排）：net[i] 是第 i 根那一天的買賣超（沒有資料是 NaN），
  // sum／count 是累計，查一段的合計不用逐日加。兩邊都是舊到新，一路往後對，不逐日查表。
  function indexBranches(branches, dates) {
    const n = dates.length;
    return branches.map(branch => {
      const net = new Float64Array(n).fill(NaN);
      let at = 0;
      for (let k = 0; k < branch.d.length && at < n; k += 1) {
        const day = branch.d[k];
        while (at < n && dates[at] < day) at += 1;
        if (at < n && dates[at] === day && Number.isFinite(branch.n[k])) net[at] = branch.n[k];
      }
      const sum = new Float64Array(n + 1), count = new Int32Array(n + 1);
      for (let i = 0; i < n; i += 1) {
        const has = !Number.isNaN(net[i]);
        sum[i + 1] = sum[i] + (has ? net[i] : 0);
        count[i + 1] = count[i] + (has ? 1 : 0);
      }
      return { id: branch.id, name: branch.name, net, sum, count };
    });
  }

  // 這一段（第 start 根到第 end 根之前）的主力分點：合計買賣超絕對值最大的前幾個；這一段沒有資料或合計是 0 的不算
  function topBranches(rows, start, end) {
    return rows.map(row => ({ row, total: row.sum[end] - row.sum[start], days: row.count[end] - row.count[start] }))
      .filter(item => item.days > 0 && Math.round(item.total) !== 0)
      .sort((a, b) => Math.abs(b.total) - Math.abs(a.total))
      .slice(0, TOP);
  }

  // 要畫哪幾個分點：選了某一個而且它還在這一段的名單裡就只畫它；不在了就回到合計（selected 變回 null）
  function pick(top, selected) {
    const kept = selected && top.some(item => item.row.id === selected) ? selected : null;
    return { selected: kept, rows: (kept ? top.filter(item => item.row.id === kept) : top).map(item => item.row) };
  }

  // 那一天的買賣超：畫的那幾個分點裡有資料的加起來；都沒有資料是 null（不畫）
  function valueAt(rows, index) {
    let has = false, value = 0;
    for (const row of rows) if (!Number.isNaN(row.net[index])) { has = true; value += row.net[index]; }
    return has ? value : null;
  }

  const model = { TOP, dateInt, fromApi, indexBranches, topBranches, pick, valueAt };

  // ---------- 資料 ----------
  const loaded = new Map();
  function loadBranchHistory(code, enabled) {
    if (!enabled) return Promise.resolve(null);
    if (!loaded.has(code)) {
      loaded.set(code, fetch(`/api/stocks/${encodeURIComponent(code)}/branch-history`, { cache: 'no-store', headers: { Accept: 'application/json' } })
        .then(response => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
        .then(payload => fromApi(payload, code))
        .catch(() => { loaded.delete(code); return null; }));   // 這一次沒讀到：下次進這一檔再問
    }
    return loaded.get(code);
  }

  // ---------- 畫面 ----------
  const token = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const lots = value => Math.round(Math.abs(value)).toLocaleString('en-US');
  const signed = value => (Math.round(value) > 0 ? `+${lots(value)}` : Math.round(value) < 0 ? `−${lots(value)}` : '0');

  const state = { code: '', history: null, chart: null, on: false, selected: null, rows: null, rowsKey: null };
  let pane = null, strip = null, seg = null, canvas = null, chipTimer = 0, chipKey = '', pendingTop = [];
  let last = null;   // 最後畫的那一次：snapshot() 用
  const toggle = () => document.getElementById('branchToggle');

  function rowsOf(candles) {
    const n = candles.length, key = state.rowsKey;
    const first = n ? candles[0].t : '', final = n ? candles[n - 1].t : '';
    if (!key || key.candles !== candles || key.n !== n || key.first !== first || key.final !== final) {
      state.rows = indexBranches(state.history.branches, candles.map(candle => dateInt(candle.t)));
      state.rowsKey = { candles, n, first, final };
    }
    return state.rows;
  }

  // 名稱那一排：第一顆「合計」，後面是這一段的主力分點與各自這一段的買賣超（張）
  function renderChips() {
    const top = pendingTop;
    const key = `${state.selected || ''}|${top.map(item => `${item.row.id}:${Math.round(item.total)}`).join(',')}`;
    if (key === chipKey) return;
    chipKey = key;
    seg.hidden = !top.length;
    const chip = (id, name, total) => {
      const button = document.createElement('button');
      button.type = 'button'; button.setAttribute('role', 'radio'); button.dataset.branch = id;
      button.setAttribute('aria-checked', String((state.selected || '') === id));
      const label = document.createElement('span'); label.textContent = name;
      const net = document.createElement('span'); net.className = 'num'; net.textContent = signed(total);
      button.append(label, net);
      return button;
    };
    seg.replaceChildren(
      chip('', '合計', top.reduce((sum, item) => sum + item.total, 0)),
      ...top.map(item => chip(item.row.id, item.row.name, item.total)));
    // 放不下時左右滑：把從鍵上開始的橫向手勢還給瀏覽器（和首頁盤勢解讀的類別鍵同一個做法）
    strip.classList.toggle('is-overflow', strip.scrollWidth > strip.clientWidth + 1);
  }
  function scheduleChips(top, now) {
    pendingTop = top;
    clearTimeout(chipTimer);
    if (now) renderChips(); else chipTimer = setTimeout(renderChips, CHIP_DELAY_MS);
  }

  function mount(chart) {
    const studies = chart.studies;
    pane = document.createElement('section');
    pane.className = 'kc-study-pane kc-study-pane-branch';
    pane.setAttribute('aria-label', '主力分點買賣超');
    strip = document.createElement('div'); strip.className = 'bp-chips';
    seg = document.createElement('div'); seg.className = 'chip-seg';
    seg.setAttribute('role', 'radiogroup'); seg.setAttribute('aria-label', '分點');
    strip.append(seg);
    canvas = document.createElement('canvas');
    canvas.className = 'kc-study-canvas'; canvas.tabIndex = 0;
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', '主力分點每日買賣超，單位：張。');
    canvas.style.touchAction = 'pan-y';
    pane.append(strip, canvas);
    studies.root.insertBefore(pane, studies.switcher);
    chart.bindCanvas(canvas);   // 和主圖同一組手勢：拖曳平移、捏合縮放、點一下選那一天
    seg.addEventListener('click', event => {
      const button = event.target.closest('button[data-branch]');
      if (!button) return;
      state.selected = button.dataset.branch || null;
      chipKey = '';
      chart.draw();
      scheduleChips(pendingTop, true);
    });
    studies.extras.push({ draw });
  }

  function draw(daily) {
    const chart = state.chart, studies = chart.studies;
    pane.hidden = !state.on || !daily || !state.history;
    last = null;
    if (pane.hidden) return;
    const width = studies._mainWidth(), height = width >= 760 ? 132 : 116;
    const context = studies._canvasSize(canvas, width, height);
    const range = studies._range();
    if (!range.valid) { scheduleChips([], true); return; }
    const { candles, start, end, geo } = range;
    const top = topBranches(rowsOf(candles), start, end);
    const picked = pick(top, state.selected);
    state.selected = picked.selected;
    scheduleChips(top, !chipKey);
    last = { candles, start, end, left: geo.l, step: geo.step, top, rows: picked.rows, selected: picked.selected };
    if (!top.length) {
      context.fillStyle = token('--dim');
      context.font = `12px ${token('--sans') || 'sans-serif'}`;
      context.textAlign = 'left'; context.textBaseline = 'middle';
      context.fillText(EMPTY_TEXT, geo.l + 6, height / 2);
      studies._drawCrosshair(context, height, range);
      return;
    }
    const shown = picked.rows;
    let amplitude = 0;
    for (let index = start; index < end; index += 1) { const value = valueAt(shown, index); if (value !== null) amplitude = Math.max(amplitude, Math.abs(value)); }
    amplitude = amplitude || 1;
    const top0 = 8, bottom = height - 12, middle = (top0 + bottom) / 2;
    const y = value => middle - value / amplitude * (bottom - top0) / 2;
    const barWidth = Math.max(1, Math.min(14, geo.step * .64));
    const up = token('--up'), down = token('--down');
    context.save();
    context.beginPath(); context.rect(geo.l, 0, geo.pw, height); context.clip();
    context.strokeStyle = token('--line'); context.lineWidth = 1;
    context.beginPath(); context.moveTo(geo.l, Math.round(middle) + .5); context.lineTo(geo.l + geo.pw, Math.round(middle) + .5); context.stroke();
    for (let index = start; index < end; index += 1) {
      const value = valueAt(shown, index);
      if (value === null) continue;
      const x = geo.l + geo.step * (index - start + .5), valueY = y(value);
      context.fillStyle = value >= 0 ? up : down;
      context.fillRect(x - barWidth / 2, Math.min(middle, valueY), barWidth, Math.max(1, Math.abs(valueY - middle)));
    }
    context.restore();
    context.fillStyle = token('--dim'); context.font = `11px ${token('--mono') || 'monospace'}`;
    context.textAlign = 'left'; context.textBaseline = 'middle';
    const axisText = studies._axisText(width, geo);
    axisText(context, `${lots(amplitude)}張`, top0 + 3);
    axisText(context, '0', middle);
    axisText(context, `−${lots(amplitude)}張`, bottom - 3);
    studies._drawCrosshair(context, height, range);
    // 選了某一天：那一天的買賣超標在十字線的另一邊（和其他指標同一個位置規則）
    const hover = chart.hover;
    if (Number.isInteger(hover) && hover >= start && hover < end && valueAt(shown, hover) !== null && window.KChartTags) {
      const value = valueAt(shown, hover), cx = geo.l + geo.step * (hover - start + .5);
      const tagWidth = geo.inline ? 84 : width - (geo.l + geo.pw + 4) - 2;
      const left = geo.inline ? (cx > geo.l + geo.pw / 2 ? geo.l + 4 : geo.l + geo.pw - tagWidth - 4) : geo.l + geo.pw + 4;
      const tags = window.KChartTags;
      const items = [{ name: '', text: signed(value), y: y(value), bg: value >= 0 ? up : down, fg: token('--bg') }];
      tags.draw(context, left, tagWidth, TAG_HEIGHT_PX, tags.place(items, TAG_HEIGHT_PX / 2 + 1, height - TAG_HEIGHT_PX / 2 - 1, TAG_HEIGHT_PX + 2), true);
      last.picked = { date: candles[hover].t, text: signed(value) };
    }
  }

  // 最後畫的那一次畫了什麼（旅程測試核對用；平常不算，叫了才整理）：這一段的頭尾日期、名單、每一根柱子的日期／張數／水平位置
  function snapshot() {
    if (!last) return null;
    const bars = [];
    for (let index = last.start; index < last.end; index += 1) {
      const value = valueAt(last.rows, index);
      if (value !== null) bars.push({ date: last.candles[index].t, value: Math.round(value), x: last.left + last.step * (index - last.start + .5) });
    }
    return { first: last.candles[last.start].t, last: last.candles[last.end - 1].t, selected: last.selected, picked: last.picked || null,
      top: last.top.map(item => ({ id: item.row.id, name: item.row.name, total: Math.round(item.total) })), bars };
  }

  function setOn(on) {
    state.on = on;
    const button = toggle();
    if (button) button.setAttribute('aria-pressed', String(on));
    if (on && !pane && state.chart) mount(state.chart);
    chipKey = '';
    if (pane && state.chart) state.chart.draw();
  }

  // 個股頁每載入一檔叫一次：功能開著而且有這一檔的分點資料才露出「主力分點」
  async function show(code, chart, enabled) {
    const button = toggle();
    if (!button || !chart || !chart.studies) return;
    state.chart = chart;
    if (!button.dataset.bound) {
      button.dataset.bound = '1';
      button.addEventListener('click', () => { if (state.history) setOn(!state.on); });
    }
    if (enabled && state.code === code && state.history) return;
    state.code = code; state.history = null; state.selected = null; state.rowsKey = null;
    button.hidden = true;
    setOn(false);
    const history = await loadBranchHistory(code, enabled);
    if (state.code !== code) return;
    state.history = history;
    button.hidden = !history;
  }

  window.BranchPane = { loadBranchHistory, show, snapshot, model };
})();
