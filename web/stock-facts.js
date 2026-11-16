/* 個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）。
   只顯示個股分析結果裡算好的 facts，不在頁面計算數字或組句子；這裡只做排版（千分位、正負號、橫條長短）。
   沒有 facts（開關關著或算不出來）就整塊不出現；這裡出錯也只讓這一塊消失，不影響報價、圖與其他內容。
   盤中即時行情不改這一塊：它是最近資料日的數字。 */
(function () {
  'use strict';
  const TONES = new Set(['up', 'down']);
  // 切換籤的名字與順序（本人選的擺法 A）；出現哪幾個由 facts.blocks 決定，頁面不自己判斷。
  const TABS = { summary: '重點', institutional: '法人', technical: '技術' };
  const state = { key: '', code: '', block: 'summary' };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const has = value => value !== null && value !== undefined;
  const num = value => Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const signed = value => `${value > 0 ? '+' : ''}${num(value)}`;
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

  function technicalPanel(facts) {
    const block = facts.technical;
    const rows = block.rows || [];
    const max = Math.max(0, ...rows.map(row => Math.abs(row.distance_pct || 0)));
    const table = el('table', 'sf-table');
    table.append(headRow(['', '價位', '收盤距離']));
    const body = el('tbody');
    for (const row of rows) {
      const tr = el('tr');
      tr.append(el('th', '', row.name), el('td', 'num', num(row.value)));
      const distance = el('td', 'num');
      distance.append(document.createTextNode(`${signed(row.distance_pct)}%`), bar(row.distance_pct, max));
      tr.append(distance);
      body.append(tr);
    }
    table.append(body);
    const figures = el('div', 'sf-figs');
    const readings = [
      ['RSI', has(block.rsi) ? num(block.rsi) : null],
      ['量比', has(block.volume_ratio) ? `${num(block.volume_ratio)} 倍` : null],
      ['KD', has(block.kd_k) && has(block.kd_d) ? `${Math.round(block.kd_k)} / ${Math.round(block.kd_d)}` : null],
    ].filter(([, value]) => value !== null);
    for (const [label, value] of readings) {
      const box = el('div');
      box.append(el('span', '', label), el('b', 'num', value));
      figures.append(box);
    }
    return readings.length ? [table, figures] : [table];
  }

  const PANELS = { summary: summaryPanel, institutional: institutionalPanel, technical: technicalPanel };

  function renderBlocks(facts, code) {
    const host = document.getElementById('stockFactsBlocks');
    if (!host) return;
    const blocks = (Array.isArray(facts?.blocks) ? facts.blocks : []).filter(name => TABS[name] && facts[name]);
    if (!blocks.length) {
      state.key = '';
      state.code = '';
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    // 盤中每幾秒會重畫一次頁面；內容沒變就不重建，按下的那一塊也不跳回「重點」。
    // 比對整份 facts（不只資料日）：盤後法人資料日更新、K 線資料日不變時也要換新內容。
    const key = `${code}|${JSON.stringify(facts)}`;
    if (key === state.key && host.childElementCount) return;
    if (code !== state.code || !blocks.includes(state.block)) state.block = blocks[0];  // 換一檔就回到「重點」
    state.key = key;
    state.code = code;

    const tabs = el('div', 'sf-tabs');
    tabs.setAttribute('role', 'group');
    tabs.setAttribute('aria-label', '事實整理');
    const panel = el('div', 'sf-panel');
    const show = name => {
      state.block = name;
      for (const button of tabs.children) button.setAttribute('aria-pressed', String(button.dataset.block === name));
      try {
        panel.replaceChildren(...PANELS[name](facts).filter(Boolean));
      } catch (_) {
        panel.replaceChildren();
      }
    };
    for (const name of blocks) {
      const button = el('button', 'sf-tab', TABS[name]);
      button.type = 'button';
      button.dataset.block = name;
      button.addEventListener('click', () => show(name));
      tabs.append(button);
    }
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
      renderBlocks(result?.facts, result?.code || '');
    } catch (_) {
      const host = document.getElementById('stockFactsBlocks');
      if (host) { host.hidden = true; host.replaceChildren(); }
      state.key = '';
      state.code = '';
    }
  }

  window.StockFacts = { render };
})();
