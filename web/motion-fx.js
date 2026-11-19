/* 動效（本人 2026-11-20 看過樣品後選「B 資料會動」＋「C 板有重量」；樣品在 design-previews/ui-polish/motion.html）。
   三個頁面共用。全部只動 transform／opacity，不重排版面；系統設定「減少動態」時整支不做事。K 線圖本身不加任何東西。

   B 資料會動
     1. 大數字一位一位滾到位：首頁指數、個股頁收盤價、自選股頁總市值。第一次出現是「滾到位」，之後盤中更新只滾有變的那幾位。
        做法：真正的字留在原地（變透明），上面疊一層只在滾的時候存在的數字欄；滾完就拿掉。
        數字欄的字是用樣式的 content 畫的，不是真的文字——元素的 textContent 永遠是真正的數值，其他程式與測試讀到的不變。
     2. 數值改變時，數字底下一條細線掃過：漲是紅、跌是綠（紅綠只用在這條細線）。
     3. 籌碼卡的小圖自己畫出來、柱子從基準線長出來、卡片依序浮起；隔日三區分布捲到才展開。樣式在 motion-fx.css。
   C 板有重量
     1. 進頁面時板一塊一塊落到桌上（MotionFx.land；首頁由 home-motion.js 呼叫，個股頁與自選股頁在這裡）。
     2. 盤中數值更新時，一道淡淡的光掃過那塊板。

   對外：window.MotionFx = { land(elements, { back }), roll(element), sheen(element) }；減少動態時這三個都是空的。 */
(function () {
  'use strict';

  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const LAND = 'cubic-bezier(.2, .9, .25, 1)';
  const ROLL = 'cubic-bezier(.16, 1, .3, 1)';
  const noop = () => {};
  if (still || !Element.prototype.animate) { window.MotionFx = { land: noop, roll: noop, sheen: noop }; return; }

  // ---------- C1. 板落到桌上 ----------
  // back：從上一頁「放回來」（由下往上、幅度小），用在返回時；平常是從略高處落下。
  function land(elements, { back = false, gap = 90 } = {}) {
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
    if (getComputedStyle(slab).position === 'static') slab.classList.add('fx-sheen-host');
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

  window.MotionFx = { land, roll, sheen };

  const start = () => {
    ['currentPrice', 'close', 'totalMarketValue'].forEach(id => watch(document.getElementById(id)));
    // 自選股頁沒有自己的進場程式：兩塊板在這裡落桌（清單各列的進場照 portfolio.js 原本的）
    if (document.body.classList.contains('pf')) land(document.querySelectorAll('.pf-bar, .pf-card:not([hidden])'));
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
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
