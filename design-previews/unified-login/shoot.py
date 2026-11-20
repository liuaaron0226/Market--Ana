"""把一個入口的申請畫面樣品（sample.html，三個手機畫面並排）截成一張圖。用法：python shoot.py [輸出路徑]"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

here = pathlib.Path(__file__).resolve().parent
out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else here / "sample.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1310, "height": 760}, device_scale_factor=2, color_scheme="light")
    page.goto((here / "sample.html").as_uri())
    page.wait_for_timeout(300)
    page.locator(".shots").screenshot(path=str(out))
    browser.close()
print(out)
