/* 盯項：首頁頁首「在盯 N 件」（本人 2026-11-20「接下來要看的條件由系統盯」；選樣品 A，design-previews/impact-watch/）。
   收在頁首那張板裡、和行情狀態同一行，不多佔高度：平常寫「在盯 N 件 ›」；有結果時金色圓點＋等到的那件事（變粗）。
   點了用網站現成的浮起面板列清單（開合在 home-fold.js）：每項有條件、對象、「數字」或「AI 讀新聞」、剩幾個交易日；
   點一項看準備與出處，有結果的直接攤開結果與原本寫的準備。沒有盯項或開關 impact_watch_v1 關著時整塊不出現。
   資料：/api/impact-watch。 */
(() => {
  'use strict';
  const key = document.getElementById('impactWatchKey');
  const list = document.getElementById('impactWatchList');
  if (!key || !list) return;
  const KIND = { numeric: '數字', news: 'AI 讀新聞' };
  const AI_NOTE = '由 AI 讀後續的新聞判斷，可能漏看或看錯。';
  const REFRESH_MS = 5 * 60_000;   // 回到這一頁時，隔這麼久才重讀一次
  let loadedAt = 0;

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
  const outcome = item => (missed(item) ? '落空' : '有結果');
  const headline = item => `${missed(item) ? '落空：' : ''}${short(item.label)}`;

  // 清單裡的一項：條件＋剩幾個交易日（或「有結果」與結果）＋對象與類型；點了看說明、準備與出處。有結果的直接攤開。
  function row(item, index) {
    const node = el('details', done(item) ? 'iw-item is-done' : 'iw-item');
    node.style.setProperty('--i', index);
    node.open = done(item);
    const head = el('summary');
    head.append(el('span', 'iw-cond', item.label));
    if (done(item)) {
      const mark = el('span', 'iw-left');
      mark.append(el('i', 'iw-dot'), outcome(item));
      head.append(mark);
      if (item.result.note) head.append(el('span', 'iw-result', item.result.note));
    } else if (Number.isInteger(item.days_left)) {
      head.append(el('span', 'iw-left', `剩 ${item.days_left} 日`));
    }
    const meta = el('span', 'iw-meta');
    meta.append(el('span', '', item.subject === 'market' ? '大盤' : item.subject));
    if (KIND[item.kind]) meta.append(el('span', 'iw-kind', KIND[item.kind]));
    head.append(meta);
    const body = el('div', 'iw-body');
    if (item.note) body.append(el('p', '', item.note));
    const prepare = strings(item.prepare);
    if (prepare.length) {
      const prep = el('p', 'iw-prep');
      prep.append(el('b', '', done(item) ? '原本寫的準備' : '先做好的準備'), ...prepare.map(text => el('span', '', text)));
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
      if (watching) count.append('還盯 ', el('b', '', String(watching)), ' 件');
    } else {
      count.append('在盯 ', el('b', '', String(watching)), ' 件');
    }
    if (count.childNodes.length) box.append(count);
    const arrow = el('span', 'iw-arrow', '›');
    arrow.setAttribute('aria-hidden', 'true');
    box.append(arrow);
    key.classList.toggle('is-news', results.length > 0);
    key.setAttribute('aria-label', results.length
      ? `你在等的事：${short(results[0].label)}${outcome(results[0])}${watching ? `，還盯 ${watching} 件` : ''}`
      : `你在等的事：在盯 ${watching} 件`);
    return box;
  }

  function render(items) {
    key.hidden = !items.length;
    if (!items.length) {
      key.replaceChildren();
      list.replaceChildren();
      document.getElementById('impactWatchDialog')?.close?.();
      return;
    }
    key.replaceChildren(line(items));
    list.replaceChildren(...items.map(row));
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

  init();
})();
