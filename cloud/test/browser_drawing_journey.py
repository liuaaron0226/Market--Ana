"""畫線工具的本機瀏覽器旅程（桌機與手機寬度各走一遍）。
票 01：水平線畫得出來，而且存得住。票 02：點線選取、拖動、刪除（工具列與 Delete）、Esc。

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
LINES_JS = """() => {
  const canvas = document.querySelector('#chartHost canvas');
  const { width, height } = canvas;
  const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
  const hex = getComputedStyle(document.documentElement).getPropertyValue('--draw').trim();
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const rows = [];
  for (let y = 0; y < height; y++) {
    let hits = 0;
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      if (Math.abs(data[i] - r) + Math.abs(data[i + 1] - g) + Math.abs(data[i + 2] - b) < 60) hits++;
    }
    if (hits > width * 0.3) rows.push(y);
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
        "() => { const style = getComputedStyle(document.querySelector('#drawTools button'), '::after'); return style.content + '|' + style.opacity; }") == '"水平線"|1')
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
    shown = page.evaluate("() => getComputedStyle(document.querySelector('#drawTools button'), '::after').opacity")
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
