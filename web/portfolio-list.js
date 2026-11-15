/* 自選與庫存併成一份清單：把庫存（/api/holdings 的 items）與自選（/api/watchlist 的 items）整理成一檔一列
   （本人 2026-11-16：頁面太長、要整合；看樣品後選「併成一份清單」）。不碰畫面；畫面層（portfolio.js）只負責把結果畫出來。
   庫存那一邊的算法（總數、順序、橫條）在 holdings-rows.js，這裡直接用它的結果，不另外算。

   用法：PortfolioList.read(holdings, watchlist) → { summary, counts, rows }
   - summary：上面那一行總數，就是 HoldingsRows.read(holdings) 的 count、complete、value、cost、pnl、returnPct、state。
   - counts：三顆篩選鍵上的數字 { all 不重複的檔數, holding 庫存幾檔, watch 自選幾檔 }。
   - rows：一檔一列，同一檔只出現一次。庫存排前面（照 HoldingsRows 的順序：市值由大到小，沒有價格的在後），
     只在自選的排後面（照自選原本的先後）。每一列 { code, name, inUniverse, kinds, holding, watch }：
     kinds：['holding']、['watch'] 或兩個都有——篩選用。
     holding：HoldingsRows 的那一列（state、gain、share、橫條、item），沒有庫存是 null。
     watch：自選的那一筆（horizon 分類……），不在自選是 null。
     name、inUniverse：兩邊都有時用庫存那一筆，只在自選就用自選那一筆。 */
(function (global) {
  'use strict';

  const list = value => (Array.isArray(value) ? value : []);

  function read(holdings, watchlist) {
    const { rows: held, ...summary } = global.HoldingsRows.read(list(holdings));
    const watched = new Map(list(watchlist).map(item => [item.code, item]));
    const owned = new Set(held.map(row => row.code));
    const entry = (source, holding, watch) => ({
      code: source.code, name: source.name, inUniverse: source.in_universe === true,
      kinds: [...(holding ? ['holding'] : []), ...(watch ? ['watch'] : [])], holding, watch,
    });
    const rows = [
      ...held.map(row => entry(row.item, row, watched.get(row.code) || null)),
      ...list(watchlist).filter(item => !owned.has(item.code)).map(item => entry(item, null, item)),
    ];
    return { summary, counts: { all: rows.length, holding: held.length, watch: watched.size }, rows };
  }

  global.PortfolioList = { read };
})(window);
