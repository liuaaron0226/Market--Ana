/* 首頁「消息」（本人 2026-11-19「共識達成」，排法 A：.scratch/market-news/consensus.md、樣品 design-previews/market-news/）。
   取代「長期核心持有」。最上面「今天為什麼這樣走」與各市場指數；下面一排「台股｜美股｜日韓中｜我的自選」一次看一組，
   組內依影響大小排；每則：利多（紅）／利空（綠）小記號、中文標題（點了開原文、新分頁）、一句理由、出處、時間、影響。
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
  const GROUPS = [['tw', '台股'], ['us', '美股'], ['asia', '日韓中'], ['mine', '我的自選']];
  const TONE = { up: '利多', down: '利空', flat: '中性' };
  const IMPACT = { 3: '大', 2: '中', 1: '小' };
  const SLOT = { morning: '盤前', midday: '午盤', close: '收盤後' };
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

  function itemNode(item, { stockTag = false } = {}) {
    const li = el('li', 'news-item');
    const head = el('div', 'news-item-head');
    if (item.tone) head.append(el('span', `news-tone tone-${item.tone}`, TONE[item.tone]));
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
    els.status.hidden = items.length > 0;
    els.status.textContent = key === 'mine' ? '你的自選股這一天沒有新聞' : '這一組這一輪沒有消息';
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
      els.summary.append(el('p', 'news-summary-label', '今天為什麼這樣走'), el('p', 'news-summary-text', run.summary));
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
      els.status.textContent = '還沒有消息（交易日 08:00、11:30、14:00 更新）';
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
      if (!body.available) { button.hidden = true; return; }
      render(body);
    } catch (_) {
      if (ticket !== loading) return;
      block.dataset.state = 'error';
      els.status.hidden = false;
      els.status.textContent = '消息暫時無法讀取，請稍後再試。';
    }
  }

  async function init() {
    if (location.protocol === 'file:') return;
    try {
      const response = await fetch('/api/features', { cache: 'no-store', headers: { Accept: 'application/json' } });
      const policy = response.ok ? await response.json() : null;
      if (!policy?.features?.market_news_v1) return;   // 開關關著：分頁不出現
    } catch (_) { return; }
    button.hidden = false;
    load(null);
  }

  init();
})();
