/* K 線週期收成一枚小鍵（寫著現在的週期），點了浮出全部週期的選單（本人 11-15：參數收起來、互動時才出現；不分「盤中／趨勢」）。
   原 <select id="periodSelect"> 保留為唯一真值：選單的按鈕只負責改 select 的值並觸發 change，
   select 的 disabled／value 變化會同步回小鍵與選單。小鍵與選單的開合由 chart-menus.js 統一處理。 */
(function () {
  'use strict';
  const select = document.getElementById('periodSelect');
  if (!select) return;
  const wrap = document.createElement('div');
  wrap.className = 'hm-period';

  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'chart-chip';
  chip.setAttribute('aria-expanded', 'false');
  chip.setAttribute('aria-controls', 'periodMenu');

  const menu = document.createElement('div');
  menu.className = 'chart-menu chart-period-menu';
  menu.id = 'periodMenu';
  menu.hidden = true;
  menu.setAttribute('role', 'radiogroup');
  menu.setAttribute('aria-label', 'K 線週期');

  const compact = text => text.replace(/\s+/g, '');
  const buttons = [...select.options].map(opt => {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.dataset.value = opt.value;
    b.dataset.menuClose = '';
    b.textContent = compact(opt.textContent);
    b.addEventListener('click', () => {
      if (b.disabled || select.value === opt.value) return;
      select.value = opt.value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      sync();
    });
    menu.append(b);
    return [b, opt];
  });
  wrap.append(chip, menu);

  // 左右方向鍵在可用的週期之間移動焦點；按 Enter 或空白鍵才切換（切換後選單會收回）
  menu.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const live = buttons.filter(([b]) => !b.disabled).map(([b]) => b);
    const i = live.indexOf(document.activeElement);
    if (i < 0) return;
    live[(i + (e.key === 'ArrowRight' ? 1 : -1) + live.length) % live.length].focus();
    e.preventDefault();
  });

  function sync() {
    buttons.forEach(([b, opt]) => {
      const on = select.value === opt.value;
      b.disabled = opt.disabled;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
      b.title = opt.disabled ? '即時資料目前連不上' : '';
      // 小鍵寫現在的週期；首頁的選項是「日」「60分」，補成「日K」「60分K」（秒 K 不補）
      if (on) chip.textContent = /[K秒]$/.test(b.textContent) ? b.textContent : `${b.textContent}K`;
    });
    chip.setAttribute('aria-label', `K 線週期：${chip.textContent}`);
  }

  const label = select.closest('label') || select;
  label.classList.add('hm-period-native');
  label.after(wrap);
  sync();
  // select 的值或 disabled 被 minimal.js 改掉時同步
  new MutationObserver(sync).observe(select, { subtree: true, attributes: true, attributeFilter: ['disabled'] });
  select.addEventListener('change', sync);
  // minimal.js 直接設 select.value 後會發 periodsync（loadPeriod 裡的一行）
  select.addEventListener('periodsync', sync);
})();
