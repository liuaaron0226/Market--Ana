/* 返回原處（本人 2026-11-24：「從籌碼成交金額點進某檔股票，要返回看其他成交金額大的股票，卻直接回到主要大盤」；
   本人說這只是例子，整類「返回要回到剛剛點進來的地方」都要修）。首頁、個股頁、自選股與庫存頁共用這一支。

   1. 頁面上的返回鍵（帶 data-nav-back 的連結，例如個股頁的「‹ 大盤」）：這一頁是從本站別頁點進來的，就走瀏覽器的上一頁
      （history.back()），和 iPhone 邊緣滑回、瀏覽器的上一頁鍵是同一條路；直接打開的（書籤、推播、網址列）才照連結去大盤。
      「從本站點進來」不看 document.referrer（全站 Referrer-Policy: no-referrer，永遠是空的），
      而是點連結的那一刻在 sessionStorage 記一筆「從哪裡、要去哪裡」，新的一頁對得上就記在自己這一筆歷史紀錄上（history.state）。
      個股頁在頁內換股票（頂列搜尋）用 NavReturn.push：返回鍵先一檔一檔退回去，最後回到原本的清單。
   2. 回到原本那一頁時恢復原樣：瀏覽器整頁快取（bfcache）還原時頁面本來就是原樣，這裡不做事。
      頁面是重新載入的（全站頁面 Cache-Control: no-store，瀏覽器多半不放進整頁快取）時，用離開前存下的樣子還原：
      各頁用 NavReturn.remember(名稱, { save, restore, layer }) 登記自己要記的東西（開著哪一類、哪個面板、哪個分頁、清單展開到哪）；
      只有「經由上一頁／下一頁回來」（navigation type 是 back_forward）才還原，一般打開這一頁照舊。
      還原順序：頁面上的東西（layer 'page'）→ 等資料畫好、版面不再長高 → 整頁捲回原位 → 浮起的面板與對話框（layer 'overlay'）
      → 面板自己裡面的捲動由面板那一段自己捲。使用者在還原途中自己動了（點、捲、按鍵），剩下的就不做。
   用法：NavReturn.remember(name, { save: () => 資料或 null, restore: 資料 => Promise, layer: 'page' | 'overlay', anchor: () => 對齊用的元素 })；
        NavReturn.push(url)；NavReturn.until(test, timeout)。純計算的部分在 NavReturn.model（cloud/test/nav-return.test.js）。 */
(function (global) {
  'use strict';

  const PENDING = 'nav-return:pending';   // 點連結那一刻記下的「從哪裡、要去哪裡」
  const SNAPS = 'nav-return:snaps';       // 每一筆歷史紀錄離開前的樣子
  const FRESH_MS = 30_000;                // 點了之後這麼久內進到的那一頁才算（搜尋結果先記紀錄再換頁，最多 0.9 秒）
  const KEEP = 24;                        // 最多記幾筆歷史紀錄的樣子
  const WAIT_MS = 8000;                   // 一段還原最多等多久（資料讀不到就放棄那一段）

  // ---------- 純計算 ----------
  // 同一頁：Pages 會把 /stock.html 轉成 /stock、/ 是首頁；比較時去掉 .html 與結尾的 index，錨點不算
  function pageKey(href, base) {
    try {
      const url = new URL(href, base);
      const path = url.pathname.replace(/\.html$/, '').replace(/\/index$/, '/');
      return `${url.origin}${path}${url.search}`;
    } catch (_) { return ''; }
  }
  const samePage = (a, b) => Boolean(pageKey(a)) && pageKey(a) === pageKey(b);

  // 剛進到這一頁：是不是從本站別頁點連結進來的？是的話回傳來源網址
  function arrival(pending, here, now, navType) {
    if (!pending || typeof pending !== 'object' || navType !== 'navigate') return null;
    if (!(now - pending.at >= 0 && now - pending.at <= FRESH_MS)) return null;
    if (!samePage(pending.to, here) || typeof pending.from !== 'string') return null;
    return pending.from;
  }

  // 只留最近的幾筆
  function prune(snaps, keep = KEEP) {
    const entries = Object.entries(snaps || {}).filter(([, snap]) => snap && Number.isFinite(snap.at));
    entries.sort((a, b) => b[1].at - a[1].at);
    return Object.fromEntries(entries.slice(0, keep));
  }

  // 元素在整頁裡的位置（版面上的，不含動畫的位移：回來時板子正在落桌，量畫面位置會差幾格；body 被釘住時也不受影響）
  function docTop(node) {
    let top = 0;
    for (let at = node; at; at = at.offsetParent) top += at.offsetTop || 0;
    return top;
  }
  // 整頁要捲到哪：記得那時畫面上方的元素離畫面頂端多遠，就對齊它（上面的區塊晚一點才長出來也對得準）；找不到才用原本的距離
  function scrollTarget(snap, find) {
    const anchor = snap && snap.anchor;
    const node = anchor && find(anchor.id);
    if (node && node.offsetHeight > 0) return Math.max(0, Math.round(docTop(node) - anchor.top));
    return Math.max(0, Math.round((snap && snap.y) || 0));
  }

  // ---------- 存取（私密視窗、關掉網站資料時讀寫會失敗：失敗就當成沒有） ----------
  function read(key) {
    try { return JSON.parse(global.sessionStorage.getItem(key) || 'null'); } catch (_) { return null; }
  }
  function write(key, value) {
    try {
      if (value === null) global.sessionStorage.removeItem(key);
      else global.sessionStorage.setItem(key, JSON.stringify(value));
    } catch (_) { /* 同上 */ }
  }
  const newId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const stateObject = () => (global.history.state && typeof global.history.state === 'object' ? global.history.state : {});
  function mark(fields) {
    try { global.history.replaceState({ ...stateObject(), ...fields }, ''); } catch (_) { /* 沒有歷史紀錄可寫：照舊 */ }
  }

  const doc = global.document;
  const navEntry = global.performance && global.performance.getEntriesByType ? global.performance.getEntriesByType('navigation')[0] : null;
  const navType = (navEntry && navEntry.type) || 'navigate';

  // 這一筆歷史紀錄的編號（存樣子用）；從本站點進來的再記來源
  const from = arrival(read(PENDING), global.location.href, Date.now(), navType);
  write(PENDING, null);
  if (!stateObject().navId) mark({ navId: newId() });
  if (from) mark({ navFrom: from });
  const entryId = () => stateObject().navId || '';

  const parts = new Map();

  // 整頁現在捲到哪：有浮層開著時 body 被釘住（scroll-lock.js），要問它原本的位置
  const pageY = () => (global.ScrollLock && global.ScrollLock.pageY ? global.ScrollLock.pageY() : global.scrollY);

  // 對齊用的元素：畫面上方三分之一處、不在浮層裡、帶 id 的最內層元素（越內層越準）；
  // 它不在各段指定的範圍（例如首頁開著的那一類、自選股頁的清單）裡時，改用那個範圍本身——
  // 範圍外的東西（例如上面的 K 線、解讀卡）回來時高度可能不同，對齊它清單會差幾格
  function pointed() {
    if (!doc.elementsFromPoint) return null;
    const x = global.innerWidth / 2, y = Math.min(global.innerHeight / 3, 240);
    for (const node of doc.elementsFromPoint(x, y)) {
      if (node.closest('dialog, [aria-modal="true"], .panel-backdrop')) continue;
      const owner = node.closest('[id]');
      if (owner && owner !== doc.body && owner !== doc.documentElement) return owner;
    }
    return null;
  }
  function anchorNow() {
    const point = pointed();
    let scope = null;
    for (const part of parts.values()) {
      let node = null;
      try { node = part.anchor ? part.anchor() : null; } catch (_) { node = null; }
      if (!node || !node.id || !(node.offsetHeight > 0)) continue;
      const top = docTop(node) - pageY();
      if (!(top + node.offsetHeight > 0 && top < global.innerHeight)) continue;
      if (point && node.contains(point)) { scope = point; break; }
      scope = scope || node;
    }
    const chosen = scope || point;
    return chosen ? { id: chosen.id, top: Math.round(docTop(chosen) - pageY()) } : null;
  }

  function save() {
    const id = entryId();
    if (!id) return;
    const saved = {};
    parts.forEach((part, name) => {
      try {
        const value = part.save();
        if (value !== null && value !== undefined) saved[name] = value;
      } catch (error) { console.warn('[返回原處]', name, error); }
    });
    const snaps = read(SNAPS) || {};
    snaps[id] = { at: Date.now(), y: Math.round(pageY()), anchor: anchorNow(), parts: saved };
    write(SNAPS, prune(snaps));
  }

  // 點本站的連結：記下從哪裡、要去哪裡，也先把這一頁的樣子存起來（換頁前 pagehide 會再存一次）
  doc.addEventListener('click', event => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest && event.target.closest('a[href]');
    if (!link || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
    let url;
    try { url = new URL(link.href, global.location.href); } catch (_) { return; }
    if (url.origin !== global.location.origin) return;
    if (url.pathname === global.location.pathname && url.search === global.location.search) return;   // 頁內錨點
    write(PENDING, { from: global.location.href, to: url.href, at: Date.now() });
    save();
  }, true);
  global.addEventListener('pagehide', save);

  // ---------- 返回鍵 ----------
  // 掛在 window 的冒泡階段，而且等整頁的程式都掛好之後才掛：換頁的沉下去動效（motion-fx.js）先照常播，這裡再把換頁改成上一頁
  function onBack(event) {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest && event.target.closest('a[data-nav-back]');
    if (!link || !stateObject().navFrom) return;   // 直接打開的：照連結去大盤
    event.preventDefault();
    // 上一筆其實已經不在（極少見）：一秒內沒有離開就照連結走
    let left = false;
    const away = () => { left = true; };
    global.addEventListener('pagehide', away, { once: true });
    global.addEventListener('popstate', away, { once: true });
    global.setTimeout(() => { if (!left) global.location.assign(link.href); }, 1000);
    global.history.back();
  }

  function push(url) {
    global.history.pushState({ navId: newId(), navFrom: global.location.href }, '', url);
  }

  // 等到 test() 成立（資料畫好）；逾時回 false
  function until(test, timeout = WAIT_MS) {
    return new Promise(resolve => {
      const start = Date.now();
      const tick = () => {
        let ok = false;
        try { ok = Boolean(test()); } catch (_) { ok = false; }
        if (ok) resolve(true);
        else if (Date.now() - start >= timeout) resolve(false);
        else global.setTimeout(tick, 50);
      };
      tick();
    });
  }
  const sleep = ms => new Promise(resolve => global.setTimeout(resolve, ms));

  // ---------- 還原 ----------
  let cancelled = false;
  const stop = () => { cancelled = true; };
  async function runLayer(layer, snap) {
    const jobs = [];
    parts.forEach((part, name) => {
      if ((part.layer || 'page') !== layer || !(name in snap.parts)) return;
      let timer = 0;
      const job = Promise.resolve().then(() => part.restore(snap.parts[name])).catch(error => console.warn('[返回原處]', name, error));
      jobs.push(Promise.race([job, new Promise(resolve => { timer = global.setTimeout(resolve, WAIT_MS); })])
        .then(() => global.clearTimeout(timer)));
    });
    await Promise.all(jobs);
  }
  // 捲回原位，接著版面還在長高（上面的區塊晚一點才出現）就再對齊一次，直到 0.3 秒不再變（最多 2.5 秒）
  async function scrollBack(snap) {
    const find = id => doc.getElementById(id);
    const place = () => global.scrollTo({ left: 0, top: scrollTarget(snap, find), behavior: 'instant' });
    place();
    const start = Date.now();
    let height = doc.documentElement.scrollHeight, still = Date.now();
    while (!cancelled && Date.now() - start < 2500 && Date.now() - still < 300) {
      await sleep(50);
      const now = doc.documentElement.scrollHeight;
      if (now !== height) { height = now; still = Date.now(); place(); }
    }
  }
  async function restore() {
    const snap = (read(SNAPS) || {})[entryId()];
    if (!snap || !snap.parts) return;
    try { global.history.scrollRestoration = 'manual'; } catch (_) { /* 舊瀏覽器 */ }
    ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(type => global.addEventListener(type, stop, { once: true, passive: true, capture: true }));
    await runLayer('page', snap);
    if (cancelled) return;
    await scrollBack(snap);
    if (cancelled) return;
    await runLayer('overlay', snap);
  }

  let restoring = null;
  const start = () => {
    global.addEventListener('click', onBack);
    if (navType === 'back_forward') restoring = restore();
  };
  // 等整頁的程式（含 defer 的）都跑完、各段都登記好才開始
  if (doc.readyState === 'complete') global.setTimeout(start, 0);
  else doc.addEventListener('DOMContentLoaded', () => global.setTimeout(start, 0), { once: true });

  global.NavReturn = {
    remember(name, part) { parts.set(name, part); },
    push,
    until,
    done: () => restoring || Promise.resolve(),
    model: { pageKey, samePage, arrival, prune, scrollTarget, docTop, FRESH_MS, KEEP },
  };
})(window);
