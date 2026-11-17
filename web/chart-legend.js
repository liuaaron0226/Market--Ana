/* K 線上方那一行的圖例（本人 2026-11-17 券商版面；首頁大盤與個股頁共用）：
   每條線一個名稱（字是線的顏色）＋目前的數值＋方向 ▲▼；自設的均線（kind:'ma'）是一顆鍵，點了打開設定（ma-settings.js，
   帶 data-ma-open：點它不算點到設定那一塊的外面）；判斷線（mark）名稱前面一小段同色的線；
   預測線或關注線和某條自設均線同天數（合成一條）時，那條均線後面一枚小字（tags，例如「轉強」「觸發」）。
   資料由 kchart 的 legendItems() 給。內容一樣就不重畫（十字線每動一下都會叫）。樣式在 chart-legend.css。
   對外：window.ChartLegend = { render(host, items), format(value) }。 */
(function (global) {
  'use strict';

  // 千元以上取整、以下最多兩位小數（和圖上價籤同一套）
  const format = value => (value >= 1000 ? Math.round(value).toLocaleString('en-US') : value.toLocaleString('en-US', { maximumFractionDigits: 2 }));
  const shown = new WeakMap();

  function row(item) {
    const isMa = item.kind === 'ma';
    const node = document.createElement(isMa ? 'button' : 'span');
    node.className = item.mark ? 'legend-name is-line' : isMa ? 'legend-name is-ma' : 'legend-name';
    if (isMa) {
      node.type = 'button';
      node.dataset.maOpen = '';
      node.setAttribute('aria-label', `${item.name}${Number.isFinite(item.value) ? ` ${format(item.value)}` : ''}，設定均線`);
    }
    node.style.setProperty('--tone', item.color);
    if (item.alpha < 1) node.style.opacity = String(item.alpha);
    const name = document.createElement('b');
    name.textContent = item.name;
    node.append(name);
    if (Number.isFinite(item.value)) {
      const value = document.createElement('span');
      value.className = 'num';
      value.textContent = format(item.value);
      // 有數值的一律留箭頭的位置（持平時看不見）：拖十字線時寬度不變，手機上這一行不會在一行、兩行之間跳
      const arrow = document.createElement('i');
      arrow.className = item.dir === 'up' ? 'is-up' : item.dir === 'down' ? 'is-down' : 'is-flat';
      arrow.textContent = item.dir === 'down' ? '▼' : '▲';
      if (item.dir) arrow.setAttribute('aria-label', item.dir === 'up' ? '往上' : '往下');
      else arrow.setAttribute('aria-hidden', 'true');
      node.append(value, arrow);
    }
    for (const tag of item.tags || []) {
      const mark = document.createElement('em');
      mark.className = 'legend-tag';
      mark.textContent = tag.text;
      mark.style.setProperty('--tag', tag.color);
      node.append(mark);
    }
    return node;
  }

  // 有重畫回傳 true（頁面可能要重新量這一行的高度）
  function render(host, items) {
    const key = JSON.stringify(items);
    if (shown.get(host) === key) return false;
    shown.set(host, key);
    host.replaceChildren(...items.map(row));
    return true;
  }

  global.ChartLegend = { render, format };
})(window);
