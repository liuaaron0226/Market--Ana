/* 個股頁五塊新功能的畫面（規格 .scratch/stock-signals/spec.md〈畫面〉；本人 2026-11-16 定案的樣子：樣品 q3f-1、q4n-2）。
   每一塊一張板，放進事實區塊的面板裡：漲停動能在「重點」；和大盤比、均線排列、爆大量成交區在「技術」；融資維持率（估）在「融資券」。
   出現哪幾張板只看五塊資料的 blocks，頁面不自己判斷、不計算數字；這裡只做排版（光條長短、千分位）。
   光條是液態玻璃：每一條的高光位置、亮度、漂移的範圍與速度由「代號＋這一條的位置」決定，同一檔每次看到的一樣，條與條不同步。
   進場：板浮上來、數字跳到位、光條伸出去；每一檔、每一張板只在第一次出現在畫面上時播。開了「減少動態效果」就直接畫好、高光不漂。
   這裡出錯只讓板不出現，事實區塊與頁面其他部分照常（呼叫的那一邊包了 try）。
   對外：window.StockSignals = { read, finish, fill }。read、finish 不碰畫面（Node 測試直接驗）。 */
(function () {
  'use strict';
  // 每一塊放在事實區塊的哪個面板；排列照五塊資料 blocks 的固定順序
  const PANEL = { limit_up_momentum: 'summary', versus_market: 'technical', ma_alignment: 'technical', margin_maintenance: 'credit', heavy_zones: 'technical' };
  // 漲停動能六級裡最高一級的歷史比例：跟著算法版本鎖住（規格〈漲停動能的規則〉）。不認得的版本不猜，不畫這張板。
  const TOP_RATE = { 'limit-up-momentum-20261116': 17.6 };
  // 最低一級（0.4%）照比例只有 2%，放大到看得到一小段
  const MIN_FILL = 0.06;
  // 里程表一格的高度（em），和 stock-signals.css 的 .ss-odo 一致
  const DIGIT_EM = 1.19;

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const pct = value => `${Number(value).toFixed(1)}%`;

  // ---------- 純整理（不碰畫面） ----------
  function momentum(block) {
    if (!block || !finite(block.points) || !finite(block.band_rate_pct) || !finite(block.average_rate_pct)) return null;
    const top = TOP_RATE[block.algorithm];
    if (!top) return null;
    const points = Math.round(block.points);
    return {
      kind: 'limit_up_momentum', name: '漲停動能', points, digits: String(points).split(''),
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
        const [side, word] = row.gap === 0 ? ['', ''] : SIDE[row.gap > 0 ? 'up' : 'down'];
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
    if (!list.length || !finite(block.close) || !Array.isArray(chart?.closes) || !Array.isArray(chart?.volumes)) return null;
    const price = priceWriter([block.close, ...list.flatMap(zone => [zone.low, zone.high])]);
    const byHigh = (a, b) => b.high - a.high;
    const row = zone => ({ range: `${price(zone.low)}～${price(zone.high)}`, note: zone.position === 'inside' ? '現價在區內' : `${signed(zone.distance_pct)}%`, now: false });
    const above = list.filter(zone => zone.position === 'above').sort(byHigh), rest = list.filter(zone => zone.position !== 'above').sort(byHigh);
    const items = [...above.map(row), { range: '現價', note: price(block.close), now: true }, ...rest.map(row)];
    return {
      kind: 'heavy_zones', name: '爆大量成交區', countText: `${list.length} 區`, list: items,
      close: block.close, zones: list.map(zone => ({ low: zone.low, high: zone.high })),
      chart: { closes: chart.closes, volumes: chart.volumes, heavy: Array.isArray(chart.heavy) ? chart.heavy : [] },
      label: `爆大量成交區 ${list.length} 區：${items.map(item => (item.now ? `現價 ${item.note}` : `${item.range}，${item.note}`)).join('；')}`,
    };
  }
  const BUILD = { limit_up_momentum: momentum, versus_market: versus, ma_alignment: alignment, heavy_zones: zones };

  // 某個面板要出現的板（照 blocks 的順序）。資料缺、形狀不對、這一塊還沒有畫面的，都不出現。
  function read(signals, panel) {
    const blocks = Array.isArray(signals?.blocks) ? signals.blocks : [];
    return blocks.filter(kind => PANEL[kind] === panel && BUILD[kind])
      .map(kind => { try { return BUILD[kind](signals[kind]); } catch (_) { return null; } })
      .filter(Boolean);
  }

  // 一條光條的高光：由「代號＋位置」算出一串固定的亂數（FNV-1a 當種子、線性同餘往下走），同一檔每次一樣。
  function finish(code, slot) {
    let seed = 2166136261;
    for (const ch of `${code}|${slot}`) { seed ^= ch.codePointAt(0); seed = Math.imul(seed, 16777619) >>> 0; }
    const next = (lo, hi) => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return lo + seed / 4294967296 * (hi - lo); };
    const near = next(4, 38), far = next(52, 96), flip = next(0, 1) > 0.5;
    const round = (value, digits) => Number(value.toFixed(digits));
    return {
      from: round(flip ? far : near, 1), to: round(flip ? near : far, 1),
      shine: round(next(0.45, 0.85), 2), seconds: round(next(5.5, 11), 1), delay: round(-next(0, 9), 1),
    };
  }

  // ---------- 畫面 ----------
  const played = new Set();      // 「代號|哪一塊」：播過進場的就不再播
  const still = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function glass(code, slot, tone) {
    const bar = el('i', 'ss-glass');
    bar.dataset.tone = tone;
    const look = finish(code, slot);
    bar.style.setProperty('--e1', `${look.from}%`);
    bar.style.setProperty('--e2', `${look.to}%`);
    bar.style.setProperty('--hi', String(look.shine));
    bar.style.setProperty('--dt', `${look.seconds}s`);
    bar.style.setProperty('--dd', `${look.delay}s`);
    return bar;
  }

  // 「隔天漲停」那個數字的顏色跟著光條：低的偏金、高的偏紅（金 → 銅 → 紅，和光條同一組）
  const rampColor = t => (t <= 0.5
    ? `color-mix(in srgb, var(--ss-ramp-b) ${Math.round(t * 200)}%, var(--ss-ramp-a))`
    : `color-mix(in srgb, var(--ss-ramp-c) ${Math.round((t - 0.5) * 200)}%, var(--ss-ramp-b))`);

  function momentumBoard(model, code) {
    const board = el('button', 'ss-board ss-momentum');
    board.type = 'button';
    board.setAttribute('aria-label', model.label);
    const head = el('span', 'ss-head');
    const rate = el('span', 'ss-read', '隔天漲停 ');
    const value = el('b', 'num', model.rateText);
    value.dataset.count = String(model.rate);
    value.style.color = rampColor(model.fill);
    rate.append(value);
    head.append(el('span', 'ss-name', model.name), rate);

    const main = el('span', 'ss-lu');
    const odo = el('span', 'ss-odo');
    odo.setAttribute('aria-hidden', 'true');
    for (const digit of model.digits) {
      const wheel = el('span');
      const strip = el('i');
      strip.dataset.d = digit;
      for (let n = 0; n <= 9; n++) strip.append(el('b', '', String(n)));
      wheel.append(strip);
      odo.append(wheel);
    }
    const track = el('span', 'ss-bar');
    track.setAttribute('aria-hidden', 'true');
    const groove = el('span', 'ss-groove');
    const clip = el('span', 'ss-clip');
    clip.style.setProperty('--fill', String(model.fill));
    clip.append(glass(code, model.kind, 'ramp'));
    groove.append(clip);
    const at = `calc((100% - 8px) * ${model.averageAt.toFixed(4)} + 4px)`;
    const avg = el('span', 'ss-avg', '平均');
    avg.style.left = at;
    const tip = el('span', 'ss-tip', '全部股票平均 ');
    tip.append(el('b', 'num', model.averageText));
    tip.style.left = at;
    track.append(groove, avg, tip);
    main.append(odo, track);
    board.append(head, main);

    // 按住（或鍵盤）：整張板壓下去，「平均」上方浮出全部股票平均；放開後隔一下收回
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
  // 一般的板：一個 <section>，標題列左邊是塊名、右邊是讀數（可以沒有）
  function plainBoard(model, read) {
    const board = el('section', `ss-board ss-${model.kind.replace(/_/g, '-')}`);
    board.setAttribute('aria-label', model.label);
    const head = el('div', 'ss-head');
    head.append(el('span', 'ss-name', model.name));
    if (read) head.append(read);
    board.append(head);
    return board;
  }

  function versusBoard(model, code) {
    const board = plainBoard(model);
    for (const row of model.rows) {
      const line = el('div', 'ss-vs');
      line.setAttribute('aria-hidden', 'true');
      const groove = el('span', 'ss-vs-groove');
      if (row.side) {
        const bar = el('span', `ss-vs-bar ${row.side}`);
        bar.style.width = `${(row.width * 50).toFixed(2)}%`;
        bar.append(glass(code, `${model.kind}:${row.days}`, row.side));
        groove.append(bar);
      }
      const read = el('span', 'ss-vs-read');
      if (row.word) read.append(el('span', `ss-word ${row.side}`, row.word), document.createTextNode(' '));
      const number = el('b', 'num', row.gapText);
      number.dataset.count = row.gapText;
      number.dataset.sign = '1';
      number.dataset.suffix = '';
      read.append(number);
      line.append(el('span', 'ss-vs-name', row.name), groove, read);
      board.append(line);
    }
    return board;
  }

  function alignmentBoard(model, code) {
    const board = plainBoard(model, el('b', 'ss-word-read', model.summary));
    const lamps = el('div', 'ss-lamps');
    lamps.setAttribute('aria-hidden', 'true');
    let lit = 0;
    model.lamps.forEach((lamp, index) => {
      const item = el('span', `ss-lamp${lamp.on ? ' on' : ''}`);
      const groove = el('span', 'ss-lamp-groove');
      if (lamp.on) {
        const light = glass(code, `${model.kind}:${index}`, 'gold');
        light.style.transitionDelay = `${300 + lit++ * 160}ms`;   // 亮的那幾盞依序亮起
        groove.append(light);
      }
      item.append(groove, el('span', 'ss-lamp-name', lamp.name));
      lamps.append(item);
    });
    board.append(lamps);
    return board;
  }

  // 小圖：近 240 個交易日的收盤線（進場時自己畫出來）、成交量（爆量那幾天金色）、金色的區塊
  const SVG = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs) => { const node = document.createElementNS(SVG, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); return node; };
  function zonesChart(model) {
    const { closes, volumes, heavy } = model.chart, W = 320, H = 150, VOL = 30, top = H - VOL - 6;
    const lows = [...closes, ...model.zones.map(zone => zone.low)], highs = [...closes, ...model.zones.map(zone => zone.high)];
    const lo = Math.min(...lows), hi = Math.max(...highs), span = hi - lo || 1, count = Math.max(2, closes.length);
    const x = i => 2 + i / (count - 1) * (W - 4), y = v => 4 + (1 - (v - lo) / span) * (top - 8);
    // 寬度跟著板走、高度固定（樣式表給）：不等比縮放，線寬不跟著變
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
    const read = el('b', 'num', model.countText);
    const board = plainBoard(model, read);
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
  const BOARD = { limit_up_momentum: momentumBoard, versus_market: versusBoard, ma_alignment: alignmentBoard, heavy_zones: zonesBoard };

  // 數字由零跳到位（減少動態效果時直接是最後的值）
  function countUp(node, quiet) {
    const target = Number(node.dataset.count), digits = 1, suffix = node.dataset.suffix ?? '%';
    const show = v => { node.textContent = `${node.dataset.sign && v > 0 ? '+' : ''}${v.toFixed(digits)}${suffix}`; };
    if (quiet || !Number.isFinite(target)) return show(target);
    const start = performance.now();
    const step = now => {
      const t = Math.min(1, (now - start) / 1300), eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      show(target * eased);
      if (t < 1 && node.isConnected) requestAnimationFrame(step);
    };
    show(0);
    requestAnimationFrame(step);
  }

  function settle(board) {   // 直接是最後的樣子
    board.classList.add('is-in');
    board.querySelectorAll('.ss-odo i').forEach(strip => { strip.style.transform = `translateY(${-Number(strip.dataset.d) * DIGIT_EM}em)`; });
  }

  function enter(board, order) {
    const quiet = still();
    if (quiet) { settle(board); board.querySelectorAll('[data-count]').forEach(node => countUp(node, true)); return; }
    setTimeout(() => {
      if (!board.isConnected) return;
      board.classList.add('is-in');
      board.querySelectorAll('.ss-odo i').forEach((strip, index) => {
        strip.style.transitionDelay = `${index * 120}ms`;
        strip.style.transform = `translateY(${-Number(strip.dataset.d) * DIGIT_EM}em)`;
      });
      board.querySelectorAll('[data-count]').forEach(node => countUp(node, false));
    }, 120 + order * 200);
  }

  // 把某個面板的板放進去。panel 是事實區塊那一塊的內容容器；name 是面板名（summary、technical、credit）。
  function fill(panel, name, signals, code) {
    const models = read(signals, name);
    if (!models.length) return;
    const host = el('div', `ss${typeof CSS !== 'undefined' && CSS.registerProperty ? ' ss-drift' : ''}`);
    host.dataset.panel = name;
    const boards = models.map(model => BOARD[model.kind](model, code));
    host.append(...boards);
    panel.append(host);
    const fresh = models.map(model => !played.has(`${code}|${model.kind}`));
    // 播過的（同一檔切回這個面板）直接是最後的樣子
    boards.forEach((board, index) => { if (!fresh[index]) settle(board); });
    if (!fresh.some(Boolean)) return;
    if (typeof IntersectionObserver !== 'function') {
      boards.forEach((board, index) => { if (fresh[index]) { played.add(`${code}|${models[index].kind}`); settle(board); } });
      return;
    }
    // 第一次出現在畫面上才播（面板收著、還沒捲到時不跑動畫）
    let order = 0;
    const watch = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const index = boards.indexOf(entry.target);
        watch.unobserve(entry.target);
        played.add(`${code}|${models[index].kind}`);
        enter(entry.target, order++);
      }
    }, { threshold: 0.35 });
    boards.forEach((board, index) => { if (fresh[index]) watch.observe(board); });
  }

  window.StockSignals = { read, finish, fill };
})();
