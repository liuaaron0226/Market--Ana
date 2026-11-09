"""首頁範例功能回歸：重排版後，原有點擊入口是否仍能用。隨機埠、不碰 8897。"""
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import live_server  # noqa: E402
from shoot import make_handler  # noqa: E402

server = live_server.MarketHTTPServer(("127.0.0.1", 0), make_handler(HERE / "web-bold"))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
out: dict[str, object] = {}
panel_open = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'"
with sync_playwright() as pw:
    br = pw.chromium.launch(headless=True)
    for w, h in ((1440, 900), (390, 844)):
        p = br.new_page(viewport={"width": w, "height": h}, reduced_motion="reduce")
        errs: list[str] = []
        p.on("pageerror", lambda e: errs.append(str(e)))
        p.goto(base + "/minimal.html"); p.wait_for_selector("#mainChart canvas"); p.wait_for_timeout(600)
        r: dict[str, object] = {}
        for name, sel in (("收復線", "#lineUpper"), ("防守線", "#lineLower"), ("結論依據", "#conclusionTrigger"),
                          ("直白分析", "#readCard"), ("籌碼卡", ".chip-card")):
            loc = p.locator(sel).first
            if not loc.is_visible():
                r[name] = "not-visible"; continue
            loc.scroll_into_view_if_needed(); loc.click(); p.wait_for_timeout(250)
            r[name] = "opens-panel" if p.evaluate(panel_open) else "NO-PANEL"
            p.keyboard.press("Escape"); p.wait_for_timeout(200)
        p.fill("#stockSearchInput", "2330"); p.press("#stockSearchInput", "Enter")
        try:
            p.wait_for_selector("#stockSearchResults li", timeout=8000)
        except Exception:
            pass
        r["搜尋結果數"] = p.locator("#stockSearchResults li").count()
        more = p.locator(".hm-tab-more")
        r["更多選單可見"] = more.count() and more.is_visible()
        if more.count() and more.is_visible():
            opts = more.locator("option").all_inner_texts()
            more.scroll_into_view_if_needed(); more.select_option(index=1, timeout=5000); p.wait_for_timeout(300)
            r["更多選單"] = {"選項數": len(opts) - 1, "選到後高亮": "is-picked" in (more.get_attribute("class") or ""),
                         "分頁已切換": p.evaluate("!!document.querySelector('[role=tab][aria-selected=true][hidden]')")}
        r["errors"] = errs
        out[str(w)] = r
        p.close()
    br.close()
server.shutdown()
print(json.dumps(out, ensure_ascii=False, indent=1))
