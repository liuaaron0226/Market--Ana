"""首頁改版對照截圖：正式 web/ vs web-bold/。隨機埠、不碰 8897。"""
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

SHOTS = HERE / "shots-home"
VARIANTS = {"before": HERE.parents[1] / "web", "after": HERE / "web-bold"}
only = sys.argv[1:] or list(VARIANTS)


def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    report: dict[str, object] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for variant in only:
            server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(VARIANTS[variant]))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for size, (w, h) in {"1440": (1440, 900), "390": (390, 844)}.items():
                    for theme in ("light", "dark"):
                        ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme)
                        page = ctx.new_page()
                        errors: list[str] = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        page.add_init_script(f"try{{localStorage.setItem('market-theme','{theme}')}}catch(e){{}}")
                        page.goto(base + "/minimal.html", wait_until="networkidle")
                        page.wait_for_timeout(900)
                        name = f"home-{size}-{theme}-{variant}"
                        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
                        page.screenshot(path=str(SHOTS / f"{name}-fold.png"))
                        report[name] = {
                            "errors": errors,
                            "h_overflow": page.evaluate("document.documentElement.scrollWidth > innerWidth"),
                        }
                        ctx.close()
            finally:
                server.shutdown()
                server.server_close()
        browser.close()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
