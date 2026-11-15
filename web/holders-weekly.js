/* 大戶持股：把每週一筆的集保大戶持股（個股分析的 big_holders）讀成畫面要的東西（立體重設計，本人 2026-11-15 定案的 ⑥）。
   不碰畫面；畫面層（stock.js）只負責把結果畫出來，不另外算一套。

   用法：HoldersWeekly.read(rows, weeks = 12) → { date, title, note, state, levels }，沒有資料回 null。
   - rows：舊到新，每一筆 { date, pct_1000, holders_1000, pct_400, holders_400, holders }。
   - title：一句結論——千張以上的比例連續幾週往同一個方向（「連 4 週增加」「這一週減少」「和前一週一樣」）；只有一週時是 null。
     連續幾週是照全部的資料算的（不只畫出來的那幾週）；中間缺了一週（那一週沒有這一檔的資料）就算到缺的地方為止。
     note：這句話講的是誰（千張以上大戶）。state：這一週的方向，'bull' 增加、'bear' 減少、'flat' 沒變或只有一週。
   - levels：三個級距（千張以上、400 張以上、全部股東；缺數字的級距不給），每一個
     { key, name 鍵上的名稱, label 數字上面的名稱, value 這一週的數字, change 較前一週, kind, read 點了之後下面那一行, bars }。
     kind：這一週的方向 'bull'／'bear'／'none'；只有一週時 change、kind 都是 null。
     bars：最近幾週每週的增減，舊到新、最多 weeks 根，每一根 { kind, size }；size 是 0～1，照這個級距這幾週裡最大的一次增減來比。
   「增加」「減少」講的是數字本身往哪邊走，不是好壞。 */
(function (global) {
  'use strict';

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const fmt = (value, digits) => value.toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (value, digits) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${fmt(Math.abs(value), digits)}`;
  const kindOf = delta => (delta > 0 ? 'bull' : delta < 0 ? 'bear' : 'none');
  const hundredths = value => Math.round(value * 100) / 100;   // 比例到小數兩位；不這樣 58.56 − 58.43 會多出一長串尾數
  // 兩筆資料隔幾週（照日期算，四捨五入）。資料一週一筆，但那一週沒有這一檔時後端會略過，所以不能用「隔幾筆」當成隔幾週
  const weeksApart = (later, earlier) => Math.round((Date.parse(later.date) - Date.parse(earlier.date)) / (7 * 86400000));

  // 比例的級距（千張以上、400 張以上）：數字是持股比例，下面那一行寫人數
  const percentLevel = (key, name, label, pct, people) => ({
    key, name, label,
    of: week => week[pct],
    delta: (week, before) => hundredths(week[pct] - before[pct]),
    value: week => `${fmt(week[pct], 2)}%`,
    change: delta => `${signed(delta, 2)} 個百分點`,
    read: (week, before) => `持股 ${fmt(week[pct], 2)}%，${fmt(week[people], 0)} 人${before && finite(before[people]) ? `（人數 ${signed(week[people] - before[people], 0)}）` : ''}`,
  });
  const LEVELS = [
    percentLevel('pct1000', '千張以上', '千張以上大戶', 'pct_1000', 'holders_1000'),
    percentLevel('pct400', '400 張以上', '400 張以上', 'pct_400', 'holders_400'),
    { key: 'holders', name: '全部股東', label: '全部股東',
      of: week => week.holders,
      delta: (week, before) => week.holders - before.holders,
      value: week => `${fmt(week.holders, 0)} 人`,
      change: delta => `${signed(delta, 0)} 人`,
      // 全部股東看的是這一段時間多了還是少了多少人
      read: (week, before, first) => (first ? `${weeksApart(week, first)} 週前 ${fmt(first.holders, 0)} 人，現在 ${fmt(week.holders, 0)} 人（${signed(week.holders - first.holders, 0)}）` : `全部股東 ${fmt(week.holders, 0)} 人`) },
  ];

  // 千張以上連續幾週往同一個方向。照全部的資料往回數；中間缺了一週（兩筆隔超過一週）就數到那裡為止
  function headline(level, rows) {
    const usable = rows.slice(rows.findLastIndex(week => !finite(level.of(week))) + 1);
    if (usable.length < 2) return null;
    const kind = kindOf(level.delta(usable.at(-1), usable.at(-2)));
    if (kind === 'none') return '和前一週一樣';
    let weeks = 0;
    for (let index = usable.length - 1; index > 0; index -= 1) {
      if (weeksApart(usable[index], usable[index - 1]) !== 1 || kindOf(level.delta(usable[index], usable[index - 1])) !== kind) break;
      weeks += 1;
    }
    return `${weeks > 1 ? `連 ${weeks} 週` : '這一週'}${kind === 'bull' ? '增加' : '減少'}`;
  }

  function read(rows, weeks = 12) {
    const all = Array.isArray(rows) ? rows.filter(row => row && row.date) : [];
    if (!all.length) return null;
    const shown = all.slice(-(weeks + 1)), latest = shown.at(-1), before = shown.length > 1 ? shown.at(-2) : null;
    const levels = LEVELS.filter(level => finite(level.of(latest))).map(level => {
      // 中間有哪一週缺這個級距的數字，就從缺的那一週之後開始算
      const gap = shown.findLastIndex(week => !finite(level.of(week)));
      const usable = shown.slice(gap + 1);
      const deltas = usable.slice(1).map((week, index) => level.delta(week, usable[index]));
      const most = Math.max(...deltas.map(Math.abs), 0);
      const delta = deltas.length ? deltas.at(-1) : null;
      return {
        key: level.key, name: level.name, label: level.label,
        value: level.value(latest),
        change: delta === null ? null : delta === 0 ? '和前一週一樣' : `較前一週 ${level.change(delta)}`,
        kind: delta === null ? null : kindOf(delta),
        read: level.read(latest, usable.length > 1 ? usable.at(-2) : null, usable.length > 1 ? usable[0] : null),
        bars: deltas.map(value => ({ kind: kindOf(value), size: most ? Math.abs(value) / most : 0 })),
      };
    });
    if (!levels.length) return null;
    const lead = levels.find(level => level.key === 'pct1000') || levels[0];
    const state = !before || lead.kind === null || lead.kind === 'none' ? 'flat' : lead.kind;
    return { date: latest.date, title: headline(LEVELS.find(level => level.key === lead.key), all), note: lead.label, state, levels };
  }

  global.HoldersWeekly = { read };
})(window);
