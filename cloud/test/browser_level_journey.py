"""G4-2W 水位頁瀏覽器旅程：暫存 SQLite、隨機埠，不碰 8897 與使用者資料。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402

OUT = ROOT / ".scratch/chart-screening-workbench/logs/lane-G4-2W-slice1-shots"
OUT.mkdir(parents=True, exist_ok=True)
securities = {"2330": {"code": "2330", "name": "台積電", "market": "上市", "in_universe": True}}
prices = {"2330": {"price": 600.0, "as_of": "2026-11-09", "source": "驗收盤後資料"}}

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
    server.portfolio_db = Path(temp) / "portfolio.sqlite"
    server.portfolio_resolver = lambda code: securities.get(code)
    server.portfolio_prices = lambda codes: {code: prices[code] for code in codes if code in prices}
    store = live_server.LocalPortfolioStore(server.portfolio_db, server.portfolio_resolver, server.portfolio_prices)
    store.save_holding({"code": "2330", "shares": 1000, "average_cost": 500, "note": "核心部位"})
    store.save_cash({"amount_twd": 400000})
    advice = {
        "schema": "level-daily/v1", "data_date": "2026-11-10", "rule_version": "cash-level-v2.1",
        "recommended_level": 90, "raw_level": 90, "international_cap_applied": False,
        "missing_signals": [], "unavailable_signals": ["vix", "sox_5d"], "warning": None,
        "score": {"total": 3, "groups": {"trend": 2, "volatility": 0, "warning": 1, "taiwan_funding": 0}, "signals": [
            {"key": "taiex_trend", "group": "trend", "raw": {"close": 20000, "ma200": 20500}, "threshold": "收盤低於 200 日均線", "points": 2, "available": True},
            {"key": "sp500_trend", "group": "trend", "raw": {"close": 7487.5, "ma200": 7330.91}, "threshold": "EODHD S&P 500 未調整收盤低於 200 日均線", "points": 0, "available": True},
            {"key": "credit_stress", "group": "warning", "raw": 0.92, "threshold": "OFR FSI Credit 20 交易日變化達近三年 90% 分位（待本人確認）", "points": 1, "available": True},
            {"key": "vix", "group": "volatility", "raw": None, "threshold": "未接", "points": 0, "available": False, "unavailable": True},
            {"key": "sox_5d", "group": "warning", "raw": None, "threshold": "未接", "points": 0, "available": False, "unavailable": True},
        ]},
        "confirmation": {"direction": "down", "days": 2, "required": 2, "target": 90},
        "wave": {"active": False, "used_tranches": []},
        "lock": {"remaining_days": 0}, "source_status": {
            "vix": {"label": "VIX", "reason": "條款限制，未接"},
            "sox_5d": {"label": "費城半導體 5 日跌幅", "reason": "條款限制，未接"},
        },
    }
    with sqlite3.connect(server.portfolio_db) as connection:
        connection.execute(
            "INSERT INTO level_daily (data_date,version,rule_version,source_cutoff,input_sha256,payload,created_at,is_current) VALUES (?,1,?,?,?,?,?,1)",
            ("2026-11-10", "cash-level-v2.1", "2026-11-10T08:30:00+08:00", "a" * 64,
             json.dumps(advice, ensure_ascii=False), "2026-11-10T00:31:00Z"),
        )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report = {"base": base, "checks": [], "errors": [], "screenshots": []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.goto(base + "/portfolio.html", wait_until="networkidle")
            page.get_by_text("目前股票水位偏低").wait_for()
            assert page.locator("#recommendedLevel").inner_text() == "90%"
            assert page.locator("#currentLevel").inner_text() == "60%"
            assert "300,000" in page.locator("#levelAmount").inner_text()
            page.locator("#levelDetails summary").click()
            assert "2/2 個交易日" in page.locator("#levelBreakdown").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0
            desktop = OUT / "portfolio-level-desktop.png"
            page.screenshot(path=str(desktop), full_page=True)
            report["screenshots"].append(str(desktop))
            report["checks"].append("desktop:recommendation-current-gap-amount-details")

            page.set_viewport_size({"width": 375, "height": 812})
            page.goto(base + "/portfolio.html", wait_until="networkidle")
            page.get_by_text("目前股票水位偏低").wait_for()
            assert page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0
            assert page.locator("#cashForm button").evaluate("node => getComputedStyle(node).minHeight") == "44px"
            mobile = OUT / "portfolio-level-375.png"
            page.screenshot(path=str(mobile), full_page=True)
            report["screenshots"].append(str(mobile))
            report["checks"].append("mobile-375:no-overflow:cash-touch-target")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
if report["errors"]:
    raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
