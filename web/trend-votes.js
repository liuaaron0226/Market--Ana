/* 短中長期走勢：把現有的走勢資料（market-trend/v1）讀成畫面要的東西（立體重設計，本人 2026-11-15 定案的 ⑤）。
   不碰畫面；畫面層（trend-matrix.js）只負責把結果畫出來，不另外算一套。

   用法：TrendVotes.read(trend) → { title, note, periods }，不是走勢資料回 null。
        TrendVotes.sideOf(左, 右) → 1、-1、0：規則的比法（首頁本機來源重算走勢時也用這一個，不另外寫）。
   - title、note：最上面那一句（三期一不一致）。
   - periods：每一期 { key, label, conclusion, state, sentence, votes, extra }。
     state：'bull' 偏多、'bear' 偏空、'flat' 盤整、'none' 資料不足。
     votes：照規則的順序，每一票 { name, side, text }；side 是 1 偏多、-1 偏空、0 沒有方向（兩個數字相等或缺資料）。
            比法和規則（market_trend.py）一樣。資料不足、或算出來的票和資料裡的結論對不上時是 null——畫面退回 sentence。
     extra：不算票、但原本說明裡有的數字（短期的量比與 5 日法人），沒有就是 null。
     sentence：原本那句說明，拿掉開頭的結論。 */
(function (global) {
  'use strict';

  const STATES = { bullish: 'bull', bearish: 'bear', neutral: 'flat', insufficient: 'none' };
  const LINES = { short: '短線', medium: '中線', long: '長線' };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const fmt = (value, digits) => value.toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const signed = (value, digits) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${fmt(Math.abs(value), digits)}`;

  // 和規則同一個比法：缺任何一邊或兩邊相等就是沒有方向
  function sideOf(left, right) {
    if (!finite(left) || !finite(right) || Math.abs(left - right) <= 1e-9) return 0;
    return left > right ? 1 : -1;
  }
  const word = side => (side > 0 ? '高於' : side < 0 ? '低於' : '等於');

  // 兩個數字比高低的那一種票：收盤對均線（帶差幾 %）、均線對均線、K 對 D
  function versus(name, leftLabel, left, rightLabel, right, digits, withGap) {
    const side = sideOf(left, right);
    if (!finite(left) || !finite(right)) return { name, side, text: `${leftLabel}對${rightLabel}：沒有資料` };
    const gap = withGap && right !== 0 ? `（${signed((left / right - 1) * 100, 1)}%）` : '';
    return { name, side, text: `${leftLabel} ${fmt(left, digits)} ${word(side)} ${rightLabel} ${fmt(right, digits)}${gap}` };
  }
  // 一個數字對一條線的那一種票：MACD 柱對零軸、RSI 對 50、報酬對 0、法人合計對 0
  function against(name, value, line, missing, text) {
    const side = sideOf(value, line);
    return { name, side, text: finite(value) ? text(side) : missing };
  }

  const RULES = {
    short: m => [
      versus('MA5', '收盤', m.close, 'MA5', m.ma5, 2, true),
      versus('MA5/10', 'MA5', m.ma5, 'MA10', m.ma10, 2, false),
      against('MACD', m.macd_hist, 0, 'MACD 柱：沒有資料', side => `MACD 柱 ${signed(m.macd_hist, 2)}，${side > 0 ? '在零軸上' : side < 0 ? '在零軸下' : '在零軸上（剛好是 0）'}`),
      versus('KD', 'K', m.kd_k, 'D', m.kd_d, 1, false),
      against('RSI', m.rsi14, 50, 'RSI 無方向', side => `RSI14 ${fmt(m.rsi14, 1)}，${word(side)} 50`),
    ],
    medium: m => [
      versus('MA20', '收盤', m.close, 'MA20', m.ma20, 2, true),
      versus('MA20/60', 'MA20', m.ma20, 'MA60', m.ma60, 2, false),
      against('近 20 日', m.return_20d_pct, 0, '近 20 日：沒有資料', () => `近 20 日 ${signed(m.return_20d_pct, 1)}%`),
      // 國外指數（本人 11-19 大盤切換）沒有法人：資料裡沒有這一項就不列這一票（台股缺資料是 null，照樣列「沒有資料」）
      ...('institutional_20d_billion' in m ? [against('法人', m.institutional_20d_billion, 0, '近 20 日外資加投信合計：沒有資料', side =>
        `近 20 日外資加投信合計${side < 0 ? '賣超' : side > 0 ? '買超' : ''} ${fmt(Math.abs(m.institutional_20d_billion), 1)} 億`)] : []),
    ],
    long: m => [
      versus('MA120', '收盤', m.close, 'MA120', m.ma120, 2, true),
      versus('MA60/120', 'MA60', m.ma60, 'MA120', m.ma120, 2, false),
      versus('MA120/240', 'MA120', m.ma120, 'MA240', m.ma240, 2, false),
      against('近 60 日', m.return_60d_pct, 0, '近 60 日：沒有資料', () => `近 60 日 ${signed(m.return_60d_pct, 1)}%`),
    ],
  };

  // 規則：合計 2 以上偏多、-2 以下偏空，其餘盤整
  const stateOf = score => (score >= 2 ? 'bull' : score <= -2 ? 'bear' : 'flat');

  function readPeriod(period) {
    const state = STATES[period.state] || 'none', metrics = period.metrics || {};
    const conclusion = String(period.conclusion || '');
    // 那句話開頭自己帶著結論（「偏空：…」）；照那句話寫的拿掉，不看 conclusion 欄位——兩邊萬一不一樣，退回文字時才不會出現兩個結論
    const sentence = String(period.sentence || '').replace(/^(偏多|偏空|盤整|資料不足)：/, '');
    const rule = state === 'none' ? null : RULES[period.key];
    let votes = rule ? rule(metrics) : null;
    if (votes && stateOf(votes.reduce((sum, vote) => sum + vote.side, 0)) !== state) votes = null;
    const extras = period.key !== 'short' || state === 'none' ? [] : [
      finite(metrics.volume_ratio_20d) ? `量比 ${fmt(metrics.volume_ratio_20d, 2)} 倍` : '',
      finite(metrics.institutional_5d_billion) ? `5 日法人 ${signed(metrics.institutional_5d_billion, 1)} 億` : '',
    ].filter(Boolean);
    const extra = extras.length ? extras.join('，') : null;
    return { key: period.key, label: String(period.label || ''), conclusion, state, sentence, votes, extra };
  }

  // 最上面那一句：有資料不足的期就先講那個；三期都一樣是同調，否則一期一期列出來
  function headline(periods) {
    const known = periods.filter(period => period.state !== 'none'), missing = periods.filter(period => period.state === 'none');
    const list = known.map(period => `${LINES[period.key] || period.label}${period.conclusion}`).join('、');
    if (!known.length) return { title: '資料不足', note: '' };
    if (missing.length) return { title: `${missing.map(period => period.label).join('、')}資料不足`, note: list };
    if (known.every(period => period.state === known[0].state)) {
      return known[0].state === 'flat' ? { title: '三期都在盤整', note: '' } : { title: '三期同調', note: `短中長線都${known[0].conclusion}` };
    }
    return { title: '三期不同調', note: list };
  }

  function read(trend) {
    if (!trend || trend.schema !== 'market-trend/v1' || !Array.isArray(trend.periods)) return null;
    const periods = trend.periods.map(readPeriod);
    return { ...headline(periods), periods };
  }

  global.TrendVotes = { read, sideOf };
})(window);
