"""手機鍵盤彈出時，搜尋結果最下面幾列不能被鍵盤擋住（本人 2026-11-20：「搜尋的頁面最下面的幾檔股票會被鍵盤擋住」）。

iPhone 的鍵盤不會讓頁面變矮，只會讓「看得到的範圍」（visualViewport）變矮；Chromium 沒有螢幕鍵盤可以模擬，
所以這裡把 visualViewport 換成一個可以調高度的替身：`__setKeyboard(336)` 等於鍵盤佔掉畫面下方 336px，
`__setKeyboard(336, 120)` 等於鍵盤彈出時 iOS 又把畫面往上推了 120px（看得到的範圍是 120～120+508）。
替身只能證明「版面有照看得到的範圍調整」，鍵盤實際的高度與 iOS 自己的捲動行為要本人用 iPhone 驗。

全站有股票搜尋結果的地方各走一遍（390×844，觸控）：
  首頁    右上放大鏡的搜尋對話框（#stockSearchDialog）
  自選股  「＋」裡的代號或名稱框（選股下拉排在框下面）
  個股頁  頂列的代號或名稱框（選股下拉浮在框下面）
  個股頁  「比較」→ 加入比較股票的框（選股下拉浮在框下面）
每一處：打兩個字出現結果 → 鍵盤彈出 → 最後一列捲得到、而且在鍵盤上緣以上；輸入框本身也還看得到。

用法（工作目錄根目錄，先 node cloud/build.mjs）：python design-previews/ui-polish/check-keyboard.py [截圖資料夾]
全部通過結束碼 0，否則 1。
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "3d-redesign"))
import shoot  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
KEYBOARD = 336
results: list[tuple[str, bool, str]] = []

FAKE_VIEWPORT = """
(() => {
  const fake = new EventTarget();
  let keyboard = 0, pushed = 0;
  Object.defineProperties(fake, {
    width: { get: () => window.innerWidth }, height: { get: () => window.innerHeight - keyboard },
    offsetTop: { get: () => pushed }, offsetLeft: { get: () => 0 },
    pageTop: { get: () => window.scrollY + pushed }, pageLeft: { get: () => 0 }, scale: { get: () => 1 },
  });
  Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => fake });
  window.__setKeyboard = (height, push = 0) => {
    keyboard = height; pushed = push;
    fake.dispatchEvent(new Event('resize'));
    fake.dispatchEvent(new Event('scroll'));
  };
  // 看得到的範圍（和 getBoundingClientRect 同一套座標）
  window.__visible = () => ({ top: pushed, bottom: pushed + window.innerHeight - keyboard });
})();
"""


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((name, bool(ok), str(detail)))
    print(("  ok  " if ok else "  FAIL ") + name + (f" | {detail}" if detail != "" and not ok else ""))


def settle(page: Page) -> None:
    page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
    page.wait_for_timeout(450)   # 版面跟著調整的過場


# 像手指一樣把最後一列捲出來：先捲清單自己（對話框、浮起的下拉），不夠再捲整頁；回最後一列與輸入框的位置
LAST_ROW = """
([rowSelector, inputSelector]) => {
  const rows = [...document.querySelectorAll(rowSelector)].filter(row => row.offsetParent !== null);
  const last = rows.at(-1);
  if (!last) return null;
  const visible = window.__visible();
  for (let node = last.parentElement; node && node !== document.documentElement; node = node.parentElement) {
    const over = last.getBoundingClientRect().bottom - visible.bottom;
    if (over <= 0) break;
    if (node === document.body) { if (!document.querySelector('dialog[open]')) window.scrollBy({ top: over, behavior: 'instant' }); break; }
    if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) node.scrollTop += over;
  }
  const box = last.getBoundingClientRect(), input = document.querySelector(inputSelector).getBoundingClientRect();
  return { rows: rows.length, lastTop: Math.round(box.top), lastBottom: Math.round(box.bottom), inputTop: Math.round(input.top), inputBottom: Math.round(input.bottom), visible };
}
"""

# 選股下拉的搜尋結果用固定的六檔（預覽的樣本名錄太小，兩個字的搜尋常常只有一兩檔）
SIX = [{"code": f"23{n:02d}", "name": f"樣本電子{n}", "market": "上市", "security_type": "普通股", "universe_rank": n} for n in range(1, 7)]


def verify(page: Page, label: str, row_selector: str, input_selector: str, shot: str, push: int = 0) -> None:
    page.evaluate("([height, push]) => window.__setKeyboard(height, push)", [KEYBOARD, push])
    settle(page)
    got = page.evaluate(LAST_ROW, [row_selector, input_selector])
    settle(page)
    got = page.evaluate(LAST_ROW, [row_selector, input_selector])
    if OUT:
        page.screenshot(path=str(OUT / f"{shot}.png"))
    if not got:
        check(f"{label}：有搜尋結果", False, "no rows")
        return
    visible = got["visible"]
    check(f"{label}：有搜尋結果（{got['rows']} 列）", got["rows"] >= 6, got)
    check(f"{label}：最後一列在鍵盤上緣以上", got["lastBottom"] <= visible["bottom"] + 1 and got["lastTop"] >= visible["top"] - 1, got)
    page.evaluate("() => window.__setKeyboard(0, 0)")
    settle(page)


def type_query(page: Page, selector: str, text: str) -> None:
    page.focus(selector)
    page.fill(selector, "")
    page.type(selector, text, delay=30)


def main() -> int:
    proc, base = shoot.start_server("--owner-extras")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            context.add_init_script(FAKE_VIEWPORT)
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            # 首頁搜尋對話框
            page.goto(f"{base}/minimal.html")
            page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
            for push, tag in ((0, ""), (120, "（畫面被 iOS 往上推 120px）")):
                page.tap("#searchToggle")
                page.wait_for_selector("#stockSearchDialog[open]")
                type_query(page, "#stockSearchInput", "電")
                page.keyboard.press("Enter")
                page.wait_for_selector("#stockSearchResults li")
                verify(page, f"首頁搜尋對話框{tag}", "#stockSearchResults li, #stockSearchMore:not([hidden])", "#stockSearchInput", f"home-search-{push}", push)
                page.evaluate(f"() => window.__setKeyboard({KEYBOARD}, {push})")
                settle(page)
                box = page.evaluate("""() => { const d = document.getElementById('stockSearchDialog').getBoundingClientRect(), v = window.__visible();
                    const i = document.getElementById('stockSearchInput').getBoundingClientRect();
                    document.getElementById('stockSearchDialog').scrollTop = 0;
                    return { top: Math.round(d.top), bottom: Math.round(d.bottom), inputTop: Math.round(i.top), v }; }""")
                check(f"首頁搜尋對話框{tag}：整個面板在看得到的範圍裡", box["top"] >= box["v"]["top"] and box["bottom"] <= box["v"]["bottom"], box)
                page.evaluate("() => window.__setKeyboard(0, 0)")
                page.evaluate("() => document.getElementById('stockSearchDialog').close()")
                settle(page)
            # 手指在清單上一滑：鍵盤收起來（輸入框失焦），面板留著；在輸入框上滑不算
            page.tap("#searchToggle")
            page.wait_for_selector("#stockSearchDialog[open]")
            page.focus("#stockSearchInput")
            swipe = """selector => {
              const el = document.querySelector(selector), box = el.getBoundingClientRect();
              const touch = new Touch({ identifier: 1, target: el, clientX: box.left + 20, clientY: box.top + 10 });
              el.dispatchEvent(new TouchEvent('touchmove', { bubbles: true, touches: [touch], targetTouches: [touch], changedTouches: [touch] }));
              return { focused: document.activeElement && document.activeElement.id, open: document.getElementById('stockSearchDialog').open };
            }"""
            on_input = page.evaluate(swipe, "#stockSearchInput")
            check("首頁搜尋：手指在輸入框上滑，鍵盤不收", on_input == {"focused": "stockSearchInput", "open": True}, on_input)
            on_list = page.evaluate(swipe, "#stockSearchSuggestions, #stockSearchResults")
            check("首頁搜尋：手指在清單上一滑，鍵盤收起來、面板留著", on_list["focused"] != "stockSearchInput" and on_list["open"], on_list)
            page.evaluate("() => document.getElementById('stockSearchDialog').close()")
            settle(page)
            check("首頁：關掉搜尋之後頁面照常可以捲", page.evaluate("() => { window.scrollTo(0, 400); return window.scrollY; }") > 300)

            page.route("**/api/stocks/search?*", lambda route: route.fulfill(json={"source_date": "2026-11-09", "items": SIX, "next_cursor": None}))
            # 自選股頁「＋」
            page.goto(f"{base}/portfolio.html")
            page.wait_for_selector("#holdingRows .hv-row")
            page.tap("#addToggle")
            page.wait_for_timeout(500)
            type_query(page, "#addCode", "電子")
            page.wait_for_selector("#addPanel .sp-option")
            verify(page, "自選股頁新增", "#addPanel .sp-option", "#addCode", "portfolio-add")

            # 個股頁頂列與加入比較
            page.goto(f"{base}/stock.html?code=2330")
            page.wait_for_selector("#mainChart canvas, #stockChart canvas, canvas")
            type_query(page, "#codeInput", "電子")
            page.wait_for_selector(".sp-list:not([hidden]) .sp-option")
            verify(page, "個股頁頂列", ".sp-list:not([hidden]) .sp-option", "#codeInput", "stock-bar")
            page.evaluate("() => document.getElementById('codeInput').blur()")
            page.keyboard.press("Escape")
            page.tap("#stockCats button[aria-controls='comparePanel']")
            page.wait_for_timeout(700)
            page.evaluate("() => document.getElementById('compareInput').scrollIntoView({ block: 'center' })")
            type_query(page, "#compareInput", "電子")
            page.wait_for_selector(".sp-list:not([hidden]) .sp-option")
            verify(page, "個股頁加入比較", ".sp-list:not([hidden]) .sp-option", "#compareInput", "stock-compare")

            check("沒有頁面錯誤", not errors, errors)
            browser.close()
    finally:
        proc.kill()
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
