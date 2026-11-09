"""unslop 範例對照截圖：原版 web/ vs 範例 design-previews/unslop-20261109/web/。

隨機埠、不碰 8897；/api/live 回本地假回應，不對外請求；其他 API 沿用 live_server 唯讀路徑。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402

SHOTS = HERE / "shots"
VARIANTS = {"before": ROOT / "web", "after": HERE / "web"}


def make_handler(web_dir: Path):
    class PreviewHandler(live_server.Handler):
        def __init__(self, *args, **kwargs):
            super(live_server.Handler, self).__init__(*args, directory=str(web_dir), **kwargs)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/api/live"):
                self._send_json({"ok": False, "reason": "preview-offline"})
                return
            super().do_GET()

        def log_message(self, *args):
            pass

    return PreviewHandler


PAGES = {
    "home": "/minimal.html",
    "stock": "/stock.html?code=6488",
}
SIZES = {"1440": (1440, 900), "390": (390, 844)}


def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    report: dict[str, object] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for variant, web_dir in VARIANTS.items():
            server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(web_dir))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for page_name, path in PAGES.items():
                    for size_name, (w, h) in SIZES.items():
                        for theme in ("light", "dark"):
                            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme)
                            page = ctx.new_page()
                            errors: list[str] = []
                            page.on("pageerror", lambda e: errors.append(str(e)))
                            page.add_init_script(
                                f"try{{localStorage.setItem('theme','{theme}')}}catch(e){{}};"
                                f"document.addEventListener('DOMContentLoaded',()=>document.documentElement.dataset.theme='{theme}')"
                            )
                            page.goto(base + path, wait_until="networkidle")
                            page.evaluate(f"document.documentElement.dataset.theme='{theme}'")
                            page.wait_for_timeout(900)
                            name = f"{page_name}-{size_name}-{theme}-{variant}.png"
                            page.screenshot(path=str(SHOTS / name), full_page=True)
                            overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                            report[name] = {"errors": errors, "h_overflow": overflow}
                            ctx.close()
            finally:
                server.shutdown()
                server.server_close()
        browser.close()
    (SHOTS / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
