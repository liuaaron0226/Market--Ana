/* 資金浮雕的版面計算（立體重設計 E，本人 2026-11-15 選的方向）。只做計算，不碰畫面；畫面在 relief.js。

   用法：ReliefLayout.layout(items)
   - items：一項一筆 { key, name, value, tone, signed, values }。
     value 是今天要寫出來的字、tone 是它的顏色（up／down／mid），原樣帶出；
     values 是由舊到新的每日數值（最後一筆是今天）；signed 表示這一項有買有賣（外資現貨、投信）。
   - 回傳每條軌道 { key, name, value, tone, signed, days }，days 的每一天是 { direction, size, today }：
     有買有賣的項目：direction 是 up（買超，往上長）、down（賣超，往下凹）或 flat（剛好 0）；
       size 是那一天的幅度占這段期間最大幅度的比例（0 到 1）。
     只有高低的項目：direction 都是 level；size 是那一天在這段期間最低到最高之間的位置（0 到 1；全部一樣高時都是 1）。
     那一天沒有數值：direction 是 none、size 是 0，不畫。 */
(function (global) {
  'use strict';

  const usable = value => typeof value === 'number' && Number.isFinite(value);

  function signedDays(values) {
    const peak = Math.max(0, ...values.filter(usable).map(Math.abs));
    return values.map(value => {
      if (!usable(value)) return { direction: 'none', size: 0 };
      if (value === 0 || peak === 0) return { direction: 'flat', size: 0 };
      return { direction: value > 0 ? 'up' : 'down', size: Math.abs(value) / peak };
    });
  }

  function levelDays(values) {
    const known = values.filter(usable);
    const low = Math.min(...known), high = Math.max(...known);
    return values.map(value => {
      if (!usable(value)) return { direction: 'none', size: 0 };
      return { direction: 'level', size: high === low ? 1 : (value - low) / (high - low) };
    });
  }

  function layout(items) {
    return (items || []).map(item => {
      const values = Array.isArray(item.values) ? item.values : [];
      const days = (item.signed ? signedDays(values) : levelDays(values)).map((day, index) => ({ ...day, today: index === values.length - 1 }));
      return { key: item.key, name: item.name, value: item.value, tone: item.tone, signed: Boolean(item.signed), days };
    });
  }

  global.ReliefLayout = { layout };
})(window);
