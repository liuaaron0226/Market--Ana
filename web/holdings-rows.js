/* 庫存總覽：把庫存清單（/api/holdings 的 items）讀成畫面要的東西（立體重設計，本人 2026-11-15 定案的 ③）。
   不碰畫面；畫面層（portfolio.js）只負責把結果畫出來，不另外算一套。只呈現現況，不說要調整哪一檔。

   用法：HoldingsRows.read(items) → { count, complete, value, cost, pnl, returnPct, state, rows }
   - count：幾檔。complete：每一檔都有價格（而且至少有一檔）。
   - value：有價格的那幾檔的市值合計。cost：全部的成本合計。pnl、returnPct：complete 才有，否則 null（全部是多少不知道）。
     state：全部合起來是 'bull' 賺、'bear' 賠、'flat' 打平、'none' 不知道。
   - rows：一列一檔，照市值由大到小；沒有價格的排最後（維持原本的先後）。每一列 { code, state, gain, share, base, edge, edgeKind, item }：
     state：'bull' 賺、'bear' 賠、'flat' 打平、'none' 沒有價格。gain：這一檔賺（正）或賠（負）多少；沒有價格是 null。
     橫條兩段（單位是百分比，兩段加起來不會超過 100）：賺錢是 base＝成本、edge＝賺的那一段（edgeKind 'gain'）；
       賠錢是 base＝現在的市值、edge＝還差成本的那一段（edgeKind 'lack'）；打平只有 base；沒有價格都是 null。
       整條的寬度平常是有價格的那幾檔的市值合計，所以賺錢的那一條的長度就是它的占比。賠錢的那一條要畫到成本那麼長，
       如果某一檔的成本比全部的市值還大（賠很多而且占比大），整條的寬度就改用那個成本，大家一起等比例縮短，才不會畫出去。
     share：這一檔占全部庫存市值的百分比；complete 才有。
     item：原本那一筆（名稱、股數、均價、備註……）。 */
(function (global) {
  'use strict';

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const stateOf = gain => (gain === null ? 'none' : gain > 0 ? 'bull' : gain < 0 ? 'bear' : 'flat');

  function read(items) {
    const list = Array.isArray(items) ? items : [];
    const priced = list.filter(item => finite(item.market_value));
    const value = priced.reduce((sum, item) => sum + item.market_value, 0);
    const cost = list.reduce((sum, item) => sum + (finite(item.cost_basis) ? item.cost_basis : 0), 0);
    const complete = list.length > 0 && priced.length === list.length;
    const pnl = complete ? value - cost : null;

    // 整條橫條代表多少錢：全部的市值，或最長的那一條（賠錢時要畫到成本）——取大的
    const full = Math.max(value, ...priced.map(item => (finite(item.cost_basis) ? item.cost_basis : 0)));
    const rowOf = item => {
      if (!finite(item.market_value) || !(value > 0)) return { code: item.code, state: 'none', gain: null, share: null, base: null, edge: null, edgeKind: null, item };
      const basis = finite(item.cost_basis) ? item.cost_basis : 0, gain = item.market_value - basis;
      return {
        code: item.code,
        state: stateOf(gain),
        gain,
        share: complete ? item.market_value / value * 100 : null,
        base: Math.min(basis, item.market_value) / full * 100,
        edge: Math.abs(gain) / full * 100,
        edgeKind: gain > 0 ? 'gain' : gain < 0 ? 'lack' : null,
        item,
      };
    };
    // 市值由大到小；一樣大的與沒有價格的維持原本的先後（Array.prototype.sort 是穩定的）
    const order = list.map((item, index) => ({ item, index })).sort((a, b) => {
      const left = finite(a.item.market_value), right = finite(b.item.market_value);
      if (left !== right) return left ? -1 : 1;
      return left ? b.item.market_value - a.item.market_value || a.index - b.index : a.index - b.index;
    });
    return { count: list.length, complete, value, cost, pnl, returnPct: pnl === null || !cost ? null : pnl / cost * 100, state: stateOf(pnl), rows: order.map(entry => rowOf(entry.item)) };
  }

  global.HoldingsRows = { read };
})(window);
