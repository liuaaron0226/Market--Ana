/* 個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）。
   只顯示個股分析結果裡算好的 facts，不在頁面計算數字或組句子；這裡只做排版（千分位、正負號、橫條長短）。
   沒有 facts（開關關著或算不出來）就整塊不出現；這裡出錯也只讓這一塊消失，不影響報價、圖與其他內容。
   盤中即時行情不改這一塊：它是最近資料日的數字。 */
(function () {
  'use strict';
  const TONES = new Set(['up', 'down']);
  // 切換籤的名字與順序（本人選的擺法 A）；出現哪幾個由 facts.blocks 決定，頁面不自己判斷。
  const TABS = { summary: '重點', institutional: '法人', credit: '融資券', fundamentals: '基本面', news: '消息', technical: '技術' };
  // 消息那一塊的公告清單：第一次按「消息」才讀，同一檔讀過就不再讀（一次一頁，不會變）。
  const NEWS_FIRST = 5;
  const newsCache = new Map();
  const state = { key: '', code: '', block: 'summary', signals: null };
  // 本人 2026-11-17「我不太喜歡你在個股頁面最上面的排版，太多資訊了，圖二可以移到技術那邊」：
  // 當日三格（#stockFacts）與操作策略（#strategy）平常收在看不到的停放處，打開「技術」時搬到那一塊的最前面。
  // 兩個區塊的內容照舊由本檔的 renderToday 與 stock.js 填（它們一直留在頁面上，只是換位置），事實區塊沒有出現時留在報價卡。
  const TECH_SLOTS = ['stockFacts', 'strategy'];
  let parking = null, slots = null, home = null;
  // 第一次用到時記下兩個區塊與它們原本所在的地方（報價卡）；之後一律用記下的節點，不靠 id 找（被換掉的面板裡就找不到了）
  function slotNodes() {
    if (!slots) {
      slots = TECH_SLOTS.map(id => document.getElementById(id)).filter(Boolean);
      home = slots[0]?.parentElement || null;
    }
    return slots;
  }
  function park() {
    const host = document.getElementById('stockFactsBlocks');
    if (!host || !slotNodes().length) return;
    if (!parking) {
      parking = el('div', 'sf-parking');
      parking.hidden = true;
      host.after(parking);
    }
    parking.append(...slots);
  }
  function unpark(panel) {
    panel.prepend(...slotNodes());
  }
  // 事實區塊不出現（沒有 facts 或出錯）：兩個區塊放回報價卡
  function restore() {
    if (home && slots) home.append(...slots);
  }

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const has = value => value !== null && value !== undefined;
  const num = value => Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const signed = value => value === 0 ? '0' : `${value > 0 ? '+' : ''}${num(value)}`;   // 0 和 -0 都寫 0
  const pct = value => value === 0 ? '0.0%' : `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}%`;   // 一位小數，和句子一致
  const tick = tone => {
    const node = el('i', `sf-tick${TONES.has(tone) ? ` ${tone}` : ''}`);
    node.setAttribute('aria-hidden', 'true');
    return node;
  };

  // 左右對稱的細橫條：正的往右（紅）、負的往左（綠），長短依同一張表裡最大的那個值。
  function bar(value, max) {
    const track = el('span', 'sf-bar');
    track.setAttribute('aria-hidden', 'true');
    if (value && max) {
      const fill = el('u', value > 0 ? 'up' : 'down');
      fill.style.width = `${Math.min(50, Math.abs(value) / max * 50)}%`;
      fill.style[value > 0 ? 'left' : 'right'] = '50%';
      track.append(fill);
    }
    return track;
  }

  // ---------- 當日三格 ----------
  function renderToday(facts) {
    const host = document.getElementById('stockFacts');
    if (!host) return;
    const today = Array.isArray(facts?.today)
      ? facts.today.filter(item => item && typeof item.label === 'string' && typeof item.text === 'string')
      : [];
    if (!today.length) {
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    const row = el('div', 'sf-today');
    row.dataset.count = String(today.length);
    for (const item of today) {
      const value = el('span', 'sf-value');
      value.append(tick(item.tone), document.createTextNode(item.text));
      const box = el('div', 'sf-cell');
      box.append(el('span', 'sf-label', item.label), value);
      row.append(box);
    }
    host.replaceChildren(row);
    host.hidden = false;
  }

  function headRow(labels) {
    const row = el('tr');
    for (const text of labels) row.append(el('th', '', text));
    const thead = el('thead');
    thead.append(row);
    return thead;
  }

  // ---------- 六塊（B 票先有重點、法人、技術） ----------
  function dateLine(blockDate, factsDate) {
    return blockDate && blockDate !== factsDate ? el('p', 'sf-date num', `資料日 ${blockDate}`) : null;
  }

  function summaryPanel(facts) {
    const list = el('ul', 'sf-facts');
    for (const sentence of facts.summary || []) list.append(el('li', '', sentence));
    return [list];
  }

  function institutionalPanel(facts) {
    const block = facts.institutional;
    const rows = block.rows || [];
    const max = Math.max(0, ...rows.flatMap(row => [row.sum5, row.sum10, row.sum20].map(v => Math.abs(v || 0))));
    // 右欄只有約 250 寬：同一張表裡有數字到百萬張時，整張表改用萬張（一位小數），單位寫在表頭。
    const inTenThousands = max >= 1_000_000;
    const lots = value => inTenThousands
      ? `${value > 0 ? '+' : ''}${(value / 10_000).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`
      : signed(value);
    const table = el('table', 'sf-table');
    table.append(headRow([inTenThousands ? '萬張' : '張', '連續', '5 日', '10 日', '20 日']));
    const body = el('tbody');
    for (const row of rows) {
      const tr = el('tr');
      tr.append(el('th', '', row.name));
      const streak = el('td', 'sf-streak');
      if (row.side) {
        streak.append(tick(row.side === 'buy' ? 'up' : 'down'),
          document.createTextNode(`${row.side === 'buy' ? '買' : '賣'} ${row.days} 天${row.at_least ? '以上' : ''}`));
      }
      tr.append(streak);
      for (const value of [row.sum5, row.sum10, row.sum20]) {
        const td = el('td', 'num');
        if (has(value)) td.append(document.createTextNode(lots(value)), bar(value, max));
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body);
    return [dateLine(block.data_date, facts.data_date), table];
  }

  // 技術：本人 2026-11-17「這裡的資訊我覺得我用不到，我也看不懂，你可以換成其他重要的資訊或者是操作手法」——
  // 均線距離表與 RSI／量比／KD 拿掉。這一塊現在放：當日三格與操作策略（從報價卡搬來，見下面 TECH_SLOTS）＋五塊新功能的三張卡。
  function technicalPanel() {
    return [];
  }

  // 近 20 日走勢小圖：缺資料的日子斷開，不補線。
  function sparkline(series) {
    const W = 64, H = 20;
    const values = series.filter(has);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'sf-spark');
    svg.setAttribute('aria-hidden', 'true');
    if (values.length < 2) return svg;
    const hi = Math.max(...values), lo = Math.min(...values);
    const x = i => i / (series.length - 1) * W;
    const y = v => hi === lo ? H / 2 : H - 2 - (v - lo) / (hi - lo) * (H - 4);
    let segment = [];
    const flush = () => {
      if (segment.length > 1) {
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        line.setAttribute('points', segment.join(' '));
        svg.append(line);
      } else if (segment.length === 1) {   // 前後都缺資料的單一天：畫一個小點，不讓它消失
        const [cx, cy] = segment[0].split(',');
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        for (const [k, v] of Object.entries({ cx, cy, r: 1.4 })) dot.setAttribute(k, v);
        svg.append(dot);
      }
      segment = [];
    };
    series.forEach((v, i) => { if (has(v)) segment.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); else flush(); });
    flush();
    return svg;
  }

  function creditPanel(facts) {
    const rows = facts.credit.rows || [];
    const table = el('table', 'sf-table');
    table.append(headRow(['張', '餘額', '5 日增減', '近 20 日']));
    const body = el('tbody');
    for (const row of rows) {
      const tr = el('tr');
      tr.append(el('th', '', row.name), el('td', 'num', num(row.balance)), el('td', 'num', has(row.change5) ? signed(row.change5) : ''));
      const spark = el('td', 'sf-spark-cell');
      spark.append(sparkline(row.series || []));
      tr.append(spark);
      body.append(tr);
    }
    table.append(body);
    // 融資融券比法人晚一個交易日；借券可能又不同一天，不同時分開標。
    const dates = [...new Set(rows.map(row => row.data_date))];
    const borrow = rows.find(row => row.name === '借券');
    const main = rows.find(row => row.name !== '借券') || rows[0];
    // 這一塊固定標資料日（票面要求；融資融券多半比法人晚一天）。
    const text = dates.length > 1 && borrow ? `資料日 ${main.data_date}（借券 ${borrow.data_date}）` : `資料日 ${dates[0]}`;
    return [el('p', 'sf-date num', text), table];
  }

  // ---------- 月營收圖（本人 2026-11-17：「營收長條圖 x 軸我不知道是什麼，看不懂圖」） ----------
  // 參考專業財經網站的月營收圖：長條是每月營收（億元），橫軸標月份（26/03 這種短格式，從最新一個月往回每 3 個月標一個），
  // 選中的那一根（預設最新一個月）上方標數值，圖上方一行讀數寫那個月的營收、年增（最新一個月連月增）。點或滑過某一根就換那一根。
  // 舊資料包沒有營收金額（revenue_100m）時照舊畫年增率，同樣標月份與讀數。
  const TICK_EVERY = 3;
  const yearMonth = period => `${period.slice(0, 4)} 年 ${Number(period.slice(5, 7))} 月`;
  const shortMonth = period => `${period.slice(2, 4)}/${period.slice(5, 7)}`;
  // 億元的位數：百億以上取整數、十億以上一位小數、更小兩位小數
  const hundredMillion = value => Number(value).toLocaleString('en-US', {
    minimumFractionDigits: value >= 100 ? 0 : value >= 10 ? 1 : 2, maximumFractionDigits: value >= 100 ? 0 : value >= 10 ? 1 : 2,
  });

  function revenueChart(series, latest) {
    if (!Array.isArray(series) || !series.length) return null;
    const kind = series.some(item => has(item?.revenue_100m)) ? 'revenue' : series.some(item => has(item?.yoy)) ? 'yoy' : null;
    if (!kind) return null;
    const key = kind === 'revenue' ? 'revenue_100m' : 'yoy';
    const values = series.map(item => item[key]).filter(has);
    const high = Math.max(0, ...values), low = Math.min(0, ...values);
    const span = high - low || 1;
    const zero = high / span;   // 零軸離圖頂多遠（0～1）；營收都是正的，零軸就在最底
    const last = series.length - 1;
    const bars = series.map((item, i) => {
      const value = item[key];
      const tick = (last - i) % TICK_EVERY === 0 ? shortMonth(item.period) : '';
      if (!has(value)) {
        return { period: item.period, tick, missing: true, top: zero, height: 0, label: '', read: `${yearMonth(item.period)}　沒有營收資料`, aria: `${yearMonth(item.period)}沒有營收資料` };
      }
      const size = Math.abs(value) / span;
      const mom = latest?.period === item.period && has(latest.mom) ? latest.mom : null;
      const growth = [has(mom) ? `月增 ${pct(mom)}` : null, has(item.yoy) ? `年增 ${pct(item.yoy)}` : kind === 'revenue' ? '年增：去年同月沒有資料' : null].filter(Boolean);
      const amount = kind === 'revenue' ? `營收 ${hundredMillion(value)} 億元` : null;
      return {
        period: item.period, tick, missing: false,
        top: value >= 0 ? zero - size : zero, height: size,
        label: kind === 'revenue' ? hundredMillion(value) : pct(value),
        read: [yearMonth(item.period), amount, ...growth].filter(Boolean).join('　'),
        aria: `${yearMonth(item.period)}${[amount, ...growth].filter(Boolean).join('，')}`,
      };
    });
    let selected = last;
    while (selected > 0 && bars[selected].missing) selected -= 1;
    return { kind, title: kind === 'revenue' ? '每月營收（億元）' : '每月營收年增率（%）', zero, bars, selected };
  }

  // 畫出來：每一根是一顆鍵（點、滑鼠滑過、鍵盤左右鍵都能選）；同一時間只有選中的那一顆在 Tab 順序裡。
  function revenueFigure(chart) {
    const figure = el('figure', `sf-rev sf-rev-${chart.kind}`);
    const head = el('figcaption', 'sf-rev-head');
    const read = el('p', 'sf-rev-read num');
    read.setAttribute('aria-live', 'polite');
    head.append(el('span', 'sf-rev-title', chart.title), read);
    const plot = el('div', 'sf-rev-plot');
    plot.setAttribute('role', 'group');
    plot.setAttribute('aria-label', `${chart.title}，近 ${chart.bars.length} 個月`);
    plot.style.setProperty('--zero', String(chart.zero));
    const keys = chart.bars.map(bar => {
      const key = el('button', 'sf-rev-col');
      key.type = 'button';
      key.disabled = bar.missing;
      key.setAttribute('aria-label', bar.aria);
      key.style.setProperty('--top', String(bar.top));
      key.style.setProperty('--h', String(bar.height));
      const fill = el('i', 'sf-rev-bar');
      fill.setAttribute('aria-hidden', 'true');
      const value = el('span', 'sf-rev-val', bar.label);
      value.setAttribute('aria-hidden', 'true');
      const tick = el('span', 'sf-rev-x', bar.tick);
      tick.setAttribute('aria-hidden', 'true');
      key.append(fill, value, tick);
      plot.append(key);
      return key;
    });
    const pick = index => {
      if (chart.bars[index]?.missing !== false) return;
      keys.forEach((key, i) => {
        key.setAttribute('aria-pressed', String(i === index));
        key.tabIndex = i === index ? 0 : -1;
      });
      // 每一段（月份、營收、月增、年增）不從中間斷行，窄螢幕放不下時整段換到下一行
      read.replaceChildren(...chart.bars[index].read.split('　').map(text => el('span', '', text)));
    };
    // 點（或鍵盤選）的那一根會留著；滑鼠滑過只是先看一下，離開圖就回到留著的那一根
    let pinned = chart.selected;
    plot.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') pick(pinned); });
    keys.forEach((key, i) => {
      key.addEventListener('click', () => { pinned = i; pick(i); });
      key.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') pick(i); });
      key.addEventListener('keydown', event => {
        const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
        if (!step) return;
        event.preventDefault();
        let next = i + step;
        while (chart.bars[next]?.missing) next += step;
        if (!chart.bars[next]) return;
        pinned = next;
        pick(next);
        keys[next].focus();
      });
    });
    pick(chart.selected);
    figure.append(head, plot);
    return figure;
  }

  function fundamentalsPanel(facts) {
    const block = facts.fundamentals;
    const parts = [];
    const valuation = block.valuation;
    if (valuation) {
      const figures = el('div', 'sf-figs');
      for (const [label, value] of [['本益比', valuation.pe], ['淨值比', valuation.pb], ['殖利率', has(valuation.yield) ? `${num(valuation.yield)}%` : null]]) {
        if (!has(value)) continue;
        const box = el('div');
        box.append(el('span', '', label), el('b', 'num', typeof value === 'string' ? value : num(value)));
        figures.append(box);
      }
      parts.push(dateLine(valuation.data_date, facts.data_date), figures);
    }
    const revenue = block.revenue;
    if (revenue) {
      // 有圖時最新一個月的月增、年增寫在圖的讀數裡；畫不出圖才留原本這一列
      const chart = revenueChart(block.revenue_yoy, revenue);
      const pieces = [has(revenue.mom) ? `月增 ${pct(revenue.mom)}` : null, has(revenue.yoy) ? `年增 ${pct(revenue.yoy)}` : null].filter(Boolean);
      if (chart) parts.push(revenueFigure(chart));
      else if (pieces.length) {   // 月增年增都算不出來就不放這一列（缺什麼就少什麼）
        const sub = el('p', 'sf-sub');
        sub.append(el('span', '', `${Number(revenue.period.slice(5, 7))} 月營收`), el('b', 'num', pieces.join('　')));
        parts.push(sub);
      }
    }
    return parts;
  }

  function newsItem(item) {
    const box = el('details', 'sf-news-item');
    const summary = el('summary');
    summary.append(el('span', 'sf-news-when num', `${item.date.slice(5)} ${item.time.slice(0, 5)}`), el('span', 'sf-news-subject', item.subject));
    box.append(summary);
    if (item.body) box.append(el('div', 'sf-news-body', item.body));
    const meta = [item.clause, item.fact_date ? `事實發生日 ${item.fact_date}` : null].filter(Boolean).join('・');
    if (meta) box.append(el('p', 'sf-news-meta', meta));
    return box;
  }

  // 先放前 NEWS_FIRST 則，其餘收在「更多」後面（公告與新聞標題同一個做法）。
  function fillList(list, items, make) {
    list.replaceChildren(...items.slice(0, NEWS_FIRST).map(make));
    if (items.length > NEWS_FIRST) {
      const more = el('button', 'sf-news-more', `更多（共 ${items.length} 則）`);
      more.type = 'button';
      more.addEventListener('click', () => {
        more.remove();
        list.append(...items.slice(NEWS_FIRST).map(make));
      });
      list.append(more);
    }
  }
  const fillNews = (list, items) => fillList(list, items, newsItem);

  // ---------- 關注股的新聞標題（自選股關注 C 票：只給站主、只有正在關注的那幾檔） ----------
  // 發布時間是 UTC，畫面一律換成台北時間；「今天」也以台北的日期為準。
  const TAIPEI = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const taipei = iso => {
    const part = Object.fromEntries(TAIPEI.formatToParts(new Date(iso)).map(p => [p.type, p.value]));
    return { day: `${part.year}-${part.month}-${part.day}`, text: `${part.month}-${part.day} ${part.hour}:${part.minute}` };
  };
  const headlineCache = new Map();

  function headlineItem(item) {
    const link = el('a', 'sf-headline');
    if (/^https:\/\//.test(item.link)) link.href = item.link;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.append(el('span', 'sf-news-when num', taipei(item.published_at).text), el('span', 'sf-headline-title', item.title));
    if (item.summary) link.append(el('span', 'sf-headline-lead', item.summary));
    link.append(el('span', 'sf-headline-src', item.publisher || ''));
    return link;
  }

  // 沒抓到的來源各一行：今天試過但失敗是「今天沒抓到」，好幾天沒有新的成功是「MM-DD 之後沒抓到」。
  // 這幾天一則都沒有、而且至少有一個來源抓到了，才說「這幾天沒有消息」。
  function fillHeadlines(host, body) {
    if (!body?.available || !body.watching) return;
    const items = Array.isArray(body.items) ? body.items : [];
    const sources = Array.isArray(body.sources) ? body.sources : [];
    const today = taipei(Date.now()).day;
    const parts = sources.filter(source => !source.ok).map(source => {
      const triedToday = taipei(source.last_attempt_at).day === today;
      const since = source.last_success_at ? `${taipei(source.last_success_at).text.slice(0, 5)} 之後` : '';
      return el('p', 'sf-miss', `${source.label} ${triedToday || !since ? '今天' : since}沒抓到`);
    });
    if (items.length) {
      const list = el('div', 'sf-headlines');
      fillList(list, items, headlineItem);
      parts.push(list);
    } else if (sources.some(source => source.ok)) {
      parts.push(el('p', 'sf-quiet', '這幾天沒有消息'));
    }
    host.replaceChildren(...parts);
  }

  async function headlines(code) {
    if (!headlineCache.has(code)) {
      headlineCache.set(code, fetch(`/api/watch/${encodeURIComponent(code)}/news`, { cache: 'no-store' })
        .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
        .catch(() => { headlineCache.delete(code); return null; }));
    }
    return headlineCache.get(code);
  }

  async function newsItems(code) {
    if (!newsCache.has(code)) {
      // 只記住成功的回應；限流或伺服器錯誤不記，下次按「消息」再讀一次。
      newsCache.set(code, fetch(`/api/stocks/${encodeURIComponent(code)}/announcements`, { cache: 'no-store' })
        .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
        .then(body => (body?.available && Array.isArray(body.items) ? body.items : []))
        .catch(() => { newsCache.delete(code); return []; }));
    }
    return newsCache.get(code);
  }

  function newsPanel(facts, code) {
    const industry = el('div', 'sf-kv');
    industry.append(el('span', '', '產業'), el('b', '', facts.industry || ''));
    const list = el('div', 'sf-news');
    // 讀到之前只有產業別；讀不到或這幾天沒有公告也只有產業別（不放說明文字）。
    newsItems(code).then(items => { if (list.isConnected) fillNews(list, items); });
    // 新聞標題接在公告後面；不是站主或這一檔沒有關注時什麼都不出現。
    const press = el('div', 'sf-press');
    headlines(code).then(body => { if (press.isConnected) fillHeadlines(press, body); });
    return [industry, list, press];
  }

  const PANELS = { summary: summaryPanel, institutional: institutionalPanel, credit: creditPanel, fundamentals: fundamentalsPanel,
    news: newsPanel, technical: technicalPanel };

  function renderBlocks(facts, code, signals) {
    const host = document.getElementById('stockFactsBlocks');
    if (!host) return;
    // 防呆：清單裡的每一塊都要有對應的內容才出現（「消息」的內容是產業別，公告清單另外讀）。
    const present = name => (name === 'news' ? Boolean(facts.industry) : Boolean(facts[name]));
    const blocks = (Array.isArray(facts?.blocks) ? facts.blocks : []).filter(name => TABS[name] && present(name));
    if (!blocks.length) {
      state.key = '';
      state.code = '';
      restore();
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    // 盤中每幾秒會重畫一次頁面；內容沒變就不重建，按下的那一塊也不跳回「重點」。
    // 比對整份 facts（不只資料日）：盤後法人資料日更新、K 線資料日不變時也要換新內容。
    const key = `${code}|${JSON.stringify(facts)}|${JSON.stringify(signals ?? null)}`;
    if (key === state.key && host.childElementCount) return;
    if (code !== state.code || !blocks.includes(state.block)) state.block = blocks[0];  // 換一檔就回到「重點」
    state.key = key;
    state.code = code;
    state.signals = signals ?? null;

    const tabs = el('div', 'sf-tabs');
    tabs.setAttribute('role', 'group');
    tabs.setAttribute('aria-label', '事實整理');
    const panel = el('div', 'sf-panel');
    const show = name => {
      state.block = name;
      for (const button of tabs.children) button.setAttribute('aria-pressed', String(button.dataset.block === name));
      park();
      try {
        panel.replaceChildren(...PANELS[name](facts, code).filter(Boolean));
      } catch (_) {
        panel.replaceChildren();
      }
      if (name === 'technical') unpark(panel);
      // 五塊新功能的板（stock-signals.js，獨立檔）：接在這一塊原有內容的後面；它出錯只讓板不出現
      try { window.StockSignals?.fill(panel, name, state.signals, code); } catch (_) { /* 板不出現，這一塊照常 */ }
    };
    for (const name of blocks) {
      const button = el('button', 'sf-tab', TABS[name]);
      button.type = 'button';
      button.dataset.block = name;
      button.addEventListener('click', () => show(name));
      tabs.append(button);
    }
    park();   // 換掉舊面板之前先收回（「技術」開著時兩個區塊在舊面板裡）
    host.replaceChildren(tabs, panel);
    host.hidden = false;
    show(state.block);
  }

  function render(result) {
    try {
      renderToday(result?.facts);
    } catch (_) {
      const host = document.getElementById('stockFacts');
      if (host) { host.hidden = true; host.replaceChildren(); }
    }
    try {
      renderBlocks(result?.facts, result?.code || '', result?.signals);
    } catch (_) {
      const host = document.getElementById('stockFactsBlocks');
      restore();
      if (host) { host.hidden = true; host.replaceChildren(); }
      state.key = '';
      state.code = '';
    }
  }

  window.StockFacts = { render, _logic: { revenueChart } };
})();
