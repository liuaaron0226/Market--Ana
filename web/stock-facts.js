/* 個股頁事實區塊（本人 2026-11-15 共識；規格 .scratch/stock-facts/spec.md）。
   只顯示個股分析結果裡算好的 facts，不在頁面計算或組句子。沒有 facts（開關關著或算不出來）就整塊不出現；
   這裡出錯也只讓這一塊消失，不影響報價、圖與其他內容。盤中即時行情不改這一塊：它是最近資料日的數字。 */
(function () {
  'use strict';
  const TONES = new Set(['up', 'down']);

  function cell(item) {
    const box = document.createElement('div');
    box.className = 'sf-cell';
    const label = document.createElement('span');
    label.className = 'sf-label';
    label.textContent = item.label;
    const value = document.createElement('span');
    value.className = 'sf-value';
    const tick = document.createElement('i');
    tick.className = `sf-tick${TONES.has(item.tone) ? ` ${item.tone}` : ''}`;
    tick.setAttribute('aria-hidden', 'true');
    value.append(tick, document.createTextNode(item.text));
    box.append(label, value);
    return box;
  }

  function render(result) {
    const host = document.getElementById('stockFacts');
    if (!host) return;
    try {
      const today = Array.isArray(result?.facts?.today)
        ? result.facts.today.filter(item => item && typeof item.label === 'string' && typeof item.text === 'string')
        : [];
      if (!today.length) {
        host.hidden = true;
        host.replaceChildren();
        return;
      }
      const row = document.createElement('div');
      row.className = 'sf-today';
      row.dataset.count = String(today.length);
      today.forEach(item => row.append(cell(item)));
      host.replaceChildren(row);
      host.hidden = false;
    } catch (_) {
      host.hidden = true;
      host.replaceChildren();
    }
  }

  window.StockFacts = { render };
})();
