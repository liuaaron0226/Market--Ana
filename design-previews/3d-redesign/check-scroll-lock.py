"""浮層打開時底下的頁面不能跟著捲（本人 2026-11-17 用 iPhone 回報：明細浮板沒東西可以往下滑時，手指一滑就一直滑到底下的頁面）。

全站每一類浮層各走一遍（盤點表在 .scratch/scroll-lock/inventory.md）：
  首頁：籌碼卡等點開的明細板（#detailPanel）、搜尋對話框、產業資金流向「排行與表格」對話框、大盤預測浮板（手機貼底）
  個股頁：關注判斷浮板（手機貼底）
每一類檢查：
  1. 先把頁面捲到中間某處，打開浮層
  2. 手指在浮層上（不能捲的標題列、能捲的內容區）、在浮層外的暗底上，往上、往下各滑一次：底下的頁面不動
  3. 浮層內容比框高的話，在內容上滑會捲內容；捲到底再滑，底下的頁面照樣不動
  4. 關掉之後回到原本的捲動位置，頁面照常可以用手指捲
手機（390，觸控）經由瀏覽器除錯介面一點一點送真正的觸控事件（Input.dispatchTouchEvent，和手指一樣會有捲動串接）；
桌機（1440）用滑鼠滾輪，只測有暗底的那三類（明細板、兩個對話框）；預測與關注浮板在桌機是掛在小鍵旁的小浮板、會跟著頁面走，不鎖。
「底下的頁面不動」量的是頁面內容的實際位置（main 的上緣），不是 window.scrollY——鎖住的做法本身可能改動 scrollY。

自己起預覽伺服器（隨機埠、帶 --owner-extras 才有預測浮板），不碰任何使用者資料。
關注判斷浮板要「這一檔有判斷」才會出現，預覽伺服器沒有樣本，所以用 page.route 給一份固定的判斷。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/check-scroll-lock.py [截圖資料夾]
另外三種邊界（規格審查提出）：兩個浮層接連打開、開著時換頁再從整頁快取回來（模擬 pagehide／pageshow）、開著時從桌機寬度縮到手機寬度。
全部通過結束碼 0，否則 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((name, bool(ok), str(detail)))
    print(("  ok  " if ok else "  FAIL ") + name + (f"｜{detail}" if detail != "" and not ok else ""))


# 頁面內容現在在畫面上的位置：鎖住時 scrollY 可能被改成 0，但內容在畫面上的位置不能動。
PAGE_TOP = "() => Math.round((document.querySelector('main') || document.body.firstElementChild).getBoundingClientRect().top)"


class Finger:
    """手機：瀏覽器除錯介面的觸控捲動手勢；桌機：滑鼠滾輪。dy > 0 表示手指往上推（想看下面的內容）。"""

    def __init__(self, page: Page, mobile: bool) -> None:
        self.page, self.mobile = page, mobile
        self.cdp = page.context.new_cdp_session(page) if mobile else None

    def swipe(self, x: float, y: float, dy: float) -> None:
        if self.mobile:
            # 一點一點送真正的觸控事件（按下、移動十次、放開）。Input.synthesizeScrollGesture 在無頭瀏覽器裡不會捲，不能用。
            touch = lambda kind, points: self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})
            touch("touchStart", [{"x": x, "y": y}])
            for step in range(1, 11):
                touch("touchMove", [{"x": x, "y": y - dy * step / 10}])
            touch("touchEnd", [])
        else:
            self.page.mouse.move(x, y)
            self.page.mouse.wheel(0, dy)
        self.page.wait_for_timeout(250)

    def close(self) -> None:
        if self.cdp:
            self.cdp.detach()


def center(box: dict) -> tuple[float, float]:
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def visible_box(page: Page, selector: str) -> dict | None:
    """元素在畫面內的那一塊（超出視窗的部分裁掉）；看不到回 None。"""
    return page.evaluate("""selector => {
      const node = document.querySelector(selector); if (!node) return null;
      const r = node.getBoundingClientRect();
      const x = Math.max(0, r.left), y = Math.max(0, r.top), right = Math.min(innerWidth, r.right), bottom = Math.min(innerHeight, r.bottom);
      return right - x > 4 && bottom - y > 4 ? { x, y, width: right - x, height: bottom - y } : null;
    }""", selector)


def settle(page: Page) -> None:
    """等標題字型載完、頁高連續一段時間不變（晚到的資料、字型都會把版面撐高或縮短），再開始量位置。"""
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_function("""() => { const h = document.documentElement.scrollHeight;
      if (window.__lastHeight === h) return ++window.__sameHeight >= 6; window.__lastHeight = h; window.__sameHeight = 0; return false; }""",
                           polling=200)


def scroll_to(page: Page, y: int) -> int:
    page.evaluate("y => window.scrollTo({ top: y, behavior: 'instant' })", y)
    page.wait_for_timeout(150)
    return page.evaluate("window.scrollY")


def overlay_case(page: Page, finger: Finger, tag: str, *, start_y: int, open_js: str, is_open: str, close_js: str,
                 frame: str, still: str | None, scroller: str | None, outside: bool, shot: str) -> None:
    """一類浮層的整套檢查。frame：浮層的外框；still：浮層裡不能捲的一塊（標題列）；scroller：浮層裡能捲的那一塊。"""
    y0 = scroll_to(page, start_y)
    top0 = page.evaluate(PAGE_TOP)
    page.evaluate(open_js)
    page.wait_for_function(is_open)
    page.wait_for_timeout(450)   # 等浮起的動畫走完，量到的才是最後的位置
    check(f"{tag}：打開時底下的頁面沒有跳", page.evaluate(PAGE_TOP) == top0, [top0, page.evaluate(PAGE_TOP)])
    if OUT:
        name = shot.replace(" 淺色", "-light").replace(" 深色", "-dark")   # 檔名只用英數，方便在手機與命令列開
        page.screenshot(path=str(OUT / name), timeout=45_000)

    spots = []
    if still and visible_box(page, still):
        spots.append(("標題列", center(visible_box(page, still))))
    if scroller and visible_box(page, scroller):
        spots.append(("內容區", center(visible_box(page, scroller))))
    if not spots:
        spots.append(("浮層", center(visible_box(page, frame))))
    if outside:
        box = page.evaluate(f"document.querySelector({json.dumps(frame)}).getBoundingClientRect().toJSON()")
        below = (box["y"] + box["height"] + page.viewport_size["height"]) / 2
        above = box["y"] / 2
        spot_y = below if page.viewport_size["height"] - (box["y"] + box["height"]) > 40 else (above if box["y"] > 40 else None)
        if spot_y is not None:
            # 滑在左邊的留白：預測與關注浮板沒有暗底，浮層外就是頁面本身，正中間常常是 K 線圖，圖會接走手指另做事
            spots.append(("浮層外的暗底", (4, spot_y)))
    for where, (x, y) in spots:
        for dy, word in ((360, "往上推"), (-360, "往下拉")):
            if where == "內容區":
                page.evaluate(f"document.querySelector({json.dumps(scroller)}).scrollTop = 0")
            finger.swipe(x, y, dy)
            moved = page.evaluate(PAGE_TOP) - top0
            check(f"{tag}：手指在{where}{word}，底下的頁面不動", moved == 0, f"頁面移動 {moved}px")
            if not page.evaluate(is_open):
                check(f"{tag}：滑動不會把浮層關掉", False)
                page.evaluate(open_js)
                page.wait_for_function(is_open)

    if scroller and visible_box(page, scroller):
        room = page.evaluate(f"(n => n.scrollHeight - n.clientHeight)(document.querySelector({json.dumps(scroller)}))")
        if room > 20:
            page.evaluate(f"document.querySelector({json.dumps(scroller)}).scrollTop = 0")
            x, y = center(visible_box(page, scroller))
            finger.swipe(x, y, 160)
            inner = page.evaluate(f"document.querySelector({json.dumps(scroller)}).scrollTop")
            check(f"{tag}：內容比框高時，手指在內容上滑會捲內容", inner > 20, f"內容捲了 {inner}px")
            for _ in range(int(room / 300) + 3):
                finger.swipe(x, y, 360)
            moved = page.evaluate(PAGE_TOP) - top0
            check(f"{tag}：內容捲到底再繼續滑，底下的頁面不動", moved == 0, f"頁面移動 {moved}px")

    page.evaluate(close_js)
    page.wait_for_function(f"!({is_open})")
    page.wait_for_timeout(300)
    y1 = page.evaluate("window.scrollY")
    check(f"{tag}：關掉之後回到原本的捲動位置", y1 == y0 and page.evaluate(PAGE_TOP) == top0, [y0, y1])
    # 滑在頁面左邊的留白：畫面中間常常剛好是 K 線圖，圖本身會接走手指（拖曳、畫線），不代表頁面不能捲。
    # 下面還有 200px 以上可以捲就往上推，否則（已經在頁尾附近）往回拉；兩種都要真的捲動 100px 以上。
    # 看的是「還剩多少可以捲」而不是目前捲到哪：頁面變短（例如拿掉一行小字）時，捲得不深也可能已經貼近頁尾。
    room = page.evaluate("document.documentElement.scrollHeight - window.innerHeight - window.scrollY")
    dy = 300 if room >= 200 else -300
    finger.swipe(4, page.viewport_size["height"] / 2, dy)
    y2 = page.evaluate("window.scrollY")
    check(f"{tag}：關掉之後頁面照常可以捲", (y2 - y1) * dy > 100 * abs(dy), [y1, y2])


DETAIL_OPEN = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'"
JUDGMENT = {
    "judgment_id": "2330|2026-11-07|evening|0.4|preview", "code": "2330", "name": "台積電", "base_date": "2026-11-07",
    "edition": "evening", "method_version": "0.4", "input_version": "preview", "run_mode": "trial", "kind": "judgment",
    "locked_at": "2026-11-07T17:00:00+08:00", "layers": ["technical", "market", "news"], "missing_layers": [],
    "data_cutoffs": {"stock": "2026-11-07", "index": "2026-11-07"}, "base_close": 1460, "status": "接近",
    "trigger_line": {"id": "swing_high", "name": "前波高點", "value": 1500, "kind": "swing"},
    "wrong_line": {"id": "swing_low", "name": "波段低點", "value": 1400, "kind": "swing"},
    "reason": "預覽用的樣本判斷", "reason_news": [], "transition": False, "record_sha256": "0" * 64,
}


def home_cases(page: Page, base: str, size: str, mobile: bool, finger: Finger) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    shoot.ready_home(page)
    settle(page)
    chip_y = page.evaluate("Math.round(document.getElementById('chipGrid').getBoundingClientRect().top + scrollY) - 120")
    overlay_case(page, finger, f"首頁 {size} 明細板（籌碼卡）", start_y=chip_y,
                 open_js="document.querySelector(\"#chipGrid .chip-card[data-chip]\").click()", is_open=DETAIL_OPEN,
                 close_js="document.getElementById('panelClose').click()", frame="#detailPanel",
                 still="#detailPanel .panel-header", scroller="#detailPanel .panel-body", outside=True,
                 shot=f"home-{size}-detail.png")
    overlay_case(page, finger, f"首頁 {size} 搜尋對話框", start_y=900,
                 open_js="document.getElementById('searchToggle').click()",
                 is_open="document.getElementById('stockSearchDialog').open",
                 close_js="document.getElementById('stockSearchClose').click()", frame="#stockSearchDialog",
                 still="#stockSearchDialog", scroller=None, outside=True, shot=f"home-{size}-search.png")
    rot_y = page.evaluate("Math.round(document.getElementById('rotationBlock').getBoundingClientRect().top + scrollY) - 80")
    overlay_case(page, finger, f"首頁 {size} 排行與表格對話框", start_y=rot_y,
                 open_js="document.querySelector('#rotationBlock .rotation-open').click()",
                 is_open="document.getElementById('rotationDialog').open",
                 close_js="document.querySelector('#rotationDialog [data-dialog-close]').click()", frame="#rotationDialog",
                 still="#rotationDialog .float-dialog-head", scroller="#rotationDialog", outside=True,
                 shot=f"home-{size}-rotation.png")
    # 換到比較矮的一類時，home-fold.js 會在頁尾墊高度免得整頁跳（它聽 scroll 事件收掉用不到的部分）；
    # 墊著高度的時候開關浮層，鎖住與放開觸發的 scroll 事件不能讓它多收或少收，關掉要回到原位。
    page.evaluate("document.querySelector('#homeCats [data-cat=\"chips\"]').click()")
    page.wait_for_timeout(200)
    scroll_to(page, page.evaluate("document.documentElement.scrollHeight"))
    page.evaluate("document.querySelector('#homeCats [data-cat=\"chips\"]').click()")
    page.wait_for_timeout(200)
    held = page.evaluate("document.querySelector('.hm-cat-hold').offsetHeight")
    check(f"首頁 {size} 換分類後頁尾墊著高度（下一項的前提）", held > 0, held)
    overlay_case(page, finger, f"首頁 {size} 換分類後開搜尋對話框", start_y=page.evaluate("window.scrollY"),
                 open_js="document.getElementById('searchToggle').click()",
                 is_open="document.getElementById('stockSearchDialog').open",
                 close_js="document.getElementById('stockSearchClose').click()", frame="#stockSearchDialog",
                 still="#stockSearchDialog", scroller=None, outside=True, shot=f"home-{size}-search-held.png")
    if mobile:
        page.wait_for_selector(".forecast-chip")
        chip_top = page.evaluate("Math.round(document.querySelector('.forecast-chip').getBoundingClientRect().top + scrollY)")
        overlay_case(page, finger, f"首頁 {size} 大盤預測浮板（貼底）", start_y=max(0, chip_top - 300),
                     open_js="document.querySelector('.forecast-chip').click()",
                     is_open="!document.getElementById('forecastPanel').hidden",
                     close_js="document.querySelector('#forecastPanel [data-close]').click()", frame="#forecastPanel",
                     still="#forecastPanel .forecast-head", scroller="#forecastPanel", outside=True,
                     shot=f"home-{size}-forecast.png")


def edge_cases(page: Page, base: str, finger: Finger) -> None:
    """規格審查提出的三種邊界：兩個浮層同時開、開著時換頁（再按上一頁回來）、開著時跨過手機／桌機寬度。"""
    page.goto(base + "/", wait_until="domcontentloaded")
    shoot.ready_home(page)
    settle(page)

    # 預測浮板開著時點搜尋：預測浮板因為點了外面而收起，對話框接著鎖；頁面全程不動，關掉回原位
    page.wait_for_selector(".forecast-chip")
    y0 = scroll_to(page, max(0, page.evaluate("Math.round(document.querySelector('.forecast-chip').getBoundingClientRect().top + scrollY)") - 300))
    top0 = page.evaluate(PAGE_TOP)
    page.evaluate("document.querySelector('.forecast-chip').click()")
    page.wait_for_function("!document.getElementById('forecastPanel').hidden")
    page.evaluate("document.getElementById('searchToggle').click()")
    page.wait_for_function("document.getElementById('stockSearchDialog').open")
    page.wait_for_timeout(300)
    finger.swipe(*center(visible_box(page, "#stockSearchDialog")), 360)
    check("兩個浮層接連打開：頁面不動", page.evaluate(PAGE_TOP) == top0, page.evaluate(PAGE_TOP) - top0)
    page.evaluate("document.getElementById('stockSearchClose').click()")
    page.wait_for_timeout(300)
    check("兩個浮層接連打開：全部關掉後回到原位", page.evaluate("window.scrollY") == y0 and page.evaluate(PAGE_TOP) == top0,
          [y0, page.evaluate("window.scrollY")])

    # 開著時換頁：離開前要放開並捲回原位（瀏覽器才記得住）；從整頁快取回來時浮層還開著，要再鎖回去
    y0 = scroll_to(page, 800)
    top0 = page.evaluate(PAGE_TOP)
    page.evaluate("document.getElementById('searchToggle').click()")
    page.wait_for_function("document.getElementById('stockSearchDialog').open")
    page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))")
    check("開著時換頁：離開前頁面放開、捲回原位", page.evaluate("getComputedStyle(document.body).position") != "fixed"
          and page.evaluate("window.scrollY") == y0, [y0, page.evaluate("window.scrollY")])
    page.evaluate("dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))")
    finger.swipe(*center(visible_box(page, "#stockSearchDialog")), 360)
    check("從整頁快取回來：浮層還開著，頁面再鎖回去", page.evaluate("getComputedStyle(document.body).position") == "fixed"
          and page.evaluate(PAGE_TOP) == top0, page.evaluate(PAGE_TOP) - top0)
    page.evaluate("document.getElementById('stockSearchClose').click()")
    page.wait_for_timeout(300)
    check("從整頁快取回來再關掉：回到原位", page.evaluate("window.scrollY") == y0, [y0, page.evaluate("window.scrollY")])


def width_change_case(browser, base: str, errors: list[str]) -> None:
    """桌機寬度打開預測浮板（不鎖），把視窗縮到手機寬度：浮板變成貼底，頁面要鎖住；關掉回原位。"""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce", locale="zh-TW", timezone_id="Asia/Taipei")
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.clock.set_fixed_time(shoot.FIXED_NOW)
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(base + "/", wait_until="domcontentloaded")
        shoot.ready_home(page)
        settle(page)
        page.wait_for_selector(".forecast-chip")
        scroll_to(page, max(0, page.evaluate("Math.round(document.querySelector('.forecast-chip').getBoundingClientRect().top + scrollY)") - 300))
        page.evaluate("document.querySelector('.forecast-chip').click()")
        page.wait_for_function("!document.getElementById('forecastPanel').hidden")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        top0 = page.evaluate(PAGE_TOP)
        fixed = page.evaluate("getComputedStyle(document.getElementById('forecastPanel')).position")
        wheel = Finger(page, False)
        panel_top = page.evaluate("document.getElementById('forecastPanel').getBoundingClientRect().top")
        # 浮板上與浮板外（左邊留白）各滑；上下各一次：頁面剛好在頭或尾時，單一方向本來就捲不動。
        # 只滑浮板上不夠：它的內容比框高，滾輪被它自己的 overscroll-behavior 擋住，修前也不會穿透。
        moves = []   # 每滑一次就量：上下兩次的位移會互相抵銷，只看最後會漏掉
        for spot in (center(visible_box(page, "#forecastPanel")), (4, panel_top / 2)):
            for dy in (360, -360):
                wheel.swipe(*spot, dy)
                moves.append(page.evaluate(PAGE_TOP) - top0)
        check("開著時縮到手機寬度：浮板變成貼底、頁面鎖住", fixed == "fixed" and not any(moves), [fixed, moves])
        page.evaluate("document.querySelector('#forecastPanel [data-close]').click()")
        page.wait_for_timeout(300)
        check("開著時縮到手機寬度：關掉後回到原位", page.evaluate(PAGE_TOP) == top0, page.evaluate(PAGE_TOP) - top0)
    except Exception as error:  # noqa: BLE001
        check("開著時換寬度：走到一半出錯", False, str(error).splitlines()[0][:200])
    context.close()


def stock_cases(page: Page, base: str, size: str, finger: Finger) -> None:
    def judgments(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "available": True, "code": "2330", "base_date": "2026-11-07", "latest": {"evening": JUDGMENT, "morning": None},
            "default_edition": "evening", "history": [{**JUDGMENT, "reconciliations": {}}]}))
    page.route("**/api/watch/judgments/2330", judgments)
    page.goto(base + f"/stock.html?code={shoot.STOCK}", wait_until="domcontentloaded")
    shoot.ready_stock(page)
    page.wait_for_selector(".wj-chip")
    settle(page)   # 即時行情一直在輪詢，等不到網路靜止，改等頁高穩定
    chip_top = page.evaluate("Math.round(document.querySelector('.wj-chip').getBoundingClientRect().top + scrollY)")
    overlay_case(page, finger, f"個股 {size} 關注判斷浮板（貼底）", start_y=max(0, chip_top - 200),
                 open_js="document.querySelector('.wj-chip').click()",
                 is_open="!document.getElementById('watchJudgmentPanel').hidden",
                 close_js="document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))", frame="#watchJudgmentPanel",
                 still=None, scroller="#watchJudgmentPanel", outside=True, shot=f"stock-{size}-judgment.png")
    page.unroute("**/api/watch/judgments/2330")


def main() -> None:
    proc, base = shoot.start_server("--features", "core", "--owner-extras")
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for size, mobile in (("390", True), ("1440", False)):
                for theme in (("light", "dark") if mobile else ("light",)):
                    width, height = shoot.SIZES[size]
                    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce",
                                                  locale="zh-TW", timezone_id="Asia/Taipei", has_touch=mobile, is_mobile=mobile,
                                                  device_scale_factor=2 if mobile else 1, color_scheme=theme)
                    context.add_init_script(f"try {{ localStorage.setItem('market-theme', '{theme}'); }} catch (e) {{}}")
                    page = context.new_page()
                    page.set_default_timeout(15_000)
                    page.clock.set_fixed_time(shoot.FIXED_NOW)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    finger = Finger(page, mobile)
                    label = f"{size} {'淺色' if theme == 'light' else '深色'}"
                    for name, run in ((f"首頁 {label}", lambda: home_cases(page, base, label, mobile, finger)),
                                      (f"個股 {label}", lambda: stock_cases(page, base, label, finger) if mobile else None),
                                      (f"邊界 {label}", lambda: edge_cases(page, base, finger) if mobile and theme == "light" else None)):
                        try:
                            run()
                        except Exception as error:  # noqa: BLE001
                            check(f"{name}：走到一半出錯", False, str(error).splitlines()[0][:200])
                    finger.close()
                    context.close()
            width_change_case(browser, base, errors)
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    check("全程沒有頁面腳本錯誤", not errors, errors[:3])
    failed = [(name, detail) for name, ok, detail in results if not ok]
    print(f"{len(results) - len(failed)}／{len(results)} 項通過")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
