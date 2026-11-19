"""產業強勢股樣品的截圖（可丟棄）。自己開一個只服務工作樹的靜態伺服器，拍完就關。
python design-previews/rotation-strong/shoot.py
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "shots"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/design-previews/rotation-strong/sample.html"
    overflow = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, kw in (("desktop", {"viewport": {"width": 1100, "height": 900}}),
                         ("mobile", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "has_touch": True,
                                     "is_mobile": True})):
            page = browser.new_page(**kw)
            for variant in ("a", "b", "c"):
                page.goto(f"{base}?v={variant}")
                page.wait_for_timeout(2600)
                overflow[f"{variant}-{size}"] = page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth")
                page.screenshot(path=str(OUT / f"{variant}-{size}.png"), full_page=True)
            # 點樓的明細（三種排法都有）：點成交額最大的那一棟
            page.goto(f"{base}?v=c")
            page.wait_for_timeout(2600)
            page.locator('.terrain-tower[data-rank="0"]').focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / f"c-{size}-picked.png"), full_page=True)
            page.close()
        browser.close()
    server.shutdown()
    print(overflow)


if __name__ == "__main__":
    main()
