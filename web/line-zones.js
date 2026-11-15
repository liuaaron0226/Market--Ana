/* 收復線／防守線點開之後那張圖：把兩條線、收盤、過去隔天收在三區的比例整理成畫面要的東西
   （本人 2026-11-15：文字看不懂、內容太多，要整理過的圖表說明；看樣品後答「照這樣做」）。
   不碰畫面；畫面層（minimal.js 的 renderLinePanel）只負責把結果畫出來。

   用法：LineZones.read({ upper, lower, close, percent, count }) → { count, rows }
        LineZones.zoneOf(價位, 收復線, 防守線) → 'stand_back'、'between'、'continue_break'，三個數字有一個不是數字就是 null
          （首頁標頭判斷現價落點也用這一個，不另外寫）。
        LineZones.dayOf(線是哪一天畫的, 今天是哪一天) → { mark, word }：那一天就是今天才寫「今天」；
          隔天盤中再看，那已經是上一個交易日——落點的記號改寫「收盤」，句子裡改寫日期（前後帶空格，直接接在中文裡）。
   - upper、lower：收復線、防守線的價位；close：收盤價；
     percent：過去很像的日子隔天收在三區的比例 { stand_back, between, continue_break }；count：那些日子有幾次。
   - rows：由上到下五列，和 K 線圖上的高低一樣——站回、收復線、兩線間、防守線、跌破。
     三區那三列 { kind: 'zone', key, label, percent, text, here }
       percent：橫條的長度，0 到 100；text：寫在旁邊的字（缺資料是「—」，不寫成 0%）；here：收盤是不是落在這一區。
       落在哪一區的規則和首頁判斷落點的一樣：大於等於收復線算站回，小於防守線算跌破。
     兩條線那兩列 { kind: 'line', key, label, value, gap }
       gap：離收盤多遠的一句話（還差幾點、還有幾點、已站上、已跌破、剛好在線上）；沒有收盤價是空字串。
   - count：不是數字時是 null。 */
(function (global) {
  'use strict';

  const ZONES = [['stand_back', '站回'], ['between', '兩線間'], ['continue_break', '跌破']];
  // 資料裡的數字有時是字串；null、空字串、布林不是數字（不能被當成 0）
  const number = value => (value === null || value === '' || typeof value === 'boolean' ? NaN : Number(value));
  // 價位落在哪一區：大於等於收復線算站回，小於防守線算跌破
  function zoneOf(price, upper, lower) {
    if (![price, upper, lower].every(Number.isFinite)) return null;
    if (price >= upper) return 'stand_back';
    return price >= lower ? 'between' : 'continue_break';
  }
  function dayOf(date, today) {
    if (date && date === today) return { mark: '今天', word: '今天' };
    return { mark: '收盤', word: /^\d{4}-\d{2}-\d{2}/.test(date || '') ? ` ${date.slice(5, 7)}/${date.slice(8, 10)} ` : '當天' };
  }
  // 離那條線多遠：still 是還沒碰到線時的說法，passed 是已經過了線的說法
  function gapText(distance, still, passed) {
    if (!Number.isFinite(distance)) return '';
    const whole = Math.round(Math.abs(distance));
    if (whole === 0) return '剛好在線上';   // 差不到半點：寫「還差 0 點」反而奇怪
    return `${distance > 0 ? still : passed} ${whole.toLocaleString('zh-TW')} 點`;
  }

  function read(input) {
    const source = input || {}, shares = source.percent || {};
    const upper = number(source.upper), lower = number(source.lower), close = number(source.close);
    const here = zoneOf(close, upper, lower);
    const zone = ([key, label]) => {
      const share = number(shares[key]);
      return { kind: 'zone', key, label, percent: Number.isFinite(share) ? Math.min(Math.max(share, 0), 100) : 0,
        text: Number.isFinite(share) ? `${Math.round(share)}%` : '—', here: key === here };
    };
    const [above, between, below] = ZONES.map(zone);
    return {
      count: Number.isFinite(number(source.count)) ? number(source.count) : null,
      rows: [
        above,
        { kind: 'line', key: 'upper', label: '收復線', value: upper, gap: gapText(upper - close, '還差', '已站上') },
        between,
        { kind: 'line', key: 'lower', label: '防守線', value: lower, gap: gapText(close - lower, '還有', '已跌破') },
        below,
      ],
    };
  }

  global.LineZones = { read, zoneOf, dayOf };
})(window);
