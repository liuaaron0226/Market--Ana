/* 首頁「消息」（本人 2026-11-19「共識達成」，排法 A：.scratch/market-news/consensus.md、樣品 design-previews/market-news/）。
   取代「長期核心持有」。最上面「今天為什麼這樣走」與各市場指數；下面一排「台股｜美股｜日韓中｜我的自選」一次看一組，
   組內依影響大小排；每則：利多（紅）／利空（綠）小記號、中文標題（點了開原文、新分頁）、一句發生什麼、
   影響／要看／準備三段（2026-11-20 起的新寫法，有才畫）、出處、時間、影響大小。
   右上日期鍵看近一個月。開關 market_news_v1 關著時整個分頁不出現（其他三顆鍵平分）。資料：/api/market-news。 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const block = $('marketNews');
  const button = $('newsCatButton');
  if (!block || !button) return;
  const els = {
    meta: $('marketNewsMeta'), summary: $('marketNewsSummary'), status: $('marketNewsStatus'), seg: $('marketNewsGroups'),
    list: $('marketNewsList'), dates: $('marketNewsDates'), dateChip: $('marketNewsDateChip'),
  };
  const GROUPS = [['tw', '台股'], ['us', '美股'], ['asia', '亞洲股市'], ['mine', '自選股']];
  const TONE = { up: '利多', down: '利空', flat: '中性' };
  // 類別小標（本人 11-20：政策、財報、合作關係最會動股價）。市場新聞的 other、總經以外都標；個股新聞六類都標。
  const MARKET_KIND = { policy: '政策', earnings: '財報', deal: '合作訂單', flows: '法人', macro: '總經' };
  const STOCK_KIND = { earnings: '財報營收', guidance: '法說', deal: '訂單合作', policy: '政策', rating: '法人評等', incident: '異常事件' };
  const IMPACT = { 3: '大', 2: '中', 1: '小' };
  // 一天六班（台北）：美股收盤 06:30、盤前 08:00、午盤 11:30、收盤後 14:00、盤後 18:30、美股開盤 23:00
  const SLOT = { us_close: '美股收盤', morning: '盤前', midday: '午盤', close: '收盤後', evening: '盤後', us_open: '美股開盤' };
  const TAIPEI = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const parts = iso => Object.fromEntries(TAIPEI.formatToParts(new Date(iso)).map(p => [p.type, p.value]));
  const clock = iso => { const p = parts(iso); return `${p.hour}:${p.minute}`; };
  const monthDay = iso => { const p = parts(iso); return `${p.month}-${p.day}`; };
  const shortDate = date => date.slice(5);
  let current = null;
  let group = 'tw';
  let loading = 0;

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const pct = v => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}%`;

  // 新寫法（本人 2026-11-20「講影響＋該做的準備」）：影響、要看的條件、準備，一段一行。沒有的那一段不畫；
  // 三段都沒有（舊消息、影響最小的）回 null，畫面就和以前一樣。點名站主股票的那一句只有站主的資料裡有。
  function pointsNode(data) {
    const strings = list => (Array.isArray(list) ? list.filter(text => typeof text === 'string' && text.trim()) : []);
    const rows = [
      ['影響', [data.effect, data.owner_effect].filter(Boolean), data.owner_effect],
      ['觀察', strings(data.conditions)],
      ['因應', strings(data.prepare)],
    ].filter(([, lines]) => lines.length);
    if (!rows.length) return null;
    const dl = el('dl', 'news-points');
    for (const [label, lines, mine] of rows) {
      const dd = el('dd');
      dd.append(...lines.map(text => el('p', text === mine ? 'news-mine' : '', text)));
      dl.append(el('dt', '', label), dd);
    }
    return dl;
  }

  function itemNode(item, { stockTag = false } = {}) {
    const li = el('li', 'news-item');
    const head = el('div', 'news-item-head');
    if (item.tone) head.append(el('span', `news-tone tone-${item.tone}`, TONE[item.tone]));
    const kind = (stockTag ? STOCK_KIND : MARKET_KIND)[item.kind];
    if (kind) head.append(el('span', 'news-kind', kind));
    if (item.market) head.append(el('span', 'news-tag', item.market));
    if (stockTag && item.code) head.append(el('span', 'news-tag', `${item.code} ${item.name || ''}`.trim()));
    const link = el('a', 'news-title', item.title);
    if (/^https:\/\//.test(item.link)) link.href = item.link;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    const meta = el('p', 'news-meta');
    meta.append(el('span', '', `${item.source}${item.translated ? '（譯）' : ''}`),
      el('span', 'num', `${monthDay(item.published_at) === monthDay(current?.run?.created_at || item.published_at) ? '' : monthDay(item.published_at) + ' '}${clock(item.published_at)}`));
    if (item.impact) {
      const impact = el('span', 'news-impact', `影響${IMPACT[item.impact]}`);
      const bars = el('i', 'news-bars');
      bars.dataset.level = String(item.impact);
      bars.setAttribute('aria-hidden', 'true');
      bars.append(el('b'), el('b'), el('b'));
      impact.prepend(bars);
      meta.append(impact);
    }
    if (head.childElementCount) li.append(head);
    li.append(link);
    if (item.reason) li.append(el('p', 'news-reason', item.reason));
    const points = pointsNode(item);
    if (points) li.append(points);
    li.append(meta);
    return li;
  }

  function itemsOf(key) {
    if (!current) return [];
    return key === 'mine' ? current.mine?.items || [] : current.groups?.[key] || [];
  }

  function showGroup(key) {
    group = key;
    for (const b of els.seg.querySelectorAll('button')) b.setAttribute('aria-selected', String(b.dataset.group === key));
    const items = itemsOf(key);
    els.list.replaceChildren(...items.map(item => itemNode(item, { stockTag: key === 'mine' })));
    // 這一組的來源這一輪全部沒抓到：寫出來；有上一輪的就列上一輪的（/api/market-news 的 run.missed、run.carried）
    const run = current?.run;
    const missed = key !== 'mine' && Array.isArray(run?.missed) && run.missed.includes(key);
    const carried = missed && run.carried?.[key];
    els.status.hidden = items.length > 0 && !missed;
    els.status.textContent = carried ? `本時段無資料，先列 ${monthDay(carried)} ${clock(carried)} 時段的內容`
      : missed ? '本時段無資料'
        : key === 'mine' ? (current?.mine?.judged ? '近 30 天無自選股直接相關的重要消息' : '自選股近日無相關新聞')
          : '本時段無消息';
  }

  function renderSegments() {
    els.seg.replaceChildren(...GROUPS.map(([key, label]) => {
      const b = el('button', '', label);
      b.type = 'button';
      b.dataset.group = key;
      b.setAttribute('role', 'tab');
      const count = itemsOf(key).length;
      if (count) b.append(el('small', 'num', String(count)));
      b.addEventListener('click', () => showGroup(key));
      return b;
    }));
  }

  function renderSummary(run) {
    els.summary.replaceChildren();
    els.summary.hidden = !run;
    if (!run) return;
    if (run.classified && run.summary) {
      els.summary.append(el('p', 'news-summary-label', '今日盤勢成因'), el('p', 'news-summary-text', run.summary));
      const points = pointsNode(run);
      if (points) els.summary.append(points);
    }
    if (Array.isArray(run.indices) && run.indices.length) {
      const row = el('div', 'news-indices');
      for (const idx of run.indices) {
        if (typeof idx?.change_pct !== 'number') continue;
        const cell = el('span', 'news-index');
        cell.append(el('span', '', idx.name),
          el('b', `num ${idx.change_pct > 0 ? 'is-up' : idx.change_pct < 0 ? 'is-down' : ''}`, pct(idx.change_pct)));
        row.append(cell);
      }
      els.summary.append(row);
    }
  }

  function renderDates(dates, chosen) {
    els.dates.replaceChildren(...dates.map(date => {
      const b = el('button', '', shortDate(date));
      b.type = 'button';
      b.setAttribute('aria-pressed', String(date === chosen));
      b.addEventListener('click', () => { if (date !== current?.run?.trade_date) load(date); });
      return b;
    }));
    els.dateChip.hidden = !dates.length;
  }

  function render(body) {
    current = body;
    const run = body.run;
    block.dataset.state = run ? 'ready' : 'empty';
    els.meta.textContent = run ? `${shortDate(run.trade_date)} ${SLOT[run.slot] || ''} · 更新 ${clock(run.created_at)}` : '';
    renderSummary(run);
    renderDates(body.dates || [], run?.trade_date);
    els.seg.hidden = !run;
    if (!run) {
      els.list.replaceChildren();
      els.status.hidden = false;
      els.status.textContent = '尚無消息';
      return;
    }
    renderSegments();
    showGroup(group);
  }

  async function load(date) {
    const ticket = ++loading;
    block.dataset.state = 'loading';
    try {
      const response = await fetch(`/api/market-news${date ? `?date=${encodeURIComponent(date)}` : ''}`,
        { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(String(response.status));
      const body = await response.json();
      if (ticket !== loading) return;
      if (!body.available) { button.hidden = true; block.dataset.state = 'off'; return; }
      render(body);
    } catch (_) {
      if (ticket !== loading) return;
      block.dataset.state = 'error';
      els.status.hidden = false;
      els.status.textContent = '消息暫時無法讀取，請稍後再試。';
    }
  }

  async function init() {
    // 開關關著（或讀不到開關）：分頁不出現，這一塊標成 off（不要一直停在「讀取中」）。
    const off = () => { block.dataset.state = 'off'; };
    if (location.protocol === 'file:') return off();
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const policy = response.ok ? await response.json() : null;
      if (!policy?.features?.market_news_v1) return off();
    } catch (_) { return off(); }
    button.hidden = false;
    load(null);
    // 網址帶 #market-news（重大消息的推播點進來）：直接切到「消息」。切換在首頁那排分類鍵，所以照點一下那顆鍵的路走；
    // 這一頁已經開著時點推播，瀏覽器只改錨點、不重新載入，也要切過去並重讀最新一班。
    const openFromHash = (reload) => {
      if (location.hash !== '#market-news' || button.hidden) return;
      if (button.getAttribute('aria-expanded') !== 'true') button.click();
      if (reload) load(null);
    };
    openFromHash(false);
    window.addEventListener('hashchange', () => openFromHash(true));
  }

  init();
})();
