/* 盯項：首頁頁首「在盯 N 件」（本人 2026-11-20「接下來要看的條件由系統盯」；選樣品 A，design-previews/impact-watch/）。
   收在頁首那張板裡、和行情狀態同一行，不多佔高度：平常寫「在盯 N 件 ›」；有結果時金色圓點＋等到的那件事（變粗）。
   點了用網站現成的浮起面板列清單（開合在 home-fold.js）：每項有條件、對象、「數字」或「AI 讀新聞」、剩幾個交易日；
   點一項看準備與出處，有結果的直接攤開結果與原本寫的準備。沒有盯項或開關 impact_watch_v1 關著時整塊不出現。
   個股盯項（票 06，只有站主拿得到）：清單裡對象寫股票名稱與代號、點了進個股頁；個股頁（#stockWatch）只列那一檔的，
   用同一種列，沒有就不出現——stock.js 每次換股票呼叫 ImpactWatch.show(代號)。
   「AI 讀新聞」的（票 05）點開有一句提醒；有結果時附它依據的那一則新聞（標題連到原文）。
   資料：/api/impact-watch。 */
(() => {
  'use strict';
  const key = document.getElementById('impactWatchKey');
  const list = document.getElementById('impactWatchList');
  const stockBox = document.getElementById('stockWatch');   // 個股頁才有
  const home = Boolean(key && list);
  if (!home && !stockBox) return;
  const KIND = { numeric: '數字', news: 'AI 新聞判讀' };
  const AI_NOTE = '由 AI 讀後續的新聞判斷，可能漏看或看錯。';
  const REFRESH_MS = 5 * 60_000;   // 回到這一頁時，隔這麼久才重讀一次
  let loadedAt = 0;
  let latest = [];       // 最近一次讀到的盯項
  let stockCode = '';    // 個股頁現在看的那一檔

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const strings = value => (Array.isArray(value) ? value.filter(text => typeof text === 'string' && text.trim()) : []);
  const monthDay = date => (/^\d{4}-\d{2}-\d{2}$/.test(date || '') ? `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}` : '');
  const done = item => Boolean(item.result);
  const short = label => label.replace(/（.*?）/g, '');   // 那一行放不下價位，點開看得到
  // 條件被否定（確定不會發生）時要講明：只寫條件會被當成「發生了」
  const missed = item => item.status === 'failed';
  const outcome = item => (missed(item) ? '未成立' : '有結果');
  // 對象是個股時，那一行與報讀文字前面帶股票名稱（條件本身不含名稱）
  const isStock = item => item.subject !== 'market';
  const stockTitle = item => (typeof item.subject_name === 'string' && item.subject_name.trim()) || item.subject;
  const named = item => `${isStock(item) ? `${stockTitle(item)} ` : ''}${short(item.label)}`;
  const headline = item => `${missed(item) ? '未成立：' : ''}${named(item)}`;

  // 清單裡的一項：條件＋剩幾個交易日（或「有結果」與結果）＋對象與類型；點了看說明、準備與出處。有結果的直接攤開。
  // 對象：大盤，或股票名稱＋代號（點了進個股頁）
  function subject(item) {
    if (!isStock(item)) return el('span', '', '大盤');
    const link = el('a', 'iw-stock');
    link.href = `stock.html?code=${encodeURIComponent(item.subject)}`;
    if (stockTitle(item) !== item.subject) link.append(`${stockTitle(item)} `);
    link.append(el('span', 'num', item.subject));
    return link;
  }

  // onStockPage：個股頁那一檔自己的列——不用再寫對象，條件前面一個「在盯」小標，有結果的也先收著
  function row(item, index, onStockPage = false) {
    const node = el('details', done(item) ? 'iw-item is-done' : 'iw-item');
    node.style.setProperty('--i', index);
    node.open = done(item) && !onStockPage;   // 個股頁先收著（結果那一句在標題列，收著也看得到），少佔版面
    const head = el('summary');
    const cond = el('span', 'iw-cond', item.label);
    if (onStockPage && !done(item)) cond.prepend(el('span', 'iw-kind', '追蹤中'));
    head.append(cond);
    if (done(item)) {
      const mark = el('span', 'iw-left');
      mark.append(el('i', 'iw-dot'), outcome(item));
      head.append(mark);
      if (item.result.note) head.append(el('span', 'iw-result', item.result.note));
    } else if (Number.isInteger(item.days_left)) {
      head.append(el('span', 'iw-left', `剩 ${item.days_left} 日`));
    }
    const meta = el('span', 'iw-meta');
    if (!onStockPage) meta.append(subject(item));
    if (KIND[item.kind] && (!onStockPage || item.kind === 'news')) meta.append(el('span', 'iw-kind', KIND[item.kind]));
    if (meta.childNodes.length) head.append(meta);
    const body = el('div', 'iw-body');
    if (item.note) body.append(el('p', '', item.note));
    const basis = done(item) ? item.result.evidence : null;
    if (basis && typeof basis.title === 'string' && /^https?:\/\//i.test(basis.link || '')) {
      const link = el('a', '', basis.title);
      link.href = basis.link;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const proof = el('p', 'iw-proof');
      proof.append(el('b', '', '依據的新聞'), link);
      if (basis.source) proof.append(el('span', '', basis.source));
      body.append(proof);
    }
    const prepare = strings(item.prepare);
    if (prepare.length) {
      const prep = el('p', 'iw-prep');
      prep.append(el('b', '', done(item) ? '原訂因應' : '因應準備'), ...prepare.map(text => el('span', '', text)));
      body.append(prep);
    }
    if (item.kind === 'news') body.append(el('p', '', AI_NOTE));
    const from = [monthDay(item.source?.date), item.source?.block].filter(Boolean).join(' ');
    if (from) body.append(el('p', '', `出自 ${from}`));
    node.append(head, body);
    return node;
  }

  // 那一行：有結果時「圓點＋等到的事＋還盯幾件」，沒有時只有件數
  function line(items) {
    const results = items.filter(done);
    const watching = items.length - results.length;
    const box = el('span', 'iw-line');
    const count = el('span', 'iw-count');
    if (results.length) {
      box.append(el('i', 'iw-dot'), el('span', 'iw-text', headline(results[0])));
      if (watching) count.append('尚餘 ', el('b', '', String(watching)), ' 項');
    } else {
      count.append('追蹤 ', el('b', '', String(watching)), ' 項');
    }
    if (count.childNodes.length) box.append(count);
    const arrow = el('span', 'iw-arrow', '›');
    arrow.setAttribute('aria-hidden', 'true');
    box.append(arrow);
    key.classList.toggle('is-news', results.length > 0);
    key.setAttribute('aria-label', results.length
      ? `追蹤事項：${named(results[0])}${outcome(results[0])}${watching ? `，尚餘 ${watching} 項` : ''}`
      : `追蹤事項：追蹤 ${watching} 項`);
    return box;
  }

  // 個股頁：只列現在這一檔的；沒有就整塊不出現
  function renderStock() {
    if (!stockBox) return;
    const mine = stockCode ? latest.filter(item => item.subject === stockCode) : [];
    stockBox.hidden = !mine.length;
    stockBox.replaceChildren(...mine.map((item, index) => row(item, index, true)));
  }

  function render(items) {
    latest = items;
    renderStock();
    if (!home) return;
    key.hidden = !items.length;
    if (!items.length) {
      key.replaceChildren();
      list.replaceChildren();
      document.getElementById('impactWatchDialog')?.close?.();
      return;
    }
    key.replaceChildren(line(items));
    list.replaceChildren(...items.map((item, index) => row(item, index)));
  }

  async function load() {
    loadedAt = Date.now();
    try {
      const response = await fetch('/api/impact-watch', { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) return;   // 讀不到就維持原樣：這一行不是主要內容，不顯示錯誤
      const body = await response.json();
      render(body.available && Array.isArray(body.items) ? body.items.filter(item => item && typeof item.label === 'string') : []);
    } catch (_) { /* 同上 */ }
  }

  async function init() {
    if (location.protocol === 'file:') return;
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const policy = response.ok ? await response.json() : null;
      if (!policy?.features?.impact_watch_v1) return;
    } catch (_) { return; }
    await load();
    if (!home) {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && Date.now() - loadedAt > REFRESH_MS) load();
      });
      return;
    }
    // 網址帶 #impact-watch（之後的推播點進來）：直接打開清單。開合在 home-fold.js，所以照點一下那顆鍵的路走
    const openFromHash = () => {
      if (location.hash === '#impact-watch' && !key.hidden && !document.getElementById('impactWatchDialog')?.open) key.click();
    };
    openFromHash();
    // 這一頁已經開著時點推播（票 04）：瀏覽器只改錨點、不重新載入，也要打開清單
    window.addEventListener('hashchange', openFromHash);
    // 網站常開著放在手機主畫面：回到這一頁時重讀，盤後有結果才看得到
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && Date.now() - loadedAt > REFRESH_MS) load();
    });
  }

  // 個股頁每次換股票（stock.js）：換成那一檔的盯項；資料還沒讀到時先記著，讀到就畫
  window.ImpactWatch = { show(code) { stockCode = String(code || ''); renderStock(); } };

  init();
})();
