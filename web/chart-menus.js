/* 圖上的小鍵與浮出的選單（本人 11-15：參數與功能收起來，互動時才出現）。
   小鍵：<button class="chart-chip" aria-expanded="false" aria-controls="選單的 id">
   選單：<div class="chart-menu" id="…" hidden>；選單裡帶 data-menu-close 的按鈕按了就收回；選單帶 data-menu-autoclose 的話，裡面任何按鈕按了都收回。
   小鍵帶 data-mirror="這個設定叫什麼"：上面的字跟著選單裡選中的那一項，那一項所在的群組隱藏時小鍵也不顯示。
   按住滑到要的再放開（本人 11-15）：按住小鍵，選單不用放開就浮出，手指滑到要的那一項（會亮起）放開就選到；滑到外面放開不選；
   按一下就放開和原本一樣，選單開著等人點。
   選中的那個框跟著手指走，放開時靠近哪一顆就選哪一顆（本人 11-15 更正）：有槽的成排切換鍵（首頁的分類鍵、明細板的買超／賣超）與帶記號的那一排鍵（.key-strip：首頁短中長期走勢展開後的指標、個股頁大戶持股展開後的級距）左右跟，直排的樣本清單（換一組比較樣本）上下跟；
   「選一個」的選單（週期、指標、排行、天數、買賣超、比較期間）橫排的左右跟、直排的上下跟，手指離開選單框就收掉、放開不選。
   沒有「選一個」的選單（均線設定板）沒有框：按住滑到另一顆按鈕，那一顆亮起，放開才生效。
   一次只開一個；按 Esc 或點選單以外的地方收回；選單帶 data-menu-focus 的話，打開時游標直接進裡面的輸入框
   （只給「打開就是為了打字」的選單用；均線設定板不要，不然手機一打開就跳出鍵盤）。
   K 線的週期（period-switch.js）、指標（chart-studies.js）、個股頁的均線、首頁的籌碼選股與長期核心持有都用這一套。 */
(function () {
  'use strict';
  const menuOf = chip => document.getElementById(chip.getAttribute('aria-controls'));
  // 帶 data-own-toggle 的小鍵（個股頁的「分析工具」）自己管開合，不是浮出選單，這裡不碰
  const CHIP = '.chart-chip[aria-controls]:not([data-own-toggle])';
  const openChip = () => document.querySelector('.chart-chip[aria-expanded="true"]:not([data-own-toggle])');

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
  const TRACKS = '.hm-cats, .chip-seg, .sample-list, .key-strip';   // 整組都是「選一個」的：滑的時候選中的那個框（.slide-thumb）跟著手指走
  const GROUPS = `.chart-menu, ${TRACKS}`;                          // 成排的鍵：在同一組裡滑。選單之外就是上面那幾種，加新的只改一處
  const MENU_KEYS = 'button:is([aria-checked], [aria-pressed], [aria-selected])';   // 選單裡「選一個」的那些項目；新增、刪除這類按鈕不算
  const SELECTED_KEY = '[aria-checked="true"], [aria-expanded="true"], [aria-pressed="true"], [aria-selected="true"]';
  const REACH_PX = 12;                                 // 選單：手指離項目這麼遠以內框才跟著，再遠就當成離開選單
  const SETTLE_MS = 200;                               // 放開之後框滑到定位的時間，和樣式裡 .is-settling 的轉場一致
  const CALM = window.matchMedia('(prefers-reduced-motion: reduce)');
  const SLIDE_START_PX = 8;                            // 動超過這麼多才算滑，不然就是點一下
  const STRAY_CLICK_MS = 150;                          // 補發的 click 緊跟在放開之後；窗口留短，免得把使用者緊接著的下一次點擊吃掉
  const PRESS_CLICK_MS = 400;                          // 按下去就打開的那一次，放開後這麼久以內到的 click 才算是同一下
  let slide = null;         // 這一次按住：{ group 可以滑到的範圍, origin 按下去的那一顆, chip 按的是小鍵的話, track 有框可以跟的那一組, thumb 跟著手指的框, x, y, moved, hot 手指底下（或最靠近）的那一顆 }
  let pressOpened = null;   // 這一次按下去才打開選單的小鍵：{ chip, releasedAt }。緊接著的那一下 click 不要又把它收回
  let stray = null;         // 滑動選到之後，瀏覽器可能再補一下 click 給按下去的地方：{ origin, at }，那一下不算

  const usable = button => Boolean(button) && !button.disabled && button.getClientRects().length > 0;
  const nodeAt = event => document.elementFromPoint(event.clientX, event.clientY);
  const isMenu = track => track.matches('.chart-menu');
  const keysOf = track => [...track.querySelectorAll(isMenu(track) ? MENU_KEYS : 'button')].filter(usable);
  // 這一組按鈕有沒有「選中的框」可以跟著手指：整組都是「選一個」的那幾種（TRACKS）一定有；選單要有「選一個」的項目才有
  const trackOf = group => (group.matches(TRACKS) || keysOf(group).length ? group : null);
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  const begin = (event, group, origin, chip) => ({ group, origin, chip, track: trackOf(group), thumb: null, x: event.clientX, y: event.clientY, moved: false, hot: null });
  // 接下來瀏覽器補發給按下去那個地方的 click 不算。時間從程式點完之後才起算，切換畫面花掉的時間不算在窗口裡
  const expectStrayClick = origin => { stray = { origin, at: performance.now() }; };

  // 手指底下（或最靠近）的那一顆做記號
  function markHot(button) {
    if (slide.hot === button) return;
    if (slide.hot) slide.hot.classList.remove('is-slide-hot');
    slide.hot = button;
    if (button) button.classList.add('is-slide-hot');
  }
  // 把框收掉（手指離開選單、回到小鍵上、滑鼠在視窗外放開、或上一次拖曳沒有收到放開）
  function removeThumb() {
    markHot(null);
    if (!slide.thumb) return;
    slide.thumb.remove();
    slide.thumb = null;
    if (!slide.track.querySelector('.slide-thumb')) slide.track.classList.remove('is-sliding');
  }
  // 把框放到畫面上的某個位置（座標是視窗的）；框是那一組裡絕對定位的一塊，所以換算成相對於那一組，那一組自己有捲動的話也要算進去
  function placeThumb(thumb, track, left, top, width, height) {
    const frame = track.getBoundingClientRect();
    thumb.style.width = `${width}px`;
    thumb.style.height = `${height}px`;
    thumb.style.transform = `translate(${left - frame.left - track.clientLeft + track.scrollLeft}px, ${top - frame.top - track.clientTop + track.scrollTop}px)`;
  }
  // 框跟著手指：大小照最靠近的那一顆，位置不超出所有項目合起來的範圍（一排的只會左右動，直排的只會上下動）
  function follow(event) {
    const track = slide.track, keys = keysOf(track);
    if (!keys.length) return;
    const boxes = keys.map(key => key.getBoundingClientRect()), x = event.clientX, y = event.clientY;
    const left = Math.min(...boxes.map(box => box.left)), right = Math.max(...boxes.map(box => box.right));
    const top = Math.min(...boxes.map(box => box.top)), bottom = Math.max(...boxes.map(box => box.bottom));
    // 選單：手指還在小鍵上（只是按的時候晃了一下），或離開了項目的範圍，就不跟——這時放開不會選到東西。
    // 有槽的那一排沒有「離開」，一路跟到放開
    const onChip = slide.chip && slide.chip.contains(nodeAt(event));
    if (onChip || (isMenu(track) && (x < left - REACH_PX || x > right + REACH_PX || y < top - REACH_PX || y > bottom + REACH_PX))) { removeThumb(); return; }
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
    placeThumb(slide.thumb, track, clamp(x - box.width / 2, left, right - box.width), clamp(y - box.height / 2, top, bottom - box.height), box.width, box.height);
    markHot(keys[near]);
  }
  // 放開：靠近哪一顆就選哪一顆，框滑到那一顆上再交還給那一顆自己的樣子
  function settle(track, thumb, target, origin, cancelled) {
    const done = () => {
      thumb.remove();
      if (target) target.classList.remove('is-slide-hot');
      if (!track.querySelector('.slide-thumb')) track.classList.remove('is-sliding');   // 還有另一次拖曳的框就先留著
    };
    if (cancelled || !target || !target.isConnected) { done(); return; }   // 拖到一半那一顆被重畫掉了就不選
    // 選單：選到原本那一項也照點（選單會收起，和點一下一樣）；有槽的那一排選到原本那一顆就不動（分類鍵再點一次是收起）
    if (isMenu(track) || target !== track.querySelector(SELECTED_KEY)) target.click();
    expectStrayClick(origin);   // 不擋的話，拖回原位放開會被補發的 click 把展開的那一類收起來
    if (CALM.matches || !target.isConnected) { done(); return; }
    thumb.classList.add('is-settling');
    const box = target.getBoundingClientRect();
    placeThumb(thumb, track, box.left, box.top, box.width, box.height);
    setTimeout(done, SETTLE_MS);
  }

  document.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0) return;
    pressOpened = null;
    if (slide) { removeThumb(); slide = null; }   // 上一次拖曳沒有收到放開（按下去的那一顆拖到一半被重畫掉之類）
    const chip = event.target.closest(CHIP);
    if (chip) {
      const menu = menuOf(chip);
      // 打開就是為了打字的選單照舊用點的：游標要在點完之後才進輸入框，手機鍵盤才會出來
      if (!menu || menu.hasAttribute('data-menu-focus')) return;
      if (chip.getAttribute('aria-expanded') !== 'true') {
        const current = openChip();
        if (current) set(current, false);
        set(chip, true);
        pressOpened = { chip, releasedAt: 0 };
      }
      slide = begin(event, menu, chip, chip);
      return;
    }
    const button = event.target.closest('button');
    const group = button && button.closest(GROUPS);
    if (group && usable(button)) slide = begin(event, group, button, null);
  });

  // 照指標現在的位置更新：動得夠遠才算滑；有框的讓框跟過去，沒有框的把底下那一顆做記號
  function update(event) {
    if (!slide.moved && Math.hypot(event.clientX - slide.x, event.clientY - slide.y) < SLIDE_START_PX) return;
    slide.moved = true;
    if (slide.track) { follow(event); return; }
    const node = nodeAt(event), button = node && node.closest('button');
    markHot(usable(button) && slide.group.contains(button) ? button : null);
  }

  document.addEventListener('pointermove', event => {
    if (!slide || !event.isPrimary) return;
    if (event.pointerType === 'mouse' && !(event.buttons & 1)) { removeThumb(); slide = null; pressOpened = null; return; }   // 在視窗外放開了滑鼠
    update(event);
  });

  function finish(event, cancelled) {
    if (!slide || !event.isPrimary) return;
    // 以放開的位置為準再更新一次：最後幾次移動如果來不及送到，不要照舊的位置選
    if (!cancelled) update(event);
    const { hot, moved, chip, origin, track, thumb } = slide;
    // 有框：照框的位置選。這條路之後不會再有「同一下」的 click 走到小鍵（補發的那一下會被擋掉），所以按下去打開的記號要清掉，
    // 不然下一次用鍵盤開選單會被當成同一下而沒有反應
    if (thumb) { slide = null; pressOpened = null; settle(track, thumb, hot, origin, cancelled); return; }
    markHot(null);
    slide = null;
    if (cancelled) { pressOpened = null; return; }
    if (pressOpened) pressOpened.releasedAt = performance.now();
    if (!moved) return;   // 沒有滑：就是點一下，交給下面的 click
    if (hot && hot !== origin) {
      pressOpened = null;
      hot.click();
      expectStrayClick(origin);
      return;
    }
    // 小鍵：滑出去之後在選單與小鍵以外的地方放開＝不選，收起。回到小鍵上放開當成點一下。
    const node = nodeAt(event);
    if (chip && chip.getAttribute('aria-expanded') === 'true' && !(node && (chip.contains(node) || menuOf(chip).contains(node)))) {
      pressOpened = null;
      close(chip, false);
      expectStrayClick(origin);
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
    const chip = event.target.closest(CHIP);
    const current = openChip();
    if (chip) {
      // 這一下按下去的時候已經打開了，放開跟著來的 click 不要又收回。隔了一陣子才來的 click（例如之後用鍵盤按）不算同一下
      const samePress = pressOpened && pressOpened.chip === chip && performance.now() - pressOpened.releasedAt < PRESS_CLICK_MS;
      pressOpened = null;
      if (samePress) return;
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

})();
