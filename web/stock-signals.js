/* 個股頁五塊新功能的畫面（規格 .scratch/stock-signals/spec.md〈畫面〉）。
   樣子：本人 2026-11-16 更正——不要深色液態玻璃，和個股頁上半的報價卡、K 線卡同一套淺色看盤桌（米白卡片、圓角、柔和陰影），
   光條是卡片裡乾淨的細條、紅綠只在細記號上；深色主題用同一套卡片的深色版（取代第 4 題原本選的 q4n-2 液態玻璃材質）。
   每一塊一張卡片，放進事實區塊的面板裡：漲停動能在「重點」；和大盤比、均線排列、爆大量成交區在「技術」；融資維持率（估）在「融資券」。
   出現哪幾張只看五塊資料的 blocks，頁面不自己判斷、不計算數字；這裡只做排版（細條長短、千分位）。
   進場：卡片第一次出現在畫面上時輕輕浮上來、數字由零跳到位、細條伸出去；開了「減少動態效果」就直接畫好。
   這裡出錯只讓卡片不出現，事實區塊與頁面其他部分照常（呼叫的那一邊包了 try）。
   對外：window.StockSignals = { read, fill }。read 不碰畫面（Node 測試直接驗）。 */
(function () {
  'use strict';
  // 每一塊放在事實區塊的哪個面板；排列照五塊資料 blocks 的固定順序
  const PANEL = { limit_up_momentum: 'summary', versus_market: 'technical', ma_alignment: 'technical', margin_maintenance: 'credit', heavy_zones: 'technical' };
  // 漲停動能六級裡最高一級的歷史比例：跟著算法版本鎖住（規格〈漲停動能的規則〉）。不認得的版本不猜，不畫這張板。
  const TOP_RATE = { 'limit-up-momentum-20261116': 17.6 };
  // 最低一級（0.4%）照比例只有 2%，放大到看得到一小段
  const MIN_FILL = 0.06;

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const pct = value => `${Number(value).toFixed(1)}%`;

  // ---------- 純整理（不碰畫面） ----------
  function momentum(block) {
    if (!block || !finite(block.points) || !finite(block.band_rate_pct) || !finite(block.average_rate_pct)) return null;
    const top = TOP_RATE[block.algorithm];
    if (!top) return null;
    const points = Math.round(block.points);
    return {
      kind: 'limit_up_momentum', name: '漲停動能', points,
      rate: block.band_rate_pct, rateText: pct(block.band_rate_pct), averageText: pct(block.average_rate_pct),
      fill: Math.min(1, Math.max(MIN_FILL, block.band_rate_pct / top)),
      averageAt: Math.min(1, Math.max(0, block.average_rate_pct / top)),
      label: `漲停動能 ${points}，過去同樣級距的股票隔天漲停的占 ${pct(block.band_rate_pct)}，全部股票平均 ${pct(block.average_rate_pct)}`,
    };
  }
  const signed = (value, digits = 1) => `${value > 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
  const SIDE = { up: ['up', '強於'], down: ['down', '落後'] };
  // 和大盤比的光條：差距 20 個百分點是滿格（從中間往一邊伸的那一半），超過的貼邊
  const VERSUS_FULL = 20;

  function versus(block) {
    const rows = (Array.isArray(block?.rows) ? block.rows : [])
      .filter(row => row && [5, 20, 60].includes(row.days) && finite(row.gap))
      .sort((a, b) => a.days - b.days)
      .map(row => {
        // 強弱照資料給的方向（tone）；差距是 0 時資料不給方向，不畫條、不寫強弱
        const [side, word] = SIDE[row.tone] && row.gap !== 0 ? SIDE[row.tone] : ['', ''];
        return { days: row.days, name: `近 ${row.days} 日`, side, word, gapText: signed(row.gap), width: Math.min(1, Math.abs(row.gap) / VERSUS_FULL),
          said: `近 ${row.days} 日${word ? `${word}大盤 ${Math.abs(row.gap).toFixed(1)} 個百分點` : '和大盤一樣'}` };
      });
    if (!rows.length) return null;
    return { kind: 'versus_market', name: '和大盤比', rows, label: `和大盤比：${rows.map(row => row.said).join('，')}` };
  }

  // 均線排列：四盞燈固定順序；右上那一句照資料，頁面不自己組字
  const LAMPS = ['close_above_ma20', 'ma20_above_ma60', 'ma60_above_ma120', 'ma20_rising'];
  function alignment(block) {
    const conditions = Array.isArray(block?.conditions) ? block.conditions : [];
    const lamps = LAMPS.map(key => conditions.find(item => item?.key === key));
    if (lamps.some(lamp => !lamp || typeof lamp.label !== 'string' || typeof lamp.met !== 'boolean') || typeof block.summary !== 'string') return null;
    return {
      kind: 'ma_alignment', name: '均線排列', summary: block.summary, lamps: lamps.map(lamp => ({ name: lamp.label, on: lamp.met })),
      label: `均線排列 ${block.summary}：${lamps.map(lamp => `${lamp.label}${lamp.met ? '成立' : '不成立'}`).join('、')}`,
    };
  }

  // 價格的寫法：同一檔用同一個小數位數（收盤與各區的邊界裡最多的那個，最多兩位），千分位
  function priceWriter(values) {
    const places = Math.min(2, Math.max(0, ...values.map(value => { const text = String(value); return text.includes('.') ? text.split('.')[1].length : 0; })));
    return value => Number(value).toLocaleString('en-US', { minimumFractionDigits: places, maximumFractionDigits: places });
  }
  function zones(block) {
    const list = Array.isArray(block?.zones) ? block.zones.filter(zone => zone && finite(zone.low) && finite(zone.high)) : [];
    const chart = block?.chart;
    const series = values => Array.isArray(values) && values.length > 1 && values.every(finite);
    if (!list.length || !finite(block.close) || !series(chart?.closes) || !series(chart?.volumes)) return null;
    const price = priceWriter([block.close, ...list.flatMap(zone => [zone.low, zone.high])]);
    const byHigh = (a, b) => b.high - a.high;
    const row = zone => ({ range: `${price(zone.low)}～${price(zone.high)}`, note: zone.position === 'inside' ? '現價在區內' : finite(zone.distance_pct) ? `${signed(zone.distance_pct)}%` : '', now: false });
    const above = list.filter(zone => zone.position === 'above').sort(byHigh), rest = list.filter(zone => zone.position !== 'above').sort(byHigh);
    const items = [...above.map(row), { range: '現價', note: price(block.close), now: true }, ...rest.map(row)];
    return {
      kind: 'heavy_zones', name: '爆大量成交區', countText: `${list.length} 區`, list: items,
      close: block.close, zones: list.map(zone => ({ low: zone.low, high: zone.high })),
      chart: { closes: chart.closes, volumes: chart.volumes, heavy: Array.isArray(chart.heavy) ? chart.heavy : [] },
      label: `爆大量成交區 ${list.length} 區：${items.map(item => (item.now ? `現價 ${item.note}` : `${item.range}，${item.note}`)).join('；')}`,
    };
  }
  // 融資維持率（估）：軸 100%～300%，超出的那一端貼邊；追繳線 130%；很窄的範圍放寬到看得到一小段（仍蓋住真正的範圍）
  const AXIS = { low: 100, high: 300, call: 130, ticks: [130, 200, 250, 300], minSpan: 0.03 };
  const onAxis = value => Math.min(1, Math.max(0, (value - AXIS.low) / (AXIS.high - AXIS.low)));
  function margin(block) {
    if (!block || !finite(block.low_pct) || !finite(block.high_pct) || block.low_pct > block.high_pct) return null;
    let from = onAxis(block.low_pct), to = onAxis(block.high_pct);
    if (to - from < AXIS.minSpan) {
      const middle = Math.min(1 - AXIS.minSpan / 2, Math.max(AXIS.minSpan / 2, (from + to) / 2));
      from = middle - AXIS.minSpan / 2; to = middle + AXIS.minSpan / 2;
    }
    const rangeText = `${Math.round(block.low_pct)}%～${Math.round(block.high_pct)}%`;
    return {
      kind: 'margin_maintenance', name: '融資維持率（估）', rangeText, from, to, callAt: onAxis(AXIS.call),
      ticks: AXIS.ticks.map(value => ({ value, at: onAxis(value), hot: value === AXIS.call })),
      dataDate: typeof block.data_date === 'string' ? block.data_date : '',
      label: `融資維持率（估）${rangeText}，追繳線 ${AXIS.call}%`,
    };
  }
  const BUILD = { limit_up_momentum: momentum, versus_market: versus, ma_alignment: alignment, heavy_zones: zones, margin_maintenance: margin };

  // 某個面板要出現的板（照 blocks 的順序）。資料缺、形狀不對、這一塊還沒有畫面的，都不出現。
  function read(signals, panel) {
    const blocks = Array.isArray(signals?.blocks) ? signals.blocks : [];
    return blocks.filter(kind => PANEL[kind] === panel && BUILD[kind])
      .map(kind => { try { return BUILD[kind](signals[kind]); } catch (_) { return null; } })
      .filter(Boolean);
  }

  // ---------- 畫面 ----------
  // 本人 2026-11-16 更正：不要深色液態玻璃，五塊和個股頁上半的報價卡、K 線卡同一套——米白卡片、圓角、柔和陰影（bold.css 的板），
  // 光條是卡片裡乾淨的細條（和事實區塊的細橫條同一種），紅綠只在細記號上。進場只做克制的淡入、數字由零跳到位。
  const played = new Set();      // 「哪一塊」：這一檔播過進場的就不再播（切面板再切回來直接是最後的樣子）
  let playedCode = '';            // 換一檔就清掉，換回來也重播一次
  const still = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const at = value => `${(value * 100).toFixed(2)}%`;
  // 紅綠的細記號：沿用事實區塊的 .sf-tick（3px 寬的一小條）
  const tick = tone => { const mark = el('i', `sf-tick${tone ? ` ${tone}` : ''}`); mark.setAttribute('aria-hidden', 'true'); return mark; };
  // 會由零跳到位的數字
  const counter = (text, value, { sign = false, suffix = '%', digits = 1 } = {}) => {
    const node = el('b', 'num', text);
    Object.assign(node.dataset, { count: String(value), suffix, digits: String(digits) });
    if (sign) node.dataset.sign = '1';
    return node;
  };

  // 卡片：一個標題列（左邊塊名、右邊讀數），內容接在下面
  function card(model, read, tag = 'section') {
    const board = el(tag, `ss-board ss-${model.kind.replace(/_/g, '-')}`);
    board.setAttribute('aria-label', model.label);
    const head = el('span', 'ss-head');
    head.append(el('span', 'ss-name', model.name));
    if (read) head.append(read);
    board.append(head);
    return board;
  }

  function momentumBoard(model) {
    const read = el('span', 'ss-read', '隔天漲停 ');
    read.append(counter(model.rateText, model.rate));
    const board = card(model, read, 'button');
    board.type = 'button';
    board.classList.add('ss-momentum');   // 按住壓下去的樣式（stock-signals.css）認這個名字
    const main = el('span', 'ss-lu');
    const big = counter(String(model.points), model.points, { suffix: '', digits: 0 });
    big.classList.add('ss-big');
    big.setAttribute('aria-hidden', 'true');
    const meter = el('span', 'ss-meter');
    meter.setAttribute('aria-hidden', 'true');
    const track = el('span', 'ss-track');
    const fill = el('span', 'ss-fill');
    fill.style.width = at(model.fill);
    track.append(fill);
    const mark = el('span', 'ss-mark');
    mark.style.left = at(model.averageAt);
    const avg = el('span', 'ss-avg', '平均');
    avg.style.left = at(model.averageAt);
    const tip = el('span', 'ss-tip', '全部股票平均 ');
    tip.append(el('b', 'num', model.averageText));
    tip.style.left = at(model.averageAt);
    meter.append(track, mark, avg, tip);
    main.append(big, meter);
    board.append(main);

    // 按住（或鍵盤）：整張卡壓下去，「平均」上方浮出全部股票平均；放開後隔一下收回
    let timer = 0;
    const hold = () => { clearTimeout(timer); board.classList.add('is-held'); };
    const release = () => { clearTimeout(timer); timer = setTimeout(() => board.classList.remove('is-held'), 1400); };
    board.addEventListener('pointerdown', hold);
    board.addEventListener('pointerup', release);
    board.addEventListener('pointercancel', release);
    board.addEventListener('pointerleave', () => { if (board.classList.contains('is-held')) release(); });
    board.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); hold(); release(); } });
    board.addEventListener('contextmenu', event => event.preventDefault());
    return board;
  }

  function versusBoard(model) {
    const board = card(model);
    for (const row of model.rows) {
      const line = el('span', 'ss-vs');
      line.setAttribute('aria-hidden', 'true');
      const track = el('span', 'ss-track ss-two');
      if (row.side) {
        const fill = el('span', `ss-fill ${row.side}`);
        fill.style.width = at(row.width / 2);
        track.append(fill);
      }
      const read = el('span', 'ss-vs-read');
      if (row.word) read.append(tick(row.side), document.createTextNode(`${row.word} `));
      read.append(counter(row.gapText, Number(row.gapText), { sign: true, suffix: '' }));
      line.append(el('span', 'ss-vs-name', row.name), track, read);
      board.append(line);
    }
    return board;
  }

  function alignmentBoard(model) {
    const board = card(model, el('b', 'ss-word-read', model.summary));
    const checks = el('span', 'ss-checks');
    checks.setAttribute('aria-hidden', 'true');
    for (const lamp of model.lamps) {
      const item = el('span', `ss-check${lamp.on ? ' on' : ''}`);
      item.append(el('i', 'ss-dot'), document.createTextNode(lamp.name));
      checks.append(item);
    }
    board.append(checks);
    return board;
  }

  // 小圖：近 240 個交易日的收盤線（進場時自己畫出來）、成交量（爆量那幾天金色）、淡金色的區塊
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs) => { const node = document.createElementNS(SVG, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); return node; };
  function zonesChart(model) {
    const { closes, volumes, heavy } = model.chart, W = 320, H = 150, VOL = 30, top = H - VOL - 6;
    const lows = [...closes, ...model.zones.map(zone => zone.low)], highs = [...closes, ...model.zones.map(zone => zone.high)];
    const lo = Math.min(...lows), hi = Math.max(...highs), span = hi - lo || 1, count = Math.max(2, closes.length);
    const x = i => 2 + i / (count - 1) * (W - 4), y = v => 4 + (1 - (v - lo) / span) * (top - 8);
    // 寬度跟著卡片走、高度固定（樣式表給）：不等比縮放，線寬不跟著變
    const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', class: 'ss-zchart', role: 'img', 'aria-label': `近 ${closes.length} 個交易日的收盤、成交量與爆大量成交區` });
    const bands = svg('g', { class: 'ss-fade' });
    for (const zone of model.zones) bands.append(svg('rect', { x: 2, width: W - 4, y: y(zone.high).toFixed(1), height: Math.max(2, y(zone.low) - y(zone.high)).toFixed(1), class: 'ss-zband' }));
    chart.append(bands);
    chart.append(svg('path', { class: 'ss-draw', pathLength: 1, d: closes.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('') }));
    const most = Math.max(1, ...volumes), width = Math.max(1, (W - 4) / count - 0.5), hot = new Set(heavy);
    const bars = svg('g', { class: 'ss-vol' });
    volumes.forEach((v, i) => bars.append(svg('rect', { x: (x(i) - width / 2).toFixed(1), width: width.toFixed(1), y: (H - v / most * VOL).toFixed(1), height: (v / most * VOL).toFixed(1), class: hot.has(i) ? 'hot' : '' })));
    chart.append(bars);
    return chart;
  }
  function zonesBoard(model) {
    const board = card(model, el('b', 'num', model.countText));
    board.append(zonesChart(model));
    const list = el('ul', 'ss-zones');
    for (const item of model.list) {
      const row = el('li', item.now ? 'now' : '');
      row.append(el('span', item.now ? '' : 'num', item.range), el('span', 'num', item.note));
      list.append(row);
    }
    board.append(list);
    return board;
  }
  function marginBoard(model, code, panel) {
    const board = card(model, el('b', 'num', model.rangeText));
    // 資料日：融資比行情晚一個交易日。「融資券」面板上面已經標了同一天就不重複標，不同天才標（寫法和面板一樣）
    const shown = (panel?.querySelector('.sf-date')?.textContent || '').match(/資料日 (\S+?)(?:（|$)/)?.[1] || '';
    if (model.dataDate && model.dataDate !== shown) board.append(el('p', 'sf-date num', `資料日 ${model.dataDate}`));
    const axis = el('span', 'ss-rng');
    axis.setAttribute('aria-hidden', 'true');
    const band = el('span', 'ss-band');
    band.style.left = at(model.from);
    band.style.width = at(model.to - model.from);
    const call = el('span', 'ss-call');
    call.style.left = at(model.callAt);
    axis.append(el('span', 'ss-track'), band, call);
    for (const one of model.ticks) {
      const mark = el('span', `ss-tick-label num${one.hot ? ' hot' : ''}`, String(one.value));
      mark.style.left = at(one.at);
      axis.append(mark);
    }
    board.append(axis);
    return board;
  }
  const BOARD = { limit_up_momentum: momentumBoard, versus_market: versusBoard, ma_alignment: alignmentBoard, heavy_zones: zonesBoard, margin_maintenance: marginBoard };

  // 數字由零跳到位（減少動態效果時直接是最後的值）
  function countUp(node, quiet) {
    const target = Number(node.dataset.count), digits = Number(node.dataset.digits ?? 1), suffix = node.dataset.suffix ?? '%';
    const show = v => { node.textContent = `${node.dataset.sign && v > 0 ? '+' : ''}${v.toFixed(digits)}${suffix}`; };
    if (quiet || !Number.isFinite(target)) return show(target);
    const start = performance.now();
    const step = now => {
      const t = Math.min(1, (now - start) / 900), eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      show(target * eased);
      if (t < 1 && node.isConnected) requestAnimationFrame(step);
    };
    show(0);
    requestAnimationFrame(step);
  }

  function settle(board) { board.classList.add('is-in'); }   // 直接是最後的樣子

  function enter(board, order) {
    if (still()) { settle(board); board.querySelectorAll('[data-count]').forEach(node => countUp(node, true)); return; }
    setTimeout(() => {
      if (!board.isConnected) return;
      board.classList.add('is-in');
      board.querySelectorAll('[data-count]').forEach(node => countUp(node, false));
    }, 80 + order * 120);
  }

  // 把某個面板的板放進去。panel 是事實區塊那一塊的內容容器；name 是面板名（summary、technical、credit）。
  function fill(panel, name, signals, code) {
    const models = read(signals, name);
    if (code !== playedCode) { played.clear(); playedCode = code; }
    if (!models.length) return;
    const host = el('div', 'ss');
    host.dataset.panel = name;
    const boards = models.map(model => BOARD[model.kind](model, code, panel));
    host.append(...boards);
    panel.append(host);
    const fresh = models.map(model => !played.has(model.kind));
    // 播過的（同一檔切回這個面板）直接是最後的樣子
    boards.forEach((board, index) => { if (!fresh[index]) settle(board); });
    if (!fresh.some(Boolean)) return;
    if (typeof IntersectionObserver !== 'function') {
      boards.forEach((board, index) => { if (fresh[index]) { played.add(models[index].kind); settle(board); } });
      return;
    }
    // 第一次出現在畫面上才播（面板收著、還沒捲到時不跑動畫）
    let order = 0, waiting = fresh.filter(Boolean).length;
    const watch = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const index = boards.indexOf(entry.target);
        watch.unobserve(entry.target);
        if (--waiting === 0) watch.disconnect();
        played.add(models[index].kind);
        enter(entry.target, order++);
      }
    }, { threshold: 0.35 });
    boards.forEach((board, index) => { if (fresh[index]) watch.observe(board); });
  }

  window.StockSignals = { read, fill };
})();
