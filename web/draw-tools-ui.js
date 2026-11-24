/* 分析工具（本人 2026-11-24 選樣品 A：「大盤也要分析工具；分析工具的呈現有點太亂，整理介面」）。個股頁與大盤 K 線共用。
   沒有工具列：「分析工具」是一枚小鍵，點了浮出一張分三組的選單（線條／區間／標記），每一項是圖示＋名稱；選了就收起。
   畫的時候那枚鍵寫著正在用的工具（深色），旁邊一顆 ✕ 離開；選了一條線時同一行多出要用的鍵（刪除；到價提醒的線另有鈴鐺），沒有別的。
   只管呈現：選工具、離開、刪除、提醒的開關都照舊叫 KChart 的 setTool／cancelDrawing／deleteSelectedDrawing／setSelectedAlert；
   畫線行為在 chart-drawings.js、存檔在 drawing-sync.js。小鍵與選單的開合、按住滑到要的再放開交給 chart-menus.js（樣式在 chart-studies.css）。 */
(function () {
  'use strict';
  const PATHS = {
    trend: '<path d="M7.4 16.2 16.6 7.8"/><circle cx="5.5" cy="18" r="2.5"/><circle cx="18.5" cy="6" r="2.5"/>',
    hline: '<path d="M3 12h6.5m5 0H21"/><circle cx="12" cy="12" r="2.5"/>',
    ray: '<path d="M7.4 16.2 21 3.8"/><circle cx="5.5" cy="18" r="2.5"/>',
    rect: '<path d="M8 6.5h8M17.5 8v8M16 17.5H8M6.5 16V8"/><circle cx="6.5" cy="6.5" r="2"/><circle cx="17.5" cy="17.5" r="2"/>',
    fib: '<path d="M3 5.5h12.5M3 10.5h18M3 14h18M8.5 18.5H21"/><circle cx="18.5" cy="5.5" r="2.5"/><circle cx="5.5" cy="18.5" r="2.5"/>',
    text: '<path d="M5.5 8V5.5h13V8M12 5.5v13M9.5 18.5h5"/>',
    measure: '<path d="M3.5 15.5 15.5 3.5l5 5-12 12zM7.2 11.8l2 2M10.2 8.8l2.8 2.8M13.2 5.8l2 2"/>',
    channel: '<path d="M6.7 12.6 14.3 6.4M8.5 20.5 21 10.3"/><circle cx="4.8" cy="14.2" r="2.3"/><circle cx="16.2" cy="4.8" r="2.3"/>',
    alert: '<path d="M3 19h18M8.3 14.5v-4a3.7 3.7 0 0 1 7.4 0v4l1.3 1.5H7zM12 4.5v2.3"/>',
    vprofile: '<path d="M4.5 3.5v17M4.5 6.5h7.5M4.5 10.5h14M4.5 14.5h10M4.5 18.5h5"/>',
    pen: '<path d="m14.5 5.5 4 4L9 19H5v-4z"/><path d="m12.5 7.5 4 4"/>',
    bell: '<path d="M6.8 16.5v-5.2a5.2 5.2 0 0 1 10.4 0v5.2l1.6 2H5.2zM10.2 21h3.6M12 3.8v2.3"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10.2 10.5v5.5m3.6-5.5v5.5"/>',
    close: '<path d="m7 7 10 10M17 7 7 17"/>',
  };
  // 分組與順序（本人 11-24 選 A 時的分法）
  const TOOLS = [
    { kind: 'trend', name: '趨勢線', group: '線條' },
    { kind: 'ray', name: '射線', group: '線條' },
    { kind: 'hline', name: '水平線', group: '線條' },
    { kind: 'channel', name: '平行通道', group: '線條' },
    { kind: 'rect', name: '矩形', group: '區間' },
    { kind: 'fib', name: '黃金分割', group: '區間' },
    { kind: 'measure', name: '量測', group: '區間' },
    { kind: 'vprofile', name: '區間成交量分布', group: '區間' },
    { kind: 'text', name: '文字標註', group: '標記' },
    { kind: 'alert', name: '到價提醒', group: '標記' },
  ];
  const svg = name => `<svg class="dt-svg" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name]}</svg>`;

  // 文字標註寫到一半時按這些鍵不搬走焦點（輸入框會當成「確定」把寫到一半的存下去）；鍵盤（Tab、Enter、空白鍵）不受影響
  const keepFocus = button => button.addEventListener('mousedown', event => event.preventDefault());
  function button(className, html, attrs) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = className;
    node.innerHTML = html;
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    keepFocus(node);
    return node;
  }

  // bar：放鍵的那一行（選單以它為定位基準）；keys：鍵排在裡面的那一格；before：鍵排在它前面（週期鍵）。
  // chart()：目前的 KChart；kinds：這張圖可用的工具（大盤沒有到價提醒）；alertable(drawing)：選的這條能不能開提醒。
  // escape：Esc 由這裡放下工具（個股頁自己綁了就不用）。
  function mount({ bar, keys, before = null, chart, kinds = TOOLS.map(tool => tool.kind), alertable = () => false, escape = false }) {
    const tools = TOOLS.filter(tool => kinds.includes(tool.kind));
    const byKind = Object.fromEntries(tools.map(tool => [tool.kind, tool]));
    const state = { allowed: false, tool: null, selected: null };

    const ui = document.createElement('span');
    ui.className = 'dt-ui';
    const acts = document.createElement('span');
    acts.className = 'dt-acts';
    const bell = button('chart-chip is-plain dt-icon', svg('bell'), { id: 'drawAlert', 'aria-label': '到價提醒', 'aria-pressed': 'false' });
    const trash = button('chart-chip is-plain dt-icon', svg('trash'), { id: 'drawDelete', 'aria-label': '刪除選取的線' });
    acts.append(bell, trash);
    const key = button('chart-chip ts-tools-key dt-key', '', { id: 'toolsKey', 'aria-expanded': 'false', 'aria-controls': 'drawTools', 'aria-haspopup': 'true' });
    const exit = button('chart-chip is-plain dt-icon dt-exit', svg('close'), { id: 'toolsExit', 'aria-label': '離開畫線' });
    ui.append(acts, key, exit);
    keys.insertBefore(ui, before && before.parentElement === keys ? before : null);

    // 選單：一組一個小標題，一項一個圖示＋名稱（指標選單同一種 .kc-study-choice）；項目多，在選單裡上下捲（data-menu-scroll）
    const menu = document.createElement('div');
    menu.className = 'chart-menu dt-menu';
    menu.id = 'drawTools';
    menu.hidden = true;
    menu.setAttribute('role', 'group');
    menu.setAttribute('aria-label', '分析工具');
    menu.dataset.menuAutoclose = '';
    menu.dataset.menuScroll = '';
    let group = '';
    for (const tool of tools) {
      if (tool.group !== group) {
        group = tool.group;
        const head = document.createElement('div');
        head.className = 'dt-group';
        head.textContent = group;
        menu.append(head);
      }
      menu.append(button('kc-study-choice dt-item', `${svg(tool.kind)}<span>${tool.name}</span>`,
        { 'data-tool': tool.kind, 'aria-label': tool.name, 'aria-pressed': 'false' }));
    }
    bar.append(menu);

    // 每次打開時量一次（和主力分點的選單同一個做法，branch-pane.js placeMenu）：照鍵下方看得到的空間決定高度上限，
    // 下方放不下而上方比較寬時往上開；選單一律不超出看得到的畫面（手機鍵盤、網址列收放都照 visualViewport 算）。
    // 開合由 chart-menus.js 改 hidden，這裡跟著看。
    const MENU_GAP_PX = 6, EDGE_PX = 8, MIN_PX = 132;
    function placeMenu() {
      if (menu.hidden) return;
      menu.style.top = ''; menu.style.bottom = ''; menu.style.maxHeight = 'none';
      const keyBox = key.getBoundingClientRect(), barBox = bar.getBoundingClientRect();
      const view = window.visualViewport, viewTop = view ? view.offsetTop : 0, viewBottom = viewTop + (view ? view.height : window.innerHeight);
      const below = viewBottom - keyBox.bottom - MENU_GAP_PX - EDGE_PX, above = keyBox.top - viewTop - MENU_GAP_PX - EDGE_PX;
      const natural = menu.scrollHeight;
      const up = below < natural && above > below;
      if (up) { menu.style.top = 'auto'; menu.style.bottom = `${Math.round(barBox.bottom - keyBox.top + MENU_GAP_PX)}px`; }
      else menu.style.top = `${Math.round(keyBox.bottom - barBox.top + MENU_GAP_PX)}px`;
      menu.style.maxHeight = `${Math.max(MIN_PX, Math.floor(up ? above : below))}px`;
      menu.dataset.side = up ? 'up' : 'down';
    }
    new MutationObserver(placeMenu).observe(menu, { attributes: true, attributeFilter: ['hidden'] });

    // 選一項：再選一次正在用的那一個＝放下；選別的＝換過去
    menu.addEventListener('click', event => {
      const item = event.target.closest('button[data-tool]');
      if (!item || !chart()) return;
      chart().setTool(item.getAttribute('aria-pressed') === 'true' ? null : item.dataset.tool);
    });
    exit.addEventListener('click', () => { if (chart()) chart().setTool(null); });
    trash.addEventListener('click', () => {
      // 刪除鈕跟著收起：焦點交還圖表，鍵盤操作不中斷
      if (chart() && chart().deleteSelectedDrawing()) chart().canvas.focus({ preventScroll: true });
    });
    bell.addEventListener('click', () => {
      const on = bell.getAttribute('aria-pressed') !== 'true';
      if (chart() && chart().setSelectedAlert(on)) bell.setAttribute('aria-pressed', String(on));
    });
    if (escape) document.addEventListener('keydown', event => { if (event.key === 'Escape' && chart()) chart().cancelDrawing(); });

    function render() {
      const { allowed, tool, selected } = state;
      ui.hidden = !allowed;
      if (!allowed && !menu.hidden) { menu.hidden = true; key.setAttribute('aria-expanded', 'false'); }
      menu.querySelectorAll('button[data-tool]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.tool === tool)));
      const using = tool && byKind[tool];
      key.classList.toggle('is-on', Boolean(using));
      key.innerHTML = using ? `${svg(tool)}<span>${using.name}</span>` : `${svg('pen')}<span>分析工具</span>`;
      key.setAttribute('aria-label', using ? `分析工具：正在畫${using.name}` : '分析工具');
      exit.hidden = !using;
      acts.hidden = !selected;
      const canAlert = Boolean(selected) && alertable(selected);
      bell.hidden = !canAlert;
      bell.setAttribute('aria-pressed', String(canAlert && Boolean(selected.alert)));
    }
    render();

    return {
      // 正在用的工具（null＝一般模式）：KChart 的 drawings.onTool
      tool(kind) { state.tool = kind || null; render(); },
      // 選了哪一條（null＝沒選）：KChart 的 drawings.onSelect
      select(drawing) { state.selected = drawing || null; render(); },
      // 線的提醒開關存檔時跟著更新鈴鐺
      alert(drawing) {
        if (!state.selected || !drawing || drawing.id !== state.selected.id) return;
        state.selected = { ...state.selected, alert: Boolean(drawing.alert) };
        render();
      },
      // 這個週期能不能畫（分時不能）：不能時整組不出現
      allow(on) { state.allowed = Boolean(on); render(); },
    };
  }

  window.DrawTools = { mount, TOOLS };
})();
