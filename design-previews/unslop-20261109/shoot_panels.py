"""首頁範例：把每個點開的詳情面板拍下來。隨機埠、不碰 8897。"""
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa: E402
from shoot import make_handler  # noqa: E402

OUT = HERE / "shots-panels"
OUT.mkdir(exist_ok=True)
folder = sys.argv[1] if len(sys.argv) > 1 else "web-bold"
tag = sys.argv[2] if len(sys.argv) > 2 else "after"
server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(HERE / folder))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
TARGETS = [("price", "#priceSummary"), ("conclusion", "#conclusionTrigger"), ("line", "#lineUpper"),
           ("plain", "#readCard"), ("chip", ".chip-card")]
with sync_playwright() as pw:
    br = pw.chromium.launch(headless=True)
    for w, h in ((1440, 900), (390, 844)):
        p = br.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
        p.goto(base + "/minimal.html"); p.wait_for_selector("#mainChart canvas"); p.wait_for_timeout(600)
        for name, sel in TARGETS:
            loc = p.locator(sel).first
            if not loc.is_visible():
                continue
            loc.scroll_into_view_if_needed(); loc.click(); p.wait_for_timeout(400)
            p.screenshot(path=str(OUT / f"{name}-{w}-{tag}.png"))
            # 第二層分頁（方法、來源…）只拍第一個可見的
            nav = p.locator("#panelNav button:visible").first
            if name == "conclusion" and nav.count():
                nav.click(); p.wait_for_timeout(300)
                p.screenshot(path=str(OUT / f"{name}-layer-{w}-{tag}.png"))
            p.keyboard.press("Escape"); p.wait_for_timeout(250)
        p.close()
    br.close()
server.shutdown()
print("ok", sorted(x.name for x in OUT.glob(f"*-{tag}.png")))
