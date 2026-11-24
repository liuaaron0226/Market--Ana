"""對照標準的截圖：本人 2026-11-24 驗收通過的「AI 產業鏈資金版圖」樣品 v4，全景、點開一層、點開環節，加上展開過程的連續畫面。

樣品檔不入版控；先複製一份到 .tmp/site-design-upgrade/ref/chain-map-v4.html，並用 http.server 開在 5311（見 .claude/launch.json）。
用法：python design-previews/site-design-upgrade/sample_capture.py
輸出：.scratch/site-design-upgrade/shots/sample-*.png、frames/sample-*.png
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch" / "site-design-upgrade"
URL = "http://127.0.0.1:5311/ref/chain-map-v4.html"
SIZES = {
    "1280": dict(viewport={"width": 1280, "height": 820}, device_scale_factor=1),
    "390": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True),
}


def burst(page, name: str, times=(0, 80, 160, 260, 400, 700)) -> None:
    start = time.perf_counter()
    for index, ms in enumerate(times):
        wait = ms / 1000 - (time.perf_counter() - start)
        if wait > 0:
            page.wait_for_timeout(wait * 1000)
        page.screenshot(path=str(OUT / "frames" / f"{name}-{index}.png"))


def main() -> None:
    (OUT / "shots").mkdir(parents=True, exist_ok=True)
    (OUT / "frames").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for size, options in SIZES.items():
            context = browser.new_context(color_scheme="light", locale="zh-TW", **options)
            page = context.new_page()
            # 樣品的字型來自 Google Fonts；無頭瀏覽器等不到時截圖會卡住，所以直接略過（字形會換成系統字，動態不受影響）
            page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
            page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
            page.goto(URL, wait_until="domcontentloaded")
            burst(page, f"sample-enter-{size}", (0, 120, 240, 400, 600, 900))
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / "shots" / f"sample-0-overview-{size}.png"))
            box = page.locator("#cv").bounding_box()
            # 點占比最大的那一層（畫面中間偏下那一塊橘色的）：直接點畫布高度 45% 的位置
            x, y = box["x"] + box["width"] * .4, box["y"] + box["height"] * .47
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.up()
            burst(page, f"sample-open-layer-{size}")
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "shots" / f"sample-1-layer-{size}.png"))
            # 點展開後的第一個大環節
            page.mouse.move(box["x"] + box["width"] * .45, box["y"] + box["height"] * .5)
            page.mouse.down()
            page.mouse.up()
            burst(page, f"sample-open-node-{size}")
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "shots" / f"sample-2-node-{size}.png"))
            # 拖月份帶：由最右邊拖到中間，邊拖邊拍
            track = page.locator("#track").bounding_box()
            ty = track["y"] + 20
            page.mouse.move(track["x"] + track["width"] - 14, ty)
            page.mouse.down()
            start = time.perf_counter()
            for step in range(6):
                page.mouse.move(track["x"] + track["width"] * (1 - .1 * (step + 1)), ty, steps=4)
                page.screenshot(path=str(OUT / "frames" / f"sample-scrub-{size}-{step}.png"))
            page.mouse.up()
            page.wait_for_timeout(500)
            # 收合一層（Esc 或點標題列）
            page.keyboard.press("Escape")
            burst(page, f"sample-close-node-{size}")
            context.close()
            print("sample", size, "done", flush=True)
        browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
