"""產生自選與庫存頁「整合」的兩個可丟棄樣品（本人 2026-11-16：「自選股和庫存的葉面我覺得太長可以幫我整合一下」）。
兩頁共用同一批列與樣式，所以用這支腳本產生，不手寫兩份：
  portfolio-one-list.html（短網址 /pa）：庫存與自選併成一份清單，一檔只出現一次，上面三顆鍵篩「全部／庫存／自選」。
  portfolio-two-tabs.html（短網址 /pb）：分成「庫存／自選」兩個分頁，一次看一邊。
兩頁都把新增的表單收進「＋」鍵、把每一檔的設定收進點開的那一列。數字取自預覽的樣本資料；樣品上按儲存、刪除不會真的寫資料。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-portfolio-samples.py"""
from pathlib import Path

HERE = Path(__file__).resolve().parent

HOLDINGS = [
    dict(code="2330", name="台積電", base=32.47, gain=41.58, ret="+128.06%", note="核心部位",
         facts=[("市值", "$2,235,000", ""), ("成本", "$980,000", ""), ("賺", "+$1,255,000", "up"), ("占庫存", "74.0%", ""), ("最新收盤", "2,235", ""), ("股數・均價", "1,000 股 · 980", "")]),
    dict(code="2603", name="長榮", base=14.25, gain=2.02, ret="+14.19%", note="短線",
         facts=[("市值", "$491,000", ""), ("成本", "$430,000", ""), ("賺", "+$61,000", "up"), ("占庫存", "16.3%", ""), ("最新收盤", "245.5", ""), ("股數・均價", "2,000 股 · 215", "")]),
    dict(code="0050", name="元大台灣50", base=5.80, gain=3.88, ret="+66.87%", note="",
         facts=[("市值", "$292,350", ""), ("成本", "$175,200", ""), ("賺", "+$117,150", "up"), ("占庫存", "9.7%", ""), ("最新收盤", "97.45", ""), ("股數・均價", "3,000 股 · 58.4", "")]),
]
WATCH = [dict(code="6669", name="緯穎", horizon="none"), dict(code="2603", name="長榮", horizon="short"), dict(code="2330", name="台積電", horizon="long")]
HORIZONS = [("none", "未分類"), ("short", "短期"), ("long", "長期")]
WORD = dict(HORIZONS)
watch_of = {item["code"]: item for item in WATCH}


def settings(code, name, horizon):
    """點開之後的自選設定：分類三選一、關注開關。"""
    cells = "".join(f'<button type="button" class="cell" data-horizon="{key}" aria-pressed="{str(key == horizon).lower()}"><span class="cell-face"></span><span class="cell-name">{label}</span></button>' for key, label in HORIZONS)
    return (f'<div class="sv-set"><span>自選分類</span><div class="key-strip sv-strip" role="group" aria-label="{code} 自選分類">{cells}</div>'
            f'<label class="pf-watch-toggle"><input type="checkbox" role="switch" aria-label="關注 {code} {name}"><span>關注</span></label></div>')


def actions(code, name, holding, watching):
    buttons = []
    if holding:
        buttons.append(f'<button type="button" class="pf-delete" aria-label="刪除庫存 {code} {name}">刪除庫存</button>')
    if watching:
        buttons.append(f'<button type="button" class="pf-delete" aria-label="從自選股移出 {code} {name}">移出自選</button>')
    return f'<div class="sv-acts"><a href="/stock.html?code={code}">看 {name} 的個股頁 ›</a><span>{"".join(buttons)}</span></div>'


def holding_row(item, merged):
    """庫存的一列。merged：併成一份清單時，這一檔如果也在自選，名稱下面寫出分類、點開有自選設定。"""
    code, name = item["code"], item["name"]
    watch = watch_of.get(code) if merged else None
    under = f'{code}' + (f'<em>自選・{WORD[watch["horizon"]]}</em>' if watch else '')
    facts = "".join(f'<li><span>{title}</span><b class="{tone}">{text}</b></li>' for title, text, tone in item["facts"])
    note = f'<p class="hv-note">備註：{item["note"]}</p>' if item["note"] else ''
    kinds = "holding watch" if watch else "holding"
    return f'''<li class="hv-row depth-row on-page" data-code="{code}" data-kinds="{kinds}" data-state="bull">
  <button class="hv-top" type="button" aria-expanded="false"><span class="hv-name"><strong>{name}</strong><span>{under}</span></span>
    <span class="hv-bar" aria-hidden="true"><i class="base" style="--w:{item["base"]}"></i><i class="gain" style="--w:{item["gain"]}"></i></span>
    <span class="hv-ret num up">{item["ret"]}</span><span class="chev" aria-hidden="true"></span></button>
  <div class="hv-panel depth-panel" inert><div class="hv-clip"><div class="hv-inner">
    <ul class="hv-facts">{facts}</ul>{note}
    {settings(code, name, watch["horizon"]) if watch else ""}
    {actions(code, name, True, bool(watch))}
  </div></div></div>
</li>'''


def watch_row(item):
    """只在自選、沒有庫存的一列：沒有橫條與賺賠，右邊寫分類。"""
    code, name, horizon = item["code"], item["name"], item["horizon"]
    return f'''<li class="hv-row depth-row on-page sv-watch" data-code="{code}" data-kinds="watch" data-state="none">
  <button class="hv-top" type="button" aria-expanded="false"><span class="hv-name"><strong>{name}</strong><span>{code}</span></span>
    <span class="sv-tag">{WORD[horizon]}</span><span class="chev" aria-hidden="true"></span></button>
  <div class="hv-panel depth-panel" inert><div class="hv-clip"><div class="hv-inner">
    {settings(code, name, horizon)}
    {actions(code, name, False, True)}
  </div></div></div>
</li>'''


TOTAL = '<p class="hv-total"><strong class="num" aria-label="目前市值">$3,018,350</strong><span>成本 <b class="num">$1,585,200</b></span><span>賺 <b class="num up">+$1,433,150</b>（<b class="num up">+90.41%</b>）</span></p>'
HOLDING_FORM = '''<form class="pf-form pf-holding-form"><label>股票代號<input inputmode="text" autocomplete="off" maxlength="6" placeholder="例如 2330"></label>
  <label>股數<input type="number" inputmode="numeric"></label><label>平均成本<input type="number" inputmode="decimal"></label>
  <label class="pf-note-field">備註<input maxlength="200" placeholder="選填，最多 200 字"></label><button type="submit">儲存庫存</button></form>'''
WATCH_FORM = '''<form class="pf-form pf-watch-form"><label>股票代號<input inputmode="text" autocomplete="off" maxlength="6" placeholder="例如 2330"></label>
  <label>分類<select><option>未分類</option><option>短期</option><option>長期</option></select></label><button type="submit">加入或更新</button></form>'''
PLUS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'

STYLE = '''
  body { margin: 0; }
  #sample .pf-title-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 24px 0 16px; }
  #sample .pf-title-row h1 { font-size: 26px; }
  #sample .pf-freshness { margin: 0; white-space: nowrap; }
  #sample .pf-summary { padding-top: 0; border-top: 0; }
  #sample .pf-summary .hv-total { margin: 0 0 16px; }
  /* 一排鍵＋右邊的「＋」：鍵用共用的分類鍵（槽與鍵），「＋」是一顆現成的鍵 */
  .sv-bar { display: flex; align-items: stretch; gap: 8px; margin-bottom: 12px; }
  #sample .sv-bar .hm-cats { flex: 1; margin-top: 0; }
  #sample .sv-bar .hm-cats button small { margin-left: 4px; font: 400 12px var(--mono); color: var(--faint); }
  .sv-add { flex: none; width: 50px; min-height: 50px; display: grid; place-items: center; border: 0; border-radius: var(--r-track); background: var(--key); color: var(--ink); box-shadow: var(--key-shadow); cursor: pointer; transition: transform var(--t-press) linear, box-shadow var(--t-press) linear; }
  .sv-add svg { width: 20px; height: 20px; transition: transform 220ms var(--ease-depth); }
  .sv-add:active { transform: translateY(calc(var(--key-thick) - 1px)); box-shadow: var(--key-shadow-press); }
  .sv-add[aria-expanded="true"] { background: var(--well); box-shadow: var(--well-shadow); }
  .sv-add[aria-expanded="true"] svg { transform: rotate(45deg); }
  .sv-add:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
  /* 新增的表單：平常收著，按「＋」才展開 */
  .sv-form { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 320ms var(--ease-depth); }
  .sv-form.is-open { grid-template-rows: 1fr; }
  .sv-form > div { overflow: hidden; min-height: 0; }
  .sv-form .sv-form-in { padding: 4px 0 16px; }
  .sv-form .sv-kind { display: flex; gap: 6px; margin-bottom: 12px; }
  .sv-form h3 { margin: 0 0 10px; font: 600 14px var(--sans); color: var(--ink); }
  /* 只在自選的一列：沒有橫條，右邊寫分類 */
  #sample .sv-watch .hv-top { grid-template-columns: minmax(0, 1fr) auto 12px; }
  .sv-tag { font: 12px var(--sans); color: var(--dim); white-space: nowrap; }
  #sample .hv-name em { margin-left: 6px; font: 400 11px var(--sans); font-style: normal; color: var(--faint); }
  /* 點開之後的自選設定與下面的動作 */
  .sv-set { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; padding-top: 12px; border-top: 1px solid var(--hair); color: var(--faint); font-size: 12px; }
  .sv-watch .sv-set { padding-top: 0; border-top: 0; }
  #sample .sv-strip { justify-content: flex-start; }
  #sample .sv-strip .cell { max-width: 72px; min-height: 40px; }
  #sample .sv-strip .cell-name { font: 500 13px var(--sans); color: var(--ink); }
  .sv-acts { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--hair); font-size: 13px; }
  .sv-acts a { color: var(--ink); }
  .sv-acts { flex-wrap: wrap; }
  .sv-acts span { display: flex; gap: 8px; }
  #sample .sv-acts .pf-delete { white-space: nowrap; }
  #sample .pf-summary .hv-rows { margin-top: 0; }
  #sample .hv-row[hidden] { display: none; }
  .sv-empty { margin: 12px 0 0; color: var(--faint); font-size: 13px; }
  @media (prefers-reduced-motion: reduce) { .sv-form, .sv-add svg { transition: none; } }
  @media (max-width: 760px) { .sv-set { grid-template-columns: minmax(0, 1fr) auto; } .sv-set > span:first-child { grid-column: 1 / -1; } }
'''

SCRIPT = '''
(function () {
  'use strict';
  const root = document.getElementById('sample');
  // 點一列：展開或收起
  root.addEventListener('click', event => {
    const top = event.target.closest('.hv-top');
    if (top) {
      const row = top.parentElement, open = !row.classList.contains('is-open');
      row.classList.toggle('is-open', open);
      top.setAttribute('aria-expanded', String(open));
      row.querySelector('.hv-panel').toggleAttribute('inert', !open);
      return;
    }
    // 自選分類三選一
    const cell = event.target.closest('.hv-row .sv-strip .cell');
    if (cell) {
      cell.parentElement.querySelectorAll('.cell').forEach(other => other.setAttribute('aria-pressed', String(other === cell)));
      const row = cell.closest('.hv-row'), word = cell.textContent;
      const mark = row.querySelector('.hv-name em'), tag = row.querySelector('.sv-tag');
      if (mark) mark.textContent = '自選・' + word;
      if (tag) tag.textContent = word;
      return;
    }
    // 「＋」：展開或收起新增的表單
    const add = event.target.closest('.sv-add');
    if (add) {
      const open = add.getAttribute('aria-expanded') !== 'true';
      add.setAttribute('aria-expanded', String(open));
      document.getElementById(add.getAttribute('aria-controls')).classList.toggle('is-open', open);
      document.getElementById(add.getAttribute('aria-controls')).firstElementChild.toggleAttribute('inert', !open);
      return;
    }
    // 表單裡選「加入自選／新增庫存」
    const kind = event.target.closest('.sv-kind .cell');
    if (kind) {
      kind.parentElement.querySelectorAll('.cell').forEach(other => other.setAttribute('aria-pressed', String(other === kind)));
      root.querySelectorAll('[data-form]').forEach(form => { form.hidden = form.dataset.form !== kind.dataset.kind; });
      return;
    }
    // 上面那一排鍵：篩選（一份清單）或換分頁（兩個分頁）
    const key = event.target.closest('.hm-cats button');
    if (key) {
      key.parentElement.querySelectorAll('button').forEach(other => other.setAttribute('aria-expanded', String(other === key)));
      if (key.dataset.filter) {
        root.querySelectorAll('.hv-row').forEach(row => { row.hidden = key.dataset.filter !== 'all' && !row.dataset.kinds.split(' ').includes(key.dataset.filter); });
      } else {
        root.querySelectorAll('[data-tab]').forEach(tab => { tab.hidden = tab.dataset.tab !== key.dataset.show; });
        root.querySelectorAll('.sv-add').forEach(add => { if (add.getAttribute('aria-expanded') === 'true') add.click(); });
      }
    }
  });
  // 樣品上的儲存、刪除不會真的寫資料
  root.addEventListener('submit', event => { event.preventDefault(); const add = event.target.closest('.sv-form'); if (add) document.querySelector(`[aria-controls="${add.id}"]`).click(); });
})();
'''


def page(title, comment, body):
    return f'''<!doctype html>
<html lang="zh-Hant" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>樣品：{title}</title>
<!-- 可丟棄的樣品（由 build-portfolio-samples.py 產生，不要手改）：{comment}
     直接吃自選與庫存頁自己的樣式；陰影、圓角、顏色只用網站現成的。樣品上按儲存、刪除不會真的寫資料。 -->
<link rel="stylesheet" href="../../../web/minimal.css">
<link rel="stylesheet" href="../../../web/bold.css">
<link rel="stylesheet" href="../../../web/portfolio.css">
<style>{STYLE}</style>
</head>
<body class="bd pf">
<div class="pf-shell" id="sample">
  <header class="pf-bar"><a class="pf-back" href="/"><span aria-hidden="true">←</span> 大盤首頁</a></header>
  <main>
    <div class="pf-title-row"><h1>自選股與庫存</h1><p class="pf-freshness">庫存價格至 2026-11-09</p></div>
{body}
  </main>
</div>
<script src="../../../web/chart-menus.js"></script>
<script>{SCRIPT}</script>
</body>
</html>
'''


def kind_form(default):
    """一份清單的新增：先選是加入自選還是新增庫存，再填對應的欄位。"""
    cells = "".join(f'<button type="button" class="cell" data-kind="{key}" aria-pressed="{str(key == default).lower()}"><span class="cell-face"></span><span class="cell-name">{label}</span></button>'
                    for key, label in (("watch", "加入自選"), ("holding", "新增庫存")))
    return (f'<div class="key-strip sv-strip sv-kind" role="group" aria-label="要新增的是">{cells}</div>'
            f'<div data-form="watch"{"" if default == "watch" else " hidden"}>{WATCH_FORM}</div><div data-form="holding"{"" if default == "holding" else " hidden"}>{HOLDING_FORM}</div>')


only_watch = [item for item in WATCH if item["code"] not in {h["code"] for h in HOLDINGS}]
ONE_LIST = f'''    <section class="pf-summary" aria-label="自選與庫存">
      {TOTAL}
      <div class="sv-bar">
        <div class="hm-cats" role="group" aria-label="要看哪些">
          <button type="button" data-filter="all" aria-expanded="true">全部<small>{len(HOLDINGS) + len(only_watch)}</small></button>
          <button type="button" data-filter="holding" aria-expanded="false">庫存<small>{len(HOLDINGS)}</small></button>
          <button type="button" data-filter="watch" aria-expanded="false">自選<small>{len(WATCH)}</small></button>
        </div>
        <button type="button" class="sv-add" aria-expanded="false" aria-controls="addForm" aria-label="新增自選或庫存">{PLUS}</button>
      </div>
      <div class="sv-form" id="addForm"><div inert><div class="sv-form-in">{kind_form("watch")}</div></div></div>
      <ol class="hv-rows">
{chr(10).join(holding_row(item, True) for item in HOLDINGS)}
{chr(10).join(watch_row(item) for item in only_watch)}
      </ol>
    </section>'''

TWO_TABS = f'''    <section class="pf-summary" aria-label="自選與庫存">
      <div class="sv-bar">
        <div class="hm-cats" role="group" aria-label="要看哪一邊">
          <button type="button" data-show="holding" aria-expanded="true">庫存<small>{len(HOLDINGS)}</small></button>
          <button type="button" data-show="watch" aria-expanded="false">自選<small>{len(WATCH)}</small></button>
        </div>
      </div>
      <div data-tab="holding">
        <div class="sv-bar">{TOTAL.replace('<p class="hv-total">', '<p class="hv-total" style="flex:1;margin:0;align-self:center">')}
          <button type="button" class="sv-add" aria-expanded="false" aria-controls="addHolding" aria-label="新增或更新庫存">{PLUS}</button></div>
        <div class="sv-form" id="addHolding"><div inert><div class="sv-form-in"><h3>新增或更新庫存</h3>{HOLDING_FORM}</div></div></div>
        <ol class="hv-rows">
{chr(10).join(holding_row(item, False) for item in HOLDINGS)}
        </ol>
      </div>
      <div data-tab="watch" hidden>
        <div class="sv-bar"><p class="sv-empty" style="flex:1;margin:0;align-self:center">關注 0／20</p>
          <button type="button" class="sv-add" aria-expanded="false" aria-controls="addWatch" aria-label="加入自選">{PLUS}</button></div>
        <div class="sv-form" id="addWatch"><div inert><div class="sv-form-in"><h3>加入自選</h3>{WATCH_FORM}</div></div></div>
        <ol class="hv-rows">
{chr(10).join(watch_row(item) for item in WATCH)}
        </ol>
      </div>
    </section>'''

(HERE / "portfolio-one-list.html").write_text(page("自選與庫存併成一份清單", "庫存與自選併成一份清單，一檔只出現一次；上面三顆鍵篩全部／庫存／自選，新增收進「＋」。", ONE_LIST), encoding="utf-8", newline="\n")
(HERE / "portfolio-two-tabs.html").write_text(page("自選與庫存分成兩個分頁", "分成庫存／自選兩個分頁，一次看一邊；各自的新增收進「＋」。", TWO_TABS), encoding="utf-8", newline="\n")
print("ok")
