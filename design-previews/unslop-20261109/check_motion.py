"""動態效果實測：錄影＋關鍵時間點截圖＋檢查動畫是否真的有跑。隨機埠、不碰 8897。"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa: E402
from shoot import make_handler  # noqa: E402

OUT = HERE / "motion-check"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(HERE / "web-bold"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    report: dict[str, object] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 800},
                                      record_video_dir=str(OUT), record_video_size={"width": 1280, "height": 800})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/stock.html?code=6488")
            page.wait_for_selector("#content:not([hidden])")
            report["running_animations_on_enter"] = page.evaluate(
                "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(document.getAnimations().map(a => a.effect.target.className || a.effect.target.id)))))")
            page.screenshot(path=str(OUT / "01-entering.png"))
            report["pill_on_load"] = page.evaluate("document.querySelectorAll('.bd-switch-pill').length")
            page.wait_for_timeout(900)
            page.screenshot(path=str(OUT / "02-settled.png"))
            report["close_text"] = page.inner_text("#close")

            pill = page.locator(".bd-switch-pill")
            report["pill_exists"] = pill.count()
            before = page.evaluate("getComputedStyle(document.querySelector('.bd-switch-pill')).transform") if pill.count() else None
            page.click(".kc-study-choice[data-study='macd']")
            page.wait_for_timeout(100)
            report["animations_mid_switch"] = page.evaluate("document.getAnimations().length")
            page.screenshot(path=str(OUT / "03-switching.png"))
            page.wait_for_timeout(400)
            after = page.evaluate("getComputedStyle(document.querySelector('.bd-switch-pill')).transform") if pill.count() else None
            report["pill_moved"] = before != after
            page.screenshot(path=str(OUT / "04-macd.png"))

            page.click("#verdictButton")
            page.wait_for_timeout(80)
            report["animations_sources"] = page.evaluate("document.getAnimations().length")
            page.wait_for_timeout(400)

            page.locator(".kc-study-details-summary:visible").first.click()
            page.wait_for_timeout(80)
            report["details_animating"] = page.evaluate("document.getAnimations().length")
            page.wait_for_timeout(400)
            report["details_open"] = page.evaluate("[...document.querySelectorAll('.kc-study-details')].some(d => d.open)")

            # 換股：數字從舊值滾到新值
            page.fill("#codeInput", "2330")
            page.press("#codeInput", "Enter")
            page.wait_for_timeout(250)
            report["close_mid_tween"] = page.inner_text("#close")
            page.screenshot(path=str(OUT / "05-switch-stock.png"))
            page.wait_for_timeout(900)
            report["close_after"] = page.inner_text("#close")
            report["errors"] = errors

            ctx.close()
            # 減少動態：不應有動畫
            ctx2 = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
            p2 = ctx2.new_page()
            p2.goto(base + "/stock.html?code=6488")
            p2.wait_for_selector("#content:not([hidden])")
            p2.wait_for_timeout(120)
            report["reduced_motion_animations"] = p2.evaluate("document.getAnimations().length")
            ctx2.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
