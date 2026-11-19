"""全站操作毛病巡檢（本人 2026-11-20：「還有其他的請你幫我檢查並修正」）。

三個頁面 × 手機 390×844（觸控）／桌機 1440×900，把每一種狀態都點出來，每個狀態量：
  overflow   整頁有沒有橫向捲動（文件比畫面寬多少 px）
  offscreen  看得到的元素有沒有超出畫面左右（不在會橫向捲的容器裡的）
  small      手機上點擊區小於 44×44 的鍵（文字行內的連結不算）
  overlay    開著的浮層有沒有超出畫面上下左右
  shift      進頁面之後版面跳了多少（layout shift 累計）
  slow       這一步點下去之後主執行緒被佔住最久多久（long task，毫秒）
每個狀態存一張截圖。結果寫到 <out>/audit.json 與印在畫面上。這支只找問題，不判定通過與否。

用法（工作目錄根目錄，先 node cloud/build.mjs）：python design-previews/ui-polish/audit.py [輸出資料夾] [--only home|stock|portfolio] [--size 390|1440] [--throttle]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "3d-redesign"))
import shoot  # noqa: E402

ARGS = sys.argv[1:]
OUT = Path(next((a for a in ARGS if not a.startswith("--") and a not in ("home", "stock", "portfolio", "390", "1440")), ".tmp/ui-polish/audit"))
ONLY = ARGS[ARGS.index("--only") + 1] if "--only" in ARGS else None
SIZE = ARGS[ARGS.index("--size") + 1] if "--size" in ARGS else None
OUT.mkdir(parents=True, exist_ok=True)
SIZES = {"390": (390, 844), "1440": (1440, 900)}

OBSERVE = """
(() => {
  window.__shift = 0; window.__long = 0;
  try {
    new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) window.__shift += e.value; }).observe({ type: 'layout-shift', buffered: true });
    new PerformanceObserver(list => { for (const e of list.getEntries()) window.__long = Math.max(window.__long, e.duration); }).observe({ type: 'longtask', buffered: true });
  } catch (_) {}
})();
"""

PROBE = """
mobile => {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const name = el => {
    const id = el.id ? '#' + el.id : '';
    const cls = typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '';
    const text = (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 14);
    return `${el.tagName.toLowerCase()}${id}${cls}${text ? ' "' + text + '"' : ''}`;
  };
  const shown = el => {
    if (!el.getClientRects().length) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.opacity === '0' || style.pointerEvents === 'none') return false;
    if (el.closest('[inert], [hidden], [aria-hidden="true"]')) return false;
    return true;
  };
  // 在會自己橫向捲的容器裡的不算超出
  const scrollsX = el => { for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) { const s = getComputedStyle(n); if (/(auto|scroll|hidden|clip)/.test(s.overflowX)) return true; } return false; };
  const offscreen = [];
  document.querySelectorAll('body *').forEach(el => {
    if (!shown(el) || el.closest('.sr-only')) return;
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) return;
    if ((box.right > vw + 1 || box.left < -1) && !scrollsX(el) && getComputedStyle(el).position !== 'fixed') offscreen.push(`${name(el)} [${Math.round(box.left)}..${Math.round(box.right)}]`);
  });
  // 點擊區：量「手指點得到的範圍」，不是元素本身的框——用 ::before／::after 撐大的、包在 label 裡的都算。
  // 從中心往上下左右各 REACH px 的四個點，點下去要落在這個元素（或它裡面、或它的 label）上。
  // REACH = 18：點得到的範圍不到 36px 才報（44 是目標，36～44 之間的密集表格與成排小鍵不逐一報）。
  const REACH = 18;
  const small = [];
  if (mobile) {
    const hits = (el, x, y) => {
      if (x < 0 || y < 0 || x >= vw || y >= vh) return true;   // 貼著畫面邊緣：邊緣以外算有
      const got = document.elementFromPoint(x, y);
      if (!got) return false;
      if (el.contains(got) || got.contains(el) && got.closest('a[href], button, label, summary')) return true;
      const label = got.closest('label');
      return Boolean(label && (label.control === el || label.contains(el)));
    };
    const keep = window.scrollY;
    document.querySelectorAll('a[href], button, select, input:not([type=hidden]), summary, [role=button], [role=tab], [role=radio], [role=option]').forEach(el => {
      if (!shown(el) || el.disabled || el.closest('.sr-only') || el.closest('svg')) return;
      if (el.tagName === 'A' && getComputedStyle(el).display === 'inline' && !el.className) return;   // 文字行內的連結
      let box = el.getBoundingClientRect();
      if (box.width >= 2 * REACH && box.height >= 2 * REACH) return;
      if (box.width <= 2 && box.height <= 2) return;   // 只給螢幕閱讀器與鍵盤的替代鍵（向左滑的列）
      if (!el.closest('dialog[open], [role=dialog], .chart-menu, .sp-list')) el.scrollIntoView({ block: 'center', behavior: 'instant' });
      box = el.getBoundingClientRect();
      const cx = box.left + box.width / 2, cy = box.top + box.height / 2;
      const reach = { up: hits(el, cx, cy - REACH), down: hits(el, cx, cy + REACH), left: box.width >= 2 * REACH || hits(el, cx - REACH, cy), right: box.width >= 2 * REACH || hits(el, cx + REACH, cy) };
      const short = Object.entries(reach).filter(([, ok]) => !ok).map(([side]) => side);
      if (short.length) small.push(`${name(el)} ${Math.round(box.width)}x${Math.round(box.height)} short:${short.join('/')}`);
    });
    window.scrollTo({ top: keep, behavior: 'instant' });
  }
  const overlay = [];
  document.querySelectorAll('dialog[open], [role=dialog]:not([aria-hidden="true"]), .chart-menu:not([hidden]), .sp-list:not([hidden]), #forecastPanel:not([hidden]), .ma-pop:not([hidden]), .wj-panel:not([hidden])').forEach(el => {
    if (!shown(el)) return;
    const box = el.getBoundingClientRect();
    const fixed = getComputedStyle(el).position === 'fixed' || el.tagName === 'DIALOG';
    if (box.left < -1 || box.right > vw + 1 || (fixed && (box.top < -1 || box.bottom > vh + 1))) overlay.push(`${name(el)} x[${Math.round(box.left)}..${Math.round(box.right)}] y[${Math.round(box.top)}..${Math.round(box.bottom)}] of ${vw}x${vh}`);
  });
  const long = Math.round(window.__long || 0);
  window.__long = 0;
  return {
    overflow: Math.max(0, document.documentElement.scrollWidth - vw),
    offscreen: [...new Set(offscreen)].slice(0, 12), small: [...new Set(small)].slice(0, 40), overlay,
    shift: Math.round((window.__shift || 0) * 1000) / 1000, slow: long,
  };
}
"""

report: list[dict] = []


def probe(page: Page, page_name: str, size: str, state: str) -> None:
    page.wait_for_timeout(450)
    got = page.evaluate(PROBE, size == "390")
    file = f"{page_name}-{size}-{state}.png"
    try:
        page.screenshot(path=str(OUT / file))
    except Exception as error:  # noqa: BLE001
        file = f"(screenshot failed: {error})"
    got.update({"page": page_name, "size": size, "state": state, "shot": file})
    report.append(got)
    flags = []
    if got["overflow"]:
        flags.append(f"overflow {got['overflow']}px")
    if got["offscreen"]:
        flags.append(f"offscreen {len(got['offscreen'])}")
    if got["overlay"]:
        flags.append(f"overlay {len(got['overlay'])}")
    if got["small"]:
        flags.append(f"small {len(got['small'])}")
    if got["slow"] > (200 if "--throttle" in ARGS else 120):
        flags.append(f"slow {got['slow']}ms")
    print(f"  {page_name} {size} {state}: " + ("; ".join(flags) if flags else "clean") + (f" (shift {got['shift']})" if state == "load" else ""))


def step(page: Page, page_name: str, size: str, state: str, action) -> None:
    try:
        action()
        probe(page, page_name, size, state)
    except Exception as error:  # noqa: BLE001
        report.append({"page": page_name, "size": size, "state": state, "error": str(error).splitlines()[0][:200]})
        print(f"  {page_name} {size} {state}: COULD NOT REACH - {str(error).splitlines()[0][:160]}")


def press(page: Page, selector: str, mobile: bool) -> None:
    target = page.locator(selector).first
    target.scroll_into_view_if_needed()
    target.tap() if mobile else target.click()


def close_all(page: Page) -> None:
    page.keyboard.press("Escape")
    page.evaluate("""() => {
      document.querySelectorAll('dialog[open]').forEach(d => d.close());
      document.getElementById('panelClose')?.click();
      document.querySelector('#settingsDialog .st-close')?.click();
      document.body.click();
    }""")
    page.wait_for_timeout(350)


def settings_states(page: Page, name: str, size: str, mobile: bool) -> None:
    step(page, name, size, "settings", lambda: press(page, "#settingsOpen", mobile))
    tabs = page.evaluate("() => [...document.querySelectorAll('#settingsDialog .st-tab')].map((b, i) => i)")
    for index in tabs[1:]:
        step(page, name, size, f"settings-tab{index}", lambda i=index: page.evaluate("i => [...document.querySelectorAll('#settingsDialog .st-tab')][i].click()", i))
    close_all(page)


def home(page: Page, size: str, mobile: bool) -> None:
    name = "home"
    probe(page, name, size, "load")
    for cat in shoot.HOME_CATS:
        step(page, name, size, f"cat-{cat}", lambda c=cat: press(page, f"#homeCats button[data-cat='{c}']", mobile))
    step(page, name, size, "rotation-dialog", lambda: (press(page, "#homeCats button[data-cat='rotation']", mobile) if page.get_attribute("#homeCats button[data-cat='rotation']", "aria-expanded") != "true" else None, press(page, ".rotation-open", mobile)))
    close_all(page)
    step(page, name, size, "search", lambda: press(page, "#searchToggle", mobile))
    step(page, name, size, "search-results", lambda: (page.fill("#stockSearchInput", "電"), page.keyboard.press("Enter"), page.wait_for_selector("#stockSearchResults li")))
    close_all(page)
    step(page, name, size, "price-panel", lambda: press(page, "#priceSummary", mobile))
    close_all(page)
    step(page, name, size, "chip-panel", lambda: (press(page, "#homeCats button[data-cat='chips']", mobile) if page.get_attribute("#homeCats button[data-cat='chips']", "aria-expanded") != "true" else None, press(page, "#chipGrid button, #chipGrid .chip-card", mobile)))
    close_all(page)
    step(page, name, size, "index-menu", lambda: press(page, "#indexChip", mobile))
    close_all(page)
    for period in ("ts", "h60", "weekly", "monthly", "daily"):
        step(page, name, size, f"period-{period}", lambda p=period: (page.select_option("#periodSelect", p), page.wait_for_timeout(500)))
    step(page, name, size, "legend-menu", lambda: press(page, "#legendNames button, #chartLegend .legend-set, #chartLegend [aria-haspopup]", mobile))
    close_all(page)
    step(page, name, size, "forecast", lambda: press(page, ".fc-key, #forecastKey, [aria-controls='forecastPanel']", mobile))
    close_all(page)
    settings_states(page, name, size, mobile)


def stock(page: Page, size: str, mobile: bool) -> None:
    name = "stock"
    probe(page, name, size, "load")
    for panel in ("levelsPanel", "chipsPanel", "comparePanel"):
        step(page, name, size, f"cat-{panel}", lambda p=panel: press(page, f"#stockCats button[aria-controls='{p}']", mobile))
    step(page, name, size, "strategy-details", lambda: press(page, "#strategyDetails summary", mobile))
    tabs = page.evaluate("() => [...document.querySelectorAll('#stockFacts [role=tab]')].map(t => t.textContent.trim())")
    for index, label in enumerate(tabs):
        step(page, name, size, f"facts-tab{index}", lambda i=index: page.evaluate("i => { const t = [...document.querySelectorAll('#stockFacts [role=tab]')][i]; t.scrollIntoView({ block: 'center' }); t.click(); }", i))
    step(page, name, size, "tools", lambda: press(page, "#toolsKey", mobile))
    for period in page.evaluate("() => [...document.querySelectorAll('#periodSelect option')].map(o => o.value)"):
        step(page, name, size, f"period-{period}", lambda p=period: (page.select_option("#periodSelect", p), page.wait_for_timeout(600)))
    page.select_option("#periodSelect", "daily")
    step(page, name, size, "period-chip-menu", lambda: press(page, ".chart-chip[aria-controls]:not([hidden])", mobile))
    close_all(page)
    step(page, name, size, "legend-menu", lambda: press(page, "#legendNames button, #chartLegend [aria-haspopup]", mobile))
    close_all(page)
    step(page, name, size, "picker", lambda: (page.fill("#codeInput", ""), page.type("#codeInput", "台積", delay=30), page.wait_for_selector(".sp-list:not([hidden])")))
    close_all(page)
    settings_states(page, name, size, mobile)


def portfolio(page: Page, size: str, mobile: bool) -> None:
    name = "portfolio"
    probe(page, name, size, "load")
    for which in ("holding", "watch", "all"):
        step(page, name, size, f"filter-{which}", lambda w=which: press(page, f"#listFilter button[data-filter='{w}']", mobile))
    step(page, name, size, "row-open", lambda: press(page, "#holdingRows .hv-row .hv-open", mobile))
    step(page, name, size, "row-open-watch", lambda: (press(page, "#listFilter button[data-filter='watch']", mobile), press(page, "#holdingRows .hv-row .hv-open", mobile)))
    press(page, "#listFilter button[data-filter='all']", mobile)
    step(page, name, size, "add", lambda: press(page, "#addToggle", mobile))
    step(page, name, size, "add-holding", lambda: press(page, "#addHolding", mobile))
    step(page, name, size, "add-picker", lambda: (page.type("#addCode", "台積", delay=30), page.wait_for_selector("#addPanel .sp-option")))
    press(page, "#addToggle", mobile)
    settings_states(page, name, size, mobile)


RUNS = {"home": ("/", shoot.ready_home, home), "stock": (f"/stock.html?code={shoot.STOCK}", shoot.ready_stock, stock), "portfolio": ("/portfolio.html", shoot.ready_portfolio, portfolio)}


def main() -> int:
    proc, base = shoot.start_server("--owner-extras")
    errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for size, (width, height) in SIZES.items():
                if SIZE and SIZE != size:
                    continue
                mobile = size == "390"
                context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2 if mobile else 1, is_mobile=mobile, has_touch=mobile)
                context.add_init_script(OBSERVE)
                for name, (path, ready, walk) in RUNS.items():
                    if ONLY and ONLY != name:
                        continue
                    page = context.new_page()
                    if "--throttle" in ARGS:   # 處理器放慢 4 倍（接近手機），看哪一步會卡
                        context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": 4})
                    page.on("pageerror", lambda error, n=name, s=size: errors.append(f"{n} {s}: {error}"))
                    page.goto(base + path)
                    ready(page)
                    shoot.settle(page)
                    walk(page, size, mobile)
                    page.close()
                context.close()
            browser.close()
    finally:
        proc.kill()
    (OUT / "audit.json").write_text(json.dumps({"states": report, "page_errors": errors}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(report)} states, page errors: {len(errors)}")
    for error in errors:
        print("  page error:", error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
