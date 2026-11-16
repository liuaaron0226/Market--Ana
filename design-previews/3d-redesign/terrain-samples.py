"""資金地形樣張（立體重設計 B 包第 2 片）：把 terrain-samples.html 用不同的配色與入口各拍一張，給本人判定。

資料是測試夾具 cloud/test/fixtures/rotation-terrain.json（已發布的 34 個產業），用真的版面計算與畫面程式畫，不是色票。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/terrain-samples.py
輸出到 design-previews/3d-redesign/shots/terrain-samples/（PNG 不入版控）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "shots" / "terrain-samples"
PAGE = (HERE / "terrain-samples.html").as_uri()
SAMPLE = json.loads((ROOT / "cloud/test/fixtures/rotation-terrain.json").read_text(encoding="utf-8"))
PALETTES = {"site": "A 站內現行色", "ink": "B 墨色四階", "duo": "C 藍與沙"}
ENTRIES = {"head": "1 標題列的鍵", "row": "2 地形下方三顆鍵", "corner": "3 地形角落的圖示鍵"}
SIZES = {"1440": (1440, 900), "390": (390, 844)}


def shot(browser, name: str, size: str, query: str, clip_height: int | None = None) -> Image.Image:
    width, height = SIZES[size]
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce", locale="zh-TW",
                                  has_touch=size == "390")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(f"window.SAMPLE = {json.dumps(SAMPLE, ensure_ascii=False)};")
    page.goto(f"{PAGE}?{query}")
    page.wait_for_selector(".terrain-city.is-grown", state="attached")  # 街區容器本身寬高是 0，只能等它存在
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_timeout(500)
    block = page.locator(".screen-block").bounding_box()
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": width, "height": clip_height or min(height, block["y"] + block["height"] + 24)})
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    if errors or overflow:
        print(name, "errors", errors, "overflow", overflow)
    context.close()
    return Image.open(path).convert("RGB")


def side(images: list[Image.Image], gap: int = 24) -> Image.Image:
    height = max(image.height for image in images)
    sheet = Image.new("RGB", (sum(image.width for image in images) + gap * (len(images) - 1), height), (110, 110, 110))
    x = 0
    for image in images:
        sheet.paste(image, (x, 0))
        x += image.width + gap
    return sheet


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for palette in PALETTES:
            desktop = [shot(browser, f"palette-{palette}-1440-{theme}", "1440", f"palette={palette}&theme={theme}") for theme in ("light", "dark")]
            side(desktop).save(OUT / f"sheet-palette-{palette}-desktop.png")
            mobile = [shot(browser, f"palette-{palette}-390-{theme}", "390", f"palette={palette}&theme={theme}") for theme in ("light", "dark")]
            picked = shot(browser, f"palette-{palette}-1440-light-picked", "1440", f"palette={palette}&theme=light&pick=電子零組件業")
            side([*mobile, picked]).save(OUT / f"sheet-palette-{palette}-mobile-and-picked.png")
        for entry in ENTRIES:
            images = [shot(browser, f"entry-{entry}-1440", "1440", f"entry={entry}"), shot(browser, f"entry-{entry}-390", "390", f"entry={entry}")]
            side(images).save(OUT / f"sheet-entry-{entry}.png")
        browser.close()
    print(f"樣張存到 {OUT}")


if __name__ == "__main__":
    main()
