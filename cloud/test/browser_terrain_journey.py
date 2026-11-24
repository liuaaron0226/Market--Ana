"""資金地形的手勢與常駐動效的瀏覽器旅程（2026-11-24 效能修正）。

本人用 iPhone：原本手指放在地形上往哪滑都是轉（頁面捲不動）、樓頂只有十幾像素很難點中、點了頁面會跳，轉起來也很卡。
這裡驗修正後的行為：
- 手機：手指在地形上下滑是捲頁面、左右拖才轉；手指抖 8 像素還算點選，選到的是手指下（或最近）的那棟；點選不捲頁面。
- 轉的時候名稱牌用投影算的位置要和瀏覽器畫出來的樓頂對齊；整片街區仍在框內。
- 桌機：滑鼠往哪拖都轉（滑鼠不會捲頁面）。
- 常駐動效只在看得到時動：「籌碼與資金」收著時，籌碼卡小圖上的動畫一個都沒在跑；展開、卡片在畫面上時才跑；
  頁首板邊的細光捲出畫面就暫停、捲回來接著動。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），不連外部。先在 cloud/ 跑過 `node build.mjs`。
截圖放 .tmp/terrain-journey/。

    python cloud/test/browser_terrain_journey.py
"""
from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/terrain-journey"
OWNER = "owner@example.com"

ANGLE = "() => parseFloat(document.querySelector('#rotationTerrain .terrain-city').dataset.angle)"
ROOFS = """() => [...document.querySelectorAll('#rotationTerrain .terrain-tower')].map(t => {
  const b = t.querySelector('.top').getBoundingClientRect();
  return { name: t.dataset.name, rank: +t.dataset.rank, x: b.left + b.width / 2, y: b.top + b.height / 2, w: b.width, h: b.height };
})"""
SELECTED = "() => { const s = document.querySelector('#rotationTerrain .terrain-tower.is-selected'); return s ? s.dataset.name : null; }"
# 名稱牌（看得到的）中心和它那棟樓頂中心的水平距離、牌的下緣到樓頂上緣的垂直距離（應該都是很小的數）
TAG_ALIGN = """() => {
  const towers = [...document.querySelectorAll('#rotationTerrain .terrain-tower')];
  const byTag = new Map(towers.map(t => [window.Terrain.shortName(t.dataset.name), t]));
  return [...document.querySelectorAll('#rotationTerrain .terrain-tag')].map(tag => {
    if (!tag.offsetWidth || tag.classList.contains('is-crowded')) return null;
    const roof = byTag.get(tag.textContent).querySelector('.top').getBoundingClientRect(), box = tag.getBoundingClientRect();
    return Math.abs((box.left + box.width / 2) - (roof.left + roof.width / 2));
  }).filter(v => v !== null);
}"""
INSIDE = """() => {
  const host = document.querySelector('#rotationTerrain'), stage = host.querySelector('.terrain-stage').getBoundingClientRect();
  const out = [...host.querySelectorAll('.terrain-ground, .terrain-tower .top')].map(n => n.getBoundingClientRect())
    .filter(b => b.left < stage.left - 1 || b.right > stage.right + 1 || b.top < stage.top - 1 || b.bottom > stage.bottom + 1);
  return out.length;
}"""
CHIP_ANIMS = """() => document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.target
  && a.effect.target.closest && a.effect.target.closest('#chipGrid') && ['fx-halo', 'fx-comet', 'fx-breath'].includes(a.animationName)).length"""
RIM_STATE = """() => { const rim = document.querySelector('.fx-rim'); if (!rim) return null;
  const a = rim.getAnimations({ subtree: true }).find(x => x.animationName === 'fx-rim'); return a ? a.playState : 'none'; }"""


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console = base, [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def home(self, browser, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": OWNER, "url": self.base}])
        context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())   # 外部字型之類的一律不連
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.goto(f"{self.base}/minimal.html", wait_until="domcontentloaded")
        page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(1500)
        return context, page


def open_cat(page, cat: str) -> None:
    key = page.locator(f'#homeCats button[data-cat="{cat}"]')
    key.scroll_into_view_if_needed()
    key.click()
    page.wait_for_function(f"() => document.querySelector('[data-cat-panel=\"{cat}\"]').hasAttribute('data-open')", timeout=10_000)
    page.wait_for_timeout(500)


def open_terrain(page) -> None:
    open_cat(page, "rotation")
    page.locator("#rotationTerrain .terrain-stage").wait_for(state="visible", timeout=20_000)
    page.evaluate("() => document.querySelector('#rotationTerrain .terrain-stage').scrollIntoView({ block: 'center' })")
    page.wait_for_function("() => document.querySelector('#rotationTerrain .terrain-city.is-settled')", timeout=30_000)
    page.wait_for_timeout(600)


class Finger:
    """經由瀏覽器的除錯介面送真正的觸控事件（Playwright 自己的觸控只會點一下）。"""

    def __init__(self, page) -> None:
        self.page, self.cdp = page, page.context.new_cdp_session(page)

    def send(self, kind: str, x: float | None = None, y: float | None = None) -> None:
        points = [] if kind == "touchEnd" else [{"x": round(x), "y": round(y)}]
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    def drag(self, x0: float, y0: float, x1: float, y1: float, steps: int = 16) -> None:
        self.send("touchStart", x0, y0)
        for i in range(1, steps + 1):
            self.send("touchMove", x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
            self.page.wait_for_timeout(16)
        self.send("touchEnd")

    def wobbly_tap(self, x: float, y: float, path=((3, 2), (6, 4), (8, 3), (7, -1))) -> None:
        """點下去、手指抖一下（最遠約 8 像素）再放開。"""
        self.send("touchStart", x, y)
        for dx, dy in path:
            self.page.wait_for_timeout(20)
            self.send("touchMove", x + dx, y + dy)
        self.page.wait_for_timeout(30)
        self.send("touchEnd")


def mobile(j: Journey, browser) -> None:
    tag = "手機"
    context, page = j.home(browser, viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    finger = Finger(page)

    # 常駐動效：籌碼與資金收著的時候，小圖上的動畫一個都不跑；展開、卡片在畫面上時才跑
    chips = page.locator("#chipsSection")
    j.check(f"{tag}：籌碼與資金一開始是收著的", not chips.evaluate("n => n.hasAttribute('data-open')"))
    page.wait_for_timeout(2500)
    j.check(f"{tag}：籌碼與資金收著時，籌碼卡小圖的動畫都停著", page.evaluate(CHIP_ANIMS) == 0, page.evaluate(CHIP_ANIMS))
    open_cat(page, "chips")
    page.evaluate("() => document.getElementById('chipGrid').scrollIntoView({ block: 'start' })")
    page.wait_for_timeout(3000)
    running = page.evaluate(CHIP_ANIMS)
    j.check(f"{tag}：展開籌碼與資金、卡片在畫面上，小圖的動畫在跑", running > 0, running)
    # 收起來（點同一個分類鍵）：馬上全部停
    page.locator('#homeCats button[data-cat="chips"]').click()
    page.wait_for_timeout(1200)
    j.check(f"{tag}：再收起籌碼與資金，小圖的動畫又全部停下來", page.evaluate(CHIP_ANIMS) == 0, page.evaluate(CHIP_ANIMS))
    # 頁首板邊的細光：在畫面上時動，捲出畫面就暫停，捲回來接著動
    page.evaluate("() => scrollTo(0, 0)")
    page.wait_for_timeout(800)
    top_state = page.evaluate(RIM_STATE)
    page.evaluate("() => scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(800)
    away_state = page.evaluate(RIM_STATE)
    page.evaluate("() => scrollTo(0, 0)")
    page.wait_for_timeout(800)
    back_state = page.evaluate(RIM_STATE)
    j.check(f"{tag}：頁首板邊的細光在畫面上時動、捲走暫停、捲回來再動", [top_state, away_state, back_state] == ["running", "paused", "running"],
            [top_state, away_state, back_state])

    open_terrain(page)
    stage = page.locator("#rotationTerrain .terrain-stage")
    j.check(f"{tag}：地形框讓瀏覽器處理上下捲動（touch-action: pan-y）", stage.evaluate("n => getComputedStyle(n).touchAction") == "pan-y")
    page.screenshot(path=str(OUT / "mobile-1-terrain.png"))

    # 手指在地形上上下滑：頁面跟著捲，街區不轉。地形在頁面底部附近，往下已經捲不動的話就往上捲
    box = stage.bounding_box()
    y0, a0 = page.evaluate("() => scrollY"), page.evaluate(ANGLE)
    room = page.evaluate("() => document.documentElement.scrollHeight - innerHeight - scrollY")
    top, bottom = box["y"] + box["height"] * 0.2, box["y"] + box["height"] * 0.8
    start, end = (bottom, top) if room > 120 else (top, bottom)
    finger.drag(box["x"] + box["width"] / 2, start, box["x"] + box["width"] / 2 + 4, end)
    page.wait_for_timeout(900)
    y1, a1 = page.evaluate("() => scrollY"), page.evaluate(ANGLE)
    j.check(f"{tag}：手指在地形上上下滑，頁面跟著捲", abs(y1 - y0) > 60, [y0, y1, room])
    j.check(f"{tag}：上下滑的時候街區不轉", abs(a1 - a0) < 0.5, [a0, a1])

    # 左右拖：轉，頁面不捲
    page.evaluate("() => document.querySelector('#rotationTerrain .terrain-stage').scrollIntoView({ block: 'center' })")
    page.wait_for_timeout(500)
    box = stage.bounding_box()
    y0, a0 = page.evaluate("() => scrollY"), page.evaluate(ANGLE)
    cy = box["y"] + box["height"] / 2
    finger.drag(box["x"] + box["width"] * 0.2, cy, box["x"] + box["width"] * 0.75, cy + 8, steps=24)
    page.wait_for_timeout(1500)   # 放手後的慣性滑完
    y1, a1 = page.evaluate("() => scrollY"), page.evaluate(ANGLE)
    j.check(f"{tag}：手指左右拖，街區轉（拖約 190 像素轉超過 60 度）", abs(a1 - a0) >= 60, [a0, a1])
    j.check(f"{tag}：左右拖的時候頁面不捲", abs(y1 - y0) < 2, [y0, y1])
    page.wait_for_timeout(300)
    gaps = page.evaluate(TAG_ALIGN)
    j.check(f"{tag}：轉完名稱牌仍對在樓頂正上方（投影算的位置和畫出來的一致）", gaps and max(gaps) < 2.5, gaps)
    j.check(f"{tag}：轉完整片街區仍在框內", page.evaluate(INSIDE) == 0, page.evaluate(INSIDE))

    # 點選：手指抖約 8 像素還算點選，選到的是那一棟；頁面不捲
    page.evaluate("() => document.querySelector('#rotationTerrain .terrain-stage').scrollIntoView({ block: 'start' })")
    page.wait_for_timeout(600)
    roofs = sorted(page.evaluate(ROOFS), key=lambda r: r["rank"])
    picks = [roofs[0], roofs[len(roofs) // 2], roofs[-1]]
    for target in picks:
        roof = next(r for r in page.evaluate(ROOFS) if r["name"] == target["name"])
        y0 = page.evaluate("() => scrollY")
        finger.wobbly_tap(roof["x"], roof["y"])
        page.wait_for_timeout(700)
        picked, y1 = page.evaluate(SELECTED), page.evaluate("() => scrollY")
        j.check(f"{tag}：手指抖 8 像素點「{target['name']}」，選到的就是它", picked == target["name"], picked)
        j.check(f"{tag}：點「{target['name']}」頁面不捲動", y1 == y0, [y0, y1])
        if picked:   # 再點一次取消，下一棟各自獨立
            roof = next(r for r in page.evaluate(ROOFS) if r["name"] == picked)
            finger.wobbly_tap(roof["x"], roof["y"])
            page.wait_for_timeout(500)
    j.check(f"{tag}：再點同一棟取消選取", page.evaluate(SELECTED) is None, page.evaluate(SELECTED))

    # 點在樓頂旁邊（離中心約 12 像素）：選到樓頂中心離手指最近的那一棟
    roofs = page.evaluate(ROOFS)
    small = min(roofs, key=lambda r: min(r["w"], r["h"]))
    x, y = small["x"] + 9, small["y"] - 8
    want = min(roofs, key=lambda r: math.hypot(r["x"] - x, r["y"] - y))["name"]
    finger.wobbly_tap(x, y, path=((1, 1),))
    page.wait_for_timeout(700)
    j.check(f"{tag}：點在小樓旁邊，選到最近的一棟", page.evaluate(SELECTED) == want, [page.evaluate(SELECTED), want])
    box = stage.bounding_box()
    finger.wobbly_tap(box["x"] + 6, box["y"] + box["height"] - 6, path=((1, 0),))
    page.wait_for_timeout(500)
    j.check(f"{tag}：點地形的空白角落取消選取", page.evaluate(SELECTED) is None, page.evaluate(SELECTED))
    j.check(f"{tag}：不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
    page.screenshot(path=str(OUT / "mobile-2-after.png"))
    context.close()


def desktop(j: Journey, browser) -> None:
    tag = "桌機"
    context, page = j.home(browser, viewport={"width": 1280, "height": 900})
    open_terrain(page)
    box = page.locator("#rotationTerrain .terrain-stage").bounding_box()
    a0 = page.evaluate(ANGLE)
    low = box["y"] + box["height"] - 12
    page.mouse.move(box["x"] + 12, low)
    page.mouse.down()
    page.mouse.move(box["x"] + 92, low, steps=6)
    page.mouse.up()
    a1 = page.evaluate(ANGLE)
    j.check(f"{tag}：滑鼠水平拖 80 像素轉超過 40 度", abs(a1 - a0) >= 40, [a0, a1])
    page.mouse.move(box["x"] + 12, low)
    page.mouse.down()
    page.mouse.move(box["x"] + 52, low - 70, steps=6)
    page.mouse.up()
    a2 = page.evaluate(ANGLE)
    j.check(f"{tag}：滑鼠斜著拖（偏上下）也算旋轉", a2 != a1, [a1, a2])
    page.wait_for_timeout(1500)   # 放手後的慣性滑完（滑的時候點一下只是讓它停）
    gaps = page.evaluate(TAG_ALIGN)
    j.check(f"{tag}：轉完名稱牌仍對在樓頂正上方", gaps and max(gaps) < 2.5, gaps)
    j.check(f"{tag}：轉完整片街區仍在框內", page.evaluate(INSIDE) == 0, page.evaluate(INSIDE))
    top = min(page.evaluate(ROOFS), key=lambda r: r["rank"])
    page.mouse.click(top["x"], top["y"])
    page.wait_for_timeout(400)
    j.check(f"{tag}：點樓頂選到那一棟、出明細", page.evaluate(SELECTED) == top["name"]
            and top["name"] in page.locator("#rotationTerrain .terrain-detail").inner_text())
    page.screenshot(path=str(OUT / "desktop-terrain.png"))
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "MARKET_ANA_ENABLED_FEATURES": "market_trend_v1"}
    server = subprocess.Popen(["node", "--no-warnings", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    j = Journey(base)
    try:
        for _ in range(100):
            try:
                urlopen(base, timeout=1)
                break
            except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
                if getattr(error, "code", None):
                    break
                time.sleep(0.2)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            mobile(j, browser)
            desktop(j, browser)
            browser.close()
        errors = [text for text in j.console if "favicon" not in text]
        j.check("沒有程式錯誤", not errors, errors[:5])
    finally:
        server.terminate()
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
