/* 大盤頁的指數（本人 2026-11-19：點圖卡左上角的名稱切換台股加權、道瓊、那斯達克、標普 500、日經 225、韓國綜合）。
   - LIST：選單的順序與名稱。國外五個和伺服器的 cloud/lib/market-indices.js 同一份（測試會比對），zone 是收盤日期的地方（us＝美東）。
   - candles(rows, period)：/api/indices 的日線換成 K 線圖要的格式；週K 以週一為一週、月K 以月份為一組。
   - hasVolume(rows)：來源有沒有成交量（日經這類全是 0 的不畫量）。
   - trend(daily, opts)：「短中長期走勢」的規則（market-trend/v1，和 market_trend.py 同一套），台股本機版與國外指數共用；
     國外沒有法人：不傳 institutional5／institutional20 時，中期不列法人那一票、短期不寫 5 日法人。
   要 window.KChartMath（kchart.js）與 window.ChartStudies.kdSeries／rsiSeries（chart-studies.js）。 */
(function (global) {
  'use strict';

  const LIST = [
    { key: 'twii', short: '台股', name: '台股加權' },
    { key: 'dji', short: '道瓊', name: '道瓊工業', zone: 'us' },
    { key: 'ixic', short: '那指', name: '那斯達克綜合', zone: 'us' },
    { key: 'gspc', short: '標普', name: '標普 500', zone: 'us' },
    { key: 'n225', short: '日經', name: '日經 225', zone: 'asia' },
    { key: 'ks11', short: '韓綜', name: '韓國綜合', zone: 'asia' }
  ];

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const labelOf = date => String(date).slice(5).replace('-', '/');
  // 來源偶爾缺高或低（null）：只拿有數字的比，不然 Math.min(null, x) 會變成 0
  const extreme = (pick, a, b) => (finite(a) ? (finite(b) ? pick(a, b) : a) : finite(b) ? b : null);

  function weekKey(dateText) {
    const date = new Date(`${dateText}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() || 7) - 1));
    return date.toISOString().slice(0, 10);
  }

  const bar = row => ({ t: row.date, label: labelOf(row.date), open: row.open, high: row.high, low: row.low, close: row.close,
    volume: finite(row.volume) ? row.volume : null });

  function candles(rows, period) {
    const list = (rows || []).filter(row => row && finite(row.close));
    if (period !== 'weekly' && period !== 'monthly') return list.map(bar);
    const keyOf = period === 'weekly' ? row => weekKey(row.date) : row => String(row.date).slice(0, 7);
    const out = [];
    let key = null;
    for (const row of list) {
      const current = out[out.length - 1];
      if (!current || keyOf(row) !== key) {
        key = keyOf(row);
        out.push(bar(row));
        continue;
      }
      Object.assign(current, { t: row.date, label: labelOf(row.date), close: row.close,
        high: extreme(Math.max, current.high, row.high), low: extreme(Math.min, current.low, row.low) });
      if (finite(row.volume)) current.volume = (finite(current.volume) ? current.volume : 0) + row.volume;
    }
    return out;
  }

  const hasVolume = rows => (rows || []).some(row => row && finite(row.volume) && row.volume > 0);

  // ---------- 短中長期走勢（原本在 minimal.js 的 localMarketTrend，規則不變） ----------
  const fmt = (value, digits = 2) => Number.isFinite(Number(value))
    ? Number(value).toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
  const signed = (value, digits = 2) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return `${n > 0 ? '+' : n < 0 ? '−' : ''}${fmt(Math.abs(n), digits)}`;
  };
  const sideOf = (left, right) => global.TrendVotes.sideOf(left, right);
  const word = side => (side > 0 ? '高於' : side < 0 ? '低於' : '等於');

  function stateOf(score) {
    if (score >= 2) return ['bullish', '偏多'];
    if (score <= -2) return ['bearish', '偏空'];
    return ['neutral', '盤整'];
  }

  function change(values, bars) {
    if (values.length <= bars || !Number.isFinite(values.at(-1)) || !Number.isFinite(values.at(-bars - 1)) || values.at(-bars - 1) === 0) return null;
    return (values.at(-1) / values.at(-bars - 1) - 1) * 100;
  }

  // 量比：近 20 根的平均成交（台股用成交金額，國外用成交量）
  function volumeRatio(list) {
    const field = list.some(row => finite(row.amount)) ? 'amount' : 'volume';
    const values = list.slice(-20).map(row => row[field]).filter(finite);
    const sum = values.reduce((total, value) => total + value, 0);
    return values.length === 20 && sum > 0 && finite(list.at(-1)[field]) ? list.at(-1)[field] / (sum / 20) : null;
  }

  function trend(daily, { dataDate, institutional5, institutional20 } = {}) {
    const list = daily || [];
    const math = global.KChartMath;
    const closes = list.map(row => row.close);
    const ma = Object.fromEntries([5, 10, 20, 60, 120, 240].map(period => [period, math.sma(closes, period)]));
    const macd = math.macd(closes);
    const kd = global.ChartStudies.kdSeries(list);
    const rsi = global.ChartStudies.rsiSeries(list);
    const withInstitutional = institutional5 !== undefined || institutional20 !== undefined;
    const date = dataDate || (list.length ? String(list.at(-1).t).slice(0, 10) : null);
    const close = closes.at(-1);
    const periods = [];
    const insufficient = (key, label, need) => ({ key, label, state: 'insufficient', conclusion: '資料不足',
      sentence: `資料不足：需要至少 ${need} 根完整日 K，目前只有 ${list.length} 根。`, metrics: {}, data_date: date });

    if (list.length < 49 || !macd.at(-1)?.ready || !kd.at(-1)?.ready || !rsi.at(-1)?.ready) {
      periods.push(insufficient('short', '短期', 49));
    } else {
      const ma5 = ma[5].at(-1), ma10 = ma[10].at(-1), hist = macd.at(-1).hist;
      const k = kd.at(-1).k, d = kd.at(-1).d, rsi14 = rsi.at(-1).rsi;
      const ratio = volumeRatio(list);
      const [state, conclusion] = stateOf([sideOf(close, ma5), sideOf(ma5, ma10), sideOf(hist, 0), sideOf(k, d), sideOf(rsi14, 50)].reduce((a, b) => a + b, 0));
      const rsiText = Number.isFinite(rsi14) ? `RSI ${fmt(rsi14, 1)}` : 'RSI 無方向';
      const tail = withInstitutional ? `；量比 ${fmt(ratio)} 倍，5 日法人 ${signed(institutional5, 1)} 億`
        : finite(ratio) ? `；量比 ${fmt(ratio)} 倍` : '';
      const metrics = { close, ma5, ma10, macd_hist: hist, kd_k: k, kd_d: d, rsi14, volume_ratio_20d: ratio };
      if (withInstitutional) metrics.institutional_5d_billion = institutional5;
      periods.push({ key: 'short', label: '短期', state, conclusion, data_date: date, metrics,
        sentence: `${conclusion}：收盤 ${fmt(close)} ${word(sideOf(close, ma5))} MA5 ${fmt(ma5)}，MACD 柱 ${signed(hist)}，KD ${fmt(k, 1)}／${fmt(d, 1)}，${rsiText}${tail}。` });
    }

    if (list.length < 60 || !Number.isFinite(ma[20].at(-1)) || !Number.isFinite(ma[60].at(-1))) {
      periods.push(insufficient('medium', '中期', 60));
    } else {
      const ma20 = ma[20].at(-1), ma60 = ma[60].at(-1), ret20 = change(closes, 20);
      const votes = [sideOf(close, ma20), sideOf(ma20, ma60), sideOf(ret20, 0)];
      if (withInstitutional) votes.push(sideOf(institutional20, 0));
      const [state, conclusion] = stateOf(votes.reduce((a, b) => a + b, 0));
      const metrics = { close, ma20, ma60, return_20d_pct: ret20 };
      if (withInstitutional) metrics.institutional_20d_billion = institutional20;
      periods.push({ key: 'medium', label: '中期', state, conclusion, data_date: date, metrics,
        sentence: `${conclusion}：收盤 ${fmt(close)} ${word(sideOf(close, ma20))} MA20 ${fmt(ma20)}，MA20 ${word(sideOf(ma20, ma60))} MA60 ${fmt(ma60)}，近 20 日 ${signed(ret20, 1)}%${withInstitutional ? `；20 日法人 ${signed(institutional20, 1)} 億` : ''}。` });
    }

    if (list.length < 240 || !Number.isFinite(ma[240].at(-1))) {
      periods.push(insufficient('long', '長期', 240));
    } else {
      const ma60 = ma[60].at(-1), ma120 = ma[120].at(-1), ma240 = ma[240].at(-1), ret60 = change(closes, 60);
      const [state, conclusion] = stateOf([sideOf(close, ma120), sideOf(ma60, ma120), sideOf(ma120, ma240), sideOf(ret60, 0)].reduce((a, b) => a + b, 0));
      periods.push({ key: 'long', label: '長期', state, conclusion, data_date: date,
        sentence: `${conclusion}：收盤 ${fmt(close)} ${word(sideOf(close, ma120))} MA120 ${fmt(ma120)}，MA60 ${fmt(ma60)}／MA120 ${fmt(ma120)}／MA240 ${fmt(ma240)}，近 60 日 ${signed(ret60, 1)}%。`,
        metrics: { close, ma60, ma120, ma240, return_60d_pct: ret60 } });
    }
    return { schema: 'market-trend/v1', rule_version: 'market-trend-v1', data_date: date, periods };
  }

  global.MarketIndex = { LIST, candles, hasVolume, trend };
})(window);
