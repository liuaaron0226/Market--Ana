/* 設定（本人 2026-11-17：「在設定加入一個 gemini 剩餘用量…手機推播通知…桌面的背景」；選樣品 A：齒輪浮板＋三顆籤）。
   首頁、個股頁、自選股頁的頁首都有同一顆齒輪（[data-settings-open]），點了浮出同一塊板：
   - Gemini 用量（只有站主）：/api/settings/gemini-usage，每次打開重讀
   - 通知（只有站主）：到價推播，畫面由 watch-push.js 的 WatchPush.mount 畫（第一次切到這一籤才畫，免得每頁都去註冊推播）
   - 外觀（所有人）：淺色／深色、桌面背景（只換首頁與個股頁的桌面底色）
   板子最下面一列：站主是「帳號管理」（有人等待核准時齒輪上也標人數），朋友是「Gemini 設定」（朋友 AI 開著時）。
   只有一顆籤時（一般使用者）不顯示籤列。浮層開著時底下的頁面鎖住（scroll-lock.js）。

   主題與背景存在這台瀏覽器（market-theme、market-desk），換主題時發出 market:theme 事件，各頁自己重畫圖。 */
(function (global) {
  'use strict';

  const THEME_KEY = 'market-theme';
  const DESK_KEY = 'market-desk';
  // 第一個是現在的米白（bold.css 的 --desk）。色值只在 settings.css（--desk-<key>），色票也照那裡畫
  const DESKS = [
    { key: '', name: '米白' },
    { key: 'sand', name: '暖沙' },
    { key: 'paper', name: '紙白' },
    { key: 'mist', name: '霧灰' },
    { key: 'sage', name: '淡青' },
  ];
  // 任務的 key 是排程檔名（.github/workflows/<key>.yml，gemini_usage.py --task）。
  // 自選股那支排程同時跑每日判斷與提名同類股；籌碼與資金白話是在每日更新那支排程裡寫的。
  const TASKS = {
    'watch-ledger': '自選股判斷與提名',
    'daily-update': '籌碼與資金白話',
    'ai-digest': '首頁每日解讀',
    'forecast-ledger': '晚間事前預測',
    'forecast-morning': '晨間事前預測',
    'forecast-replay': '歷史重播',
  };
  const TAIPEI = 8 * 3600_000;
  const WEEKDAYS = '日一二三四五六';
  const LOW = 20;   // 剩不到兩成標成偏低（金色）

  function readPrefs(storage) {
    let theme = null, desk = '';
    try {
      const t = storage.getItem(THEME_KEY);
      if (t === 'light' || t === 'dark') theme = t;
      const d = storage.getItem(DESK_KEY);
      if (DESKS.some(item => item.key === d)) desk = d;
    } catch (_) { /* 私密視窗可能讀不到 */ }
    return { theme, desk };
  }

  const pad = (n) => String(n).padStart(2, '0');
  const taipei = (iso) => {
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return null;
    const d = new Date(ms + TAIPEI);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, day: d.getUTCDate(), w: d.getUTCDay(), hm: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
  };
  const sameDay = (a, b) => a.y === b.y && a.m === b.m && a.day === b.day;

  function resetText(iso, now) {
    const at = iso ? taipei(iso) : null;
    if (!at) return '';
    const today = taipei(new Date(now).toISOString());
    return sameDay(at, today) ? `今天 ${at.hm} 補滿` : `${pad(at.m)}-${pad(at.day)}（${WEEKDAYS[at.w]}）${at.hm} 補滿`;
  }

  function modelLabel(name) {
    const m = /gemini-[\d.]+-([a-z]+)/i.exec(String(name || ''));
    return m ? m[1][0].toUpperCase() + m[1].slice(1).toLowerCase() : '';
  }

  // 本人 11-19：白話分析多記思考程度、失敗、乾跑（遷移 0029）；其他任務沒記就不顯示。乾跑不算進次數，另外寫。
  const EFFORTS = { low: '思考低', medium: '思考中', high: '思考高' };
  const taskModel = (item) => [modelLabel(item.model), EFFORTS[item.effort]].filter(Boolean).join('・');
  const taskNote = (item) => [
    Number(item.failed) > 0 ? `失敗 ${Number(item.failed)}` : '',
    Number(item.dry_runs) > 0 ? `乾跑 ${Number(item.dry_runs)}（不計）` : '',
  ].filter(Boolean).join('・');

  const meter = (label, cell, now) => {
    const percent = Math.round(Number(cell.remaining) * 100);
    return { label, percent, low: percent < LOW, reset: resetText(cell.reset_time, now) };
  };

  function usageView(data, now) {
    const groups = [];
    const flash = [];
    if (data?.flash?.five_hour?.remaining != null) flash.push(meter('5 小時', data.flash.five_hour, now));
    if (data?.flash?.weekly?.remaining != null) flash.push(meter('本週', data.flash.weekly, now));
    if (flash.length) groups.push({ title: 'Flash', meters: flash });
    if (data?.argon?.weekly?.remaining != null) groups.push({ title: 'Argon', meters: [meter('本週', data.argon.weekly, now)] });
    const today = (data?.today || [])
      .map(item => ({ label: TASKS[item.task] || item.task, model: taskModel(item), runs: Number(item.runs) || 0, note: taskNote(item) }))
      .sort((a, b) => b.runs - a.runs);
    let updated = '';
    const read = data?.read_at ? taipei(data.read_at) : null;
    if (read) {
      updated = `${pad(read.m)}-${pad(read.day)} ${read.hm} 讀取`;
      const last = data.last_run_at ? taipei(data.last_run_at) : null;
      if (last && data.last_run_at > data.read_at) updated += `（${sameDay(read, last) ? last.hm : `${pad(last.m)}-${pad(last.day)} ${last.hm}`} 那次沒讀到）`;
    }
    return { groups, today, updated, empty: groups.length ? '' : '下一次 Gemini 排程跑完後就會有數字。' };
  }

  const tabsFor = ({ usage, notify }) => ['usage', 'notify', 'look'].filter(key => key === 'look' || (key === 'usage' ? usage : notify));

  global.MarketSettings = { DESKS, readPrefs, resetText, modelLabel, usageView, tabsFor };

  // ---------- 畫面 ----------
  const doc = global.document;
  if (!doc) return;
  const root = doc.documentElement;

  function applyPrefs() {
    let storage = null;
    try { storage = global.localStorage; } catch (_) { /* 私密視窗 */ }
    const prefs = storage ? readPrefs(storage) : { theme: null, desk: '' };
    if (prefs.theme) root.dataset.theme = prefs.theme;
    if (prefs.desk) root.dataset.desk = prefs.desk; else delete root.dataset.desk;
  }
  applyPrefs();
  // 按「上一頁」整頁還原時，可能在別頁改過背景（主題由各頁自己的 pageshow 處理）
  global.addEventListener('pageshow', event => { if (event.persisted) applyPrefs(); });

  const opener = doc.querySelector('[data-settings-open]');
  if (!opener || typeof global.HTMLDialogElement !== 'function') return;

  const save = (key, value) => { try { global.localStorage.setItem(key, value); } catch (_) { /* 私密視窗可能存不了 */ } };
  const el = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const getJson = async (path) => {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  };
  const ICON_CLOSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
  const TAB_NAMES = { usage: 'Gemini 用量', notify: '通知', look: '外觀' };

  const dialog = el('dialog', 'st-sheet');
  dialog.id = 'settingsDialog';
  dialog.tabIndex = -1;
  dialog.setAttribute('aria-labelledby', 'settingsTitle');
  const head = el('div', 'st-head');
  const title = el('h2', 'st-title', '設定');
  title.id = 'settingsTitle';
  const close = el('button', 'st-close');
  close.type = 'button';
  close.setAttribute('aria-label', '關閉設定');
  close.innerHTML = ICON_CLOSE;
  head.append(title, close);
  const tabBar = el('div', 'st-tabs');
  const track = el('div', 'st-track');
  track.setAttribute('role', 'tablist');
  track.setAttribute('aria-label', '設定分類');
  tabBar.append(track);
  const panes = {};
  for (const key of ['usage', 'notify', 'look']) {
    const pane = el('section', `st-pane st-pane-${key}`);
    pane.id = `settings-${key}`;
    pane.setAttribute('role', 'tabpanel');
    pane.dataset.pane = key;
    pane.hidden = true;
    panes[key] = pane;
  }
  const foot = el('div', 'st-foot');
  dialog.append(head, tabBar, panes.usage, panes.notify, panes.look, foot);
  doc.body.append(dialog);

  const access = { usage: false, notify: false };
  let current = 'look';
  let notifyMounted = false;

  function renderTabs() {
    const keys = tabsFor(access);
    if (!keys.includes(current)) current = keys[0];
    tabBar.hidden = keys.length < 2;
    // 籤的組合沒變時只換選取狀態：不把按鈕整排換掉，按著的那顆不會因此失去焦點
    const shown = [...track.children].map(tab => tab.dataset.tab);
    if (shown.join() === keys.join()) {
      for (const tab of track.children) {
        tab.setAttribute('aria-selected', String(tab.dataset.tab === current));
        tab.tabIndex = tab.dataset.tab === current ? 0 : -1;
      }
    } else track.replaceChildren(...keys.map(key => {
      const tab = el('button', 'st-tab', TAB_NAMES[key]);
      tab.type = 'button';
      tab.id = `settings-tab-${key}`;
      tab.dataset.tab = key;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', `settings-${key}`);
      tab.setAttribute('aria-selected', String(key === current));
      tab.tabIndex = key === current ? 0 : -1;
      return tab;
    }));
    for (const [key, pane] of Object.entries(panes)) {
      pane.hidden = key !== current;
      if (keys.length > 1) pane.setAttribute('aria-labelledby', `settings-tab-${key}`); else pane.setAttribute('aria-labelledby', 'settingsTitle');
    }
  }

  function show(key) {
    current = key;
    renderTabs();
    if (key === 'usage') loadUsage();
    if (key === 'notify') mountNotify();
  }
  track.addEventListener('click', event => {
    const tab = event.target.closest?.('[data-tab]');
    if (tab) show(tab.dataset.tab);
  });
  // 籤列的左右鍵（tablist 的慣例）
  track.addEventListener('keydown', event => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const keys = tabsFor(access);
    const at = keys.indexOf(current) + (event.key === 'ArrowRight' ? 1 : -1);
    show(keys[(at + keys.length) % keys.length]);
    track.querySelector(`[data-tab="${current}"]`)?.focus();
    event.preventDefault();
  });

  // ---- 外觀 ----
  function renderLook() {
    const pane = panes.look;
    pane.replaceChildren();
    const dark = getComputedStyle(root).colorScheme.includes('dark');
    const themeRow = el('div', 'st-row');
    const group = el('div', 'st-seg');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', '主題');
    for (const [value, name] of [['light', '淺色'], ['dark', '深色']]) {
      const button = el('button', '', name);
      button.type = 'button';
      button.dataset.themeSet = value;
      button.setAttribute('aria-pressed', String((value === 'dark') === dark));
      group.append(button);
    }
    themeRow.append(el('span', 'st-label', '主題'), group);
    pane.append(themeRow);
    if (dark) return;   // 桌面背景只有淺色主題才有
    const deskRow = el('div', 'st-row st-row-stack');
    const label = el('span', 'st-label', '桌面背景');
    label.append(el('small', '', '只換首頁與個股頁的桌面底色'));
    const swatches = el('div', 'st-swatches');
    swatches.setAttribute('role', 'group');
    swatches.setAttribute('aria-label', '桌面背景');
    const chosen = root.dataset.desk || '';
    for (const desk of DESKS) {
      const button = el('button', 'st-swatch');
      button.type = 'button';
      button.dataset.deskSet = desk.key;
      button.setAttribute('aria-pressed', String(desk.key === chosen));
      button.append(el('i'), el('span', '', desk.name));
      swatches.append(button);
    }
    deskRow.append(label, swatches);
    pane.append(deskRow);
  }
  panes.look.addEventListener('click', event => {
    const theme = event.target.closest?.('[data-theme-set]');
    if (theme) {
      root.dataset.theme = theme.dataset.themeSet;
      save(THEME_KEY, root.dataset.theme);
      doc.dispatchEvent(new CustomEvent('market:theme', { detail: { theme: root.dataset.theme } }));
      renderLook();
      panes.look.querySelector(`[data-theme-set="${root.dataset.theme}"]`)?.focus();
      return;
    }
    const desk = event.target.closest?.('[data-desk-set]');
    if (desk) {
      if (desk.dataset.deskSet) root.dataset.desk = desk.dataset.deskSet; else delete root.dataset.desk;
      save(DESK_KEY, desk.dataset.deskSet);
      for (const b of panes.look.querySelectorAll('[data-desk-set]')) b.setAttribute('aria-pressed', String(b === desk));
    }
  });

  // ---- Gemini 用量 ----
  async function loadUsage() {
    const pane = panes.usage;
    if (!pane.childElementCount) pane.append(el('p', 'st-note', '讀取中…'));
    let view;
    try { view = usageView(await getJson('/api/settings/gemini-usage'), Date.now()); }
    catch (_) { pane.replaceChildren(el('p', 'st-note', '用量暫時讀不到，稍後再打開看看。')); return; }
    pane.replaceChildren();
    if (view.empty) pane.append(el('p', 'st-note', view.empty));
    for (const group of view.groups) {
      pane.append(el('p', 'st-group', group.title));
      for (const m of group.meters) {
        const row = el('div', 'st-meter');
        if (m.low) row.dataset.low = '';
        const name = el('span', 'st-meter-name', m.label);
        const value = el('span', 'st-meter-val num', String(m.percent));
        value.append(el('small', '', '%'));
        const bar = el('span', 'st-meter-bar');
        bar.setAttribute('role', 'meter');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', String(m.percent));
        bar.setAttribute('aria-label', `${group.title} ${m.label}剩 ${m.percent}%`);
        const fill = el('i');
        fill.style.setProperty('--v', `${Math.max(0, Math.min(100, m.percent))}%`);
        bar.append(fill);
        row.append(name, value, bar, el('span', 'st-meter-reset num', m.reset));
        pane.append(row);
      }
    }
    if (view.today.length) {
      pane.append(el('p', 'st-group', '今天跑了幾次'));
      const list = el('ul', 'st-today');
      for (const item of view.today) {
        const li = el('li');
        const name = el('span', '', item.label);
        if (item.note) name.append(' ', el('small', 'st-model', item.note));
        li.append(name, el('span', 'st-model', item.model), el('span', 'num', String(item.runs)));
        list.append(li);
      }
      pane.append(list);
    }
    if (view.updated) pane.append(el('p', 'st-updated num', view.updated));
  }

  // ---- 通知（到價推播）----
  async function mountNotify() {
    if (notifyMounted) return;
    notifyMounted = true;
    const pane = panes.notify;
    pane.classList.add('wp');
    pane.append(el('p', 'st-note', '讀取中…'));
    let ok = false;
    try { ok = await global.WatchPush.mount(pane); }
    catch (_) { notifyMounted = false; }   // 讀不到：下次切回這一籤再試
    pane.hidden = current !== 'notify';   // mount 會把容器打開，照目前選的籤
    if (!ok) pane.replaceChildren(el('p', 'st-note', notifyMounted ? '到價推播目前沒有開放。' : '推播設定暫時讀不到，稍後再切回這一籤看看。'));
  }

  // ---- 最下面一列：帳號管理（站主）或 Gemini 設定（朋友）----
  function footLink(href, text, count = 0) {
    const a = el('a', 'st-link', text);
    a.href = href;
    const end = el('span', 'st-link-end');
    if (count) end.append(el('b', 'num', String(count)), ` 人等待核准`);
    end.append(el('span', 'st-chev', '›'));
    a.append(end);
    foot.prepend(a);
  }

  // 登出（換帳號）：每個登入的人都有，寫出目前登入的信箱（本人 11-18「沒有登出這個選項」）。
  // 登出的是 Access 登入網域，回到網站要重新收驗證碼，才能換成別的信箱。
  function logoutLink(me) {
    if (!me?.logout_url) return;
    const a = el('a', 'st-link st-logout', '登出（換帳號）');
    a.href = me.logout_url;
    const end = el('span', 'st-link-end');
    end.append(el('span', 'st-who', me.email || ''), el('span', 'st-chev', '›'));
    a.append(end);
    foot.append(a);
  }

  async function discover() {
    let me = null;
    try { me = await getJson('/api/me'); } catch (_) { /* 本機版沒有帳號 */ }
    foot.replaceChildren();
    logoutLink(me);
    if (me?.owner) {
      access.usage = true;
      // 到價推播跟著自選股關注的開關（和原本自選股頁的鈴鐺同一個判斷）；/api/features 對站主不扣額度
      try { access.notify = Boolean(global.WatchPush) && Boolean((await getJson('/api/features')).features?.watchlist_watch_v1); }
      catch (_) { access.notify = false; }
      // 浮板還沒打開才預設先看用量；使用者已經打開、選好的籤不去動它
      if (!dialog.open) current = 'usage';
      let pending = 0;
      try { pending = (await getJson('/api/admin/users')).users.filter(u => u.status === 'pending').length; } catch (_) { /* 讀不到就不標 */ }
      footLink('/admin/', '帳號管理', pending);
      if (pending) {
        const badge = el('b', 'st-badge num', String(pending));
        badge.setAttribute('aria-hidden', 'true');
        opener.append(badge);
        opener.setAttribute('aria-label', `設定，${pending} 人等待核准`);
      }
    } else if (me) {
      try { if ((await getJson('/api/friend-ai')).available) footLink('/gemini/', 'Gemini 設定'); } catch (_) { /* 沒有就不放 */ }
    }
    renderTabs();
  }

  // ---- 打開與收回（和到價推播原本的浮層同一套：Esc、關閉鈕、按下與放開都在板外才收）----
  function open() {
    if (dialog.open) return;
    renderLook();
    renderTabs();
    global.ScrollLock?.lock(dialog);
    dialog.showModal();
    dialog.focus();
    opener.setAttribute('aria-expanded', 'true');
    show(current);
  }
  opener.setAttribute('aria-haspopup', 'dialog');
  opener.setAttribute('aria-controls', dialog.id);
  opener.setAttribute('aria-expanded', 'false');
  opener.addEventListener('click', open);
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    global.ScrollLock?.unlock(dialog);
    opener.setAttribute('aria-expanded', 'false');
  });
  const outside = event => {
    if (event.target !== dialog) return false;
    const box = dialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  };
  let pressedOutside = false;
  dialog.addEventListener('pointerdown', event => { pressedOutside = outside(event); });
  dialog.addEventListener('click', event => {
    if (pressedOutside && outside(event)) dialog.close();
    pressedOutside = false;
  });

  renderTabs();
  discover();
})(window);
