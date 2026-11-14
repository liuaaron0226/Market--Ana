/* 圖上的小鍵與浮出的選單（本人 11-15：參數與功能收起來，互動時才出現）。
   小鍵：<button class="chart-chip" aria-expanded="false" aria-controls="選單的 id">
   選單：<div class="chart-menu" id="…" hidden>；選單裡帶 data-menu-close 的按鈕按了就收回；選單帶 data-menu-autoclose 的話，裡面任何按鈕按了都收回。
   小鍵帶 data-mirror="這個設定叫什麼"：上面的字跟著選單裡選中的那一項，那一項所在的群組隱藏時小鍵也不顯示。
   一次只開一個；按 Esc 或點選單以外的地方收回；選單帶 data-menu-focus 的話，打開時游標直接進裡面的輸入框
   （只給「打開就是為了打字」的選單用；均線設定板不要，不然手機一打開就跳出鍵盤）。
   K 線的週期（period-switch.js）、指標（chart-studies.js）、個股頁的均線、首頁的籌碼選股與長期核心持有都用這一套。 */
(function () {
  'use strict';
  const menuOf = chip => document.getElementById(chip.getAttribute('aria-controls'));
  const openChip = () => document.querySelector('.chart-chip[aria-expanded="true"]');

  function set(chip, open) {
    const menu = menuOf(chip);
    if (!menu) return;
    chip.setAttribute('aria-expanded', String(open));
    menu.hidden = !open;
    const field = open && menu.hasAttribute('data-menu-focus') && menu.querySelector('input');
    if (field) field.focus();
  }

  // 收回之後把鍵盤焦點放回小鍵。指標的小鍵每一種指標各有一枚，選完之後原本那枚會跟著舊的指標區隱藏，所以找現在看得到的那一枚。
  function close(chip, refocus) {
    set(chip, false);
    if (!refocus) return;
    const id = chip.getAttribute('aria-controls');
    const shown = [...document.querySelectorAll(`.chart-chip[aria-controls="${id}"]`)].find(item => item.getClientRects().length);
    if (shown) shown.focus();
  }

  document.addEventListener('click', event => {
    const chip = event.target.closest('.chart-chip[aria-controls]');
    const current = openChip();
    if (chip) {
      if (current && current !== chip) set(current, false);
      set(chip, chip.getAttribute('aria-expanded') !== 'true');
      return;
    }
    if (!current) return;
    // 用事件當下走過的路徑判斷是不是點在選單裡：均線的刪除鍵按了會重畫，等事件傳到這裡時那顆鍵已經不在頁面上了。
    const inside = event.composedPath().includes(menuOf(current));
    const picked = inside && event.target.closest(menuOf(current).hasAttribute('data-menu-autoclose') ? 'button' : '[data-menu-close]');
    if (!inside || picked) close(current, Boolean(picked));
  });

  document.addEventListener('keydown', event => {
    const current = event.key === 'Escape' && openChip();
    if (current) close(current, true);
  });

  // 小鍵上的字跟著選單裡選中的那一項（data-mirror）。選單裡的按鈕是別的程式畫的、選中狀態也是別的程式改的，這裡只跟著讀。
  const SELECTED = '[aria-selected="true"], [aria-pressed="true"], [aria-checked="true"]';
  document.querySelectorAll('.chart-chip[data-mirror]').forEach(chip => {
    const menu = menuOf(chip);
    if (!menu) return;
    // 選單裡面的某一層被隱藏（這個設定現在用不到）。只看到選單為止：選單自己平常就是收著的
    const tucked = node => {
      for (let item = node; item && item !== menu; item = item.parentElement) if (item.hidden) return true;
      return false;
    };
    const sync = () => {
      const on = menu.querySelector(SELECTED);
      chip.hidden = !on || tucked(on);
      // 選單裡的勾選框有勾、而且這個排行用得到它（沒有被隱藏）時，小鍵上多一個標記
      const flagged = [...menu.querySelectorAll('input[type="checkbox"]:checked')].some(box => !tucked(box));
      if (!on) return;
      chip.textContent = on.textContent.trim() + (flagged ? chip.dataset.mirrorFlag || '' : '');
      // 只念「當日」聽不出是什麼設定，前面補上這個設定的名稱
      if (chip.dataset.mirror) chip.setAttribute('aria-label', `${chip.dataset.mirror}：${chip.textContent}`);
    };
    new MutationObserver(sync).observe(menu, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['aria-selected', 'aria-pressed', 'aria-checked', 'hidden'] });
    menu.addEventListener('change', sync);
    sync();
  });

  // 個股頁的均線小鍵：寫著現在的期數（例如 18·45）。期數由 stock.js 畫進 #maControls，這裡只跟著讀。
  // 新增、刪除會重畫（MutationObserver 看得到）；直接改某一格的數字不一定重畫，所以也聽 change。
  const periods = document.getElementById('maControls'), value = document.getElementById('maChipValue');
  if (periods && value) {
    const sync = () => { value.textContent = [...periods.querySelectorAll('input')].map(input => input.value).filter(Boolean).join('·') || '未設定'; };
    new MutationObserver(sync).observe(periods, { childList: true, subtree: true });
    periods.addEventListener('change', sync);
    sync();
  }
})();
