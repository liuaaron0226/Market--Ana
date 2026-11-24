"""畫線工具的本機瀏覽器旅程（桌機與手機寬度各走一遍）。
工具的選法（本人 11-24 選樣品 A）：週期鍵左邊一枚「分析工具」小鍵（#toolsKey），點了浮出分三組（線條／區間／標記）的選單（#drawTools），
       每一項是圖示＋名稱；選了就收起，鍵上改寫正在用的工具、旁邊一顆 ✕（#toolsExit）離開；選了線時同一行多出刪除（與到價提醒的鈴鐺）。
       旅程裡「按某個工具」一律經過這個選單（ToolItem）：沒選著就打開選單點那一項，已經選著就按 ✕ 放下。
票 01：水平線畫得出來，而且存得住。票 02：點線選取、拖動、刪除（刪除鈕與 Delete）、Esc。
票 03：趨勢線與射線——點兩下成線、預覽線、取消、拖整條、拖端點、切日週月、重新整理後仍在。
票 04：矩形——點兩下對角、預覽、點邊線選取、拖整個、拖角落、切日週月、重新整理後仍在。
票 05：黃金分割——點兩下出七條線、預覽、比例的位置、選取、拖整組、拖一端、切日週月、重新整理後仍在。
票 06：文字標註——點一下出現輸入框、確定／取消／空白、字數上限、純文字顯示、選取、拖動、修改、切日週月、重新整理後仍在；
       手機另驗輸入框大小、鍵盤彈出（用縮小視窗模擬）時不被遮住、寫完頁面回到原位。
票 07：儲存失敗時不白畫——斷線（真的關掉瀏覽器的網路）時畫、拖、寫字、刪都留在畫面上並出現「未儲存」，
       恢復後照先後補存；還沒存上去就重新整理會先被瀏覽器提醒；被限速照伺服器說的秒數等；伺服器出錯自動再試；
       被拒絕的不重試、提示改成「沒存成」；換股票不混檔。失敗用攔截請求製造，不改伺服器。
票 08：手機觸控驗收——六種工具在手機上用手指（會晃幾像素、會滑、會兩指）逐一走過畫、選取、拖整個、拖把手、刪除、
       重新整理後還在，並和桌機同一帳號看到的一致；長按「分析工具」與選單項目不選取文字、不誤選；320 寬（iPhone 顯示縮放）選單與那一行放得下。
       觸控用 CDP 的 Input.dispatchTouchEvent 送（瀏覽器的手機模擬）；真機才看得出來的另列在票檔，留給本人。
大盤（本人 11-24「大盤也要分析工具」）：首頁 K 線同一套選單（沒有到價提醒），畫的存在 IDX＋指數鍵（台股加權＝IDXTWII）底下；
       畫、重新整理後還在、點線只選線不選 K 棒、刪除、分時不出現（390 觸控與 1440 桌機各一遍）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），Access 由 cookie 模擬；
不連正式站、不碰 8876／8897。先在 cloud/ 跑過 `node build.mjs` 讓 dist 是最新的。

    python cloud/test/browser_drawing_journey.py            # 自己起一個旅程伺服器
    python cloud/test/browser_drawing_journey.py --base http://127.0.0.1:5192   # 用已經開著的預覽

截圖與報告寫到 .tmp/drawing-journey/（不入庫）。任何一項不符就以非零碼結束。
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/drawing-journey"
OWNER, OTHER = "owner@example.com", "a@example.com"

# 回傳畫布上「用畫線顏色畫滿三成以上寬度」的橫線位置（CSS 像素，相對畫布上緣）。
# 可以指定只看畫布的右邊一段（from：從寬度的幾成看起）與門檻（share：佔畫布寬度幾成才算）。
LINES_JS = """options => {
  const { from = 0, share = 0.3, selector = '#chartHost canvas' } = options || {};
  const canvas = document.querySelector(selector);
  const { width, height } = canvas;
  const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  const hex = getComputedStyle(document.documentElement).getPropertyValue('--draw').trim();
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const rows = [];
  for (let y = 0; y < height; y++) {
    let hits = 0;
    for (let x = Math.round(width * from); x < width; x++) {
      const i = (y * width + x) * 4;
      if (Math.abs(data[i] - r) + Math.abs(data[i + 1] - g) + Math.abs(data[i + 2] - b) < 60) hits++;
    }
    if (hits > width * share) rows.push(y);
  }
  const scale = height / canvas.getBoundingClientRect().height;
  const lines = [];
  rows.forEach(y => { if (!lines.length || y - lines[lines.length - 1].at(-1) > 1) lines.push([y]); else lines[lines.length - 1].push(y); });
  return lines.map(group => Math.round((group[0] + group.at(-1)) / 2 / scale * 10) / 10);
}"""

# 選取把手：線的下方 3～5 像素那幾列，只有把手的圓邊會用到畫線顏色（價位標示在線的上方）。回傳那幾列的畫線色像素數。
HANDLE_JS = """lineY => {
  const canvas = document.querySelector('#chartHost canvas');
  const scale = canvas.height / canvas.getBoundingClientRect().height;
  const hex = getComputedStyle(document.documentElement).getPropertyValue('--draw').trim();
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const top = Math.round((lineY + 3) * scale), rows = Math.max(1, Math.round(2 * scale));
  const data = canvas.getContext('2d').getImageData(0, top, canvas.width, rows).data;
  let hits = 0;
  for (let i = 0; i < data.length; i += 4) if (Math.abs(data[i] - r) + Math.abs(data[i + 1] - g) + Math.abs(data[i + 2] - b) < 60) hits++;
  return hits;
}"""

# 畫布上藍紫色（畫線色，含預覽線的淡色與斜線邊緣半透明的像素）的像素：個數與外框（CSS 像素，相對畫布左上角）。
# 圖表其他東西是紅、綠、橘、金與帶一點藍的灰，藍得沒這麼明顯（幾乎透明的邊緣像素也不算）；斜線沒辦法像水平線那樣用「整列都是畫線色」來認。
INK_JS = """() => {
  const canvas = document.querySelector('#chartHost canvas');
  const { width, height } = canvas;
  const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  const scale = height / canvas.getBoundingClientRect().height;
  let count = 0, minX = width, maxX = -1, minY = height, maxY = -1;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    if (data[i + 3] > 200 && data[i + 2] > 150 && data[i + 2] - data[i] > 30 && data[i + 2] - data[i + 1] > 25) {
      count++; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
  }
  const css = value => Math.round(value / scale * 10) / 10;
  return count ? { count, minX: css(minX), maxX: css(maxX), minY: css(minY), maxY: css(maxY), width: css(width) } : { count: 0, width: css(width) };
}"""

# 時間軸那一條（畫布最下面）的像素指紋：圖表有沒有平移，看它變了沒有。
AXIS_JS = """() => {
  const canvas = document.querySelector('#chartHost canvas');
  const scale = canvas.height / canvas.getBoundingClientRect().height;
  const rows = Math.round(16 * scale);
  const data = canvas.getContext('2d').getImageData(0, canvas.height - Math.round(20 * scale), canvas.width, rows).data;
  let hash = 0;
  for (let i = 0; i < data.length; i += 4) hash = (hash * 31 + data[i] + data[i + 1] * 3 + data[i + 2] * 7) >>> 0;
  return hash;
}"""


# 「分析工具」選單（本人 11-24 選樣品 A）：分三組，順序照 draw-tools-ui.js 的 TOOLS
TOOL_MENU = [("線條", [("trend", "趨勢線"), ("ray", "射線"), ("hline", "水平線"), ("channel", "平行通道")]),
             ("區間", [("rect", "矩形"), ("fib", "黃金分割"), ("measure", "量測"), ("vprofile", "區間成交量分布")]),
             ("標記", [("text", "文字標註"), ("alert", "到價提醒")])]


def menu_order(skip: tuple[str, ...] = ()) -> list[str]:
    """選單由上到下應有的樣子：「#組名」是小標題，「代號:可讀名稱:看得到的字」是一項（看得到的字就是名稱，圖示沒有字）。"""
    order: list[str] = []
    for group, tools in TOOL_MENU:
        items = [f"{kind}:{name}:{name}" for kind, name in tools if kind not in skip]
        if items:
            order += [f"#{group}", *items]
    return order


# 選單現在的樣子：開著沒、由上到下的小標題與項目、每一項的大小、選單的外框與自己的捲動、整頁有沒有橫向捲動。
MENU_JS = """() => { const menu = document.getElementById('drawTools'); if (!menu) return null;
  const box = menu.getBoundingClientRect(), items = [...menu.querySelectorAll('button[data-tool]')];
  return { open: !menu.hidden && menu.getClientRects().length > 0,
    order: [...menu.children].filter(node => node.matches('.dt-group, button[data-tool]'))
      .map(node => node.matches('.dt-group') ? '#' + node.textContent.trim() : `${node.dataset.tool}:${node.getAttribute('aria-label')}:${node.textContent.trim()}`),
    sizes: items.map(item => [item.offsetWidth, item.offsetHeight]), clipped: items.filter(item => item.scrollWidth > item.clientWidth + 1).map(item => item.dataset.tool),
    left: box.left, right: box.right, top: box.top, bottom: box.bottom, scrollTop: menu.scrollTop,
    scrollHeight: menu.scrollHeight, clientHeight: menu.clientHeight, overflowY: getComputedStyle(menu).overflowY,
    fits: document.documentElement.scrollWidth <= innerWidth, width: innerWidth, height: innerHeight }; }"""

# 小鍵的觸控範圍：.chart-chip 用 ::before 往外補（inset 是負的），外觀的大小另外回（offsetWidth／offsetHeight：剛按過的那一顆
# 還在「按下去」的縮小動畫裡，外框會小一點）。沒顯示回 null。
HIT_JS = """selector => { const node = document.querySelector(selector); if (!node || !node.getClientRects().length) return null;
  const box = node.getBoundingClientRect(), before = getComputedStyle(node, '::before'), px = value => parseFloat(value) || 0;
  return { width: node.offsetWidth - px(before.left) - px(before.right), height: node.offsetHeight - px(before.top) - px(before.bottom),
    shown: [node.offsetWidth, node.offsetHeight], x: box.x, y: box.y, right: box.right, bottom: box.bottom, middle: box.y + box.height / 2,
    select: getComputedStyle(node).userSelect }; }"""


def menu_fits(layout: dict | None, count: int) -> bool:
    """選單開著、有 count 項、每項至少 44 高（手指點得到）、整個在畫面寬度內、整頁不橫向捲動、名稱沒有被切掉。"""
    return (bool(layout) and layout["open"] and len(layout["sizes"]) == count and all(height >= 44 for _, height in layout["sizes"])
            and layout["left"] >= -0.5 and layout["right"] <= layout["width"] + 0.5 and layout["fits"] and layout["clipped"] == [])


class ToolItem:
    """選單裡的一種工具。click()／tap()＝使用者「按這個工具」：還沒選著就打開「分析工具」選單（已經開著就不再按）點那一項
    （選單自己收起）；已經選著（再按一次就是放下）就按鍵旁的 ✕ 離開畫線。aria-pressed 等屬性讀選單裡那一項（選單收著也會跟著改）。"""

    def __init__(self, page, kind: str) -> None:
        self.page, self.kind = page, kind
        self.item = page.locator(f"#drawTools button[data-tool={kind}]")
        self.key = page.locator("#toolsKey")
        self.exit = page.locator("#toolsExit")

    def _press(self, touch: bool) -> None:
        if self.item.get_attribute("aria-pressed") == "true":
            target = self.exit
        else:
            if self.key.get_attribute("aria-expanded") != "true":
                (self.key.tap() if touch else self.key.click())
            self.item.wait_for(state="visible", timeout=5000)
            self.item.scroll_into_view_if_needed()
            target = self.item
        (target.tap() if touch else target.click())

    def click(self) -> None:
        self._press(False)

    def tap(self) -> None:
        self._press(True)

    def get_attribute(self, name: str):
        return self.item.get_attribute(name)

    def __getattr__(self, name: str):
        return getattr(self.item, name)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Journey:
    def __init__(self, base: str) -> None:
        self.base = base
        self.checks: list[dict[str, object]] = []
        self.console: list[str] = []
        self.failed: list[str] = []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("  ok  " if ok else "  FAIL") + f" {name}" + (f"｜{detail}" if detail != "" and not ok else ""))

    def context(self, browser, email: str | None, **options):
        context = browser.new_context(**options)
        # 只連本機：外部字型等請求一律擋掉，旅程不受網路快慢影響（畫面改用系統字型）。
        context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())
        if email:
            context.add_cookies([{"name": "test_as", "value": quote(email), "url": self.base}])
        return context

    # expect_failures：這一頁會故意讓畫線的存檔失敗（票 07），那些失敗不算進最後的「畫線的讀寫請求沒有失敗」。
    def open(self, context, code: str = "2330", expect_failures: bool = False):
        page = context.new_page()
        self.watch(page, expect_failures)
        page.goto(f"{self.base}/stock.html?code={code}")
        self.ready(page, code)
        return page

    def watch(self, page, expect_failures: bool = False) -> None:
        # 「Failed to load resource」另由下面的回應狀態記錄（帶網址），這裡只收程式本身的錯誤訊息。
        page.on("console", lambda message: self.console.append(message.text)
                if message.type == "error" and "Failed to load resource" not in message.text else None)
        page.on("response", lambda response: self.failed.append(f"{response.status} {response.request.method} {response.url.replace(self.base, '')}")
                if response.status >= 400 and not (expect_failures and "/api/drawings/" in response.url) else None)
        page.on("pageerror", lambda error: self.console.append(f"pageerror: {error}"))

    def ready(self, page, code: str) -> None:
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
        page.wait_for_function("code => document.getElementById('stockCode').textContent === code", arg=code, timeout=60_000)
        page.locator("#chartHost canvas").scroll_into_view_if_needed()
        page.wait_for_timeout(700)  # 畫線與歷史在分析畫出來之後才讀

    # ---- 「分析工具」選單（本人 11-24 選樣品 A；沒有常駐的工具列了，也就不用先展開） ----
    def tool(self, page, kind: str) -> ToolItem:
        return ToolItem(page, kind)

    def open_menu(self, page, touch: bool = False) -> None:
        key = page.locator("#toolsKey")
        if key.get_attribute("aria-expanded") != "true":
            (key.tap() if touch else key.click())
        page.locator("#drawTools").wait_for(state="visible", timeout=5000)

    def close_menu(self, page, touch: bool = False) -> None:
        """再按一次「分析工具」收起（不用 Esc：Esc 在個股頁也會取消選取的線）。"""
        key = page.locator("#toolsKey")
        if key.get_attribute("aria-expanded") == "true":
            (key.tap() if touch else key.click())
        page.locator("#drawTools").wait_for(state="hidden", timeout=5000)

    def menu(self, page) -> dict | None:
        return page.evaluate(MENU_JS)

    def hit(self, page, selector: str) -> dict | None:
        return page.evaluate(HIT_JS, selector)

    def lines(self, page) -> list[float]:
        return page.evaluate(LINES_JS)

    def saved(self, page, code: str = "2330") -> list[dict]:
        return page.evaluate("async code => (await (await fetch(`/api/drawings/${code}`, { cache: 'no-store' })).json()).items", code)

    def point(self, page, fx: float, fy: float) -> tuple[float, float, float]:
        box = page.locator("#chartHost canvas").bounding_box()
        return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy, box["height"] * fy

    def choose_period(self, page, value: str) -> None:
        page.locator("#periodSelect").select_option(value)
        page.locator("#periodSelect").dispatch_event("change")
        page.wait_for_timeout(250)

    def handle(self, page, line_y: float) -> bool:
        return page.evaluate(HANDLE_JS, line_y) > 0

    # 立體版拿掉了 K 棒的浮窗（本人 11-15）：選了一根 K 棒，十字線停在那一根、右側價格欄標出那一天的開高低收；
    # 再點一次同一根是取消選取。這裡讀圖表自己記的「選了哪一根、右側標了什麼」（stock.js 的 StockView.chart）。
    def bar(self, page) -> dict:
        return page.evaluate("() => { const chart = window.StockView.chart(); return { selection: chart.selection, picked: chart.debugBoxes.picked }; }")

    def shows_bar(self, page) -> bool:
        """選了一根 K 棒，右側標出那一天的開、高、低、收。"""
        state = self.bar(page)
        return state["selection"] >= 0 and bool(state["picked"]) and [tag["name"] for tag in state["picked"]["tags"]] == ["開", "高", "低", "收"]

    def axis(self, page) -> int:
        return page.evaluate(AXIS_JS)

    def canvas_xy(self, page, fx: float, canvas_y: float) -> tuple[float, float]:
        box = page.locator("#chartHost canvas").bounding_box()
        return box["x"] + box["width"] * fx, box["y"] + canvas_y

    def ink(self, page) -> dict:
        return page.evaluate(INK_JS)

    def at(self, page, canvas_x: float, canvas_y: float) -> tuple[float, float]:
        box = page.locator("#chartHost canvas").bounding_box()
        return box["x"] + canvas_x, box["y"] + canvas_y

    def draw(self, page, fx: float, fy: float, touch: bool = False) -> float:
        tool = self.tool(page, "hline")
        # 先選工具再量位置：選單從圖的上方浮出，點選單裡的工具時頁面可能捲動讓它露出來，先量的位置會整個偏掉。
        if touch:
            tool.tap()
        else:
            tool.click()
        x, y, canvas_y = self.point(page, fx, fy)
        with page.expect_response(lambda response: "/api/drawings/" in response.url and response.request.method == "PUT") as saved:
            if touch:
                page.touchscreen.tap(x, y)
            else:
                page.mouse.click(x, y)
        self.check("畫完自動儲存（PUT 回 201）", saved.value.status == 201, saved.value.status)
        return canvas_y


def desktop(journey: Journey, browser) -> None:
    print("桌機寬度 1280")
    viewport = {"width": 1280, "height": 900}
    context = journey.context(browser, OWNER, viewport=viewport)
    page = journey.open(context)
    tools = page.locator("#toolsKey")   # 「分析工具」鍵：能畫的週期都在
    menu = page.locator("#drawTools")
    exit_key = page.locator("#toolsExit")
    tool = journey.tool(page, "hline")
    item = tool.item

    journey.check("日 K 上方有「分析工具」鍵，選單收著", tools.is_visible() and tools.inner_text().strip() == "分析工具"
                  and tools.bounding_box()["y"] < page.locator("#chartHost").bounding_box()["y"] and menu.is_hidden())
    journey.open_menu(page)
    layout = journey.menu(page)
    journey.check("點「分析工具」浮出選單：分線條、區間、標記三組，依序十種，每一項是圖示＋名稱、有可讀名稱", layout["order"] == menu_order(), layout["order"])
    journey.check("選單每一項至少 44px 高、名稱沒被切掉、整個在畫面寬度內", menu_fits(layout, TOOL_COUNT), layout)
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-00-tools-menu.png"))
    page.keyboard.press("Escape")
    journey.check("按 Esc 收起選單、什麼都沒選", menu.is_hidden() and tools.get_attribute("aria-expanded") == "false" and item.get_attribute("aria-pressed") == "false")
    tools.focus()
    page.keyboard.press("Enter")
    opened = menu.is_visible()
    item.focus()
    page.keyboard.press("Enter")
    journey.check("鍵盤：在「分析工具」按 Enter 打開選單、在一項上按 Enter 選中；選單收起、焦點回到鍵上，鍵上改寫「水平線」（深色）、旁邊出現離開鈕",
                  opened and tool.get_attribute("aria-pressed") == "true" and menu.is_hidden()
                  and page.evaluate("() => document.activeElement === document.getElementById('toolsKey')")
                  and tools.inner_text().strip() == "水平線" and "is-on" in (tools.get_attribute("class") or "") and exit_key.is_visible(),
                  {"opened": opened, "key": tools.inner_text(), "class": tools.get_attribute("class")})
    page.keyboard.press("Enter")
    item.focus()
    page.keyboard.press("Space")
    journey.check("鍵盤：再打開選單、在選中的那一項按空白鍵＝放下工具（鍵回到「分析工具」、離開鈕收起）", tool.get_attribute("aria-pressed") == "false"
                  and tools.inner_text().strip() == "分析工具" and not exit_key.is_visible())
    journey.tool(page, "trend").click()
    tool.click()
    journey.check("選著趨勢線時從選單選水平線：直接換過去（只有一項選著）", tool.get_attribute("aria-pressed") == "true"
                  and journey.tool(page, "trend").get_attribute("aria-pressed") == "false" and tools.inner_text().strip() == "水平線")
    journey.check("離開鈕只有圖示、有可讀名稱「離開畫線」", exit_key.inner_text().strip() == "" and exit_key.get_attribute("aria-label") == "離開畫線")
    exit_key.click()
    journey.check("按離開鈕：放下工具、離開鈕收起", tool.get_attribute("aria-pressed") == "false" and not exit_key.is_visible())

    journey.check("一開始沒有畫線", journey.lines(page) == [] and journey.saved(page) == [])
    x, y, _ = journey.point(page, 0.4, 0.5)
    page.mouse.click(x, y)
    journey.check("沒選工具時點一下照舊顯示該根 K 棒的數值", journey.shows_bar(page) and journey.lines(page) == [])
    page.mouse.click(x, y)
    journey.check("再點一次同一根 K 棒就取消選取（本人 11-15），也不會畫出線", journey.bar(page)["selection"] == -1 and journey.lines(page) == [],
                  journey.bar(page))

    canvas_y = journey.draw(page, 0.4, 0.3)
    lines = journey.lines(page)
    journey.check("點的那個價位出現一條水平線", len(lines) == 1 and abs(lines[0] - canvas_y) <= 1.5, {"lines": lines, "clicked": canvas_y})
    journey.check("工具自動回到一般模式", tool.get_attribute("aria-pressed") == "false")
    items = journey.saved(page)
    journey.check("雲端存了這一條（水平線、日期＋價格）", len(items) == 1 and items[0]["kind"] == "hline"
                  and len(items[0]["anchors"]) == 1 and items[0]["anchors"][0]["price"] > 0, items)
    price = items[0]["anchors"][0]["price"] if items else None
    page.screenshot(path=str(OUT / "desktop-01-drawn.png"))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-02-chart.png"))

    x, y, _ = journey.point(page, 0.5, 0.6)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 160, y, steps=6)
    page.mouse.up()
    page.wait_for_timeout(150)
    journey.check("拖曳平移後線還在（或該價位已不在畫面內時不留殘影）", len(journey.lines(page)) <= 1, journey.lines(page))
    page.mouse.move(x, y)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(200)
    journey.check("滾輪縮小後最多一條線", len(journey.lines(page)) <= 1, journey.lines(page))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-03-zoomed-out.png"))

    journey.choose_period(page, "weekly")
    journey.check("週 K：分析工具在、線在", tools.is_visible() and len(journey.lines(page)) == 1, journey.lines(page))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-04-weekly.png"))
    journey.choose_period(page, "monthly")
    journey.check("月 K：分析工具在、線在", tools.is_visible() and len(journey.lines(page)) == 1, journey.lines(page))
    journey.choose_period(page, "60m")
    journey.check("60 分 K：分析工具在（本人 11-18 可以畫）、日 K 畫的線不出現（各畫各的）", tools.is_visible() and journey.lines(page) == [], journey.lines(page))
    # 本人 11-18：秒 K 換成分時；分時下 K 線整塊讓位，分析工具鍵也不出現
    journey.choose_period(page, "ts")
    journey.check("分時：分析工具（整組）與 K 線（連同畫線）都不顯示", not tools.is_visible() and not page.locator(".dt-ui").is_visible()
                  and not page.locator("#chartHost").is_visible())
    journey.choose_period(page, "daily")
    journey.check("切回日 K：分析工具與線都恢復", tools.is_visible() and len(journey.lines(page)) == 1, journey.lines(page))

    page.reload()
    journey.ready(page, "2330")
    journey.check("重新整理後線還在", len(journey.lines(page)) == 1 and len(journey.saved(page)) == 1, journey.lines(page))

    page.locator("#codeInput").fill("2317")
    page.locator("#searchForm button[type=submit]").click()
    journey.ready(page, "2317")
    journey.check("換到另一檔：看不到上一檔的線", journey.lines(page) == [] and journey.saved(page, "2317") == [], journey.lines(page))
    journey.draw(page, 0.5, 0.4)
    journey.check("另一檔自己的線", len(journey.lines(page)) == 1 and len(journey.saved(page, "2317")) == 1)
    page.go_back()
    journey.ready(page, "2330")
    items = journey.saved(page)
    journey.check("回到原本那一檔：顯示的是它自己的那一條", len(journey.lines(page)) == 1 and len(items) == 1 and items[0]["anchors"][0]["price"] == price)

    second = journey.context(browser, OWNER, viewport=viewport)
    other_page = journey.open(second)
    journey.check("同一帳號從另一個瀏覽器進來也看得到", len(journey.lines(other_page)) == 1)
    second.close()

    applicant = journey.context(browser, OTHER, viewport=viewport)
    applied = applicant.request.post(f"{journey.base}/apply/api/apply", data={"nickname": "A"}, headers={"origin": journey.base})
    pending = applicant.request.get(f"{journey.base}/api/drawings/2330")
    approved = context.request.post(f"{journey.base}/api/admin/users/{quote(OTHER)}/approve", data={}, headers={"origin": journey.base})
    journey.check("未核准的帳號讀不到畫線", applied.ok and pending.status == 403 and approved.ok, [applied.status, pending.status, approved.status])
    other_page = journey.open(applicant)
    journey.check("別的帳號看不到我的線", journey.lines(other_page) == [] and journey.saved(other_page) == [])
    applicant.close()

    anonymous = journey.context(browser, None, viewport=viewport)
    journey.check("未登入讀不到畫線", anonymous.request.get(f"{journey.base}/api/drawings/2330").status == 401)
    anonymous.close()

    broken = journey.context(browser, OWNER, viewport=viewport)
    broken.route("**/api/drawings/**", lambda route: route.abort())
    broken_page = journey.open(broken)
    journey.check("畫線讀取失敗時個股頁其他內容照常顯示", broken_page.locator("#close").inner_text().strip() not in ("", "—")
                  and broken_page.locator("#chartHost canvas").is_visible() and journey.lines(broken_page) == [])
    broken.close()
    context.close()


def mobile(journey: Journey, browser) -> None:
    print("手機寬度 390（觸控）")
    context = journey.context(browser, OWNER, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context)
    tools = page.locator("#toolsKey")
    tool = journey.tool(page, "hline")
    before = len(journey.lines(page))
    journey.check("手機：桌機畫的線看得到", before == 1, before)
    journey.check("手機：分析工具那一行不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
                  page.evaluate("() => [document.documentElement.scrollWidth, innerWidth]"))
    hit = journey.hit(page, "#toolsKey")
    journey.check("手機：「分析工具」鍵的觸控範圍夠大（至少 44px）", hit and hit["width"] >= 44 and hit["height"] >= 44, hit)

    # 手指按住「分析工具」不放（長按）再放開：選單打開等人點，不會選取文字、不會誤選工具
    finger = Finger(context, page)
    scrolled = page.evaluate("() => scrollY")
    key_box = tools.bounding_box()
    finger.send("touchStart", [(key_box["x"] + key_box["width"] / 2, key_box["y"] + key_box["height"] / 2)])
    page.wait_for_timeout(700)
    finger.send("touchEnd", [])
    page.wait_for_timeout(200)
    held = page.evaluate("() => ({ open: !document.getElementById('drawTools').hidden, selected: String(getSelection()), pressed: document.querySelectorAll('#drawTools [aria-pressed=true]').length })")
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-01-menu.png"))
    journey.check("手機：長按「分析工具」再放開，選單打開、沒有選取到文字、沒有誤選工具", held["open"] and held["selected"] == "" and held["pressed"] == 0, held)
    layout = journey.menu(page)
    journey.check("手機：選單十項每項至少 44px 高、整個在畫面寬度內、不造成橫向捲動", menu_fits(layout, TOOL_COUNT), layout)
    # 本人 11-24（協調者轉達）：390×844 上整張選單（第一項到最後一項）不用捲頁面、也不用在選單裡捲就看得到
    whole = page.evaluate("""() => { const items = [...document.querySelectorAll('#drawTools button[data-tool]')], menu = document.getElementById('drawTools');
      const first = items[0].getBoundingClientRect(), last = items.at(-1).getBoundingClientRect(), box = menu.getBoundingClientRect();
      return { first: [first.top, first.bottom], last: [last.top, last.bottom], menu: [box.top, box.bottom], inner: menu.scrollHeight - menu.clientHeight,
        height: innerHeight, scrollY, side: menu.dataset.side }; }""")
    journey.check("手機 390×844：整張選單（第一項到最後一項）都在畫面內，不用捲頁面也不用捲選單", whole["first"][0] >= 0 and whole["last"][1] <= whole["height"]
                  and whole["menu"][0] >= 0 and whole["menu"][1] <= whole["height"] and whole["inner"] <= 1 and whole["scrollY"] == scrolled, whole)

    canvas_y = journey.draw(page, 0.45, 0.62, touch=True)
    lines = journey.lines(page)
    journey.check("手機：點工具再點圖，該價位出現水平線", len(lines) == before + 1 and any(abs(line - canvas_y) <= 1.5 for line in lines),
                  {"lines": lines, "tapped": canvas_y})
    journey.check("手機：工具自動回到一般模式", tool.get_attribute("aria-pressed") == "false")
    page.screenshot(path=str(OUT / "mobile-02-drawn.png"))
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-03-chart.png"))

    journey.choose_period(page, "60m")
    journey.check("手機：60 分 K 分析工具在、日 K 畫的線不出現", tools.is_visible() and journey.lines(page) == [])
    journey.choose_period(page, "daily")
    page.reload()
    journey.ready(page, "2330")
    journey.check("手機：重新整理後兩條線都還在", len(journey.lines(page)) == before + 1 and len(journey.saved(page)) == before + 1, journey.lines(page))
    context.close()


def near(lines: list[float], y: float, tolerance: float = 1.5) -> bool:
    return any(abs(line - y) <= tolerance for line in lines)


def drawing_request(method: str):
    return lambda response: "/api/drawings/" in response.url and response.request.method == method


def edit_desktop(journey: Journey, browser) -> None:
    print("票 02｜桌機寬度 1280：選取、拖動、刪除")
    viewport = {"width": 1280, "height": 900}
    context = journey.context(browser, OWNER, viewport=viewport)
    page = journey.open(context, "2317")
    tools = page.locator("#toolsKey")
    tool = journey.tool(page, "hline")
    delete = page.locator("#drawDelete")

    lines = journey.lines(page)
    before = journey.saved(page, "2317")
    journey.check("起點：這一檔有一條存好的線", len(lines) == 1 and len(before) == 1, lines)
    line_y, line_id, price = lines[0], before[0]["id"], before[0]["anchors"][0]["price"]
    journey.check("沒選取時：沒有刪除鈕、線上沒有把手", not delete.is_visible() and not journey.handle(page, line_y))

    x, y = journey.canvas_xy(page, 0.4, line_y + 5)
    page.mouse.click(x, y)
    journey.check("點在線旁 5px 內就選取：「分析工具」那一行出現刪除、線上出現把手", delete.is_visible() and journey.handle(page, line_y))
    journey.check("刪除鈕只有圖示、有可讀名稱", delete.inner_text().strip() == "" and delete.get_attribute("aria-label") == "刪除選取的線")
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-05-selected.png"))

    x, y = journey.canvas_xy(page, 0.4, line_y + 80)
    page.mouse.click(x, y)
    journey.check("點空白處取消選取：刪除收起、把手消失、線還在", not delete.is_visible() and not journey.handle(page, line_y)
                  and journey.lines(page) == lines)
    page.mouse.click(x, y - 60)  # 離線 20px：點不到
    journey.check("點在可點選範圍外不會選到線", not delete.is_visible())

    x, y = journey.canvas_xy(page, 0.4, line_y)
    page.mouse.click(x, y)
    axis = journey.axis(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 30, steps=4)
    page.mouse.move(x, y + 60, steps=4)
    moving = journey.lines(page)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    journey.check("拖的時候線就跟著走", near(moving, line_y + 60), {"lines": moving, "want": line_y + 60})
    journey.check("放開後自動儲存（PUT 回 200，是覆寫不是新增）", moved.value.status == 200, moved.value.status)
    after = journey.saved(page, "2317")
    journey.check("線移到新價位：同一條、價位變低、仍只有一條", len(after) == 1 and after[0]["id"] == line_id
                  and after[0]["anchors"][0]["price"] < price and near(journey.lines(page), line_y + 60), after)
    journey.check("拖線時圖表沒有跟著平移", journey.axis(page) == axis)
    journey.check("放開後仍選取著", delete.is_visible() and journey.handle(page, line_y + 60))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-06-moved.png"))
    price = after[0]["anchors"][0]["price"] if after else price

    page.reload()
    journey.ready(page, "2317")
    lines = journey.lines(page)
    journey.check("重新整理後線在拖過去的位置，沒有選取", near(lines, line_y + 60) and len(lines) == 1 and not delete.is_visible()
                  and journey.saved(page, "2317")[0]["anchors"][0]["price"] == price, lines)
    line_y = lines[0]

    x, y = journey.canvas_xy(page, 0.4, line_y)
    page.mouse.click(x, y)
    axis = journey.axis(page)
    bx, by = journey.canvas_xy(page, 0.5, line_y + 90)
    page.mouse.move(bx, by)
    page.mouse.down()
    page.mouse.move(bx + 160, by, steps=6)
    page.mouse.up()
    page.wait_for_timeout(150)
    # 平移後價格座標會重新取範圍：線還在畫面內就維持選取，移出畫面則解除（看不到的線不留著刪除鈕）。
    journey.check("選取後從空白處拖曳：照舊平移圖表，線的價位不變；線還看得到就維持選取", journey.axis(page) != axis
                  and delete.is_visible() == (len(journey.lines(page)) == 1)
                  and journey.saved(page, "2317")[0]["anchors"][0]["price"] == price, journey.lines(page))
    page.reload()
    journey.ready(page, "2317")
    line_y = journey.lines(page)[0]
    x, y = journey.canvas_xy(page, 0.4, line_y)
    axis = journey.axis(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 160, y + 40, steps=6)
    page.mouse.up()
    page.wait_for_timeout(150)
    journey.check("沒選取的線拖不動：從線上拖也是平移", journey.axis(page) != axis
                  and journey.saved(page, "2317")[0]["anchors"][0]["price"] == price)
    page.reload()
    journey.ready(page, "2317")
    line_y = journey.lines(page)[0]

    x, y = journey.canvas_xy(page, 0.4, line_y)
    page.mouse.click(x, y)
    page.keyboard.press("Escape")
    journey.check("按 Esc 取消選取，線還在", not delete.is_visible() and len(journey.lines(page)) == 1)
    tool.click()
    page.keyboard.press("Escape")
    journey.check("選了工具還沒點下去時按 Esc：放下工具", tool.get_attribute("aria-pressed") == "false")
    page.mouse.click(x, y - 100)
    journey.check("放下工具後點圖不會畫出線", len(journey.lines(page)) == 1 and len(journey.saved(page, "2317")) == 1)

    second_y = journey.draw(page, 0.5, 0.25)
    x2, y2 = journey.canvas_xy(page, 0.5, second_y)
    page.mouse.click(x2, y2)
    journey.check("新畫的線也選得到", delete.is_visible() and journey.handle(page, second_y))
    with page.expect_response(drawing_request("DELETE")) as removed:
        page.keyboard.press("Delete")
    journey.check("按 Delete 刪除選取的線（DELETE 回 204）", removed.value.status == 204, removed.value.status)
    saved = journey.saved(page, "2317")
    journey.check("刪掉的是選取的那一條，另一條還在", len(saved) == 1 and saved[0]["id"] == line_id
                  and len(journey.lines(page)) == 1 and not delete.is_visible(), saved)

    page.mouse.click(x, y)
    # 本人 11-24 樣品 A：沒有名稱浮標了；刪除鈕就排在「分析工具」那一行（同一列、在圖的上方）
    key_hit, delete_hit = journey.hit(page, "#toolsKey"), journey.hit(page, "#drawDelete")
    journey.check("刪除鈕和「分析工具」在同一行、在圖的上方", bool(key_hit and delete_hit) and abs(key_hit["middle"] - delete_hit["middle"]) < 2
                  and delete_hit["bottom"] <= page.locator("#chartHost").bounding_box()["y"], {"key": key_hit, "delete": delete_hit})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-07-delete-button.png"))
    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.click()
    journey.check("按刪除鈕：線消失、雲端也刪了、刪除鈕收起", removed.value.status == 204 and journey.lines(page) == []
                  and journey.saved(page, "2317") == [] and not delete.is_visible())
    journey.check("刪除後焦點回到圖表", page.evaluate("() => document.activeElement === document.querySelector('#chartHost canvas')"))
    page.reload()
    journey.ready(page, "2317")
    journey.check("重新整理後刪掉的線不再出現", journey.lines(page) == [] and journey.saved(page, "2317") == [])

    third_y = journey.draw(page, 0.5, 0.45)
    x3, y3 = journey.canvas_xy(page, 0.5, third_y)
    page.mouse.click(x3, y3)
    page.mouse.move(x3, y3)
    page.mouse.down()
    page.mouse.move(x3, y3 - 40, steps=4)
    with page.expect_response(drawing_request("DELETE")) as removed:
        page.mouse.up()
        page.keyboard.press("Delete")
    page.wait_for_timeout(200)
    journey.check("拖完馬上刪：線不會又冒出來", removed.value.status == 204 and journey.saved(page, "2317") == [] and journey.lines(page) == [])

    journey.draw(page, 0.5, 0.45)
    kept = journey.saved(page, "2317")
    x3, y3 = journey.canvas_xy(page, 0.5, journey.lines(page)[0])
    page.mouse.click(x3, y3)
    journey.choose_period(page, "weekly")
    journey.check("切到週 K：選取還在", delete.is_visible() and len(journey.lines(page)) == 1 and journey.handle(page, journey.lines(page)[0]))
    journey.choose_period(page, "60m")
    journey.check("切到 60 分 K：日 K 選取的那條不在這一套，刪除收起（分析工具在）", tools.is_visible() and not delete.is_visible())
    page.keyboard.press("Delete")
    journey.choose_period(page, "daily")
    journey.check("切回日 K：線還在、沒有被60 分 K 上的 Delete 刪掉、不會自己恢復選取", len(journey.lines(page)) == 1
                  and journey.saved(page, "2317") == kept and not delete.is_visible())

    kept_id = kept[0]["id"] if kept else "missing-line"
    other = journey.context(browser, OTHER, viewport=viewport)
    foreign = other.request.delete(f"{journey.base}/api/drawings/2317/{kept_id}", headers={"origin": journey.base})
    journey.check("別的帳號對同一個識別碼下刪除：刪不到我的線", foreign.status == 204 and journey.saved(page, "2317") == kept, foreign.status)
    other.close()
    cross = context.request.delete(f"{journey.base}/api/drawings/2317/{kept_id}", headers={"origin": "https://evil.example"})
    anonymous = journey.context(browser, None, viewport=viewport)
    unsigned = anonymous.request.delete(f"{journey.base}/api/drawings/2317/{kept_id}", headers={"origin": journey.base})
    anonymous.close()
    journey.check("跨站與未登入的刪除被擋，線還在", cross.status == 403 and unsigned.status == 401 and journey.saved(page, "2317") == kept,
                  [cross.status, unsigned.status])
    context.close()


def edit_mobile(journey: Journey, browser) -> None:
    print("票 02｜手機寬度 390（觸控）：選取、拖動、刪除")
    context = journey.context(browser, OWNER, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context)
    delete = page.locator("#drawDelete")
    lines = journey.lines(page)
    saved = journey.saved(page)
    journey.check("手機起點：這一檔有兩條線", len(lines) == 2 and len(saved) == 2, lines)
    # 挑下面那一條：往下還有空間可以拖。
    line_y = max(lines)
    x, y = journey.canvas_xy(page, 0.6, line_y + 11)
    page.touchscreen.tap(x, y)
    journey.check("手機：手指點在線旁 11px 就選得到（比滑鼠寬），出現刪除鈕", delete.is_visible() and journey.handle(page, line_y))
    box = journey.hit(page, "#drawDelete")
    journey.check("手機：刪除鈕的觸控範圍夠大（至少 44px）、在畫面內、不造成橫向捲動", bool(box) and box["width"] >= 44 and box["height"] >= 44
                  and box["right"] <= 390 and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), box)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-04-selected.png"))

    cdp = context.new_cdp_session(page)

    def touch(kind: str, tx: float, ty: float) -> None:
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(tx), "y": round(ty)}]})

    x, y = journey.canvas_xy(page, 0.6, line_y)
    axis = journey.axis(page)
    scroll = page.evaluate("() => scrollY")
    touch("touchStart", x, y)
    for step in range(1, 7):
        touch("touchMove", x, y + step * 5)
        page.wait_for_timeout(16)
    # 停一下再放開。帶著速度放開會被 Chromium 當成甩動，之後的第一次點擊會被吞掉（第二次才有 click）；
    # 這和畫線無關——不選線、單純甩動平移圖表後點工具鈕也一樣（2026-11-14 以同一套模擬觸控重現）。真機上是否如此留給票 08。
    page.wait_for_timeout(250)
    with page.expect_response(drawing_request("PUT")) as moved:
        touch("touchEnd", x, y)
    after = journey.lines(page)
    journey.check("手機：手指拖動選取的線，放開後自動儲存", moved.value.status == 200 and near(after, line_y + 30, 2) and len(after) == 2,
                  {"lines": after, "want": line_y + 30})
    journey.check("手機：拖線時圖表不平移、頁面不捲動", journey.axis(page) == axis and page.evaluate("() => scrollY") == scroll)
    now = journey.saved(page)
    journey.check("手機：雲端仍是兩條，其中一條換了價位", len(now) == 2
                  and sorted(item["id"] for item in now) == sorted(item["id"] for item in saved) and now != saved)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-05-moved.png"))

    # 本人 11-24 樣品 A：刪除鈕沒有長按顯示的名稱了（只有圖示＋可讀名稱）；改驗它和「分析工具」同一行、長按不會選到文字
    key_hit, delete_hit = journey.hit(page, "#toolsKey"), journey.hit(page, "#drawDelete")
    journey.check("手機：刪除鈕和「分析工具」在同一行、只有圖示有可讀名稱、不能選取文字", abs(key_hit["middle"] - delete_hit["middle"]) < 2
                  and delete.inner_text().strip() == "" and delete.get_attribute("aria-label") == "刪除選取的線" and delete_hit["select"] == "none",
                  {"key": key_hit, "delete": delete_hit})

    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.tap()
    journey.check("手機：點刪除，線消失、刪除鈕收起", removed.value.status == 204 and len(journey.lines(page)) == 1 and not delete.is_visible())
    x, y = journey.canvas_xy(page, 0.6, max(journey.lines(page)) + 60)
    page.touchscreen.tap(x, y)
    journey.check("手機：點空白處照舊顯示 K 棒數值，不出現刪除", journey.shows_bar(page) and not delete.is_visible())
    page.touchscreen.tap(x, y)
    journey.check("手機：再點一次同一根 K 棒就取消選取（本人 11-15），右側不再標數值", journey.bar(page)["selection"] == -1
                  and journey.bar(page)["picked"] is None and not delete.is_visible(), journey.bar(page))
    page.reload()
    journey.ready(page, "2330")
    journey.check("手機：重新整理後只剩一條", len(journey.lines(page)) == 1 and len(journey.saved(page)) == 1, journey.lines(page))
    context.close()


def spans(ink: dict, a: tuple[float, float], b: tuple[float, float], slack_x: float = 10, slack_y: float = 7) -> bool:
    """畫線色像素的外框是不是剛好框住 a、b 兩點（端點對在 K 棒正中央，所以橫向容許半根多一點；外框含把手的半徑）。"""
    if not ink.get("count"):
        return False
    return (abs(ink["minX"] - min(a[0], b[0])) <= slack_x and abs(ink["maxX"] - max(a[0], b[0])) <= slack_x
            and abs(ink["minY"] - min(a[1], b[1])) <= slack_y and abs(ink["maxY"] - max(a[1], b[1])) <= slack_y)


def two_point_desktop(journey: Journey, browser) -> None:
    print("票 03｜桌機寬度 1280：趨勢線與射線")
    code = "2303"
    context = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    page = journey.open(context, code)
    tools = page.locator("#toolsKey")
    trend, ray = journey.tool(page, "trend"), journey.tool(page, "ray")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    a, b = (round(width * 0.35), round(height * 0.62)), (round(width * 0.6), round(height * 0.3))  # 畫布座標
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    journey.open_menu(page)
    order = journey.menu(page)["order"]
    journey.close_menu(page)
    # 本人 11-24 樣品 A：「線條」一組是趨勢線、射線、水平線、平行通道，名稱直接寫在項目上
    journey.check("選單「線條」一組依序是趨勢線、射線、水平線、平行通道，每項寫著名稱、各有可讀名稱", order[:5] == menu_order()[:5], order)
    trend.click()
    ray.click()
    journey.check("選著趨勢線時選射線：直接換成射線，鍵上寫「射線」", ray.get_attribute("aria-pressed") == "true" and trend.get_attribute("aria-pressed") == "false"
                  and tools.inner_text().strip() == "射線")
    ray.click()
    page.mouse.move(5, 5)
    journey.check("起點：這一檔沒有畫線，圖上沒有畫線色", journey.saved(page, code) == [] and journey.ink(page)["count"] == 0, journey.ink(page))

    trend.click()
    journey.check("選了趨勢線：工具列看得出已選中", trend.get_attribute("aria-pressed") == "true")
    page.mouse.click(*journey.at(page, *a))
    first = journey.ink(page)
    journey.check("點第一下：起點出現記號，還沒有線、也還沒儲存，工具留著", 0 < first["count"] and first["maxX"] - first["minX"] <= 12
                  and journey.saved(page, code) == [] and trend.get_attribute("aria-pressed") == "true", first)
    page.mouse.move(*journey.at(page, *b), steps=8)
    preview = journey.ink(page)
    journey.check("移動游標：預覽線從起點跟到游標", spans(preview, a, b) and journey.saved(page, code) == [], {"ink": preview, "a": a, "b": b})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-08-trend-preview.png"))
    page.keyboard.press("Escape")
    journey.check("按 Esc：預覽線與起點記號都消失、工具放下、什麼都沒存", journey.ink(page)["count"] == 0
                  and trend.get_attribute("aria-pressed") == "false" and journey.saved(page, code) == [])

    trend.click()
    page.mouse.click(*journey.at(page, *a))
    trend.click()
    page.mouse.move(*journey.at(page, *b), steps=4)
    journey.check("點了第一下後再點一次同一個工具：取消，不留下半條線", journey.ink(page)["count"] == 0 and trend.get_attribute("aria-pressed") == "false")
    page.mouse.click(*journey.at(page, *b))
    journey.check("取消後點圖照舊顯示 K 棒數值，沒有畫出線", journey.shows_bar(page) and journey.saved(page, code) == [])
    page.keyboard.press("Escape")

    trend.click()
    page.mouse.click(*journey.at(page, *a))
    page.mouse.click(*journey.at(page, *a))
    journey.check("同一個位置連點兩下：不成線，工具與起點留著", journey.saved(page, code) == [] and trend.get_attribute("aria-pressed") == "true")
    with page.expect_response(drawing_request("PUT")) as created:
        page.mouse.click(*journey.at(page, *b))
    page.mouse.move(5, 5)
    items = journey.saved(page, code)
    drawn = journey.ink(page)
    journey.check("點第二下：線畫出來並自動儲存（PUT 回 201），工具回一般模式", created.value.status == 201
                  and trend.get_attribute("aria-pressed") == "false" and spans(drawn, a, b), {"ink": drawn, "a": a, "b": b})
    journey.check("雲端存的是一條趨勢線、兩個錨點：起點日期較早、價位較低", len(items) == 1 and items[0]["kind"] == "trend"
                  and len(items[0]["anchors"]) == 2 and items[0]["anchors"][0]["date"] < items[0]["anchors"][1]["date"]
                  and items[0]["anchors"][0]["price"] < items[0]["anchors"][1]["price"], items)
    journey.check("新畫的線沒有被選取", not delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-09-trend-drawn.png"))
    line_id, before = items[0]["id"], items[0]["anchors"]

    page.mouse.click(*journey.at(page, a[0] + 40, a[1]))
    journey.check("點在線的延長範圍外（起點右邊、離線很遠）：不選取", not delete.is_visible())
    page.mouse.click(*journey.at(page, mid[0], mid[1] + 4))
    journey.check("點在線身旁 4px：選取，出現刪除鈕", delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-10-trend-selected.png"))

    axis = journey.axis(page)
    page.mouse.move(*journey.at(page, *mid))
    page.mouse.down()
    page.mouse.move(*journey.at(page, mid[0] - 60, mid[1] + 40), steps=6)
    moving = journey.ink(page)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]
    journey.check("拖線身：整條跟著走，圖表不平移", spans(moving, (a[0] - 60, a[1] + 40), (b[0] - 60, b[1] + 40)) and journey.axis(page) == axis,
                  {"ink": moving, "a": a, "b": b})
    # 平移是以「根」為單位，放開後的端點位置從畫面量：選取中，外框比端點多出把手的半徑（約 5px）。
    placed = journey.ink(page)
    a2, b2 = (placed["minX"] + 5, placed["maxY"] - 5), (placed["maxX"] - 5, placed["minY"] + 5)
    drop = [round(before[i]["price"] - after["anchors"][i]["price"], 2) for i in (0, 1)]
    journey.check("放開後儲存（PUT 回 200，同一條）：兩端的價位降了同樣的幅度、日期都往前", moved.value.status == 200 and after["id"] == line_id
                  and drop[0] > 0 and abs(drop[0] - drop[1]) <= 0.02
                  and all(after["anchors"][i]["date"] < before[i]["date"] for i in (0, 1)), {"before": before, "after": after["anchors"]})
    before = after["anchors"]

    page.mouse.move(*journey.at(page, *b2))
    page.mouse.down()
    b3 = (b2[0] + 80, b2[1] - 50)
    page.mouse.move(*journey.at(page, *b3), steps=6)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]
    reshaped = journey.ink(page)
    journey.check("拖終點把手：只有那一端移動（往右、往上），起點不動；放開後儲存", moved.value.status == 200
                  and after["anchors"][0] == before[0] and after["anchors"][1]["price"] > before[1]["price"]
                  and after["anchors"][1]["date"] > before[1]["date"] and spans(reshaped, a2, b3) and delete.is_visible(),
                  {"before": before, "after": after["anchors"], "ink": reshaped})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-11-trend-reshaped.png"))
    kept = after["anchors"]

    daily = journey.ink(page)
    for period, name in [("weekly", "週 K"), ("monthly", "月 K")]:
        journey.choose_period(page, period)
        ink = journey.ink(page)
        journey.check(f"{name}：線還在、選取還在，雲端的錨點沒有被改", ink["count"] > 0 and delete.is_visible()
                      and journey.saved(page, code)[0]["anchors"] == kept, ink)
        page.locator(".bd-chart").screenshot(path=str(OUT / f"desktop-12-trend-{period}.png"))
    journey.choose_period(page, "60m")
    journey.check("60 分 K：分析工具在、日 K 畫的線不出現", tools.is_visible() and journey.ink(page)["count"] == 0)
    journey.choose_period(page, "daily")
    back = journey.ink(page)
    journey.check("切回日 K：線回到原本的位置", spans(back, a2, b3), {"ink": back, "was": daily})

    page.reload()
    journey.ready(page, code)
    reloaded = journey.ink(page)
    journey.check("重新整理後線還在原位，雲端的錨點相同", spans(reloaded, a2, b3) and journey.saved(page, code)[0]["anchors"] == kept
                  and not delete.is_visible(), {"ink": reloaded, "a": a2, "b": b3})

    # 射線：畫在另一個位置，穿過第二點一路延伸到繪圖區右緣，不畫進價格軸。
    ra, rb = (round(width * 0.2), round(height * 0.25)), (round(width * 0.4), round(height * 0.3))
    alone = journey.ink(page)
    ray.click()
    page.mouse.click(*journey.at(page, *ra))
    page.mouse.move(*journey.at(page, *rb), steps=6)
    with page.expect_response(drawing_request("PUT")) as created:
        page.mouse.click(*journey.at(page, *rb))
    page.mouse.move(5, 5)
    both = journey.ink(page)
    items = journey.saved(page, code)
    rays = [item for item in items if item["kind"] == "ray"]
    # 券商版面（本人 11-17「左右不留價格欄」）：右側沒有價格軸了，繪圖區一直到畫布右緣。
    axis_width = 0
    journey.check("射線：點兩下成線並儲存，延伸超過第二點、到右緣為止，不超出圖", created.value.status == 201 and len(rays) == 1 and len(items) == 2
                  and both["maxX"] > rb[0] + 200 and both["maxX"] <= width - axis_width and both["count"] > alone["count"],
                  {"ink": both, "second": rb, "canvas": width})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-13-ray-drawn.png"))

    far = (rb[0] + 200, rb[1] + 200 * (rb[1] - ra[1]) / (rb[0] - ra[0]))  # 延伸出去的那一段上的一點
    page.mouse.click(*journey.at(page, *far))
    journey.check("射線延伸出去的那一段也點得到", delete.is_visible())
    with page.expect_response(drawing_request("DELETE")) as removed:
        page.keyboard.press("Delete")
    left = journey.saved(page, code)
    journey.check("按 Delete 刪掉射線：趨勢線還在", removed.value.status == 204 and [item["kind"] for item in left] == ["trend"]
                  and spans(journey.ink(page), a2, b3))

    other = journey.context(browser, OTHER, viewport={"width": 1280, "height": 900})
    other_page = journey.open(other, code)
    journey.check("別的帳號看不到我的趨勢線", journey.ink(other_page)["count"] == 0 and journey.saved(other_page, code) == [])
    other.close()
    context.close()


def two_point_mobile(journey: Journey, browser) -> None:
    print("票 03｜手機寬度 390（觸控）：趨勢線與射線")
    code = "2303"
    context = journey.context(browser, OWNER, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context, code)
    ray = journey.tool(page, "ray")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    start = journey.ink(page)
    journey.check("手機：桌機畫的趨勢線看得到", start["count"] > 0 and len(journey.saved(page, code)) == 1, start)
    journey.open_menu(page, touch=True)
    layout = journey.menu(page)
    journey.close_menu(page, touch=True)
    journey.check("手機：選單裡每項都夠大（至少 44px 高）、選單在畫面寬度內、不造成橫向捲動", menu_fits(layout, TOOL_COUNT), layout)

    ra, rb = (round(width * 0.15), round(height * 0.78)), (round(width * 0.4), round(height * 0.7))
    ray.tap()
    page.touchscreen.tap(*journey.at(page, *ra))
    marked = journey.ink(page)
    journey.check("手機：點第一下後起點有記號（沒有游標也看得出點到了），還沒儲存", marked["count"] > start["count"]
                  and len(journey.saved(page, code)) == 1 and ray.get_attribute("aria-pressed") == "true", marked)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-06-ray-first-tap.png"))

    cdp = context.new_cdp_session(page)

    def touch(kind: str, tx: float, ty: float) -> None:
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(tx), "y": round(ty)}]})

    x, y = journey.at(page, *rb)
    touch("touchStart", x, y)
    page.wait_for_timeout(120)
    held = journey.ink(page)
    journey.check("手機：手指按下去還沒放開，預覽線就從起點延伸出來", held["count"] > marked["count"] and held["maxX"] > rb[0] + 60
                  and len(journey.saved(page, code)) == 1, held)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-07-ray-preview.png"))
    with page.expect_response(drawing_request("PUT")) as created:
        touch("touchEnd", x, y)
    drawn = journey.ink(page)
    items = journey.saved(page, code)
    journey.check("手機：放開就畫出射線並儲存，延伸到右緣、不超出圖，工具回一般模式", created.value.status == 201 and len(items) == 2
                  and drawn["maxX"] > rb[0] + 100 and drawn["maxX"] <= width and ray.get_attribute("aria-pressed") == "false", {"ink": drawn, "canvas": width})
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-08-ray-drawn.png"))
    ray_item = next(item for item in items if item["kind"] == "ray")

    slope = (rb[1] - ra[1]) / (rb[0] - ra[0])
    body = (rb[0] + 60, rb[1] + 60 * slope + 9)  # 延伸段旁 9px：手指的可點選範圍比滑鼠寬
    page.touchscreen.tap(*journey.at(page, *body))
    journey.check("手機：手指點在射線旁 9px 就選得到，出現刪除鈕", delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-09-ray-selected.png"))

    axis = journey.axis(page)
    scroll = page.evaluate("() => scrollY")
    x, y = journey.at(page, *rb)
    touch("touchStart", x, y)
    for step in range(1, 7):
        touch("touchMove", x, y - step * 6)
        page.wait_for_timeout(16)
    page.wait_for_timeout(250)  # 停一下再放開，不讓 Chromium 當成甩動（見票 02 的註記）
    with page.expect_response(drawing_request("PUT")) as moved:
        touch("touchEnd", x, y - 36)
    after = next(item for item in journey.saved(page, code) if item["id"] == ray_item["id"])
    journey.check("手機：手指拖第二個錨點往上，只有那一端變高、起點不動；圖表不平移、頁面不捲動", moved.value.status == 200
                  and after["anchors"][0] == ray_item["anchors"][0] and after["anchors"][1]["price"] > ray_item["anchors"][1]["price"]
                  and journey.axis(page) == axis and page.evaluate("() => scrollY") == scroll, {"before": ray_item["anchors"], "after": after["anchors"]})
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-10-ray-reshaped.png"))

    journey.choose_period(page, "weekly")
    journey.check("手機：切到週 K 兩條線都還在", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 2)
    journey.choose_period(page, "daily")
    page.reload()
    journey.ready(page, code)
    journey.check("手機：重新整理後兩條都還在", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 2)

    page.touchscreen.tap(*journey.at(page, rb[0], rb[1] - 36))
    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.tap()
    journey.check("手機：選取射線後點刪除，剩下趨勢線", removed.value.status == 204
                  and [item["kind"] for item in journey.saved(page, code)] == ["trend"] and not delete.is_visible())
    context.close()


def rect_desktop(journey: Journey, browser) -> None:
    print("票 04｜桌機寬度 1280：矩形")
    code = "2308"
    context = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    page = journey.open(context, code)
    rect = journey.tool(page, "rect")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    a, b = (round(width * 0.3), round(height * 0.3)), (round(width * 0.55), round(height * 0.6))  # 左上、右下
    inside = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    # 本人 11-24 樣品 A：沒有滑鼠停留的名稱浮標了，名稱直接寫在選單項目上
    heading = "b => { const prev = b.previousElementSibling; return prev && prev.matches('.dt-group') ? prev.textContent.trim() : ''; }"
    journey.check("選單裡「矩形」寫著名稱、排在「區間」一組第一項", rect.item.evaluate(heading) == "區間"
                  and rect.item.text_content().strip() == "矩形" and rect.get_attribute("aria-label") == "矩形")
    journey.check("起點：這一檔沒有畫線", journey.saved(page, code) == [] and journey.ink(page)["count"] == 0)

    rect.click()
    page.mouse.click(*journey.at(page, *b))  # 先點右下角
    page.mouse.move(*journey.at(page, *a), steps=8)
    preview = journey.ink(page)
    journey.check("點第一下後移動游標：預覽的框跟著走，還沒儲存", spans(preview, a, b) and journey.saved(page, code) == [], preview)
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-14-rect-preview.png"))
    with page.expect_response(drawing_request("PUT")) as created:
        page.mouse.click(*journey.at(page, *a))  # 再點左上角
    page.mouse.move(5, 5)
    items = journey.saved(page, code)
    drawn = journey.ink(page)
    journey.check("點第二下：矩形畫出來並儲存（PUT 回 201），工具回一般模式", created.value.status == 201 and spans(drawn, a, b)
                  and rect.get_attribute("aria-pressed") == "false", drawn)
    journey.check("先點右下再點左上，存的仍是「較早＋較高、較晚＋較低」", len(items) == 1 and items[0]["kind"] == "rect"
                  and items[0]["anchors"][0]["date"] < items[0]["anchors"][1]["date"]
                  and items[0]["anchors"][0]["price"] > items[0]["anchors"][1]["price"], items)
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-15-rect-drawn.png"))
    before = items[0]["anchors"]

    page.mouse.click(*journey.at(page, b[0] + 30, inside[1]))
    journey.check("點在框外 30px：不選取", not delete.is_visible())
    page.mouse.click(*journey.at(page, *inside))
    journey.check("點在框的內部：不選取，照舊顯示 K 棒數值", not delete.is_visible() and journey.shows_bar(page))
    page.mouse.click(*journey.at(page, inside[0], a[1] + 3))
    journey.check("點在上邊線旁 3px：選取，出現刪除鈕", delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-16-rect-selected.png"))

    axis = journey.axis(page)
    page.mouse.move(*journey.at(page, *inside))
    page.mouse.down()
    page.mouse.move(*journey.at(page, inside[0] + 60, inside[1] + 30), steps=6)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]["anchors"]
    drop = [round(before[i]["price"] - after[i]["price"], 2) for i in (0, 1)]
    journey.check("拖框的內部：整個平移（兩個角的價位降一樣多、日期都往後），圖表不平移", moved.value.status == 200 and drop[0] > 0
                  and abs(drop[0] - drop[1]) <= 0.02 and all(after[i]["date"] > before[i]["date"] for i in (0, 1))
                  and journey.axis(page) == axis, {"before": before, "after": after})
    before = after

    placed = journey.ink(page)  # 選取中：外框比角落多出把手的半徑（約 5px）
    corner = (placed["maxX"] - 5, placed["maxY"] - 5)  # 右下角
    page.mouse.move(*journey.at(page, *corner))
    page.mouse.down()
    page.mouse.move(*journey.at(page, corner[0] + 70, corner[1] + 40), steps=6)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]["anchors"]
    journey.check("拖右下角：只有右下角變（更晚、更低），左上角不動", moved.value.status == 200 and after[0] == before[0]
                  and after[1]["date"] > before[1]["date"] and after[1]["price"] < before[1]["price"], {"before": before, "after": after})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-17-rect-resized.png"))
    kept = after
    daily = journey.ink(page)

    for period, name in [("weekly", "週 K"), ("monthly", "月 K")]:
        journey.choose_period(page, period)
        journey.check(f"{name}：矩形還在，雲端的兩個角沒有被改", journey.ink(page)["count"] > 0 and journey.saved(page, code)[0]["anchors"] == kept)
        page.locator(".bd-chart").screenshot(path=str(OUT / f"desktop-18-rect-{period}.png"))
    journey.choose_period(page, "daily")
    back = journey.ink(page)
    journey.check("切回日 K：矩形回到原本的位置", all(abs(back[key] - daily[key]) <= 1.5 for key in ("minX", "maxX", "minY", "maxY")), {"back": back, "was": daily})

    page.reload()
    journey.ready(page, code)
    journey.check("重新整理後矩形還在，雲端的兩個角相同，沒有選取", journey.ink(page)["count"] > 0
                  and journey.saved(page, code)[0]["anchors"] == kept and not delete.is_visible())
    context.close()


def rect_mobile(journey: Journey, browser) -> None:
    print("票 04｜手機寬度 390（觸控）：矩形")
    code = "2308"
    context = journey.context(browser, OWNER, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context, code)
    rect = journey.tool(page, "rect")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    journey.check("手機：桌機畫的矩形看得到", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 1)
    journey.open_menu(page, touch=True)
    layout = journey.menu(page)
    journey.close_menu(page, touch=True)
    journey.check("手機：選單十項每項都夠大（至少 44px 高）、在畫面寬度內、不造成橫向捲動", menu_fits(layout, TOOL_COUNT), layout)

    a, b = (round(width * 0.12), round(height * 0.7)), (round(width * 0.4), round(height * 0.88))
    existing = {item["id"] for item in journey.saved(page, code)}
    rect.tap()
    page.touchscreen.tap(*journey.at(page, *a))
    with page.expect_response(drawing_request("PUT")) as created:
        page.touchscreen.tap(*journey.at(page, *b))
    items = journey.saved(page, code)
    journey.check("手機：點工具再點兩下，畫出第二個矩形並儲存，工具回一般模式", created.value.status == 201 and len(items) == 2
                  and rect.get_attribute("aria-pressed") == "false", items)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-11-rect-drawn.png"))
    mine = next(item for item in items if item["id"] not in existing)

    # 點框內偏下的地方：券商版面的價格範圍只看 K 棒，桌機畫的大框下邊線剛好穿過這個小框的正中間附近，
    # 點正中間會（正確地）選到那條邊線。
    page.touchscreen.tap(*journey.at(page, (a[0] + b[0]) / 2, a[1] + (b[1] - a[1]) * 0.7))
    journey.check("手機：手指點框的內部不選取", not delete.is_visible())
    # 點下邊線：立體版手機的圖比較高，這個框的上邊線離桌機畫的那個大框的下邊線不到 10px，點上邊線旁 9px
    # 會（正確地）選到比較近的那個大框；下邊線附近沒有別的線。
    page.touchscreen.tap(*journey.at(page, (a[0] + b[0]) / 2, b[1] - 9))
    journey.check("手機：手指點在下邊線旁 9px 就選取，出現刪除鈕", delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-12-rect-selected.png"))

    cdp = context.new_cdp_session(page)

    def touch(kind: str, tx: float, ty: float) -> None:
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(tx), "y": round(ty)}]})

    axis = journey.axis(page)
    scroll = page.evaluate("() => scrollY")
    x, y = journey.at(page, *b)
    touch("touchStart", x, y)
    for step in range(1, 7):
        touch("touchMove", x + step * 5, y - step * 4)
        page.wait_for_timeout(16)
    page.wait_for_timeout(250)
    with page.expect_response(drawing_request("PUT")) as moved:
        touch("touchEnd", x + 30, y - 24)
    after = next(item for item in journey.saved(page, code) if item["id"] == mine["id"])
    journey.check("手機：手指拖右下角，只有右下角變（更晚、更高），左上角不動；圖表不平移、頁面不捲動", moved.value.status == 200
                  and after["anchors"][0] == mine["anchors"][0] and after["anchors"][1]["price"] > mine["anchors"][1]["price"]
                  and after["anchors"][1]["date"] >= mine["anchors"][1]["date"]
                  and journey.axis(page) == axis and page.evaluate("() => scrollY") == scroll, {"before": mine["anchors"], "after": after["anchors"]})
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-13-rect-resized.png"))

    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.tap()
    journey.check("手機：點刪除，選取的矩形消失，另一個還在", removed.value.status == 204 and len(journey.saved(page, code)) == 1 and not delete.is_visible())
    page.reload()
    journey.ready(page, code)
    journey.check("手機：重新整理後只剩一個矩形", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 1)
    context.close()

FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
TOOL_COUNT = 10  # 工具列目前有幾顆畫線工具（不含到價提醒的開關與刪除）；本人 11-24 加四種，放不下時整排左右捲


def fib_levels(page) -> list[float]:
    """黃金分割的橫線位置：只看畫布右半邊（標示文字在線的左端，不會到這裡），而且線會被前面的 K 棒遮掉一部分，
    所以用「右半邊有畫布寬度一成以上是畫線色」的列來認（把手、兩點的連線都不到一成）。"""
    return page.evaluate(LINES_JS, {"from": 0.5, "share": 0.1})


def in_ratio(levels: list[float], tolerance: float = 2) -> bool:
    """七條線由上到下（或由下到上）是不是照 0、0.236、0.382、0.5、0.618、0.786、1 的比例排。"""
    if len(levels) != 7:
        return False
    top, span = levels[0], levels[-1] - levels[0]
    ratios = [(y - top) / span for y in levels]
    return any(all(abs(ratio - want) * span <= tolerance for ratio, want in zip(ratios, wanted))
               for wanted in (FIB_RATIOS, [1 - ratio for ratio in reversed(FIB_RATIOS)]))


def marker(page, journey: Journey) -> tuple[float, float]:
    """點了第一下之後起點記號（小圓）的圓心：就是那個錨點實際落的位置（K 棒正中央、取到分的價位）。"""
    ink = journey.ink(page)
    return (ink["minX"] + ink["maxX"]) / 2, (ink["minY"] + ink["maxY"]) / 2


def fib_desktop(journey: Journey, browser) -> None:
    print("票 05｜桌機寬度 1280：黃金分割")
    code = "2454"
    context = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    page = journey.open(context, code)
    tools = page.locator("#toolsKey")
    fib = journey.tool(page, "fib")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    axis_width = 0  # 券商版面（本人 11-17）：右側沒有價格軸，繪圖區到畫布右緣
    low, high = (round(width * 0.3), round(height * 0.66)), (round(width * 0.5), round(height * 0.3))  # 由低往高拉

    # 本人 11-24 樣品 A：沒有滑鼠停留的名稱浮標了，選了之後「分析工具」鍵上就寫著正在用的工具
    fib.click()
    journey.check("選了黃金分割：「分析工具」鍵上改寫「黃金分割」", tools.inner_text().strip() == "黃金分割"
                  and tools.get_attribute("aria-label") == "分析工具：正在畫黃金分割", tools.inner_text())
    fib.click()
    page.mouse.move(5, 5)
    journey.check("起點：這一檔沒有畫線", journey.saved(page, code) == [] and journey.ink(page)["count"] == 0)

    # 先各點一下再取消，量出兩個點實際落的位置（對在 K 棒正中央）；順便驗 Esc 取消不留東西。
    fib.click()
    page.mouse.click(*journey.at(page, *high))
    b = marker(page, journey)
    page.keyboard.press("Escape")
    journey.check("點了第一下後按 Esc：記號消失、工具放下、什麼都沒存", journey.ink(page)["count"] == 0
                  and fib.get_attribute("aria-pressed") == "false" and journey.saved(page, code) == [])

    fib.click()
    journey.check("選了黃金分割：工具列看得出已選中", fib.get_attribute("aria-pressed") == "true")
    page.mouse.click(*journey.at(page, *low))
    a = marker(page, journey)
    page.mouse.move(*journey.at(page, *high), steps=8)
    preview = fib_levels(page)
    ink = journey.ink(page)
    journey.check("點第一下後移動游標：七條線的預覽跟著走，從起點延伸到右緣，還沒儲存", in_ratio(preview) and abs(preview[0] - b[1]) <= 2
                  and abs(preview[-1] - a[1]) <= 2 and ink["maxX"] > width - axis_width - 60 and journey.saved(page, code) == [],
                  {"levels": preview, "a": a, "b": b, "ink": ink})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-19-fib-preview.png"))
    with page.expect_response(drawing_request("PUT")) as created:
        page.mouse.click(*journey.at(page, *high))
    page.mouse.move(5, 5)
    items = journey.saved(page, code)
    levels = fib_levels(page)
    ink = journey.ink(page)
    journey.check("點第二下：畫出七條線並儲存（PUT 回 201），工具回一般模式", created.value.status == 201 and len(levels) == 7
                  and fib.get_attribute("aria-pressed") == "false", levels)
    journey.check("七條線照 0、0.236、0.382、0.5、0.618、0.786、1 的比例排：0 在第二點（上）、1 在第一點（下）", in_ratio(levels)
                  and abs(levels[0] - b[1]) <= 2 and abs(levels[-1] - a[1]) <= 2, {"levels": levels, "a": a, "b": b})
    journey.check("雲端存的是一組黃金分割、兩個錨點照點的順序（第一點較早、較低）", len(items) == 1 and items[0]["kind"] == "fib"
                  and items[0]["anchors"][0]["date"] < items[0]["anchors"][1]["date"]
                  and items[0]["anchors"][0]["price"] < items[0]["anchors"][1]["price"], items)
    journey.check("橫線延伸到繪圖區右緣為止、不超出圖；標示在線的左端外側", width - axis_width - 60 < ink["maxX"] <= width - axis_width
                  and ink["minX"] < a[0] - 30, {"ink": ink, "start": a, "canvas": width})
    journey.check("新畫的沒有被選取", not delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-20-fib-drawn.png"))
    before = items[0]["anchors"]
    line_id = items[0]["id"]

    right = b[0] + 120  # 兩點右邊、只有橫線經過的地方
    page.mouse.click(*journey.at(page, right, (levels[5] + levels[6]) / 2))
    journey.check("點在兩條橫線中間：不選取，照舊顯示 K 棒數值", not delete.is_visible() and journey.shows_bar(page))
    page.mouse.click(*journey.at(page, right, levels[3] + 3))
    journey.check("點在 0.5 那條旁 3px：選取，出現刪除鈕", delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-21-fib-selected.png"))

    page.mouse.move(*journey.at(page, *b))
    axis = journey.axis(page)  # 游標停好再記：游標所在那一根的日期也畫在時間軸上
    page.mouse.down()
    page.mouse.move(*journey.at(page, b[0] + 70, b[1] - 40), steps=6)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]
    reshaped = fib_levels(page)
    journey.check("拖第二點的把手：只有那一端移動（更晚、更高），第一點不動；七條線照新的範圍重排；圖表不平移", moved.value.status == 200
                  and after["id"] == line_id and after["anchors"][0] == before[0] and after["anchors"][1]["date"] > before[1]["date"]
                  and after["anchors"][1]["price"] > before[1]["price"] and in_ratio(reshaped) and abs(reshaped[0] - (b[1] - 40)) <= 2
                  and abs(reshaped[-1] - a[1]) <= 2 and journey.axis(page) == axis, {"before": before, "after": after["anchors"], "levels": reshaped})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-22-fib-reshaped.png"))
    before = after["anchors"]

    page.mouse.move(*journey.at(page, right + 80, reshaped[3]))
    axis = journey.axis(page)
    page.mouse.down()
    page.mouse.move(*journey.at(page, right + 40, reshaped[3] + 30), steps=6)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]["anchors"]
    dragged = fib_levels(page)
    drop = [round(before[i]["price"] - after[i]["price"], 2) for i in (0, 1)]
    journey.check("拖任一條橫線：整組平移（兩點的價位降一樣多、日期都往前），圖表不平移", moved.value.status == 200 and drop[0] > 0
                  and abs(drop[0] - drop[1]) <= 0.02 and all(after[i]["date"] < before[i]["date"] for i in (0, 1))
                  and in_ratio(dragged) and abs(dragged[0] - reshaped[0] - 30) <= 2 and journey.axis(page) == axis,
                  {"before": before, "after": after, "levels": dragged})
    kept = after

    for period, name in [("weekly", "週 K"), ("monthly", "月 K")]:
        journey.choose_period(page, period)
        journey.check(f"{name}：黃金分割還在、選取還在，雲端的兩個錨點沒有被改", journey.ink(page)["count"] > 0 and delete.is_visible()
                      and journey.saved(page, code)[0]["anchors"] == kept)
        page.locator(".bd-chart").screenshot(path=str(OUT / f"desktop-23-fib-{period}.png"))
    journey.choose_period(page, "60m")
    journey.check("60 分 K：分析工具在、日 K 畫的線不出現", tools.is_visible() and journey.ink(page)["count"] == 0)
    journey.choose_period(page, "daily")
    back = fib_levels(page)
    journey.check("切回日 K：七條線回到原本的位置", len(back) == 7 and all(abs(one - other) <= 1.5 for one, other in zip(back, dragged)), {"back": back, "was": dragged})

    page.reload()
    journey.ready(page, code)
    reloaded = fib_levels(page)
    journey.check("重新整理後七條線還在原位，雲端的錨點相同，沒有選取", len(reloaded) == 7 and all(abs(one - other) <= 1.5 for one, other in zip(reloaded, dragged))
                  and journey.saved(page, code)[0]["anchors"] == kept and not delete.is_visible(), {"levels": reloaded, "was": dragged})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-24-fib-reloaded.png"))
    context.close()


def fib_mobile(journey: Journey, browser) -> None:
    print("票 05｜手機寬度 390（觸控）：黃金分割")
    code = "2454"
    context = journey.context(browser, OWNER, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context, code)
    fib = journey.tool(page, "fib")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    seen = fib_levels(page)
    journey.check("手機：桌機畫的黃金分割看得到", len(seen) > 0 and len(journey.saved(page, code)) == 1, seen)
    journey.open_menu(page, touch=True)
    layout = journey.menu(page)
    journey.close_menu(page, touch=True)
    journey.check("手機：選單裡每項仍夠大（至少 44px 高）、在畫面寬度內、不造成橫向捲動", menu_fits(layout, TOOL_COUNT), layout)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-14-fib-from-desktop.png"))

    page.touchscreen.tap(*journey.at(page, width * 0.7, seen[len(seen) // 2] + 5))
    journey.check("手機：手指點在橫線旁 5px 就選取，出現刪除鈕", delete.is_visible())
    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.tap()
    journey.check("手機：點刪除，整組消失並從雲端刪掉", removed.value.status == 204 and journey.saved(page, code) == []
                  and journey.ink(page)["count"] == 0 and not delete.is_visible())

    high, low = (round(width * 0.15), round(height * 0.3)), (round(width * 0.45), round(height * 0.72))  # 由高往低拉
    fib.tap()
    page.touchscreen.tap(*journey.at(page, *high))
    marked = journey.ink(page)
    journey.check("手機：點第一下後起點有記號，還沒儲存", 0 < marked["count"] and marked["maxX"] - marked["minX"] <= 12
                  and journey.saved(page, code) == [] and fib.get_attribute("aria-pressed") == "true", marked)
    a = marker(page, journey)

    cdp = context.new_cdp_session(page)

    def touch(kind: str, tx: float, ty: float) -> None:
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(tx), "y": round(ty)}]})

    x, y = journey.at(page, *low)
    touch("touchStart", x, y)
    page.wait_for_timeout(120)
    held = fib_levels(page)
    journey.check("手機：手指按下去還沒放開，七條線的預覽就出現", in_ratio(held, 3) and journey.saved(page, code) == [], held)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-15-fib-preview.png"))
    with page.expect_response(drawing_request("PUT")) as created:
        touch("touchEnd", x, y)
    items = journey.saved(page, code)
    levels = fib_levels(page)
    ink = journey.ink(page)
    journey.check("手機：放開就畫出七條線並儲存，工具回一般模式", created.value.status == 201 and len(items) == 1 and in_ratio(levels, 3)
                  and fib.get_attribute("aria-pressed") == "false", levels)
    journey.check("手機：由高往低拉，第一點（1）較高、第二點（0）較低；1 那條在上、0 那條在下", items[0]["kind"] == "fib"
                  and items[0]["anchors"][0]["price"] > items[0]["anchors"][1]["price"] and abs(levels[0] - a[1]) <= 3
                  and abs(levels[-1] - low[1]) <= 3, {"items": items, "levels": levels, "a": a, "low": low})
    journey.check("手機：橫線到右緣為止、不超出圖，標示留在圖內", ink["maxX"] <= width and ink["minX"] >= 0, {"ink": ink, "canvas": width})
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-16-fib-drawn.png"))
    mine = items[0]

    page.touchscreen.tap(*journey.at(page, width * 0.7, levels[0] + 9))
    journey.check("手機：手指點在最上面那條旁 9px 就選取", delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-17-fib-selected.png"))

    axis = journey.axis(page)
    scroll = page.evaluate("() => scrollY")
    x, y = journey.at(page, low[0], levels[-1])
    touch("touchStart", x, y)
    for step in range(1, 7):
        touch("touchMove", x, y - step * 6)
        page.wait_for_timeout(16)
    page.wait_for_timeout(250)  # 停一下再放開，不讓 Chromium 當成甩動（見票 02 的註記）
    with page.expect_response(drawing_request("PUT")) as moved:
        touch("touchEnd", x, y - 36)
    after = journey.saved(page, code)[0]
    reshaped = fib_levels(page)
    journey.check("手機：手指拖第二點往上，只有那一端變高、第一點不動，七條線重排；圖表不平移、頁面不捲動", moved.value.status == 200
                  and after["id"] == mine["id"] and after["anchors"][0] == mine["anchors"][0]
                  and after["anchors"][1]["price"] > mine["anchors"][1]["price"] and in_ratio(reshaped, 3)
                  and abs(reshaped[-1] - (levels[-1] - 36)) <= 3 and journey.axis(page) == axis and page.evaluate("() => scrollY") == scroll,
                  {"before": mine["anchors"], "after": after["anchors"], "levels": reshaped})
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-18-fib-reshaped.png"))

    journey.choose_period(page, "weekly")
    journey.check("手機：切到週 K 黃金分割還在", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 1)
    journey.choose_period(page, "daily")
    page.reload()
    journey.ready(page, code)
    reloaded = fib_levels(page)
    journey.check("手機：重新整理後七條線還在原位", len(reloaded) == 7 and all(abs(one - other) <= 1.5 for one, other in zip(reloaded, reshaped))
                  and journey.saved(page, code)[0]["anchors"] == after["anchors"], {"levels": reloaded, "was": reshaped})
    context.close()


# 文字標註的輸入框（圖表容器裡的小表單）：沒開著回 null。位置是相對瀏覽器視窗的 CSS 像素。
EDITOR_JS = """() => {
  const form = document.querySelector('#chartHost .kc-note');
  if (!form) return null;
  const input = form.querySelector('.kc-note-input'), cancel = form.querySelector('.kc-note-cancel'), limit = form.querySelector('.kc-note-limit');
  const box = form.getBoundingClientRect(), host = document.querySelector('#chartHost canvas').getBoundingClientRect();
  return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, height: box.height, value: input.value,
    focused: document.activeElement === input, fontSize: parseFloat(getComputedStyle(input).fontSize), cancelWidth: cancel.getBoundingClientRect().width,
    label: input.getAttribute('aria-label'), cancelLabel: cancel.getAttribute('aria-label'),
    limit: limit.hidden ? '' : limit.textContent, hostLeft: host.left, hostRight: host.right, viewport: innerHeight };
}"""

# 畫布上某一塊（CSS 像素，相對畫布左上角）裡，顏色接近漲跌色（K 棒）的像素數。
CANDLE_JS = """([left, top, width, height]) => {
  const canvas = document.querySelector('#chartHost canvas');
  const scale = canvas.height / canvas.getBoundingClientRect().height;
  const rgb = name => { const hex = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)); };
  const colors = [rgb('--up'), rgb('--down')];
  const data = canvas.getContext('2d').getImageData(Math.round(left * scale), Math.round(top * scale), Math.max(1, Math.round(width * scale)), Math.max(1, Math.round(height * scale))).data;
  let hits = 0;
  for (let i = 0; i < data.length; i += 4) if (colors.some(([r, g, b]) => Math.abs(data[i] - r) + Math.abs(data[i + 1] - g) + Math.abs(data[i + 2] - b) < 60)) hits++;
  return hits;
}"""

# 在畫布寬度 from～to 成之間找一個落在 K 棒實體上的點（CSS 像素，相對畫布左上角）：文字要寫在 K 棒上面才驗得到「不被 K 棒顏色吃掉」。
ON_CANDLE_JS = """([from, to]) => {
  const canvas = document.querySelector('#chartHost canvas');
  const { width, height } = canvas, scale = height / canvas.getBoundingClientRect().height;
  const rgb = name => { const hex = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)); };
  const colors = [rgb('--up'), rgb('--down')];
  const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  for (let x = Math.round(width * from); x < width * to; x += 2) for (let y = Math.round(height * 0.2); y < height * 0.7; y += 2) {
    const i = (y * width + x) * 4;
    if (colors.some(([r, g, b]) => Math.abs(data[i] - r) + Math.abs(data[i + 1] - g) + Math.abs(data[i + 2] - b) < 30)) return [Math.round(x / scale), Math.round(y / scale)];
  }
  return null;
}"""

MARKUP = "<b>粗體</b> <img src=x onerror=alert(1)> &amp;"


def editor(page) -> dict | None:
    return page.evaluate(EDITOR_JS)


def text_desktop(journey: Journey, browser) -> None:
    print("票 06｜桌機寬度 1280：文字標註")
    code = "2412"
    context = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    page = journey.open(context, code)
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    tools = page.locator("#toolsKey")
    text = journey.tool(page, "text")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    axis_width = 60

    journey.open_menu(page)
    order = journey.menu(page)["order"]
    journey.close_menu(page)
    # 本人 11-24 樣品 A：文字標註在「標記」一組（和到價提醒一起），名稱直接寫在項目上（沒有滑鼠停留的浮標了）
    journey.check("選單「標記」一組的第一項是文字標註，寫著名稱、有可讀名稱；共十種", order[order.index("#標記") + 1] == "text:文字標註:文字標註"
                  and len([one for one in order if not one.startswith("#")]) == TOOL_COUNT, order)
    page.mouse.move(5, 5)
    journey.check("起點：這一檔沒有畫線", journey.saved(page, code) == [] and journey.ink(page)["count"] == 0)

    spot = page.evaluate(ON_CANDLE_JS, [0.6, 0.7])  # 寫在一根 K 棒的實體上；放在圖的右半，手機預設只看最近 50 根也看得到
    journey.check("找到一根 K 棒當落點", spot is not None, spot)
    spot = spot or [round(width * 0.65), round(height * 0.4)]
    text.click()
    journey.check("選了文字工具：選單那一項是選中的、鍵上寫「文字標註」，還沒有輸入框", text.get_attribute("aria-pressed") == "true"
                  and tools.inner_text().strip() == "文字標註" and editor(page) is None)
    click = journey.at(page, *spot)
    page.mouse.click(*click)
    box_now = editor(page)
    journey.check("在圖上點一下：那個位置出現輸入框，可以直接打字；還沒儲存，工具仍選著", box_now is not None and box_now["focused"]
                  and abs(box_now["left"] - click[0]) <= 12 and abs((box_now["top"] + box_now["bottom"]) / 2 - click[1]) <= 2
                  and journey.saved(page, code) == [] and text.get_attribute("aria-pressed") == "true", {"editor": box_now, "click": click})
    journey.check("輸入框與取消鈕有給輔助科技讀的名稱", box_now and box_now["label"] == "文字標註的內容" and box_now["cancelLabel"] == "取消", box_now)
    page.keyboard.insert_text("突破前高，等回測")
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-25-text-editor.png"))
    with page.expect_response(drawing_request("PUT")) as created:
        page.keyboard.press("Enter")
    page.mouse.move(5, 5)
    items = journey.saved(page, code)
    ink = journey.ink(page)
    journey.check("按 Enter 確定：文字留在圖上並儲存（PUT 回 201），輸入框收起、工具回一般模式", created.value.status == 201 and editor(page) is None
                  and text.get_attribute("aria-pressed") == "false" and ink["count"] > 0, ink)
    journey.check("雲端存的是一則文字標註：一個錨點、內容原樣", len(items) == 1 and items[0]["kind"] == "text" and len(items[0]["anchors"]) == 1
                  and items[0]["text"] == "突破前高，等回測", items)
    journey.check("文字就在點的位置：左緣在那一根、上下置中在那個價位", abs(ink["minX"] - spot[0]) <= 16 and abs((ink["minY"] + ink["maxY"]) / 2 - spot[1]) <= 6
                  and ink["maxX"] - ink["minX"] < 140, {"ink": ink, "spot": spot})
    region = [ink["minX"], ink["minY"], ink["maxX"] - ink["minX"], ink["maxY"] - ink["minY"]]
    journey.check("字底下墊了底色：那一塊裡沒有 K 棒的紅綠透上來", page.evaluate(CANDLE_JS, region) == 0, page.evaluate(CANDLE_JS, region))
    journey.check("新寫的沒有被選取", not delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-26-text-drawn.png"))
    note_id = items[0]["id"]
    placed = items[0]["anchors"][0]

    centre = ((ink["minX"] + ink["maxX"]) / 2, (ink["minY"] + ink["maxY"]) / 2)
    page.mouse.click(*journey.at(page, *centre))
    journey.check("點文字：選取（出現刪除鈕），第一下不會打開輸入框", delete.is_visible() and editor(page) is None)
    page.mouse.move(5, 5)
    outlined = journey.ink(page)
    journey.check("選取時整塊文字外面多一圈外框", outlined["minX"] < ink["minX"] and outlined["maxX"] > ink["maxX"] and outlined["count"] > ink["count"], {"before": ink, "selected": outlined})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-27-text-selected.png"))

    page.mouse.move(*journey.at(page, *centre))
    axis = journey.axis(page)
    page.mouse.down()
    page.mouse.move(*journey.at(page, centre[0] + 90, centre[1] + 50), steps=6)
    with page.expect_response(drawing_request("PUT")) as moved:
        page.mouse.up()
    after = journey.saved(page, code)[0]
    journey.check("拖文字：移到較晚的日期、較低的價位，內容不變，自動儲存（PUT 回 200）；圖表不平移", moved.value.status == 200 and after["id"] == note_id
                  and after["anchors"][0]["date"] > placed["date"] and after["anchors"][0]["price"] < placed["price"]
                  and after["text"] == "突破前高，等回測" and journey.axis(page) == axis, {"before": placed, "after": after})
    page.mouse.move(5, 5)
    moved_ink = journey.ink(page)
    journey.check("畫面上的文字跟著移了 90、50 左右", abs(moved_ink["minX"] - outlined["minX"] - 90) <= 12 and abs(moved_ink["minY"] - outlined["minY"] - 50) <= 3,
                  {"before": outlined, "after": moved_ink})
    centre = ((moved_ink["minX"] + moved_ink["maxX"]) / 2, (moved_ink["minY"] + moved_ink["maxY"]) / 2)
    placed = after["anchors"][0]

    page.mouse.click(*journey.at(page, *centre))
    opened = editor(page)
    journey.check("再點一下已選取的文字：輸入框帶著原本的內容打開", opened is not None and opened["value"] == "突破前高，等回測" and opened["focused"], opened)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text("假突破，停損")
    with page.expect_response(drawing_request("PUT")) as edited:
        page.keyboard.press("Enter")
    after = journey.saved(page, code)[0]
    journey.check("改完按 Enter：內容換成新的並儲存（PUT 回 200），位置不變、仍選取著", edited.value.status == 200 and after["id"] == note_id
                  and after["text"] == "假突破，停損" and after["anchors"][0] == placed and editor(page) is None and delete.is_visible(), after)

    page.keyboard.press("Enter")  # 確定之後焦點回到圖表：直接按 Enter 又能改
    opened = editor(page)
    journey.check("鍵盤：選取著文字時按 Enter 也能打開輸入框", opened is not None and opened["value"] == "假突破，停損", opened)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text("假突破，停損出場")
    with page.expect_response(drawing_request("PUT")) as edited:
        page.mouse.click(*journey.at(page, width * 0.15, height * 0.8))
    journey.check("改到一半點圖上別處：就是確定（PUT 回 200），輸入框收起", edited.value.status == 200 and editor(page) is None
                  and journey.saved(page, code)[0]["text"] == "假突破，停損出場")

    page.mouse.click(*journey.at(page, *centre))  # 仍選取著：再點一下打開
    page.keyboard.press("Control+A")
    page.keyboard.insert_text("不要這個修改")
    page.keyboard.press("Escape")
    journey.check("改到一半按 Esc：輸入框收起，內容維持原樣；只是不改了，那一則仍選取著", editor(page) is None
                  and journey.saved(page, code)[0]["text"] == "假突破，停損出場" and delete.is_visible())
    page.mouse.click(*journey.at(page, *centre))
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    cleared = editor(page)
    page.keyboard.press("Enter")
    page.mouse.move(5, 5)
    journey.check("清空後確定：不會變成空白的標註，內容維持原樣", cleared is not None and cleared["value"] == "" and editor(page) is None
                  and journey.saved(page, code)[0]["text"] == "假突破，停損出場" and journey.ink(page)["count"] > 0,
                  {"cleared": cleared, "editor": editor(page), "saved": journey.saved(page, code), "ink": journey.ink(page)})

    # 新的標註：沒寫就確定、按 Esc、按取消鈕都不留東西。
    empty = journey.at(page, width * 0.25, height * 0.25)
    for name, finish in [("沒寫內容就按 Enter", lambda: page.keyboard.press("Enter")),
                         ("寫到一半按 Esc", lambda: (page.keyboard.insert_text("寫到一半"), page.keyboard.press("Escape"))),
                         ("寫到一半按取消鈕", lambda: (page.keyboard.insert_text("寫到一半"), page.locator("#chartHost .kc-note-cancel").click())),
                         ("寫到一半按離開鈕（＝再按一次文字工具）", lambda: (page.keyboard.insert_text("寫到一半"), text.click())),
                         ("只打空白就按 Enter", lambda: (page.keyboard.insert_text("   "), page.keyboard.press("Enter")))]:
        text.click()
        page.mouse.click(*empty)
        opened = editor(page) is not None
        finish()
        journey.check(f"{name}：不留下標註，輸入框收起、工具回一般模式", opened and editor(page) is None and len(journey.saved(page, code)) == 1
                      and text.get_attribute("aria-pressed") == "false")

    text.click()
    page.mouse.click(*empty)
    page.keyboard.insert_text("線" * 199)
    journey.check("打到 199 字：沒有提示", editor(page)["limit"] == "" and len(editor(page)["value"]) == 199)
    page.keyboard.insert_text("超過了" * 20)
    limited = editor(page)
    notice = page.locator("#chartHost .kc-note-limit").bounding_box()
    journey.check("超過 200 字：多的進不去，並出現「最多 200 字」（在輸入框上方，不會被手機鍵盤遮住）", len(limited["value"]) == 200 and limited["limit"] == "最多 200 字"
                  and notice["y"] + notice["height"] <= limited["top"], {"length": len(limited["value"]), "limit": limited["limit"], "notice": notice, "top": limited["top"]})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-28-text-limit.png"))
    page.keyboard.press("Escape")

    # 內容像標記語言：原樣存、原樣當成字畫出來，不會變成元素、不會執行。
    text.click()
    page.mouse.click(*journey.at(page, width * 0.2, height * 0.6))
    page.keyboard.insert_text(MARKUP)
    with page.expect_response(drawing_request("PUT")) as created:
        page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    items = journey.saved(page, code)
    # 圖表裡別的地方也可能有粗體字（以前的 K 棒浮窗就有），所以粗體只看有沒有帶著那兩個字的。
    injected = page.evaluate("() => document.querySelectorAll('#chartHost img, #chartHost script').length"
                             " + [...document.querySelectorAll('#chartHost b')].filter(b => b.textContent.includes('粗體')).length")
    journey.check("寫了像 HTML 的內容：原樣存、畫成字，頁面上沒有多出元素、沒有跳出對話框", created.value.status == 201 and len(items) == 2
                  and items[1]["text"] == MARKUP and injected == 0 and dialogs == [], {"items": items, "injected": injected, "dialogs": dialogs})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-29-text-markup.png"))
    page.mouse.click(*journey.at(page, width * 0.2 + 30, height * 0.6))
    with page.expect_response(drawing_request("DELETE")) as removed:
        page.keyboard.press("Delete")
    journey.check("選取那一則按 Delete：刪掉並從雲端移除，另一則還在", removed.value.status == 204
                  and [item["id"] for item in journey.saved(page, code)] == [note_id] and not delete.is_visible())
    kept = journey.saved(page, code)[0]
    page.mouse.move(5, 5)
    daily = journey.ink(page)

    for period, name in [("weekly", "週 K"), ("monthly", "月 K")]:
        journey.choose_period(page, period)
        journey.check(f"{name}：文字還在，雲端的錨點與內容沒有被改", journey.ink(page)["count"] > 0 and journey.saved(page, code)[0] == kept)
        page.locator(".bd-chart").screenshot(path=str(OUT / f"desktop-30-text-{period}.png"))
    journey.choose_period(page, "60m")
    journey.check("60 分 K：分析工具在、日 K 寫的文字不出現", tools.is_visible() and journey.ink(page)["count"] == 0)
    journey.choose_period(page, "daily")
    back = journey.ink(page)
    journey.check("切回日 K：文字回到原本的位置", back["count"] > 0 and abs(back["minX"] - daily["minX"]) <= 1.5 and abs(back["minY"] - daily["minY"]) <= 1.5, {"back": back, "was": daily})

    page.locator("#chartHost canvas").focus()
    for _ in range(6):
        page.keyboard.press("+")  # 放大到只看二十幾根
    page.keyboard.press("Home")   # 再移到最前面：那一天不在畫面內
    journey.check("平移到那一天不在畫面內：文字不顯示", journey.ink(page)["count"] == 0, journey.ink(page))
    page.keyboard.press("ArrowRight")
    journey.check("（文字不在畫面上時雲端那一則沒有被動到）", journey.saved(page, code)[0] == kept)

    page.reload()
    journey.ready(page, code)
    page.mouse.move(5, 5)
    reloaded = journey.ink(page)
    journey.check("重新整理後文字還在原位、內容相同，沒有選取", reloaded["count"] > 0 and abs(reloaded["minX"] - daily["minX"]) <= 1.5
                  and abs(reloaded["minY"] - daily["minY"]) <= 1.5 and journey.saved(page, code)[0] == kept and not delete.is_visible(), {"reloaded": reloaded, "was": daily})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-31-text-reloaded.png"))
    context.close()

    # 另一個帳號（前面的段落核准過的；單跑這一段時還沒核准，那就是 403）：讀不到這則文字。
    other = journey.context(browser, OTHER, viewport={"width": 1280, "height": 900})
    theirs = other.request.get(f"{journey.base}/api/drawings/{code}")
    journey.check("別的帳號讀同一檔：讀不到這則文字", theirs.status == 403 or (theirs.status == 200 and theirs.json()["items"] == []), theirs.status)
    other.close()


def text_mobile(journey: Journey, browser) -> None:
    print("票 06｜手機寬度 390（觸控）：文字標註")
    code = "2412"
    size = {"width": 390, "height": 844}
    context = journey.context(browser, OWNER, viewport=size, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context, code)
    text = journey.tool(page, "text")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    # 這一段的截圖都用整頁的：元素截圖會讓 Chromium 的觸控模擬失去「粗指標」（pointer: coarse），之後量到的會是桌機的樣式。
    seen = journey.ink(page)
    journey.check("手機：桌機寫的文字看得到，整塊在圖內、沒有進價格軸", seen["count"] > 0 and seen["minX"] >= 0 and seen["maxX"] <= width - 40
                  and len(journey.saved(page, code)) == 1, {"ink": seen, "canvas": width})
    journey.open_menu(page, touch=True)
    layout = journey.menu(page)
    journey.close_menu(page, touch=True)
    journey.check("手機：選單裡每項仍夠大（至少 44px 高）、在畫面寬度內、不造成橫向捲動", menu_fits(layout, TOOL_COUNT), layout)
    page.screenshot(path=str(OUT / "mobile-19-text-from-desktop.png"))
    desktop_note = journey.saved(page, code)[0]

    # 先選文字工具（選單從圖的上方浮出，點選單裡的項目時頁面可能為了讓它露出來而捲動），
    # 再把圖捲到畫面下半（手機上圖常在這個位置），在圖的下半部寫字：鍵盤一彈出來就會蓋到。
    spot = (round(width * 0.08), round(height * 0.62))  # 靠左：不和桌機寫的那一則疊在一起
    text.tap()
    page.evaluate("() => { const top = document.querySelector('#chartHost canvas').getBoundingClientRect().top + scrollY; scrollTo(0, top - 420); }")
    page.wait_for_timeout(100)
    home = page.evaluate("() => scrollY")
    tap = journey.at(page, *spot)
    page.touchscreen.tap(*tap)
    opened = editor(page)
    journey.check("手機：點一下出現輸入框並取得焦點（鍵盤會跟著出來），位置在點的地方", opened is not None and opened["focused"]
                  and abs(opened["left"] - tap[0]) <= 12 and abs((opened["top"] + opened["bottom"]) / 2 - tap[1]) <= 2
                  and page.evaluate("() => matchMedia('(pointer: coarse)').matches"), {"editor": opened, "tap": tap})
    journey.check("手機：輸入框字級至少 16px（iOS 才不會在聚焦時把整頁放大）、高度與取消鈕都至少 44px、整個在圖內",
                  opened and opened["fontSize"] >= 16 and opened["height"] >= 44 and opened["cancelWidth"] >= 44
                  and opened["left"] >= opened["hostLeft"] and opened["right"] <= opened["hostRight"] - 40, opened)
    journey.check("手機：打開輸入框不會自己捲動頁面", page.evaluate("() => scrollY") == home)
    page.screenshot(path=str(OUT / "mobile-20-text-editor.png"))

    # 模擬螢幕鍵盤彈出：看得到的範圍從 844 變成 430（Chromium 沒有真的鍵盤，用縮小視窗代替）。
    covered = opened["bottom"] > 430
    page.set_viewport_size({"width": 390, "height": 430})
    page.wait_for_timeout(300)
    lifted = editor(page)
    journey.check("手機：鍵盤彈出前輸入框的位置會被鍵盤蓋住（這一步才驗得到東西）", covered, opened)
    journey.check("手機：鍵盤彈出後輸入框整個露在鍵盤上方、仍是焦點", lifted is not None and lifted["focused"] and lifted["top"] >= 0
                  and lifted["bottom"] <= lifted["viewport"] - 8, lifted)
    page.keyboard.insert_text("手機寫的備註")
    page.screenshot(path=str(OUT / "mobile-21-text-keyboard.png"))
    with page.expect_response(drawing_request("PUT")) as created:
        page.keyboard.press("Enter")  # 鍵盤上的「完成」
    page.set_viewport_size(size)      # 鍵盤收起
    page.wait_for_timeout(300)
    items = journey.saved(page, code)
    journey.check("手機：按完成就儲存（PUT 回 201），輸入框收起、工具回一般模式", created.value.status == 201 and editor(page) is None
                  and text.get_attribute("aria-pressed") == "false" and len(items) == 2 and items[1]["text"] == "手機寫的備註", items)
    journey.check("手機：寫完後頁面回到打開輸入框之前的位置", page.evaluate("() => scrollY") == home, {"scrollY": page.evaluate("() => scrollY"), "home": home})
    page.screenshot(path=str(OUT / "mobile-22-text-drawn.png"))
    mine = items[1]

    # 這一則的位置：左緣在點的那一根、上下置中在點的價位（另一則是桌機寫的，用位置分開）。
    target = (spot[0] + 20, spot[1])
    bar_before = journey.bar(page)["selection"]
    page.touchscreen.tap(*journey.at(page, *target))
    journey.check("手機：手指點文字就選取（出現刪除鈕），K 棒沒有被選取", delete.is_visible() and editor(page) is None
                  and journey.bar(page)["selection"] == bar_before, {"before": bar_before, "after": journey.bar(page)})
    page.screenshot(path=str(OUT / "mobile-23-text-selected.png"))

    cdp = context.new_cdp_session(page)

    def touch(kind: str, tx: float, ty: float) -> None:
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(tx), "y": round(ty)}]})

    axis = journey.axis(page)
    scroll = page.evaluate("() => scrollY")
    x, y = journey.at(page, *target)
    touch("touchStart", x, y)
    for step in range(1, 7):
        touch("touchMove", x + step * 5, y - step * 8)
        page.wait_for_timeout(16)
    page.wait_for_timeout(250)  # 停一下再放開，不讓 Chromium 當成甩動
    with page.expect_response(drawing_request("PUT")) as moved:
        touch("touchEnd", x + 30, y - 48)
    after = [item for item in journey.saved(page, code) if item["id"] == mine["id"]][0]
    journey.check("手機：手指拖文字往右上，移到較高的價位，內容不變；圖表不平移、頁面不捲動", moved.value.status == 200
                  and after["anchors"][0]["price"] > mine["anchors"][0]["price"] and after["anchors"][0]["date"] >= mine["anchors"][0]["date"]
                  and after["text"] == mine["text"] and journey.axis(page) == axis and page.evaluate("() => scrollY") == scroll,
                  {"before": mine["anchors"], "after": after["anchors"]})
    target = (target[0] + 30, target[1] - 48)
    page.screenshot(path=str(OUT / "mobile-24-text-moved.png"))

    page.touchscreen.tap(*journey.at(page, *target))
    opened = editor(page)
    journey.check("手機：再點一下已選取的文字，輸入框帶著原本的內容打開", opened is not None and opened["value"] == "手機寫的備註" and opened["focused"], opened)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text("手機改過的備註")
    bar_before = journey.bar(page)["selection"]
    with page.expect_response(drawing_request("PUT")) as edited:
        page.touchscreen.tap(*journey.at(page, width * 0.2, height * 0.12))
    after = [item for item in journey.saved(page, code) if item["id"] == mine["id"]][0]
    journey.check("手機：改完點圖上別處就是確定並儲存（PUT 回 200）；那一下沒有選到 K 棒", edited.value.status == 200 and after["text"] == "手機改過的備註"
                  and editor(page) is None and journey.bar(page)["selection"] == bar_before, {"item": after, "bar": [bar_before, journey.bar(page)]})

    page.touchscreen.tap(*journey.at(page, *target))
    opened = editor(page)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text("不要這個修改")
    page.locator("#chartHost .kc-note-cancel").tap()
    journey.check("手機：改到一半點取消鈕，輸入框收起、內容維持原樣", opened is not None and editor(page) is None
                  and [item for item in journey.saved(page, code) if item["id"] == mine["id"]][0]["text"] == "手機改過的備註")

    # 靠近圖的右緣寫一則長的：輸入框與文字都收在圖內、不進價格軸，文字自動換行。
    text.tap()
    page.touchscreen.tap(*journey.at(page, width - 110, height * 0.25))
    near_edge = editor(page)
    journey.check("手機：靠右緣點一下，輸入框整個留在圖內（不超出畫面）", near_edge is not None
                  and near_edge["right"] <= near_edge["hostRight"] and near_edge["left"] >= near_edge["hostLeft"], near_edge)
    long_text = "跌破季線後連三天收不回，量縮反彈先減碼三分之一"
    page.keyboard.insert_text(long_text)
    before = journey.ink(page)
    with page.expect_response(drawing_request("PUT")) as created:
        page.keyboard.press("Enter")
    ink = journey.ink(page)
    journey.check("手機：靠右緣的長文字換成兩行以上，整塊留在圖內、不超出圖", created.value.status == 201 and ink["maxX"] <= width and ink["minX"] >= 0
                  and ink["count"] > before["count"], {"ink": ink, "canvas": width})
    page.screenshot(path=str(OUT / "mobile-25-text-wrapped.png"))

    journey.choose_period(page, "weekly")
    journey.check("手機：切到週 K 三則文字都還在", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 3)
    journey.choose_period(page, "daily")
    page.reload()
    journey.ready(page, code)
    reloaded = journey.saved(page, code)
    journey.check("手機：重新整理後三則都還在，內容相同（桌機寫的那一則沒被動到）", [item["text"] for item in reloaded] == [desktop_note["text"], "手機改過的備註", long_text]
                  and reloaded[0] == desktop_note and journey.ink(page)["count"] > 0, reloaded)
    page.screenshot(path=str(OUT / "mobile-26-text-reloaded.png"))
    context.close()


class SaveNetwork:
    """票 07：控制這一頁畫線存檔請求（PUT、DELETE）的結果。
    mode："pass" 照常送到伺服器、"abort" 連不上、"429" 被限速（等 retry_after 秒）、其他數字＝直接回那個狀態碼。
    sent 記下每一筆：（時間, 方法, 「代號/識別碼」, 當時的 mode）。"""

    def __init__(self, page) -> None:
        self.mode = "pass"
        self.retry_after = "4"
        self.sent: list[tuple[float, str, str, str]] = []
        page.route("**/api/drawings/*/*", self.handle)

    def handle(self, route) -> None:
        request = route.request
        self.sent.append((time.monotonic(), request.method, request.url.split("/api/drawings/")[1], self.mode))
        if self.mode == "pass":
            route.continue_()
        elif self.mode == "abort":
            route.abort()
        elif self.mode == "429":
            route.fulfill(status=429, headers={"retry-after": self.retry_after}, content_type="application/json", body="{}")
        else:
            route.fulfill(status=int(self.mode), content_type="application/json", body="{}")


# 畫布上用畫線顏色畫的每一條橫線：[位置（CSS 像素）, 濃淡（0～1）]。
# 圖表的畫布底是透明的：還沒存上去、畫得比較淡的線，顏色和原本一樣，只是不透明度剩四成五左右，
# 所以 LINES_JS（只比顏色）分不出濃淡；這裡另外量那一列的不透明度（取線中央最濃的那一列）。
SHADE_JS = """() => {
  const canvas = document.querySelector('#chartHost canvas');
  const { width, height } = canvas;
  const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  const hex = getComputedStyle(document.documentElement).getPropertyValue('--draw').trim();
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const rows = [];
  for (let y = 0; y < height; y++) {
    const alphas = [];
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      if (Math.abs(data[i] - r) + Math.abs(data[i + 1] - g) + Math.abs(data[i + 2] - b) < 60) alphas.push(data[i + 3]);
    }
    if (alphas.length > width * 0.3) rows.push([y, alphas.sort((a, b) => a - b)[alphas.length >> 1] / 255]);
  }
  const scale = height / canvas.getBoundingClientRect().height;
  const lines = [];
  rows.forEach(row => { if (!lines.length || row[0] - lines[lines.length - 1].at(-1)[0] > 1) lines.push([row]); else lines[lines.length - 1].push(row); });
  return lines.map(group => [Math.round((group[0][0] + group.at(-1)[0]) / 2 / scale * 10) / 10, Math.max(...group.map(row => row[1]))]);
}"""


# 原本濃淡的線（已經存上去的）。journey.lines 是不分濃淡的全部。
def solid_lines(page) -> list[float]:
    return [y for y, shade in page.evaluate(SHADE_JS) if shade > 0.8]


def save_desktop(journey: Journey, browser) -> None:
    print("票 07｜桌機寬度 1280：存檔失敗時不白畫")
    code, other = "2882", "2881"
    context = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    page = journey.open(context, code, expect_failures=True)
    dialogs: list[str] = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    net = SaveNetwork(page)
    hint = page.locator("#drawUnsaved")
    tool = journey.tool(page, "hline")
    host = page.locator("#chartHost")

    def click_line(fx: float, fy: float) -> float:
        tool.click()   # 先選工具再量位置（見 Journey.draw）
        x, y, canvas_y = journey.point(page, fx, fy)
        page.mouse.click(x, y)
        return canvas_y

    def drag_line(line_y: float, by: float) -> None:
        x, y = journey.canvas_xy(page, 0.7, line_y)
        page.mouse.click(x, y)
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x, y + by, steps=6)
        page.mouse.up()
        page.wait_for_timeout(120)

    journey.check("起點：沒有線、沒有提示", solid_lines(page) == [] and journey.lines(page) == [] and not hint.is_visible(), journey.lines(page))
    chart_top = host.bounding_box()["y"]

    y1 = journey.draw(page, 0.4, 0.3)
    first = journey.saved(page, code)[0]["id"]
    journey.check("連線正常時畫線：不出現提示，線是原本的濃淡", not hint.is_visible() and near(solid_lines(page), y1), solid_lines(page))

    # ---- 斷線：真的把瀏覽器的網路關掉 ----
    context.set_offline(True)
    y2 = click_line(0.5, 0.45)
    hint.wait_for(state="visible", timeout=5000)
    journey.check("斷線時畫線：線留在畫面上，畫得比較淡", near(journey.lines(page), y2) and not near(solid_lines(page), y2),
                  {"faint": journey.lines(page), "full": solid_lines(page), "want": y2})
    box = hint.bounding_box()
    journey.check("出現「未儲存」：在圖上方那一行、不蓋到圖，圖的位置沒有被推動，沒有跳出視窗",
                  hint.inner_text().strip() == "未儲存" and box["y"] + box["height"] <= chart_top + 0.5
                  and abs(host.bounding_box()["y"] - chart_top) < 0.5 and dialogs == [],
                  {"hint": box, "chart_top": chart_top, "chart_top_now": host.bounding_box()["y"], "text": hint.inner_text(), "dialogs": dialogs})
    page.screenshot(path=str(OUT / "desktop-32-unsaved.png"))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-33-unsaved-chart.png"))

    drag_line(y1, 40)
    journey.check("斷線時拖動已經存過的線：留在新位置，也變淡", near(journey.lines(page), y1 + 40, 2) and not near(journey.lines(page), y1)
                  and not near(solid_lines(page), y1 + 40, 2), journey.lines(page))
    page.keyboard.press("Escape")

    journey.tool(page, "text").click()
    x, y, _ = journey.point(page, 0.25, 0.62)
    page.mouse.click(x, y)
    page.keyboard.type("補存測試")
    page.keyboard.press("Enter")
    page.wait_for_timeout(120)
    journey.check("斷線時寫一則文字：輸入框照常收起", editor(page) is None)

    y3 = click_line(0.5, 0.8)
    page.mouse.click(*journey.canvas_xy(page, 0.7, y3))
    page.keyboard.press("Delete")
    page.wait_for_timeout(120)
    journey.check("斷線時畫了一條又刪掉：那一條消失，其他的還在", not near(journey.lines(page), y3) and near(journey.lines(page), y2)
                  and near(journey.lines(page), y1 + 40, 2), journey.lines(page))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-34-offline-edits.png"))

    page.evaluate("() => { setTimeout(() => location.reload(), 0); }")
    page.wait_for_timeout(1200)
    journey.check("還沒存上去就重新整理：瀏覽器先提醒；留下來之後線都還在", dialogs == ["beforeunload"] and near(journey.lines(page), y2)
                  and near(journey.lines(page), y1 + 40, 2) and hint.is_visible(), {"dialogs": dialogs, "lines": journey.lines(page)})

    # ---- 連線恢復 ----
    offline_tries = len(net.sent)
    context.set_offline(False)
    hint.wait_for(state="hidden", timeout=10_000)
    page.wait_for_timeout(200)
    items = journey.saved(page, code)
    by_id = {item["id"]: item for item in items}
    resent = [(method, path.split("/")[1]) for _, method, path, _ in net.sent[offline_tries:]]
    journey.check("連線恢復後自動補存，提示消失", not hint.is_visible() and len(items) == 3
                  and sorted(item["kind"] for item in items) == ["hline", "hline", "text"], items)
    kinds = [(method, by_id.get(drawing, {}).get("kind"), drawing == first) for method, drawing in resent]
    journey.check("補存的先後和當初做的一樣（新畫的線 → 拖過的那一條 → 文字），畫了又刪的那一條沒有送",
                  kinds == [("PUT", "hline", False), ("PUT", "hline", True), ("PUT", "text", False)], kinds)
    journey.check("文字的內容也補存上去了", [item["text"] for item in items if item["kind"] == "text"] == ["補存測試"])
    full = solid_lines(page)
    journey.check("補存之後線恢復原本的濃淡", near(full, y2) and near(full, y1 + 40, 2), full)
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-35-restored.png"))

    dialogs.clear()
    page.reload()
    journey.ready(page, code)
    journey.check("都存上去之後重新整理：不再提醒，兩條線與文字都還在", dialogs == [] and len(solid_lines(page)) == 2
                  and len(journey.saved(page, code)) == 3, {"dialogs": dialogs, "lines": solid_lines(page)})

    # ---- 被限速：照伺服器說的秒數等 ----
    net.mode, net.retry_after = "429", "4"
    drag_line(y2, 30)
    hint.wait_for(state="visible", timeout=5000)
    limited = len(net.sent)
    limited_at = net.sent[-1][0]
    net.mode = "pass"
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(3000)
    journey.check("被限速（429，要求等 4 秒）：三秒內一筆都不重送，連線恢復的通知也不會提早", len(net.sent) == limited and hint.is_visible(),
                  [entry[1:] for entry in net.sent[limited:]])
    hint.wait_for(state="hidden", timeout=6000)
    waited = net.sent[limited][0] - limited_at
    journey.check("等滿 4 秒才補存，存上去後提示消失", 3.8 <= waited <= 6 and len(net.sent) == limited + 1, waited)
    page.keyboard.press("Escape")

    # ---- 伺服器暫時出錯：自動再試 ----
    net.mode = "503"
    y4 = click_line(0.45, 0.72)
    hint.wait_for(state="visible", timeout=5000)
    net.mode = "pass"
    hint.wait_for(state="hidden", timeout=6000)
    journey.check("伺服器暫時出錯（503）：線留著，兩秒後自動再試、存上去", near(solid_lines(page), y4) and len(journey.saved(page, code)) == 4)

    # ---- 被拒絕：不重試，說「儲存失敗」 ----
    net.mode = "409"
    y5 = click_line(0.55, 0.18)
    page.wait_for_function("() => document.getElementById('drawUnsaved').textContent === '儲存失敗'", timeout=5000)
    refused = len(net.sent)
    page.wait_for_timeout(4500)
    journey.check("被拒絕（409）：提示是「儲存失敗」，那一條留在畫面上、畫得比較淡，不會一直重試",
                  hint.is_visible() and len(net.sent) == refused and near(journey.lines(page), y5) and not near(solid_lines(page), y5),
                  {"sent": len(net.sent) - refused, "faint": journey.lines(page)})
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-36-refused.png"))
    net.mode = "pass"
    page.mouse.click(*journey.canvas_xy(page, 0.7, y5))
    page.keyboard.press("Delete")
    hint.wait_for(state="hidden", timeout=5000)
    journey.check("刪掉沒存成的那一條：提示消失，雲端還是四筆", not near(journey.lines(page), y5) and len(journey.saved(page, code)) == 4)

    # ---- 換股票：不丟、不混 ----
    net.mode = "abort"
    y6 = click_line(0.5, 0.9)
    hint.wait_for(state="visible", timeout=5000)
    journey.choose_period(page, "60m")
    box = hint.bounding_box() or {"y": 0, "height": 9999}
    journey.check("切到 60 分 K：「未儲存」還看得到、不蓋到圖", hint.is_visible()
                  and box["y"] + box["height"] <= host.bounding_box()["y"] + 0.5, box)
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-37-unsaved-intraday.png"))
    journey.choose_period(page, "daily")
    journey.check("切回日 K：提示與那條線都還在", hint.is_visible() and near(journey.lines(page), y6), journey.lines(page))
    page.locator("#codeInput").fill(other)
    page.locator("#searchForm button[type=submit]").click()
    journey.ready(page, other)
    journey.check("換到另一檔：看不到上一檔的線；上一檔還沒存上去，提示還在", journey.lines(page) == [] and hint.is_visible(), journey.lines(page))
    y7 = click_line(0.5, 0.5)
    page.wait_for_timeout(200)
    net.mode = "pass"
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    hint.wait_for(state="hidden", timeout=10_000)
    page.wait_for_timeout(200)
    mine, theirs = journey.saved(page, code), journey.saved(page, other)
    journey.check("恢復後各自補存到原本那一檔，沒有混在一起", len(mine) == 5 and len(theirs) == 1
                  and not {item["id"] for item in mine} & {item["id"] for item in theirs}, {"first": len(mine), "second": len(theirs)})
    journey.check("新的一檔只有自己那一條", near(solid_lines(page), y7) and len(solid_lines(page)) == 1, solid_lines(page))
    page.go_back()
    journey.ready(page, code)
    journey.check("回到上一檔：補存的那一條在", near(solid_lines(page), y6) and len(solid_lines(page)) == 4, solid_lines(page))

    broken = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    broken.route("**/drawing-sync.js*", lambda route: route.abort())
    broken_page = journey.open(broken, code)
    journey.check("存檔那支程式沒載入時：個股頁其他內容照常顯示，「分析工具」不出現", broken_page.locator("#close").inner_text().strip() not in ("", "—")
                  and broken_page.locator("#chartHost canvas").is_visible() and not broken_page.locator("#toolsKey").is_visible()
                  and not broken_page.locator("#drawTools").is_visible())
    broken.close()
    context.close()


def save_mobile(journey: Journey, browser) -> None:
    print("票 07｜手機寬度 390（觸控）：存檔失敗時不白畫")
    code = "2891"
    context = journey.context(browser, OWNER, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context, code, expect_failures=True)
    net = SaveNetwork(page)
    hint = page.locator("#drawUnsaved")
    tool = journey.tool(page, "hline")
    delete = page.locator("#drawDelete")
    host = page.locator("#chartHost")
    fits = "() => document.documentElement.scrollWidth <= innerWidth"
    journey.check("手機起點：沒有線、沒有提示", journey.lines(page) == [] and not hint.is_visible())

    context.set_offline(True)
    tool.tap()   # 先選工具再量位置（見 Journey.draw）
    chart_top = host.bounding_box()["y"]
    x, y, line_y = journey.point(page, 0.45, 0.4)
    page.touchscreen.tap(x, y)
    hint.wait_for(state="visible", timeout=5000)
    journey.check("手機斷線時畫線：線留在畫面上（比較淡），出現「未儲存」", near(journey.lines(page), line_y) and not near(solid_lines(page), line_y)
                  and hint.inner_text().strip() == "未儲存", journey.lines(page))
    box, button = hint.bounding_box(), journey.hit(page, "#toolsKey")
    journey.check("手機：提示在圖上方那一行、不蓋到圖、不推動圖，不造成橫向捲動，「分析工具」鍵仍至少 44px",
                  box["y"] + box["height"] <= chart_top + 0.5 and abs(host.bounding_box()["y"] - chart_top) < 0.5 and page.evaluate(fits)
                  and button["width"] >= 44 and button["height"] >= 44 and box["x"] + box["width"] <= 390,
                  {"hint": box, "button": button, "chart_top": chart_top, "chart_top_now": host.bounding_box()["y"], "fits": page.evaluate(fits)})
    page.screenshot(path=str(OUT / "mobile-27-unsaved.png"))

    page.touchscreen.tap(*journey.canvas_xy(page, 0.7, line_y))
    page.wait_for_timeout(150)
    # 本人 11-24 樣品 A：刪除鈕排在「分析工具」那一行，提示在圖下方另一行（不再和按鈕擠同一行，所以提示照舊寫完整的「未儲存」）
    box, button, trash = hint.bounding_box(), journey.hit(page, "#toolsKey"), journey.hit(page, "#drawDelete")
    journey.check("手機：選了線、多出刪除鈕時提示仍完整、按鈕不被擠小（觸控範圍至少 44px）、都在畫面內、不橫向捲動、圖不移動",
                  delete.is_visible() and hint.is_visible() and hint.inner_text().strip() == "未儲存" and page.evaluate(fits)
                  and button["width"] >= 44 and trash["width"] >= 44 and trash["height"] >= 44 and trash["right"] <= 390 and box["x"] + box["width"] <= 390
                  and abs(host.bounding_box()["y"] - chart_top) < 0.5,
                  {"hint": box, "button": button, "delete": trash, "chart_top": chart_top, "chart_top_now": host.bounding_box()["y"]})
    page.screenshot(path=str(OUT / "mobile-28-unsaved-selected.png"))

    cdp = context.new_cdp_session(page)

    def touch(kind: str, tx: float, ty: float) -> None:
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": round(tx), "y": round(ty)}]})

    x, y = journey.canvas_xy(page, 0.7, line_y)
    touch("touchStart", x, y)
    for step in range(1, 7):
        touch("touchMove", x, y + step * 5)
        page.wait_for_timeout(16)
    page.wait_for_timeout(250)
    touch("touchEnd", x, y)
    page.wait_for_timeout(150)
    journey.check("手機斷線時拖動：線留在新位置", near(journey.lines(page), line_y + 30, 2) and len(journey.lines(page)) == 1, journey.lines(page))

    tries = len(net.sent)
    context.set_offline(False)
    hint.wait_for(state="hidden", timeout=10_000)
    page.wait_for_timeout(200)
    items = journey.saved(page, code)
    journey.check("手機：連線恢復後自動補存（只送最後的位置一次）、提示消失、線恢復原本的濃淡",
                  len(items) == 1 and [entry[1] for entry in net.sent[tries:]] == ["PUT"] and near(solid_lines(page), line_y + 30, 2),
                  {"items": len(items), "sent": [entry[1:] for entry in net.sent[tries:]], "lines": solid_lines(page)})
    page.screenshot(path=str(OUT / "mobile-29-restored.png"))
    page.reload()
    journey.ready(page, code)
    journey.check("手機：重新整理後那條線還在補存後的位置", near(solid_lines(page), line_y + 30, 2) and len(journey.saved(page, code)) == 1, solid_lines(page))
    context.close()


class Finger:
    """用 CDP 送真正的觸控事件（Playwright 的 touchscreen 只有「點一下」）：可以晃、可以滑、可以兩指。"""

    def __init__(self, context, page) -> None:
        self.page = page
        self.cdp = context.new_cdp_session(page)

    def send(self, kind: str, points: list[tuple[float, float]]) -> None:
        self.cdp.send("Input.dispatchTouchEvent", {
            "type": kind, "touchPoints": [{"x": round(x), "y": round(y), "id": index} for index, (x, y) in enumerate(points)]})

    def tap(self, x: float, y: float, wobble: tuple[float, float] = (5, 2)) -> None:
        """手指點一下：按下、晃幾像素、放開（放開的位置是晃過之後的地方）。"""
        self.send("touchStart", [(x, y)])
        self.send("touchMove", [(x + wobble[0], y + wobble[1])])
        self.send("touchEnd", [])
        self.page.wait_for_timeout(150)

    def slide(self, x: float, y: float, dx: float, dy: float, steps: int = 6) -> None:
        """按住滑過去：每一步之間停一下，放開前停久一點（不讓 Chromium 當成甩動）。"""
        self.send("touchStart", [(x, y)])
        for step in range(1, steps + 1):
            self.send("touchMove", [(x + dx * step / steps, y + dy * step / steps)])
            self.page.wait_for_timeout(16)
        self.page.wait_for_timeout(250)
        self.send("touchEnd", [])
        self.page.wait_for_timeout(150)

    def pinch(self, x: float, y: float, start: float, end: float, steps: int = 6) -> None:
        """兩指以 (x, y) 為中心左右拉開或收攏（兩指距離由 start 變成 end），再先後放開。"""
        self.send("touchStart", [(x - start / 2, y)])
        self.send("touchStart", [(x - start / 2, y), (x + start / 2, y)])
        for step in range(1, steps + 1):
            gap = start + (end - start) * step / steps
            self.send("touchMove", [(x - gap / 2, y), (x + gap / 2, y)])
            self.page.wait_for_timeout(16)
        self.page.wait_for_timeout(200)
        self.send("touchEnd", [(x - end / 2, y)])
        self.send("touchEnd", [])
        self.page.wait_for_timeout(150)


# 票 08 用的六種工具：在 390 寬的畫布（358×300；繪圖區上緣 10、下緣 278，右側約 0.8 之後是價格軸）上各畫在一塊不重疊的地方
# （畫布寬、高的幾成）。pick：點這裡選取（線身上，不在把手的可抓範圍內）；grip：只拖這一端的把手（第幾個錨點）。
# 選取之後每一條都整個往下拖 MOVE 像素，之後選取、拖把手、刪除都在往下 MOVE 的位置。
TOUCH_TOOLS = [
    {"kind": "hline", "name": "水平線", "taps": [(0.30, 0.10)], "pick": (0.60, 0.10)},
    {"kind": "trend", "name": "趨勢線", "taps": [(0.06, 0.36), (0.28, 0.24)], "pick": (0.17, 0.30), "grip": 1},
    {"kind": "ray", "name": "射線", "taps": [(0.06, 0.55), (0.26, 0.60)], "pick": (0.16, 0.575), "grip": 1},
    {"kind": "rect", "name": "矩形", "taps": [(0.36, 0.24), (0.50, 0.40)], "pick": (0.43, 0.24), "grip": 1},
    {"kind": "fib", "name": "黃金分割", "taps": [(0.56, 0.30), (0.66, 0.48)], "pick": (0.70, 0.30), "grip": 1},
    {"kind": "text", "name": "文字標註", "taps": [(0.08, 0.80)], "pick": (0.08, 0.80), "text": "手機驗收"},
]
MOVE = 30
PHONE = {"width": 390, "height": 844}
FITS_JS = "() => document.documentElement.scrollWidth <= innerWidth"


def touch_mobile(journey: Journey, browser) -> None:
    print("票 08｜手機寬度 390（觸控）：六種工具逐一走過畫、選取、拖動、調整、刪除、重新整理後還在")
    code = "2603"
    context = journey.context(browser, OWNER, viewport=PHONE, device_scale_factor=3, is_mobile=True, has_touch=True)
    page = journey.open(context, code)
    finger = Finger(context, page)
    delete = page.locator("#drawDelete")
    canvas = page.locator("#chartHost canvas")
    box = canvas.bounding_box()
    width, height = box["width"], box["height"]
    px = lambda point: journey.at(page, width * point[0], height * point[1])   # noqa: E731
    pressed = lambda kind: journey.tool(page, kind).get_attribute("aria-pressed") == "true"   # noqa: E731

    # ---- 「分析工具」鍵與選單 ----
    key_hit = journey.hit(page, "#toolsKey")
    journey.open_menu(page, touch=True)
    layout = journey.menu(page)
    page.screenshot(path=str(OUT / "mobile-40-touch-menu.png"))
    journey.check("08 手機：「分析工具」鍵在圖的上方、觸控範圍至少 44×44；選單十項（含六種）每項至少 44 高、整個在畫面寬度內、不造成橫向捲動",
                  bool(key_hit) and key_hit["width"] >= 44 and key_hit["height"] >= 44 and key_hit["bottom"] <= box["y"]
                  and menu_fits(layout, TOOL_COUNT) and all(f"{tool['kind']}:{tool['name']}:{tool['name']}" in layout["order"] for tool in TOUCH_TOOLS),
                  {"key": key_hit, "menu": layout})
    # 長按選單裡的一項（手指按著不動再放開）：不選取到文字。放開就和點一下一樣選到那一項（名稱本來就寫在項目上，
    # 不像以前的圖示鈕要靠長按看名稱），而且只會是按著的那一項。
    item_box = journey.tool(page, "hline").item.bounding_box()
    finger.send("touchStart", [(item_box["x"] + item_box["width"] / 2, item_box["y"] + item_box["height"] / 2)])
    page.wait_for_timeout(700)
    finger.send("touchEnd", [])
    page.wait_for_timeout(200)
    held = page.evaluate("""() => ({ selected: String(getSelection()), pressed: [...document.querySelectorAll('#drawTools [aria-pressed=true]')].map(b => b.dataset.tool),
      select: getComputedStyle(document.querySelector('#drawTools button[data-tool=hline]')).userSelect })""")
    journey.check("08 手機：長按選單裡的「水平線」不會選取到文字；放開最多只選到按著的那一項", held["selected"] == "" and held["pressed"] in ([], ["hline"]), held)
    if pressed("hline"):
        page.locator("#toolsExit").tap()
    if page.locator("#drawTools").is_visible():
        journey.close_menu(page, touch=True)
    journey.choose_period(page, "60m")
    hidden_height = canvas.bounding_box()["height"]
    journey.choose_period(page, "daily")
    journey.check("08 手機：分析工具不擠壓圖表高度（日 K 與 60 分 K 一樣高）", abs(canvas.bounding_box()["height"] - hidden_height) < 0.5,
                  [canvas.bounding_box()["height"], hidden_height])

    axis = journey.axis(page)
    finger.pinch(*px((0.4, 0.4)), 60, 160)
    zoomed = journey.axis(page)
    finger.slide(*px((0.5, 0.4)), 80, 0)
    journey.check("08 手機：沒選工具時雙指張開是縮放、單指左右拖是平移（時間軸都變了），不會畫出線",
                  zoomed != axis and journey.axis(page) != zoomed and journey.saved(page, code) == [])
    page.reload()
    journey.ready(page, code)

    # 本人 11-24 樣品 A：選單每一項都寫著名稱（不用長按看名稱了）
    names = page.evaluate("kinds => kinds.map(kind => document.querySelector(`#drawTools button[data-tool=${kind}]`).textContent.trim())", [tool["kind"] for tool in TOUCH_TOOLS])
    journey.check("08 手機：六種工具在選單裡都直接寫著名稱", names == [tool["name"] for tool in TOUCH_TOOLS], names)
    journey.check("08 手機：圖表畫布不能被選取（長按不跳選取）", page.evaluate("() => getComputedStyle(document.querySelector('#chartHost canvas')).userSelect") == "none")

    # ---- 畫 ----
    before = {item["id"] for item in journey.saved(page, code)}
    for tool in TOUCH_TOOLS:
        button = journey.tool(page, tool["kind"])
        button.tap()
        page.wait_for_timeout(100)
        if tool["kind"] == "text":
            finger.tap(*px(tool["taps"][0]))
            form = page.locator("#chartHost .kc-note")
            visible = form.is_visible()
            sized = page.evaluate("() => { const i = document.querySelector('.kc-note-input'); return i && [getComputedStyle(i).fontSize, document.querySelector('.kc-note').getBoundingClientRect().height, document.activeElement === i]; }")
            page.screenshot(path=str(OUT / "mobile-41-touch-text-editor.png"))
            with page.expect_response(drawing_request("PUT")) as saved:
                page.keyboard.type(tool["text"])
                page.keyboard.press("Enter")
            journey.check("08 手機：文字標註點一下（手指晃了幾像素）出現輸入框，字級 16px、高 44px、已聚焦；寫完按完成就存",
                          visible and sized == ["16px", 44, True] and saved.value.status == 201, sized)
            continue
        if len(tool["taps"]) == 1:
            with page.expect_response(drawing_request("PUT")) as saved:
                finger.tap(*px(tool["taps"][0]))
            journey.check(f"08 手機：{tool['name']}點一下（手指晃了幾像素）就畫好並儲存", saved.value.status == 201)
            continue
        finger.tap(*px(tool["taps"][0]))
        count = len(journey.saved(page, code))
        x, y = px((0.20, 0.15))
        finger.slide(x, y, 4, 120)   # 上下滑過圖表：不是第二點
        journey.check(f"08 手機：{tool['name']}點了第一下之後手指上下滑過圖表，不會被當成第二點、工具還在",
                      len(journey.saved(page, code)) == count and pressed(tool["kind"]))
        with page.expect_response(drawing_request("PUT")) as saved:
            finger.tap(*px(tool["taps"][1]))
        journey.check(f"08 手機：{tool['name']}點兩下（手指晃了幾像素）就畫好並儲存，工具回一般模式", saved.value.status == 201 and not pressed(tool["kind"]))
    items = {item["kind"]: item for item in journey.saved(page, code) if item["id"] not in before}
    journey.check("08 手機：六種各畫了一條", sorted(items) == sorted(tool["kind"] for tool in TOUCH_TOOLS), sorted(items))
    page.screenshot(path=str(OUT / "mobile-42-touch-six-drawn.png"))

    # ---- 選取、拖整條、拖把手 ----
    for tool in TOUCH_TOOLS:
        drawing = items[tool["kind"]]
        pick = px(tool["pick"]) if tool["kind"] != "text" else journey.at(page, width * tool["pick"][0] + 14, height * tool["pick"][1])
        finger.tap(*pick, wobble=(3, -3))
        journey.check(f"08 手機：手指點{tool['name']}就選到（刪除鈕出現）", delete.is_visible())
        with page.expect_response(drawing_request("PUT")) as moved:
            finger.slide(pick[0], pick[1], 0, MOVE)
        after = next(item for item in journey.saved(page, code) if item["id"] == drawing["id"])
        journey.check(f"08 手機：拖{tool['name']}整個往下：每個錨點都變低，放開存一次", moved.value.status == 200
                      and all(new["price"] < old["price"] for new, old in zip(after["anchors"], drawing["anchors"])),
                      {"before": drawing["anchors"], "after": after["anchors"]})
        drawing = after
        if "grip" in tool:
            index = tool["grip"]
            end = journey.at(page, width * tool["taps"][index][0] + 8, height * tool["taps"][index][1] + MOVE - 8)   # 把手旁 11px
            with page.expect_response(drawing_request("PUT")) as reshaped:
                finger.slide(end[0], end[1], 0, 16)
            after = next(item for item in journey.saved(page, code) if item["id"] == drawing["id"])
            journey.check(f"08 手機：按在{tool['name']}的把手旁邊往下拖：只有那一端變低，另一端不動", reshaped.value.status == 200
                          and after["anchors"][1 - index] == drawing["anchors"][1 - index] and after["anchors"][index]["price"] < drawing["anchors"][index]["price"],
                          {"before": drawing["anchors"], "after": after["anchors"]})
            drawing = after
        if tool["kind"] == "text":
            finger.tap(pick[0], pick[1] + MOVE, wobble=(2, 3))
            opened = page.locator("#chartHost .kc-note").is_visible()
            with page.expect_response(drawing_request("PUT")) as edited:
                page.keyboard.type("改")
                page.keyboard.press("Enter")
            after = next(item for item in journey.saved(page, code) if item["id"] == drawing["id"])
            journey.check("08 手機：選取的文字再點一下打開輸入框（游標在最後面），接著寫的字存上去", opened and edited.value.status == 200 and after["text"] == tool["text"] + "改", after)
            drawing = after
        items[tool["kind"]] = drawing
        finger.tap(*journey.at(page, width * 0.45, height * 0.88), wobble=(0, 0))   # 點空白處取消選取
    page.screenshot(path=str(OUT / "mobile-43-touch-six-edited.png"))

    # ---- 兩點工具畫到一半時縮放與平移 ----
    start = (0.25, 0.47)
    with page.expect_response(drawing_request("PUT")):
        journey.tool(page, "hline").tap()
        finger.tap(*px(start), wobble=(0, 0))
    probe = next(item for item in journey.saved(page, code) if item["id"] not in before and item["id"] not in {d["id"] for d in items.values()})
    finger.tap(*journey.at(page, width * 0.03, height * start[1]), wobble=(0, 0))
    with page.expect_response(drawing_request("DELETE")):
        delete.tap()
    journey.tool(page, "trend").tap()
    finger.tap(*px(start), wobble=(0, 0))
    axis = journey.axis(page)
    count = len(journey.saved(page, code))
    finger.pinch(*px((0.35, 0.3)), 60, 160)
    zoomed = journey.axis(page)
    finger.slide(*px((0.5, 0.3)), 60, 0)
    journey.check("08 手機：趨勢線點了第一下之後，雙指放大、單指平移都照常，而且都不會變成第二點",
                  zoomed != axis and journey.axis(page) != zoomed and len(journey.saved(page, code)) == count and pressed("trend"),
                  {"縮放": zoomed != axis, "平移": journey.axis(page) != zoomed, "沒多存": len(journey.saved(page, code)) == count, "工具還在": pressed("trend")})
    with page.expect_response(drawing_request("PUT")) as saved:
        finger.tap(*px((0.5, 0.55)))
    trend = next(item for item in journey.saved(page, code) if item["id"] not in before and item["id"] not in {d["id"] for d in items.values()})
    journey.check("08 手機：縮放平移完再點一下才畫完，起點仍是原本點的那一天與價位", saved.value.status == 201 and trend["anchors"][0] == probe["anchors"][0],
                  {"first": trend["anchors"][0], "expected": probe["anchors"][0]})
    page.screenshot(path=str(OUT / "mobile-44-touch-pinch-then-second.png"))

    # ---- 重新整理、換到桌機 ----
    phone_items = sorted(journey.saved(page, code), key=lambda item: item["id"])
    page.reload()
    journey.ready(page, code)
    ink = journey.ink(page)
    journey.check("08 手機：重新整理後七條都還在、位置不變", sorted(journey.saved(page, code), key=lambda item: item["id"]) == phone_items
                  and ink["count"] > 0 and len(phone_items) == 7, len(phone_items))
    page.screenshot(path=str(OUT / "mobile-45-touch-reloaded.png"))
    desk = journey.context(browser, OWNER, viewport={"width": 1280, "height": 900})
    desk_page = journey.open(desk, code)
    same = [{key: item[key] for key in ("id", "kind", "anchors", "text")} for item in sorted(journey.saved(desk_page, code), key=lambda item: item["id"])]
    journey.check("08 桌機：同一帳號看到的就是手機畫的那七條（種類、日期、價位、文字都一樣），圖上畫得出來",
                  same == [{key: item[key] for key in ("id", "kind", "anchors", "text")} for item in phone_items] and journey.ink(desk_page)["count"] > 0)
    desk_page.screenshot(path=str(OUT / "desktop-40-touch-same-as-phone.png"))
    desk.close()

    # ---- 刪除 ----
    for tool in TOUCH_TOOLS:
        x, y = tool["pick"][0] * width + (14 if tool["kind"] == "text" else 0), tool["pick"][1] * height + MOVE
        finger.tap(*journey.at(page, x, y), wobble=(2, 2))
        shown = delete.is_visible()
        with page.expect_response(drawing_request("DELETE")) as removed:
            delete.tap()
        journey.check(f"08 手機：選取{tool['name']}後點刪除就刪掉", shown and removed.value.status == 204
                      and all(item["id"] != items[tool["kind"]]["id"] for item in journey.saved(page, code)) and not delete.is_visible())
    page.reload()
    journey.ready(page, code)
    journey.check("08 手機：刪掉的六條重新整理後也不在（只剩縮放時畫的那條趨勢線）", [item["kind"] for item in journey.saved(page, code)] == ["trend"])
    context.close()


def touch_narrow(journey: Journey, browser) -> None:
    print("票 08｜手機寬度 320（iPhone 開「顯示縮放」）：分析工具與選單")
    code = "2882"
    context = journey.context(browser, OWNER, viewport={"width": 320, "height": 640}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = journey.open(context, code)
    host = page.locator("#chartHost")

    # 本人 11-24 樣品 A：沒有一排十顆的工具列了。320 寬要驗的是「分析工具」那一行（鍵、週期，選了線多出刪除）放得下，
    # 浮出的選單整個在畫面寬度內、每項手指點得到。觸控範圍看 .chart-chip::before 補出來的大小（HIT_JS）。
    def row() -> dict:
        shown = [hit for hit in (journey.hit(page, selector) for selector in ("#toolsKey", "#toolsExit", "#drawDelete", "#drawAlert")) if hit]
        return {"fits": page.evaluate(FITS_JS), "right": max(hit["right"] for hit in shown), "hits": shown,
                "chart": {**host.bounding_box(), "top": page.evaluate("() => document.getElementById('chartHost').getBoundingClientRect().top + scrollY")}}

    first = row()
    journey.check("08 320 寬：「分析工具」那一行在圖表寬度內、不造成橫向捲動，鍵的觸控範圍至少 44×44", first["fits"] and first["right"] <= first["chart"]["x"] + first["chart"]["width"] + 4.5
                  and all(hit["width"] >= 44 and hit["height"] >= 44 for hit in first["hits"]), first)
    journey.open_menu(page, touch=True)
    layout = journey.menu(page)
    page.screenshot(path=str(OUT / "mobile-46-touch-320-menu.png"))
    journey.check("08 320 寬：選單十項都在、依序分三組、每項至少 44 高、名稱沒被切掉、整個在 320 寬內、不造成橫向捲動",
                  menu_fits(layout, TOOL_COUNT) and layout["order"] == menu_order(), layout)
    journey.close_menu(page, touch=True)
    line_tool = journey.tool(page, "hline")
    line_tool.tap()
    x, y, line_y = journey.point(page, 0.3, 0.3)
    with page.expect_response(drawing_request("PUT")):
        page.touchscreen.tap(x, y)
    page.touchscreen.tap(*journey.canvas_xy(page, 0.5, line_y))
    page.wait_for_timeout(150)
    second = row()
    journey.check("08 320 寬：選了線多出刪除鈕：那一行仍在圖表寬度內、不橫向捲動、每顆觸控範圍至少 44×44，圖不移動",
                  page.locator("#drawDelete").is_visible() and second["fits"] and len(second["hits"]) == 2
                  and second["right"] <= second["chart"]["x"] + second["chart"]["width"] + 4.5 and all(hit["width"] >= 44 and hit["height"] >= 44 for hit in second["hits"])
                  and abs(second["chart"]["top"] - first["chart"]["top"]) < 0.5 and abs(second["chart"]["height"] - first["chart"]["height"]) < 0.5, second)
    page.screenshot(path=str(OUT / "mobile-46-touch-320-selected.png"))
    for kind in ("trend", "hline", "ray", "rect", "fib", "text"):
        button = journey.tool(page, kind)
        button.tap()
        on = button.get_attribute("aria-pressed") == "true"
        using = row()
        button.tap()
        journey.check(f"08 320 寬：{kind} 點得到（從選單點一下選中、鍵旁的離開鈕點一下放下），選著時那一行仍放得下",
                      on and button.get_attribute("aria-pressed") == "false" and using["fits"] and using["right"] <= 320
                      and all(hit["width"] >= 44 and hit["height"] >= 44 for hit in using["hits"]), using)
    with page.expect_response(drawing_request("DELETE")):
        page.touchscreen.tap(*journey.canvas_xy(page, 0.5, line_y))
        page.locator("#drawDelete").tap()
    context.close()


HOURLY_DAYS = 24  # 種進 D1 的 60 分 K：今天以前最近 24 個平日、每天五根（2330；旅程不連富果）


def hourly_seed_sql() -> str:
    import datetime as dt
    rows, day, price = [], dt.date.today() - dt.timedelta(days=1), 2235.0
    days = []
    while len(days) < HOURLY_DAYS:
        if day.weekday() < 5:
            days.append(day)
        day -= dt.timedelta(days=1)
    for index, d in enumerate(reversed(days)):
        for hour in range(9, 14):
            close = round(price + (5 if (index + hour) % 3 else -5), 2)
            rows.append(f"('2330', '{d.isoformat()}T{hour:02d}:00:00.000+08:00', {price}, {max(price, close) + 5}, {min(price, close) - 5}, {close}, 1000000)")
            price = close
    return ("INSERT INTO stock_hourly_candles (code, t, open, high, low, close, volume) VALUES " + ", ".join(rows) + ";\n"
            + f"INSERT INTO stock_hourly_state (code, through, fetched_at) VALUES ('2330', '{(dt.date.today() - dt.timedelta(days=1)).isoformat()}', 'journey');\n")


# 本人 11-18：60 分 K 也能畫線，各畫各的——60 分畫的線只在 60 分出現（錨點記到那一根開始的時分），日／週／月畫的不在 60 分出現。
def hourly(journey: Journey, browser, touch: bool) -> None:
    name = "手機" if touch else "桌機"
    print(f"60 分畫線（{name}）")
    options = ({"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
               if touch else {"viewport": {"width": 1280, "height": 900}})
    code = "2330"
    context = journey.context(browser, OWNER, **options)
    page = journey.open(context, code)
    before = journey.saved(page, code)
    day_lines = len(journey.lines(page))
    journey.choose_period(page, "60m")
    page.wait_for_function("() => /60分K · \\d+ 根/.test(document.getElementById('stockChartContext').textContent)", timeout=10_000)
    journey.open_menu(page, touch=touch)
    layout = journey.menu(page)
    journey.close_menu(page, touch=touch)
    journey.check(f"60 分（{name}）：「分析工具」出現、選單打得開、十種工具都在而且都能按", page.locator("#toolsKey").is_visible() and bool(layout) and layout["open"]
                  and layout["order"] == menu_order() and all(page.locator(f"#drawTools button[data-tool={kind}]").is_enabled() for kind in ["trend", "hline", "ray", "rect", "fib", "text"]), layout)
    journey.check(f"60 分（{name}）：日 K 畫的線不出現", journey.lines(page) == [], journey.lines(page))
    canvas_y = journey.draw(page, 0.45, 0.4, touch=touch)
    drawn = [item for item in journey.saved(page, code) if item["id"] not in {d["id"] for d in before}]
    journey.check(f"60 分（{name}）：存下來的錨點記到那一根 60 分 K 開始的時分", len(drawn) == 1
                  and len(drawn[0]["anchors"][0]["date"]) == 16 and drawn[0]["anchors"][0]["date"][11:] in {"09:00", "10:00", "11:00", "12:00", "13:00"}, drawn)
    journey.check(f"60 分（{name}）：畫的那條在點的價位", any(abs(line - canvas_y) <= 1.5 for line in journey.lines(page)), journey.lines(page))
    page.locator(".bd-chart").screenshot(path=str(OUT / f"hourly-{'mobile' if touch else 'desktop'}-1-drawn.png"))

    journey.choose_period(page, "daily")
    journey.check(f"60 分（{name}）：切到日 K，60 分畫的不出現、日 K 原本的照舊", len(journey.lines(page)) == day_lines, journey.lines(page))
    journey.choose_period(page, "60m")
    page.wait_for_timeout(400)
    journey.check(f"60 分（{name}）：切回 60 分又出現", any(abs(line - canvas_y) <= 1.5 for line in journey.lines(page)), journey.lines(page))

    page.reload()
    journey.ready(page, code)
    journey.choose_period(page, "60m")
    page.wait_for_function("() => /60分K · \\d+ 根/.test(document.getElementById('stockChartContext').textContent)", timeout=10_000)
    page.wait_for_timeout(500)
    journey.check(f"60 分（{name}）：重新整理後還在", any(abs(line - canvas_y) <= 1.5 for line in journey.lines(page)), journey.lines(page))
    page.locator(".bd-chart").screenshot(path=str(OUT / f"hourly-{'mobile' if touch else 'desktop'}-2-after-reload.png"))
    if touch:
        journey.check("60 分（手機）：分析工具那一行不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
        page.screenshot(path=str(OUT / "hourly-mobile-3-page.png"))

    # 收尾：刪掉這一條（點選、再按刪除），日 K 的線不受影響
    x, y = journey.canvas_xy(page, 0.5, canvas_y)
    if touch:
        page.touchscreen.tap(x, y)
    else:
        page.mouse.click(x, y)
    with page.expect_response(drawing_request("DELETE")):
        (page.locator("#drawDelete").tap() if touch else page.locator("#drawDelete").click())
    journey.check(f"60 分（{name}）：刪得掉，日 K 的線都還在", [d["id"] for d in journey.saved(page, code)] == [d["id"] for d in before])
    context.close()


def hourly_desktop(journey: Journey, browser) -> None:
    hourly(journey, browser, touch=False)


def hourly_mobile(journey: Journey, browser) -> None:
    hourly(journey, browser, touch=True)


# ---------- 本人 2026-11-24：十字線的橫線；量測、平行通道、到價提醒、區間成交量分布 ----------
SHOTS = ROOT / ".scratch/chart-tools"   # 交接用的 390 寬截圖（十字線、四種新工具各一張）
NEW_TOOLS = [("measure", "量測"), ("channel", "平行通道"), ("alert", "到價提醒"), ("vprofile", "區間成交量分布")]
WIDE = {"width": 1440, "height": 900}
CROSS_JS = """() => { const chart = window.StockView.chart(), s = chart.studies;
  return { cross: chart.debugBoxes.cross, picked: chart.debugBoxes.picked, study: s ? s.debugCross : null, pickedStudy: s ? s.debugPicked : [],
    width: chart.canvas.clientWidth, hover: chart.hover, selection: chart.selection }; }"""
# 畫布上某一列左右相鄰像素顏色變了幾次：虛線那一列會比上面幾像素的那一列多很多（實線的格線與 K 棒只會多幾次）。
DASH_ROW_JS = """([selector, y]) => { const canvas = document.querySelector(selector); const scale = canvas.height / canvas.getBoundingClientRect().height;
  const row = at => { const d = canvas.getContext('2d').getImageData(0, Math.floor(at * scale), canvas.width, 1).data; let n = 0;
    for (let i = 4; i < d.length; i += 4) if (Math.abs(d[i] - d[i - 4]) + Math.abs(d[i + 1] - d[i - 3]) + Math.abs(d[i + 2] - d[i - 2]) > 20) n++; return n; };
  return [row(y), row(y - 5)]; }"""
VISIBLE_JS = "() => window.StockView.chart().drawings.visible().map(item => item.drawing.kind)"
# 要選取某一種新工具畫的那一條時點哪裡（畫布座標）：量測與平行通道點線的中點、成交量分布點左邊那條邊線、價位線點線上。
PICK_JS = """kind => { const chart = window.StockView.chart(), found = chart.drawings.visible().find(item => item.drawing.kind === kind);
  if (!found) return null;
  const at = found.at;
  if (kind === 'vprofile') return [at.edges[0][0][0], (at.top + at.bottom) / 2 + 24];
  if (kind === 'alert') return [chart.canvas.clientWidth * 0.4, at.y];
  return [(at.a[0] + at.b[0]) / 2, (at.a[1] + at.b[1]) / 2]; }"""
NUMBER_RE = __import__("re").compile(r"^[\d,]+(\.\d+)?$")


def open_dom(journey: Journey, context, code: str):
    """打開個股頁：不等外部資源（字型一律擋掉），頁面的程式讀完就開始。"""
    page = context.new_page()
    page.route("**/*", lambda route: route.abort() if "fonts.g" in route.request.url else route.fallback())
    page.on("console", lambda message: journey.console.append(message.text)
            if message.type == "error" and "Failed to load resource" not in message.text else None)
    page.on("response", lambda response: journey.failed.append(f"{response.status} {response.request.method} {response.url.replace(journey.base, '')}")
            if response.status >= 400 else None)
    page.on("pageerror", lambda error: journey.console.append(f"pageerror: {error}"))
    page.goto(f"{journey.base}/stock.html?code={code}", wait_until="domcontentloaded")
    journey.ready(page, code)
    return page


def reload_dom(journey: Journey, page, code: str) -> None:
    page.reload(wait_until="domcontentloaded")
    journey.ready(page, code)


def clear_drawings(journey: Journey, page, code: str) -> None:
    """前面的段落可能在這一檔留了線：全部刪掉再開始，選取時才不會點到別條。"""
    page.evaluate("""async code => { const items = (await (await fetch(`/api/drawings/${code}`, { cache: 'no-store' })).json()).items;
      for (const item of items) await fetch(`/api/drawings/${code}/${item.id}`, { method: 'DELETE' }); }""", code)
    reload_dom(journey, page, code)


def mouse_drag(page, start: tuple[float, float], end: tuple[float, float]) -> None:
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(*end, steps=8)
    page.wait_for_timeout(60)
    page.mouse.up()
    page.wait_for_timeout(120)


def cross(journey: Journey, browser, touch: bool) -> None:
    name = "手機 390" if touch else "桌機 1440"
    print(f"十字線的橫線（{name}）")
    code = "2330"
    options = {"viewport": PHONE, "device_scale_factor": 2, "is_mobile": True, "has_touch": True} if touch else {"viewport": WIDE}
    context = journey.context(browser, OWNER, **options)
    page = open_dom(journey, context, code)
    canvas = page.locator("#chartHost canvas")
    state = lambda: page.evaluate(CROSS_JS)   # noqa: E731
    journey.check(f"十字線（{name}）：起點沒有十字線", state()["cross"] is None and state()["picked"] is None)

    # 本人 11-24 更正：水平虛線「直接對準我點到那天的收盤價」——橫線的價位就是那一天「收」的價位，和游標高度無關
    def snapped(now: dict) -> bool:
        line, picked = now["cross"], now["picked"]
        close = next((tag["text"] for tag in (picked or {}).get("tags", []) if tag["name"] == "收"), None)
        return bool(line) and close is not None and line["text"] == close and bool(NUMBER_RE.match(line["text"]))

    if not touch:
        for period, label in [("daily", "日"), ("weekly", "週"), ("monthly", "月")]:
            if period != "daily":
                journey.choose_period(page, period)
            x, y, canvas_y = journey.point(page, 0.4, 0.35)
            page.mouse.move(x, y)
            page.wait_for_timeout(80)
            now = state()
            dashes = page.evaluate(DASH_ROW_JS, ["#chartHost canvas", now["cross"]["y"] if now["cross"] else canvas_y])
            journey.check(f"十字線（{name}・{label}K）：垂直線之外多一條水平虛線，對準那一天的收盤",
                          snapped(now) and dashes[0] - dashes[1] >= 30, {"state": now, "dashes": dashes})
            if period == "daily":
                page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-50-crosshair.png"))
                first_y, first_date = now["cross"]["y"], now["picked"]["date"]
                x2, y2, canvas_y2 = journey.point(page, 0.4, 0.5)
                page.mouse.move(x2, y2)
                page.wait_for_timeout(80)
                moved = state()
                journey.check(f"十字線（{name}）：同一天游標上下移，水平線不跟著動（還是那一天的收盤），開高低收照樣標",
                              snapped(moved) and moved["cross"]["y"] == first_y and moved["picked"]["date"] == first_date
                              and [tag["name"] for tag in moved["picked"]["tags"]] == ["開", "高", "低", "收"], moved)
                # 副圖（成交量）：游標移到副圖裡，副圖也畫一條橫線、對準那一天的量（不標另一個數字）；主圖的橫線照樣在收盤
                study = page.locator("#chartStudies canvas.kc-study-canvas:visible").first
                box = study.bounding_box()
                page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.6)
                page.wait_for_timeout(80)
                sub = state()
                study_dashes = page.evaluate(DASH_ROW_JS, ["#chartStudies canvas.kc-study-canvas:not([hidden])", sub["study"]["y"] if sub["study"] else 0]) \
                    if page.locator("#chartStudies section:not([hidden]) canvas.kc-study-canvas").count() else [0, 0]
                journey.check(f"十字線（{name}）：游標在成交量副圖裡，副圖的橫線對準那一天的量、不跟著游標；主圖的橫線仍在收盤",
                              snapped(sub) and bool(sub["study"]) and sub["study"]["text"] == "" and abs(sub["study"]["y"] - box["height"] * 0.6) > 1.5
                              and len(sub["pickedStudy"]) == 1, {"state": sub, "dashes": study_dashes})
                page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-51-crosshair-study.png"))
        page.mouse.move(5, 5)
        page.wait_for_timeout(80)
        journey.check(f"十字線（{name}）：游標離開圖表就收掉", state()["cross"] is None and state()["picked"] is None)
        # 畫線工具不受影響：選了水平線工具照樣點一下就畫好，游標移動時十字線還在
        journey.choose_period(page, "daily")
        before = {item["id"] for item in journey.saved(page, code)}
        canvas_y = journey.draw(page, 0.5, 0.22)
        drawn = [item for item in journey.saved(page, code) if item["id"] not in before]
        journey.check(f"十字線（{name}）：畫線照常（水平線畫在點的價位）", len(drawn) == 1 and near(journey.lines(page), canvas_y), journey.lines(page))
        page.mouse.click(*journey.canvas_xy(page, 0.5, canvas_y))
        with page.expect_response(drawing_request("DELETE")):
            page.locator("#drawDelete").click()
    else:
        SHOTS.mkdir(parents=True, exist_ok=True)
        finger = Finger(context, page)
        box = canvas.bounding_box()
        x, y = box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.35
        finger.tap(x, y, wobble=(2, 1))
        now = state()
        dashes = page.evaluate(DASH_ROW_JS, ["#chartHost canvas", now["cross"]["y"] if now["cross"] else 0])
        journey.check(f"十字線（{name}）：手指點一下選了那一天，垂直線之外多一條水平虛線對準那一天的收盤",
                      snapped(now) and now["selection"] >= 0 and journey.shows_bar(page) and dashes[0] - dashes[1] >= 30,
                      {"state": now, "dashes": dashes})
        page.locator(".bd-chart").screenshot(path=str(SHOTS / "390-crosshair-k.png"))
        page.screenshot(path=str(OUT / "mobile-50-crosshair.png"))
        # 按住拖：垂直線跟著手指到別天，水平線對準那一天的收盤；放開後回到選的那一天、它的收盤
        finger.send("touchStart", [(x + 40, y + 20)])
        for step in range(1, 5):
            finger.send("touchMove", [(x + 40 + 15 * step, y + 20 + 10 * step)])
            page.wait_for_timeout(16)
        held = state()
        journey.check(f"十字線（{name}）：手指按住拖到別天，水平線對準那一天的收盤（不是手指的高度）", snapped(held) and held["hover"] != held["selection"], held)
        page.wait_for_timeout(250)
        finger.send("touchEnd", [])
        page.wait_for_timeout(150)
        after = state()
        journey.check(f"十字線（{name}）：放開後十字線回到選的那一天，水平線在它的收盤", after["hover"] == after["selection"] >= 0 and snapped(after), after)
        axis = journey.axis(page)
        finger.pinch(x, y, 60, 160)
        journey.check(f"十字線（{name}）：雙指縮放照常", journey.axis(page) != axis)
        journey.check(f"十字線（{name}）：不造成橫向捲動", page.evaluate(FITS_JS))
    context.close()


def cross_desktop(journey: Journey, browser) -> None:
    cross(journey, browser, touch=False)


def cross_mobile(journey: Journey, browser) -> None:
    cross(journey, browser, touch=True)


def new_tools(journey: Journey, browser, touch: bool) -> None:
    name = "手機 390" if touch else "桌機 1440"
    print(f"新的四種分析工具（{name}）：畫、存、重新整理後還在、刪除")
    code = "2882" if touch else "2303"
    options = {"viewport": PHONE, "device_scale_factor": 2, "is_mobile": True, "has_touch": True} if touch else {"viewport": WIDE}
    context = journey.context(browser, OWNER, **options)
    page = open_dom(journey, context, code)
    clear_drawings(journey, page, code)
    finger = Finger(context, page) if touch else None
    delete, canvas = page.locator("#drawDelete"), page.locator("#chartHost canvas")
    if touch:
        SHOTS.mkdir(parents=True, exist_ok=True)

    def xy(fx: float, fy: float) -> tuple[float, float]:
        box = canvas.bounding_box()
        return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy

    def press(kind: str) -> None:
        button = journey.tool(page, kind)
        (button.tap() if touch else button.click())
        page.wait_for_timeout(150)

    def click(point: tuple[float, float]) -> None:
        (finger.tap(*point, wobble=(1, 1)) if touch else page.mouse.click(*point))
        page.wait_for_timeout(100)

    def drag(start: tuple[float, float], end: tuple[float, float]) -> None:
        (finger.slide(start[0], start[1], end[0] - start[0], end[1] - start[1]) if touch else mouse_drag(page, start, end))

    def pressed(kind: str) -> bool:
        return journey.tool(page, kind).get_attribute("aria-pressed") == "true"

    def remove(kind: str, label: str) -> None:
        click(journey.at(page, *page.evaluate(PICK_JS, kind)))
        shown = delete.is_visible()
        box = journey.hit(page, "#drawDelete") or {"right": 9999, "width": 0, "height": 0, "shown": [0, 0]}
        key = journey.hit(page, "#toolsKey")
        with page.expect_response(drawing_request("DELETE")) as removed:
            (delete.tap() if touch else delete.click())
        # 手指：觸控範圍至少 44×44；滑鼠：外觀和板上其他小鍵一樣高（.chart-chip 至少 34）
        big = box["width"] >= 44 and box["height"] >= 44 if touch else min(box["shown"]) >= 34
        journey.check(f"新工具（{name}）：點{label}選得到，刪除鈕出現在「分析工具」那一行（看得到、夠大），按了就刪掉",
                      shown and removed.value.status == 204 and big and bool(key) and abs(key["middle"] - box.get("middle", -99)) < 2
                      and box["right"] <= page.viewport_size["width"] and all(item["kind"] != kind for item in journey.saved(page, code)), box)

    # ---- 選單（本人 11-24 樣品 A：取代原本一排十顆的工具列） ----
    journey.open_menu(page, touch=touch)
    layout = journey.menu(page)
    journey.check(f"新工具（{name}）：選單十種、分三組，量測與區間成交量分布在「區間」、平行通道在「線條」、到價提醒在「標記」，每項寫著名稱、各有可讀名稱",
                  layout["order"] == menu_order() and all(f"{kind}:{label}:{label}" in layout["order"] for kind, label in NEW_TOOLS), layout["order"])
    chart_top = canvas.bounding_box()["y"]
    key_box = journey.hit(page, "#toolsKey")
    if touch:
        journey.check(f"新工具（{name}）：選單每項至少 44 高、名稱沒被切掉、整個在畫面寬度內，整頁不橫向捲動；「分析工具」鍵在圖的上方",
                      menu_fits(layout, TOOL_COUNT) and key_box["bottom"] <= chart_top + 0.5, {"menu": layout, "key": key_box, "chart": chart_top})
        page.locator(".bd-chart").screenshot(path=str(SHOTS / "390-tools-menu.png"))
        journey.close_menu(page, touch=True)
        # 畫面比選單矮（例如手機橫拿、或鍵盤開著）：選單在自己裡面上下捲，手指在選單裡上下滑是捲動、不會選到工具、整頁不動
        page.set_viewport_size({"width": 390, "height": 480})
        page.wait_for_timeout(200)
        journey.open_menu(page, touch=True)
        page.locator("#drawTools").scroll_into_view_if_needed()
        short = journey.menu(page)
        scroll_y = page.evaluate("() => scrollY")
        top_item = page.locator("#drawTools button[data-tool=ray]").bounding_box()
        finger.slide(top_item["x"] + top_item["width"] / 2, top_item["y"] + top_item["height"] / 2, 0, -140)
        page.wait_for_timeout(300)
        scrolled = journey.menu(page)
        moved_page = page.evaluate("() => scrollY") - scroll_y
        page.locator(".bd-chart").screenshot(path=str(SHOTS / "390-tools-menu-short.png"))
        journey.check(f"新工具（{name}）：畫面比選單矮時選單在自己裡面捲（不超出畫面）；手指在選單裡往上滑就捲下去，滑的那一下不會選到工具、整頁不動",
                      short["scrollHeight"] > short["clientHeight"] and short["overflowY"] == "auto" and short["bottom"] <= short["height"] + 0.5
                      and scrolled["open"] and scrolled["scrollTop"] > 30 and page.locator("#drawTools [aria-pressed=true]").count() == 0
                      and moved_page == 0 and scrolled["fits"], {"short": short, "scrolled": scrolled, "page": moved_page})
        journey.close_menu(page, touch=True)
        page.set_viewport_size(PHONE)
        page.wait_for_timeout(200)
    else:
        journey.check(f"新工具（{name}）：選單每項至少 44 高、名稱（含最長的「區間成交量分布」）沒被切掉；桌機放得下、選單不用捲；「分析工具」鍵在圖的上方",
                      menu_fits(layout, TOOL_COUNT) and layout["scrollHeight"] <= layout["clientHeight"] + 1 and key_box["bottom"] <= chart_top + 0.5,
                      {"menu": layout, "key": key_box, "chart": chart_top})
        page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-52-tools-menu.png"))
        journey.close_menu(page)
        page.mouse.move(5, 5)

    # ---- 量測：按住拖過去 ----
    press("measure")
    with page.expect_response(drawing_request("PUT")) as saved:
        drag(xy(0.30, 0.62), xy(0.50, 0.36))
    item = next((one for one in journey.saved(page, code) if one["kind"] == "measure"), None)
    stats = page.evaluate("""() => { const found = window.StockView.chart().drawings.visible().find(item => item.drawing.kind === 'measure');
      return found ? { stats: found.at.stats, lines: window.ChartDrawingMath.measureLines(found.at.stats) } : null; }""")
    lines_ok = bool(stats) and bool(__import__("re").match(r"^\+[\d,.]+  \+\d+\.\d{2}%$", stats["lines"][0])) and bool(__import__("re").match(r"^\d+ 根 · \d+ 天$", stats["lines"][1]))
    journey.check(f"新工具（{name}）：量測——按住拖過去放開就量好並儲存（圖表不平移），工具回一般模式；標出漲跌、漲跌幅、根數、天數",
                  saved.value.status == 201 and bool(item) and len(item["anchors"]) == 2 and item["anchors"][0]["date"] < item["anchors"][1]["date"]
                  and item["anchors"][1]["price"] > item["anchors"][0]["price"] and not pressed("measure") and lines_ok
                  and stats["stats"]["bars"] >= 2 and stats["stats"]["days"] >= stats["stats"]["bars"] and stats["stats"]["change"] > 0, {"item": item, "stats": stats})
    page.locator(".bd-chart").screenshot(path=str((SHOTS / "390-measure.png") if touch else (OUT / "desktop-53-measure.png")))
    reload_dom(journey, page, code)
    journey.check(f"新工具（{name}）：量測重新整理後還在", page.evaluate(VISIBLE_JS) == ["measure"] and journey.saved(page, code) == [item], page.evaluate(VISIBLE_JS))
    remove("measure", "量測的對角線")

    # ---- 平行通道：點三下 ----
    press("channel")
    click(xy(0.12, 0.70))
    click(xy(0.32, 0.55))
    journey.check(f"新工具（{name}）：平行通道點了兩下還沒畫完（還要一下定寬度），工具還在", journey.saved(page, code) == [] and pressed("channel"))
    with page.expect_response(drawing_request("PUT")) as saved:
        click(xy(0.22, 0.82))
    item = next((one for one in journey.saved(page, code) if one["kind"] == "channel"), None)
    ink = journey.ink(page)
    journey.check(f"新工具（{name}）：平行通道第三下畫好並儲存：三個錨點，第三個記在第一個那一天、價位比趨勢線低（平行線在下面）",
                  saved.value.status == 201 and bool(item) and len(item["anchors"]) == 3 and item["anchors"][2]["date"] == item["anchors"][0]["date"]
                  and item["anchors"][2]["price"] < item["anchors"][0]["price"] and item["anchors"][1]["price"] > item["anchors"][0]["price"]
                  and not pressed("channel") and ink["count"] > 0, {"item": item, "ink": ink})
    page.locator(".bd-chart").screenshot(path=str((SHOTS / "390-channel.png") if touch else (OUT / "desktop-54-channel.png")))
    reload_dom(journey, page, code)
    journey.check(f"新工具（{name}）：平行通道重新整理後還在", page.evaluate(VISIBLE_JS) == ["channel"] and journey.saved(page, code) == [item], page.evaluate(VISIBLE_JS))
    remove("channel", "通道的趨勢線")

    # ---- 區間成交量分布：按住拖過一段日期 ----
    press("vprofile")
    with page.expect_response(drawing_request("PUT")) as saved:
        drag(xy(0.50, 0.45), xy(0.80, 0.60))
    item = next((one for one in journey.saved(page, code) if one["kind"] == "vprofile"), None)
    profile = page.evaluate("""() => { const found = window.StockView.chart().drawings.visible().find(item => item.drawing.kind === 'vprofile');
      return found ? { rows: found.at.rows.length, poc: found.at.poc, strong: found.at.rows.filter(row => row.alpha > .4).length,
        widest: Math.max(...found.at.rows.map(row => row.rect[2])), left: found.at.left, right: found.at.right,
        pick: [found.at.edges[0][0][0], (found.at.top + found.at.bottom) / 2 + 24] } : null; }""")
    journey.check(f"新工具（{name}）：區間成交量分布——拖過一段日期就畫出各價位的量並儲存；量最大的一格（POC）另外標示",
                  saved.value.status == 201 and bool(item) and len(item["anchors"]) == 2 and item["anchors"][0]["date"] < item["anchors"][1]["date"]
                  and bool(profile) and 2 <= profile["rows"] <= 24 and profile["strong"] == 1 and bool(profile["poc"])
                  and profile["widest"] >= 56 and not pressed("vprofile") and journey.ink(page)["count"] > 0, {"item": item, "profile": profile})
    page.locator(".bd-chart").screenshot(path=str((SHOTS / "390-vprofile.png") if touch else (OUT / "desktop-55-vprofile.png")))
    reload_dom(journey, page, code)
    journey.check(f"新工具（{name}）：區間成交量分布重新整理後還在", page.evaluate(VISIBLE_JS) == ["vprofile"] and journey.saved(page, code) == [item], page.evaluate(VISIBLE_JS))
    remove("vprofile", "分布的邊線")

    # ---- 到價提醒的價位線：這個預覽沒有開關注（和其他核准使用者一樣）——線畫得出來、提醒是關的，工具列那一行說明 ----
    press("alert")
    with page.expect_response(drawing_request("PUT")) as saved:
        click(xy(0.60, 0.20))
    item = next((one for one in journey.saved(page, code) if one["kind"] == "alert"), None)
    note = page.locator("#drawNote")
    journey.check(f"新工具（{name}）：到價提醒——這裡不能提醒時線照樣畫好並儲存、提醒是關的，工具列那一行寫「無法到價提醒」，沒有提醒的開關",
                  saved.value.status == 201 and bool(item) and item["alert"] is False and note.is_visible() and note.inner_text().strip() == "無法到價提醒"
                  and not page.locator("#drawAlert").is_visible() and page.evaluate(FITS_JS), item)
    reload_dom(journey, page, code)
    journey.check(f"新工具（{name}）：到價提醒的線重新整理後還在；沒選它的時候那一行不寫字", page.evaluate(VISIBLE_JS) == ["alert"] and not note.is_visible())
    remove("alert", "價位線")
    journey.check(f"新工具（{name}）：四種都刪掉了", journey.saved(page, code) == [] and not note.is_visible())
    context.close()


def new_tools_desktop(journey: Journey, browser) -> None:
    new_tools(journey, browser, touch=False)


def new_tools_mobile(journey: Journey, browser) -> None:
    new_tools(journey, browser, touch=True)


# ---------- 本人 2026-11-24「大盤也要分析工具」：首頁的 K 線 ----------
HOME_CODE = "IDXTWII"   # 台股加權的畫線存在這個代號底下（minimal.js 的 drawingCode：IDX＋指數鍵大寫）
HOME_CANVAS = "#mainChart canvas"
HOME_STATE_JS = """() => { const chart = window.HomeView.chart();
  return { selection: chart.selection, details: !document.getElementById('mobileCandleDetails').hidden,
    panel: document.body.classList.contains('panel-open'), kinds: chart.drawings.visible().map(item => item.drawing.kind) }; }"""


def live_closed_payload() -> str:
    """/api/live 的一份「今天已收盤」行情（旅程伺服器沒有即時服務，回 503 時首頁的分時是停用的）：
    帶今天 09:00 起的幾分鐘走勢，首頁才有分時可以切，驗「分時不出現分析工具」。只回給首頁這一段。"""
    import datetime as dt
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    day = now.date().isoformat()
    series = [{"timestamp": f"{day}T09:{minute:02d}:00+08:00", "price": 23000 + minute * 3, "high": 23002 + minute * 3,
               "low": 22998 + minute * 3, "volume_raw": 1000} for minute in range(30)]
    stamp = now.isoformat()
    return json.dumps({"ok": True, "live": False, "market_open": False, "phase": "closed", "fetched_at": stamp, "data_time": stamp,
                       "data": {"price": 23087, "change": 87, "change_percent": 0.38, "previous_close": 23000, "open": 23000,
                                "high": 23089, "low": 22998}, "series": series})


def home_chart(journey: Journey, browser, touch: bool) -> None:
    name = "手機 390" if touch else "桌機 1440"
    print(f"大盤 K 線的分析工具（{name}）：畫、存、重新整理後還在、選線不選 K 棒、刪除、分時不出現")
    options = {"viewport": PHONE, "device_scale_factor": 2, "is_mobile": True, "has_touch": True} if touch else {"viewport": WIDE}
    context = journey.context(browser, OWNER, **options)
    payload = live_closed_payload()
    context.route("**/api/live", lambda route: route.fulfill(status=200, content_type="application/json", body=payload))
    page = context.new_page()
    journey.watch(page)
    key, menu, delete = page.locator("#toolsKey"), page.locator("#drawTools"), page.locator("#drawDelete")
    canvas = page.locator(HOME_CANVAS)
    drawing = lambda method: (lambda response: f"/api/drawings/{HOME_CODE}/" in response.url and response.request.method == method)   # noqa: E731
    state = lambda: page.evaluate(HOME_STATE_JS)   # noqa: E731
    lines = lambda: page.evaluate(LINES_JS, {"selector": HOME_CANVAS})   # noqa: E731

    def ready() -> None:
        key.wait_for(state="visible", timeout=60_000)
        page.wait_for_function("() => { const chart = window.HomeView && window.HomeView.chart(); return Boolean(chart && chart.candles.length); }", timeout=60_000)
        canvas.scroll_into_view_if_needed()
        page.wait_for_timeout(700)   # 畫線在 K 線畫出來之後才讀

    def wipe() -> None:
        page.evaluate("""async code => { const items = (await (await fetch(`/api/drawings/${code}`, { cache: 'no-store' })).json()).items || [];
          for (const item of items) await fetch(`/api/drawings/${code}/${item.id}`, { method: 'DELETE' }); }""", HOME_CODE)

    def at(fx: float, fy: float) -> tuple[float, float, float]:
        box = canvas.bounding_box()
        return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy, box["height"] * fy

    def tap(x: float, y: float) -> None:
        (page.touchscreen.tap(x, y) if touch else page.mouse.click(x, y))
        page.wait_for_timeout(150)

    def set_period(value: str) -> None:
        page.evaluate("value => { const select = document.getElementById('periodSelect'); select.value = value; select.dispatchEvent(new Event('change')); }", value)
        page.wait_for_timeout(400)

    page.goto(f"{journey.base}/")
    ready()
    wipe()   # 前一次沒跑完留下的
    page.reload()
    ready()
    try:
        placed = page.evaluate("""() => { const ui = document.querySelector('.chart-controls .dt-ui'), period = document.querySelector('.chart-controls .period-control');
          return Boolean(ui && period && ui.contains(document.getElementById('toolsKey')) && (ui.compareDocumentPosition(period) & Node.DOCUMENT_POSITION_FOLLOWING)); }""")
        key_hit = journey.hit(page, "#toolsKey")
        journey.check(f"大盤（{name}）：K 線上方、週期鍵左邊有「分析工具」鍵（觸控範圍至少 44×44），選單收著", placed and key.inner_text().strip() == "分析工具"
                      and key_hit["bottom"] <= canvas.bounding_box()["y"] + 0.5 and key_hit["width"] >= 44 and key_hit["height"] >= 44 and menu.is_hidden(), key_hit)
        journey.check(f"大盤（{name}）：起點這個指數沒有畫線", journey.saved(page, HOME_CODE) == [] and state()["kinds"] == [])

        journey.open_menu(page, touch=touch)
        layout = journey.menu(page)
        journey.check(f"大盤（{name}）：選單九種（沒有到價提醒）、分三組、每項寫著名稱，每項至少 44 高、整個在畫面寬度內、不造成橫向捲動",
                      layout["order"] == menu_order(skip=("alert",)) and menu_fits(layout, TOOL_COUNT - 1)
                      and page.locator("#drawTools button[data-tool=alert]").count() == 0, layout)
        page.screenshot(path=str(OUT / f"home-{'mobile' if touch else 'desktop'}-1-menu.png"))
        hline = journey.tool(page, "hline")
        (hline.tap() if touch else hline.click())
        menu.wait_for(state="hidden", timeout=2_000)   # 首頁的選單收起有 0.2 秒的退場（web/desk-motion.css 第 3 段），等它退完再看
        journey.check(f"大盤（{name}）：選水平線：選單收起，鍵上寫「水平線」、旁邊出現離開鈕", menu.is_hidden() and hline.get_attribute("aria-pressed") == "true"
                      and key.inner_text().strip() == "水平線" and page.locator("#toolsExit").is_visible())

        before = lines()
        x, y, line_y = at(0.5, 0.3)
        with page.expect_response(drawing("PUT")) as saved:
            tap(x, y)
        items = journey.saved(page, HOME_CODE)
        now = state()
        journey.check(f"大盤（{name}）：點圖就畫好水平線並存到 {HOME_CODE}（PUT 回 201），工具回一般模式，那一下沒有選到 K 棒",
                      saved.value.status == 201 and len(items) == 1 and items[0]["kind"] == "hline" and now["kinds"] == ["hline"]
                      and near(lines(), line_y) and not near(before, line_y) and hline.get_attribute("aria-pressed") == "false"
                      and now["selection"] == -1 and not now["details"] and not now["panel"], {"items": items, "state": now, "lines": lines(), "want": line_y})
        page.screenshot(path=str(OUT / f"home-{'mobile' if touch else 'desktop'}-2-drawn.png"))

        page.reload()
        ready()
        again = journey.saved(page, HOME_CODE)
        journey.check(f"大盤（{name}）：重新整理後線還在（讀 {HOME_CODE} 是同一條）", bool(items) and [item["id"] for item in again] == [items[0]["id"]]
                      and state()["kinds"] == ["hline"] and near(lines(), line_y), {"saved": again, "lines": lines(), "want": line_y})

        x, y, _ = at(0.3, 0)
        tap(x, y + line_y)
        now = state()
        journey.check(f"大盤（{name}）：點線只選線（出現刪除鈕），沒有同時選到 K 棒（{'沒有出現「詳情」鍵' if touch else '沒有打開明細板'}）",
                      delete.is_visible() and now["selection"] == -1 and not now["details"] and not now["panel"], now)
        page.screenshot(path=str(OUT / f"home-{'mobile' if touch else 'desktop'}-3-selected.png"))
        with page.expect_response(drawing("DELETE")) as removed:
            (delete.tap() if touch else delete.click())
        page.wait_for_timeout(150)
        journey.check(f"大盤（{name}）：按刪除：線消失、雲端也刪了（DELETE 回 204）、刪除鈕收起", removed.value.status == 204
                      and journey.saved(page, HOME_CODE) == [] and state()["kinds"] == [] and not near(lines(), line_y) and not delete.is_visible())

        available = page.evaluate("() => { const option = [...document.getElementById('periodSelect').options].find(one => one.value === 'ts'); return Boolean(option && !option.disabled); }")
        set_period("ts")
        if page.evaluate("() => document.getElementById('periodSelect').value") == "ts":
            journey.check(f"大盤（{name}）：分時：「分析工具」整組不出現", not key.is_visible() and not page.locator(".dt-ui").is_visible() and page.evaluate(FITS_JS))
            set_period("daily")
            journey.check(f"大盤（{name}）：切回日 K：「分析工具」回來", key.is_visible())
        else:
            print(f"  （大盤（{name}）：這個預覽沒有分時資料（分時選項{'可選' if available else '停用'}，選了退回日 K），分時不出現分析工具這一項沒驗）")
        journey.check(f"大盤（{name}）：整頁不橫向捲動", page.evaluate(FITS_JS), page.evaluate("() => [document.documentElement.scrollWidth, innerWidth]"))
    finally:
        wipe()   # 不留線：下一次跑起點一樣
        context.close()


def home_desktop(journey: Journey, browser) -> None:
    home_chart(journey, browser, touch=False)


def home_mobile(journey: Journey, browser) -> None:
    home_chart(journey, browser, touch=True)


def start_server(extra: dict[str, str]) -> tuple[subprocess.Popen, str]:
    """另外起一個旅程伺服器（例如打開關注開關的）。回傳行程與網址。"""
    port = free_port()
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud",
                              env={**__import__("os").environ, **extra})
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urlopen(base + "/", timeout=1)
        except OSError as error:
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
        else:
            break
    return server, base


# 到價提醒開著的那一面：站主、關注開關開著（另起一個伺服器）。比價與推播在排程（cloud/test/drawing-alerts.test.js），這裡驗線上的開關與存檔。
def new_alert(journey: Journey, browser) -> None:
    print("到價提醒（站主、關注開關開著；手機 390）：預設開著、開關、重新整理後還在")
    server, base = start_server({"MARKET_ANA_WATCHLIST_WATCH": "owner"})
    side = Journey(base)
    side.checks, side.console, side.failed = journey.checks, journey.console, []
    code = "2330"
    try:
        context = side.context(browser, OWNER, viewport=PHONE, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = open_dom(side, context, code)
        finger = Finger(context, page)
        delete, toggle, note = page.locator("#drawDelete"), page.locator("#drawAlert"), page.locator("#drawNote")
        canvas = page.locator("#chartHost canvas")

        def point(fx: float, fy: float) -> tuple[float, float]:
            # 每次重量：從選單點工具時頁面可能為了讓那一項露出來而捲動
            box = canvas.bounding_box()
            return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy

        saved_alert = lambda: next((one for one in side.saved(page, code) if one["kind"] == "alert"), None)   # noqa: E731

        side.tool(page, "alert").tap()
        page.wait_for_timeout(150)
        with page.expect_response(drawing_request("PUT")) as saved:
            finger.tap(*point(0.55, 0.18), wobble=(1, 1))
        sent = saved.value.request.post_data_json
        item = saved_alert()
        journey.check("到價提醒：站主畫的價位線提醒預設開著；存的時候帶現價與股名（伺服器用來判斷方向與寫通知標題）",
                      saved.value.status == 201 and bool(item) and item["alert"] is True and item["fired_at"] is None and sent.get("alert") is True
                      and isinstance(sent.get("ref"), (int, float)) and sent["ref"] > 0 and sent.get("name") == page.locator("#stockName").inner_text().strip()
                      and not note.is_visible(), {"sent": sent, "item": item})
        finger.tap(*point(0.30, 0.18), wobble=(1, 1))
        page.wait_for_timeout(200)
        sizes = [side.hit(page, "#drawAlert"), side.hit(page, "#drawDelete")] if toggle.is_visible() and delete.is_visible() else []
        key = side.hit(page, "#toolsKey")
        journey.check("到價提醒：選了那條線，「分析工具」那一行出現提醒的開關（開著，可讀名稱「到價提醒」）與刪除，都看得到、觸控範圍至少 44×44，整頁不橫向捲動",
                      len(sizes) == 2 and toggle.get_attribute("aria-pressed") == "true" and toggle.get_attribute("aria-label") == "到價提醒"
                      and all(s["width"] >= 44 and s["height"] >= 44 and s["right"] <= 390 and abs(s["middle"] - key["middle"]) < 2 for s in sizes)
                      and page.evaluate(FITS_JS), {"buttons": sizes, "key": key})
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.locator(".bd-chart").screenshot(path=str(SHOTS / "390-alert.png"))
        with page.expect_response(drawing_request("PUT")) as saved:
            toggle.tap()
        journey.check("到價提醒：按開關關掉——線還在、存成關的", saved.value.status == 200 and saved_alert()["alert"] is False
                      and toggle.get_attribute("aria-pressed") == "false" and page.evaluate(VISIBLE_JS).count("alert") == 1)
        with page.expect_response(drawing_request("PUT")) as saved:
            toggle.tap()
        journey.check("到價提醒：再按一下又打開", saved.value.status == 200 and saved_alert()["alert"] is True and toggle.get_attribute("aria-pressed") == "true")
        reload_dom(side, page, code)
        journey.check("到價提醒：重新整理後線與開著的提醒都還在", saved_alert() == {**item, "updated_at": saved_alert()["updated_at"]} and page.evaluate(VISIBLE_JS).count("alert") == 1)
        finger.tap(*point(0.30, 0.18), wobble=(1, 1))
        page.wait_for_timeout(200)
        shown = toggle.is_visible() and toggle.get_attribute("aria-pressed") == "true"
        with page.expect_response(drawing_request("DELETE")) as removed:
            delete.tap()
        journey.check("到價提醒：重新整理後選取仍顯示開著；刪得掉", shown and removed.value.status == 204 and saved_alert() is None and not toggle.is_visible())
        # 一般水平線沒有提醒的開關
        side.tool(page, "hline").tap()
        with page.expect_response(drawing_request("PUT")):
            finger.tap(*point(0.55, 0.30), wobble=(1, 1))
        finger.tap(*point(0.30, 0.30), wobble=(1, 1))
        page.wait_for_timeout(200)
        journey.check("到價提醒：一般的水平線選了只有刪除，沒有提醒的開關", delete.is_visible() and not toggle.is_visible())
        with page.expect_response(drawing_request("DELETE")):
            delete.tap()
        context.close()
        journey.failed.extend(line for line in side.failed if "/api/drawings" in line)
    finally:
        server.terminate()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="已經開著的旅程伺服器；省略時自己起一個")
    parser.add_argument("--only", help="只跑名稱以這幾個開頭的段落（逗號分開，例如 text 或 desktop,mobile），開發時用；省略時全部跑")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server = None
    base = args.base
    if not base:
        port = free_port()
        seed = OUT / "hourly-seed.sql"
        seed.write_text(hourly_seed_sql(), encoding="utf-8")
        server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud",
                                  env={**__import__("os").environ, "JOURNEY_SEED_SQL": str(seed)})
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urlopen(base + "/", timeout=1)
            except OSError as error:
                if getattr(error, "code", None):  # 伺服器已回應（未登入是 401）
                    break
                time.sleep(0.2)
            else:
                break
    journey = Journey(base.rstrip("/"))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for step in (desktop, mobile, edit_desktop, edit_mobile, two_point_desktop, two_point_mobile, rect_desktop, rect_mobile, fib_desktop, fib_mobile, text_desktop, text_mobile, save_desktop, save_mobile, touch_mobile, touch_narrow, hourly_desktop, hourly_mobile,
                         cross_desktop, cross_mobile, new_tools_desktop, new_tools_mobile, home_desktop, home_mobile, new_alert):
                if not args.only or step.__name__.startswith(tuple(args.only.split(","))):
                    step(journey, browser)
            browser.close()
    finally:
        if server:
            server.terminate()
    journey.check("頁面程式沒有丟出錯誤", not journey.console, journey.console[:5])
    drawing_failures = [line for line in journey.failed if "/api/drawings" in line]
    journey.check("畫線的讀寫請求沒有失敗", not drawing_failures, drawing_failures)
    others = sorted(set(line for line in journey.failed if "/api/drawings" not in line))
    if others:
        print("  （與畫線無關、這個預覽環境沒有的資料）" + "；".join(others))
    failed = [check for check in journey.checks if not check["ok"]]
    (OUT / "report.json").write_text(json.dumps({"base": journey.base, "checks": journey.checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(journey.checks) - len(failed)}/{len(journey.checks)} 項通過；截圖與報告在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
