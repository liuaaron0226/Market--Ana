from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402

SHOTS = ROOT / ".tmp/lane-P3/screenshots"


def candles(minutes: int):
    points = [
        ("10:15", 2258, 2262, 2256, 2260, 120000),
        ("10:16", 2260, 2266, 2259, 2265, 180000),
        ("10:17", 2265, 2268, 2262, 2264, 90000),
        ("10:18", 2264, 2270, 2263, 2268, 140000),
    ]
    return [{"t": f"2026-11-10T{time}:00+08:00", "label": time, "open": op, "high": hi,
             "low": lo, "close": close, "volume": volume}
            for time, op, hi, lo, close, volume in points]


class FakeLive:
    def snapshot(self, code: str, market: str):
        if code == "6488":
            return {"ok": False, "environment": "cloud", "error": "cloud-live-unavailable",
                    "message": "雲端無法取得即時資料", "market_open": True, "live": False,
                    "retry_after_seconds": 0, "candles": {"1": [], "5": [], "15": []}}
        return {
            "ok": True, "environment": "local", "code": code, "market": market,
            "market_open": True, "market_phase": "open", "live": True,
            "quote": {"price": 2268, "previous_close": 2235, "change": 33, "change_percent": 1.48,
                      "open": 2258, "high": 2270, "low": 2256, "volume_shares": 14530000,
                      "data_time": "2026-11-10T10:18:00+08:00", "data_date": "2026-11-10"},
            "delay_seconds": {"quote": 5, "chart": 15}, "retry_after_seconds": 0,
            "candles": {"1": candles(1), "5": candles(5), "15": candles(15)},
        }


def verify_page(page, base: str, width: int, shot: Path):
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base}/stock.html?code=2330", wait_until="domcontentloaded", timeout=60_000)
    page.locator("#content").wait_for(state="visible", timeout=60_000)
    page.locator("#liveBadge").wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(1200)
    assert "2,268" in page.locator("#close").inner_text(), page.locator("#close").inner_text()
    assert page.locator("#liveStatus").inner_text() == "10:18:00 更新"
    assert page.locator("#ladderCurrentLabel").inner_text() == "現價"
    page.select_option("#periodSelect", "1m")
    page.locator("#periodSelect").dispatch_event("change")
    page.wait_for_function("document.querySelector('#stockChartContext').textContent.includes('1分K')")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.screenshot(path=str(shot), full_page=False)
    assert not errors, errors
    return {"width": width, "live": page.locator("#liveBadge").is_visible(),
            "status": page.locator("#liveStatus").inner_text(),
            "chart": page.locator("#stockChartContext").inner_text()}


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
    server.stock_live_service = FakeLive()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            for width, height, name in [(1440, 900, "stock-live-desktop.png"), (375, 812, "stock-live-375.png")]:
                context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
                page = context.new_page()
                page.route("https://fonts.**/*", lambda route: route.abort())
                report.append(verify_page(page, base, width, SHOTS / name))
                context.close()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.route("https://fonts.**/*", lambda route: route.abort())
            page.goto(f"{base}/stock.html?code=6488", wait_until="domcontentloaded", timeout=60_000)
            page.locator("#content").wait_for(state="visible", timeout=60_000)
            page.wait_for_function("document.querySelector('#liveStatus').textContent === '雲端無法取得即時資料'")
            assert not page.locator("#liveBadge").is_visible()
            report.append({"cloud_failure": page.locator("#liveStatus").inner_text(), "live": False})
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    (SHOTS.parent / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
