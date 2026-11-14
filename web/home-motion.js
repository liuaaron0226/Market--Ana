/* unslop 首頁範例：動態效果（2026-11-09），跟個股頁 motion.js 同一套規則
   1. 載入：報價→主K線依序淡入上移 8px（一次）
   2. 往下捲：直白分析進入畫面時才淡入（一次）。籌碼與選股看板 11-14 起收在分類鍵後面，展開的動態在 home.css
   3. 指數數字從前一個值滾到新值（盤中更新時看得出變化）
   4. 指標切換滑動底塊沿用 motion.js 的做法
   系統設定「減少動態」時全部跳過。方法 unslop by @heydamir, CC BY 4.0 */
(function () {
  'use strict';
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const EASE = 'cubic-bezier(.2, .8, .2, 1)';
  const rise = [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }];

  // 1. 進場
  ['.market-header', '.chart-workspace'].forEach((sel, i) => {
    const el = document.querySelector(sel);
    el && el.animate(rise, { duration: 420, delay: i * 80, easing: EASE, fill: 'backwards' });
  });

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

  // 3. 指數數字滾動
  const price = document.getElementById('currentPrice');
  let shown = null;
  const parse = t => { const n = Number(String(t).replace(/,/g, '')); return Number.isFinite(n) ? n : null; };
  if (price) new MutationObserver(() => {
    if (price.dataset.tweening) return;
    const target = parse(price.textContent);
    if (target === null) return;
    const from = shown === null ? target * .995 : shown;
    shown = target;
    if (from === target) return;
    const digits = (price.textContent.split('.')[1] || '').length;
    const start = performance.now();
    price.dataset.tweening = '1';
    const fmt = v => v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const step = now => {
      const t = Math.min(1, (now - start) / 600), k = 1 - Math.pow(1 - t, 3);
      price.textContent = fmt(from + (target - from) * k);
      if (t < 1) requestAnimationFrame(step); else { price.textContent = fmt(target); delete price.dataset.tweening; }
    };
    requestAnimationFrame(step);
  }).observe(price, { childList: true, characterData: true, subtree: true });

  // 4. 指標切換滑動底塊（與個股頁相同）
  const host = document.getElementById('chartStudies');
  let pill = null, lastKind = null;
  const order = ['volume', 'macd'];
  const place = animate => {
    const sw = host && host.querySelector('.kc-study-switcher');
    const on = sw && sw.querySelector('.kc-study-choice[aria-pressed="true"]');
    if (!on) return;
    if (!pill || !sw.contains(pill)) {
      pill = document.createElement('span'); pill.className = 'bd-switch-pill'; pill.setAttribute('aria-hidden', 'true');
      sw.prepend(pill); sw.classList.add('bd-has-pill'); animate = false;
    }
    const box = sw.getBoundingClientRect(), r = on.getBoundingClientRect();
    pill.style.transition = animate ? `transform 280ms ${EASE}` : 'none';
    pill.style.transform = `translateX(${r.left - box.left}px) scaleX(${r.width / 100})`;
    const kind = on.dataset.study;
    if (animate && lastKind && kind !== lastKind) {
      const pane = host.querySelector(`.kc-study-pane-${kind}`);
      const dir = order.indexOf(kind) > order.indexOf(lastKind) ? 1 : -1;
      pane && pane.animate([{ opacity: 0, transform: `translateX(${dir * 16}px)` }, { opacity: 1, transform: 'none' }], { duration: 260, easing: EASE });
    }
    lastKind = kind;
  };
  if (host) {
    new MutationObserver(muts => place(muts.some(m => m.attributeName === 'aria-pressed')))
      .observe(host, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed'] });
    addEventListener('resize', () => place(false));
  }
})();
