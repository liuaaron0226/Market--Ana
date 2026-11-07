// 本機資料來源：讀 *-data.js 放好的全域變數，提供與雲端相同的非同步介面（票 06）。
(() => {
  'use strict';
  const detail = window.MARKET_DETAIL || null;
  window.MarketData = {
    source: 'local',
    async overview() {
      return {
        analysis: window.MARKET_ANALYSIS,
        dashboard: window.MARKET_DASHBOARD,
        chips: window.MARKET_CHIPS || null,
        plain: window.MARKET_PLAIN || null,
        detailIndex: detail ? { days: detail.days, notes: detail.notes } : null,
        current: detail && detail.current ? detail.current : null
      };
    },
    async detail(kind, date) {
      return detail && detail.by_date[date] ? detail.by_date[date] : {};
    }
  };
})();
