"""全站動態與元件設計升級（2026-11-24）的盤點工具：逐頁截圖、列出元件的按壓／滑過回饋、量每個互動實際跑了哪些動畫。

只讀已經開著的本機預覽（design-previews/3d-redesign/preview-server.mjs，預設 http://127.0.0.1:5310，樣本資料、即時行情離線），
不連正式站、不寫任何使用者資料。輸出放 .scratch/site-design-upgrade/（不入版控）。

用法（在工作目錄根目錄）：
    python design-previews/site-design-upgrade/audit_capture.py pages          # 每一頁整頁截圖＋元件清單
    python design-previews/site-design-upgrade/audit_capture.py motion         # 逐個互動：跑了哪些動畫＋連續畫面
    python design-previews/site-design-upgrade/audit_capture.py motion --only home-cat
    python design-previews/site-design-upgrade/audit_capture.py press          # 真的按住每一種鍵，比較按之前與按住時的樣子

「元件清單」怎麼量：把頁面上看得到、可以點的東西照（標籤＋類別）分組，每組記大小、有沒有 :active／:hover 規則與改了哪些屬性、
有沒有過場（transition）。規則是從頁面載入的樣式表逐條比對出來的，不是猜的。
「跑了哪些動畫」怎麼量：互動後 40 毫秒問瀏覽器 document.getAnimations()，記每一個動畫的對象、屬性、時間、曲線；
什麼都沒有就是瞬間切換。連續畫面是同一段時間內每隔一小段拍一張（無頭瀏覽器，時間是近似值）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / ".scratch" / "site-design-upgrade"
TAG = ""   # --tag：輸出檔名前面加一段（例如 after-），量樣品時才不會蓋掉改版前的那一份
SIZES = {
    "1280": dict(viewport={"width": 1280, "height": 820}, device_scale_factor=1),
    "390": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
}

READY = {
    "home": """async () => { for (let i = 0; i < 80; i++) { if (window.MinimalApp && window.MinimalApp.ready && document.querySelector('#mainChart canvas')) return true; await new Promise(r => setTimeout(r, 150)); } return false; }""",
    "stock": """async () => { for (let i = 0; i < 80; i++) { const c = document.getElementById('content'), o = document.getElementById('outside'); if ((c && !c.hidden && document.querySelector('#chartHost canvas')) || (o && !o.hidden)) return true; await new Promise(r => setTimeout(r, 150)); } return false; }""",
    "portfolio": """async () => { for (let i = 0; i < 80; i++) { const f = document.getElementById('portfolioFreshness'); if (f && f.textContent.trim() !== '讀取中') return true; await new Promise(r => setTimeout(r, 150)); } return false; }""",
    "plain": """async () => { await new Promise(r => setTimeout(r, 900)); return true; }""",
}
PAGES = {
    "home": ("/", "home"),
    "stock": ("/stock.html?code=2330", "stock"),
    "stock-etf": ("/stock.html?code=0050", "stock"),
    "stock-fund": ("/stock.html?code=BLKTENAU", "stock"),
    "portfolio": ("/portfolio.html", "portfolio"),
    "apply": ("/apply/", "plain"),
    "admin": ("/admin/", "plain"),
    "gemini": ("/gemini/", "plain"),
}

# 頁面上看得到、可以點的東西，照（標籤＋類別）分組；每組記大小、過場、:active／:hover 規則改了什麼
COMPONENTS_JS = r"""
() => {
  const rules = [];
  const walk = list => { for (const rule of list) { if (rule.cssRules && rule.type !== 1) { try { walk(rule.cssRules); } catch (_) {} } if (rule.type === 1 && rule.selectorText) { rules.push(rule); if (rule.cssRules && rule.cssRules.length) { try { walk(rule.cssRules); } catch (_) {} } } } };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch (_) {} }
  const pseudo = name => rules.filter(r => r.selectorText.includes(':' + name)).map(r => ({
    parts: r.selectorText.split(/,(?![^()]*\))/).map(s => s.trim()).filter(s => s.includes(':' + name)),
    props: [...r.style].filter(p => !p.startsWith('--')),
  }));
  const ACTIVE = pseudo('active'), HOVER = pseudo('hover'), FOCUS = pseudo('focus-visible');
  const strip = (sel, name) => sel.replace(new RegExp(':' + name + '\\b', 'g'), '').replace(/:is\(\)|:where\(\)|:not\(\)/g, '') || '*';
  const hits = (el, table, name) => {
    const props = new Set();
    for (const { parts, props: list } of table) for (const part of parts) {
      let base; try { base = strip(part, name); if (el.matches(base) || el.closest(base) === el) list.forEach(p => props.add(p)); else {
        // 規則寫在外層（例如 .row:has(> button:active)）：按的是裡面的鍵，變的是外層
        const host = el.parentElement && el.parentElement.closest(base.replace(/:has\([^)]*\)/g, ''));
        if (host && /:has\(/.test(part) && host.contains(el)) list.forEach(p => props.add('外層.' + p));
      } } catch (_) {}
    }
    return [...props].sort();
  };
  const seen = new Map();
  const nodes = document.querySelectorAll('button, a[href], summary, select, input, textarea, [role="tab"], [role="button"], [role="slider"], [role="switch"], [role="option"], [tabindex]:not([tabindex="-1"])');
  for (const el of nodes) {
    const box = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!box.width || !box.height || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const classes = [...el.classList].filter(c => !/^(is-|fx-|on$|sel$)/.test(c)).slice(0, 3).join('.');
    const home = el.closest('[id]');
    const key = el.tagName.toLowerCase() + (classes ? '.' + classes : (el.id ? '#' + el.id : '')) ;
    const durations = cs.transitionDuration.split(',').map(s => parseFloat(s) * (s.includes('ms') ? 1 : 1000));
    const tprops = cs.transitionProperty.split(',').map(s => s.trim());
    const trans = tprops.map((p, i) => [p, durations[i % durations.length]]).filter(([, d]) => d > 0).map(([p, d]) => `${p} ${Math.round(d)}ms`);
    let hit = { w: box.width, h: box.height };
    const before = getComputedStyle(el, '::before');
    if (before.content !== 'none' && before.position === 'absolute') { const bh = parseFloat(before.height), bw = parseFloat(before.width); if (bh > hit.h) hit.h = bh; if (bw > hit.w) hit.w = bw; }
    const row = seen.get(key) || { key, count: 0, where: new Set(), label: '', minW: 1e9, minH: 1e9, hitMinH: 1e9, transition: trans, timing: cs.transitionTimingFunction, active: hits(el, ACTIVE, 'active'), hover: hits(el, HOVER, 'hover'), focus: hits(el, FOCUS, 'focus-visible').length > 0, shadow: cs.boxShadow !== 'none', radius: cs.borderTopLeftRadius };
    row.count++; if (home && home !== el) row.where.add('#' + home.id); else if (el.id) row.where.add('#' + el.id);
    if (!row.label) row.label = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ').slice(0, 24);
    row.minW = Math.min(row.minW, Math.round(box.width)); row.minH = Math.min(row.minH, Math.round(box.height)); row.hitMinH = Math.min(row.hitMinH, Math.round(hit.h));
    seen.set(key, row);
  }
  return [...seen.values()].map(r => ({ ...r, where: [...r.where].slice(0, 4) }));
}
"""

# 互動後正在跑的動畫
ANIMS_JS = r"""
() => document.getAnimations().map(a => {
  const eff = a.effect, t = eff && eff.getComputedTiming ? eff.getComputedTiming() : {}, el = eff && eff.target;
  const kind = a.constructor.name;
  let what = kind === 'CSSTransition' ? a.transitionProperty : kind === 'CSSAnimation' ? a.animationName : (eff && eff.getKeyframes ? [...new Set(eff.getKeyframes().flatMap(k => Object.keys(k)))].filter(k => !['offset', 'easing', 'composite', 'computedOffset'].includes(k)).join('+') : '');
  let who = '';
  if (el) { who = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ([...el.classList].slice(0, 2).map(c => '.' + c).join('')); if (eff.pseudoElement) who += eff.pseudoElement; }
  const box = el ? el.getBoundingClientRect() : { top: 0, bottom: 0, height: 0 };
  return { kind, what, who, duration: Math.round(t.duration || 0), delay: Math.round(t.delay || 0), easing: t.easing, iterations: t.iterations === Infinity ? 'infinite' : t.iterations,
    onScreen: box.height > 0 && box.bottom > 0 && box.top < innerHeight, state: a.playState };
})
"""


def open_page(browser, base: str, key: str, size: str, theme: str = "light"):
    path, ready = PAGES[key]
    context = browser.new_context(color_scheme=theme, locale="zh-TW", timezone_id="Asia/Taipei", **SIZES[size])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    if key == "gemini":
        # 朋友的 Gemini 設定頁：預覽伺服器沒開這個功能（站主也不用這一頁）。直接拿打包好的頁面，狀態用假的「還沒設定」，只看版面；不輸入任何金鑰。
        dist = ROOT / "cloud" / "dist" / "gemini"
        state = {"available": True, "configured": False, "consented": False, "status": None, "last_success_at": None, "last_failure_reason": None,
                 "consent": {"version": "preview", "items": [{"id": "adult", "text": "我已滿 18 歲"}, {"id": "data_to_google", "text": "我知道我的自選股、股數和成本會送到 Google，免費層可能被拿去改進產品或由人工審閱"}]},
                 "studio_url": "https://example.invalid/", "limit": 10, "full": False}
        kinds = {"": ("index.html", "text/html; charset=utf-8"), "gemini.js": ("gemini.js", "text/javascript; charset=utf-8"), "account.css": ("account.css", "text/css; charset=utf-8")}

        def serve(route):
            name = route.request.url.split("?")[0].rsplit("/", 1)[-1]
            file, kind = kinds.get(name, kinds[""])
            route.fulfill(status=200, body=(dist / file).read_bytes(), content_type=kind)

        page.route(re.compile(r"/gemini/"), serve)
        page.route("**/api/friend-ai", lambda route: route.fulfill(json=state))
    page.goto(base + path, wait_until="domcontentloaded")
    page.evaluate(READY[ready])
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(1600)   # 進場動畫（板落桌 620ms＋間隔、數字滾動 900ms）播完
    return context, page, errors


def summarize(rows: list[dict]) -> list[dict]:
    """把「常駐在跑」的動畫與一次性的分開，並把同一種合併計數。"""
    merged: dict[tuple, dict] = {}
    for row in rows:
        key = (row["kind"], row["what"], row["who"], row["duration"], row["easing"], row["iterations"])
        entry = merged.setdefault(key, {**row, "count": 0})
        entry["count"] += 1
    return sorted(merged.values(), key=lambda r: (r["iterations"] == "infinite", r["who"]))


def run_pages(browser, base: str, only: str | None) -> None:
    shots = OUT / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    for key in PAGES:
        if only and only not in key:
            continue
        for size in SIZES:
            for theme in (("light", "dark") if key in ("home", "stock", "portfolio") and size == "390" else ("light",)):
                context, page, errors = open_page(browser, base, key, size, theme)
                name = f"{key}-{size}-{theme}"
                page.screenshot(path=str(shots / f"{name}.png"), full_page=True)
                entry: dict[str, object] = {"errors": errors, "height": page.evaluate("document.documentElement.scrollHeight"),
                                            "h_overflow": page.evaluate("document.documentElement.scrollWidth - innerWidth")}
                if theme == "light":
                    entry["components"] = page.evaluate(COMPONENTS_JS)
                    entry["idle_animations"] = summarize(page.evaluate(ANIMS_JS))
                report[name] = entry
                print(name, "height", entry["height"], "errors", len(errors), flush=True)
                context.close()
    target = OUT / ("pages.json" if not only else f"pages-{only}.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", target)


# ---- 逐個互動 ----
# 關掉剛剛打開的東西：浮起的面板（dialog）按它自己的關閉鍵、明細板按右上角的叉、選單送 Esc
CLOSE = """(() => {
  const dialog = document.querySelector('dialog[open]');
  if (dialog) { const key = dialog.querySelector('[data-dialog-close], .st-close, button[aria-label^="關閉"]'); if (key) key.click(); else dialog.close(); return 'dialog'; }
  const panel = document.querySelector('#detailPanel[aria-hidden="false"] .panel-close');
  if (panel) { panel.click(); return 'panel'; }
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  return 'esc';
})()"""


def again(selector: str) -> str:
    """再點同一顆（收回）。"""
    return f"document.querySelector({json.dumps(selector)}).click()"


# 每一項：name 名稱、page 頁面、click 要點的選擇器（或 select 改下拉的值、js 直接跑一段）、prep_click 先點一下做準備、
# then_js 第二段（通常是關掉）、fresh 進頁面、note 說明
def motions() -> list[dict]:
    cat = lambda c: f"#homeCats button[data-cat='{c}']"
    return [
        dict(name="home-enter", page="home", fresh=True, note="進頁面：板落桌、數字滾動"),
        dict(name="home-cat-chips", page="home", click=cat("chips"), then_click=cat("chips"), note="首頁分類鍵：展開籌碼與資金，再點收回"),
        dict(name="home-cat-switch", page="home", prep_click=cat("chips"), click=cat("screens"), note="首頁分類鍵：從一類換到另一類"),
        dict(name="home-cat-rotation", page="home", click=cat("rotation"), note="展開產業資金流向（地形長出來）", frames=(0, 120, 300, 600, 1000, 1600)),
        dict(name="home-cat-news", page="home", click=cat("news"), note="展開消息"),
        dict(name="home-chip-card", page="home", prep_click=cat("chips"), click="#chipGrid .chip-card", then_js=CLOSE, note="點一張籌碼卡：明細板浮出，再關掉"),
        dict(name="home-price-panel", page="home", click="#priceSummary", then_js=CLOSE, note="點報價：收復線／防守線面板浮出，再關掉"),
        dict(name="home-trend-row", page="home", click="#marketTrendRows .trend-top", then_click="#marketTrendRows .trend-top", note="短中長期走勢：展開一期，再收回"),
        dict(name="home-search", page="home", click="#searchToggle", then_js=CLOSE, note="打開搜尋，再關掉"),
        dict(name="home-settings", page="home", click="#settingsOpen", then_js=CLOSE, note="打開設定，再關掉"),
        dict(name="home-index-menu", page="home", click="#indexChip", then_js=CLOSE, note="指數切換選單，再關掉"),
        dict(name="home-period", page="home", select=("#periodSelect", "weekly"), note="週期：日 → 週（圖表換資料）"),
        dict(name="home-tools-menu", page="home", click="#toolsKey", then_js=CLOSE, note="分析工具選單，再關掉"),
        dict(name="home-study-menu", page="home", click="#chartStudies .chart-chip", then_js=CLOSE, note="指標選單，再關掉"),
        dict(name="home-ma-settings", page="home", click="#legendNames .legend-name", then_js=CLOSE, note="點均線名稱：均線設定，再關掉"),
        dict(name="home-forecast", page="home", click=".forecast-chip", then_click=".forecast-chip", note="預測鍵，再點一次"),
        dict(name="home-scroll", page="home", js="window.scrollTo({ top: 900, behavior: 'smooth' })", note="往下捲", frames=(0, 150, 300, 500, 800)),
        dict(name="stock-enter", page="stock", fresh=True, note="進個股頁"),
        dict(name="stock-cat-1", page="stock", click="#stockCats button:nth-of-type(1)", then_click="#stockCats button:nth-of-type(1)", note="個股頁分類鍵：價位，再點收回"),
        dict(name="stock-cat-2", page="stock", click="#stockCats button:nth-of-type(2)", note="個股頁分類鍵：籌碼"),
        dict(name="stock-cat-3", page="stock", click="#stockCats button:nth-of-type(3)", note="個股頁分類鍵：比較"),
        dict(name="stock-cat-switch", page="stock", prep_click="#stockCats button:nth-of-type(1)", click="#stockCats button:nth-of-type(2)", note="個股頁分類鍵：價位換到籌碼"),
        dict(name="stock-fact-tab", page="stock", click="#stockFactsBlocks .sf-tab:nth-of-type(2)", note="事實區塊：換一個分頁"),
        dict(name="stock-fact-tab-tech", page="stock", click="#stockFactsBlocks .sf-tab:nth-of-type(6)", note="事實區塊：技術"),
        dict(name="stock-fav", page="stock", click="#favToggle", note="星號：加入／移出自選"),
        dict(name="stock-period", page="stock", select=("#periodSelect", "weekly"), note="個股週期：日 → 週"),
        dict(name="stock-tools-menu", page="stock", click="#toolsKey", then_js=CLOSE, note="個股分析工具選單，再關掉"),
        dict(name="stock-settings", page="stock", click="#settingsOpen", then_js=CLOSE, note="個股頁打開設定，再關掉"),
        dict(name="stock-back", page="stock", click=".bd-back", note="返回：這一頁的板沉下去", frames=(0, 60, 120, 200)),
        dict(name="portfolio-enter", page="portfolio", fresh=True, note="進自選股與庫存"),
        dict(name="portfolio-filter", page="portfolio", click="#listFilter button:nth-of-type(2)", note="自選與庫存：篩選鍵（清單換內容）"),
        dict(name="portfolio-row", page="portfolio", click="#holdingRows .hv-open", then_click="#holdingRows .hv-open", note="點一檔展開明細，再收回"),
        dict(name="portfolio-add", page="portfolio", click="#addToggle", then_click="#addToggle", note="新增自選或庫存，再收回"),
        dict(name="portfolio-settings", page="portfolio", click="#settingsOpen", then_js=CLOSE, note="自選與庫存打開設定，再關掉"),
    ]


def new_context(browser, size: str):
    context = browser.new_context(color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei", **SIZES[size])
    page = context.new_page()
    # 標題字型來自 Google Fonts，中文字型切成很多小包、用到才抓；無頭瀏覽器每拍一張都會等字型，一個互動要拖到近一分鐘。
    # 量動態用不到字形，直接略過（標題會換成系統的襯線字，版面高度略有不同）。
    page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
    page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
    return context, page


def burst(page, frames_dir: Path, name: str, start: float, times) -> list[dict]:
    shots = []
    for ms in times:
        wait = ms / 1000 - (time.perf_counter() - start)
        if wait > 0:
            page.wait_for_timeout(wait * 1000)
        actual = round((time.perf_counter() - start) * 1000)
        file = f"{name}-{len(shots)}.png"
        page.screenshot(path=str(frames_dir / file))
        shots.append({"file": file, "ms": actual})
    return shots


def run_motion(browser, base: str, only: str | None) -> None:
    frames_dir = OUT / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    suffix = "-" + TAG.rstrip("-") if TAG else ""
    target_file = OUT / (f"motion{suffix}.json" if not only else f"motion-{TAG}{only}.json")
    report: dict[str, object] = {}
    for item in motions():
        if only and only not in item["name"]:
            continue
        for size in SIZES:
            name = f"{TAG}{item['name']}-{size}"
            path, ready = PAGES[item["page"]]
            context, page = new_context(browser, size)
            entry: dict[str, object] = {"note": item["note"]}
            try:
                if item.get("fresh"):
                    # 進頁面：不等頁面說「好了」，從送出網址就開始拍，才看得到板落桌與數字滾動
                    start = time.perf_counter()
                    page.goto(base + path, wait_until="commit")
                    seen: dict[tuple, dict] = {}
                    shots = []
                    for ms in item.get("frames", (300, 500, 700, 900, 1200, 1600, 2200)):
                        wait = ms / 1000 - (time.perf_counter() - start)
                        if wait > 0:
                            page.wait_for_timeout(wait * 1000)
                        for a in page.evaluate(ANIMS_JS):
                            seen.setdefault((a["kind"], a["what"], a["who"], a["duration"]), a)
                        file = f"{name}-{len(shots)}.png"
                        page.screenshot(path=str(frames_dir / file))
                        shots.append({"file": file, "ms": round((time.perf_counter() - start) * 1000)})
                    entry["animations"] = summarize(list(seen.values()))
                    entry["frames"] = shots
                else:
                    page.goto(base + path, wait_until="domcontentloaded")
                    page.evaluate(READY[ready])
                    page.wait_for_timeout(1900)
                    if item.get("prep_click"):
                        first = page.locator(item["prep_click"]).first
                        first.scroll_into_view_if_needed()
                        first.click()
                        page.wait_for_timeout(1100)
                    if item.get("click") or item.get("select"):
                        selector = item.get("click") or item["select"][0]
                        target = page.locator(selector).first
                        if target.count() == 0:
                            entry["skipped"] = "找不到 " + selector
                            report[name] = entry
                            print(name, "SKIP", flush=True)
                            context.close()
                            continue
                        # 把要點的東西捲到畫面上方四分之一：點了之後往下展開的內容才拍得到
                        target.evaluate("el => { const box = el.getBoundingClientRect(); window.scrollBy(0, box.top - innerHeight * .24); }")
                        page.wait_for_timeout(1500)   # 捲到才出現的動畫先播完，免得混進來
                    before = {(a["kind"], a["what"], a["who"]) for a in page.evaluate(ANIMS_JS)}
                    if item.get("click"):
                        entry["target"] = target.evaluate("el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + [...el.classList].map(c => '.' + c).join('') + ' ｜' + (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 20)")
                        box = target.bounding_box()
                        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        page.mouse.down()
                        page.mouse.up()
                    elif item.get("select"):
                        # 選單是系統原生的，拍不到；直接改值並送出 change，量圖表怎麼換
                        page.evaluate("([s, v]) => { const el = document.querySelector(s); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }", list(item["select"]))
                    elif item.get("js"):
                        page.evaluate(item["js"])
                    start = time.perf_counter()
                    page.wait_for_timeout(40)
                    now = page.evaluate(ANIMS_JS)
                    entry["animations"] = summarize([a for a in now if (a["kind"], a["what"], a["who"]) not in before])
                    entry["frames"] = burst(page, frames_dir, name, start, item.get("frames", (0, 80, 160, 260, 400, 700)))
                    if item.get("then_js") or item.get("then_click"):
                        # 第二段：通常是「關掉剛剛打開的東西」，量關的時候有沒有動畫
                        page.wait_for_timeout(500)
                        before = {(a["kind"], a["what"], a["who"]) for a in page.evaluate(ANIMS_JS)}
                        if item.get("then_click"):
                            # 再點同一顆（收回）：用真的滑鼠點，和使用者的點擊一樣
                            box = page.locator(item["then_click"]).first.bounding_box()
                            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                            page.mouse.down()
                            page.mouse.up()
                        else:
                            page.evaluate(item["then_js"])
                        start = time.perf_counter()
                        page.wait_for_timeout(40)
                        entry["close_animations"] = summarize([a for a in page.evaluate(ANIMS_JS) if (a["kind"], a["what"], a["who"]) not in before])
                        entry["close_frames"] = burst(page, frames_dir, name + "-close", start, (0, 80, 160, 260, 400))
            except Exception as exc:   # 一項失敗不影響其他項
                entry["error"] = str(exc)[:300]
            report[name] = entry
            print(name, "anims", len(entry.get("animations", [])), "close", len(entry.get("close_animations", [])) if "close_animations" in entry else "-", entry.get("error", ""), flush=True)
            context.close()
            target_file.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", target_file)


# ---- 按下去有沒有回饋：真的按住，比較按之前與按住時的樣子 ----
# 量元件本身、往外三層、往內第一層（有的鍵是外層那一列在變，有的是裡面那一塊在變）。
SNAP_JS = r"""
el => {
  const pick = node => { const cs = getComputedStyle(node); return { transform: cs.transform, translate: cs.translate, scale: cs.scale, boxShadow: cs.boxShadow, background: cs.backgroundColor, opacity: cs.opacity, color: cs.color, filter: cs.filter }; };
  const out = { self: pick(el) };
  let node = el.parentElement;
  for (let i = 1; i <= 3 && node && node !== document.body; i++, node = node.parentElement) out['up' + i] = pick(node);
  if (el.firstElementChild) out.child = pick(el.firstElementChild);
  return out;
}
"""
PRESS = {
    "home": [("報價（點了開收復線）", "#priceSummary"), ("行情狀態", "#statusTrigger"), ("圖示鍵：搜尋", "#searchToggle"), ("指數鍵", "#indexChip"),
             ("預測鍵", ".forecast-chip"), ("分析工具鍵", "#toolsKey"), ("週期（下拉）", "#periodSelect"), ("均線名稱", "#legendNames .legend-name"),
             ("指標名稱鍵", "#chartStudies .chart-chip"), ("短中長期走勢的一列", "#marketTrendRows .trend-top"), ("盤勢解讀卡", ".read-card, #marketBrief"),
             ("分類鍵", "#homeCats button"), ("籌碼卡", "#chipGrid .chip-card")],
    "stock": [("返回大盤", ".bd-back"), ("查詢", "#searchForm button"), ("設定", "#settingsOpen"), ("星號", "#favToggle"), ("分析工具鍵", "#toolsKey"),
              ("分類鍵", "#stockCats button"), ("事實區塊的分頁", "#stockFactsBlocks .sf-tab"), ("漲停動能板", ".ss-board")],
    "portfolio": [("返回首頁", ".pf-back"), ("設定", "#settingsOpen"), ("篩選鍵", "#listFilter button:nth-of-type(2)"), ("新增", "#addToggle"),
                  ("清單的一列", "#holdingRows .hv-open"), ("股票名稱（進個股頁）", "#holdingRows .hv-name")],
    "apply": [("進入網站", "a.btn"), ("管理申請", "a.btn.ghost")],
    "admin": [("分頁鍵", "button.ghost")],
}


def run_press(browser, base: str, only: str | None) -> None:
    report: dict[str, list] = {}
    for key, items in PRESS.items():
        if only and only not in key:
            continue
        path, ready = PAGES[key]
        context, page = new_context(browser, "390")
        page.goto(base + path, wait_until="domcontentloaded")
        page.evaluate(READY[ready])
        page.wait_for_timeout(1900)
        rows = []
        for label, selector in items:
            if label == "籌碼卡":
                page.locator("#homeCats button[data-cat='chips']").click()
                page.wait_for_timeout(2600)
            target = page.locator(selector).first
            if target.count() == 0:
                rows.append({"label": label, "selector": selector, "missing": True})
                continue
            target.evaluate("el => { const box = el.getBoundingClientRect(); window.scrollBy(0, box.top - innerHeight * .4); }")
            page.wait_for_timeout(1300)
            box = target.bounding_box()
            if not box:
                rows.append({"label": label, "selector": selector, "missing": True})
                continue
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(2, 2)
            page.wait_for_timeout(450)
            rest = target.evaluate(SNAP_JS)
            page.mouse.move(x, y)
            page.wait_for_timeout(500)
            hover = target.evaluate(SNAP_JS)
            page.mouse.down()
            page.wait_for_timeout(260)
            held = target.evaluate(SNAP_JS)
            page.mouse.move(2, 2)   # 拖開再放：不觸發點擊
            page.mouse.up()
            page.wait_for_timeout(300)

            def diff(a: dict, b: dict) -> list[str]:
                names = {"self": "自己", "up1": "外一層", "up2": "外二層", "up3": "外三層", "child": "裡面第一塊"}
                changed = []
                for who in a:
                    for prop in a[who]:
                        if b.get(who, {}).get(prop) != a[who][prop]:
                            changed.append(f"{names[who]}.{prop}")
                return changed

            rows.append({"label": label, "selector": selector, "size": [round(box["width"]), round(box["height"])],
                         "hover": diff(rest, hover), "press": diff(hover, held)})
            print(key, label, "| 滑過", rows[-1]["hover"] or "無", "| 按住", rows[-1]["press"] or "無", flush=True)
        report[key] = rows
        context.close()
    (OUT / "press.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", OUT / "press.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("pages", "motion", "press"))
    parser.add_argument("--base", default="http://127.0.0.1:5310")
    parser.add_argument("--only")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    global TAG
    TAG = args.tag
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        {"pages": run_pages, "motion": run_motion, "press": run_press}[args.mode](browser, args.base, args.only)
        browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
