/* 個股盤中投影：用即時價替換／新增當日暫定 K，沿用 analyze_v2.py 的指標、價位與訊號規則。 */
(function () {
  'use strict';

  const SHARED = new Set(['ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma240', 'prior_high_20', 'high_20', 'high_60', 'intraday_high_60',
    'vp_center', 'vp_high', 'swing_high', 'breakout_failure', 'gap_down_upper', 'gap_down_lower', 'heavy_volume', 'heavy_high']);
  const STRUCTURAL = new Set(['prior_high_20', 'high_20', 'low_20', 'high_60', 'low_60', 'intraday_high_60', 'vp_center', 'vp_high',
    'vp_low', 'swing_high', 'swing_low', 'breakout_failure', 'gap_up_upper', 'gap_up_lower', 'gap_down_upper', 'gap_down_lower', 'ma60', 'ma120', 'ma240']);
  const WEIGHTS = {
    vp_center: 1.5, prior_high_20: 1.4, swing_high: 1.2, vp_high: 1.1, breakout_failure: 1.1,
    gap_down_upper: 1.1, intraday_high_60: 1, gap_down_lower: 1, ma60: 1, ma120: 1,
    heavy_volume: .9, ma20: .9, heavy_high: .8, ma5: .7, ma10: .7, high_20: 1.1,
    high_60: 1.1, low_20: 1.1, low_60: 1.1, vp_low: 1.1, swing_low: 1.2,
    gap_up_upper: 1.1, gap_up_lower: 1, ma240: 1, heavy_low: .8,
  };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const pyRound = (value, digits = 0) => {
    if (!Number.isFinite(value)) return value;
    const scale = 10 ** digits;
    const scaled = value * scale;
    const sign = scaled < 0 ? -1 : 1;
    const absolute = Math.abs(scaled);
    const floor = Math.floor(absolute);
    const fraction = absolute - floor;
    const rounded = Math.abs(fraction - .5) < 1e-10 ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(absolute);
    return sign * rounded / scale;
  };
  const average = values => values.reduce((sum, value) => sum + value, 0) / values.length;
  const state = (trigger, confirm = false) => trigger === null ? null : trigger ? 'triggered' : confirm ? 'confirming' : 'off';

  function sma(closes, count) {
    return closes.length >= count ? pyRound(average(closes.slice(-count)), 2) : null;
  }

  function rsi14(closes) {
    if (closes.length < 15) return null;
    let up = 0, down = 0;
    for (let index = closes.length - 14; index < closes.length; index += 1) {
      const move = closes[index] - closes[index - 1];
      if (move > 0) up += move; else down -= move;
    }
    return up + down === 0 ? null : pyRound(100 * up / (up + down), 1);
  }

  function atr14(bars) {
    if (bars.length < 15) return null;
    const ranges = [];
    for (let index = bars.length - 14; index < bars.length; index += 1) {
      const current = bars[index], previous = bars[index - 1];
      ranges.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
    }
    return pyRound(average(ranges), 2);
  }

  function kd9(bars) {
    let k = 50, d = 50;
    const output = [];
    for (let index = 0; index < bars.length; index += 1) {
      if (index < 8) continue;
      const window = bars.slice(index - 8, index + 1);
      const high = Math.max(...window.map(bar => bar.high));
      const low = Math.min(...window.map(bar => bar.low));
      const rsv = high === low ? 50 : (bars[index].close - low) / (high - low) * 100;
      k = k * 2 / 3 + rsv / 3;
      d = d * 2 / 3 + k / 3;
      output.push([pyRound(k, 2), pyRound(d, 2)]);
    }
    return output;
  }

  function macd(closes) {
    let fast = null, slow = null, dea = null;
    return closes.map(close => {
      fast = fast === null ? close : fast + (close - fast) * 2 / 13;
      slow = slow === null ? close : slow + (close - slow) * 2 / 27;
      const dif = fast - slow;
      dea = dea === null ? dif : dea + (dif - dea) * 2 / 10;
      return [pyRound(dif, 3), pyRound(dea, 3), pyRound(dif - dea, 3)];
    });
  }

  function indicators(bars, fallback) {
    const closes = bars.map(bar => bar.close);
    const last = bars.at(-1);
    const volumes = bars.slice(-20).map(bar => bar.volume);
    const avgVolume20 = volumes.length >= 20 ? pyRound(average(volumes)) : null;
    const atr = atr14(bars);
    const prior = bars.slice(-21, -1);
    const kd = kd9(bars);
    const m = macd(closes);
    return {
      ...Object.fromEntries([5, 10, 20, 60, 120].map(period => [`ma${period}`, sma(closes, period)])),
      ma240: sma(closes, 240) ?? fallback?.ma240 ?? null,
      rsi14: rsi14(closes), atr14: atr,
      atr_pct: atr ? pyRound(atr / last.close * 100, 2) : null,
      relative_volume: avgVolume20 ? pyRound(last.volume / Math.max(1, avgVolume20), 2) : null,
      high20_close: closes.length >= 20 ? Math.max(...closes.slice(-20)) : null,
      low20_close: closes.length >= 20 ? Math.min(...closes.slice(-20)) : null,
      prior_high20: prior.length >= 20 ? Math.max(...prior.map(bar => bar.high)) : null,
      low20_intraday: bars.length >= 20 ? Math.min(...bars.slice(-20).map(bar => bar.low)) : null,
      k9: kd.length ? kd.at(-1)[0] : null, d9: kd.length ? kd.at(-1)[1] : null,
      macd_dif: closes.length >= 34 ? m.at(-1)[0] : null,
      macd_dea: closes.length >= 34 ? m.at(-1)[1] : null,
      macd_hist: closes.length >= 34 ? m.at(-1)[2] : null,
    };
  }

  function candidates(bars, values) {
    const close = bars.at(-1).close;
    const output = [];
    const add = (kind, value, label, date = null) => {
      if (finite(value) && Number(value) > 0) output.push({ kind, value: pyRound(Number(value), 2), label, date });
    };
    for (const period of [5, 10, 20, 60, 120, 240]) add(`ma${period}`, values[`ma${period}`], `MA${period}`);
    const closes = bars.map(bar => bar.close);
    add('prior_high_20', values.prior_high20, '前 20 日盤中高點');
    if (bars.length >= 20) {
      add('high_20', Math.max(...closes.slice(-20)), '近 20 日收盤高點');
      add('low_20', Math.min(...bars.slice(-20).map(bar => bar.low)), '近 20 日盤中低點');
    }
    if (bars.length >= 60) {
      add('high_60', Math.max(...closes.slice(-60)), '近 60 日收盤高點');
      add('low_60', Math.min(...closes.slice(-60)), '近 60 日收盤低點');
      add('intraday_high_60', Math.max(...bars.slice(-60).map(bar => bar.high)), '近 60 日盤中高點');
      const window = bars.slice(-60);
      const low = Math.min(...window.map(bar => bar.close));
      const high = Math.max(...window.map(bar => bar.close));
      if (high > low) {
        const width = (high - low) / 20;
        const volumes = Array(20).fill(0);
        for (const bar of window) volumes[Math.min(19, Math.max(0, Math.floor((bar.close - low) / width)))] += bar.volume;
        let selected = 0;
        for (let index = 1; index < volumes.length; index += 1) if (volumes[index] > volumes[selected]) selected = index;
        add('vp_low', low + selected * width, '成交量分布下界');
        add('vp_center', low + (selected + .5) * width, '成交量分布密集價');
        add('vp_high', low + (selected + 1) * width, '成交量分布上界');
      }
    }
    const swing = bars.slice(-120);
    for (let index = 2; index < swing.length - 2; index += 1) {
      const current = swing[index];
      const around = [...swing.slice(index - 2, index), ...swing.slice(index + 1, index + 3)];
      if (around.every(bar => current.high >= bar.high)) add('swing_high', current.high, current.high < close * .999 ? '前波高點轉支撐' : '前波高點', current.date);
      if (around.every(bar => current.low <= bar.low)) add('swing_low', current.low, '波段低點', current.date);
    }
    for (let index = Math.max(20, bars.length - 60); index < bars.length; index += 1) {
      const priorHigh = Math.max(...bars.slice(index - 20, index).map(bar => bar.high));
      if (bars[index].high > priorHigh && bars[index].close < priorHigh) add('breakout_failure', priorHigh, '假突破價位', bars[index].date);
    }
    for (let index = 1; index < swing.length; index += 1) {
      const previous = swing[index - 1], current = swing[index], later = swing.slice(index + 1);
      if (current.low > previous.high && !later.some(bar => bar.low <= previous.high)) {
        add('gap_up_upper', current.low, '未回補向上缺口上緣', current.date);
        add('gap_up_lower', previous.high, '未回補向上缺口下緣', current.date);
      } else if (current.high < previous.low && !later.some(bar => bar.high >= previous.low)) {
        add('gap_down_upper', previous.low, '未回補向下缺口上緣', current.date);
        add('gap_down_lower', current.high, '未回補向下缺口下緣', current.date);
      }
    }
    bars.slice(-60).filter(bar => bar.volume > 0).sort((a, b) => b.volume - a.volume).slice(0, 9).forEach(bar => {
      add('heavy_volume', bar.close, '大量成交日收盤', bar.date);
      add('heavy_high', bar.high, '大量成交日高點', bar.date);
      add('heavy_low', bar.low, '大量成交日低點', bar.date);
    });
    return output;
  }

  function tickStep(price) {
    for (const [limit, step] of [[10, .01], [50, .05], [100, .1], [500, .5], [1000, 1]]) if (price < limit) return step;
    return 5;
  }
  function tickStepDown(price) {
    for (const [limit, step] of [[10, .01], [50, .05], [100, .1], [500, .5], [1000, 1]]) if (price <= limit) return step;
    return 5;
  }
  const tickFloor = price => pyRound(Math.max(.01, Math.floor((price + 1e-9) / tickStep(price)) * tickStep(price)), 2);
  const tickCeil = price => pyRound(Math.max(.01, Math.ceil((price - 1e-9) / tickStep(price)) * tickStep(price)), 2);
  const tickRound = price => price - tickFloor(price) < tickCeil(price) - price ? tickFloor(price) : tickCeil(price);

  // 漲跌停價（本人 11-18 要現價碰到時紅綠反白）：前一天收盤 ±10%，照跳動單位取到 10% 以內——漲停往下取、跌停往上取。
  // ETF 的跳動單位和股票不同：50 元以下 0.01、50 元以上 0.05。
  const etfStep = price => (price < 50 ? .01 : .05);
  function limitPrices(previousClose, { etf = false } = {}) {
    const step = etf ? etfStep : tickStep;
    const up = previousClose * 1.1, down = previousClose * .9;
    return { up: pyRound(Math.floor((up + 1e-9) / step(up)) * step(up), 2), down: pyRound(Math.ceil((down - 1e-9) / step(down)) * step(down), 2) };
  }
  // 現價碰到漲停是 'up'、跌停是 'down'，其他（含資料不全）是空字串
  function limitState(price, previousClose, options) {
    if (!finite(price) || !finite(previousClose) || previousClose <= 0) return '';
    const { up, down } = limitPrices(previousClose, options);
    return Math.abs(price - up) < 1e-6 ? 'up' : Math.abs(price - down) < 1e-6 ? 'down' : '';
  }
  const supportTick = (price, close) => pyRound(Math.max(.01, tickRound(price) >= close ? tickFloor(close - tickStepDown(close)) : tickRound(price)), 2);
  const resistanceTick = (price, close) => pyRound(tickRound(price) <= close ? tickCeil(close + tickStep(close)) : tickRound(price), 2);
  const tier = distance => Math.abs(distance) <= 10 ? 'near' : Math.abs(distance) <= 20 ? 'mid' : 'far';
  const weight = kind => kind === 'high_20' || kind === 'high_60' ? 1.1 : kind === 'ma240' ? 1 : SHARED.has(kind) ? WEIGHTS[kind] : null;

  function cluster(group, close, side) {
    const low = Math.min(...group.map(item => item.value)), high = Math.max(...group.map(item => item.value));
    const weights = group.map(item => weight(item.kind));
    const known = weights.every(value => value !== null);
    const level = side === 'resistance'
      ? pyRound(group.reduce((sum, item, index) => sum + weights[index] * item.value, 0) / weights.reduce((sum, value) => sum + value, 0), 2)
      : high;
    const families = new Set(group.map(item => item.kind.split('_')[0]));
    const strength = known ? pyRound(weights.reduce((sum, value) => sum + value, 0) + .6 * Math.max(0, families.size - 1), 2) : null;
    const distance = pyRound((level - close) / close * 100, 2);
    return { tick: side === 'resistance' ? resistanceTick(level, close) : supportTick(level, close), level,
      low: pyRound(low, 2), high: pyRound(high, 2), distance_pct: distance, tier: tier(distance),
      signal_count: group.length, structural: group.some(item => STRUCTURAL.has(item.kind)), strength_score: strength,
      sources: group.map(({ kind, label, value, date }) => ({ kind, label, value, date })) };
  }

  function priceLevels(bars, values) {
    const empty = { aggregation_tolerance_pct: null, observable_range_pct: null, resistance: [], support: [], resistance_all: [], support_all: [], no_resistance_above: false, limit_up_reference: null };
    if (!values.atr_pct) return empty;
    const close = bars.at(-1).close;
    const tolerance = pyRound(Math.min(3, Math.max(1, .6 * values.atr_pct)), 2);
    const observable = pyRound(Math.min(20, Math.max(8, 4 * values.atr_pct)), 2);
    const factors = candidates(bars, values);
    const above = factors.filter(item => SHARED.has(item.kind) && item.value > close * 1.001).sort((a, b) => a.value - b.value);
    const resistanceGroups = [];
    for (const item of above) {
      const last = resistanceGroups.at(-1);
      if (last && (item.value - last[0].value) / last[0].value * 100 <= tolerance) last.push(item);
      else resistanceGroups.push([item]);
    }
    const resistanceAll = resistanceGroups.map(group => cluster(group, close, 'resistance'));
    const below = factors.filter(item => item.value < close * .999).sort((a, b) => b.value - a.value);
    const supportGroups = [];
    for (const item of below) {
      const last = supportGroups.at(-1);
      if (last) {
        const top = last[0].value, bottom = Math.min(...last.map(entry => entry.value));
        if ((top - item.value) / top * 100 <= tolerance / 2) { last.push(item); continue; }
        if ((bottom - item.value) / bottom * 100 <= tolerance / 2) { last.push({ ...item, value: bottom }); continue; }
      }
      supportGroups.push([item]);
    }
    const supports = supportGroups.map(group => cluster(group, close, 'support'));
    const qualified = supports.filter(item => item.structural || item.signal_count >= 2);
    const supportAll = qualified.length ? qualified : supports;
    const usable = resistanceAll.filter(item => item.distance_pct <= observable && item.signal_count >= 2);
    const first = usable[0] || null;
    const second = first ? usable.slice(1).find(item => (item.level - first.level) / first.level * 100 >= 2 * tolerance) || null : null;
    return { aggregation_tolerance_pct: tolerance, observable_range_pct: observable,
      resistance: [first, second].filter(Boolean), support: supportAll.filter(item => item.tier !== 'far').slice(0, 2),
      resistance_all: resistanceAll, support_all: supportAll, no_resistance_above: above.length === 0,
      limit_up_reference: above.length ? null : { value: tickFloor(close * 1.10), label: '明日漲停參考價', note: '上方無歷史結構，依 10% 漲跌幅推算；非結構價位' } };
  }

  function setCondition(group, key, value) {
    if (group?.[key]) group[key].state = value;
  }

  function projectedConditions(original, values, bars) {
    const result = clone(original || { confirmed: {}, approximate: {} });
    const close = bars.at(-1).close, previous = bars.at(-2)?.close ?? null;
    const up = previous === null ? null : close > previous;
    let maTrend = null;
    if (values.ma5 !== null && values.ma20 !== null) {
      if (close > values.ma5 || close > values.ma20) maTrend = 'triggered';
      else if (values.ma60 !== null) maTrend = values.ma20 > values.ma60 ? 'confirming' : 'off';
    }
    const over = values.rsi14 === null && values.k9 === null ? null : state((values.rsi14 !== null && values.rsi14 >= 75) || (values.k9 !== null && values.k9 >= 85), true);
    setCondition(result.confirmed, 'ma_trend', maTrend);
    setCondition(result.confirmed, 'kd_bullish', values.k9 !== null && values.d9 !== null ? state(values.k9 > values.d9) : null);
    setCondition(result.confirmed, 'momentum_overheated', over);
    setCondition(result.confirmed, 'near_prior_high', values.high20_close !== null ? state(close >= .98 * values.high20_close) : null);
    setCondition(result.confirmed, 'volume_confirmed', up === null || values.relative_volume === null ? null : state(up && values.relative_volume > 1, true));
    setCondition(result.confirmed, 'heavy_volume_stall', values.relative_volume === null || previous === null ? null : state(values.relative_volume >= 2 && close <= previous));
    setCondition(result.approximate, 'macd_bullish', values.macd_hist !== null && values.macd_dif !== null && values.macd_dea !== null ? state(values.macd_hist > 0 && values.macd_dif > values.macd_dea) : null);
    setCondition(result.approximate, 'volume_contraction', values.relative_volume !== null ? state(values.relative_volume < .7) : null);
    return result;
  }

  // 收盤後到每日更新發布前（約 15:30／16:30）：即時服務給的今天收盤報價比日資料新，就用它（本人 11-17
  // 「我剛剛看股票卻是昨天的」）。日資料一更新到同一天就不再用；比日資料舊的快取報價也不用。
  function closeQuoteNewer(base, live) {
    const day = live?.quote?.data_date;
    return Boolean(live?.ok !== false && !live?.market_open && day && base?.as_of && day > base.as_of);
  }

  function projectAnalysis(base, live) {
    if (!base || !live?.quote || !(live.market_open || closeQuoteNewer(base, live))) return base;
    const result = clone(base);
    const quote = live.quote;
    const bar = { date: quote.data_date, open: quote.open, high: quote.high, low: quote.low, close: quote.price, volume: quote.volume_shares };
    if (!bar.date || !['open', 'high', 'low', 'close', 'volume'].every(key => finite(bar[key]))) return base;
    const bars = clone(base.bars || []);
    if (bars.at(-1)?.date === bar.date) bars[bars.length - 1] = bar; else bars.push(bar);
    const values = indicators(bars, base.indicators);
    result.as_of = quote.data_date;
    result.quote = { ...result.quote, close: quote.price, chg_pct: quote.change_percent, volume: quote.volume_shares,
      change_sign: quote.change > 0 ? '+' : quote.change < 0 ? '-' : '' };
    result.bars = bars;
    result.indicators = values;
    result.levels = priceLevels(bars, values);
    result.conditions = projectedConditions(base.conditions, values, bars);
    result.reference = { ...(result.reference || {}), low20_intraday: { ...(result.reference?.low20_intraday || {}), value: values.low20_intraday } };
    // closed：收盤後的報價；final：伺服器確認是收盤價（close_pending 時還只是收盤前最後一筆成交）。
    result._intraday = { live: Boolean(live.live), data_time: quote.data_time, environment: live.environment || null,
      closed: !live.market_open, final: !live.market_open && !live.close_pending };
    return result;
  }

  // ---- 報價卡上的字：顯示的是哪一天、哪一種價格 ----
  function taipeiParts(nowMs) {
    const local = new Date(nowMs + 8 * 60 * 60 * 1000);
    return { day: local.toISOString().slice(0, 10), weekday: local.getUTCDay(), minutes: local.getUTCHours() * 60 + local.getUTCMinutes() };
  }
  const shortDay = day => `${day.slice(5, 7)}/${day.slice(8, 10)}`;
  // 「今日收盤」「11/16 收盤」「今日最後成交」：顯示的是哪一天、哪一種價。
  const dayPrice = (day, today, word) => (day === today ? `今日${word}` : `${shortDay(day)} ${word}`);
  const liveUnavailable = payload => payload?.error === 'cloud-live-unavailable' || payload?.environment === 'cloud' && payload?.ok === false;

  // 主報價那一行（模式＋時間）與價位階梯的標籤。shown 是畫面上這一份分析（盤後原樣或已投影）。
  function quoteCaption(shown, nowMs = Date.now()) {
    const intraday = shown?._intraday;
    const stamp = intraday?.data_time ? intraday.data_time.slice(0, 16).replace('T', ' ') : shown?.as_of;
    if (intraday && !intraday.closed) return { mode: '盤中', stamp, ladderLabel: '現價', ladderMeta: '盤中更新' };
    if (intraday) {
      const word = intraday.final ? '收盤' : '最後成交';
      return { mode: dayPrice(intraday.data_time?.slice(0, 10) || shown.as_of, taipeiParts(nowMs).day, word), stamp,
        ladderLabel: word, ladderMeta: '盤後暫用即時報價' };
    }
    return { mode: '資料日', stamp: shown?.as_of, ladderLabel: '收盤', ladderMeta: '資料日收盤' };
  }

  // 價格下面那一行狀態字。收盤之後一定寫出顯示的是哪一天的收盤，不再只寫「收盤資料」。
  function liveStatusText(payload, shown, nowMs = Date.now()) {
    if (liveUnavailable(payload)) return '雲端無法取得即時資料';
    if (payload?.live && payload.quote?.data_time) return `${payload.quote.data_time.slice(11, 19)} 更新`;
    if (payload?.market_open) return '即時資料逾 30 秒，顯示最近一筆';
    const now = taipeiParts(nowMs);
    const intraday = shown?._intraday;
    const day = intraday?.data_time?.slice(0, 10) || shown?.as_of;
    const which = !day ? '收盤資料' : dayPrice(day, now.day, intraday && !intraday.final ? '最後成交' : '收盤');
    const weekday = now.weekday >= 1 && now.weekday <= 5;
    // 平日收盤後，伺服器抓到的最後報價還是前一個交易日：今天沒開市（國定假日）。
    const holiday = weekday && now.minutes >= 13 * 60 + 30 && !payload?.close_pending
      && payload?.quote?.data_date && payload.quote.data_date < now.day;
    const prefix = payload?.market_phase === 'unknown' ? '尚未取得今日報價'
      : !weekday ? '休市' : holiday ? '今日休市' : now.minutes < 9 * 60 ? '尚未開盤' : '已收盤';
    const note = !intraday?.closed ? '' : !intraday.final ? '（收盤價更新中）'
      : day === now.day ? '（日資料稍後更新）' : '（暫用即時報價）';
    return `${prefix}，顯示${which.startsWith('今日') ? '' : ' '}${which}${note}`;
  }

  function nextPollDelay(payload) {
    const retry = Number(payload?.retry_after_seconds);
    if (!payload?.ok && Number.isFinite(retry) && retry > 0) return Math.max(5_000, retry * 1000);
    if (payload?.market_phase === 'unknown') return 15_000;
    // 收盤後還沒拿到收盤價（撮合結果未出或上游失敗）：照退避再問，拿到就停。
    if (payload?.close_pending) return Math.max(15_000, Number.isFinite(retry) ? retry * 1000 : 0);
    if (payload?.error === 'cloud-live-unavailable' || payload?.market_open === false) return null;
    if (!payload?.ok) return 15_000;
    const quote = Number(payload.delay_seconds?.quote);
    return Math.max(5_000, Number.isFinite(quote) ? quote * 1000 : 5_000);
  }

  function createPoller({ fetchImpl = fetch, onData, onError = () => {}, timer = setTimeout, clear = clearTimeout } = {}) {
    let handle = null, controller = null, activeCode = null, activeMarket = 'tse';
    const stop = () => { if (handle !== null) clear(handle); handle = null; if (controller) controller.abort(); controller = null; activeCode = null; activeMarket = 'tse'; };
    const poll = async () => {
      const code = activeCode;
      controller = new AbortController();
      try {
        const params = new URLSearchParams({ market: activeMarket });
        const response = await fetchImpl(`/api/stocks/${encodeURIComponent(code)}/live?${params}`, { cache: 'no-store', signal: controller.signal });
        const payload = await response.json();
        if (code !== activeCode) return;
        onData(payload, response);
        const delay = nextPollDelay(payload);
        if (delay !== null) handle = timer(poll, delay);
      } catch (error) {
        if (error.name !== 'AbortError' && code === activeCode) { onError(error); handle = timer(poll, 15_000); }
      }
    };
    return { start(code, market) { stop(); activeCode = code; activeMarket = market === '上櫃' || market === 'otc' ? 'otc' : 'tse'; poll(); }, stop };
  }

  // 秒 K：把當天逐筆成交（[毫秒, 價格, 張數, 流水號]，由舊到新）依 seconds 秒一格彙總。
  // 沒有成交的格子不補 K 棒；成交量換成股（與日 K 相同單位）。
  function secondCandles(ticks, seconds) {
    const span = seconds * 1000;
    const candles = [];
    let last = null;
    for (const [ms, price, size] of ticks) {
      const bucket = Math.floor(ms / span) * span;
      if (!last || last.bucket !== bucket) {
        const local = new Date(bucket + 8 * 60 * 60 * 1000).toISOString();  // 台北時間（UTC+8，無日光節約）
        last = { bucket, t: `${local.slice(0, 19)}+08:00`, label: local.slice(11, 19), open: price, high: price, low: price, close: price, volume: 0 };
        candles.push(last);
      }
      if (price > last.high) last.high = price;
      if (price < last.low) last.low = price;
      last.close = price;
      last.volume += size * 1000;
    }
    return candles;
  }

  window.Intraday = { createPoller, nextPollDelay, projectAnalysis, quoteCaption, liveStatusText, liveUnavailable, secondCandles, limitPrices, limitState, _test: { indicators, priceLevels, projectedConditions } };
})();
