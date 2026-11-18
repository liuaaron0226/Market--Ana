/* 自己設定的均線（本人 2026-11-17）：「我希望我可以自己設定均線，不要有上限」；首頁大盤與個股頁「共用一組」；顏色「讓我自己挑」。
   一組均線是 [{ period, color }]：period 是天數（2 以上的整數，不設上限，只受載入的 K 棒根數限制），
   color 是下面 8 色的代號。存在這台裝置（localStorage 'ma-lines'）；個股頁以前只存天數（'stock-ma-periods'），沒有新的就接過來。
   對外：window.MaLines = { PALETTE, read, write, normalize, add, setPeriod, watch, tokenOf, nameOf }。設定那一小塊在 ma-settings.js。 */
(function (global) {
  'use strict';

  const KEY = 'ma-lines', OLD_KEY = 'stock-ma-periods';
  // 8 色（紅綠留給 K 棒與預測線）。顏色值在 minimal.css 的主題變數，深淺色各一組。
  // 前四色是原本均線的四色；8 色無法兩兩都分得很開（橘與金、褐與金比較近），圖上方用同色寫名稱補足
  const PALETTE = [
    { key: 'orange', name: '橘', token: '--accent' },
    { key: 'gold', name: '金', token: '--gold' },
    { key: 'blue', name: '藍', token: '--study-dif' },
    { key: 'gray', name: '灰', token: '--dim' },
    { key: 'purple', name: '紫', token: '--ma-purple' },
    { key: 'teal', name: '青', token: '--ma-teal' },
    { key: 'brown', name: '褐', token: '--ma-brown' },
    { key: 'rose', name: '玫瑰', token: '--ma-rose' },
  ];
  const DEFAULT = [{ period: 18, color: 'orange' }, { period: 45, color: 'gold' }];
  const COMMON = [5, 10, 20, 60, 120, 240];   // 加一條時先挑這些常用的
  const known = key => PALETTE.some(item => item.key === key);

  // 下一個顏色：還沒用過的第一個；8 色都用過就照用過的條數從頭輪
  function nextColor(used) {
    const free = PALETTE.find(item => !used.includes(item.key));
    return free ? free.key : PALETTE[used.length % PALETTE.length].key;
  }

  // 整理：天數四捨五入、小於 2 或不是數字的丟掉、重複的只留第一條；顏色不認得的給一個還沒用過的
  function normalize(lines) {
    const kept = [];
    for (const line of Array.isArray(lines) ? lines : []) {
      const period = Math.round(Number(line && line.period));
      if (!Number.isFinite(period) || period < 2 || kept.some(item => item.period === period)) continue;
      kept.push({ period, color: line.color });
    }
    const used = [];
    return kept.map(line => {
      const color = known(line.color) ? line.color : nextColor(used);
      used.push(color);
      return { period: line.period, color };
    });
  }

  function storage(action) {
    try { return action(global.localStorage || (typeof localStorage !== 'undefined' ? localStorage : null)); } catch (_) { return null; }   // 私密視窗存不了
  }

  function read() {
    const saved = storage(store => store && JSON.parse(store.getItem(KEY) || 'null'));
    let lines = Array.isArray(saved) ? normalize(saved) : [];
    if (!lines.length) {
      const old = storage(store => store && JSON.parse(store.getItem(OLD_KEY) || 'null'));
      if (Array.isArray(old)) lines = normalize(old.map(period => ({ period })));
    }
    return lines.length ? lines : normalize(DEFAULT);
  }

  function write(lines) {
    const clean = normalize(lines);
    storage(store => store && store.setItem(KEY, JSON.stringify(clean)));
    return clean;
  }

  // 加一條：天數取常用裡還沒有的（都有了就取最長那條的兩倍），顏色取還沒用過的
  function add(lines) {
    const clean = normalize(lines);
    const periods = clean.map(line => line.period);
    const period = COMMON.find(value => !periods.includes(value)) || Math.max(...periods) * 2;
    return [...clean, { period, color: nextColor(clean.map(line => line.color)) }];
  }

  // 改某一條的天數：和別條重複、小於 2 或不是數字就不改（回傳 null，頁面把格子改回原本的數字）——
  // 不然整理時重複的那條會被默默丟掉，畫面上少一條也沒有提示
  function setPeriod(lines, index, value) {
    const period = Math.round(Number(String(value).trim() || NaN));
    if (!Number.isFinite(period) || period < 2) return null;
    if (lines.some((line, i) => i !== index && line.period === period)) return null;
    return lines.map((line, i) => (i === index ? { ...line, period } : line));
  }

  // 兩頁共用一組：別的分頁改了（storage 事件），或從另一頁按「上一頁」回來（整頁還原、不會重新載入）時，把最新的那組交給 callback
  function watch(callback) {
    global.addEventListener('storage', event => { if (event.key === KEY) callback(read()); });
    global.addEventListener('pageshow', event => { if (event.persisted) callback(read()); });
  }

  // 代號換成 CSS 變數名稱（畫圖時才照目前的深淺色主題取值，換主題不用重設）與中文名稱
  const entryOf = key => PALETTE.find(entry => entry.key === key) || PALETTE[0];
  const tokenOf = key => entryOf(key).token;
  const nameOf = key => entryOf(key).name;

  global.MaLines = { PALETTE, read, write, normalize, add, setPeriod, watch, tokenOf, nameOf };
})(typeof window !== 'undefined' ? window : globalThis);
