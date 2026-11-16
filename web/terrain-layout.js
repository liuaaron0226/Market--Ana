/* 資金地形的版面計算（立體重設計 B 包；本人 2026-11-14 定案的方向）
   給產業資金流向的產業清單，回傳每棟樓屬於哪個街區、排在第幾格、佔地與樓高。
   單位都是「格」，不碰畫面；畫面層（首頁的地形）只負責把結果畫出來，不另外算一套。

   用法：TerrainLayout.layout(bubbles) → { blocks, towers }
   - blocks：固定四個街區，各有 quadrant、side（x：-1 左、1 右；y：1 上、-1 下）、cols、rows、towers。
   - towers：每棟樓 { name, quadrant, col, row, footprint, height, rank }。
     col、row 從中央十字往外數（0 最靠近十字）；rank 是全體依今日成交額的名次（0 最大）。
   沒有象限的產業不會出現；缺今日成交額或成員檔數的，用最小樓高或最小佔地。

   TerrainLayout.stackTags(boxes, { gap, ceiling }) → 每個名稱牌的新 top（像素）；null＝這一刻藏起來
   （這一個是畫面上的像素，不是格；放在這裡是因為它是純計算、可以單獨測。）
   - boxes：名稱牌在畫面上的框 { left, top, width, height }，照成交額名次排好（最大的在前）。
   - 名次前面的永遠不動；後面的真的疊到前面的（只是靠得近不算）才往上讓開一格，上下隔 gap。
     讓開一格還是疊到，或會高過框頂 ceiling，就藏起來——不一路往上爬，牌才不會離自己的樓太遠、也不會跑出框。
     寬是 0 的（樣式藏起來的）不參與。 */
(function (global) {
  'use strict';

  // 四個街區圍著中央十字，方位和一般象限圖一致。
  const BLOCKS = [
    { quadrant: 'improving', side: { x: -1, y: 1 } },
    { quadrant: 'leading', side: { x: 1, y: 1 } },
    { quadrant: 'weakening', side: { x: 1, y: -1 } },
    { quadrant: 'lagging', side: { x: -1, y: -1 } },
  ];
  // 樓高：壓縮比例（0.45 次方），最大的產業不至於把其他樓壓成平地，最小的也保有看得見的高度。
  const MIN_HEIGHT = 0.12, HEIGHT_RANGE = 4.4, HEIGHT_CURVE = 0.45;
  // 佔地：由成員檔數決定，最大也不滿一格，相鄰的樓之間留得出街道。
  const MIN_FOOTPRINT = 0.46, FOOTPRINT_RANGE = 0.4;

  const amountOf = value => (Number.isFinite(value) && value > 0 ? value : 0);
  // 金額大的在前；同額用名稱決定先後，結果才不會跟著輸入的順序變。
  const bigFirst = (a, b) => b.amount - a.amount || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);

  function layout(bubbles) {
    const known = new Set(BLOCKS.map(block => block.quadrant));
    const industries = (bubbles || []).filter(bubble => bubble && known.has(bubble.quadrant)).map(bubble => ({
      name: String(bubble.name), quadrant: bubble.quadrant,
      amount: amountOf(bubble.amount_today), members: amountOf(bubble.member_count),
    })).sort(bigFirst);
    const maxAmount = Math.max(0, ...industries.map(item => item.amount));
    const maxMembers = Math.max(0, ...industries.map(item => item.members));
    const share = (value, max) => (max > 0 ? value / max : 0);

    const ranked = industries.map((item, rank) => ({
      name: item.name, quadrant: item.quadrant, rank,
      height: MIN_HEIGHT + HEIGHT_RANGE * Math.pow(share(item.amount, maxAmount), HEIGHT_CURVE),
      footprint: MIN_FOOTPRINT + FOOTPRINT_RANGE * Math.sqrt(share(item.members, maxMembers)),
    }));

    const blocks = BLOCKS.map(({ quadrant, side }) => {
      const inBlock = ranked.filter(tower => tower.quadrant === quadrant);
      // 欄數讓街區接近正方形；一列排滿（先排靠近十字的那一列）再往外排下一列。
      const cols = Math.max(2, Math.ceil(Math.sqrt(inBlock.length * 1.3)));
      const towers = inBlock.map((tower, index) => ({
        name: tower.name, quadrant, col: index % cols, row: Math.floor(index / cols),
        footprint: tower.footprint, height: tower.height, rank: tower.rank,
      }));
      return { quadrant, side: { x: side.x, y: side.y }, cols, rows: Math.max(1, Math.ceil(inBlock.length / cols)), towers };
    });
    return { blocks, towers: blocks.flatMap(block => block.towers) };
  }

  const touches = (a, b) => a.left < b.left + b.width && b.left < a.left + a.width && a.top < b.top + b.height && b.top < a.top + a.height;

  function stackTags(boxes, { gap, ceiling }) {
    const placed = [];
    return (boxes || []).map(({ left, top, width, height }) => {
      if (!(width > 0)) return top;
      const spot = { left, top, width, height };
      const blocking = placed.filter(other => touches(spot, other));
      if (blocking.length) {
        // 只讓開一格：移到疊到的那些牌裡最上面那個的上方。還是疊到、或超出框頂，就這一刻先藏起來。
        spot.top = Math.min(...blocking.map(other => other.top)) - height - gap;
        if (spot.top < ceiling || placed.some(other => touches(spot, other))) return null;
      }
      placed.push(spot);
      return spot.top;
    });
  }

  global.TerrainLayout = { layout, stackTags };
})(window);
