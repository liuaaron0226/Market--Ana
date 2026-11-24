/* 設定（本人 2026-11-17：「在設定加入一個 gemini 剩餘用量…手機推播通知…桌面的背景」；選樣品 A：齒輪浮板＋三顆籤）。
   首頁、個股頁、自選股頁的頁首都有同一顆齒輪（[data-settings-open]），點了浮出同一塊板：
   - Gemini 用量（只有站主）：/api/settings/gemini-usage，每次打開重讀
   - 通知（只有站主）：到價推播，畫面由 watch-push.js 的 WatchPush.mount 畫（第一次切到這一籤才畫，免得每頁都去註冊推播）
   - 外觀（所有人）：淺色／深色、桌面背景（淺色：只換首頁與個股頁的桌面底色；深色：板、桌面、細線一起換，本人 2026-11-20）
   板子最下面一列：站主是「帳號管理」（有人等待核准時齒輪上也標人數），朋友是「Gemini 設定」（朋友 AI 開著時）。
   只有一顆籤時（一般使用者）不顯示籤列。浮層開著時底下的頁面鎖住（scroll-lock.js）。

   主題與背景存在這台瀏覽器（market-theme、market-desk；深色的背景另外存 market-desk-dark，淺色深色各記各的），
   換主題或換深色背景時發出 market:theme 事件，各頁自己重畫圖。<html data-desk> 寫的是「現在這個主題」選的那一種。 */
(function (global) {
  'use strict';

  const THEME_KEY = 'market-theme';
  const DESK_KEY = 'market-desk';
  const DARK_DESK_KEY = 'market-desk-dark';
  // 第一個是現在的米白（bold.css 的 --desk）。色值只在 settings.css（--desk-<key>），色票也照那裡畫
  const DESKS = [
    { key: '', name: '米白' },
    { key: 'sand', name: '暖沙' },
    { key: 'paper', name: '紙白' },
    { key: 'mist', name: '霧灰' },
    { key: 'sage', name: '淡青' },
  ];
  // 深色的桌面（本人 2026-11-20：「暗色系桌面選擇只有一個請你多增加其他種暗色系配色，且不會影響我看K線圖」，從樣品選了三種）。
  // 第一個是原本的深色。換的是桌面、板、鍵的底與細線；字色、紅綠、均線的顏色都不動，K 線上的顏色和原本完全一樣。
  // 色值只在 settings.css；每一種底色上 K 線各種顏色的對比度量過（design-previews/ui-polish/dark-contrast.py）。
  const DARK_DESKS = [
    { key: '', name: '墨黑' },
    { key: 'slate', name: '深藍灰' },
    { key: 'umber', name: '炭褐' },
    { key: 'graphite', name: '石墨' },
  ];
  // 任務的 key 是排程檔名（.github/workflows/<key>.yml，gemini_usage.py --task）。
  // 自選股那支排程同時跑每日判斷與提名同類股；籌碼與資金白話是在每日更新那支排程裡寫的。
  const TASKS = {
    'watch-ledger': '自選股判斷與提名',
    'daily-update': '籌碼卡解讀',   // 大盤資料包裡那一份（籌碼卡點開的說明）
    'market-brief': '盤勢解讀',     // 分類＋圖的那一份（market_brief.py）
    'ai-digest': '首頁每日解讀',
    'forecast-ledger': '晚間事前預測',
    'forecast-morning': '晨間事前預測',
    'forecast-replay': '歷史重播',
    'market-news': '首頁消息',
    'impact-watch': '追蹤事項新聞判讀',  // 消息型盯項每日檢查（daily-update.yml 裡的一步，不是獨立的排程檔）
  };
  const TAIPEI = 8 * 3600_000;
  const WEEKDAYS = '日一二三四五六';
  const LOW = 20;   // 剩不到兩成標成偏低（金色）

  function readPrefs(storage) {
    let theme = null, desk = '', darkDesk = '';
    try {
      const t = storage.getItem(THEME_KEY);
      if (t === 'light' || t === 'dark') theme = t;
      const d = storage.getItem(DESK_KEY);
      if (DESKS.some(item => item.key === d)) desk = d;
      const dd = storage.getItem(DARK_DESK_KEY);
      if (DARK_DESKS.some(item => item.key === dd)) darkDesk = dd;
    } catch (_) { /* 私密視窗可能讀不到 */ }
    return { theme, desk, darkDesk };
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
    Number(item.dry_runs) > 0 ? `試跑 ${Number(item.dry_runs)}（不計）` : '',
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

  // ---- AI 模型（本人 11-19 晚：五項任務自己選模型與思考強度；樣品 C：每列寫現值、點開才選）----
  // key 和 /api/settings/ai-models 的 tasks[].key 一樣（cloud/lib/ai-models.js）。
  const MODEL_TASKS = {
    forecast: { name: '大盤預測', sub: '晚間・晨間・重播一起' },
    watch_judge: { name: '自選股每日判斷', sub: '' },
    watch_nominate: { name: '提名國外同類股', sub: '' },
    plain: { name: '盤勢解讀', sub: '' },
    digest: { name: '今日 AI 解讀', sub: '' },
    news: { name: '首頁消息', sub: '盤前・午盤・收盤後' },
  };
  const MODEL_CHOICES = [['gemini-4-argon', 'Argon'], ['gemini-3.8-flash', 'Flash']];
  const EFFORT_CHOICES = [['low', '低'], ['medium', '中'], ['high', '高']];
  // 改了會另開一組成績的兩項（本人第 5、6 題：存檔前先問）
  const SCORED = {
    forecast: '成績另開一組從頭累積，正式起算要重數連續 5 天。從下一個晚間版開始用。',
    watch_judge: '成績另開一組從頭累積。下一時段開始用。',
  };
  const choiceText = (c) => `${modelLabel(c.model)}・${(EFFORT_CHOICES.find(([k]) => k === c.effort) || [])[1] || ''}`;
  const shortTime = (iso) => { const t = iso ? taipei(iso) : null; return t ? `${pad(t.m)}-${pad(t.day)} ${t.hm}` : ''; };
  // 草稿裡和現值不同的才算改動
  const pending = (data, draft) => (data?.tasks || [])
    .map(task => ({ task, next: draft?.[task.key] }))
    .filter(({ task, next }) => next && (next.model !== task.model || next.effort !== task.effort));

  function modelsView(data, draft) {
    const changed = new Set(pending(data, draft).map(({ task }) => task.key));
    const rows = (data?.tasks || []).filter(task => MODEL_TASKS[task.key]).map(task => {
      const current = changed.has(task.key) ? draft[task.key] : task;
      return { key: task.key, ...MODEL_TASKS[task.key], model: current.model, effort: current.effort,
        value: choiceText(current), changed: changed.has(task.key) };
    });
    const quota = [['argon', 'Argon 本週剩'], ['flash', 'Flash 本週剩']]
      .filter(([key]) => data?.quota?.[key] != null)
      .map(([key, label]) => { const percent = Math.round(Number(data.quota[key]) * 100); return { label, percent, low: percent < LOW }; });
    const updated = shortTime(data?.updated_at);
    return {
      rows, quota, dirty: changed.size > 0,
      unread: (data?.unread || []).map(item => `${TASKS[item.task] || item.task}上一時段（${shortTime(item.read_at)}）沒讀到你的設定，用了預設。`),
      notReady: data?.ready === false ? '網站資料庫還沒更新，現在顯示的是預設，暫時不能存。' : '',
      foot: `存檔後下一時段排程開始用（大盤預測從下一個晚間版起）。${updated ? `上次修改 ${updated}。` : ''}`,
    };
  }

  // 按了一顆鍵之後的選擇。本人 11-19 晚「Flash還是改用高好了」：從別的模型切到 Flash 時思考強度先帶「高」，之後仍可自己改
  const nextChoice = (base, pick) => {
    if (pick.model && pick.model !== base.model) return { model: pick.model, effort: pick.model === 'gemini-3.8-flash' ? 'high' : base.effort };
    return { ...base, ...pick };
  };

  const confirmNotes = (data, draft) => pending(data, draft)
    .filter(({ task }) => SCORED[task.key])
    .map(({ task, next }) => `${MODEL_TASKS[task.key].name}改成 ${choiceText(next)}：${SCORED[task.key]}`);

  // ---- 資料更新（2026-11-24：每一份資料最後更新的時間與狀態；/api/settings/data-health，判斷在 cloud/lib/data-health.js）----
  const HEALTH_STATES = { ok: '正常', late: '延遲', failed: '停止更新', degraded: '部分異常', error: '無法檢查' };
  function healthTime(value, now) {
    const text = String(value || '');
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text.slice(5);   // 只有資料日（基金淨值、主力分點…）
    const at = taipei(text);
    if (!at) return '—';
    return sameDay(at, taipei(new Date(now).toISOString())) ? `今天 ${at.hm}` : `${pad(at.m)}-${pad(at.day)} ${at.hm}`;
  }
  function healthView(data, now) {
    const rows = (data?.items || []).filter(item => HEALTH_STATES[item.state]).map(item => ({
      key: item.key, label: item.label, time: healthTime(item.last_ok_at, now),
      state: item.state, stateText: HEALTH_STATES[item.state], detail: item.state === 'ok' ? '' : String(item.detail || ''),
    }));
    return { rows, empty: rows.length ? '' : '目前沒有可檢查的資料。' };
  }

  const tabsFor = ({ usage, health, models, notify }) => ['usage', 'health', 'models', 'notify', 'look']
    .filter(key => key === 'look' || ({ usage, health, models, notify })[key]);

  global.MarketSettings = { DESKS, DARK_DESKS, readPrefs, resetText, modelLabel, usageView, tabsFor, modelsView, confirmNotes, nextChoice, healthView };

  // ---------- 畫面 ----------
  const doc = global.document;
  if (!doc) return;
  const root = doc.documentElement;

  const isDark = () => getComputedStyle(root).colorScheme.includes('dark');
  function applyPrefs() {
    let storage = null;
    try { storage = global.localStorage; } catch (_) { /* 私密視窗 */ }
    const prefs = storage ? readPrefs(storage) : { theme: null, desk: '', darkDesk: '' };
    if (prefs.theme) root.dataset.theme = prefs.theme;
    // 現在是深色就套深色選的那一種，淺色套淺色選的
    const desk = isDark() ? prefs.darkDesk : prefs.desk;
    if (desk) root.dataset.desk = desk; else delete root.dataset.desk;
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
  const TAB_NAMES = { usage: 'Gemini 用量', health: '資料更新', models: 'AI 模型', notify: '通知', look: '外觀' };

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
  for (const key of ['usage', 'health', 'models', 'notify', 'look']) {
    const pane = el('section', `st-pane st-pane-${key}`);
    pane.id = `settings-${key}`;
    pane.setAttribute('role', 'tabpanel');
    pane.dataset.pane = key;
    pane.hidden = true;
    panes[key] = pane;
  }
  const foot = el('div', 'st-foot');
  dialog.append(head, tabBar, panes.usage, panes.health, panes.models, panes.notify, panes.look, foot);
  doc.body.append(dialog);

  const access = { usage: false, health: false, models: false, notify: false };
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
    if (key === 'health') loadHealth();
    if (key === 'models') loadModels();
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
    const dark = isDark();
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
    // 淺色、深色各有一組桌面背景，各記各的
    const deskRow = el('div', 'st-row st-row-stack');
    const label = el('span', 'st-label', '桌面背景');
    label.append(el('small', '', dark ? '桌面與板一起換，K 線的顏色不變' : '只換首頁與個股頁的桌面底色'));
    const swatches = el('div', `st-swatches${dark ? ' is-dark' : ''}`);
    swatches.setAttribute('role', 'group');
    swatches.setAttribute('aria-label', '桌面背景');
    const chosen = root.dataset.desk || '';
    for (const desk of dark ? DARK_DESKS : DESKS) {
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
      applyPrefs();   // 桌面背景換成這個主題選的那一種
      doc.dispatchEvent(new CustomEvent('market:theme', { detail: { theme: root.dataset.theme } }));
      renderLook();
      panes.look.querySelector(`[data-theme-set="${root.dataset.theme}"]`)?.focus();
      return;
    }
    const desk = event.target.closest?.('[data-desk-set]');
    if (desk) {
      const dark = isDark();
      if (desk.dataset.deskSet) root.dataset.desk = desk.dataset.deskSet; else delete root.dataset.desk;
      save(dark ? DARK_DESK_KEY : DESK_KEY, desk.dataset.deskSet);
      for (const b of panes.look.querySelectorAll('[data-desk-set]')) b.setAttribute('aria-pressed', String(b === desk));
      // 深色的背景連板的底色一起換：K 線上的字與底要照新的顏色重畫（和換主題同一個事件）
      if (dark) doc.dispatchEvent(new CustomEvent('market:theme', { detail: { theme: 'dark' } }));
    }
  });

  // ---- Gemini 用量 ----
  async function loadUsage() {
    const pane = panes.usage;
    if (!pane.childElementCount) pane.append(el('p', 'st-note', '讀取中…'));
    let view;
    try { view = usageView(await getJson('/api/settings/gemini-usage'), Date.now()); }
    catch (_) { pane.replaceChildren(el('p', 'st-note', '用量暫時讀不到，請稍後再試。')); return; }
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
      pane.append(el('p', 'st-group', '今日執行次數'));
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

  // ---- 資料更新 ----
  async function loadHealth() {
    const pane = panes.health;
    if (!pane.childElementCount) pane.append(el('p', 'st-note', '讀取中…'));
    let view;
    try { view = healthView(await getJson('/api/settings/data-health'), Date.now()); }
    catch (_) { pane.replaceChildren(el('p', 'st-note', '資料更新狀態暫時讀不到，請稍後再試。')); return; }
    pane.replaceChildren();
    if (view.empty) { pane.append(el('p', 'st-note', view.empty)); return; }
    const list = el('ul', 'st-health');
    list.setAttribute('aria-label', '資料更新狀態');
    for (const row of view.rows) {
      const li = el('li');
      li.dataset.health = row.key;
      li.dataset.state = row.state;
      const name = el('span', 'st-hname', row.label);
      if (row.detail) name.append(el('small', '', row.detail));
      li.append(name, el('span', 'st-htime num', row.time), el('span', 'st-hstate', row.stateText));
      list.append(li);
    }
    pane.append(list);
  }

  // ---- AI 模型 ----
  // models.data：伺服器上的現值；draft：還沒存的改動；open：展開的那一列；confirming：存檔前的確認；note：存檔結果
  const models = { data: null, draft: {}, open: '', confirming: false, note: '', saving: false };
  const MODELS_PATH = '/api/settings/ai-models';

  async function loadModels() {
    const pane = panes.models;
    // 有沒存的改動時不重讀，免得切籤回來改動不見
    if (models.data && models.draft && Object.keys(models.draft).length) { renderModels(); return; }
    if (!pane.childElementCount) pane.append(el('p', 'st-note', '讀取中…'));
    try { models.data = await getJson(MODELS_PATH); }
    catch (_) { pane.replaceChildren(el('p', 'st-note', 'AI 模型設定暫時讀不到，請稍後再試。')); return; }
    models.note = '';
    renderModels();
  }

  function segment(label, choices, value, key, field) {
    const group = el('div', 'st-seg');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', label);
    for (const [k, name] of choices) {
      const button = el('button', '', name);
      button.type = 'button';
      button.dataset.mkey = key;
      button.dataset[field] = k;
      button.setAttribute('aria-pressed', String(k === value));
      group.append(button);
    }
    return group;
  }

  function button(text, action, primary = false) {
    const b = el('button', primary ? 'st-btn st-btn-primary' : 'st-btn', text);
    b.type = 'button';
    b.dataset.maction = action;
    return b;
  }

  function renderModels() {
    const pane = panes.models;
    const view = modelsView(models.data, models.draft);
    pane.replaceChildren();
    if (view.quota.length) {
      const line = el('p', 'st-quota');
      for (const q of view.quota) {
        const item = el('span', '', `${q.label} `);
        if (q.low) item.dataset.low = '';
        item.append(el('b', 'num', `${q.percent}%`));
        line.append(item);
      }
      pane.append(line);
    }
    for (const text of view.unread) pane.append(el('p', 'st-warn', text));
    if (view.notReady) pane.append(el('p', 'st-warn', view.notReady));
    for (const row of view.rows) {
      const open = models.open === row.key;
      const head = el('button', 'st-mrow');
      head.type = 'button';
      head.dataset.mrow = row.key;
      head.id = `settings-model-${row.key}`;
      head.setAttribute('aria-expanded', String(open));
      head.setAttribute('aria-controls', `settings-model-${row.key}-choices`);
      const name = el('span', 'st-mname', row.name);
      if (row.sub) name.append(el('small', '', row.sub));
      const value = el('span', 'st-mval', row.value);
      if (row.changed) value.dataset.changed = '';
      value.append(el('span', 'st-chev', '›'));
      head.append(name, value);
      const choices = el('div', 'st-mopen');
      choices.id = `settings-model-${row.key}-choices`;
      choices.hidden = !open;
      choices.append(segment(`${row.name}的模型`, MODEL_CHOICES, row.model, row.key, 'model'),
        segment(`${row.name}的思考強度`, EFFORT_CHOICES, row.effort, row.key, 'effort'));
      pane.append(head, choices);
    }
    if (models.confirming) {
      const box = el('div', 'st-confirm');
      box.setAttribute('role', 'alertdialog');
      box.setAttribute('aria-label', '確認存檔');
      for (const text of confirmNotes(models.data, models.draft)) box.append(el('p', '', text));
      const actions = el('div', 'st-actions');
      actions.append(button('取消', 'cancel'), button('確認變更', 'confirm', true));
      box.append(actions);
      pane.append(box);
    } else if (view.dirty) {
      const actions = el('div', 'st-actions');
      const save = button(models.saving ? '存檔中…' : '存檔', 'save', true);
      save.disabled = models.saving || models.data?.ready === false;
      actions.append(button('還原', 'revert'), save);
      pane.append(actions);
    }
    if (models.note) {
      const note = el('p', 'st-saved', models.note);
      note.setAttribute('role', 'status');
      pane.append(note);
    }
    pane.append(el('p', 'st-updated num', view.foot));
  }

  async function saveModels() {
    const tasks = Object.fromEntries(pending(models.data, models.draft).map(({ task, next }) => [task.key, next]));
    models.saving = true;
    models.confirming = false;
    renderModels();
    try {
      const response = await fetch(MODELS_PATH, {
        method: 'PUT', credentials: 'same-origin', cache: 'no-store',
        headers: { 'content-type': 'application/json' }, body: JSON.stringify({ tasks }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || String(response.status));
      models.data = body;
      models.draft = {};
      models.open = '';
      models.note = '已存檔，下一時段排程開始用。';
    } catch (error) {
      models.note = `無法儲存：${error.message}`;
    }
    models.saving = false;
    renderModels();
    refocus(`#${CSS.escape(panes.models.querySelector('[data-mrow]')?.id || 'none')}`);
  }

  // 重畫之後把焦點放回剛才按的那一顆
  const refocus = (selector) => panes.models.querySelector(selector)?.focus();

  panes.models.addEventListener('click', event => {
    const row = event.target.closest?.('[data-mrow]');
    if (row) {
      models.open = models.open === row.dataset.mrow ? '' : row.dataset.mrow;
      renderModels();
      refocus(`[data-mrow="${row.dataset.mrow}"]`);
      return;
    }
    const pick = event.target.closest?.('[data-mkey]');
    if (pick) {
      const key = pick.dataset.mkey;
      const task = models.data.tasks.find(t => t.key === key);
      const base = models.draft[key] || { model: task.model, effort: task.effort };
      models.draft = { ...models.draft, [key]: nextChoice(base, pick.dataset.model ? { model: pick.dataset.model } : { effort: pick.dataset.effort }) };
      models.confirming = false;
      models.note = '';
      renderModels();
      refocus(`[data-mkey="${key}"]${pick.dataset.model ? `[data-model="${pick.dataset.model}"]` : `[data-effort="${pick.dataset.effort}"]`}`);
      return;
    }
    const action = event.target.closest?.('[data-maction]')?.dataset.maction;
    if (action === 'revert') { models.draft = {}; models.note = ''; renderModels(); }
    if (action === 'cancel') { models.confirming = false; renderModels(); refocus('[data-maction="save"]'); }
    if (action === 'confirm') saveModels();
    if (action === 'save') {
      if (confirmNotes(models.data, models.draft).length) {
        models.confirming = true;
        renderModels();
        refocus('[data-maction="confirm"]');
      } else saveModels();
    }
  });

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
    if (!ok) pane.replaceChildren(el('p', 'st-note', notifyMounted ? '到價推播目前沒有開放。' : '推播設定暫時讀不到，請稍後再試。'));
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
      access.models = true;
      // 資料更新：開關關著時入口回 available: false，就不放這一籤
      try { access.health = Boolean((await getJson('/api/settings/data-health')).available); }
      catch (_) { access.health = false; }
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
    // 資料更新提醒的推播點了會帶 #data-health 打開首頁：直接打開設定的這一籤
    if (access.health && global.location?.hash === '#data-health') { current = 'health'; open(); }
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
