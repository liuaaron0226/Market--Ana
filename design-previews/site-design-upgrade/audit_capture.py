"""全站動態與元件設計升級（2026-11-24）的盤點工具：逐頁截圖、列出元件的按壓／滑過回饋、量每個互動實際跑了哪些動畫。

只讀已經開著的本機預覽（design-previews/3d-redesign/preview-server.mjs，預設 http://127.0.0.1:5310，樣本資料、即時行情離線），
不連正式站、不寫任何使用者資料。輸出放 .scratch/site-design-upgrade/（不入版控）。

用法（在工作目錄根目錄）：
    python design-previews/site-design-upgrade/audit_capture.py pages          # 每一頁整頁截圖＋元件清單
    python design-previews/site-design-upgrade/audit_capture.py motion         # 逐個互動：跑了哪些動畫＋連續畫面
    python design-previews/site-design-upgrade/audit_capture.py motion --only home-cat

「元件清單」怎麼量：把頁面上看得到、可以點的東西照（標籤＋類別）分組，每組記大小、有沒有 :active／:hover 規則與改了哪些屬性、
有沒有過場（transition）。規則是從頁面載入的樣式表逐條比對出來的，不是猜的。
「跑了哪些動畫」怎麼量：互動後 40 毫秒問瀏覽器 document.getAnimations()，記每一個動畫的對象、屬性、時間、曲線；
什麼都沒有就是瞬間切換。連續畫面是同一段時間內每隔一小段拍一張（無頭瀏覽器，時間是近似值）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / ".scratch" / "site-design-upgrade"
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
# 每一項：(名稱, 頁面, 準備（JS，可省略）, 觸發（JS 或 ('click', 選擇器) 或 ('tap', 選擇器)), 說明)
def motions() -> list[dict]:
    cat = lambda c: f"#homeCats button[data-cat='{c}']"
    return [
        dict(name="home-enter", page="home", fresh=True, note="進頁面：板落桌、數字滾動"),
        dict(name="home-cat-chips", page="home", click=cat("chips"), note="首頁分類鍵：展開籌碼與資金"),
        dict(name="home-cat-switch", page="home", prep_click=cat("chips"), click=cat("screens"), note="首頁分類鍵：從一類換到另一類"),
        dict(name="home-cat-rotation", page="home", click=cat("rotation"), note="展開產業資金流向（地形長出來）", frames=(0, 120, 300, 600, 1000, 1600)),
        dict(name="home-cat-news", page="home", click=cat("news"), note="展開消息"),
        dict(name="home-chip-card", page="home", prep_click=cat("chips"), click="#chipGrid .chip-card", note="點一張籌碼卡：明細板浮出"),
        dict(name="home-price-panel", page="home", click="#priceSummary", note="點報價：收復線／防守線面板浮出"),
        dict(name="home-price-panel-close", page="home", prep_click="#priceSummary", js="(document.querySelector('#detailPanel .panel-back') || document.querySelector('#detailPanel button'))?.click()", note="關掉面板"),
        dict(name="home-trend-row", page="home", click="#marketTrendRows .trend-top", note="短中長期走勢：展開一期"),
        dict(name="home-search", page="home", click="#searchToggle", note="打開搜尋"),
        dict(name="home-settings", page="home", click="#settingsOpen", note="打開設定"),
        dict(name="home-index-menu", page="home", click="#indexChip", note="指數切換選單"),
        dict(name="home-period", page="home", select=("#periodSelect", "weekly"), note="週期：日 → 週（圖表換資料）"),
        dict(name="home-tools-menu", page="home", click="#toolsKey", note="分析工具選單"),
        dict(name="home-study-menu", page="home", click="#chartStudies .chart-chip", note="指標選單"),
        dict(name="home-ma-settings", page="home", click="#legendNames .legend-name", note="點均線名稱：均線設定"),
        dict(name="home-forecast", page="home", click=".forecast-chip", note="預測鍵"),
        dict(name="home-scroll", page="home", js="window.scrollTo({ top: 900, behavior: 'smooth' })", note="往下捲", frames=(0, 150, 300, 500, 800)),
        dict(name="stock-enter", page="stock", fresh=True, note="進個股頁"),
        dict(name="stock-cat-1", page="stock", click="#stockCats button:nth-of-type(1)", note="個股頁分類鍵：價位"),
        dict(name="stock-cat-2", page="stock", click="#stockCats button:nth-of-type(2)", note="個股頁分類鍵：籌碼"),
        dict(name="stock-cat-3", page="stock", click="#stockCats button:nth-of-type(3)", note="個股頁分類鍵：比較"),
        dict(name="stock-fact-tab", page="stock", click="#stockFactsBlocks .sf-tab:nth-of-type(2)", note="事實區塊：換一個分頁"),
        dict(name="stock-fact-tab-tech", page="stock", click="#stockFactsBlocks .sf-tab:nth-of-type(6)", note="事實區塊：技術"),
        dict(name="stock-fav", page="stock", click="#favToggle", note="星號：加入／移出自選"),
        dict(name="stock-period", page="stock", select=("#periodSelect", "weekly"), note="個股週期：日 → 週"),
        dict(name="stock-tools-menu", page="stock", click="#toolsKey", note="個股分析工具選單"),
        dict(name="stock-settings", page="stock", click="#settingsOpen", note="個股頁打開設定"),
        dict(name="stock-back", page="stock", click=".bd-back", note="返回：這一頁的板沉下去", frames=(0, 60, 120, 200)),
        dict(name="portfolio-enter", page="portfolio", fresh=True, note="進自選股與庫存"),
        dict(name="portfolio-filter", page="portfolio", click="#listFilter button:nth-of-type(2)", note="自選與庫存：篩選鍵（清單換內容）"),
        dict(name="portfolio-row", page="portfolio", click="#holdingRows .hv-open", note="點一檔展開明細"),
        dict(name="portfolio-add", page="portfolio", click="#addToggle", note="新增自選或庫存"),
        dict(name="portfolio-settings", page="portfolio", click="#settingsOpen", note="自選與庫存打開設定"),
    ]


def run_motion(browser, base: str, only: str | None) -> None:
    frames_dir = OUT / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    for item in motions():
        if only and only not in item["name"]:
            continue
        for size in SIZES:
            name = f"{item['name']}-{size}"
            path, ready = PAGES[item["page"]]
            context = browser.new_context(color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei", **SIZES[size])
            page = context.new_page()
            entry: dict[str, object] = {"note": item["note"]}
            try:
                if item.get("fresh"):
                    page.goto(base + path, wait_until="domcontentloaded")
                    page.evaluate(READY[ready])
                    start = time.perf_counter()
                else:
                    page.goto(base + path, wait_until="domcontentloaded")
                    page.evaluate(READY[ready])
                    page.evaluate("document.fonts.ready.then(() => true)")
                    page.wait_for_timeout(1700)
                    if item.get("prep_click"):
                        first = page.locator(item["prep_click"]).first
                        first.scroll_into_view_if_needed()
                        first.click()
                        page.wait_for_timeout(900)
                    before = {(a["kind"], a["what"], a["who"]) for a in page.evaluate(ANIMS_JS)}
                    if item.get("click"):
                        target = page.locator(item["click"]).first
                        if target.count() == 0:
                            entry["skipped"] = "找不到 " + item["click"]
                            report[name] = entry
                            print(name, "SKIP", flush=True)
                            context.close()
                            continue
                        target.scroll_into_view_if_needed()
                        page.wait_for_timeout(250)
                        entry["target"] = target.evaluate("el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + [...el.classList].map(c => '.' + c).join('') + ' ｜' + (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 20)")
                        box = target.bounding_box()
                        # 用 pointer 事件點在元件正中央；立刻開始計時
                        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        page.mouse.down()
                        page.mouse.up()
                    elif item.get("select"):
                        selector, value = item["select"]
                        page.locator(selector).first.scroll_into_view_if_needed()
                        page.wait_for_timeout(250)
                        # 選單是系統原生的，拍不到；直接改值並送出 change，量圖表怎麼換
                        page.evaluate("([s, v]) => { const el = document.querySelector(s); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }", [selector, value])
                    elif item.get("js"):
                        page.evaluate(item["js"])
                    start = time.perf_counter()
                    page.wait_for_timeout(40)
                    now = page.evaluate(ANIMS_JS)
                    entry["animations"] = summarize([a for a in now if (a["kind"], a["what"], a["who"]) not in before and a["onScreen"]])
                shots = []
                for ms in item.get("frames", (0, 80, 160, 260, 400, 700)):
                    wait = ms / 1000 - (time.perf_counter() - start)
                    if wait > 0:
                        page.wait_for_timeout(wait * 1000)
                    actual = round((time.perf_counter() - start) * 1000)
                    file = f"{name}-{len(shots)}.png"
                    page.screenshot(path=str(frames_dir / file))
                    shots.append({"file": file, "ms": actual})
                entry["frames"] = shots
            except Exception as exc:   # 一項失敗不影響其他項
                entry["error"] = str(exc)[:300]
            report[name] = entry
            print(name, "anims", len(entry.get("animations", [])), entry.get("error", ""), flush=True)
            context.close()
    target = OUT / ("motion.json" if not only else f"motion-{only}.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("pages", "motion"))
    parser.add_argument("--base", default="http://127.0.0.1:5310")
    parser.add_argument("--only")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        (run_pages if args.mode == "pages" else run_motion)(browser, args.base, args.only)
        browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
