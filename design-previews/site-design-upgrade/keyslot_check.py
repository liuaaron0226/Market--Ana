"""K 線板標題列「鍵收成一格、左右滑切換」（web/key-slot.js、key-slot.css）的檢查：
用真的觸控事件（不是滑鼠）在手機寬度滑、點、上下捲；首頁三顆、個股頁兩顆、電腦寬度維持並排。

對著本機預覽跑（先 node cloud/build.mjs，再 node design-previews/3d-redesign/preview-server.mjs，預設 http://127.0.0.1:5310）。
無頭 Chromium，不是真的 iPhone：Safari 從螢幕右緣往左滑的系統手勢、手指的實際手感，這支量不到。
用法：python design-previews/site-design-upgrade/keyslot_check.py [網址]
輸出：.scratch/site-design-upgrade/keyslot/*.png、keyslot.json；有不符就結束碼 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch" / "site-design-upgrade" / "keyslot"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5310"
PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
READY = """async () => { for (let i = 0; i < 100; i++) { if (window.MinimalApp && window.MinimalApp.ready && document.querySelector('#mainChart canvas') && document.querySelector('.chart-controls .forecast-chip')) return true; await new Promise(r => setTimeout(r, 150)); } return false; }"""
STATE = r"""() => {
  const host = document.querySelector('body.hm .chart-controls, body.st .ts-bar-keys');
  const seen = [...host.querySelectorAll(':scope > .forecast-control, :scope > .dt-ui, :scope > .hm-period')].filter(u => u.getClientRects().length && getComputedStyle(u).opacity === '1' && getComputedStyle(u).pointerEvents !== 'none')
    .map(u => (u.querySelector('#toolsKey') || u.querySelector('.forecast-chip, .chart-chip') || u).textContent.trim().replace(/\s+/g, ''));
  const key = [...host.querySelectorAll('.ks-on')].find(u => u.getClientRects().length) || host.querySelector('.hm-period'), box = key.getBoundingClientRect();
  const title = (document.querySelector('#indexChip') || document.body).getBoundingClientRect();
  const pop = document.querySelector('#forecastPanel'), popBox = pop && !pop.hidden ? pop.getBoundingClientRect() : null;
  return { seen, current: window.KeySlot && window.KeySlot.current(), live: document.documentElement.classList.contains('ks-live'), count: host.dataset.ksCount || null,
    dots: getComputedStyle(host, '::after').content !== 'none', key: { x: box.left + box.width / 2, y: box.top + box.height / 2, left: Math.round(box.left), right: Math.round(box.right), w: Math.round(box.width) },
    titleRight: Math.round(title.right), overlap: !!document.querySelector('#indexChip') && Math.round(title.right) > Math.round(box.left),
    open: [...host.querySelectorAll('[aria-expanded="true"]')].map(e => e.textContent.trim().replace(/\s+/g, '')),
    menus: [...document.querySelectorAll('#periodMenu, #drawTools')].filter(m => !m.hidden).map(m => m.id),
    forecast: popBox && { top: Math.round(popBox.top), bottom: Math.round(popBox.bottom), left: Math.round(popBox.left), right: Math.round(popBox.right) },
    scrollY: Math.round(scrollY), toolOn: !!document.querySelector('#toolsKey.is-on') };
}"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    problems: list[str] = []

    def expect(name: str, ok: bool, detail=None) -> None:
        report[name] = {"ok": bool(ok), "detail": detail}
        print(("通過 " if ok else "不符 ") + name, "" if detail is None else detail, flush=True)
        if not ok:
            problems.append(name)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei", **PHONE)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
        page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
        cdp = context.new_cdp_session(page)
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.evaluate(READY)
        page.wait_for_timeout(2200)
        page.evaluate("() => document.querySelector('.chart-toolbar').scrollIntoView({ block: 'center' })")
        page.wait_for_timeout(500)

        def touch(kind: str, x: float, y: float) -> None:
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y}]})

        def swipe(dx: float, dy: float = 0, steps: int = 8, pause: int = 16, shot: str | None = None, settle: int = 700) -> None:
            at = page.evaluate(STATE)["key"]
            touch("touchStart", at["x"], at["y"])
            for index in range(1, steps + 1):
                touch("touchMove", at["x"] + dx * index / steps, at["y"] + dy * index / steps)
                page.wait_for_timeout(pause)
                if shot and index == steps // 2:
                    crop(shot)
            touch("touchEnd", 0, 0)
            page.wait_for_timeout(settle)

        def tap() -> None:
            at = page.evaluate(STATE)["key"]
            touch("touchStart", at["x"], at["y"])
            page.wait_for_timeout(60)
            touch("touchEnd", 0, 0)
            page.wait_for_timeout(700)

        def period_menu_swipes(prefix: str) -> None:
            # 細項自己的橫向滑選不能被外層三鍵攔走；放開選定後仍照既有規則收合。
            for origin, target, label, across in (("daily", "weekly", "週K", 0), ("weekly", "daily", "日K", 0),
                                                   ("daily", "daily", "日K", 1), ("daily", "daily", "日K", -1)):
                direction = ("向右" if across > 0 else "向左") if across else "沿選單排列"
                page.evaluate("() => window.KeySlot.show('period')")
                page.wait_for_timeout(450)
                if not page.locator("#periodMenu").is_visible():
                    tap()
                start = page.locator(f'#periodMenu button[data-value="{origin}"]').bounding_box()
                end = page.locator(f'#periodMenu button[data-value="{target}"]').bounding_box()
                x, y = start["x"] + start["width"] / 2, start["y"] + start["height"] / 2
                tx, ty = end["x"] + end["width"] / 2, end["y"] + end["height"] / 2
                if across:
                    # 在同一個細項內左右各滑超過三鍵門檻，仍只能選它，不能換標題列功能。
                    half = min(start["width"] / 2 - 3, 20)
                    tx, ty = x + across * half, y
                    x -= across * half
                touch("touchStart", x, y)
                for index in range(1, 9):
                    touch("touchMove", x + (tx - x) * index / 8, y + (ty - y) * index / 8)
                    page.wait_for_timeout(30)
                during = page.evaluate(STATE)
                touch("touchEnd", 0, 0)
                page.wait_for_timeout(700)
                after = page.evaluate(STATE)
                value = page.locator("#periodSelect").input_value()
                expect(f"{prefix}細項{direction}滑向{label}途中：選單保持開啟、不切三鍵",
                       during["current"] == "period" and "periodMenu" in during["menus"], during)
                expect(f"{prefix}細項{direction}滑向{label}放開：只選週期、仍停週期鍵並正常收合",
                       value == target and after["current"] == "period" and after["seen"] == [label] and not after["menus"],
                       {"value": value, "state": after})

        def crop(name: str) -> None:
            box = page.locator(".chart-toolbar").bounding_box()
            page.screenshot(path=str(OUT / f"{name}.png"), clip={"x": 0, "y": max(0, box["y"] - 8), "width": 390, "height": box["height"] + 34})

        first = page.evaluate(STATE)
        crop("1-start")
        expect("一進來只看得到一顆鍵（日K），標題沒有被蓋到，下面三個小點", first["seen"] == ["日K"] and not first["overlap"] and first["count"] == "3" and first["dots"], first)

        swipe(-90, shot="2-mid-swipe")
        after = page.evaluate(STATE)
        crop("3-after-left")
        expect("往左滑：換成下一顆（預測），沒有打開任何選單", after["seen"] == ["預測1日"] and not after["open"] and not after["menus"] and after["forecast"] is None, after)

        swipe(90)
        back = page.evaluate(STATE)
        expect("往右滑：回到日K", back["seen"] == ["日K"], back["seen"])
        swipe(90)
        tools = page.evaluate(STATE)
        crop("4-tools")
        expect("再往右滑：換成分析工具", tools["seen"] == ["分析工具"], tools["seen"])

        swipe(-14)
        small = page.evaluate(STATE)
        expect("只滑一點點（14px）：彈回，不換、也不打開選單", small["seen"] == ["分析工具"] and not small["menus"], small)

        tap()
        opened = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "5-tools-menu.png"))
        expect("點一下分析工具：選單打開", "drawTools" in opened["menus"], opened["menus"])
        page.locator("#drawTools button[data-tool=hline]").tap()
        page.wait_for_timeout(700)
        picked = page.evaluate(STATE)
        crop("6-tool-picked")
        expect("選了水平線：停在分析工具那一顆，旁邊的離開鍵看得到", picked["toolOn"] and page.locator("#toolsExit").is_visible() and picked["current"] == "tools", picked["seen"])
        page.locator("#toolsExit").tap()
        page.wait_for_timeout(500)
        left = page.evaluate(STATE)
        expect("按離開鍵：不在畫線狀態了", not left["toolOn"], left["seen"])

        tap()
        swipe(-90)
        shut = page.evaluate(STATE)
        expect("分析工具的選單開著時往左滑：選單收起、換到日K", not shut["menus"] and not shut["open"] and shut["seen"] == ["日K"], shut)
        swipe(90)

        swipe(-90)
        swipe(-90)
        fore = page.evaluate(STATE)
        expect("往左滑兩次（繞一圈）：到預測", fore["seen"] == ["預測1日"], fore["seen"])
        tap()
        pop = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "7-forecast-open.png"))
        expect("點一下預測：浮窗打開，位置在畫面內（手機上貼著下緣）", pop["forecast"] is not None and pop["forecast"]["left"] >= 0 and pop["forecast"]["right"] <= 390 and 0 < pop["forecast"]["bottom"] <= 844, pop["forecast"])
        swipe(90)
        closed = page.evaluate(STATE)
        expect("浮窗開著時往右滑：先收起浮窗，換到日K（從第一顆往右是繞回最後一顆）", closed["forecast"] is None and closed["seen"] == ["日K"], closed)

        tap()
        menu = page.evaluate(STATE)
        page.screenshot(path=str(OUT / "8-period-menu.png"))
        expect("點一下日K：週期選單打開", "periodMenu" in menu["menus"], menu["menus"])
        period_menu_swipes("首頁")
        page.evaluate("() => window.KeySlot.show('period')")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        before_y = page.evaluate("() => scrollY")
        swipe(0, -160, steps=10)
        scrolled = page.evaluate(STATE)
        expect("在那一格上往上滑：頁面照常捲動，鍵沒有換", scrolled["scrollY"] > before_y + 40 and scrolled["seen"] == ["日K"], {"from": before_y, "to": scrolled["scrollY"], "seen": scrolled["seen"]})
        page.evaluate("() => document.querySelector('.chart-toolbar').scrollIntoView({ block: 'center' })")
        page.wait_for_timeout(900)

        # 滑完馬上點（中間只隔 0.15 秒）：那一下點擊要有作用
        swipe(90, settle=150)
        tap()
        quick = page.evaluate(STATE)
        expect("滑到分析工具後馬上點：選單打開", quick["seen"] == ["分析工具"] and "drawTools" in quick["menus"], quick)
        # 選單開著時用程式換到別顆（鍵盤焦點走到別顆也是走這一條）：原本那顆的選單要一起收
        page.evaluate("() => window.KeySlot.show('period')")
        page.wait_for_timeout(500)
        moved = page.evaluate(STATE)
        expect("選單開著時換到別顆：選單收起，不會留一個看不到卻開著的選單", moved["seen"] == ["日K"] and not moved["menus"] and not moved["open"], moved)
        page.evaluate("() => document.querySelector('.chart-controls .forecast-chip').focus()")
        page.wait_for_timeout(400)
        focused = page.evaluate(STATE)
        expect("鍵盤焦點走到沒顯示的那一顆：換成那一顆", focused["seen"] == ["預測1日"], focused["seen"])
        page.evaluate("() => { document.activeElement.blur(); window.KeySlot.show('period'); }")
        page.wait_for_timeout(300)
        expect("沒有程式錯誤", not errors, errors)
        context.close()

        # ---- 個股頁：同一排只有兩顆（分析工具、日K），也收成一格 ----
        context = browser.new_context(color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei", **PHONE)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
        page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
        cdp = context.new_cdp_session(page)
        page.goto(BASE + "/stock.html?code=2330", wait_until="domcontentloaded")
        page.wait_for_function("() => !document.getElementById('content').hidden && !!document.querySelector('#chartHost canvas') && !!document.querySelector('.ts-bar-keys #toolsKey')", timeout=30_000)
        page.wait_for_timeout(2200)

        def crop_stock(name: str) -> None:
            box = page.locator(".bd-chart-bar").bounding_box()
            page.screenshot(path=str(OUT / f"{name}.png"), clip={"x": 0, "y": max(0, box["y"] - 24), "width": 390, "height": box["height"] + 70})

        start = page.evaluate(STATE)
        crop_stock("s1-start")
        expect("個股頁一進來只看得到日K", start["seen"] == ["日K"], start["seen"])
        expect("個股頁是兩個小點", page.evaluate("() => document.querySelector('.ts-bar-keys').dataset.ksCount") == "2")
        swipe(-90)
        moved = page.evaluate(STATE)
        crop_stock("s2-tools")
        expect("個股頁往左滑：換成分析工具", moved["seen"] == ["分析工具"] and not moved["menus"], moved["seen"])
        tap()
        opened = page.evaluate(STATE)
        expect("個股頁點一下分析工具：選單打開", "drawTools" in opened["menus"], opened["menus"])
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        swipe(90)
        tap()
        period = page.evaluate(STATE)
        expect("個股頁往右滑回日K 再點：週期選單打開", period["seen"] == ["日K"] and "periodMenu" in period["menus"], period)
        period_menu_swipes("個股頁")
        page.evaluate("() => window.KeySlot.show('period')")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        # 選了一條畫好的線（draw-tools-ui.js 會把刪除與到價提醒那一組放出來）：自動換到分析工具，不然刪除鍵看不到；取消選取就回到原本那一顆
        page.evaluate("() => { document.querySelector('.ts-bar-keys .dt-acts').hidden = false; }")
        page.wait_for_timeout(500)
        chosen = page.evaluate(STATE)
        trash = page.evaluate("() => { const b = document.getElementById('drawDelete').getBoundingClientRect(); const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return Boolean(hit && hit.closest('#drawDelete')); }")
        page.evaluate("() => { document.querySelector('.ts-bar-keys .dt-acts').hidden = true; }")
        page.wait_for_timeout(500)
        dropped = page.evaluate(STATE)
        expect("選了一條線：自動換到分析工具、刪除鍵點得到；取消選取回到日K", chosen["seen"] == ["分析工具"] and trash and dropped["seen"] == ["日K"], [chosen["seen"], trash, dropped["seen"]])
        # 分析工具暫時不在（分時沒有它）：只剩一顆、不畫小點、滑不動；它回來之後，原本停在它上面的話要換回去
        page.evaluate("() => window.KeySlot.show('tools')")
        page.wait_for_timeout(300)
        page.evaluate("() => { document.querySelector('.ts-bar-keys .dt-ui').hidden = true; }")
        page.wait_for_timeout(400)
        alone = page.evaluate(STATE)
        swipe(-90)
        stuck = page.evaluate(STATE)
        crop_stock("s3-one-key")
        expect("個股頁只剩一顆時：顯示日K、沒有小點、滑了不換", alone["seen"] == ["日K"] and alone["count"] == "1" and not alone["dots"] and stuck["seen"] == ["日K"], [alone, stuck["seen"]])
        page.evaluate("() => { document.querySelector('.ts-bar-keys .dt-ui').hidden = false; }")
        page.wait_for_timeout(400)
        again = page.evaluate(STATE)
        expect("分析工具回來：換回原本停著的那一顆，兩個小點", again["seen"] == ["分析工具"] and again["count"] == "2" and again["dots"], again)
        expect("個股頁沒有程式錯誤", not errors, errors)
        context.close()

        desk = browser.new_context(viewport={"width": 1280, "height": 820}, color_scheme="light", locale="zh-TW")
        page = desk.new_page()
        page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
        page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.evaluate(READY)
        page.wait_for_timeout(1800)
        wide = page.evaluate(STATE)
        expect("電腦寬度：維持三顆並排，那一格沒有啟用", len(wide["seen"]) == 3 and not wide["live"] and not wide["dots"], wide)
        desk.close()
        browser.close()

    (OUT.parent / "keyslot.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("全部通過" if not problems else f"不符 {len(problems)} 項：{problems}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
