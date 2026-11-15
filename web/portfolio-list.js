/* 自選與庫存併成一份清單：把庫存（/api/holdings 的 items）與自選（/api/watchlist 的 items）整理成一檔一列
   （本人 2026-11-16：頁面太長、要整合；看樣品後選「併成一份清單」）。不碰畫面；畫面層（portfolio.js）只負責把結果畫出來。
   庫存那一邊的算法（總數、順序、橫條）在 holdings-rows.js，這裡直接用它的結果，不另外算。

   用法：PortfolioList.read(holdings, watchlist) → { summary, counts, rows, groups }
   - summary：上面那一行總數，就是 HoldingsRows.read(holdings) 的 count、complete、value、cost、pnl、returnPct、state。
   - counts：三顆篩選鍵上的數字 { all 不重複的檔數, holding 庫存幾檔, watch 自選幾檔 }。
   - rows：一檔一列，同一檔只出現一次。庫存排前面（照 HoldingsRows 的順序：市值由大到小，沒有價格的在後），
     只在自選的排後面（照自選原本的先後）。每一列 { code, name, inUniverse, kinds, holding, watch }：
     kinds：['holding']、['watch'] 或兩個都有——篩選用。
     holding：HoldingsRows 的那一列（state、gain、share、橫條、item），沒有庫存是 null。
     watch：自選的那一筆（horizon 分類……），不在自選是 null。
     name、inUniverse：兩邊都有時用庫存那一筆，只在自選就用自選那一筆。
   - groups：自選用到的群組名稱（本人 2026-11-16：自己取名、一檔只放一個群組），照每個群組最早加入的那一檔的先後；
     時間一樣就照名稱。群組沒有自己的資料：有股票用著才存在。未分組（空字串）不在裡面。

   PortfolioList.sections(model, filter) → 清單要畫的幾段 [{ group, titled, rows }]。filter：'all'、'holding'、'watch'。
   - 'all'、'holding'：一段、沒有標題（titled false）。
   - 'watch'：照群組分段（順序同 groups），未分組放最後；每一段都有標題。全部都沒有分組時只有一段、沒有標題。
   - 沒有東西可畫的段不出現，所以空的清單是 []。

   PortfolioList.actions(row) → 這一列向左滑露出的動作，照畫面由左到右：
   'edit' 修改（有庫存）、'unwatch' 移出自選（在自選）、'delete' 刪除庫存（有庫存，放最右邊）。 */
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
    return { summary, counts: { all: rows.length, holding: held.length, watch: watched.size }, rows, groups: groupsOf(list(watchlist)) };
  }

  const groupOf = watch => (watch && typeof watch.group === 'string' ? watch.group : '');

  function groupsOf(watchlist) {
    const first = new Map();
    watchlist.forEach(item => {
      const name = groupOf(item), at = typeof item.created_at === 'string' ? item.created_at : '';
      if (name && (!first.has(name) || at < first.get(name))) first.set(name, at);
    });
    return [...first.keys()].sort((a, b) => (first.get(a) < first.get(b) ? -1 : first.get(a) > first.get(b) ? 1 : a.localeCompare(b, 'zh-Hant')));
  }

  function sections(model, filter) {
    const rows = model.rows.filter(row => filter === 'all' || row.kinds.includes(filter));
    if (!rows.length) return [];
    if (filter !== 'watch' || !model.groups.length) return [{ group: '', titled: false, rows }];
    return [...model.groups, ''].map(group => ({ group, titled: true, rows: rows.filter(row => groupOf(row.watch) === group) })).filter(section => section.rows.length);
  }

  const actions = row => [...(row.holding ? ['edit'] : []), ...(row.watch ? ['unwatch'] : []), ...(row.holding ? ['delete'] : [])];

  global.PortfolioList = { read, sections, actions, groupOf };
})(window);
