"""國外指數切換樣品的截圖（可丟棄）。先在工作樹根目錄開 `python -m http.server 5282 --bind 127.0.0.1`，再跑：
python design-previews/overseas-index/shoot.py
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "shots"
BASE = "http://127.0.0.1:5282/design-previews/overseas-index/sample.html"


def card(page: Page, path: Path) -> None:
    box = page.locator("#stage").bounding_box()
    menu = page.locator(".chart-menu:not([hidden])")
    bottom = box["y"] + min(box["height"], 640)
    if menu.count():
        m = menu.bounding_box()
        bottom = max(bottom, m["y"] + m["height"])
    page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": page.viewport_size["width"], "height": bottom + 12},
                    full_page=True)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, kw in (("desktop", {"viewport": {"width": 1100, "height": 900}}),
                         ("mobile", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "has_touch": True,
                                     "is_mobile": True})):
            page = browser.new_page(**kw)
            for variant in ("a", "b"):
                for index in ("twii", "dji"):
                    page.goto(f"{BASE}?v={variant}&i={index}")
                    page.wait_for_timeout(500)
                    card(page, OUT / f"{variant}-{size}-{index}.png")
                if variant == "a":
                    page.click("button[aria-label^='指數']")
                    page.wait_for_timeout(500)
                    card(page, OUT / f"a-{size}-menu.png")
            # C／D 版：點圖卡標題切換（本人 11-19）；C 標題後面有淡淡的小箭頭，D 完全沒有
            for variant in ("c", "d"):
                for index in ("twii", "dji"):
                    page.goto(f"{BASE}?v={variant}&i={index}")
                    page.wait_for_timeout(500)
                    card(page, OUT / f"{variant}-{size}-{index}.png")
                page.click("#titleChip")
                page.wait_for_timeout(500)
                card(page, OUT / f"{variant}-{size}-menu.png")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
