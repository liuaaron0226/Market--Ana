"""縮放／平移後價籤是否重疊或跑出圖外（ANALYSIS_PLAN 11-08 驗收要求）。"""
import json, sys, threading
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa
from shoot import make_handler  # noqa
server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(HERE / "web-bold"))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
PAGE = "/" + (sys.argv[1] if len(sys.argv) > 1 else "stock.html?code=6488").lstrip("/")
READY = "#content:not([hidden])" if "stock" in PAGE else "#mainChart canvas"
out = {}
check = """() => { const b = window.__kchartLabelBoxes || []; const H = (document.querySelector('#chartHost canvas') || document.querySelector('#mainChart canvas')).clientHeight;
  let overlap = false; for (let i = 1; i < b.length; i++) if (b[i].y < b[i-1].y + b[i-1].h) overlap = true;
  return { n: b.length, overlap, outside: b.some(x => x.y < 0 || x.y + x.h > H), order: b.map(x => x.label) }; }"""
with sync_playwright() as pw:
    br = pw.chromium.launch(headless=True)
    for w, h in ((1440, 900), (390, 844)):
        p = br.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
        p.goto(base + PAGE); p.wait_for_selector(READY); p.wait_for_timeout(500)
        out[f"{w}-initial"] = p.evaluate(check)
        cv = p.locator("#chartHost canvas, #mainChart canvas").first; bb = cv.bounding_box()
        cx, cy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
        p.mouse.move(cx, cy)
        for _ in range(6): p.mouse.wheel(0, -300); p.wait_for_timeout(60)
        out[f"{w}-zoom-in"] = p.evaluate(check)
        p.screenshot(path=str(HERE / "shots-bold" / f"_zoom-in-{w}-{PAGE[1:5]}.png"))
        p.mouse.move(cx, cy); p.mouse.down(); p.mouse.move(cx + 250, cy, steps=8); p.mouse.up(); p.wait_for_timeout(200)
        out[f"{w}-pan"] = p.evaluate(check)
        p.screenshot(path=str(HERE / "shots-bold" / f"_pan-{w}-{PAGE[1:5]}.png"))
        p.close()
    br.close()
server.shutdown()
print(json.dumps(out, ensure_ascii=False, indent=1))
