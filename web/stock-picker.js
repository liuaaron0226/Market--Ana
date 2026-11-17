/* 全站股票輸入框共用的選股下拉（本人 11-17「搜尋股票只能透過代號很不方便」）。
   代號、名稱（全名或部分字，例如「台積電」「積電」）都能打；名稱走既有的 /api/stocks/search，不另寫一套。
   打的是代號就照舊直接用（名錄外的代號也一樣交給原本的流程說明）；選了某一檔，框裡寫「代號 名稱」。
   基金（基金當成一般個股 03 票，本人 11-18）：搜尋回應 items 裡的一般一筆（類別「基金」），和股票一樣每個框都收。
   用法：StockPicker.attach(input, { accept(item), onPick(option), host, inline, strict }) → { choose(text), close() }。
   choose：按「查詢／加入」時用，回 { option } 或 { message }（要顯示給本人的一句話）。
   inline：放在會收合（overflow: hidden）的區塊裡時，下拉不浮起、直接排在輸入框下面。
   strict：會存資料的框（加自選、新增庫存）——打的名稱符合好幾檔又沒有完全相同的，不默默取第一個，攤開下拉要本人挑。 */
(function (global) {
  'use strict';

  const CODE = /^\s*([0-9A-Za-z]{4,6})(?=\s|$)/;
  // 全站代號規則（基金當成一般個股 01 票，本人 11-18）：4～12 碼大寫英數；和雲端 cloud/lib/codes.js 的 CODE_PATTERN 同一個。
  const VALID_CODE = /^[0-9A-Z]{4,12}$/;
  const isCode = code => VALID_CODE.test(String(code ?? ''));
  const MIN_LENGTH = 2;
  const LIMIT = 6;
  const QUERY_BYTES = 48;   // /api/stocks/search 的上限（gate.js handleStockSearch）
  let serial = 0;

  // 打的字開頭是 4～6 碼代號（後面可以接空白和名稱）就拿那個代號；其他（名稱、基金代碼）回 null。
  function codeOf(text) {
    const match = String(text ?? '').match(CODE);
    return match && /\d/.test(match[1]) ? match[1].toUpperCase() : null;
  }

  // /api/stocks/search 的回應 → 下拉選項，照回應的順序。基金標「基金」（市場「境外基金」不必再寫一次）。
  function options(payload, { accept = () => true } = {}) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    return items.filter(item => item && item.code && accept(item)).map(item => ({
      kind: 'stock', code: item.code, name: item.name || item.code, title: `${item.code} ${item.name || ''}`.trim(),
      meta: item.security_type === '基金' ? '基金' : `${item.market || ''}${item.security_type || ''}`,
    }));
  }

  // 搜尋字超過 API 上限（48 個 UTF-8 位元組，約 16 個中文字，例如貼上基金全名）就從尾巴截短，不讓整個搜尋失敗。
  const utf8Length = text => encodeURIComponent(text).replace(/%[0-9A-F]{2}/g, '.').length;
  function fitQuery(term) {
    let fitted = String(term);
    while (utf8Length(fitted) > QUERY_BYTES) fitted = Array.from(fitted).slice(0, -1).join('');
    return fitted.trim();
  }

  // 按「查詢／加入」沒選下拉時，從搜尋結果決定用哪一檔：代碼或名稱完全相同的優先（例如打基金代碼 BLKTENAU），否則第一個；
  // strict 的框遇到好幾檔又沒有完全相同的，回 choose 要本人挑。
  function decide(term, choices, { strict = false } = {}) {
    if (!choices.length) return { message: `查無「${term}」，請改打代號或從清單選擇。` };
    const upper = term.toUpperCase();
    const exact = choices.find(option => option.code === upper) || choices.find(option => option.name === term);
    if (exact) return { option: exact };
    if (strict && choices.length > 1) return { choose: true, message: `有好幾檔符合「${term}」，請從下面選一檔。` };
    return { option: choices[0] };
  }

  async function fetchOptions(term, settings, signal) {
    const response = await fetch(`/api/stocks/search?${new URLSearchParams({ q: fitQuery(term), limit: String(LIMIT) })}`, { cache: 'no-store', signal });
    if (!response.ok) throw new Error('search-failed');
    return options(await response.json(), settings).slice(0, LIMIT);
  }

  function attach(input, settings = {}) {
    const id = `stockPicker${++serial}`;
    const host = settings.host || input.parentElement;
    // 浮起的下拉要宿主當定位基準；排在框下面的（inline）不動宿主，宿主可能本身就是浮出選單（position: absolute）
    if (!settings.inline) host.classList.add('sp-host');
    const list = document.createElement('div');
    list.className = settings.inline ? 'sp-list sp-inline' : 'sp-list';
    list.id = id;
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    host.append(list);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', id);
    input.setAttribute('aria-expanded', 'false');

    const state = { timer: null, abort: null, term: '', choices: [], buttons: [], active: -1 };

    function close() {
      clearTimeout(state.timer);
      if (state.abort) state.abort.abort();
      state.abort = null;
      state.term = '';
      state.choices = [];
      state.buttons = [];
      state.active = -1;
      list.hidden = true;
      list.replaceChildren();
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }

    function pick(option) {
      input.value = `${option.code} ${option.name}`;
      close();
      settings.onPick?.(option);
    }

    function row(option, index) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'sp-option';
      button.id = `${id}-${index}`;
      button.setAttribute('role', 'option');
      button.tabIndex = -1;   // 選項用上下鍵在輸入框裡選（aria-activedescendant），不進 Tab 順序
      button.setAttribute('aria-selected', 'false');
      const title = document.createElement('strong');
      title.textContent = option.title;
      const meta = document.createElement('small');
      meta.textContent = option.meta;
      button.append(title, meta);
      button.addEventListener('click', () => pick(option));
      return button;
    }

    function show(term, choices) {
      state.term = term;
      state.choices = choices;
      state.buttons = choices.map(row);
      state.active = -1;
      if (choices.length) {
        list.replaceChildren(...state.buttons);
      } else {
        const empty = document.createElement('p');
        empty.className = 'sp-empty';
        empty.textContent = `查無「${term}」，可以改打代號或名稱的其他字。`;
        list.replaceChildren(empty);
      }
      list.hidden = false;
      keepInside();
      input.setAttribute('aria-expanded', String(choices.length > 0));
      input.removeAttribute('aria-activedescendant');
    }

    // 浮起的下拉超出畫面右邊（例如手機上個股頁頂列的框靠右）就往左挪，左右各留 16px
    function keepInside() {
      if (settings.inline) return;
      list.style.left = '';
      const box = list.getBoundingClientRect();
      const over = box.right - (document.documentElement.clientWidth - 16);
      if (over > 0) list.style.left = `${Math.max(16 - host.getBoundingClientRect().left, -over)}px`;
    }

    async function search(term) {
      if (state.abort) state.abort.abort();
      const controller = new AbortController();
      state.abort = controller;
      try {
        const choices = await fetchOptions(term, settings, controller.signal);
        if (controller.signal.aborted || input.value.trim() !== term) return;
        state.abort = null;
        show(term, choices);
      } catch (error) {
        if (error.name !== 'AbortError') close();
      }
    }

    function setActive(index) {
      if (!state.buttons.length) return;
      state.active = (index + state.buttons.length) % state.buttons.length;
      state.buttons.forEach((button, i) => button.setAttribute('aria-selected', String(i === state.active)));
      const active = state.buttons[state.active];
      input.setAttribute('aria-activedescendant', active.id);
      active.scrollIntoView({ block: 'nearest' });
    }

    input.addEventListener('input', () => {
      const term = input.value.trim();
      close();
      if (term.length < MIN_LENGTH || codeOf(term)) return;
      state.timer = setTimeout(() => search(term), 180);
    });
    input.addEventListener('keydown', event => {
      if (list.hidden) return;
      if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); close(); return; }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        if (!state.buttons.length) return;
        event.preventDefault();
        setActive(state.active + (event.key === 'ArrowDown' ? 1 : -1));
        return;
      }
      if (event.key === 'Enter' && state.active >= 0) {
        event.preventDefault();
        event.stopPropagation();
        pick(state.choices[state.active]);
      }
    });
    // 點選項時不讓輸入框失焦（手機鍵盤不會跳一下）；點下拉以外的地方收起來
    list.addEventListener('pointerdown', event => event.preventDefault());
    document.addEventListener('click', event => { if (!host.contains(event.target)) close(); });

    // 按「查詢／加入」時：打的是代號就直接用；有反白的選項用那個；下拉已經列出同一串字的結果就直接用，否則搜一次；
    // 再由 decide 決定用哪一檔。回 { option } 或 { message }。
    async function choose(text = input.value) {
      const term = String(text).trim();
      if (!term) return { message: '請先輸入股票代號或名稱。' };
      const code = codeOf(term);
      if (code) { close(); return { option: { kind: 'stock', code, name: code } }; }
      if (state.active >= 0) { const option = state.choices[state.active]; close(); return { option }; }
      let choices = !list.hidden && state.term === term ? state.choices : null;
      if (!choices) {
        close();
        try { choices = await fetchOptions(term, settings); }
        catch { return { message: '搜尋暫時無法使用，請稍後再試。' }; }
      }
      const result = decide(term, choices, settings);
      if (result.choose) show(term, choices);
      else close();
      return result;
    }

    return { choose, close };
  }

  // 還不收基金的框用的 accept（基金當成一般個股 03 票）：自選股頁加自選與庫存要等 07 票，
  // 在那之前選到基金只會被說查無此股，所以先不列；07 做完就拿掉（首頁核心持有已由 05 票改成收基金）。
  const notFund = item => item.security_type !== '基金';

  global.StockPicker = { codeOf, isCode, options, decide, fitQuery, attach, notFund };
})(window);
