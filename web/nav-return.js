/* 返回原處（本人 2026-11-24：「從籌碼成交金額點進某檔股票，要返回看其他成交金額大的股票，卻直接回到主要大盤」；
   本人說這只是例子，整類「返回要回到剛剛點進來的地方」都要修）。首頁、個股頁、自選股與庫存頁共用這一支。

   1. 頁面上的返回鍵（帶 data-nav-back 的連結，例如個股頁的「‹ 大盤」）：這一頁是從本站別頁點進來的，就走瀏覽器的上一頁
      （history.back()），和 iPhone 邊緣滑回、瀏覽器的上一頁鍵是同一條路；直接打開的（書籤、推播、網址列）才照連結去大盤。
      「從本站點進來」不看 document.referrer（全站 Referrer-Policy: no-referrer，永遠是空的），
      而是點連結的那一刻在 sessionStorage 記一筆「從哪裡、要去哪裡」，新的一頁對得上就記在自己這一筆歷史紀錄上（history.state）。
      個股頁在頁內換股票（頂列搜尋）用 NavReturn.push：返回鍵先一檔一檔退回去，最後回到原本的清單。
   2. 回到原本那一頁時恢復原樣：各頁用 NavReturn.remember(名稱, { save, restore }) 登記自己要記的東西
      （開著哪一類、哪個面板、哪個分頁、清單展開到哪），離開前（點站內連結、pagehide）存成這一筆歷史紀錄的樣子。
      - 頁面重新載入回來：這一筆歷史紀錄已經帶著編號、又存過樣子，而且不是重新整理，就照樣子還原（見下面 decide；
        不靠 navigation type，iPhone Safari 回報的不可靠）。一般打開這一頁照舊。
      - 整頁快取（bfcache）回來：和存下的樣子比，不一樣的那幾段才補。
      還原順序：分類、面板、對話框一開始就打開 → 資料到了填進去 → 最後才捲（整頁跟著版面對齊，面板裡的由那一段自己捲）。
      碰一下螢幕不算；使用者明顯去做別的事（點別的按鈕、捲一段）才不再替他捲，已經還原的留著。
   用法：NavReturn.remember(name, { save: () => 資料或 null, restore: 資料 => Promise, anchor: () => 對齊用的元素 })；
        NavReturn.push(url)；NavReturn.until(test, timeout)；NavReturn.still()（還沒被使用者打斷）。純計算的部分在 NavReturn.model（cloud/test/nav-return.test.js）。 */
(function (global) {
  'use strict';

  const PENDING = 'nav-return:pending';   // 點連結那一刻記下的「從哪裡、要去哪裡」
  const SNAPS = 'nav-return:snaps';       // 每一筆歷史紀錄離開前的樣子
  const LEFT = 'nav-return:left';         // 最後離開（pagehide）的是哪一筆歷史紀錄
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

  // 這一次載入要不要照存下的樣子還原（2026-11-24 夜，本人用 iPhone 在正式站試：左緣滑回，首頁回到頁首、什麼都沒打開）。
  // 原本只認 navigation type 是 back_forward；WebKit 的 Navigation Timing 在上一頁、整頁快取這幾條路上回報不可靠，
  // 回報成別的就整個不還原。改成看「這一筆歷史紀錄載入時已經帶著我們的編號、而且有存過它的樣子」：
  // 新點進來的一頁是新的一筆，沒有 state；按上一頁回到的那一筆，state 還在。只有確定是重新整理時才不還原
  // （新的 API 說 reload，或舊的 performance.navigation.type 是 1；舊的說 2＝上一頁／下一頁，就算新的說 reload 也還原）。
  // 另外：剛剛離開的就是這同一筆（重新整理；或在同一個網址上再打開一次，Chrome 會沿用同一筆紀錄、state 也留著）也不還原——
  // 這一條只靠我們自己在 pagehide 記的「最後離開的是哪一筆」，不靠瀏覽器回報。
  // 2026-11-25 凌晨第三版：連「瀏覽器說這是重新整理」也不信了（iPhone 回到 no-store 的頁面是重新向伺服器要，
  // 有可能被回報成 reload）。重新整理只靠我們自己記的「剛剛離開的就是這同一筆」（leftSame）來認。
  // 代價：離開超過幾秒才重新載入同一筆（例如分頁被系統回收後重開）會照存下的樣子還原——那是回到原本在看的地方，可以接受。
  function decide({ hadId, hasSnap, leftSame }) {
    return Boolean(hadId && hasSnap && !leftSame);
  }

  const doc = global.document;
  const perf = global.performance;
  const navEntry = perf && perf.getEntriesByType ? perf.getEntriesByType('navigation')[0] : null;
  const navType = (navEntry && navEntry.type) || 'navigate';
  const legacyType = perf && perf.navigation && typeof perf.navigation.type === 'number' ? perf.navigation.type : null;

  // 這一筆歷史紀錄的編號（存樣子用）；從本站點進來的再記來源
  const hadId = Boolean(stateObject().navId);
  const left = read(LEFT);
  // 只看最近 5 秒：重新整理、同網址再開都是離開後馬上載入；去了沒有這一支的頁面（帳號管理、外站）再回來，中間通常更久。
  // 寧可偶爾在重新整理後多還原一次，也不要把「上一頁回來」誤判成重新整理而不還原
  const leftSame = Boolean(hadId && left && left.id === stateObject().navId && Date.now() - left.at >= 0 && Date.now() - left.at < 5_000);
  const from = arrival(read(PENDING), global.location.href, Date.now(), navType);
  write(PENDING, null);
  if (!hadId) mark({ navId: newId() });
  if (from) mark({ navFrom: from });
  const entryId = () => stateObject().navId || '';
  const snapOf = id => (id ? (read(SNAPS) || {})[id] || null : null);
  const restoreOnLoad = decide({ hadId, hasSnap: Boolean(snapOf(entryId())), navType, legacyType, leftSame });
  // 診斷（手邊沒有 iPhone 可驗）：每次載入、每次整頁快取回來、每一輪還原做完各記一筆「還原了沒、依據是什麼、捲到哪」，只留最近 12 筆，
  // 只有判斷用的幾個值，沒有任何個人資料。在同一個分頁把網址結尾加上 #navdebug 再載入，這幾筆會印在畫面下方。
  const LOG = 'nav-return:log';
  function note(fields) {
    try {
      const log = read(LOG);
      const rows = Array.isArray(log) ? log : [];
      rows.push({ at: new Date().toISOString().slice(11, 19), page: global.location.pathname, ...fields });
      write(LOG, rows.slice(-12));
    } catch (_) { /* 診斷失敗不影響功能 */ }
  }
  note({ how: 'load', hadId, hasSnap: Boolean(snapOf(entryId())), navType, legacyType, leftSame, restore: restoreOnLoad });
  // 要還原的話，瀏覽器自己不要先捲（捲動由這裡負責，免得兩邊搶）
  if (restoreOnLoad) { try { global.history.scrollRestoration = 'manual'; } catch (_) { /* 舊瀏覽器 */ } }

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

  let clickSavedAt = 0;   // 上一次因為點站內連結而存樣子的時間
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
    clickSavedAt = Date.now();
  }, true);
  // 剛點連結時存的那一份算數：pagehide 當下浮層的釘住已經放開（scroll-lock.js），在 iPhone 上這時量到的整頁位置不一定可靠，
  // 不拿它蓋掉點連結那一刻存的。不是點連結離開的（上一頁、下一頁、手勢）才在這裡存
  global.addEventListener('pagehide', () => {
    if (Date.now() - clickSavedAt > 3000) save();
    write(LEFT, { id: entryId(), at: Date.now() });
  });

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
  // 順序：開著的那一類、面板、對話框一開始就照存下的樣子打開（清單照常先顯示讀取中）→ 資料到了填進去 → 最後才捲：
  // 整頁的位置在版面還在長高時一路跟著對齊，各段面板裡的捲動由那一段在資料到了之後自己捲（先問 NavReturn.still()）。
  // 碰一下螢幕不算離開；只有使用者明顯去做別的事（點了別的按鈕或連結、整頁或面板捲了一段距離）才不再替他捲，已經還原的留著。
  let cancelled = false, touchY = null, wheeled = 0;
  const MOVE_PX = 40;                       // 手指拖、滾輪捲超過這麼多才算使用者自己在捲
  const stop = () => { cancelled = true; };
  const watchers = {
    click: event => {
      if (!event.isTrusted) return;   // 還原時程式自己點的不算
      if (event.target.closest && event.target.closest('button, a[href], summary, input, select, textarea, [role="tab"], [data-chip]')) stop();
    },
    touchstart: event => { touchY = event.touches && event.touches[0] ? event.touches[0].clientY : null; },
    touchmove: event => {
      const y = event.touches && event.touches[0] ? event.touches[0].clientY : null;
      if (touchY !== null && y !== null && Math.abs(y - touchY) > MOVE_PX) stop();
    },
    wheel: event => { wheeled += Math.abs(event.deltaY || 0); if (wheeled > MOVE_PX * 2) stop(); },
    // 捲動事件本身不看：版面長高時瀏覽器自己也會調捲動位置，分不出是不是人捲的；只看手指、滾輪、鍵盤
    keydown: event => { if (['PageDown', 'PageUp', 'ArrowDown', 'ArrowUp', ' ', 'Home', 'End', 'Escape'].includes(event.key)) stop(); },
  };
  function watch(on) {
    Object.entries(watchers).forEach(([type, fn]) => {
      if (on) global.addEventListener(type, fn, { capture: true, passive: true });
      else global.removeEventListener(type, fn, { capture: true, passive: true });
    });
  }
  // 正在還原的這一輪：snap、還沒開始的段（有的段要等資料到了才登記，例如首頁的明細面板在 minimal.js 讀完大盤資料之後才登記）、
  // 正在跑的段數。登記晚的段一登記就開始（NavReturn.remember）
  let active = null;
  function runPart(name) {
    const part = parts.get(name);
    if (!active || !part || !active.waiting.has(name)) return;
    active.waiting.delete(name);
    if (cancelled) return;   // 使用者已經去做別的事：還沒開始的不再開
    const round = active;
    round.running += 1;
    let timer = 0;
    const job = Promise.resolve().then(() => part.restore(round.snap.parts[name])).catch(error => console.warn('[返回原處]', name, error));
    Promise.race([job, new Promise(resolve => { timer = global.setTimeout(resolve, WAIT_MS); })])
      .then(() => { global.clearTimeout(timer); round.running -= 1; });
  }
  const pageHeight = () => Math.max(doc.documentElement.scrollHeight || 0, (doc.body && doc.body.scrollHeight) || 0);
  // 整頁捲到存下的位置；浮層開著（body 釘住）時改釘住的位置
  function place(snap) {
    const y = Math.min(scrollTarget(snap, id => doc.getElementById(id)), Math.max(0, pageHeight() - global.innerHeight));
    if (global.ScrollLock && global.ScrollLock.pageTo) global.ScrollLock.pageTo(y);
    else global.scrollTo({ left: 0, top: y, behavior: 'instant' });
  }
  // 各段同時開始；整頁的位置在它們做完之前一路跟著版面對齊，做完之後版面 0.3 秒不再變就停。
  // 還沒登記的段最多等 WAIT_MS（那一頁可能根本沒有那一段）
  async function apply(snap, names, scroll) {
    cancelled = false; touchY = null; wheeled = 0;
    watch(true);
    const round = { snap, waiting: new Set(names), running: 0 };
    active = round;
    names.forEach(runPart);
    await sleep(0);   // 各段先動手（分類、面板先打開），再開始捲
    const begin = Date.now();
    const busy = () => round.running > 0 || (round.waiting.size > 0 && !cancelled && Date.now() - begin < WAIT_MS);
    let height = -1, still = Date.now();
    while (Date.now() - begin < WAIT_MS * 2) {
      if (scroll && !cancelled) {
        const now = pageHeight();
        if (now !== height) { height = now; still = Date.now(); place(snap); }
      }
      if (!busy() && (cancelled || !scroll || Date.now() - still >= 300)) break;
      await sleep(50);
    }
    if (active === round) active = null;
    watch(false);
    // 診斷：這一輪做到哪（要捲到哪、實際在哪、有沒有被打斷、哪幾段沒等到）；一秒後再量一次，看有沒有別的東西又把頁面捲走
    const target = scrollTarget(snap, id => doc.getElementById(id));
    const report = when => note({ how: when, parts: names.join(','), target, y: Math.round(pageY()), winY: Math.round(global.scrollY || 0),
      height: pageHeight(), anchor: (snap.anchor && snap.anchor.id) || '', cancelled, late: [...round.waiting].join(','), ms: Date.now() - begin });
    report('applied');
    global.setTimeout(() => report('after1s'), 1000);
  }
  // 頁面重新載入回來：每一段都照存下的樣子做
  async function restore() {
    const snap = snapOf(entryId());
    if (!snap || !snap.parts) return;
    await apply(snap, Object.keys(snap.parts), true);
  }
  // 整頁快取（bfcache）回來：頁面應該是原樣；和存下的樣子比，哪一段不一樣才補那一段，位置差太多才捲。都一樣就不動。
  // 比的時候數字差一兩格不算不一樣；捲動位置（top）差 MOVE_PX 以內也不算
  function same(a, b, key) {
    if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) <= (key === 'top' ? MOVE_PX : 2);
    if (a && b && typeof a === 'object' && typeof b === 'object') {
      const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
      return [...keys].every(k => same(a[k], b[k], k));
    }
    return a === b || (a == null && b == null);
  }
  function differs(snap) {
    const names = [];
    parts.forEach((part, name) => {
      let now = null;
      try { now = part.save(); } catch (_) { now = null; }
      const was = snap.parts[name];
      if (was !== undefined && !same(now, was)) names.push(name);
    });
    const off = Math.abs(pageY() - scrollTarget(snap, id => doc.getElementById(id))) > MOVE_PX;
    return { names, off };
  }
  async function repair() {
    const snap = snapOf(entryId());
    if (!snap || !snap.parts) return;
    const { names, off } = differs(snap);
    note({ how: 'bfcache', differs: names.join(','), off });
    if (!names.length && !off) return;
    await apply(snap, names, off || names.length > 0);
  }

  let restoring = null;
  const start = () => {
    global.addEventListener('click', onBack);
    if (restoreOnLoad) restoring = restore();
    if (global.location.hash === '#navdebug' && doc.body) {
      const box = doc.createElement('pre');
      box.style.cssText = 'position:fixed;z-index:99999;left:8px;right:8px;bottom:8px;max-height:60vh;overflow:auto;margin:0;padding:12px;border-radius:12px;background:#fff;color:#111;font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap;box-shadow:0 2px 12px rgba(0,0,0,.2)';
      box.textContent = (read(LOG) || []).map(row => JSON.stringify(row)).join('\n');
      doc.body.appendChild(box);
    }
  };
  global.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    const snap = snapOf(entryId());
    if (!snap || !snap.parts) note({ how: 'bfcache', hasSnap: false });
    restoring = repair();
  });
  // 等整頁的程式（含 defer 的）都跑完、各段都登記好才開始
  if (doc.readyState === 'complete') global.setTimeout(start, 0);
  else doc.addEventListener('DOMContentLoaded', () => global.setTimeout(start, 0), { once: true });

  global.NavReturn = {
    remember(name, part) { parts.set(name, part); runPart(name); },   // 還原已經開始、這一段才登記：馬上補做
    push,
    until,
    still: () => !cancelled,   // 各段在資料到了之後要捲自己的面板前先問：使用者已經去做別的事就不捲
    done: () => restoring || Promise.resolve(),
    model: { pageKey, samePage, arrival, prune, scrollTarget, docTop, decide, same, FRESH_MS, KEEP },
  };
})(window);
