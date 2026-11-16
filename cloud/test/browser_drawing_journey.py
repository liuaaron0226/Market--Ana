"""畫線工具的本機瀏覽器旅程（桌機與手機寬度各走一遍）。
票 01：水平線畫得出來，而且存得住。票 02：點線選取、拖動、刪除（工具列與 Delete）、Esc。
票 03：趨勢線與射線——點兩下成線、預覽線、取消、拖整條、拖端點、切日週月、重新整理後仍在。
票 04：矩形——點兩下對角、預覽、點邊線選取、拖整個、拖角落、切日週月、重新整理後仍在。
票 05：黃金分割——點兩下出七條線、預覽、比例的位置、選取、拖整組、拖一端、切日週月、重新整理後仍在。

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
  const { from = 0, share = 0.3 } = options || {};
  const canvas = document.querySelector('#chartHost canvas');
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

    def open(self, context, code: str = "2330"):
        page = context.new_page()
        # 「Failed to load resource」另由下面的回應狀態記錄（帶網址），這裡只收程式本身的錯誤訊息。
        page.on("console", lambda message: self.console.append(message.text)
                if message.type == "error" and "Failed to load resource" not in message.text else None)
        page.on("response", lambda response: self.failed.append(f"{response.status} {response.request.method} {response.url.replace(self.base, '')}")
                if response.status >= 400 else None)
        page.on("pageerror", lambda error: self.console.append(f"pageerror: {error}"))
        page.goto(f"{self.base}/stock.html?code={code}")
        self.ready(page, code)
        return page

    def ready(self, page, code: str) -> None:
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
        page.wait_for_function("code => document.getElementById('stockCode').textContent === code", arg=code, timeout=60_000)
        page.locator("#chartHost canvas").scroll_into_view_if_needed()
        page.wait_for_timeout(700)  # 畫線與歷史在分析畫出來之後才讀

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
        tool = page.locator("#drawTools button[data-tool=hline]")
        x, y, canvas_y = self.point(page, fx, fy)
        with page.expect_response(lambda response: "/api/drawings/" in response.url and response.request.method == "PUT") as saved:
            if touch:
                tool.tap()
                page.touchscreen.tap(x, y)
            else:
                tool.click()
                page.mouse.click(x, y)
        self.check("畫完自動儲存（PUT 回 201）", saved.value.status == 201, saved.value.status)
        return canvas_y


def desktop(journey: Journey, browser) -> None:
    print("桌機寬度 1280")
    viewport = {"width": 1280, "height": 900}
    context = journey.context(browser, OWNER, viewport=viewport)
    page = journey.open(context)
    tools = page.locator("#drawTools")
    tool = tools.locator("button[data-tool=hline]")

    journey.check("日 K 上方有工具列", tools.is_visible() and tools.bounding_box()["y"] < page.locator("#chartHost").bounding_box()["y"])
    journey.check("只有圖示沒有文字，按鈕有可讀名稱", tool.inner_text().strip() == "" and tool.get_attribute("aria-label") == "水平線")
    tool.hover()
    page.wait_for_timeout(700)
    journey.check("滑鼠停留顯示「水平線」", page.evaluate(
        "() => { const style = getComputedStyle(document.querySelector('#drawTools button[data-tool=hline]'), '::after'); return style.content + '|' + style.opacity; }") == '"水平線"|1')
    page.mouse.move(5, 5)
    tool.focus()
    page.keyboard.press("Enter")
    journey.check("鍵盤可選工具，工具列看得出已選中", tool.get_attribute("aria-pressed") == "true")
    page.keyboard.press("Space")
    journey.check("再按一次放下工具", tool.get_attribute("aria-pressed") == "false")

    journey.check("一開始沒有畫線", journey.lines(page) == [] and journey.saved(page) == [])
    x, y, _ = journey.point(page, 0.4, 0.5)
    page.mouse.click(x, y)
    journey.check("沒選工具時點一下照舊顯示該根 K 棒的數值", page.locator("#chartHost .kc-tip.show").count() == 1 and journey.lines(page) == [])

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
    journey.check("週 K：工具列在、線在", tools.is_visible() and len(journey.lines(page)) == 1, journey.lines(page))
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-04-weekly.png"))
    journey.choose_period(page, "monthly")
    journey.check("月 K：工具列在、線在", tools.is_visible() and len(journey.lines(page)) == 1, journey.lines(page))
    for period, name in [("5m", "分 K"), ("5s", "秒 K")]:
        journey.choose_period(page, period)
        journey.check(f"{name}：工具列與畫線都不顯示", not tools.is_visible() and journey.lines(page) == [], journey.lines(page))
    journey.choose_period(page, "daily")
    journey.check("切回日 K：工具列與線都恢復", tools.is_visible() and len(journey.lines(page)) == 1, journey.lines(page))

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
    tools = page.locator("#drawTools")
    tool = tools.locator("button[data-tool=hline]")
    before = len(journey.lines(page))
    journey.check("手機：桌機畫的線看得到", before == 1, before)
    journey.check("手機：工具列不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"),
                  page.evaluate("() => [document.documentElement.scrollWidth, innerWidth]"))
    box = tool.bounding_box()
    journey.check("手機：按鈕夠大（至少 44px）", box["width"] >= 44 and box["height"] >= 44, box)

    tool.dispatch_event("pointerdown", {"pointerType": "touch", "pointerId": 7, "bubbles": True})
    page.wait_for_timeout(650)
    shown = page.evaluate("() => getComputedStyle(document.querySelector('#drawTools button[data-tool=hline]'), '::after').opacity")
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-01-longpress.png"))
    tool.dispatch_event("pointerup", {"pointerType": "touch", "pointerId": 7, "bubbles": True})
    tool.dispatch_event("click", {"bubbles": True})
    journey.check("手機：長按顯示「水平線」，放開後不會誤選工具", shown == "1" and tool.get_attribute("aria-pressed") == "false", shown)

    canvas_y = journey.draw(page, 0.45, 0.62, touch=True)
    lines = journey.lines(page)
    journey.check("手機：點工具再點圖，該價位出現水平線", len(lines) == before + 1 and any(abs(line - canvas_y) <= 1.5 for line in lines),
                  {"lines": lines, "tapped": canvas_y})
    journey.check("手機：工具自動回到一般模式", tool.get_attribute("aria-pressed") == "false")
    page.screenshot(path=str(OUT / "mobile-02-drawn.png"))
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-03-chart.png"))

    journey.choose_period(page, "5m")
    journey.check("手機：分 K 不顯示工具列與畫線", not tools.is_visible() and journey.lines(page) == [])
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
    tools = page.locator("#drawTools")
    tool = tools.locator("button[data-tool=hline]")
    delete = page.locator("#drawDelete")

    lines = journey.lines(page)
    before = journey.saved(page, "2317")
    journey.check("起點：這一檔有一條存好的線", len(lines) == 1 and len(before) == 1, lines)
    line_y, line_id, price = lines[0], before[0]["id"], before[0]["anchors"][0]["price"]
    journey.check("沒選取時：工具列沒有刪除、線上沒有把手", not delete.is_visible() and not journey.handle(page, line_y))

    x, y = journey.canvas_xy(page, 0.4, line_y + 5)
    page.mouse.click(x, y)
    journey.check("點在線旁 5px 內就選取：工具列出現刪除、線上出現把手", delete.is_visible() and journey.handle(page, line_y))
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
    delete.hover()
    page.wait_for_timeout(700)
    journey.check("滑鼠停在刪除鈕上顯示「刪除」", page.evaluate(
        "() => { const style = getComputedStyle(document.getElementById('drawDelete'), '::after'); return style.content + '|' + style.opacity; }") == '"刪除"|1')
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-07-delete-button.png"))
    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.click()
    journey.check("按工具列的刪除：線消失、雲端也刪了、刪除鈕收起", removed.value.status == 204 and journey.lines(page) == []
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
    journey.choose_period(page, "5m")
    journey.check("切到分 K：工具列與刪除都收起", not tools.is_visible() and not delete.is_visible())
    page.keyboard.press("Delete")
    journey.choose_period(page, "daily")
    journey.check("切回日 K：線還在、沒有被分 K 上的 Delete 刪掉、不會自己恢復選取", len(journey.lines(page)) == 1
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
    journey.check("手機：手指點在線旁 11px 就選得到（比滑鼠寬），工具列出現刪除", delete.is_visible() and journey.handle(page, line_y))
    box = delete.bounding_box()
    journey.check("手機：刪除鈕夠大（至少 44px）、不造成橫向捲動", box["width"] >= 44 and box["height"] >= 44
                  and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), box)
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

    delete.dispatch_event("pointerdown", {"pointerType": "touch", "pointerId": 9, "bubbles": True})
    page.wait_for_timeout(650)
    shown = page.evaluate("() => getComputedStyle(document.getElementById('drawDelete'), '::after').opacity")
    delete.dispatch_event("pointerup", {"pointerType": "touch", "pointerId": 9, "bubbles": True})
    delete.dispatch_event("click", {"bubbles": True})
    journey.check("手機：長按刪除鈕只顯示「刪除」，放開不會真的刪", shown == "1" and len(journey.lines(page)) == 2 and delete.is_visible(), shown)

    with page.expect_response(drawing_request("DELETE")) as removed:
        delete.tap()
    journey.check("手機：點刪除，線消失、刪除鈕收起", removed.value.status == 204 and len(journey.lines(page)) == 1 and not delete.is_visible())
    x, y = journey.canvas_xy(page, 0.6, max(journey.lines(page)) + 60)
    page.touchscreen.tap(x, y)
    journey.check("手機：點空白處照舊顯示 K 棒數值，不出現刪除", page.locator("#chartHost .kc-tip.show").count() == 1 and not delete.is_visible())
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
    tools = page.locator("#drawTools")
    trend, ray = tools.locator("button[data-tool=trend]"), tools.locator("button[data-tool=ray]")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    a, b = (round(width * 0.35), round(height * 0.62)), (round(width * 0.6), round(height * 0.3))  # 畫布座標
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    order = page.evaluate("() => [...document.querySelectorAll('#drawTools button[data-tool]')].map(button => button.dataset.tool + ':' + button.getAttribute('aria-label') + ':' + button.textContent.trim())")
    journey.check("工具列依序是趨勢線、水平線、射線、矩形、黃金分割，只有圖示、各有可讀名稱", order == ["trend:趨勢線:", "hline:水平線:", "ray:射線:", "rect:矩形:", "fib:黃金分割:"], order)
    for button, name in [(trend, "趨勢線"), (ray, "射線")]:
        button.hover()
        page.wait_for_timeout(700)
        tip = button.evaluate("button => { const style = getComputedStyle(button, '::after'); return style.content + '|' + style.opacity; }")
        journey.check(f"滑鼠停留顯示「{name}」", tip == f'"{name}"|1', tip)
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
    journey.check("取消後點圖照舊顯示 K 棒數值，沒有畫出線", page.locator("#chartHost .kc-tip.show").count() == 1 and journey.saved(page, code) == [])
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
    journey.check("點在線身旁 4px：選取，工具列出現刪除", delete.is_visible())
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
    journey.choose_period(page, "5m")
    journey.check("分 K：工具列與線都不顯示", not tools.is_visible() and journey.ink(page)["count"] == 0)
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
    axis_width = 60  # 右側價格軸至少這麼寬（桌機 78 起）
    journey.check("射線：點兩下成線並儲存，延伸超過第二點、到右緣為止，不畫進價格軸", created.value.status == 201 and len(rays) == 1 and len(items) == 2
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
    tools = page.locator("#drawTools")
    ray = tools.locator("button[data-tool=ray]")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    start = journey.ink(page)
    journey.check("手機：桌機畫的趨勢線看得到", start["count"] > 0 and len(journey.saved(page, code)) == 1, start)
    sizes = [tools.locator(f"button[data-tool={name}]").bounding_box() for name in ("trend", "hline", "ray")]
    journey.check("手機：三顆工具都夠大（至少 44px），工具列不造成橫向捲動", all(size["width"] >= 44 and size["height"] >= 44 for size in sizes)
                  and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), sizes)

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
    journey.check("手機：放開就畫出射線並儲存，延伸到右緣、不進價格軸，工具回一般模式", created.value.status == 201 and len(items) == 2
                  and drawn["maxX"] > rb[0] + 100 and drawn["maxX"] <= width - 50 and ray.get_attribute("aria-pressed") == "false", {"ink": drawn, "canvas": width})
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-08-ray-drawn.png"))
    ray_item = next(item for item in items if item["kind"] == "ray")

    slope = (rb[1] - ra[1]) / (rb[0] - ra[0])
    body = (rb[0] + 60, rb[1] + 60 * slope + 9)  # 延伸段旁 9px：手指的可點選範圍比滑鼠寬
    page.touchscreen.tap(*journey.at(page, *body))
    journey.check("手機：手指點在射線旁 9px 就選得到，工具列出現刪除", delete.is_visible())
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
    tools = page.locator("#drawTools")
    rect = tools.locator("button[data-tool=rect]")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    a, b = (round(width * 0.3), round(height * 0.3)), (round(width * 0.55), round(height * 0.6))  # 左上、右下
    inside = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    rect.hover()
    page.wait_for_timeout(700)
    tip = rect.evaluate("button => { const style = getComputedStyle(button, '::after'); return style.content + '|' + style.opacity; }")
    journey.check("滑鼠停留顯示「矩形」", tip == '"矩形"|1', tip)
    page.mouse.move(5, 5)
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
    journey.check("點在框的內部：不選取，照舊顯示 K 棒數值", not delete.is_visible() and page.locator("#chartHost .kc-tip.show").count() == 1)
    page.mouse.click(*journey.at(page, inside[0], a[1] + 3))
    journey.check("點在上邊線旁 3px：選取，工具列出現刪除", delete.is_visible())
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
    tools = page.locator("#drawTools")
    rect = tools.locator("button[data-tool=rect]")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    journey.check("手機：桌機畫的矩形看得到", journey.ink(page)["count"] > 0 and len(journey.saved(page, code)) == 1)
    sizes = [button.bounding_box() for button in tools.locator("button[data-tool]").all()]
    journey.check("手機：每顆工具都夠大（至少 44px），工具列不造成橫向捲動", len(sizes) == TOOL_COUNT and all(size["width"] >= 44 and size["height"] >= 44 for size in sizes)
                  and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), sizes)

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

    page.touchscreen.tap(*journey.at(page, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    journey.check("手機：手指點框的內部不選取", not delete.is_visible())
    page.touchscreen.tap(*journey.at(page, (a[0] + b[0]) / 2, a[1] + 9))
    journey.check("手機：手指點在上邊線旁 9px 就選取，工具列出現刪除", delete.is_visible())
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
TOOL_COUNT = 5  # 工具列目前有幾顆畫線工具（不含刪除）


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
    tools = page.locator("#drawTools")
    fib = tools.locator("button[data-tool=fib]")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]
    axis_width = 60  # 右側價格軸至少這麼寬（桌機 78 起）
    low, high = (round(width * 0.3), round(height * 0.66)), (round(width * 0.5), round(height * 0.3))  # 由低往高拉

    fib.hover()
    page.wait_for_timeout(700)
    tip = fib.evaluate("button => { const style = getComputedStyle(button, '::after'); return style.content + '|' + style.opacity; }")
    journey.check("滑鼠停留顯示「黃金分割」", tip == '"黃金分割"|1', tip)
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
    journey.check("橫線延伸到繪圖區右緣為止、不進價格軸；標示在線的左端外側", width - axis_width - 60 < ink["maxX"] <= width - axis_width
                  and ink["minX"] < a[0] - 30, {"ink": ink, "start": a, "canvas": width})
    journey.check("新畫的沒有被選取", not delete.is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / "desktop-20-fib-drawn.png"))
    before = items[0]["anchors"]
    line_id = items[0]["id"]

    right = b[0] + 120  # 兩點右邊、只有橫線經過的地方
    page.mouse.click(*journey.at(page, right, (levels[5] + levels[6]) / 2))
    journey.check("點在兩條橫線中間：不選取，照舊顯示 K 棒數值", not delete.is_visible() and page.locator("#chartHost .kc-tip.show").count() == 1)
    page.mouse.click(*journey.at(page, right, levels[3] + 3))
    journey.check("點在 0.5 那條旁 3px：選取，工具列出現刪除", delete.is_visible())
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
    journey.choose_period(page, "5m")
    journey.check("分 K：工具列與線都不顯示", not tools.is_visible() and journey.ink(page)["count"] == 0)
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
    tools = page.locator("#drawTools")
    fib = tools.locator("button[data-tool=fib]")
    delete = page.locator("#drawDelete")
    box = page.locator("#chartHost canvas").bounding_box()
    width, height = box["width"], box["height"]

    seen = fib_levels(page)
    journey.check("手機：桌機畫的黃金分割看得到", len(seen) > 0 and len(journey.saved(page, code)) == 1, seen)
    sizes = [button.bounding_box() for button in tools.locator("button[data-tool]").all()]
    journey.check("手機：加上黃金分割後每顆工具仍夠大（至少 44px），工具列不造成橫向捲動", len(sizes) == TOOL_COUNT and all(size["width"] >= 44 and size["height"] >= 44 for size in sizes)
                  and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), sizes)
    page.locator(".bd-chart").screenshot(path=str(OUT / "mobile-14-fib-from-desktop.png"))

    page.touchscreen.tap(*journey.at(page, width * 0.7, seen[len(seen) // 2] + 5))
    journey.check("手機：手指點在橫線旁 5px 就選取，工具列出現刪除", delete.is_visible())
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
    journey.check("手機：橫線到右緣為止、不進價格軸，標示留在圖內", ink["maxX"] <= width - 40 and ink["minX"] >= 0, {"ink": ink, "canvas": width})
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="已經開著的旅程伺服器；省略時自己起一個")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server = None
    base = args.base
    if not base:
        port = free_port()
        server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud")
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
            desktop(journey, browser)
            mobile(journey, browser)
            edit_desktop(journey, browser)
            edit_mobile(journey, browser)
            two_point_desktop(journey, browser)
            two_point_mobile(journey, browser)
            rect_desktop(journey, browser)
            rect_mobile(journey, browser)
            fib_desktop(journey, browser)
            fib_mobile(journey, browser)
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
