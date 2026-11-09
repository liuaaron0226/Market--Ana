"""P2 本機瀏覽器旅程：隨機埠、暫存 SQLite，不碰既有使用者資料與 8897。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402

OUT = ROOT / ".scratch/chart-screening-workbench/logs/p2-slice1-shots"
OUT.mkdir(parents=True, exist_ok=True)

securities = {
    "0050": {"code": "0050", "name": "元大台灣50", "market": "上市", "in_universe": True},
    "1101": {"code": "1101", "name": "台泥", "market": "上市", "in_universe": False},
    "2330": {"code": "2330", "name": "台積電", "market": "上市", "in_universe": True},
}
prices = {
    "0050": {"price": 63.2, "as_of": "2026-11-09", "source": "驗收盤後資料"},
    "1101": {"price": 40.0, "as_of": "2026-11-09", "source": "驗收盤後資料"},
    "2330": {"price": 600.0, "as_of": "2026-11-09", "source": "驗收盤後資料"},
}

with tempfile.TemporaryDirectory() as temp:
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
    server.portfolio_db = Path(temp) / "portfolio.sqlite"
    server.portfolio_resolver = lambda code: securities.get(code)
    server.portfolio_prices = lambda codes: {code: prices[code] for code in codes if code in prices}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report: dict[str, object] = {"base": base, "checks": [], "errors": []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            page.set_default_timeout(60_000)
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(base + "/portfolio.html", wait_until="networkidle")

            page.locator("#watchCode").fill("2330")
            page.locator("#watchHorizon").select_option("short")
            page.locator("#watchlistForm button[type=submit]").click()
            page.get_by_text("2330 台積電").wait_for()
            page.locator("#watchCode").fill("0050")
            page.locator("#watchHorizon").select_option("long")
            page.locator("#watchlistForm button[type=submit]").click()
            page.get_by_text("0050 元大台灣50").wait_for()

            page.locator("#holdingCode").fill("1101")
            page.locator("#holdingShares").fill("2000")
            page.locator("#holdingCost").fill("35")
            page.locator("#holdingNote").fill("長期觀察")
            page.locator("#holdingForm button[type=submit]").click()
            page.get_by_text("1101 台泥").wait_for()
            holding_text = page.locator("#holdingRows").inner_text()
            assert "10,000" in holding_text and "+14.29%" in holding_text, holding_text
            assert "不在本站分析範圍" in holding_text
            assert page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.screenshot(path=str(OUT / "portfolio-desktop.png"), full_page=True)
            report["checks"].append("desktop:add-watchlist:add-holding:pnl:outside-scope")

            # 只在點進個股後才新增搜尋紀錄。
            page.goto(base + "/minimal.html", wait_until="networkidle")
            page.wait_for_timeout(500)
            assert not report["errors"], report["errors"]
            page.locator("#stockSearchInput").fill("2330")
            page.locator('#stockSearchForm button[type="submit"]').click()
            search_result = page.locator('#stockSearchResults a[data-code="2330"]')
            search_result.wait_for()
            search_result.click()
            page.wait_for_url("**/stock.html?code=2330")
            page.goto(base + "/minimal.html", wait_until="networkidle")
            page.locator("#stockSearchInput").focus()
            page.locator("#stockSearchSuggestions").wait_for(state="visible")
            page.locator(".stock-suggest-head h3").nth(2).wait_for()
            headings = page.locator(".stock-suggest-head h3").all_text_contents()
            assert headings == ["最近搜尋", "短期", "長期"], headings
            assert "2330 台積電" in page.locator(".stock-suggest-group").nth(0).inner_text()
            page.screenshot(path=str(OUT / "search-suggestions-desktop.png"), full_page=False)
            report["checks"].append("search-order:recent-short-long")

            page.route("**/api/screens**", lambda route: route.abort())
            page.goto(base + "/minimal.html", wait_until="networkidle")
            page.locator("#stockSearchInput").focus()
            page.locator(".stock-suggest-head h3").nth(2).wait_for()
            assert "2330 台積電" in page.locator(".stock-suggest-group").nth(0).inner_text()
            assert "系統短期名單暫時無法讀取" in page.locator(".stock-suggest-group").nth(1).inner_text()
            assert "2330 台積電" in page.locator(".stock-suggest-group").nth(1).inner_text()
            assert "0050 元大台灣50" in page.locator(".stock-suggest-group").nth(2).inner_text()
            page.unroute("**/api/screens**")
            report["checks"].append("suggestions:partial-failure-keeps-personal-data")

            page.set_viewport_size({"width": 375, "height": 812})
            page.goto(base + "/portfolio.html", wait_until="networkidle")
            assert page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0
            page.screenshot(path=str(OUT / "portfolio-375.png"), full_page=True)
            page.get_by_label("從自選股刪除 2330 台積電").click()
            page.get_by_text("2330 台積電").wait_for(state="detached")
            assert "2330 台積電" not in page.locator("#watchlistRows").inner_text()
            report["checks"].append("mobile-375:no-overflow:delete-watchlist")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

report["screenshots"] = [str(path) for path in sorted(OUT.glob("*.png"))]
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
if report["errors"]:
    raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
