/* 設定均線的那一小塊（本人 2026-11-17 選樣品 A「點名稱打開」）：點 K 線上方那一行的均線名稱浮出來。
   每條一列：顏色點（點了從 8 色裡挑）、名稱、天數（可改）；向左滑露出刪除（swipe-rows.js）；最下面「＋ 加一條均線」。
   條數、天數都不設上限（本人「不要有上限」）；至少留一條（最後一條不能刪）。
   改了立刻存（ma-lines.js，首頁與個股頁共用一組）並通知頁面重畫。
   用法：const panel = MaSettings.mount({ host, onChange(lines) { … } })；panel.open()／panel.close()。
   host 是要放進去的外框（要是定位的，例如 K 線那一塊）；樣式在 ma-settings.css。 */
(function (global) {
  'use strict';

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function mount({ host, onChange }) {
    const MaLines = global.MaLines;
    const pop = el('div', 'ma-pop');
    pop.hidden = true;
    pop.tabIndex = -1;
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', '設定均線');
    const head = el('div', 'ma-pop-head');
    const close = el('button', 'ma-pop-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', '關閉');
    head.append(el('b', '', '均線'), el('span', 'ma-pop-hint', '向左滑刪除'), close);
    const list = el('ul', 'ma-list');
    const add = el('button', 'ma-add', '＋ 加一條均線');
    add.type = 'button';
    pop.append(head, list, add);
    host.append(pop);

    let lines = MaLines.read();
    let picking = -1;   // 哪一列的顏色正在挑
    let opener = null;  // 打開它的那一顆（收起來時焦點還給它）

    function commit(next, redraw) {
      lines = MaLines.write(next);
      if (onChange) onChange(lines);
      if (redraw) render();
    }

    function render() {
      head.querySelector('.ma-pop-hint').hidden = lines.length < 2;
      list.replaceChildren(...lines.map((line, index) => {
        const row = el('li', 'swipe-row');
        row.dataset.index = String(index);
        row.style.setProperty('--tone', `var(${MaLines.tokenOf(line.color)})`);
        const clip = el('div', 'swipe-clip');
        const acts = el('div', 'swipe-acts');
        acts.inert = true;
        if (lines.length > 1) {
          const remove = el('button', 'swipe-act is-danger', '刪除');
          remove.type = 'button';
          remove.setAttribute('aria-label', `刪除 MA${line.period}`);
          acts.append(remove);
        }
        const face = el('div', 'swipe-face');
        const dot = el('button', 'ma-color');
        dot.type = 'button';
        dot.setAttribute('aria-expanded', String(picking === index));
        dot.setAttribute('aria-label', `MA${line.period} 的顏色：${MaLines.nameOf(line.color)}，點了換顏色`);
        dot.addEventListener('click', () => { picking = picking === index ? -1 : index; render(); list.children[index]?.querySelector('.ma-color')?.focus(); });
        const name = el('span', 'ma-name', `MA${line.period}`);
        const input = el('input');
        input.type = 'number';
        input.inputMode = 'numeric';
        input.min = '2';
        input.value = String(line.period);
        input.setAttribute('aria-label', `MA${line.period} 的天數`);
        // 只改天數：原地換名稱，不整塊重畫（重畫會把正在點的那一格換掉，手機上接著點下一格會落空）。
        // 和別條重複或不是 2 以上的數字：格子改回原本的數字，不改
        input.addEventListener('change', () => {
          const next = MaLines.setPeriod(lines, index, input.value);
          if (!next) { input.value = String(lines[index].period); return; }
          commit(next, false);
          input.value = String(lines[index].period);
          name.textContent = `MA${lines[index].period}`;
          input.setAttribute('aria-label', `MA${lines[index].period} 的天數`);
        });
        face.append(dot, name, input, el('span', 'ma-unit', '日'));
        if (lines.length > 1) {
          const more = el('button', 'swipe-more', '⋯');   // 鍵盤與螢幕報讀用它把刪除滑出來
          more.type = 'button';
          more.setAttribute('aria-label', `MA${line.period} 的動作`);
          face.append(more);
        }
        clip.append(acts, face);
        row.append(clip);
        if (picking === index) row.append(palette(index));
        return row;
      }));
    }

    // 8 色挑一個：目前的那一色標成按下
    function palette(index) {
      const strip = el('div', 'ma-palette');
      strip.setAttribute('role', 'group');
      strip.setAttribute('aria-label', `MA${lines[index].period} 的顏色`);
      for (const item of MaLines.PALETTE) {
        const swatch = el('button', 'ma-swatch');
        swatch.type = 'button';
        swatch.style.setProperty('--tone', `var(${item.token})`);
        swatch.setAttribute('aria-label', item.name);
        swatch.setAttribute('aria-pressed', String(item.key === lines[index].color));
        swatch.addEventListener('click', () => {
          picking = -1;
          commit(lines.map((line, i) => (i === index ? { ...line, color: item.key } : line)), true);
          list.children[index]?.querySelector('.ma-color')?.focus();   // 整塊重畫過：焦點回到那一列的顏色點
        });
        strip.append(swatch);
      }
      return strip;
    }

    // 刪除是手指放開時就做；刪完那一列不見、「＋ 加一條均線」往上移到手指底下，
    // 瀏覽器隨後補發的那一下點擊會落在它上面又加回一條（Chromium 實測）——刪除後一小段時間內的點擊不算
    const GHOST_MS = global.SwipeRows ? global.SwipeRows.GHOST_MS : 350;
    let actedAt = -Infinity;
    const swipe = global.SwipeRows ? global.SwipeRows.bind(list, {
      onAct(_button, row) {
        actedAt = performance.now(); picking = -1;
        const keyboard = pop.contains(document.activeElement);
        commit(lines.filter((_, i) => i !== Number(row.dataset.index)), true);
        if (keyboard) add.focus();   // 用鍵盤刪的：那一列不在了，焦點放在「＋ 加一條均線」
      },
    }) : null;
    // 鍵盤按的（detail 是 0）不算補發的
    pop.addEventListener('click', event => { if (event.detail !== 0 && performance.now() - actedAt < GHOST_MS) { event.stopPropagation(); event.preventDefault(); } }, true);
    add.addEventListener('click', () => { picking = -1; commit(MaLines.add(lines), true); });
    close.addEventListener('click', () => api.close());

    // 點這一塊以外（名稱本身除外，那是開關）或按 Esc 就收起來
    function outside(event) { if (!event.target.closest('.ma-pop, [data-ma-open]')) api.close(); }
    function escape(event) { if (event.key === 'Escape') api.close(); }

    const api = {
      open() {
        const latest = MaLines.read();
        if (JSON.stringify(latest) !== JSON.stringify(lines) && onChange) onChange(latest);   // 別頁改過：圖也換成最新的
        lines = latest;
        picking = -1;
        render();
        opener = document.activeElement;
        pop.hidden = false;
        pop.focus({ preventScroll: true });
        document.addEventListener('pointerdown', outside);
        document.addEventListener('keydown', escape);
      },
      close() {
        if (pop.hidden) return;
        pop.hidden = true;
        if (swipe) swipe.close();
        document.removeEventListener('pointerdown', outside);
        document.removeEventListener('keydown', escape);
        // 焦點在這一塊裡面（鍵盤操作）才還給打開它的那一顆；點外面收起來的，焦點留在點到的地方
        if (pop.contains(document.activeElement) && opener && opener.isConnected) opener.focus({ preventScroll: true });
      },
      get isOpen() { return !pop.hidden; },
    };
    return api;
  }

  global.MaSettings = { mount };
})(window);
