"""產生自選與庫存頁「手勢版」的樣品 portfolio-gesture.html（短網址 /pd）。
本人 2026-11-16 看過正式版點開的樣子之後說：刪除庫存改成把那一列向左滑、露出紅色的刪除點一下就刪；
看個股頁不用在下面多一個位置，點上面的股票名稱就進去；自選只有一個分類太少，要能照族群分。
之後兩題的答案：自己取名的群組、一檔只放一個群組（短期／長期照舊另外標）。
這一版的做法：
  點名稱進個股頁，點那一列其他地方才展開；
  向左滑露出動作（有庫存：修改、刪除；也在自選的再多一個移出自選；只在自選：移出），點一下就做，上面一行寫結果與「還原」；
  修改是在那一列就地改，不跳到上面的表單；
  點開之後只留三個數字與備註，自選的設定是一顆群組小鍵與短期／長期；
  按「自選」時清單照群組分段，點群組的名稱可以改名；
  「＋」只有一格代號與兩顆鍵。
列的樣式沿用自選與庫存頁自己的；小鍵與選單用網站現成的（chart-menus.js 與 chart-studies.css）。
樣品上的新增、修改、刪除只改這一頁的畫面，不會真的寫資料；重新整理就回到原樣。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-portfolio-gesture.py"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("samples", HERE / "build-portfolio-samples.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)   # 會順便重新產生前兩個樣品（內容不變）

# 庫存三檔的股數、均價、收盤與預覽的樣本資料相同；自選多放幾檔才看得出群組
STOCKS = [
    dict(code="2330", name="台積電", close=2235, holding=dict(shares=1000, cost=980, note="核心部位"), watch=dict(horizon="long", group="")),
    dict(code="2603", name="長榮", close=245.5, holding=dict(shares=2000, cost=215, note="短線"), watch=dict(horizon="short", group="航運")),
    dict(code="0050", name="元大台灣50", close=97.45, holding=dict(shares=3000, cost=58.4, note=""), watch=None),
    dict(code="6669", name="緯穎", close=None, holding=None, watch=dict(horizon="none", group="AI 伺服器")),
    dict(code="2382", name="廣達", close=None, holding=None, watch=dict(horizon="short", group="AI 伺服器")),
    dict(code="3017", name="奇鋐", close=None, holding=None, watch=dict(horizon="none", group="散熱")),
    dict(code="3324", name="雙鴻", close=None, holding=None, watch=dict(horizon="long", group="散熱")),
    dict(code="2609", name="陽明", close=None, holding=None, watch=dict(horizon="none", group="航運")),
    dict(code="2454", name="聯發科", close=None, holding=None, watch=dict(horizon="none", group="")),
]

STYLE = """
  /* ---- 一列：名稱是連結，其他地方點了展開；上面那一條可以向左滑 ---- */
  #sample .sw-clip { position: relative; overflow: hidden; border-radius: inherit; }
  #sample .sw-face { position: relative; z-index: 1; cursor: default; touch-action: pan-y; -webkit-user-select: none; user-select: none; transition: transform 320ms var(--ease-depth); }
  #sample .sw-row[data-state="bull"] .sw-face { background: var(--row-up); }
  #sample .sw-row[data-state="none"] .sw-face { background: var(--page, var(--bg)); }
  #sample .sw-row.is-dragging .sw-face { transition: none; }
  /* 展開用的按鈕鋪滿整條，名稱疊在它上面 */
  #sample .sw-open { position: absolute; inset: 0; z-index: 0; border: 0; border-radius: inherit; background: none; cursor: pointer; -webkit-tap-highlight-color: transparent; }
  #sample .sw-open:focus-visible { outline: 2px solid var(--gold); outline-offset: -2px; }
  #sample .sw-face > :not(.sw-open) { position: relative; z-index: 1; pointer-events: none; }
  #sample .sw-face > a.hv-name { justify-self: start; max-width: 100%; pointer-events: auto; color: inherit; text-decoration: none; -webkit-touch-callout: none; }
  #sample .sw-face > a.hv-name::after { content: ""; position: absolute; inset: -8px -10px; }   /* 觸控範圍補大 */
  #sample .sw-face > a.hv-name:focus-visible { outline: 2px solid var(--gold); outline-offset: 4px; border-radius: 4px; }
  #sample .sw-row[data-state="bull"]:has(.sw-open:active) { transform: translateY(calc(var(--key-thick) - 1px)); box-shadow: var(--key-shadow-press); }
  #sample .sw-row.pf-only-watch .sw-face { grid-template-columns: minmax(0, 1fr) auto 12px; }
  /* 滑開之後露出來的動作：平的色塊，墊在那一條下面 */
  #sample .sw-acts { position: absolute; inset: 0 0 0 auto; display: flex; }
  #sample .sw-act { width: 64px; padding: 0 4px; border: 0; border-left: 1px solid var(--hair); background: var(--well); color: var(--ink); font: 500 13px/1.25 var(--sans); cursor: pointer; }
  #sample .sw-act.sw-danger { border-left-color: transparent; background: var(--up); color: var(--bg); font-weight: 700; }
  #sample .sw-act + .sw-danger { border-left: 1px solid color-mix(in srgb, var(--bg) 35%, transparent); }
  #sample .sw-act:focus-visible { outline: 2px solid var(--gold); outline-offset: -3px; }
  #sample .sw-act span { display: block; }
  /* 滑鼠沒有滑動的手勢：游標移到那一列，右邊出現「⋯」，按了把動作滑出來 */
  #sample .sw-more { display: none; }
  @media (hover: hover) and (pointer: fine) {
    #sample .sw-face { grid-template-columns: 140px minmax(0, 420px) minmax(96px, 1fr) 12px 28px; }
    #sample .sw-row.pf-only-watch .sw-face { grid-template-columns: minmax(0, 1fr) auto 12px 28px; }
    #sample .sw-face > .sw-more { display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: var(--r-key); background: none; color: var(--faint); font: 700 16px var(--sans); cursor: pointer; pointer-events: auto; opacity: 0; transition: opacity 160ms linear; }
    #sample .sw-row:hover .sw-more, #sample .sw-more:focus-visible, #sample .sw-row.is-swiped .sw-more { opacity: 1; }
    #sample .sw-more:hover { background: var(--well); color: var(--ink); }
    #sample a.hv-name:hover strong { text-decoration: underline; text-underline-offset: 3px; }
  }
  /* ---- 點開之後 ---- */
  #sample .sw-facts3 { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 12px; }
  #sample .sw-set { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; }
  #sample .hv-facts ~ .sw-set, #sample .hv-note ~ .sw-set { padding-top: 12px; border-top: 1px solid var(--hair); }
  #sample .sw-pop { position: relative; display: inline-flex; }
  #sample .sw-set .chart-chip b { font-weight: 600; color: var(--dim); }
  #sample .sw-menu { left: 0; right: auto; flex-direction: column; flex-wrap: nowrap; align-items: stretch; min-width: 148px; transform-origin: 20% 0; }
  #sample .sw-menu button { min-height: 40px; padding: 0 12px; border: 0; border-radius: var(--r-key); background: transparent; color: var(--ink); font: 500 14px var(--sans); text-align: left; white-space: nowrap; cursor: pointer; }
  #sample .sw-menu button[aria-checked="true"] { background: var(--ink); color: var(--bg); font-weight: 700; }
  #sample .sw-menu hr { width: 100%; height: 1px; margin: 4px 0; border: 0; background: var(--hair); }
  #sample .sw-new { display: inline-flex; gap: 6px; }
  #sample .sw-new input, #sample .sw-group input { width: 132px; min-height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--ink); font: 16px var(--sans); }
  #sample .sw-new button { min-height: 34px; padding: 0 12px; border: 0; border-radius: 8px; background: var(--ink); color: var(--bg); font: 600 13px var(--sans); cursor: pointer; }
  #sample .sw-set .key-strip { justify-content: flex-start; flex: none; }
  #sample .sw-set .key-strip .cell { width: 60px; max-width: 60px; min-height: 36px; }
  #sample .sw-set .key-strip .cell-name { font: 500 13px var(--sans); color: var(--ink); }
  #sample .sw-set .pf-watch-toggle { min-height: 36px; margin-left: auto; }
  #sample .sw-set .pf-watch-toggle small { color: var(--faint); font: 12px var(--mono); }
  /* 選單要能蓋到下面的列：展開完之後才放開，開著的那一列疊在上面 */
  #sample .hv-row.is-open { z-index: 2; }
  #sample .hv-row.is-settled .hv-clip { overflow: visible; }
  /* 就地修改 */
  #sample .sw-edit { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 0; border-bottom: 0; }
  #sample .sw-edit .wide { grid-column: 1 / -1; }
  #sample .sw-edit > button { grid-column: auto; }
  #sample .pf-form button.sw-ghost { background: transparent; color: var(--ink); border: 1px solid var(--line); }
  #sample .pf-form button.sw-ghost[aria-expanded="true"] { background: var(--well); box-shadow: var(--well-shadow); border-color: transparent; }
  /* ---- 群組的標題（按「自選」時） ---- */
  #sample .sw-group { display: flex; align-items: baseline; gap: 8px; margin-top: 10px; padding: 0 2px; }
  #sample .sw-group:first-child { margin-top: 0; }
  #sample .sw-group button, #sample .sw-group > span { padding: 4px 0; border: 0; background: none; color: var(--dim); font: 600 13px var(--sans); cursor: pointer; }
  #sample .sw-group > span { cursor: default; }
  #sample .sw-group button:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
  #sample .sw-group small { color: var(--faint); font: 12px var(--mono); }
  /* ---- 上面一行的結果與還原 ---- */
  #sample .sw-status { display: flex; align-items: center; gap: 12px; min-height: 0; margin: 0; color: var(--dim); font-size: 13px; }
  #sample .sw-status:not(:empty) { min-height: 32px; margin-bottom: 8px; }
  #sample .sw-status button { min-height: 32px; padding: 0 12px; border: 1px solid var(--line); border-radius: 8px; background: none; color: var(--ink); font: 600 13px var(--sans); cursor: pointer; }
  /* ---- 「＋」：一格代號、兩顆鍵 ---- */
  #sample .sw-add-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 4px 0; border-bottom: 0; }
  #sample .sw-add-form > label:first-child, #sample .sw-add-form .wide { grid-column: 1 / -1; }
  #sample .sw-add-form > button { grid-column: auto; }
  #sample .sw-add-more { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  #sample .sw-add-more[hidden] { display: none; }
  #sample .sw-add-note { grid-column: 1 / -1; margin: 0; color: var(--faint); font-size: 12px; }
  @media (prefers-reduced-motion: reduce) { #sample .sw-face { transition: none; } }
  @media (max-width: 760px) { #sample .sw-set .pf-watch-toggle { margin-left: 0; } }
"""

SCRIPT = r"""
(function () {
  'use strict';
  const root = document.getElementById('sample');
  const list = root.querySelector('.hv-rows'), status = root.querySelector('.sw-status');
  const stocks = JSON.parse(document.getElementById('sampleData').textContent);
  // 樣品認得的代號（「＋」用）：名稱與收盤
  const KNOWN = { '2317': ['鴻海', 228.5], '2308': ['台達電', 412], '2881': ['富邦金', 93.2], '2376': ['技嘉', 268], '2615': ['萬海', 91.4] };
  const HORIZONS = [['none', '不標'], ['short', '短期'], ['long', '長期']];
  const WORD = { none: '', short: '短期', long: '長期' };
  const ACT_WIDTH = 64;
  let filter = 'all', openCode = null, editCode = null, namingCode = null, renaming = null, swiped = null, undo = null;

  const money = value => '$' + Math.round(value).toLocaleString('en-US');
  const signed = value => (value >= 0 ? '+' : '-') + '$' + Math.abs(Math.round(value)).toLocaleString('en-US');
  const number = value => Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const esc = text => String(text).replace(/[&<>"]/g, mark => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[mark]));
  const find = code => stocks.find(item => item.code === code);
  const groups = () => [...new Set(stocks.filter(item => item.watch && item.watch.group).map(item => item.watch.group))];

  // 庫存的算法照正式的頁面：照市值由大到小；橫條的寬度是占全部庫存市值的比例
  function figures() {
    const held = stocks.filter(item => item.holding).map(item => {
      const value = item.holding.shares * item.close, cost = item.holding.shares * item.holding.cost;
      return { item, value, cost, gain: value - cost };
    }).sort((a, b) => b.value - a.value);
    const total = held.reduce((sum, row) => sum + row.value, 0), cost = held.reduce((sum, row) => sum + row.cost, 0);
    return { held, total, cost };
  }

  function actsOf(item) {
    const acts = [];
    if (item.holding) acts.push(['edit', '修改', '']);
    if (item.holding && item.watch) acts.push(['unwatch', '<span>移出</span><span>自選</span>', '']);
    if (item.holding) acts.push(['delete', item.watch ? '<span>刪除</span><span>庫存</span>' : '刪除', 'sw-danger']);
    else acts.push(['unwatch', '移出', 'sw-danger']);
    return acts;
  }

  function settings(item) {
    const watch = item.watch, code = item.code;
    const picks = groups().map(name => `<button type="button" role="radio" aria-checked="${name === watch.group}" data-group="${esc(name)}">${esc(name)}</button>`).join('');
    const chip = namingCode === code
      ? `<form class="sw-new" data-code="${code}"><input maxlength="8" placeholder="群組名稱" aria-label="新群組的名稱" enterkeyhint="done"><button type="submit">確定</button></form>`
      : `<span class="sw-pop"><button type="button" class="chart-chip" aria-expanded="false" aria-controls="g-${code}">群組 <b>${esc(watch.group || '未分組')}</b></button>
          <div class="chart-menu sw-menu" id="g-${code}" data-menu-autoclose hidden>${picks}<button type="button" role="radio" aria-checked="${!watch.group}" data-group="">未分組</button><hr><button type="button" data-new-group>＋ 新群組</button></div></span>`;
    const cells = HORIZONS.map(([key, label]) => `<button type="button" class="cell" data-horizon="${key}" aria-pressed="${key === watch.horizon}"><span class="cell-face"></span><span class="cell-name">${label}</span></button>`).join('');
    return `<div class="sw-set">${chip}<div class="key-strip" role="group" aria-label="${code} 短期或長期">${cells}</div>
      <label class="pf-watch-toggle"><input type="checkbox" role="switch" aria-label="關注 ${code} ${esc(item.name)}"${watch.watching ? ' checked' : ''}><span>關注</span><small>${stocks.filter(other => other.watch && other.watch.watching).length}／20</small></label></div>`;
  }

  function rowHtml(item, held, total, inGroupView) {
    const { code, name, watch } = item, open = openCode === code;
    const acts = actsOf(item).map(([act, label, tone]) => `<button type="button" class="sw-act ${tone}" data-act="${act}" tabindex="-1">${label}</button>`).join('');
    const mark = watch ? (item.holding ? (watch.group || '自選') : WORD[watch.horizon]) : '';
    const under = code + (mark && !(inGroupView && item.holding && watch.group) ? `<em>${esc(mark)}</em>` : (inGroupView && item.holding && WORD[watch.horizon] ? `<em>${WORD[watch.horizon]}</em>` : ''));
    let middle, panel, state;
    if (held) {
      const gain = held.gain, base = Math.min(held.cost, held.value) / total * 100, extra = Math.abs(gain) / total * 100;
      const ret = (gain >= 0 ? '+' : '') + (gain / held.cost * 100).toFixed(2) + '%', tone = gain > 0 ? 'up' : gain < 0 ? 'down' : '';
      state = gain > 0 ? 'bull' : gain < 0 ? 'bear' : 'none';
      middle = `<span class="hv-bar" aria-hidden="true"><i class="base" style="--w:${base.toFixed(2)}"></i><i class="${gain >= 0 ? 'gain' : 'lack'}" style="--w:${extra.toFixed(2)}"></i></span><span class="hv-ret num ${tone}">${ret}</span>`;
      const facts = editCode === code
        ? `<form class="pf-form sw-edit" data-code="${code}"><label>股數<input type="number" inputmode="numeric" name="shares" value="${item.holding.shares}"></label><label>平均成本<input type="number" inputmode="decimal" step="any" name="cost" value="${item.holding.cost}"></label>
            <label class="wide">備註<input name="note" maxlength="200" placeholder="選填" value="${esc(item.holding.note)}"></label><button type="submit">儲存</button><button type="button" class="sw-ghost" data-cancel>取消</button></form>`
        : `<ul class="hv-facts sw-facts3"><li><span>市值</span><b>${money(held.value)}</b></li><li><span>${gain >= 0 ? '賺' : '賠'}</span><b class="${tone}">${signed(gain)}</b></li><li><span>股數・均價</span><b>${number(item.holding.shares)}・${number(item.holding.cost)}</b></li></ul>
           ${item.holding.note ? `<p class="hv-note">備註：${esc(item.holding.note)}</p>` : ''}`;
      panel = facts + (watch && editCode !== code ? settings(item) : '');
    } else {
      state = 'none';
      middle = `<span class="pf-tag">${inGroupView ? '' : esc(watch.group)}</span>`;
      panel = settings(item);
    }
    return `<li class="hv-row depth-row on-page sw-row${held ? '' : ' pf-only-watch'}${open ? ' is-open is-settled' : ''}" data-code="${code}" data-state="${state}" data-acts="${actsOf(item).length}">
      <div class="sw-clip"><div class="sw-acts" inert>${acts}</div>
        <div class="hv-top sw-face"><a class="hv-name" href="/stock.html?code=${code}" draggable="false" aria-label="${esc(name)} ${code} 的個股頁"><strong>${esc(name)}</strong><span>${under}</span></a>
          ${middle}<span class="chev" aria-hidden="true"></span>
          <button type="button" class="sw-more" aria-label="${esc(name)} 的動作" aria-expanded="false">⋯</button>
          <button type="button" class="sw-open" aria-expanded="${open}" aria-label="${open ? '收起' : '展開'} ${esc(name)} 的明細"></button></div></div>
      <div class="hv-panel depth-panel"${open ? '' : ' inert'}><div class="hv-clip"><div class="hv-inner">${panel}</div></div></div>
    </li>`;
  }

  function render() {
    const { held, total, cost } = figures(), heldOf = new Map(held.map(row => [row.item.code, row]));
    const gain = total - cost;
    root.querySelector('.hv-total').hidden = !held.length;
    if (held.length) root.querySelector('.hv-total').innerHTML = `<strong class="num" aria-label="目前市值">${money(total)}</strong><span>成本 <b class="num">${money(cost)}</b></span><span>${gain >= 0 ? '賺' : '賠'} <b class="num ${gain >= 0 ? 'up' : 'down'}">${signed(gain)}</b>（<b class="num ${gain >= 0 ? 'up' : 'down'}">${(gain >= 0 ? '+' : '') + (gain / cost * 100).toFixed(2)}%</b>）</span>`;
    const watchOnly = stocks.filter(item => item.watch && !item.holding), watching = stocks.filter(item => item.watch);
    root.querySelector('#countAll').textContent = held.length + watchOnly.length;
    root.querySelector('#countHolding').textContent = held.length;
    root.querySelector('#countWatch').textContent = watching.length;
    const ordered = [...held.map(row => row.item), ...watchOnly];
    let html = '';
    if (filter === 'watch') {
      // 照群組分段：有名字的群組照第一檔加入的先後，未分組放最後
      [...groups(), ''].forEach(name => {
        const members = ordered.filter(item => item.watch && item.watch.group === name);
        if (!members.length) return;
        const title = name
          ? (renaming === name ? `<form data-rename="${esc(name)}"><input maxlength="8" value="${esc(name)}" aria-label="群組的名稱" enterkeyhint="done"></form>` : `<button type="button" data-rename-start="${esc(name)}" aria-label="改 ${esc(name)} 的名稱">${esc(name)}</button>`)
          : '<span>未分組</span>';
        html += `<li class="sw-group">${title}<small>${members.length}</small></li>` + members.map(item => rowHtml(item, heldOf.get(item.code), total, true)).join('');
      });
    } else {
      html = ordered.filter(item => filter === 'all' || item.holding).map(item => rowHtml(item, heldOf.get(item.code), total, false)).join('');
    }
    list.innerHTML = html || '<li class="sw-group"><span>這裡還沒有股票，按右上的「＋」新增。</span></li>';
    swiped = null;
    const field = list.querySelector('.sw-new input, .sw-group input, .sw-edit input');
    if (field) { field.focus(); if (field.closest('.sw-group')) field.select(); }
  }

  function say(text, back) {
    undo = back || null;
    status.innerHTML = esc(text) + (back ? '<button type="button" data-undo>還原</button>' : '');
  }

  // ---- 向左滑 ----
  const rowOf = node => node && node.closest('.sw-row');
  function setDrawer(row, open) {
    if (!row) return;
    const face = row.querySelector('.sw-face'), acts = row.querySelector('.sw-acts');
    face.style.transform = open ? `translateX(${-ACT_WIDTH * Number(row.dataset.acts)}px)` : '';
    row.classList.toggle('is-swiped', open);
    acts.toggleAttribute('inert', !open);
    row.querySelector('.sw-more').setAttribute('aria-expanded', String(open));
    if (open) { if (swiped && swiped !== row) setDrawer(swiped, false); swiped = row; } else if (swiped === row) swiped = null;
  }
  let drag = null, swallow = 0, pressedAct = null;
  root.addEventListener('pointerdown', event => {
    pressedAct = event.target.closest('.sw-act');
    const face = event.target.closest('.sw-face');
    if (swiped && !event.target.closest('.sw-acts') && rowOf(event.target) !== swiped) setDrawer(swiped, false);
    if (!face || (event.pointerType === 'mouse' && event.button !== 0) || event.target.closest('.sw-more')) return;
    const row = rowOf(face), width = ACT_WIDTH * Number(row.dataset.acts);
    drag = { id: event.pointerId, row, face, width, x: event.clientX, y: event.clientY, from: row === swiped ? -width : 0, at: null, moved: false };
  });
  root.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved) {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { drag = null; return; }   // 是在上下捲
      if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      drag.moved = true;
      drag.row.classList.add('is-dragging');
      try { drag.face.setPointerCapture(drag.id); } catch (_) { /* 沒有也照樣跟著手指 */ }
    }
    let x = drag.from + dx;
    if (x > 0) x = 0;
    if (x < -drag.width) x = -drag.width + (x + drag.width) * 0.25;   // 滑過頭之後變重
    drag.last = drag.at; drag.at = x;
    drag.face.style.transform = `translateX(${x}px)`;
  });
  function release(event, cancelled) {
    if (!drag || event.pointerId !== drag.id) return;
    const done = drag; drag = null;
    // 動作滑開著的時候，點那一列（沒有滑動）是把它收回去：也在放開時就做
    if (!done.moved) { if (!cancelled && done.row === swiped) { setDrawer(done.row, false); swallow = performance.now(); } return; }
    done.row.classList.remove('is-dragging');
    // 放開時還在往左、或已經滑過一半，就停在打開；否則收回去
    const leftward = done.last != null && done.at < done.last;
    const open = !cancelled && (done.at < -done.width * 0.5 || (leftward && done.at < -18));
    setDrawer(done.row, open);
    swallow = performance.now();
  }
  root.addEventListener('pointerup', event => {
    release(event, false);
    // 滑出來的動作在手指放開時就做，不等瀏覽器補發的點擊：快速一撥之後，瀏覽器會把下一下點擊吃掉（Chromium 實測）。
    // 做完會重畫，隨後補發的點擊會落在換上來的東西上，所以一併不算。
    const act = pressedAct; pressedAct = null;
    if (act && event.target.closest('.sw-act') === act) { swallow = performance.now(); runAct(act); }
  });
  root.addEventListener('pointercancel', event => { pressedAct = null; release(event, true); });
  root.addEventListener('dragstart', event => { if (event.target.closest('.sw-face')) event.preventDefault(); });
  // 滑完跟著來的那一下點擊不算；動作滑開著的時候，點那一列是把它收回去
  root.addEventListener('click', event => {
    if (event.detail !== 0 && performance.now() - swallow < 350) { event.preventDefault(); event.stopPropagation(); return; }
    if (swiped && rowOf(event.target) === swiped && !event.target.closest('.sw-acts, .sw-more')) { event.preventDefault(); event.stopPropagation(); setDrawer(swiped, false); }
  }, true);
  // 鍵盤：在一列上按 ← 把動作滑出來，Esc 或 → 收回去
  root.addEventListener('keydown', event => {
    const row = rowOf(event.target);
    if (!row) return;
    if (event.key === 'ArrowLeft' && event.target.matches('.sw-open, a.hv-name')) { setDrawer(row, true); row.querySelector('.sw-act').focus(); event.preventDefault(); }
    else if ((event.key === 'Escape' || event.key === 'ArrowRight') && event.target.matches('.sw-act')) { setDrawer(row, false); row.querySelector('.sw-open').focus(); event.preventDefault(); }
  });

  function runAct(act) {
    const item = find(rowOf(act).dataset.code), kind = act.dataset.act;
    if (kind === 'edit') { openCode = editCode = item.code; namingCode = null; render(); return; }
    const index = stocks.indexOf(item), back = kept => () => { if (!stocks.includes(item)) stocks.splice(index, 0, item); Object.assign(item, kept); };
    if (kind === 'delete') {
      const kept = { holding: item.holding };
      item.holding = null;
      if (editCode === item.code) editCode = null;
      say(`已刪除 ${item.name} 的庫存`, back(kept));
    } else {
      const kept = { watch: item.watch };
      item.watch = null;
      say(`已把 ${item.name} 移出自選`, back(kept));
    }
    if (!item.holding && !item.watch) { stocks.splice(index, 1); if (openCode === item.code) openCode = null; }
    render();
  }

  // 展開完之後才讓選單可以超出這一列的框
  function settle(row) { clearTimeout(row.__settle); row.__settle = setTimeout(() => row.classList.add('is-settled'), 480); }

  root.addEventListener('click', event => {
    const target = event.target;
    const more = target.closest('.sw-more');
    if (more) { const row = rowOf(more); setDrawer(row, row !== swiped); return; }
    const act = target.closest('.sw-act');
    if (act) { runAct(act); return; }   // 鍵盤按下去的（手指與滑鼠在放開時已經做了）
    if (target.closest('[data-undo]')) { if (undo) undo(); say('已還原'); render(); return; }
    const opener = target.closest('.sw-open');
    if (opener) {
      const row = rowOf(opener), code = row.dataset.code, open = openCode !== code;
      // 一次只開一檔（照正式的頁面）
      list.querySelectorAll('.sw-row.is-open').forEach(other => { if (other !== row) { other.classList.remove('is-open', 'is-settled'); other.querySelector('.hv-panel').setAttribute('inert', ''); other.querySelector('.sw-open').setAttribute('aria-expanded', 'false'); } });
      openCode = open ? code : null;
      if (!open && (editCode === code || namingCode === code)) { editCode = namingCode = null; render(); return; }
      row.classList.toggle('is-open', open);
      row.classList.remove('is-settled');
      if (open) settle(row); else clearTimeout(row.__settle);
      row.querySelector('.hv-panel').toggleAttribute('inert', !open);
      opener.setAttribute('aria-expanded', String(open));
      opener.setAttribute('aria-label', `${open ? '收起' : '展開'} ${find(code).name} 的明細`);
      return;
    }
    if (target.closest('[data-cancel]')) { editCode = null; render(); return; }
    const pick = target.closest('.sw-menu [data-group]');
    if (pick) { const item = find(rowOf(pick).dataset.code); item.watch.group = pick.dataset.group; say(pick.dataset.group ? `${item.name} 放進「${pick.dataset.group}」` : `${item.name} 改成未分組`); render(); return; }
    if (target.closest('[data-new-group]')) { namingCode = rowOf(target).dataset.code; render(); return; }
    const cell = target.closest('.sw-set .cell');
    if (cell) { find(rowOf(cell).dataset.code).watch.horizon = cell.dataset.horizon; render(); return; }
    const rename = target.closest('[data-rename-start]');
    if (rename) { renaming = rename.dataset.renameStart; render(); return; }
    // 上面那一排鍵：全部／庫存／自選
    const key = target.closest('#listFilter button');
    if (key) { key.parentElement.querySelectorAll('button').forEach(other => other.setAttribute('aria-pressed', String(other === key))); filter = key.dataset.filter; renaming = null; render(); return; }
    // 「＋」
    const add = target.closest('.sv-add');
    if (add) { setAdd(add.getAttribute('aria-expanded') !== 'true'); return; }
    const adding = target.closest('[data-add]');
    if (adding) addStock(adding.dataset.add);
  });
  root.addEventListener('change', event => {
    const box = event.target.closest('.pf-watch-toggle input');
    if (box) { find(rowOf(box).dataset.code).watch.watching = box.checked; render(); }
  });
  root.addEventListener('submit', event => {
    event.preventDefault();
    const form = event.target;
    if (form.matches('.sw-edit')) {
      const item = find(form.dataset.code), shares = Number(form.shares.value), cost = Number(form.cost.value);
      if (!(shares > 0) || !(cost > 0)) return;
      const kept = { ...item.holding };
      item.holding = { shares, cost, note: form.note.value.trim() };
      editCode = null;
      say(`已更新 ${item.name} 的庫存`, () => { item.holding = kept; });
    } else if (form.matches('.sw-new')) {
      const item = find(form.dataset.code), name = form.querySelector('input').value.trim();
      namingCode = null;
      if (name) { item.watch.group = name; say(`${item.name} 放進「${name}」`); }
    } else if (form.dataset.rename) {
      const from = form.dataset.rename, to = form.querySelector('input').value.trim();
      renaming = null;
      if (to && to !== from) { stocks.forEach(item => { if (item.watch && item.watch.group === from) item.watch.group = to; }); say(`「${from}」改名為「${to}」`); }
    }
    render();
  });
  // 改名、取新群組名的輸入框：離開就算了（沒有按確定不改）
  root.addEventListener('focusout', event => {
    if (!event.target.matches('.sw-group input, .sw-new input')) return;
    setTimeout(() => { if (!root.contains(document.activeElement) || !document.activeElement.closest('.sw-new, [data-rename]')) { if (renaming || namingCode) { renaming = namingCode = null; render(); } } }, 120);
  });

  // ---- 「＋」----
  const addButton = root.querySelector('.sv-add'), addPanel = document.getElementById('addForm'), more = root.querySelector('.sw-add-more'), addNote = root.querySelector('.sw-add-note');
  function setAdd(open) {
    addButton.setAttribute('aria-expanded', String(open));
    addPanel.classList.toggle('is-open', open);
    addPanel.firstElementChild.toggleAttribute('inert', !open);
    if (!open) { more.hidden = true; root.querySelector('[data-add="holding"]').setAttribute('aria-expanded', 'false'); addNote.textContent = ''; }
  }
  function addStock(kind) {
    const fields = addPanel.querySelectorAll('input'), code = fields[0].value.trim();
    if (kind === 'holding') { const open = more.hidden; more.hidden = !open; root.querySelector('[data-add="holding"]').setAttribute('aria-expanded', String(open)); return; }
    const known = find(code) || (KNOWN[code] && { code, name: KNOWN[code][0], close: KNOWN[code][1], holding: null, watch: null });
    if (!known) { addNote.textContent = '樣品只認得這幾個代號：' + Object.keys(KNOWN).join('、') + '，以及清單上已經有的。'; return; }
    if (kind === 'watch') {
      if (known.watch) { addNote.textContent = `${known.name} 已經在自選了。`; return; }
      known.watch = { horizon: 'none', group: '' };
      say(`已把 ${known.name} 加入自選`);
    } else {
      const shares = Number(fields[1].value), cost = Number(fields[2].value);
      if (!(shares > 0) || !(cost > 0)) { addNote.textContent = '股數與平均成本要填大於 0 的數字。'; return; }
      if (known.close == null) { addNote.textContent = `樣品沒有 ${known.name} 的收盤價，沒辦法算市值。`; return; }
      known.holding = { shares, cost, note: fields[3].value.trim() };
      say(`已儲存 ${known.name} 的庫存`);
    }
    if (!stocks.includes(known)) stocks.push(known);
    fields.forEach(field => { field.value = ''; });
    setAdd(false);
    render();
  }

  render();
})();
"""

FORM = '''<div class="pf-form sw-add-form"><label>股票代號<input inputmode="text" autocomplete="off" maxlength="6" placeholder="例如 2317"></label>
  <button type="button" data-add="watch">加入自選</button><button type="button" class="sw-ghost" data-add="holding" aria-expanded="false">新增庫存</button>
  <div class="sw-add-more" hidden><label>股數<input type="number" inputmode="numeric"></label><label>平均成本<input type="number" inputmode="decimal" step="any"></label>
    <label class="wide">備註<input maxlength="200" placeholder="選填"></label><button type="button" class="wide" data-add="save">儲存庫存</button></div>
  <p class="sw-add-note" role="status"></p></div>'''
BODY = f'''    <section class="pf-summary" aria-label="自選與庫存">
      <p class="hv-total"></p>
      <div class="sv-bar">
        <div class="hm-cats" id="listFilter" role="group" aria-label="要看哪些">
          <button type="button" data-filter="all" aria-pressed="true">全部<small id="countAll"></small></button>
          <button type="button" data-filter="holding" aria-pressed="false">庫存<small id="countHolding"></small></button>
          <button type="button" data-filter="watch" aria-pressed="false">自選<small id="countWatch"></small></button>
        </div>
        <button type="button" class="sv-add" aria-expanded="false" aria-controls="addForm" aria-label="新增自選或庫存">{base.PLUS}</button>
      </div>
      <div class="sv-form" id="addForm"><div inert><div class="sv-form-in">{FORM}</div></div></div>
      <p class="sw-status" role="status"></p>
      <ol class="hv-rows"></ol>
    </section>
    <script type="application/json" id="sampleData">{json.dumps(STOCKS, ensure_ascii=False)}</script>'''

html = base.page("自選與庫存：手勢版", "點名稱進個股頁；向左滑露出修改、刪除、移出；自選照自己取名的群組分段；「＋」只有一格代號與兩顆鍵。", BODY)
html = html.replace('<link rel="stylesheet" href="../../../web/bold.css">', '<link rel="stylesheet" href="../../../web/chart-studies.css">\n<link rel="stylesheet" href="../../../web/bold.css">')
start = html.index('<script>' + base.SCRIPT)
end = start + len('<script>' + base.SCRIPT + '</script>')
html = html[:start] + f'<script>{SCRIPT}</script>' + html[end:]   # 這一頁整份清單由自己的程式畫，不用前兩個樣品的程式
html = html.replace('</style>', STYLE + '</style>', 1)
(HERE / "portfolio-gesture.html").write_text(html, encoding="utf-8", newline="\n")
print("gesture ok")
