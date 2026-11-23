/* 盤勢解讀改版（本人 2026-11-23「共識達成」；樣品 design-previews/plain-top3/ 的 ?l=b；.scratch/market-brief 票 02）。
   首頁「盤勢解讀」那張卡點開之後：一句結論 → 一排類別鍵（網站現成的 .chip-seg，一次只看一類）→ 那一類的一句判斷、圖、影響／觀察／因應。
   類別的名稱與順序在這裡定（國際股市 → 市場消息 → 籌碼面 → 技術面 → 追蹤事項）；那一班有哪幾類就出現哪幾顆鍵，不認得的類別略過。
   圖的數字不由模型寫：籌碼面、技術面用首頁已經有的序列畫（外資每日買賣超、外資期貨淨部位、加權指數日線），只畫到那一類的資料日為止；
   國際股市用產生器算好放在那一類裡的漲跌幅（票 03）；市場消息只有文字（票 04）。
   數字直接標在圖上、不放圖例，紅漲綠跌照網站的 --up／--down。需要的數字不夠就不畫。
   追蹤事項（票 05）不在那一份裡：站主的 /api/impact-watch 有項目時，這裡在最後多一顆鍵，內容直接用那些項目畫（價位刻度＋因應）。
   開合、卡片上的那一句與沒有資料時退回原本的解讀都在 minimal.js；這裡只有讀資料與組畫面。資料：/api/market-brief、/api/impact-watch。 */
(() => {
  'use strict';
  const CATEGORIES = [['overseas', '國際股市'], ['news', '市場消息'], ['chips', '籌碼面'], ['tech', '技術面'], ['tracking', '追蹤事項']];
  const W = 340;                 // 圖的座標寬（實際寬度跟著面板縮放）
  const FOREIGN_DAYS = 20, FUTURES_DAYS = 20, WEEK = 5;
  const INDEX_DAYS = 40, MA = 20;
  const TRACKING = 'tracking', TRACK_MAX = 5;

  const esc = text => String(text ?? '').replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
  const filled = value => typeof value === 'string' && value.trim() !== '';
  const num = value => Math.round(value).toLocaleString('en-US');
  const monthDay = date => `${date.slice(5, 7)}/${date.slice(8, 10)}`;
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  // 這一欄有數字、而且不晚於那一類的資料日的那幾天（白天的班用前一交易日的籌碼，圖不能畫到今天）
  const upTo = (rows, field, dataDate) => (Array.isArray(rows) ? rows : []).filter(row => row && /^\d{4}-\d{2}-\d{2}/.test(row.date || '')
    && row[field] !== null && row[field] !== '' && Number.isFinite(Number(row[field])) && (!filled(dataDate) || row.date.slice(0, 10) <= dataDate));
  const svg = (name, height, inner, label) => `<svg class="plain-chart" data-chart="${name}" viewBox="0 0 ${W} ${height.toFixed(0)}" role="img" aria-label="${esc(label)}">${inner}</svg>`;
  const path = (values, x, y) => values.map((value, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(value).toFixed(1)}`).join('');
  // 圖上一段字大約多寬（12.5px 的粗字：中文一格、數字與符號半格多一點），用來算它會蓋到幾根直條
  const textWidth = text => [...text].reduce((sum, ch) => sum + (/[⺀-￿]/.test(ch) ? 12.5 : ch === ' ' || ch === ',' ? 3.6 : 7.2), 0);

  // 外資每日買賣超：最近 20 天的直條。最後連續同一個方向的那幾根實心、其餘淡色，上面標「連 N 天賣／買」，最後一根標金額。
  function foreignBars(series, dataDate) {
    const rows = upTo(series, 'net_billion', dataDate).slice(-FOREIGN_DAYS);
    if (rows.length < 2) return '';
    const values = rows.map(row => Number(row.net_billion)), last = values.at(-1), selling = last < 0;
    let streak = 0;
    if (last !== 0) for (let i = values.length - 1; i >= 0 && values[i] !== 0 && (values[i] < 0) === selling; i -= 1) streak += 1;
    const top = 26, bottom = 110;
    const most = Math.max(0, ...values), least = Math.min(0, ...values);
    // 「連 N 天」標在零軸的另一側（連賣標在上面、連買標在下面）：那一側至少留兩成的高度放字
    const hi = selling ? Math.max(most, -least * 0.2) : most, lo = selling ? least : Math.min(least, -most * 0.2);
    const y = value => top + (hi - value) / ((hi - lo) || 1) * (bottom - top), zero = y(0);
    const step = (W - 8) / rows.length, bw = step - 4;
    const bars = values.map((value, i) => `<rect x="${(4 + i * step).toFixed(1)}" y="${Math.min(zero, y(value)).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1.5, Math.abs(y(value) - zero)).toFixed(1)}" rx="2" fill="var(--${value < 0 ? 'down' : 'up'})" opacity="${i >= values.length - streak ? 1 : 0.32}"/>`).join('');
    const end = 4 + (rows.length - 1) * step + bw, start = 4 + (rows.length - streak) * step;
    const side = selling ? '賣' : '買';
    let below = 0;   // 畫在直條下面的字最低到哪裡；日期那一行要讓開
    let mark = '';
    if (streak >= 2) {
      const text = `連 ${streak} 天${side}`, at = clamp((start + end) / 2, textWidth(text) / 2, W - textWidth(text) / 2);
      const base = selling ? Math.min(zero - 18, y(most) - 5) : Math.max(zero + 27, y(least) + 14);
      if (!selling) below = base;
      mark = `<path d="M${start.toFixed(1)} ${(zero + (selling ? -9 : 9)).toFixed(1)} v${selling ? -5 : 5} H${end.toFixed(1)} v${selling ? 5 : -5}" class="bracket"/>`
        + `<text x="${at.toFixed(1)}" y="${base.toFixed(1)}" text-anchor="middle" class="t-strong">${text}</text>`;
    }
    // 最後一根的金額靠右標在直條的外側；字比一根直條寬，所以讓開它底下那幾根裡最長的
    const amount = `${side} ${num(Math.abs(last))}`, under = values.slice(-Math.max(1, Math.ceil(textWidth(amount) / step)));
    const amountY = selling ? y(Math.min(...under)) + 14 : y(Math.max(...under)) - 5;
    if (selling) below = amountY;
    const dateY = Math.max(128, below + 16), height = dateY + 4;
    const first = monthDay(rows[0].date), latest = monthDay(rows.at(-1).date);
    const label = `外資每日買賣超，${first} 到 ${latest}：${latest} ${side}超 ${num(Math.abs(last))} 億${streak >= 2 ? `，連 ${streak} 天${side}超` : ''}`;
    return svg('foreign', height, `<text x="4" y="12" class="t-title">外資每日買賣超（億）</text>`
      + `<line x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}" class="axis"/>${bars}${mark}`
      + `<text x="${end.toFixed(1)}" y="${amountY.toFixed(1)}" text-anchor="end" class="t-strong">${amount}</text>`
      + `<text x="4" y="${dateY.toFixed(0)}" class="t-dim">${first}</text><text x="${W - 2}" y="${dateY.toFixed(0)}" text-anchor="end" class="t-dim">${latest}</text>`, label);
  }

  // 外資期貨淨空單：一條線，尾端標口數與一週（5 個交易日）的增減。最新一天是淨多單時改畫淨多單。
  function futuresLine(series, dataDate) {
    const rows = upTo(series, 'net_contracts', dataDate).slice(-FUTURES_DAYS);
    if (rows.length < 2) return '';
    const short = Number(rows.at(-1).net_contracts) <= 0, tone = short ? 'down' : 'up';
    const values = rows.map(row => (short ? -1 : 1) * Number(row.net_contracts)), last = values.at(-1);
    const height = 84, top = 30, bottom = 66;
    const least = Math.min(...values), most = Math.max(...values), pad = ((most - least) || Math.abs(most) || 1) * 0.06;
    const lo = least - pad, hi = most + pad;
    const x = i => 6 + i * (W - 76) / (values.length - 1), y = value => bottom - (value - lo) / (hi - lo) * (bottom - top);
    const name = `外資期貨淨${short ? '空' : '多'}單`;
    const change = values.length > WEEK ? last - values.at(-1 - WEEK) : null;
    const week = change === null ? '' : change === 0 ? '週持平' : `週${change > 0 ? '增' : '減'} ${num(Math.abs(change))}`;
    const label = `${name}：${monthDay(rows.at(-1).date)} ${num(last)} 口`
      + (change === null ? '' : change === 0 ? '，與一週前持平' : `，較一週前${change > 0 ? '增加' : '減少'} ${num(Math.abs(change))} 口`);
    return svg('futures', height, `<text x="4" y="12" class="t-title">${name}（口）</text>`
      + `<path d="${path(values, x, y)}" class="line-${tone}"/>`
      + `<circle cx="${x(values.length - 1).toFixed(1)}" cy="${y(last).toFixed(1)}" r="4" fill="var(--${tone})"/>`
      + `<text x="${W - 2}" y="${(y(last) + 1).toFixed(1)}" text-anchor="end" class="t-strong">${num(last)}</text>`
      + (week ? `<text x="${W - 2}" y="${(y(last) + 15).toFixed(1)}" text-anchor="end" class="t-dim">${week}</text>` : ''), label);
  }

  // 加權指數與 20 日均線：兩條線，尾端標收盤與均線，中間標收盤高於／低於均線幾 %。
  function indexLine(series, dataDate) {
    const closes = upTo(series, 'close', dataDate).slice(-(INDEX_DAYS + MA - 1));
    const count = closes.length - (MA - 1);   // 畫得出均線的天數
    if (count < 2) return '';
    const all = closes.map(row => Number(row.close)), shown = all.slice(MA - 1);
    const ma = shown.map((_, i) => all.slice(i, i + MA).reduce((sum, value) => sum + value, 0) / MA);
    const height = 150, top = 28, bottom = 132;
    const least = Math.min(...shown, ...ma), most = Math.max(...shown, ...ma), pad = ((most - least) || Math.abs(most) || 1) * 0.04;
    const lo = least - pad, hi = most + pad;
    const x = i => 6 + i * (W - 92) / (count - 1), y = value => bottom - (value - lo) / (hi - lo) * (bottom - top);
    const last = shown.at(-1), lastMa = ma.at(-1), ex = x(count - 1), gap = (last / lastMa - 1) * 100;
    const above = last >= lastMa, tone = above ? 'up' : 'down', dir = above ? -1 : 1;   // dir：收盤在均線的上面（-1）還是下面（1）
    // 右邊三行字：均線、差距、收盤。兩條線靠得近時上下各讓開一行，不疊在一起；整組不超出圖
    let maY = y(lastMa) + 4, closeY = y(last) + 4;
    const middle = (maY + closeY) / 2;
    if (Math.abs(closeY - maY) < 30) { maY = middle - dir * 15; closeY = middle + dir * 15; }
    const shift = clamp(0, 22 - Math.min(maY, closeY), height - 4 - Math.max(maY, closeY));
    const gapText = `${above ? '高於' : '低於'} ${Math.abs(gap).toFixed(1)}%`;
    const latest = monthDay(closes.at(-1).date);
    const label = `加權指數與 20 日均線：${latest} 收 ${num(last)}，20 日均線 ${num(lastMa)}，收盤${above ? '高於' : '低於'}均線 ${Math.abs(gap).toFixed(1)}%`;
    const tick = Math.abs(y(last) - y(lastMa)) > 12
      ? `<line x1="${ex.toFixed(1)}" x2="${ex.toFixed(1)}" y1="${(y(lastMa) + dir * 5).toFixed(1)}" y2="${(y(last) - dir * 6).toFixed(1)}" class="gap is-${tone}"/>` : '';
    return svg('index', height, `<text x="4" y="12" class="t-title">加權指數與 20 日均線</text>`
      + `<path d="${path(ma, x, y)}" class="line-ma"/><path d="${path(shown, x, y)}" class="line-ink"/>${tick}`
      + `<circle cx="${ex.toFixed(1)}" cy="${y(last).toFixed(1)}" r="4" fill="var(--ink)"/>`
      + `<text x="${W - 2}" y="${(maY + shift).toFixed(1)}" text-anchor="end" class="t-dim">均線 ${num(lastMa)}</text>`
      + `<text x="${W - 2}" y="${(middle + shift).toFixed(1)}" text-anchor="end" class="t-strong t-${tone}">${gapText}</text>`
      + `<text x="${W - 2}" y="${(closeY + shift).toFixed(1)}" text-anchor="end" class="t-strong">收 ${num(last)}</text>`, label);
  }

  // 各市場漲跌幅（票 03）：橫條，由大到小，台股放最後一列、顏色加深。三欄：名稱、從中軸出發的長條（跌往左、漲往右）、帶正負號的數字。
  // 數字由產生器算好放在那一類裡（markets、home）。長條的比例跟著資料：最大的那一根填滿可用的寬度；
  // 全部上漲時軸靠左、全部下跌時軸靠右、有漲有跌時軸在中間。沒有海外市場的數字就不畫。
  function overseasBars(markets, home) {
    const usable = row => row && typeof row === 'object' && filled(row.name) && row.change_pct !== null && row.change_pct !== ''
      && Number.isFinite(Number(row.change_pct));
    const tenth = row => ({ name: row.name.trim(), pct: Math.round(Number(row.change_pct) * 10) / 10 + 0 });
    const list = (Array.isArray(markets) ? markets : []).filter(usable).map(tenth).sort((a, b) => b.pct - a.pct);
    if (!list.length) return '';
    const rows = usable(home) ? [...list, { ...tenth(home), home: true }] : list;
    const most = Math.max(0, ...rows.map(row => row.pct)), least = Math.min(0, ...rows.map(row => row.pct));
    const left = 110, right = W - 48, mixed = least < 0 && most > 0;
    const axis = mixed ? (left + right) / 2 : least < 0 ? right : left;
    const scale = (mixed ? (right - left) / 2 : right - left) / (Math.max(most, -least) || 1);
    const rowH = 26, height = 22 + rows.length * rowH;
    const signed = pct => `${pct > 0 ? '+' : ''}${pct.toFixed(1)}`;
    const body = rows.map((row, i) => {
      const y = 24 + i * rowH, width = Math.max(1.5, Math.abs(row.pct) * scale);
      const tone = row.pct < 0 ? 'down' : row.pct > 0 ? 'up' : '';
      return `<text x="4" y="${y + 14}" class="${row.home ? 't-strong' : 't-name'}">${esc(row.name)}</text>`
        + `<rect x="${(row.pct < 0 ? axis - width : axis).toFixed(1)}" y="${y + 3}" width="${width.toFixed(1)}" height="15" rx="3" fill="var(--${tone || 'dim'})" opacity="${row.home ? 1 : 0.55}"/>`
        + `<text x="${W - 2}" y="${y + 14}" text-anchor="end" class="t-strong${tone ? ` t-${tone}` : ''}">${signed(row.pct)}</text>`;
    }).join('');
    const label = `各市場漲跌幅：${rows.map(row => `${row.name} ${signed(row.pct)}%`).join('、')}`;
    return svg('overseas', height, `<text x="4" y="12" class="t-title">各市場漲跌幅（%）</text>`
      + `<line x1="${axis.toFixed(1)}" x2="${axis.toFixed(1)}" y1="20" y2="${height - 2}" class="axis"/>${body}`, label);
  }

  // 追蹤的價位（票 05）：一條刻度，價位那裡一根刻度線（標價位）、最新收盤一個圓點（標「收 N」）、下面標收盤高於／低於價位幾 %。
  // 條件已經達到（要站上的收在上面、要跌破的收在下面）才上色：站上用漲的顏色、跌破用跌的顏色；還沒達到用墨色。
  // 範圍跟著兩個數字算；兩個數字很近時，上面兩段字左右讓開。四個數字欄位不齊（消息型、沒有價位的）就不畫。
  const price = value => value.toLocaleString('en-US', { maximumFractionDigits: Math.abs(value) >= 1000 ? 0 : 2 });
  function levelTrack(item) {
    const level = item?.level, close = item?.last_close;
    if (![level, close].every(value => typeof value === 'number' && Number.isFinite(value) && value > 0)
      || !['above', 'below'].includes(item.direction) || !/^\d{4}-\d{2}-\d{2}/.test(item.last_date || '')) return '';
    const above = item.direction === 'above', met = above ? close > level : close < level;
    const tone = met ? (above ? 'up' : 'down') : '';
    const pad = Math.max(Math.abs(close - level) * 0.6, level * 0.02);
    const lo = Math.min(level, close) - pad, hi = Math.max(level, close) + pad;
    const x = value => 10 + (value - lo) / (hi - lo) * (W - 20), xl = x(level), xc = x(close), middle = (xl + xc) / 2;
    const levelText = price(level), closeText = `收 ${price(close)}`, wl = textWidth(levelText), wc = textWidth(closeText);
    let levelAnchor = 'middle', closeAnchor = 'middle', lx = clamp(xl, wl / 2, W - wl / 2), cx = clamp(xc, wc / 2, W - wc / 2);
    if (Math.abs(xl - xc) < (wl + wc) / 2 + 8) {
      const levelFirst = level <= close;
      levelAnchor = levelFirst ? 'end' : 'start';
      closeAnchor = levelFirst ? 'start' : 'end';
      lx = middle + (levelFirst ? -4 : 4);
      cx = middle + (levelFirst ? 4 : -4);
    }
    const gap = Math.abs(close / level - 1) * 100;
    const gapText = close === level ? '持平' : `${close > level ? '高於' : '低於'} ${gap.toFixed(1)}%`;
    const label = `價位 ${levelText}，${monthDay(item.last_date)} ${closeText}，`
      + `${close === level ? '收盤與價位持平' : `收盤${close > level ? '高於' : '低於'}價位 ${gap.toFixed(1)}%`}，${met ? '已達條件' : '未達條件'}`;
    return svg('level', 66, `<line x1="10" x2="${W - 10}" y1="38" y2="38" class="track"/>`
      + `<line x1="${xl.toFixed(1)}" x2="${xc.toFixed(1)}" y1="38" y2="38" class="track-hot${tone ? ` is-${tone}` : ''}"/>`
      + `<line x1="${xl.toFixed(1)}" x2="${xl.toFixed(1)}" y1="28" y2="48" class="tick"/>`
      + `<circle cx="${xc.toFixed(1)}" cy="38" r="6" fill="var(--${tone || 'ink'})"/>`
      + `<text x="${lx.toFixed(1)}" y="20" text-anchor="${levelAnchor}" class="t-dim">${levelText}</text>`
      + `<text x="${cx.toFixed(1)}" y="20" text-anchor="${closeAnchor}" class="t-strong">${closeText}</text>`
      + `<text x="${clamp(middle, 34, W - 34).toFixed(1)}" y="60" text-anchor="middle" class="t-strong${tone ? ` t-${tone}` : ''}">${gapText}</text>`, label);
  }

  // 追蹤事項那一類要列的項目（票 05）：有結果的在前，其餘照離價位的遠近（近的先；畫不出刻度的排後面），最多 5 項
  function tracked(watch) {
    const list = (Array.isArray(watch) ? watch : []).filter(item => item && typeof item === 'object' && filled(item.label));
    const far = item => (levelTrack(item) ? Math.abs(item.last_close / item.level - 1) : Infinity);
    const open = list.filter(item => !item.result).map((item, at) => [item, far(item), at])
      .sort((a, b) => (a[1] === b[1] ? 0 : a[1] - b[1]) || a[2] - b[2]).map(entry => entry[0]);
    return [...list.filter(item => item.result), ...open].slice(0, TRACK_MAX);
  }

  // 追蹤事項的一項：對象＋條件一行（個股帶名稱；有結果的寫成立／未成立）→ 刻度 → 因應。有刻度時價位標在圖上，那一行就不重複寫
  function trackedHtml(item, impactPoints) {
    const chart = levelTrack(item), stock = item.subject !== 'market';
    const name = stock ? ((filled(item.subject_name) && item.subject_name.trim()) || String(item.subject ?? '')) : '';
    const state = item.status === 'met' ? '：成立' : item.status === 'failed' ? '：未成立' : '';
    const title = `${name ? `${name} ` : ''}${chart ? item.label.replace(/（.*?）/g, '') : item.label}${state}`;
    const lines = (Array.isArray(item.prepare) ? item.prepare : []).filter(filled);
    const inner = `${chart ? `<div class="plain-charts">${chart}</div>` : ''}${lines.length ? impactPoints({ prepare: lines }) : ''}`;
    return `<section class="plain-track"><h4 class="plain-tab-say">${esc(title)}</h4>${inner ? `<div class="plain-group">${inner}</div>` : ''}</section>`;
  }

  // 那一類的圖：國際股市一張、籌碼面兩張、技術面一張；市場消息與沒有新東西的那一類不畫
  function charts(cat, overview) {
    const date = filled(cat.data_date) ? cat.data_date : '';
    const drawn = (cat.key === 'chips' ? [foreignBars(overview?.chips?.foreign, date), futuresLine(overview?.chips?.futures, date)]
      : cat.key === 'tech' ? [indexLine(overview?.analysis?.daily, date)]
      : cat.key === 'overseas' ? [overseasBars(cat.markets, cat.home)] : []).filter(Boolean);
    return drawn.length ? `<div class="plain-charts">${drawn.join('')}</div>` : '';
  }

  // 這一班有的類別，照固定的順序；不認得的 key 略過。
  // 追蹤事項不在那一份裡（個股的只有站主能看）：watch 是站主自己的追蹤事項，有項目才在最後多一顆鍵；那一份裡叫 tracking 的不算。
  function categories(brief, watch) {
    const list = Array.isArray(brief?.cats) ? brief.cats.filter(cat => cat && filled(cat.key) && filled(cat.say) && cat.key !== TRACKING) : [];
    const items = tracked(watch);
    return CATEGORIES.map(([key, name]) => ({ name, cat: key === TRACKING ? (items.length ? { key, items } : undefined) : list.find(cat => cat.key === key) }))
      .filter(item => item.cat);
  }

  // 一類的內容：一句判斷 → 圖 → 影響／觀察／因應。這一班沒有新東西（quiet）的只有那一句。追蹤事項是一項一段。
  function categoryHtml(cat, overview, impactPoints) {
    if (cat.key === TRACKING && Array.isArray(cat.items)) return cat.items.map(item => trackedHtml(item, impactPoints)).join('');
    if (cat.quiet === true) return `<h4 class="plain-tab-say is-quiet">${esc(cat.say)}</h4>`;
    return `<h4 class="plain-tab-say">${esc(cat.say)}</h4><div class="plain-group">${charts(cat, overview)}${impactPoints(cat)}</div>`;
  }

  // current：面板現在停在哪一類（重畫時留在原地）；沒有就停在這一班影響最大的那一類，再沒有就第一類。watch：站主的追蹤事項
  function panelHtml(brief, overview, impactPoints, current, watch) {
    const list = categories(brief, watch), head = `<p class="read-line big">${esc(brief.one_line)}</p>`;
    if (!list.length) return head;
    const active = list.find(item => item.cat.key === current) || list.find(item => item.cat.key === brief.lead && item.cat.key !== TRACKING) || list[0];
    return `${head}<div class="plain-tabs"><div class="chip-seg" role="radiogroup" aria-label="類別">${list.map(item => `<button type="button" role="radio" data-cat="${esc(item.cat.key)}" aria-checked="${item === active}">${esc(item.name)}</button>`).join('')}</div></div>`
      + `<div id="plainTab">${categoryHtml(active.cat, overview, impactPoints)}</div>`;
  }

  function bind(body, brief, overview, impactPoints, onPick, watch) {
    const strip = body.querySelector('.plain-tabs'), tab = body.querySelector('#plainTab');
    if (!strip || !tab) return;
    const list = categories(brief, watch), buttons = [...strip.querySelectorAll('button[data-cat]')];
    // 類別鍵放不下、要左右滑的時候才把橫向的手勢還給瀏覽器（平常那一排是「按住滑到要的再放開」，見 chart-menus.js）
    // 面板畫好的時候還沒顯示、量不到寬度，所以跟著那一排的大小重量（打開、轉向、改視窗大小）
    // 放不下時先把選到的那一顆捲進來（影響最大的那一類可能排在後面），只在第一次量到時做
    let placed = false;
    const measure = () => {
      const overflow = strip.scrollWidth > strip.clientWidth + 1;
      strip.classList.toggle('is-overflow', overflow);
      const on = strip.querySelector('button[aria-checked="true"]');
      if (!overflow || placed || !on) return;
      placed = true;
      const box = on.getBoundingClientRect(), frame = strip.getBoundingClientRect();
      if (box.left < frame.left || box.right > frame.right) strip.scrollLeft += box.left - frame.left - (frame.width - box.width) / 2;
    };
    measure();
    if (window.ResizeObserver) new ResizeObserver(measure).observe(strip);
    buttons.forEach(button => button.addEventListener('click', () => {
      const picked = list.find(item => item.cat.key === button.dataset.cat);
      if (!picked) return;
      buttons.forEach(other => other.setAttribute('aria-checked', String(other === button)));
      tab.innerHTML = categoryHtml(picked.cat, overview, impactPoints);
      if (strip.classList.contains('is-overflow')) button.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      if (onPick) onPick(picked.cat.key);
    }));
  }

  // 最新一班；開關關著、還沒有資料、讀不到都回 null（首頁照舊顯示原本的解讀）
  async function load() {
    if (location.protocol === 'file:') return null;
    try {
      const response = await fetch('/api/market-brief', { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) return null;
      const brief = (await response.json())?.brief;
      return brief && typeof brief === 'object' && filled(brief.one_line) && categories(brief).length ? brief : null;
    } catch (_) { return null; }
  }

  // 站主自己的追蹤事項（票 05）：只有站主才有「追蹤事項」那一顆鍵，所以先確認是站主，再用盯項那支 API 的項目。
  // 不是站主、功能關著、讀不到都回空清單（那一顆鍵就不出現）。
  async function loadWatch() {
    if (location.protocol === 'file:') return [];
    const get = async url => {
      const response = await fetch(url, { cache: 'no-store', headers: { Accept: 'application/json' } });
      return response.ok ? response.json() : null;
    };
    try {
      const [me, body] = await Promise.all([get('/api/me'), get('/api/impact-watch')]);
      return me?.owner === true && body?.available && Array.isArray(body.items) ? tracked(body.items) : [];
    } catch (_) { return []; }
  }

  window.MarketBrief = { load, loadWatch, panelHtml, bind, categories, categoryHtml, foreignBars, futuresLine, indexLine, overseasBars, levelTrack };
})();
