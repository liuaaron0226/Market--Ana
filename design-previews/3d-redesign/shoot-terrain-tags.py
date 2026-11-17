"""資金地形名稱牌不疊（B 包 11-17 修正）的截圖：首頁產業資金流向那一塊，1440／390 × 淺色／深色。

用法：python design-previews/3d-redesign/shoot-terrain-tags.py
輸出：design-previews/3d-redesign/shots/after-B-tags/terrain-{1440,390}-{light,dark}.png（PNG 不入版控）。
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

import shoot

OUT = Path(__file__).parent / "shots" / "after-B-tags"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    proc, base = shoot.start_server("--features", "core")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for size, (width, height) in shoot.SIZES.items():
                for scheme in ("light", "dark"):
                    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                                  reduced_motion="reduce", locale="zh-TW", timezone_id="Asia/Taipei",
                                                  device_scale_factor=2)
                    page = context.new_page()
                    page.add_init_script(f"try{{localStorage.setItem('market-theme','{scheme}')}}catch(e){{}}")
                    page.clock.set_fixed_time(shoot.FIXED_NOW)
                    page.goto(base + "/")
                    page.locator("[data-cat='rotation']").click()
                    page.locator("#rotationTerrain .terrain-stage").scroll_into_view_if_needed()
                    page.wait_for_function("document.querySelector('#rotationTerrain .terrain-city').classList.contains('is-grown')")
                    page.wait_for_timeout(800)
                    page.locator("#rotationBlock").screenshot(path=str(OUT / f"terrain-{size}-{scheme}.png"))
                    context.close()
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    print(OUT)


if __name__ == "__main__":
    main()
