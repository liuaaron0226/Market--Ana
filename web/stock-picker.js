/* 全站股票輸入框共用的選股下拉（本人 11-17「搜尋股票只能透過代號很不方便」）。
   代號、名稱（全名或部分字，例如「台積電」「積電」）都能打；名稱走既有的 /api/stocks/search，不另寫一套。
   打的是代號就照舊直接用（名錄外的代號也一樣交給原本的流程說明）；選了某一檔，框裡寫「代號 名稱」。
   基金：搜尋回應的 funds（目前只有基金對照頁那檔），只有個股頁頂列這種「看東西」的框收（funds: true），
   加自選、加入比較這類只能放股票的框不收。
   用法：StockPicker.attach(input, { funds, accept(item), onPick(option), host, inline }) → { resolve(text), close() }。
   inline：放在會收合（overflow: hidden）的區塊裡時，下拉不浮起、直接排在輸入框下面。 */
(function (global) {
  'use strict';

  const CODE = /^\s*([0-9A-Za-z]{4,6})(?=\s|$)/;
  const MIN_LENGTH = 2;
  const LIMIT = 6;
  let serial = 0;

  // 打的字開頭是 4～6 碼代號（後面可以接空白和名稱）就拿那個代號；其他（名稱、基金代碼）回 null。
  function codeOf(text) {
    const match = String(text ?? '').match(CODE);
    return match && /\d/.test(match[1]) ? match[1].toUpperCase() : null;
  }

  // /api/stocks/search 的回應 → 下拉選項。股票在前、基金在後。
  function options(payload, { funds = false, accept = () => true } = {}) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const list = items.filter(item => item && item.code && accept(item)).map(item => ({
      kind: 'stock', code: item.code, name: item.name || item.code, title: `${item.code} ${item.name || ''}`.trim(),
      meta: `${item.market || ''}${item.security_type || ''}`,
    }));
    if (funds && Array.isArray(payload?.funds)) {
      payload.funds.filter(fund => fund && fund.code).forEach(fund => list.push({
        kind: 'fund', code: fund.code, name: fund.name || fund.code, title: fund.name || fund.code,
        meta: '基金 · 和 0050 比', href: `fund.html?code=${encodeURIComponent(fund.code)}`,
      }));
    }
    return list;
  }

  async function fetchOptions(term, settings, signal) {
    const response = await fetch(`/api/stocks/search?${new URLSearchParams({ q: term, limit: String(LIMIT) })}`, { cache: 'no-store', signal });
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

    const state = { timer: null, abort: null, choices: [], buttons: [], active: -1 };

    function close() {
      clearTimeout(state.timer);
      if (state.abort) state.abort.abort();
      state.abort = null;
      state.choices = [];
      state.buttons = [];
      state.active = -1;
      list.hidden = true;
      list.replaceChildren();
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }

    function pick(option) {
      if (option.kind === 'stock') input.value = `${option.code} ${option.name}`;
      close();
      settings.onPick?.(option);
    }

    function row(option, index) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'sp-option';
      button.id = `${id}-${index}`;
      button.setAttribute('role', 'option');
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

    // 按「查詢／加入」時：打的是代號就直接用；有反白的選項用那個；否則用名稱搜一次，取第一個。找不到回 null。
    async function resolve(text = input.value) {
      const code = codeOf(text);
      if (code) { close(); return { kind: 'stock', code, name: code }; }
      if (state.active >= 0) { const option = state.choices[state.active]; close(); return option; }
      const term = String(text).trim();
      if (term.length < 1) return null;
      close();
      try {
        const [first] = await fetchOptions(term, settings);
        return first || null;
      } catch {
        return null;
      }
    }

    return { resolve, close };
  }

  global.StockPicker = { codeOf, options, attach };
})(window);
