/* 圖上的小鍵與浮出的選單（本人 11-15：參數與功能收起來，互動時才出現）。
   小鍵：<button class="chart-chip" aria-expanded="false" aria-controls="選單的 id">
   選單：<div class="chart-menu" id="…" hidden>；選單裡帶 data-menu-close 的按鈕按了就收回；選單帶 data-menu-autoclose 的話，裡面任何按鈕按了都收回。
   小鍵帶 data-mirror="這個設定叫什麼"：上面的字跟著選單裡選中的那一項，那一項所在的群組隱藏時小鍵也不顯示。
   按住滑到要的再放開（本人 11-15）：按住小鍵，選單不用放開就浮出，手指滑到要的那一項（會亮起）放開就選到；滑到外面放開不選；
   按一下就放開和原本一樣，選單開著等人點。
   選中的那個框跟著手指走，放開時靠近哪一顆就選哪一顆（本人 11-15 更正）：有槽的成排切換鍵（首頁的分類鍵、明細板的買超／賣超）左右跟；
   「選一個」的選單（週期、指標、排行、天數、買賣超、比較期間）橫排的左右跟、直排的上下跟，手指離開選單框就收掉、放開不選。
   沒有「選一個」的選單（均線設定板）沒有框：按住滑到另一顆按鈕，那一顆亮起，放開才生效。
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

  // ---------- 按住滑到要的再放開 ----------
  const GROUPS = '.chart-menu, .hm-cats, .chip-seg';   // 成排的鍵：在同一組裡滑
  const TRACKS = '.hm-cats, .chip-seg';                // 其中有槽的：滑的時候選中的那個框（.slide-thumb）跟著手指走
  const MENU_KEYS = 'button:is([aria-checked], [aria-pressed], [aria-selected])';   // 選單裡「選一個」的那些項目；新增、刪除這類按鈕不算
  const SELECTED_KEY = '[aria-checked="true"], [aria-expanded="true"], [aria-pressed="true"], [aria-selected="true"]';
  const REACH_PX = 28;                                 // 選單：手指離項目這麼遠以內框才跟著，再遠就當成離開選單
  const SETTLE_MS = 200;                               // 放開之後框滑到定位的時間，和樣式裡 .is-settling 的轉場一致
  const CALM = window.matchMedia('(prefers-reduced-motion: reduce)');
  const SLIDE_START_PX = 8;                            // 動超過這麼多才算滑，不然就是點一下
  const STRAY_CLICK_MS = 150;                          // 補發的 click 緊跟在放開之後；窗口留短，免得把使用者緊接著的下一次點擊吃掉
  let slide = null;         // 這一次按住：{ group 可以滑到的範圍, origin 按下去的那一顆, chip 按的是小鍵的話, track 有槽的那一排, thumb 跟著手指的框, x, y, moved, hot 手指底下（或最靠近）的那一顆 }
  let pressOpened = null;   // 這一次按下去才打開選單的小鍵：緊接著的那一下 click 不要又把它收回
  let stray = null;         // 滑動選到之後，瀏覽器可能再補一下 click 給按下去的地方：{ origin, at }，那一下不算

  const usable = button => Boolean(button) && !button.disabled && button.getClientRects().length > 0;
  const nodeAt = event => document.elementFromPoint(event.clientX, event.clientY);
  function heat(button) {
    if (slide.hot === button) return;
    if (slide.hot) slide.hot.classList.remove('is-slide-hot');
    slide.hot = button;
    if (button) button.classList.add('is-slide-hot');
  }

  const isMenu = track => track.matches('.chart-menu');
  const keysOf = track => [...track.querySelectorAll(isMenu(track) ? MENU_KEYS : 'button')].filter(usable);
  // 這一組按鈕有沒有「選中的框」可以跟著手指：有槽的那兩種一定有；選單要有「選一個」的項目才有
  const trackOf = group => (group.matches(TRACKS) || keysOf(group).length ? group : null);
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  // 把框收掉（手指離開選單、或滑鼠在視窗外放開）
  function drop() {
    heat(null);
    if (!slide.thumb) return;
    slide.thumb.remove();
    slide.thumb = null;
    if (!slide.track.querySelector('.slide-thumb')) slide.track.classList.remove('is-sliding');
  }
  // 把框放到畫面上的某個位置（座標是視窗的）；框是槽裡絕對定位的一塊，所以換算成相對於槽
  function put(thumb, track, left, top, width, height) {
    const frame = track.getBoundingClientRect();
    thumb.style.width = `${width}px`;
    thumb.style.height = `${height}px`;
    thumb.style.transform = `translate(${left - frame.left - track.clientLeft}px, ${top - frame.top - track.clientTop}px)`;
  }
  // 框跟著手指：大小照最靠近的那一顆，位置不超出所有項目合起來的範圍（一排的只會左右動，直排的只會上下動）
  function follow(event) {
    const track = slide.track, keys = keysOf(track);
    if (!keys.length) return;
    const boxes = keys.map(key => key.getBoundingClientRect()), x = event.clientX, y = event.clientY;
    const left = Math.min(...boxes.map(box => box.left)), right = Math.max(...boxes.map(box => box.right));
    const top = Math.min(...boxes.map(box => box.top)), bottom = Math.max(...boxes.map(box => box.bottom));
    // 選單：手指離開項目的範圍就不跟了（這時放開＝不選）。有槽的那一排沒有「離開」，一路跟到放開
    if (isMenu(track) && (x < left - REACH_PX || x > right + REACH_PX || y < top - REACH_PX || y > bottom + REACH_PX)) { drop(); return; }
    const gap = box => Math.hypot(x - (box.left + box.width / 2), y - (box.top + box.height / 2));
    const near = boxes.reduce((best, box, index) => (gap(box) < gap(boxes[best]) ? index : best), 0);
    if (!slide.thumb) {
      slide.thumb = document.createElement('span');
      slide.thumb.className = 'slide-thumb';
      slide.thumb.setAttribute('aria-hidden', 'true');
      track.classList.add('is-sliding');
      track.append(slide.thumb);
    }
    const box = boxes[near];
    put(slide.thumb, track, clamp(x - box.width / 2, left, right - box.width), clamp(y - box.height / 2, top, bottom - box.height), box.width, box.height);
    heat(keys[near]);
  }
  // 放開：靠近哪一顆就選哪一顆，框滑到那一顆上再交還給那一顆自己的樣子
  function settle(track, thumb, target, origin, cancelled) {
    const done = () => {
      thumb.remove();
      if (target) target.classList.remove('is-slide-hot');
      if (!track.querySelector('.slide-thumb')) track.classList.remove('is-sliding');   // 還有另一次拖曳的框就先留著
    };
    if (cancelled || !target) { done(); return; }
    stray = { origin, at: performance.now() };   // 拖過之後瀏覽器補發的 click 不算，不然拖回原位放開會把展開的那一類收起來
    // 選單：選到原本那一項也照點（選單會收起，和點一下一樣）；有槽的那一排選到原本那一顆就不動（分類鍵再點一次是收起）
    if (isMenu(track) || target !== track.querySelector(SELECTED_KEY)) target.click();
    if (CALM.matches) { done(); return; }
    thumb.classList.add('is-settling');
    const box = target.getBoundingClientRect();
    put(thumb, track, box.left, box.top, box.width, box.height);
    setTimeout(done, SETTLE_MS);
  }

  document.addEventListener('pointerdown', event => {
    pressOpened = null;
    if (!event.isPrimary || event.button !== 0) return;
    const chip = event.target.closest('.chart-chip[aria-controls]');
    if (chip) {
      const menu = menuOf(chip);
      // 打開就是為了打字的選單照舊用點的：游標要在點完之後才進輸入框，手機鍵盤才會出來
      if (!menu || menu.hasAttribute('data-menu-focus')) return;
      if (chip.getAttribute('aria-expanded') !== 'true') {
        const current = openChip();
        if (current) set(current, false);
        set(chip, true);
        pressOpened = chip;
      }
      slide = { group: menu, origin: chip, chip, track: trackOf(menu), thumb: null, x: event.clientX, y: event.clientY, moved: false, hot: null };
      return;
    }
    const button = event.target.closest('button');
    const group = button && button.closest(GROUPS);
    if (group && usable(button)) slide = { group, origin: button, chip: null, track: trackOf(group), thumb: null, x: event.clientX, y: event.clientY, moved: false, hot: null };
  });

  document.addEventListener('pointermove', event => {
    if (!slide || !event.isPrimary) return;
    if (event.pointerType === 'mouse' && !(event.buttons & 1)) { drop(); slide = null; return; }   // 在視窗外放開了滑鼠
    if (!slide.moved && Math.hypot(event.clientX - slide.x, event.clientY - slide.y) < SLIDE_START_PX) return;
    slide.moved = true;
    if (slide.track) { follow(event); return; }
    const node = nodeAt(event), button = node && node.closest('button');
    heat(usable(button) && slide.group.contains(button) ? button : null);
  });

  function finish(event, cancelled) {
    if (!slide || !event.isPrimary) return;
    const { hot, moved, chip, origin, track, thumb } = slide;
    if (thumb) { slide = null; settle(track, thumb, hot, origin, cancelled); return; }
    heat(null);
    slide = null;
    if (cancelled || !moved) return;   // 沒有滑：就是點一下，交給下面的 click
    if (hot && hot !== origin) {
      pressOpened = null;
      stray = { origin, at: performance.now() };
      hot.click();
      return;
    }
    // 小鍵：滑出去之後在選單與小鍵以外的地方放開＝不選，收起。回到小鍵上放開當成點一下。
    const node = nodeAt(event);
    if (chip && chip.getAttribute('aria-expanded') === 'true' && !(node && (chip.contains(node) || menuOf(chip).contains(node)))) {
      pressOpened = null;
      stray = { origin, at: performance.now() };
      close(chip, false);
    }
  }
  document.addEventListener('pointerup', event => finish(event, false));
  document.addEventListener('pointercancel', event => finish(event, true));

  // 滑動選到（或滑出去不選）之後，瀏覽器補發的那一下 click：滑鼠會發給按下與放開兩處共同的外層，觸控在手指只動一點點時會發給按下去的那一顆。
  document.addEventListener('click', event => {
    if (!stray || !event.isTrusted) return;
    const { origin, at } = stray;
    stray = null;
    if (performance.now() - at > STRAY_CLICK_MS) return;
    if (origin.contains(event.target) || event.target.contains(origin)) { event.stopPropagation(); event.preventDefault(); }
  }, true);

  document.addEventListener('click', event => {
    const chip = event.target.closest('.chart-chip[aria-controls]');
    const current = openChip();
    if (chip) {
      if (pressOpened === chip) { pressOpened = null; return; }   // 這一下按下去的時候已經打開了
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
