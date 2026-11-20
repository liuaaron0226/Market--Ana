/* unslop 首頁範例：動態效果（2026-11-09），跟個股頁 motion.js 同一套規則
   1. 載入：報價→主K線兩塊板依序落到桌上（一次；11-20 起，原本是淡入上移 8px）
   2. 往下捲：直白分析進入畫面時才淡入（一次）。籌碼與選股看板 11-14 起收在分類鍵後面，展開的動態在 home.css
   3. 指數數字一位一位滾到新值（盤中更新時看得出變化）：11-20 起由 motion-fx.js 做，這裡不再做
   4. 切換指標時，新的指標區從切換的方向滑進來
   系統設定「減少動態」時全部跳過。方法 unslop by @heydamir, CC BY 4.0 */
(function () {
  'use strict';
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const EASE = 'cubic-bezier(.2, .8, .2, 1)';
  const rise = [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }];

  // 1. 進場：兩塊板依序落到桌上（本人 11-20 選「C 板有重量」；motion-fx.js 的 MotionFx.land）
  window.MotionFx.land(['.market-header', '.chart-workspace'].map(sel => document.querySelector(sel)));

  // 2. 捲動到才出現
  const io = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      io.unobserve(entry.target);
      entry.target.animate(rise, { duration: 420, easing: EASE, fill: 'backwards' });
    });
  }, { rootMargin: '0px 0px -10% 0px' });
  const readCard = document.querySelector('.read-card');
  // 一開始就在畫面內的不再重播
  if (readCard && readCard.getBoundingClientRect().top >= innerHeight) io.observe(readCard);

  // 4. 切換指標時，新的指標區從切換的方向滑進來（與個股頁相同）。切換鍵收進指標名稱的選單，不再有滑動底塊。
  const host = document.getElementById('chartStudies');
  let lastKind = null;
  const order = ['volume', 'macd', 'kd', 'rsi', 'foreign', 'trust', 'dealer'];
  const slide = animate => {
    const on = host.querySelector('.kc-study-choice[aria-pressed="true"]');
    if (!on) return;
    const kind = on.dataset.study;
    if (animate && lastKind && kind !== lastKind) {
      const pane = host.querySelector(`.kc-study-pane-${kind}`);
      const dir = order.indexOf(kind) > order.indexOf(lastKind) ? 1 : -1;
      pane && pane.animate([{ opacity: 0, transform: `translateX(${dir * 16}px)` }, { opacity: 1, transform: 'none' }], { duration: 260, easing: EASE });
    }
    lastKind = kind;
  };
  if (host) {
    new MutationObserver(muts => slide(muts.some(m => m.attributeName === 'aria-pressed')))
      .observe(host, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed'] });
  }
})();
