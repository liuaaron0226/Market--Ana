/* unslop 第二版範例：動態效果（2026-11-09，本人：「希望可以加一些適合的動態效果」）
   原則：只在「有變化要說明」時動。不循環、不視差、不彈跳，只動 transform／opacity。
   1. 載入：報價→舞台→指標，依序淡入上移 8px（一次）
   2. 數字：收盤價與階梯價位從前一個值滾到新值（換股時看得出變化）
   3. 價位階梯：由收盤往外依序展開
   4. 指標切換：副圖從切換方向滑入
   5. 展開區塊：高度平滑展開
   系統設定「減少動態」時全部跳過。方法 unslop by @heydamir, CC BY 4.0 */
(function () {
  'use strict';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const EASE = 'cubic-bezier(.2, .8, .2, 1)';
  const $ = id => document.getElementById(id);
  if (reduce) return;

  // ---------- 2. 數字滾動 ----------
  const last = new WeakMap();
  const parse = text => {
    const n = Number(String(text).replace(/,/g, ''));
    return Number.isFinite(n) ? n : null;
  };
  const decimals = text => (String(text).split('.')[1] || '').length;
  function tween(el) {
    const target = parse(el.textContent);
    const from = last.get(el);
    last.set(el, target);
    if (target === null || from === null || from === undefined || from === target) return;
    const digits = decimals(el.textContent);
    const start = performance.now();
    const dur = 520;
    el.dataset.tweening = '1';
    const step = now => {
      const t = Math.min(1, (now - start) / dur);
      const k = 1 - Math.pow(1 - t, 3);
      const v = from + (target - from) * k;
      el.textContent = v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
      if (t < 1) requestAnimationFrame(step);
      else { el.textContent = target.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }); delete el.dataset.tweening; }
    };
    requestAnimationFrame(step);
  }
  function watchNumbers() {
    ['close', 'ladderClose', 'resistance1', 'resistance2', 'support1', 'support2'].forEach(id => {
      const el = $(id);
      if (!el) return;
      // 第一次載入從 0 滾上來太花俏；改成從目標值的 96% 開始，只給一點「到位」感
      new MutationObserver(() => {
        if (el.dataset.tweening) return;
        if (!last.has(el)) { const v = parse(el.textContent); last.set(el, v === null ? null : v * .96); }
        tween(el);
      }).observe(el, { childList: true, characterData: true, subtree: true });
    });
  }

  // ---------- 1＋3. 進場（每次換股都重播，因為內容換了） ----------
  function enter() {
    const blocks = [document.querySelector('.bd-quote'), document.querySelector('.bd-guide'), document.querySelector('.bd-stage')];
    blocks.forEach((el, i) => el && el.animate(
      [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
      { duration: 420, delay: i * 70, easing: EASE, fill: 'backwards' }));
    const now = document.querySelector('.lad-now');
    const rows = [...document.querySelectorAll('#ladder > .lad:not([hidden])')];
    const center = rows.indexOf(now);
    rows.forEach((li, i) => {
      const dist = Math.abs(i - center);
      const dir = i < center ? 6 : i > center ? -6 : 0;
      li.animate(
        [{ opacity: 0, transform: `translateY(${dir}px)` }, { opacity: 1, transform: 'none' }],
        { duration: 360, delay: 180 + dist * 70, easing: EASE, fill: 'backwards' });
    });
  }
  function watchContent() {
    const content = $('content');
    let wasHidden = content.hidden;
    new MutationObserver(() => {
      if (wasHidden && !content.hidden) requestAnimationFrame(enter);
      wasHidden = content.hidden;
    }).observe(content, { attributes: true, attributeFilter: ['hidden'] });
    // 同一頁換股（查詢）時 content 不會再隱藏一次，所以也聽股名變化
    new MutationObserver(() => { if (!content.hidden) requestAnimationFrame(enter); })
      .observe($('stockName'), { childList: true, characterData: true, subtree: true });
  }

  // ---------- 4. 指標切換：新的指標區從切換的方向滑入（切換鍵收進指標名稱的選單，不再有滑動底塊） ----------
  function setupSwitcher() {
    const host = $('chartStudies');
    let lastActive = null;
    const order = ['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'];
    const slide = animate => {
      const on = host.querySelector('.kc-study-choice[aria-pressed="true"]');
      if (!on) return;
      const kind = on.dataset.study;
      if (animate && lastActive && kind !== lastActive) {
        const pane = host.querySelector(`.kc-study-pane-${kind}`);
        const dir = order.indexOf(kind) > order.indexOf(lastActive) ? 1 : -1;
        pane && pane.animate(
          [{ opacity: 0, transform: `translateX(${dir * 16}px)` }, { opacity: 1, transform: 'none' }],
          { duration: 260, easing: EASE });
      }
      lastActive = kind;
    };
    new MutationObserver(muts => slide(muts.some(m => m.attributeName === 'aria-pressed')))
      .observe(host, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed'] });
  }

  // ---------- 5. 展開區塊平滑 ----------
  function smoothDetails(root) {
    root.addEventListener('click', e => {
      const summary = e.target.closest('summary');
      if (!summary) return;
      const details = summary.parentElement;
      if (details.dataset.animating) { e.preventDefault(); return; }
      const opening = !details.open;
      e.preventDefault();
      const startH = details.offsetHeight;
      details.dataset.animating = '1';
      if (opening) details.open = true;
      const endH = opening ? details.scrollHeight : summary.offsetHeight;
      details.style.overflow = 'hidden';
      const anim = details.animate([{ height: `${startH}px` }, { height: `${endH}px` }], { duration: 240, easing: EASE });
      anim.onfinish = () => {
        if (!opening) details.open = false;
        details.style.overflow = '';
        delete details.dataset.animating;
      };
    });
  }

  // 價位來源：原本用 hidden 直接出現，改成淡入下移
  function setupSources() {
    const layer = $('layer1');
    new MutationObserver(() => {
      if (!layer.hidden) layer.animate(
        [{ opacity: 0, transform: 'translateY(-6px)' }, { opacity: 1, transform: 'none' }],
        { duration: 260, easing: EASE });
    }).observe(layer, { attributes: true, attributeFilter: ['hidden'] });
  }

  // 本檔放在 body 最後，元素都已存在；立即掛上監聽，才不會錯過第一次渲染
  watchNumbers();
  watchContent();
  setupSwitcher();
  setupSources();
  smoothDetails(document.body);
  if (!$('content').hidden) requestAnimationFrame(enter);
})();
