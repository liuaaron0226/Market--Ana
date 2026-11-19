/* 首頁「產業資金流向」下面的各產業強勢股（本人 2026-11-20 定案，排法 A：一排資金狀態鍵，一次看一種）。
   資料來自產業資金流向 API 的 strong_stocks（sector_rotation.strong_stocks）：每個產業近 3 個交易日漲最多的前 3 檔。
   plan／caption 只算要畫什麼，不碰畫面（cloud/test/rotation-strong.test.js）；render／detail 才動畫面。樣式在 home.css〈各產業強勢股〉。

   用法：RotationStrong.render(host, { bubbles, quadrants, strong })；RotationStrong.detail(strong, 產業名稱) 給地形的點樓明細用。 */
(function (global) {
  'use strict';

  // 四個資金狀態在畫面上的順序（和資金地形、表格一樣：領先、減速、回補、落後）
  const ORDER = ['leading', 'weakening', 'improving', 'lagging'];

  /* 每個資金狀態要畫什麼：cards＝有強勢股的產業（照產業資金流向原本的順序），quiet＝沒有上漲股的產業名稱，count＝這個狀態的產業數。 */
  function plan(bubbles, groups) {
    return ORDER.map(quadrant => {
      const industries = bubbles.filter(bubble => bubble.quadrant === quadrant);
      const stocksOf = bubble => groups[bubble.name] || [];
      return {
        quadrant,
        count: industries.length,
        cards: industries.filter(bubble => stocksOf(bubble).length).map(bubble => ({ name: bubble.name, stocks: stocksOf(bubble) })),
        quiet: industries.filter(bubble => !stocksOf(bubble).length).map(bubble => bubble.name),
      };
    });
  }

  // 標題旁的說明：數字跟著這一批資料帶來的規則走，規則改了這裡不用跟著改
  function caption(strong) {
    const wan = (strong.min_avg_turnover / 10000).toLocaleString('zh-TW');
    return `近 ${strong.window_days} 日漲最多的前 ${strong.limit} 檔・平均每天成交 ${wan} 萬元以上`;
  }

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const signed = value => (Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}%` : '—');
  // 產業名稱的寫法和資金地形的名稱牌一樣（去掉結尾的「業」「工業」）
  const shortName = name => String(name).replace(/工?業$/, '');
  const NONE_TEXT = days => `近 ${days} 日沒有上漲的股票`;

  // 一檔一列：上漲小記號、名稱、代號、漲停記號、今日漲跌、近 3 日漲幅；整列點了進個股頁
  function stockRow(stock) {
    const link = el('a', 'strong-row');
    link.href = `stock.html?code=${encodeURIComponent(stock.code)}`;
    // 名稱太長時只縮名稱；代號與漲停記號不被擠掉
    const name = el('span', 'strong-name');
    name.append(el('b', '', stock.name), el('small', '', stock.code));
    if (stock.limit_up) name.append(el('i', 'strong-limit', '漲停'));
    link.append(name, el('span', `strong-today num${stock.today_pct < 0 ? ' is-down' : ''}`, signed(stock.today_pct)),
      el('strong', 'num', signed(stock.gain_pct)));
    return link;
  }

  function industryCard(card, quadrant, index, days) {
    const node = el('article', 'strong-card');
    node.dataset.quadrant = quadrant;
    node.style.setProperty('--i', index);
    const head = el('header');
    head.append(el('h5', '', shortName(card.name)), el('span', 'strong-col', '今日'), el('span', 'strong-col', `近 ${days} 日`));
    node.append(head, ...card.stocks.map(stockRow));
    return node;
  }

  function statePanel(state, days) {
    const wrap = el('div', 'strong-group');
    if (state.cards.length) {
      const grid = el('div', 'strong-grid');
      grid.append(...state.cards.map((card, index) => industryCard(card, state.quadrant, index, days)));
      wrap.append(grid);
    }
    if (state.quiet.length) wrap.append(el('p', 'strong-none', `${NONE_TEXT(days)}：${state.quiet.map(shortName).join('、')}`));
    if (!state.count) wrap.append(el('p', 'strong-none', '今天沒有產業在這個資金狀態'));
    return wrap;
  }

  /* 畫在 host 裡。這一批資料沒有強勢股（舊的發布結果）就整段收起來。 */
  function render(host, payload) {
    const strong = payload && payload.strong;
    host.replaceChildren();
    host.hidden = !strong;
    if (!strong) return;
    const quadrants = payload.quadrants || {};
    const states = plan(payload.bubbles || [], strong.groups || {});
    const days = strong.window_days;

    const head = el('div', 'strong-head');
    head.append(el('h4', '', '各產業強勢股'), el('p', '', caption(strong)));
    const seg = el('div', 'strong-seg');
    seg.setAttribute('role', 'tablist');
    seg.setAttribute('aria-label', '資金狀態');
    const panel = el('div', 'strong-panel');
    panel.setAttribute('role', 'tabpanel');
    const show = state => {
      seg.querySelectorAll('button').forEach(button => button.setAttribute('aria-selected', String(button.dataset.quadrant === state.quadrant)));
      panel.replaceChildren(statePanel(state, days));
    };
    states.forEach(state => {
      const button = el('button', '', (quadrants[state.quadrant] && quadrants[state.quadrant].label) || state.quadrant);
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.dataset.quadrant = state.quadrant;
      button.append(el('small', '', String(state.count)));
      button.addEventListener('click', () => show(state));
      seg.append(button);
    });
    host.append(head, seg, panel);
    // 一進來先看資金領先；那一種今天沒有產業，就停在第一個有產業的
    show(states.find(state => state.count) || states[0]);
  }

  /* 點樓明細裡的那一段：這個產業的強勢股；沒有強勢股資料時回 null（明細照舊）。 */
  function detail(strong, name) {
    if (!strong) return null;
    const stocks = (strong.groups || {})[name] || [];
    const box = el('div', 'strong-detail');
    box.append(el('p', 'strong-detail-title', `近 ${strong.window_days} 日強勢股`));
    if (stocks.length) box.append(...stocks.map(stockRow));
    else box.append(el('p', 'strong-none', NONE_TEXT(strong.window_days)));
    return box;
  }

  global.RotationStrong = { plan, caption, render, detail };
})(window);
