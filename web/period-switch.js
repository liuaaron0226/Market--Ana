/* 把原生下拉選單換成一枚小鍵（寫著現在選的是什麼），點了浮出全部選項（本人 11-15：參數收起來、互動時才出現）。
   用在 K 線的週期（#periodSelect，不分「盤中／趨勢」），以及任何標了 data-chip-menu 的 <select>（首頁長期核心持有的比較期間）。
   原本的 <select> 保留為唯一真值：選單的按鈕只負責改它的值並觸發 change；它的值或 disabled 變了會同步回小鍵與選單。
   小鍵與選單的開合由 chart-menus.js 統一處理。 */
(function () {
  'use strict';
  const compact = text => text.replace(/\s+/g, '');

  // label(選中那一項的字) 回傳小鍵上要寫的字
  function chipMenu(select, { wrapClass, menuId, name, label }) {
    const wrap = document.createElement('div');
    wrap.className = wrapClass;

    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chart-chip';
    chip.setAttribute('aria-expanded', 'false');
    chip.setAttribute('aria-controls', menuId);

    const menu = document.createElement('div');
    menu.className = 'chart-menu chart-period-menu';
    menu.id = menuId;
    menu.hidden = true;
    menu.setAttribute('role', 'radiogroup');
    menu.setAttribute('aria-label', name);

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

    // 左右方向鍵在可用的選項之間移動焦點；按 Enter 或空白鍵才切換（切換後選單會收回）
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
        if (on) chip.textContent = label(b.textContent);
      });
      chip.setAttribute('aria-label', `${name}：${chip.textContent}`);
    }

    const native = select.closest('label') || select;
    native.classList.add('hm-period-native');
    native.after(wrap);
    sync();
    // select 的值或 disabled 被別的程式改掉時同步
    new MutationObserver(sync).observe(select, { subtree: true, attributes: true, attributeFilter: ['disabled'] });
    select.addEventListener('change', sync);
    // minimal.js 直接設週期的 select.value 後會發 periodsync（loadPeriod 裡的一行）
    select.addEventListener('periodsync', sync);
  }

  const period = document.getElementById('periodSelect');
  // 週期的小鍵：首頁的選項是「日」「60分」，補成「日K」「60分K」（秒 K 不補）
  if (period) chipMenu(period, { wrapClass: 'hm-period', menuId: 'periodMenu', name: 'K 線週期', label: text => (/[K秒]$/.test(text) ? text : `${text}K`) });

  document.querySelectorAll('select[data-chip-menu]').forEach(select => {
    const caption = select.closest('label');
    chipMenu(select, { wrapClass: 'chart-select', menuId: `${select.id}Menu`, name: caption ? compact(caption.textContent.split('\n')[0]) || '選項' : '選項', label: text => text });
  });
})();
