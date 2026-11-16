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
  const BUILD = { limit_up_momentum: momentum };

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
  const BOARD = { limit_up_momentum: momentumBoard };

  // 數字由零跳到位（減少動態效果時直接是最後的值）
  function countUp(node, quiet) {
    const target = Number(node.dataset.count), digits = 1, show = v => { node.textContent = `${v.toFixed(digits)}%`; };
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
