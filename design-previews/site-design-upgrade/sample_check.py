"""樣品自己的檢查（audit_capture.py 量不到的那幾樣）：
1. 手機的底部板：位置對不對、拖一小段放手會彈回、往下一甩會關掉（明細板、設定）。
2. 「改版前」是不是真的和網站原本一模一樣：把樣品切到改版前，和本機預覽同一頁逐點比對。
3. 電腦寬度：明細板仍在正中央（底部板只給手機）。
4. 頁面有沒有程式錯誤。

用法：python design-previews/site-design-upgrade/sample_check.py
輸出：.scratch/site-design-upgrade/check/*.png、check.json；有不符就結束碼 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch" / "site-design-upgrade" / "check"
SITE, SAMPLE = "http://127.0.0.1:5310", "http://127.0.0.1:5312"
# 給一個網址當參數＝檢查那個網址上「已經內建動態層」的首頁（例如正式打包後的本機預覽）：只跑底部板與電腦寬度兩組，
# 不做「改版前逐點相同」那一組（那一組要有樣品的開關才切得回改版前）。
BUILT_IN = sys.argv[1] if len(sys.argv) > 1 else ""
if BUILT_IN:
    SAMPLE = BUILT_IN
PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
DESK = dict(viewport={"width": 1280, "height": 820}, device_scale_factor=1)
READY = """async () => { for (let i = 0; i < 100; i++) { if (window.MinimalApp && window.MinimalApp.ready && document.querySelector('#mainChart canvas')) return true; await new Promise(r => setTimeout(r, 150)); } return false; }"""
RECT = "s => { const b = document.querySelector(s).getBoundingClientRect(); return { left: Math.round(b.left), top: Math.round(b.top), right: Math.round(b.right), bottom: Math.round(b.bottom) }; }"


def open_home(browser, base: str, options: dict, **extra):
    context = browser.new_context(color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei", **options, **extra)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
    page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
    page.goto(base + "/", wait_until="domcontentloaded")
    page.evaluate(READY)
    page.wait_for_timeout(2200)
    return context, page, errors


def drag(page, x: float, y0: float, y1: float, steps: int, step_ms: int) -> None:
    page.mouse.move(x, y0)
    page.mouse.down()
    for index in range(1, steps + 1):
        page.mouse.move(x, y0 + (y1 - y0) * index / steps)
        page.wait_for_timeout(step_ms)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    problems: list[str] = []

    def expect(name: str, ok: bool, detail) -> None:
        report[name] = {"ok": bool(ok), "detail": detail}
        print(("通過 " if ok else "不符 ") + name, detail, flush=True)
        if not ok:
            problems.append(name)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        # ---- 1. 手機的底部板 ----
        context, page, errors = open_home(browser, SAMPLE, PHONE)
        page.locator("#priceSummary").click()
        page.wait_for_timeout(700)
        box = page.evaluate(RECT, "#detailPanel")
        page.screenshot(path=str(OUT / "sheet-1-open.png"))
        expect("明細板貼齊畫面左右與下緣", box["left"] == 0 and box["right"] == 390 and box["bottom"] == 844, box)
        head = page.evaluate(RECT, "#detailPanel .panel-header")
        hx, hy = 150, head["top"] + 14
        drag(page, hx, hy, hy + 90, 6, 30)
        held = page.evaluate(RECT, "#detailPanel")
        page.screenshot(path=str(OUT / "sheet-2-dragging.png"))
        expect("按住標題列往下拖，板跟著手指走", abs((held["top"] - box["top"]) - 90) <= 3, {"moved": held["top"] - box["top"]})
        page.wait_for_timeout(400)   # 停住再放：速度是 0、距離不到三成 → 彈回
        page.mouse.up()
        page.wait_for_timeout(600)
        back = page.evaluate(RECT, "#detailPanel")
        expect("拖一小段放手會彈回、板還開著", back["top"] == box["top"] and page.evaluate("() => document.querySelector('#detailPanel').classList.contains('is-open')"), back)
        drag(page, hx, hy, hy + 260, 5, 16)   # 往下一甩
        page.mouse.up()
        page.wait_for_timeout(120)
        page.screenshot(path=str(OUT / "sheet-3-flung.png"))
        page.wait_for_timeout(600)
        closed = page.evaluate("() => { const p = document.querySelector('#detailPanel'); return !p.classList.contains('is-open') && getComputedStyle(p).visibility === 'hidden' && !p.style.getPropertyValue('--upg-drag'); }")
        expect("往下一甩會關掉，而且沒有留下拖動的位移", closed, page.evaluate("() => document.querySelector('#detailPanel').getAttribute('aria-hidden')"))
        page.locator("#priceSummary").click()
        page.wait_for_timeout(700)
        again = page.evaluate(RECT, "#detailPanel")
        expect("關掉之後再開，位置和第一次一樣", again == box, again)
        page.evaluate("() => document.querySelector('#detailPanel .panel-close').click()")
        page.wait_for_timeout(500)

        # 選中的框：展開一類之後，那一排的 ::before 要和被選中的那顆鍵一樣大、而且看得到
        page.locator("#homeCats button[data-cat='news']").scroll_into_view_if_needed()
        page.locator("#homeCats button[data-cat='news']").click()
        page.wait_for_timeout(900)
        frame = page.evaluate("""() => { const bar = document.querySelector('#homeCats'), key = bar.querySelector('button[aria-expanded="true"]'), cs = getComputedStyle(bar, '::before');
          return { frame: [Math.round(parseFloat(cs.width)), Math.round(parseFloat(cs.height))], key: [key.offsetWidth, key.offsetHeight], opacity: cs.opacity, shadow: cs.boxShadow !== 'none' }; }""")
        expect("選中的框畫得出來：和被選中的那顆鍵一樣大、不透明、有鍵的陰影", frame["frame"] == frame["key"] and frame["frame"][0] > 40 and frame["opacity"] == "1" and frame["shadow"], frame)
        page.screenshot(path=str(OUT / "frame-selected.png"))
        page.locator("#homeCats button[data-cat='news']").click()
        page.wait_for_timeout(700)
        gone = page.evaluate("() => getComputedStyle(document.querySelector('#homeCats'), '::before').opacity")
        expect("收回之後框淡掉", gone == "0", gone)
        page.evaluate("() => scrollTo(0, 0)")
        page.wait_for_timeout(300)

        page.locator("#settingsOpen").click()
        page.wait_for_timeout(700)
        sheet = page.evaluate(RECT, "#settingsDialog")
        page.screenshot(path=str(OUT / "settings-1-open.png"))
        expect("設定是貼底的板", sheet["left"] == 0 and sheet["right"] == 390 and sheet["bottom"] == 844, sheet)
        st = page.evaluate(RECT, "#settingsDialog .st-head")
        drag(page, 120, st["top"] + 8, st["top"] + 8 + 280, 5, 16)
        page.mouse.up()
        page.wait_for_timeout(700)
        expect("設定往下一甩會關掉", page.evaluate("() => !document.querySelector('#settingsDialog').open"), None)
        expect("手機沒有程式錯誤", not errors, errors)
        context.close()

        # ---- 2. 改版前＝網站原本的樣子（關掉動態比，免得桌面的光這類常駐效果造成差異） ----
        shots = {}
        for tag, base in (() if BUILT_IN else (("site", SITE), ("sample-before", SAMPLE))):
            context, page, errors = open_home(browser, base, PHONE, reduced_motion="reduce")
            if tag == "sample-before":
                page.evaluate("() => { window.UpgradeSample.state.on = false; window.UpgradeSample.apply(); document.querySelector('.upg-ctl').style.display = 'none'; }")
                page.wait_for_timeout(300)
                expect("切到改版前之後，頁面上沒有升級層的類別", page.evaluate("() => !/\\bupg/.test(document.documentElement.className)"), page.evaluate("() => document.documentElement.className"))
            page.locator("#homeCats button[data-cat='chips']").click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(OUT / f"parity-{tag}.png"), full_page=True)
            shots[tag] = Image.open(OUT / f"parity-{tag}.png").convert("RGB")
            context.close()
        if not BUILT_IN:
            same_size = shots["site"].size == shots["sample-before"].size
            diff_box = ImageChops.difference(shots["site"], shots["sample-before"]).getbbox() if same_size else "尺寸不同"
            expect("改版前和網站原本逐點相同（首頁整頁，籌碼與資金展開）", same_size and diff_box is None, {"sizes": [shots["site"].size, shots["sample-before"].size], "diff": diff_box})

        # ---- 3. 電腦寬度 ----
        context, page, errors = open_home(browser, SAMPLE, DESK)
        page.locator("#priceSummary").click()
        page.wait_for_timeout(700)
        panel = page.evaluate(RECT, "#detailPanel")
        page.screenshot(path=str(OUT / "desk-panel.png"))
        centred = abs((panel["left"] + panel["right"]) / 2 - 640) <= 2 and panel["bottom"] < 820
        expect("電腦寬度的明細板仍在正中央", centred, panel)
        expect("電腦沒有程式錯誤", not errors, errors)
        context.close()
        browser.close()

    (OUT.parent / "check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("全部通過" if not problems else f"不符 {len(problems)} 項：{problems}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
