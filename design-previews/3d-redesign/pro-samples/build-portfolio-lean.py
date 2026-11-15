"""產生自選與庫存頁「更精簡」的樣品 portfolio-lean.html（短網址 /pc）。
本人 2026-11-16 選了一份清單、看過正式版之後說「還要調整」：點開一檔之後東西太多、修改／刪除／移出那三顆按鈕、「＋」展開的新增表單。
這一版的做法：
  點開之後只留三個數字（市值、賺賠、股數・均價）與備註；
  自選的分類、關注、移出收進一顆小鍵「自選 長期」的選單；修改與刪除收進「⋯」；
  「＋」只有一格代號與兩顆鍵，選新增庫存才多出股數與成本。
小鍵與浮出的選單用網站現成的那一套（K 線上週期、均線用的，chart-menus.js 與 chart-studies.css）。
列、總數、樣式沿用 build-portfolio-samples.py 的；樣品上按加入、儲存、刪除不會真的寫資料。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-portfolio-lean.py"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("samples", HERE / "build-portfolio-samples.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)   # 會順便重新產生前兩個樣品（內容不變）
HOLDINGS, WATCH, HORIZONS, WORD, watch_of, only_watch = base.HOLDINGS, base.WATCH, base.HORIZONS, base.WORD, base.watch_of, base.only_watch

LEAN_STYLE = """
  /* 點開之後：三個數字排一行 */
  #sample .sv-facts3 { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 12px; }
  /* 一行小鍵：左邊自選的設定與個股頁，右邊「⋯」 */
  .sv-chips { display: flex; align-items: center; gap: 12px; padding-top: 12px; border-top: 1px solid var(--hair); }
  .sv-watch .sv-chips { padding-top: 0; border-top: 0; }
  .sv-pop { position: relative; display: inline-flex; }
  .sv-pop.sv-right { margin-left: auto; }
  #sample .sv-chips .chart-chip b { font-weight: 600; color: var(--dim); }
  .sv-link { color: var(--dim); font-size: 13px; text-decoration: none; white-space: nowrap; }
  .sv-link:hover { color: var(--ink); }
  /* 選單直排；左邊那一顆的選單靠左展開 */
  #sample .sv-menu { flex-direction: column; flex-wrap: nowrap; align-items: stretch; min-width: 148px; }
  #sample .sv-pop:not(.sv-right) .sv-menu { left: 0; right: auto; transform-origin: 20% 0; }
  #sample .sv-menu button { min-height: 40px; padding: 0 12px; border: 0; border-radius: var(--r-key); background: transparent; color: var(--ink); font: 500 14px var(--sans); text-align: left; white-space: nowrap; cursor: pointer; }
  #sample .sv-menu button[aria-checked="true"] { background: var(--ink); color: var(--bg); font-weight: 700; }
  #sample .sv-menu .sv-danger { color: var(--up); }
  #sample .sv-menu small { margin-left: 6px; font: 400 12px var(--mono); color: var(--faint); }
  #sample .sv-menu hr { width: 100%; height: 1px; margin: 4px 0; border: 0; background: var(--hair); }
  /* 選單要能蓋到下面的列、不被這一列的框切掉：展開完之後才放開，開著的那一列疊在上面 */
  #sample .hv-row.is-open { position: relative; z-index: 2; }
  #sample .hv-row.is-settled .hv-clip { overflow: visible; }
  /* 「＋」：一格代號、兩顆鍵；選新增庫存才多出股數與成本 */
  #sample .sv-lean-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 4px 0; border-bottom: 0; }
  #sample .sv-lean-form > label:first-child, #sample .sv-lean-form .wide { grid-column: 1 / -1; }
  #sample .sv-lean-form > button { grid-column: auto; }   /* 兩顆鍵並排（頁面原本的規則在窄螢幕把表單的按鈕排成一欄） */
  #sample .sv-lean-form button.sv-ghost { background: transparent; color: var(--ink); border: 1px solid var(--line); }
  #sample .sv-lean-form button.sv-ghost[aria-expanded="true"] { background: var(--well); box-shadow: var(--well-shadow); border-color: transparent; }
  #sample .sv-more { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  #sample .sv-more[hidden] { display: none; }
"""

LEAN_SCRIPT = """
(function () {
  'use strict';
  const root = document.getElementById('sample');
  // 展開完之後才讓選單可以超出這一列的框（展開的途中還是要切掉，不然內容會先露出來）
  new MutationObserver(records => records.forEach(record => {
    const row = record.target;
    if (!row.classList.contains('hv-row')) return;
    clearTimeout(row.__settle);
    if (row.classList.contains('is-open')) { if (!row.classList.contains('is-settled')) row.__settle = setTimeout(() => row.classList.add('is-settled'), 480); }
    else if (row.classList.contains('is-settled')) row.classList.remove('is-settled');
  })).observe(root, { subtree: true, attributes: true, attributeFilter: ['class'] });
  root.addEventListener('click', event => {
    // 選單裡換自選的分類：小鍵上的字、名稱下面與右邊的字跟著變
    const pick = event.target.closest('.sv-menu [data-horizon]');
    if (pick) {
      const row = pick.closest('.hv-row'), word = pick.textContent;
      pick.parentElement.querySelectorAll('[data-horizon]').forEach(other => other.setAttribute('aria-checked', String(other === pick)));
      row.querySelector('.sv-chips .chart-chip b').textContent = word;
      const mark = row.querySelector('.hv-name em'), tag = row.querySelector('.sv-tag');
      if (mark) mark.textContent = '自選・' + word;
      if (tag) tag.textContent = word;
      return;
    }
    const act = event.target.closest('.sv-menu [data-act]');
    if (act && act.dataset.act === 'watching') { act.setAttribute('aria-checked', String(act.getAttribute('aria-checked') !== 'true')); return; }
    if (act && act.dataset.act === 'edit') {
      // 修改：把這一檔的數字帶進「＋」的表單
      const row = act.closest('.hv-row'), add = root.querySelector('.sv-add'), fields = root.querySelectorAll('.sv-lean-form input');
      if (add.getAttribute('aria-expanded') !== 'true') add.click();
      root.querySelector('.sv-more').hidden = false;
      root.querySelector('[data-add="holding"]').setAttribute('aria-expanded', 'true');
      [row.dataset.code, row.dataset.shares, row.dataset.cost, row.dataset.note].forEach((value, index) => { fields[index].value = value || ''; });
      add.scrollIntoView({ block: 'nearest' });
      return;
    }
    const add = event.target.closest('[data-add]');
    if (add && add.dataset.add === 'holding') {
      const more = root.querySelector('.sv-more'), open = more.hidden;
      more.hidden = !open;
      add.setAttribute('aria-expanded', String(open));
    } else if (add) root.querySelector('.sv-add').click();   // 樣品：加入自選、儲存庫存只是把表單收回去
  });
})();
"""


def menu(code, horizon):
    """「自選 長期」那顆小鍵的選單：分類三選一、關注、移出自選。"""
    picks = "".join(f'<button type="button" role="radio" aria-checked="{str(key == horizon).lower()}" data-horizon="{key}">{label}</button>' for key, label in HORIZONS)
    return (f'<span class="sv-pop"><button type="button" class="chart-chip" aria-expanded="false" aria-controls="w-{code}">自選 <b>{WORD[horizon]}</b></button>'
            f'<div class="chart-menu sv-menu" id="w-{code}" data-menu-autoclose hidden>{picks}<hr>'
            f'<button type="button" role="menuitemcheckbox" aria-checked="false" data-act="watching">關注<small>0／20</small></button>'
            f'<button type="button" class="sv-danger" data-act="unwatch">移出自選</button></div></span>')


def chips(code, name, holding, watch):
    left = menu(code, watch["horizon"]) if watch else '<button type="button" class="chart-chip is-plain" data-act="watch">＋ 加入自選</button>'
    more = (f'<span class="sv-pop sv-right"><button type="button" class="chart-chip is-plain" aria-label="{name} 的其他動作" aria-expanded="false" aria-controls="m-{code}">⋯</button>'
            f'<div class="chart-menu sv-menu" id="m-{code}" data-menu-autoclose hidden><button type="button" data-act="edit">修改股數與成本</button>'
            f'<button type="button" class="sv-danger" data-act="delete">刪除庫存</button></div></span>') if holding else ''
    return f'<div class="sv-chips">{left}<a class="sv-link" href="/stock.html?code={code}">個股頁 ›</a>{more}</div>'


def holding_row(item):
    code, name = item["code"], item["name"]
    watch = watch_of.get(code)
    under = code + (f'<em>自選・{WORD[watch["horizon"]]}</em>' if watch else '')
    # 第三格寫「1,000・980」：手機一行三格放不下「1,000 股 · 980」
    facts = "".join(f'<li><span>{title}</span><b class="{tone}">{text.replace(" 股 · ", "・")}</b></li>' for title, text, tone in item["facts"] if title in ("市值", "賺", "股數・均價"))
    note = f'<p class="hv-note">備註：{item["note"]}</p>' if item["note"] else ''
    shares, cost = item["facts"][5][1].replace(",", "").split(" 股 · ")
    kinds = "holding watch" if watch else "holding"
    return f'''<li class="hv-row depth-row on-page" data-code="{code}" data-kinds="{kinds}" data-state="bull" data-shares="{shares}" data-cost="{cost}" data-note="{item["note"]}">
  <button class="hv-top" type="button" aria-expanded="false"><span class="hv-name"><strong>{name}</strong><span>{under}</span></span>
    <span class="hv-bar" aria-hidden="true"><i class="base" style="--w:{item["base"]}"></i><i class="gain" style="--w:{item["gain"]}"></i></span>
    <span class="hv-ret num up">{item["ret"]}</span><span class="chev" aria-hidden="true"></span></button>
  <div class="hv-panel depth-panel" inert><div class="hv-clip"><div class="hv-inner">
    <ul class="hv-facts sv-facts3">{facts}</ul>{note}
    {chips(code, name, True, watch)}
  </div></div></div>
</li>'''


def watch_row(item):
    code, name, horizon = item["code"], item["name"], item["horizon"]
    return f'''<li class="hv-row depth-row on-page sv-watch" data-code="{code}" data-kinds="watch" data-state="none">
  <button class="hv-top" type="button" aria-expanded="false"><span class="hv-name"><strong>{name}</strong><span>{code}</span></span>
    <span class="sv-tag">{WORD[horizon]}</span><span class="chev" aria-hidden="true"></span></button>
  <div class="hv-panel depth-panel" inert><div class="hv-clip"><div class="hv-inner">
    {chips(code, name, False, item)}
  </div></div></div>
</li>'''


FORM = '''<div class="pf-form sv-lean-form"><label>股票代號<input inputmode="text" autocomplete="off" maxlength="6" placeholder="例如 2330"></label>
  <button type="button" data-add="watch">加入自選</button><button type="button" class="sv-ghost" data-add="holding" aria-expanded="false">新增庫存</button>
  <div class="sv-more" hidden><label>股數<input type="number" inputmode="numeric"></label><label>平均成本<input type="number" inputmode="decimal"></label>
    <label class="wide">備註<input maxlength="200" placeholder="選填"></label><button type="button" class="wide" data-add="save">儲存庫存</button></div></div>'''
BODY = f'''    <section class="pf-summary" aria-label="自選與庫存">
      {base.TOTAL}
      <div class="sv-bar">
        <div class="hm-cats" role="group" aria-label="要看哪些">
          <button type="button" data-filter="all" aria-expanded="true">全部<small>{len(HOLDINGS) + len(only_watch)}</small></button>
          <button type="button" data-filter="holding" aria-expanded="false">庫存<small>{len(HOLDINGS)}</small></button>
          <button type="button" data-filter="watch" aria-expanded="false">自選<small>{len(WATCH)}</small></button>
        </div>
        <button type="button" class="sv-add" aria-expanded="false" aria-controls="addForm" aria-label="新增自選或庫存">{base.PLUS}</button>
      </div>
      <div class="sv-form" id="addForm"><div inert><div class="sv-form-in">{FORM}</div></div></div>
      <ol class="hv-rows">
{chr(10).join(holding_row(item) for item in HOLDINGS)}
{chr(10).join(watch_row(item) for item in only_watch)}
      </ol>
    </section>'''
html = base.page("自選與庫存更精簡的版本", "點開之後只留三個數字；自選的設定收進一顆小鍵的選單，修改與刪除收進「⋯」；「＋」只有一格代號與兩顆鍵。", BODY)
html = html.replace('<link rel="stylesheet" href="../../../web/bold.css">', '<link rel="stylesheet" href="../../../web/chart-studies.css">\n<link rel="stylesheet" href="../../../web/bold.css">')
html = html.replace('</style>', LEAN_STYLE + '</style>', 1).replace('</body>', f'<script>{LEAN_SCRIPT}</script>\n</body>')
(HERE / "portfolio-lean.html").write_text(html, encoding="utf-8", newline="\n")
print("lean ok")
