/* 基金對照 0050 頁（本人 2026-11-17 定案）：讀 /api/funds/<代碼>/vs-0050，用 FundView 整理好的字畫出來。
   圖、期間鍵、數字的畫法和個股頁「對照 0050」（stock.js）同一套 class 與比例，樣式直接用 stock.css／bold.css。 */
(function () {
  const $ = id => document.getElementById(id);
  const node = (tag, className = '', text = '') => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  };
  const svgNode = (tag, attrs = {}) => {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
    return el;
  };
  const signed = value => `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}%`;
  const GAP_WORDS = { bull: '領先', bear: '落後' };
  const state = { view: null, period: '1y', hidden: new Set() };

  const css = (name, fallback) => getComputedStyle($('compareBlock')).getPropertyValue(name).trim() || fallback;
  const colorOf = key => (key === '0050' ? css('--cmp-benchmark', '#1d1b20') : css('--cmp-series-1', '#2a78d6'));

  function code() {
    const raw = new URLSearchParams(location.search).get('code') || 'BLKTENAU';
    return /^[0-9A-Z]{4,12}$/.test(raw) ? raw : null;
  }

  // ---------- 期間鍵與四個數字 ----------
  function facts(period) {
    const list = period.facts.map(([title, text]) => {
      const item = node('div');
      item.append(node('dt', '', title), node('dd', 'num', text));
      return item;
    });
    const box = node('dl', 'cmp-facts');
    box.append(...list);
    return box;
  }
  function renderPeriods() {
    const view = state.view;
    const card = node('article', 'cmp-card');
    const head = node('h3', '', view.title);
    head.append(node('small', 'num', '和 0050'));
    const strip = node('div', 'key-strip');
    strip.setAttribute('role', 'group');
    strip.setAttribute('aria-label', `${view.title}各期間和 0050 的差距`);
    strip.append(...view.periods.map(period => {
      const cell = node('button', `cell ${period.kind}`);
      cell.type = 'button';
      cell.dataset.period = period.key;
      cell.setAttribute('aria-pressed', String(period.key === state.period));
      cell.setAttribute('aria-label', GAP_WORDS[period.kind] ? `${period.label}，${GAP_WORDS[period.kind]} 0050 ${period.gap.replace(/^[+-]/, '')}` : `${period.label}，${period.gap}`);
      const name = node('span', 'cell-name');
      name.append(period.short, node('b', 'num', period.gap));
      cell.append(node('span', 'cell-face'), node('i', 'tag'), name);
      return cell;
    }));
    const current = view.periods.find(p => p.key === state.period) || view.periods[0];
    card.append(head, strip, facts(current), node('p', 'fd-fx', current.fx));
    $('comparePeriods').replaceChildren(card);
  }
  function pickPeriod(key) {
    const period = state.view.periods.find(p => p.key === key);
    if (!period) return;
    state.period = key;
    const card = $('comparePeriods').querySelector('.cmp-card');
    card.querySelectorAll('.cell').forEach(cell => cell.setAttribute('aria-pressed', String(cell.dataset.period === key)));
    card.querySelector('.cmp-facts').replaceWith(facts(period));
    card.querySelector('.fd-fx').textContent = period.fx;
  }
  $('comparePeriods').addEventListener('click', event => {
    const cell = event.target.closest('.cell[data-period]');
    if (cell) pickPeriod(cell.dataset.period);
  });

  // ---------- 走勢圖（和 stock.js 的 drawComparisonChart 同一種畫法） ----------
  function renderLegend(series) {
    $('compareLegend').replaceChildren(...series.map(item => {
      const button = node('button');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(!state.hidden.has(item.key)));
      button.setAttribute('aria-label', `顯示或隱藏 ${item.label}`);
      const line = node('i', item.dash ? 'is-dashed' : '');
      line.style.setProperty('--series-color', colorOf(item.key));
      button.append(line, node('span', '', item.label));
      button.addEventListener('click', () => {
        if (state.hidden.has(item.key)) state.hidden.delete(item.key);
        else if (series.length - state.hidden.size > 1) state.hidden.add(item.key);
        drawChart();
      });
      return button;
    }));
  }
  function drawChart() {
    const all = state.view.series.filter(item => item.points.length > 1);
    renderLegend(all);
    const series = all.filter(item => !state.hidden.has(item.key));
    const host = $('compareChart'), tooltip = $('compareTooltip');
    tooltip.hidden = true;
    host.replaceChildren();
    if (!series.length) { host.append(node('p', 'bd-compare-empty', '目前沒有足夠的走勢資料。')); return; }

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
      'aria-label': `${series.map(item => item.label).join('、')}含息報酬走勢（台幣），共同起點為 100` });
    for (let i = 0; i < 5; i += 1) {
      const value = minValue + (maxValue - minValue) * i / 4, yy = y(value);
      svg.append(svgNode('line', { x1: L, x2: W - R, y1: yy, y2: yy, class: 'cmp-grid' }));
      const label = svgNode('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', class: 'cmp-axis-label' });
      label.textContent = `${Math.round(value - 100)}%`;
      svg.append(label);
    }
    svg.append(svgNode('line', { x1: L, x2: W - R, y1: y(100), y2: y(100), class: 'cmp-base' }));
    const zeroLabel = svgNode('text', { x: L - 8, y: y(100) + 4, 'text-anchor': 'end', class: 'cmp-axis-label' });
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
    series.forEach(item => {
      const path = svgNode('path', { class: 'cmp-line', stroke: colorOf(item.key),
        d: item.points.map((point, i) => `${i ? 'L' : 'M'}${x(Date.parse(point[0])).toFixed(2)},${y(point[1]).toFixed(2)}`).join(' ') });
      if (item.dash) path.setAttribute('stroke-dasharray', item.dash);
      svg.append(path);
      const end = item.points[item.points.length - 1];
      let labelY = y(end[1]) + 4;
      while (labelYs.some(value => Math.abs(value - labelY) < 13)) labelY += 13;
      labelYs.push(labelY);
      const label = svgNode('text', { x: W - R + 7, y: labelY, fill: colorOf(item.key), class: 'cmp-direct-label' });
      label.textContent = item.key === '0050' ? '0050' : '基金';
      svg.append(label);
    });
    const crosshair = svgNode('line', { x1: L, x2: L, y1: T, y2: H - B, class: 'cmp-crosshair', hidden: 'hidden' });
    const hit = svgNode('rect', { x: L, y: T, width: W - L - R, height: H - T - B, class: 'cmp-hit' });
    svg.append(crosshair, hit);
    host.append(svg);

    const dates = [...new Set(series.flatMap(item => item.points.map(point => point[0])))].sort();
    let keyboardIndex = dates.length - 1;
    // 日期是字串、線上的點是 [日期, 值]：分開找最近的（字串取 [0] 會變成第一個字）
    const nearestDate = time => dates.reduce((best, day) => (Math.abs(Date.parse(day) - time) < Math.abs(Date.parse(best) - time) ? day : best), dates[0]);
    const nearestPoint = (list, time) => list.reduce((best, point) => (Math.abs(Date.parse(point[0]) - time) < Math.abs(Date.parse(best[0]) - time) ? point : best), list[0]);
    const showAt = (time, clientX = null) => {
      const day = nearestDate(time), picked = Date.parse(day), xx = x(picked);
      crosshair.setAttribute('x1', xx); crosshair.setAttribute('x2', xx); crosshair.removeAttribute('hidden');
      const title = document.createElement('time');
      title.textContent = day;
      tooltip.replaceChildren(title, ...series.map(item => {
        const point = nearestPoint(item.points, picked), row = document.createElement('p'), label = node('span'), line = node('i');
        line.style.setProperty('--series-color', colorOf(item.key));
        label.append(line, document.createTextNode(item.label));
        row.append(label, node('b', '', signed(point[1] - 100)));
        return row;
      }));
      tooltip.hidden = false;
      const block = tooltip.parentElement.getBoundingClientRect(), rect = svg.getBoundingClientRect();
      const pointer = clientX === null ? rect.left + (xx / W) * rect.width : clientX;
      tooltip.style.left = `${Math.max(0, Math.min(block.width - Math.min(280, block.width - 24), pointer - block.left + 12))}px`;
      tooltip.style.top = `${Math.max(54, rect.top - block.top + 28)}px`;
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
      keyboardIndex = Math.max(0, Math.min(dates.length - 1, keyboardIndex + (event.key === 'ArrowRight' ? 1 : -1)));
      showAt(Date.parse(dates[keyboardIndex]));
    });
  }

  // ---------- 整頁 ----------
  function render(payload) {
    const view = state.view = window.FundView.read(payload);
    document.title = `${view.title}｜和 0050 對照`;
    $('fundTitle').textContent = view.title;
    $('fundSub').textContent = view.sub;
    $('fundNav').textContent = view.nav;
    $('fundNavDate').textContent = view.navDate;
    $('compareDate').textContent = view.dateLine;
    $('compareConclusion').textContent = view.conclusion;
    $('fundIssues').replaceChildren(...view.issues.map(text => node('li', '', text)));
    $('fundIssues').hidden = !view.issues.length;
    $('fundCapital').replaceChildren(node('b', '', view.capitalNote), ...(view.latestDistribution ? [node('span', '', view.latestDistribution)] : []));
    $('fundNotes').replaceChildren(...view.notes.map(text => node('li', '', text)));
    renderPeriods();
    drawChart();
    $('content').hidden = false;
    $('status').hidden = true;
  }

  async function load() {
    const fund = code();
    if (!fund) { $('status').textContent = '基金代碼不正確。'; return; }
    try {
      const response = await fetch(`/api/funds/${encodeURIComponent(fund)}/vs-0050`, { cache: 'no-store' });
      if (response.status === 404) { $('status').textContent = '這檔基金今天的對照資料還沒準備好，晚一點再來看。'; return; }
      if (response.status === 429) { $('status').textContent = '查詢太頻繁，請稍後再試。'; return; }
      if (!response.ok) throw new Error(String(response.status));
      render(await response.json());
    } catch {
      $('status').textContent = '暫時讀不到對照資料，請稍後再試。';
    }
  }

  let resizeTimer = 0;
  addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.view) drawChart(); }, 150);
  });
  load();
})();
