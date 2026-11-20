/* 動效（本人 2026-11-20 看過樣品後選「B 資料會動」＋「C 板有重量」；樣品在 design-previews/ui-polish/motion.html）。
   三個頁面共用。全部只動 transform／opacity，不重排版面；系統設定「減少動態」時整支不做事。K 線圖本身不加任何東西。

   B 資料會動
     1. 大數字一位一位滾到位：首頁指數、個股頁收盤價、自選股頁總市值。第一次出現是「滾到位」，之後盤中更新只滾有變的那幾位。
        做法：真正的字留在原地（變透明），上面疊一層只在滾的時候存在的數字欄；滾完就拿掉。
        數字欄的字是用樣式的 content 畫的，不是真的文字——元素的 textContent 永遠是真正的數值，其他程式與測試讀到的不變。
     2. 數值改變時，數字底下一條細線掃過：漲是紅、跌是綠（紅綠只用在這條細線）。
     3. 籌碼卡的小圖自己畫出來、柱子從基準線長出來、卡片依序浮起；隔日三區分布捲到才展開。樣式在 motion-fx.css。
     4. 小圖的細節（dress）：走勢線下面一層漸層、量好線的長度（等速畫出來用）、每根柱子的順序、最新一筆的標記。
        籌碼卡是 minimal.js 畫的，這裡只在畫好之後補上這幾樣，不改它的程式。
   C 板有重量
     1. 進頁面時板一塊一塊落到桌上（MotionFx.land；首頁由 home-motion.js 呼叫，個股頁與自選股頁在這裡）。
     2. 盤中數值更新時，一道淡淡的光掃過那塊板。

   換頁：換板（本人 11-20 看過三種轉法後選這一種；網站是「桌上的板」）
     桌面不動。點了站內換頁的連結，這一頁的板先往桌面沉下去、變淡（往回走是被拿起來）；新的一頁的板落到桌上（往回走是從下面放回來）。
     連結照常立刻換頁，沉下去的動態是邊等邊播，不會多等。方向記在 sessionStorage（fx-nav），新的一頁讀了就清掉。
     不用瀏覽器的跨頁過場（@view-transition）：實測新的一頁的動畫會卡住，見 motion-fx.css 最下面的說明。

   常駐（本人 11-20「可以長住的效果」，七項都要）：樣式都在 motion-fx.css；這裡只負責放上需要的元素——
     頁首那塊板上緣的金色細光（.fx-rim）、桌面的光（<html class="fx-desk">）、小圖最新一筆的暈與線上的光（dress 加的）。

   對外：window.MotionFx = { land(elements, { back }), roll(element), sheen(element), dress(root), adopt(from, to) }；減少動態時 land／roll／sheen／adopt 是空的，
   dress 照做（漸層是靜態的細節，不是動態）。 */
(function () {
  'use strict';

  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const LAND = 'cubic-bezier(.2, .9, .25, 1)';
  const ROLL = 'cubic-bezier(.16, 1, .3, 1)';
  const noop = () => {};
  const SVG = 'http://www.w3.org/2000/svg';
  let serial = 0;

  // ---------- 小圖的細節 ----------
  // root 裡每一張還沒補過的小圖（svg.spark）：
  //   走勢線——線下面由線的顏色淡到透明的漸層、線的實際長度（--fx-len）、最新一筆圓點外的暈、沿線走的那一小段光；
  //   柱子——每一根的順序（--fx-i）、最新一根的記號。每張卡自己的順序是 --fx-card。
  function dress(root) {
    if (!root) return;
    root.querySelectorAll('.chip-card').forEach((card, index) => card.style.setProperty('--fx-card', index));
    root.querySelectorAll('svg.spark:not([data-fx])').forEach(svg => {
      svg.dataset.fx = '1';
      const rects = [...svg.querySelectorAll('rect')];
      rects.forEach((rect, index) => rect.style.setProperty('--fx-i', index));
      rects.at(-1)?.classList.add('fx-last');
      const line = svg.querySelector('polyline');
      if (!line) return;
      const tone = line.getAttribute('stroke') || 'currentColor';
      const box = svg.viewBox.baseVal, points = line.getAttribute('points').trim();
      const first = points.split(/\s+/)[0].split(',')[0], last = points.split(/\s+/).at(-1).split(',')[0];
      const id = `fxFade${++serial}`;
      const defs = document.createElementNS(SVG, 'defs');
      defs.innerHTML = `<linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${tone}" stop-opacity=".15"/><stop offset="1" stop-color="${tone}" stop-opacity="0"/></linearGradient>`;
      const area = document.createElementNS(SVG, 'polygon');
      area.setAttribute('class', 'fx-area');
      area.setAttribute('points', `${points} ${last},${box.height} ${first},${box.height}`);
      area.setAttribute('fill', `url(#${id})`);
      svg.prepend(defs, area);
      try { svg.style.setProperty('--fx-len', `${Math.ceil(line.getTotalLength())}px`); } catch (_) { /* 還沒排版：用樣式裡的後備長度 */ }
      const comet = line.cloneNode(false);
      comet.setAttribute('class', 'fx-comet');
      svg.append(comet);
      const dot = svg.querySelector('circle');
      if (!dot) return;
      const halo = dot.cloneNode(false);
      halo.setAttribute('class', 'fx-halo');
      halo.removeAttribute('fill');
      halo.setAttribute('stroke', tone);
      dot.before(halo);
    });
  }
  // 籌碼卡畫好（或重畫）之後補上細節
  function watchSparks() {
    const grid = document.getElementById('chipGrid');
    if (!grid) return;
    new MutationObserver(() => dress(grid)).observe(grid, { childList: true });
    dress(grid);
  }
  const whenReady = run => { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run(); };
  whenReady(watchSparks);

  if (still || !Element.prototype.animate) { window.MotionFx = { land: noop, roll: noop, sheen: noop, dress, adopt: noop }; return; }

  // ---------- 換頁：這一頁是怎麼來的 ----------
  const NAV_KEY = 'fx-nav';
  const arrivedBack = (() => {
    let noted = null;
    try { noted = sessionStorage.getItem(NAV_KEY); sessionStorage.removeItem(NAV_KEY); } catch (_) { /* 私密視窗 */ }
    if (noted) return noted === 'back';
    return performance.getEntriesByType?.('navigation')[0]?.type === 'back_forward';
  })();

  // ---------- C1. 板落到桌上 ----------
  // back：從上一頁「放回來」（由下往上、幅度小），用在返回時；平常是從略高處落下。沒指定就照這一頁是怎麼來的。
  function land(elements, { back = arrivedBack, gap = 90 } = {}) {
    [...elements].filter(Boolean).forEach((element, index) => {
      element.animate(
        [{ opacity: 0, transform: back ? 'translateY(14px) scale(.97)' : 'translateY(-22px) scale(1.035)' }, { opacity: 1, offset: .55 }, { opacity: 1, transform: 'none' }],
        { duration: 620, delay: index * gap, easing: LAND, fill: 'backwards' });
    });
  }

  // ---------- C2. 一道光掃過那塊板 ----------
  // 往上找到「板」：有陰影、有底色的那一層（陰影與底色都是網站現成的，這裡不另外定義）
  function slabOf(element) {
    for (let node = element, depth = 0; node && node !== document.body && depth < 7; node = node.parentElement, depth++) {
      const style = getComputedStyle(node);
      if (style.boxShadow !== 'none' && parseFloat(style.borderTopLeftRadius) >= 8) return node;
    }
    return null;
  }
  function sheen(element) {
    const slab = slabOf(element);
    if (!slab || slab.querySelector(':scope > .fx-sheen')) return;
    if (getComputedStyle(slab).position === 'static') slab.classList.add('fx-host');
    const light = document.createElement('i');
    light.className = 'fx-sheen';
    light.setAttribute('aria-hidden', 'true');
    slab.append(light);
    setTimeout(() => light.remove(), 1000);
  }

  // ---------- B1＋B2. 數字逐位滾動、細線掃過 ----------
  const shown = new WeakMap();   // 每個數字上一次顯示的文字
  const ownText = element => [...element.childNodes].filter(node => node.nodeType === 3).map(node => node.nodeValue).join('').trim();
  const numeric = text => /^[−+\-$]?[\d,]+(\.\d+)?%?$/.test(text);
  const valueOf = text => Number(text.replace(/[^\d.\-−]/g, '').replace('−', '-'));

  function roll(element) {
    const text = ownText(element), before = shown.get(element);
    if (text === before) {
      // 頁面把同一個數字重寫一次（重畫）時，滾動的那一層會被一起換掉：字要變回看得到的
      if (!element.querySelector(':scope > .fx-odo')) element.classList.remove('fx-rolling');
      return;
    }
    shown.set(element, text);
    element.querySelector(':scope > .fx-odo')?.remove();
    element.classList.remove('fx-rolling');
    if (!numeric(text)) return;
    const first = before === undefined || !numeric(before);
    const lineHeight = element.getBoundingClientRect().height;
    if (!lineHeight) return;

    const overlay = document.createElement('span');
    overlay.className = 'fx-odo';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.style.setProperty('--fx-ink', getComputedStyle(element).color);
    overlay.style.setProperty('--fx-lh', `${lineHeight}px`);
    const columns = [];
    [...text].forEach((char, index) => {
      const cell = document.createElement('i');
      if (!/\d/.test(char)) { cell.dataset.c = char; overlay.append(cell); return; }
      // 從右邊對齊找舊的那一位；第一次出現（或位數不同）就從差幾格的數字滾過來
      const old = first ? null : before[before.length - (text.length - index)];
      const from = old !== undefined && old !== null && /\d/.test(old) ? Number(old) : (Number(char) + 7) % 10;
      cell.className = 'fx-col';
      overlay.append(cell);
      if (from !== Number(char)) columns.push({ cell, from, to: Number(char), order: text.length - index });
      else cell.style.transform = `translateY(${-Number(char) * lineHeight}px)`;
    });
    if (!columns.length) return;
    element.classList.add('fx-num', 'fx-rolling');
    element.append(overlay);
    const runs = columns.map(({ cell, from, to, order }) => cell.animate(
      [{ transform: `translateY(${-from * lineHeight}px)` }, { transform: `translateY(${-to * lineHeight}px)` }],
      { duration: first ? 900 : 620, delay: Math.min(order, 8) * 40, easing: ROLL, fill: 'both' }).finished);
    Promise.all(runs).catch(noop).then(() => {
      if (overlay.isConnected) { overlay.remove(); element.classList.remove('fx-rolling'); }
    });

    if (first) return;
    // 數值變了：細線掃過（漲紅跌綠），那塊板掃過一道光
    const diff = valueOf(text) - valueOf(before);
    if (!diff) return;
    element.dataset.fxTone = diff > 0 ? 'up' : 'down';
    element.classList.remove('fx-tick');
    void element.offsetWidth;
    element.classList.add('fx-tick');
    sheen(element);
  }

  function watch(element) {
    if (!element) return;
    // 自己加上去、拿掉的那一層不算變化（它沒有真的文字，roll 開頭比對文字就會跳過）
    new MutationObserver(() => roll(element)).observe(element, { childList: true, characterData: true, subtree: true });
    roll(element);
  }

  // 頁面把數字那一格換成另一個元素時，新的那一格接手（個股頁第一次盤中更新會把報價換成複本，見 stock.js）。
  // 複本是連狀態一起複製的：可能帶著「滾動中」的透明字與滾到一半的那一層，卻沒有人替它收尾——
  // 本人 2026-11-20 回報「個股頁目前的價位數字不見了」就是這樣。先清乾淨、記住目前顯示的字，之後有變才滾。
  function adopt(from, to) {
    if (!to) return;
    to.querySelector(':scope > .fx-odo')?.remove();
    to.classList.remove('fx-rolling', 'fx-tick');
    if (!from || !shown.has(from)) return;   // 原本那一格沒有在滾數字（價位階梯那幾格），新的也不用
    shown.set(to, shown.get(from));
    watch(to);
  }

  // ---------- 換頁：這一頁的板沉下去 ----------
  const PAGE = /(?:^|\/)(?:minimal|portfolio|stock|index)(?:\.html)?$|\/$/;
  let leaving = [];
  const stay = () => { leaving.forEach(animation => animation.cancel()); leaving = []; };
  function sink(back) {
    stay();
    const boards = [...document.querySelectorAll('.market-header, .chart-workspace, .read-card, .st-top, .bd-stage, .pf-card:not([hidden]), [data-cat-panel][data-open]')]
      .filter(board => { const box = board.getBoundingClientRect(); return box.height && box.bottom > 0 && box.top < window.innerHeight; }).slice(0, 5);
    leaving = boards.map((board, index) => board.animate(
      [{ opacity: 1, transform: 'none' }, { opacity: .42, transform: back ? 'translateY(-10px) scale(1.02)' : 'translateY(10px) scale(.965)' }],
      { duration: 200, delay: index * 35, easing: 'cubic-bezier(.4, 0, 1, 1)', fill: 'forwards' }));
    setTimeout(stay, 1600);   // 沒有真的換頁（被攔下、開新分頁）就放回來
  }
  // 掛在 window 的冒泡階段：頁面自己的處理都跑完了，被 preventDefault 的（例如搜尋結果先記紀錄再換頁）就不播
  window.addEventListener('click', event => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.('a[href]');
    if (!link || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !PAGE.test(url.pathname)) return;
    if (url.pathname === location.pathname && url.search === location.search) return;
    const back = link.matches('.bd-back, .pf-back');
    try { sessionStorage.setItem(NAV_KEY, back ? 'back' : 'forward'); } catch (_) { /* 私密視窗：新的一頁照一般的落桌 */ }
    sink(back);
  });
  // 從瀏覽器的整頁快取回來：沉下去的板放回原位
  window.addEventListener('pageshow', event => { if (event.persisted) stay(); });

  window.MotionFx = { land, roll, sheen, dress, adopt };

  const start = () => {
    ['currentPrice', 'close', 'totalMarketValue'].forEach(id => watch(document.getElementById(id)));
    // 自選股頁沒有自己的進場程式：兩塊板在這裡落桌（清單各列的進場照 portfolio.js 原本的）
    if (document.body.classList.contains('pf')) land(document.querySelectorAll('.pf-bar, .pf-card:not([hidden])'));
    // 常駐：頁首那塊板上緣的金色細光、桌面的光（樣式在 motion-fx.css）
    const head = document.querySelector('.market-header, .st-top, .pf-head');
    if (head && !head.querySelector(':scope > .fx-rim')) {
      if (getComputedStyle(head).position === 'static') head.classList.add('fx-host');
      const rim = document.createElement('i');
      rim.className = 'fx-rim';
      rim.setAttribute('aria-hidden', 'true');
      head.append(rim);
    }
    if (document.body.matches('.hm, .st')) document.documentElement.classList.add('fx-desk');
    // 個股頁：內容出現時（進頁面、查到資料）報價那塊板與 K 線那塊板落桌；同一頁換股不重播
    const content = document.body.classList.contains('st') && document.getElementById('content');
    if (content) {
      const boards = () => land(content.querySelectorAll(':scope > .st-top, :scope > .bd-stage'));
      if (!content.hidden) boards();
      else {
        const shownUp = new MutationObserver(() => { if (!content.hidden) { shownUp.disconnect(); boards(); } });
        shownUp.observe(content, { attributes: true, attributeFilter: ['hidden'] });
      }
    }
    // 隔日三區分布：捲到畫面內才展開（一開始就在畫面內的不播，免得閃一下）
    const bar = document.getElementById('distributionBar');
    if (bar && 'IntersectionObserver' in window && bar.getBoundingClientRect().top >= window.innerHeight) {
      const seen = new IntersectionObserver(entries => {
        if (!entries.some(entry => entry.isIntersecting)) return;
        seen.disconnect();
        bar.classList.add('fx-in');
      }, { rootMargin: '0px 0px -8% 0px' });
      seen.observe(bar);
    }
  };
  whenReady(start);
})();
