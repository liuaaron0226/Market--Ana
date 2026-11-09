/* 個股分析頁：只向後端 API 取個股分析契約 v2 並畫圖，不在前端算價位。每檔一份分析，沒有方案與總分。 */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const fmt = n => n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const SCHEMA = 'stock-analysis/v2';
  const TIER = { near: '近端（10% 內）', mid: '中距（10～20%）', far: '遠端（20% 以上）' };
  // 本人 2026-11-09：「不要放這種{條件文字取自參考站，判斷門檻依會員樣本反推，逐條附吻合率}敘述在我的正式網站頁面上」。
  // 研究過程（樣本、吻合率、判斷式、待反推）只留在 API 與內部文件；畫面只放整理後的狀態與價位。
  // 成交活絡股（本站母體）幾乎都處於同一狀態的條件，只收在明細最後，不進結論。
  const FIXED_CONDITIONS = [
    ['趨勢型態與均線位置', '趨勢型態分類與 MA20 斜率；跌破 MA5／MA20 視為結構轉弱', 'triggered'],
    ['成交量分布結構', '5 日均量與 20 日均量比值變化；成交量密集區與大量成交套牢區', 'triggered'],
    ['法人買超強度', '當日法人買賣超占均量比重，以及近 5／10／20 日累計', 'triggered'],
    ['法人連續買賣', '外資／投信／自營商連續買超或賣超日數', 'triggered'],
    ['營收成長動能', '月營收年增、月增與累計年增同步成長', 'triggered'],
    ['高檔爆量竭盡', '偏離 MA20 過大且連續上漲後爆量', 'off'],
    ['獲利能力', '每股盈餘表現', 'off'],
  ];
  const STATE_TEXT = { triggered: '成立', confirming: '確認中', off: '未成立' };
  const INDICATORS = [
    ['ma5', 'MA5'], ['ma10', 'MA10'], ['ma20', 'MA20'], ['ma60', 'MA60'], ['ma120', 'MA120'], ['ma240', 'MA240'],
    ['rsi14', 'RSI14'], ['k9', 'K9'], ['d9', 'D9'], ['atr14', 'ATR14'], ['atr_pct', 'ATR%'], ['relative_volume', '量比'],
    ['high20_close', '近 20 日收盤高'], ['low20_close', '近 20 日收盤低'], ['prior_high20', '前 20 日盤中高'],
    ['low20_intraday', '近 20 日盤中低'], ['macd_hist', 'MACD 柱'], ['observable_range_pct', '可觀察範圍 %'],
  ];
  const STOCK_STATE_MESSAGE = {
    not_found: code => `${code}：查無此股。`,
    missing_data: code => `${code}：缺少分析資料，暫不顯示數值。`,
    suspended: code => `${code}：目前為停牌或行情中斷狀態，暫不顯示過期分析。`,
    just_listed: code => `${code}：剛上市，歷史資料尚不足，暫不顯示分析數值。`,
    stale: (code, dataDate = '未提供') => `${code}：資料至 ${dataDate}，已過期。以下內容只供查看舊資料，不作今日判斷。`,
  };
  // ---------- 操作指引（本人 11-09：「並沒有整理，並且做出清楚明了的操作手段指引」） ----------
  // 只用「主要訊號」（契約 conditions.confirmed）與已確認的價位規則（支撐、壓力、近 20 日盤中低點）。
  // 不另訂分數或門檻；資料缺就不說。語氣是「觀察／若…代表…」，不寫成買進或賣出指令。
  const pct = value => `${value > 0 ? '+' : ''}${Number(value).toFixed(2)}%`;

  function structureText(state) {
    if (state === 'triggered') return '站上短期均線';
    if (state === 'confirming') return '短線跌破均線、中期均線仍向上排列';
    if (state === 'off') return '跌破短中期均線，結構偏弱';
    return null;
  }

  function buildGuide(r) {
    const s = Object.fromEntries(Object.entries((r.conditions || {}).confirmed || {}).map(([k, v]) => [k, v.state]));
    const lv = r.levels || {};
    const close = r.quote && r.quote.close;
    const res1 = (lv.resistance || [])[0];
    const sup1 = (lv.support || [])[0];
    const low20 = r.indicators && r.indicators.low20_intraday;
    const distance = value => (Number.isFinite(close) && close > 0 ? (value - close) / close * 100 : null);

    // 一句話結論：結構＋最多兩個主要狀態＋目前位置（距壓力、支撐哪邊近，純比較、不設門檻）。
    const parts = [];
    const structure = structureText(s.ma_trend);
    parts.push(structure || '資料不足以判斷均線結構');
    if (s.momentum_overheated === 'triggered') parts.push('短線過熱');
    if (s.heavy_volume_stall === 'triggered') parts.push('爆量但價格沒有推升');
    else if (s.volume_confirmed === 'triggered') parts.push('上漲且量能放大');
    if (s.near_prior_high === 'triggered') parts.push('逼近前波高點');
    if (lv.limit_up_reference) parts.push('創近期新高，上方沒有歷史壓力');
    else if (res1 && sup1) parts.push(Math.abs(res1.distance_pct) <= Math.abs(sup1.distance_pct) ? '距壓力較近' : '距支撐較近');
    const conclusion = parts.slice(0, 4).join('、');

    const items = [];
    if (lv.limit_up_reference) {
      items.push({ key: 'resistance', title: '上方', text: `創近期新高，上方沒有歷史壓力；明日漲停參考價 ${fmt(lv.limit_up_reference.value)}，不是目標價。` });
    } else if (res1) {
      items.push({ key: 'resistance', title: '上方壓力', text: `第一道壓力 ${fmt(res1.tick)}（${pct(res1.distance_pct)}）。若放量站上，代表上方空間打開；站上前，留意在此遇到賣壓。` });
    } else if (lv.no_resistance_above) {
      items.push({ key: 'resistance', title: '上方', text: '可觀察範圍內沒有結構壓力。' });
    }
    if (sup1) {
      const weak = s.ma_trend === 'off';
      items.push({ key: 'support', title: '回檔觀察', text: `支撐一 ${fmt(sup1.tick)}（${pct(sup1.distance_pct)}）。${weak ? '結構偏弱時，先看這裡能否止跌，止跌前不急著進場。' : '若要找進場位置，觀察回檔到這裡是否守住。'}` });
    }
    if (Number.isFinite(low20)) {
      const d = distance(low20);
      items.push({ key: 'weaken', title: '轉弱訊號', text: `跌破近 20 日低點 ${fmt(low20)}${d === null ? '' : `（${pct(d)}）`}，代表這波結構轉弱。` });
    }
    const risks = [];
    if (s.momentum_overheated === 'triggered') risks.push('RSI 或 KD 位在高檔，追價風險較高');
    if (s.heavy_volume_stall === 'triggered') risks.push('爆量但收平或收黑，留意上方賣壓');
    if (s.revenue_decline === 'triggered') risks.push('月營收年減超過兩成');
    if (s.securities_lending_sale === 'triggered') risks.push('前一交易日有借券放空');
    if (s.ma_trend === 'off') risks.push('股價在短中期均線之下');
    items.push({ key: 'risk', title: '留意', text: risks.length ? `${risks.join('；')}。` : '目前沒有主要風險訊號。' });
    return { conclusion, items: items.slice(0, 4) };
  }

  function renderGuide(r) {
    const guide = buildGuide(r);
    $('guideConclusion').textContent = guide.conclusion;
    $('guideList').replaceChildren(...guide.items.map(item => {
      const li = node('li', `bd-guide-item is-${item.key}`);
      li.dataset.key = item.key;
      li.append(node('b', '', item.title), node('span', '', item.text));
      return li;
    }));
  }

  window.StockGuide = { build: buildGuide };

  let chart = null;
  let lastChartKey = '';
  let lastResult = null;
  let requestSequence = 0;
  let pendingRequest = null;
  let chartPeriod = 'daily';

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('market-theme', theme); } catch (_) { /* 私密視窗可能無法存 */ }
    $('themeText').textContent = theme === 'dark' ? '淺色' : '深色';
    if (lastResult) drawChart(lastResult);
  }

  function codeFromUrl() {
    const code = new URLSearchParams(location.search).get('code');
    return code && /^[0-9A-Z]{4,6}$/.test(code) ? code : null;
  }

  function updateUrl(code) {
    const url = new URL(location.href);
    url.searchParams.set('code', code);
    url.searchParams.delete('preset');
    if (url.href !== location.href) history.pushState(null, '', url);
  }

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function levelMeta(cluster) {
    if (!cluster) return '無結構價位';
    const sign = cluster.distance_pct > 0 ? '+' : '';
    return `${sign}${fmt(cluster.distance_pct)}%・${TIER[cluster.tier] || cluster.tier}`;
  }

  function setLevel(id, cluster, emptyText) {
    $(id).textContent = cluster ? fmt(cluster.tick) : emptyText;
    $(`${id}Meta`).textContent = cluster ? levelMeta(cluster) : '';
  }

  // 價位階梯：壓力二／壓力一／收盤／支撐一／支撐二由上而下，列距依「與收盤相差幾 %」拉開，讓遠近一眼可比。
  function layoutLadder(close, res, sup) {
    $('ladderClose').textContent = fmt(close);
    const items = { resistance2: res[1], resistance1: res[0], close: { tick: close }, support1: sup[0], support2: sup[1] };
    const rows = [...document.querySelectorAll('#ladder > li')];
    let prev = null;
    rows.forEach((li, i) => {
      if (!li.classList.contains('lad')) return;
      const item = items[li.dataset.key];
      // 壓力一沒有結構價位時仍顯示（寫「上方無結構價位」），其他缺的就整列收起。
      const keep = item || li.dataset.key === 'resistance1';
      li.hidden = !keep;
      li.classList.toggle('is-empty', !item);
      if (!keep) return;
      if (prev && item) {
        const pct = Math.abs(prev.tick - item.tick) / close * 100;
        rows[i - 1].style.setProperty('--gap', `${Math.round(Math.min(96, Math.max(8, pct * 8)))}px`);
      } else if (i > 0) rows[i - 1].style.setProperty('--gap', '8px');
      prev = item || prev;
    });
    rows.forEach((li, i) => {
      if (li.classList.contains('lad-gap')) li.hidden = !!(rows[i - 1]?.hidden || rows[i + 1]?.hidden);
    });
  }

  function render(r) {
    document.title = `${r.name} ${r.code}｜個股分析`;
    $('content').dataset.schema = r.schema;
    lastResult = r;
    $('stockName').textContent = r.name;
    $('stockCode').textContent = r.code;
    showMarket(r);
    showCandidateReasons(r.code);
    $('asOf').textContent = r.as_of;
    $('close').textContent = fmt(r.quote.close);
    const chg = r.quote.chg_pct;
    $('chg').textContent = chg === null || chg === undefined ? '' : `${chg > 0 ? '+' : ''}${Number(chg).toFixed(2)}%`;
    $('chg').className = `num ${chg > 0 ? 'up' : chg < 0 ? 'down' : ''}`;

    const lv = r.levels || {};
    const res = lv.resistance || [];
    const sup = lv.support || [];
    const noRes = lv.no_resistance_above ? '上方無結構價位' : '無';
    setLevel('resistance1', res[0], noRes);
    setLevel('resistance2', res[1], '無');
    setLevel('support1', sup[0], '無');
    setLevel('support2', sup[1], '無');
    layoutLadder(r.quote.close, lv.limit_up_reference ? [{ tick: lv.limit_up_reference.value }, res[1]] : res, sup);

    const ind = { ...(r.indicators || {}), observable_range_pct: lv.observable_range_pct };
    $('indicators').replaceChildren(...INDICATORS.map(([key, label]) => {
      const box = node('div', 'sk-ind');
      box.append(node('span', '', label), node('b', 'num', fmt(ind[key])));
      return box;
    }));
    if (lv.limit_up_reference) {
      $('resistance1').textContent = fmt(lv.limit_up_reference.value);
      $('resistance1Meta').textContent = lv.limit_up_reference.label;
    }

    renderGuide(r);
    renderConditions(r.conditions || {});

    $('toleranceLine').textContent = lv.aggregation_tolerance_pct === null || lv.aggregation_tolerance_pct === undefined
      ? 'ATR% 資料不足，暫不計算價位聚合。'
      : `落在 ${fmt(lv.aggregation_tolerance_pct)}% 波動容差內的相鄰結構合併為同一價格帶；價位依台股升降單位取整。支撐近端 0～10%、中距 10～20%、超過 20% 為遠端。`;
    const rows = [...(lv.resistance_all || []).map(c => ['壓力', c]), ...(lv.support_all || []).map(c => ['支撐', c])];
    $('levelRows').replaceChildren(...rows.map(([type, c]) => {
      const tr = document.createElement('tr');
      const sign = c.distance_pct > 0 ? '+' : '';
      [type, fmt(c.tick), `${sign}${fmt(c.distance_pct)}%`, TIER[c.tier] || c.tier, (c.sources || []).map(s => s.label).join('、')]
        .forEach(text => tr.append(node('td', '', text)));
      return tr;
    }));
    const ref = r.reference && r.reference.low20_intraday;
    $('referenceLine').textContent = ref ? `${ref.label} ${fmt(ref.value)}：跌破代表這波結構轉弱。` : '';

    $('notes').replaceChildren(...[
      `${r.labels && r.labels.not_advice ? r.labels.not_advice : '分析數字是參考，不是買賣指令'}；支撐、壓力只描述價格所在位置。`,
      r.labels && r.labels.price_basis ? `${r.labels.price_basis}，除權息前後的均線會有落差。` : '價格未還原權值，除權息前後的均線會有落差。',
    ].map(t => node('li', '', t)));

    if (r.stock_state === 'stale' || r.analysis_status === 'stale') {
      $('status').hidden = false;
      $('status').dataset.stockState = 'stale';
      $('status').textContent = STOCK_STATE_MESSAGE.stale(r.code, r.data_date || r.as_of);
    } else {
      $('status').hidden = true;
      delete $('status').dataset.stockState;
    }
    $('content').hidden = false;
    drawChart(r);
  }

  // 訊號明細（預設收合）：每條只放名稱、狀態、一句白話；判斷式與準確度留在 API。
  function conditionItem(name, definition, state, tag) {
    const li = node('li', '');
    const head = node('div', 'sk-cond-head');
    const badge = node('b', `sk-cond-state ${state ? `is-${state}` : 'is-null'}`, state ? STATE_TEXT[state] || state : '資料不足');
    head.append(node('span', '', name), badge);
    li.append(head, node('p', 'sk-cond-def', definition));
    if (tag) li.dataset.group = tag;
    return li;
  }

  function renderConditions(c) {
    const confirmed = Object.values(c.confirmed || {});
    const approximate = Object.values(c.approximate || {});
    $('condConfirmed').replaceChildren(...confirmed.map(v => conditionItem(v.name, v.definition, v.state, 'confirmed')));
    $('condApproximate').replaceChildren(...approximate.map(v => conditionItem(v.name, v.definition, v.state, 'approximate')));
    $('condFixed').replaceChildren(...FIXED_CONDITIONS.map(([name, definition, state]) => conditionItem(name, definition, state, 'fixed')));
    const on = confirmed.filter(v => v.state === 'triggered').length;
    $('signalSummary').textContent = `主要訊號 ${confirmed.length} 項，成立 ${on} 項`;
  }

  function showOutside(data) {
    $('outside').hidden = false;
    $('outsideTitle').textContent = `${data.name || data.code} ${data.code}：不在本站涵蓋範圍`;
    $('outsideReason').textContent = data.message || '不在本站涵蓋範圍';
    $('outsideMeta').textContent = [data.market, data.universe_rank ? `成交金額排名第 ${data.universe_rank} 名` : '',
      data.data_date ? `資料日 ${data.data_date}` : ''].filter(Boolean).join('・');
    $('status').hidden = true;
  }

  function monthlyCandles(rows) {
    const grouped = [];
    rows.forEach(row => {
      const month = row.date.slice(0, 7);
      let bar = grouped[grouped.length - 1];
      if (!bar || bar.month !== month) {
        grouped.push({ month, t: row.date, label: month, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume });
        return;
      }
      bar.t = row.date;
      bar.high = Math.max(bar.high, row.high);
      bar.low = Math.min(bar.low, row.low);
      bar.close = row.close;
      bar.volume = Number.isFinite(bar.volume) && Number.isFinite(row.volume) ? bar.volume + row.volume : null;
    });
    return grouped;
  }

  function drawChart(r) {
    const daily = r.bars.map(b => ({ t: b.date, label: b.date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));
    const candles = chartPeriod === 'monthly' ? monthlyCandles(r.bars) : daily;
    const overlays = [];
    const lv = r.levels || {};
    (lv.resistance || []).forEach((c, i) => overlays.push({ value: c.tick, label: `壓力${i ? '二' : '一'} ${fmt(c.tick)}`, short: '壓力', color: css('--gold', '#94681a'), pos: 'above', align: i ? 'left' : 'right' }));
    (lv.support || []).forEach((c, i) => overlays.push({ value: c.tick, label: `支撐${i ? '二' : '一'} ${fmt(c.tick)}`, short: '支撐', color: css('--down', '#0f8058'), pos: 'below', align: i ? 'left' : 'right' }));
    const key = `${r.code}:${r.as_of}:${chartPeriod}`;
    const savedView = chart && key === lastChartKey ? { ...chart.view } : null;
    if (!chart) chart = new window.KChart($('chartHost'), { macd: false, studyHost: $('chartStudies'), studies: ['volume', 'macd', 'kd', 'rsi'], selectionRoots: [$('chartHost').parentElement], volumeKind: 'volume', overlayLabels: 'tags', height: w => Math.max(300, Math.min(460, w * 0.52)) });
    const expected = { fast: 12, slow: 26, signal: 9, histogram_multiplier: 1, seed: 'first_close', warmup_bars: 34,
      precision: 3, missing_policy: 'omit_missing_price_rows', version: 'macd-first-close-v1' };
    const compatible = chartPeriod === 'daily' && r.chart && Object.entries(expected).every(([key, value]) => r.chart.parameters?.[key] === value)
      && Array.isArray(r.chart.macd) && r.chart.macd.length === daily.length
      && r.chart.macd.every((row, i) => row.date === daily[i].t);
    const unit = chartPeriod === 'monthly' ? '月' : '日';
    chart.setData(candles, { count: Math.min(candles.length, innerWidth < 600 ? 50 : 90), overlays, unit,
      macd: chartPeriod === 'monthly' ? null : compatible ? r.chart.macd : [], macdNotice: compatible || chartPeriod === 'monthly' ? '' : 'MACD版本或日期不符，暫不顯示' });
    if (savedView) { chart.view = savedView; chart.clamp(); chart.draw(); }
    lastChartKey = key;
    $('stockChartContext').textContent = chartPeriod === 'monthly'
      ? `月K · ${candles.length} 根 · 涵蓋 ${daily[0].t}～${daily[daily.length - 1].t}`
      : `日K · ${candles.length} 根 · 資料截至 ${r.as_of}`;
    const notice = $('stockChartNotice');
    notice.hidden = chartPeriod !== 'monthly';
    notice.textContent = chartPeriod === 'monthly'
      ? `僅以目前 ${daily.length} 根日K按曆月彙整；首月可能不完整，最末月只計至資料日、尚未收定。圖上價位仍是 ${r.as_of} 的分析結果。`
      : '';
    chart.canvas.setAttribute('aria-label', `${r.name}${unit}K，${candles[0].label}至${candles[candles.length - 1].label}，含支撐與壓力參考線。`);
  }

  // 契約 v2 本身帶市場別，不必再打一次搜尋 API。
  function showMarket(r) {
    const badge = $('stockMarket');
    badge.hidden = !r.market;
    badge.textContent = r.market || '';
  }

  // G3：此股若出現在已封存的籌碼選股名單中，顯示在哪些分頁、第幾名，以及所屬產業的資金狀態；不在名單就不顯示。
  const SIDE_TEXT = { buy: '淨買超', sell: '淨賣超' };

  function reasonEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 同一分頁、同一方向合併成一行：例如「外資淨買超排行・淨賣超：當日第 2 名、3 日第 3 名」。
  function membershipLines(memberships) {
    const groups = new Map();
    memberships.forEach(m => {
      const streak = m.streak_days !== null && m.streak_days !== undefined;
      const key = `${m.key}|${m.side}`;
      if (!groups.has(key)) groups.set(key, { title: `${m.display_name}・${streak ? '連續' : ''}${SIDE_TEXT[m.side]}`, parts: [] });
      groups.get(key).parts.push(streak ? `第 ${m.rank} 名（${m.streak_days} 天）` : `${m.days === 1 ? '當日' : `${m.days} 日`}第 ${m.rank} 名`);
    });
    return [...groups.values()].map(g => `${g.title}：${g.parts.join('、')}`);
  }

  async function showCandidateReasons(code) {
    const host = $('candidateReasons');
    if (!host || host.dataset.code === code) return;
    host.dataset.code = code;
    host.replaceChildren();
    try {
      const response = await fetch(`/api/screens/membership?${new URLSearchParams({ code })}`, { cache: 'no-store' });
      const data = response.ok ? await response.json() : null;
      if (!data || host.dataset.code !== code || !Array.isArray(data.memberships) || data.memberships.length === 0) return;
      const section = reasonEl('section', 'sk-card sk-reason sk-membership');
      const head = reasonEl('div', 'sk-reason-head');
      head.append(reasonEl('h2', '', '出現在籌碼選股名單'), reasonEl('span', 'sk-reason-meta', `資料日 ${data.data_date}`));
      section.append(head);
      const ul = reasonEl('ul', 'sk-reason-invalid');
      membershipLines(data.memberships).forEach(text => ul.append(reasonEl('li', '', text)));
      section.append(ul);
      if (data.industry) {
        section.append(reasonEl('p', 'sk-reason-judgment',
          `所屬產業：${data.industry.name}（${data.industry.label}，資料日 ${data.industry.date}；產業＝官方產業別）`));
      }
      section.append(reasonEl('p', 'sk-reason-meta', '排名依法人買賣超與連續天數計算，是資金動向的觀察，不是買賣建議。'));
      host.replaceChildren(section);
    } catch (_) { /* 名單讀不到就不顯示，不影響分析 */ }
  }

  async function load(code) {
    const sequence = ++requestSequence;
    if (pendingRequest) pendingRequest.abort();
    pendingRequest = new AbortController();
    $('content').hidden = true;
    $('outside').hidden = true;
    $('content').setAttribute('aria-busy', 'true');
    $('status').hidden = false;
    delete $('status').dataset.stockState;
    $('status').textContent = `讀取 ${code}`;
    $('codeInput').value = code;
    if (location.protocol === 'file:') {
      $('content').setAttribute('aria-busy', 'false');
      $('status').textContent = '請從本機服務開啟（http://127.0.0.1:8876/stock.html），個股分析需要後端計算。';
      return;
    }
    const params = new URLSearchParams();
    const asOf = new URLSearchParams(location.search).get('as_of');
    if (asOf) params.set('as_of', asOf);
    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(code)}/analysis?${params}`, { cache: 'no-store', signal: pendingRequest.signal });
      const data = await response.json();
      if (sequence !== requestSequence) return;
      if (data.error === 'outside-universe') {
        // 和「資料尚未就緒」分開：不畫 K 線、不顯示任何分析數字。
        showOutside(data);
        return;
      }
      if (!response.ok || data.error || (data.qa && data.qa.blocking)) {
        const stockState = data.stock_state || '';
        $('status').dataset.stockState = stockState;
        $('status').textContent = STOCK_STATE_MESSAGE[stockState]
          ? STOCK_STATE_MESSAGE[stockState](code)
          : data.qa && data.qa.reason ? `${code}：${data.qa.reason}，不給結論。` : `${code}：${data.message || '查不到資料'}`;
        return;
      }
      if (data.schema !== SCHEMA) {
        $('status').textContent = '本機服務尚未載入個股分析新版，請更新服務後再使用。';
        return;
      }
      render(data);
    } catch (error) {
      if (sequence !== requestSequence || error.name === 'AbortError') return;
      $('status').textContent = '本機服務沒有回應，請稍後再試。';
    } finally {
      if (sequence === requestSequence) $('content').setAttribute('aria-busy', 'false');
    }
  }

  $('searchForm').addEventListener('submit', event => {
    event.preventDefault();
    const code = $('codeInput').value.trim().toUpperCase();
    if (!/^[0-9A-Z]{4,6}$/.test(code)) { $('status').hidden = false; $('status').textContent = '代號格式不正確。'; return; }
    updateUrl(code);
    load(code);
  });
  $('periodSelect').addEventListener('change', () => {
    chartPeriod = $('periodSelect').value === 'monthly' ? 'monthly' : 'daily';
    if (lastResult) drawChart(lastResult);
  });
  window.addEventListener('popstate', () => load(codeFromUrl() || '2330'));
  $('verdictButton').addEventListener('click', () => {
    const open = $('layer1').hidden;
    $('layer1').hidden = !open;
    $('verdictButton').setAttribute('aria-expanded', String(open));
    if (open) $('layer1').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  });
  $('themeToggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  let saved = null;
  try { saved = localStorage.getItem('market-theme'); } catch (_) { /* 私密視窗 */ }
  setTheme(saved === 'dark' ? 'dark' : 'light');
  load(codeFromUrl() || '2330');
})();
