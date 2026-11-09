/* unslop 範例：K 線週期改成分段按鈕（本人 11-09 指正原生下拉「根本沒有去設計」）
   原 <select id="periodSelect"> 保留為唯一真值：按鈕只負責改 select 的值並觸發 change，
   select 的 disabled／value 變化會同步回按鈕。minimal.js 不需要改。
   方法 unslop by @heydamir, CC BY 4.0 */
(function () {
  'use strict';
  const select = document.getElementById('periodSelect');
  if (!select) return;
  const wrap = document.createElement('div');
  wrap.className = 'hm-period';
  wrap.setAttribute('role', 'radiogroup');
  wrap.setAttribute('aria-label', 'K 線週期');

  const groups = [...select.querySelectorAll('optgroup')];
  const buttons = [];
  (groups.length ? groups : [select]).forEach((group, gi) => {
    const box = document.createElement('div');
    box.className = 'hm-period-group';
    if (group.label) {
      const cap = document.createElement('span');
      cap.className = 'hm-period-cap';
      cap.textContent = group.label;
      box.append(cap);
    }
    const seg = document.createElement('div');
    seg.className = 'hm-period-seg';
    [...group.querySelectorAll('option')].forEach(opt => {
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'radio');
      b.dataset.value = opt.value;
      b.textContent = opt.textContent.replace(/\s+/g, '');
      b.addEventListener('click', () => {
        if (b.disabled || select.value === opt.value) return;
        select.value = opt.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        sync();
      });
      seg.append(b);
      buttons.push([b, opt]);
    });
    box.append(seg);
    wrap.append(box);
    if (gi < groups.length - 1) {
      const sep = document.createElement('span');
      sep.className = 'hm-period-sep';
      sep.setAttribute('aria-hidden', 'true');
      wrap.append(sep);
    }
  });

  // 左右方向鍵在可用的週期之間移動
  wrap.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const live = buttons.filter(([b]) => !b.disabled).map(([b]) => b);
    const i = live.indexOf(document.activeElement);
    if (i < 0) return;
    const next = live[(i + (e.key === 'ArrowRight' ? 1 : -1) + live.length) % live.length];
    next.focus(); next.click(); e.preventDefault();
  });

  function sync() {
    buttons.forEach(([b, opt]) => {
      const on = select.value === opt.value;
      b.disabled = opt.disabled;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
      b.title = opt.disabled ? '盤中資料目前不可用' : '';
    });
  }

  const label = select.closest('label') || select;
  label.classList.add('hm-period-native');
  label.after(wrap);
  sync();
  // select 的值或 disabled 被 minimal.js 改掉時同步
  new MutationObserver(sync).observe(select, { subtree: true, attributes: true, attributeFilter: ['disabled'] });
  select.addEventListener('change', sync);
  // minimal.js 直接設 select.value 後會發 periodsync（範例在 loadPeriod 加的一行）
  select.addEventListener('periodsync', sync);
})();
