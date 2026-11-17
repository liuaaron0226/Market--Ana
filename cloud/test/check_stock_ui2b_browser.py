from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402

SHOT_DIR = ROOT / ".tmp/lane-UI2b/screenshots"
UNIVERSE = ROOT / "data/liquid-universe/2026-11-09.json"

LIVE = {
    "ok": True,
    "environment": "test",
    "provider": "fugle",
    "market_open": False,
    "market_phase": "closed",
    "live": False,
    "delay_seconds": {"quote": 15, "chart": 60},
    "retry_after_seconds": 0,
    "quote": {
        "price": 2210,
        "previous_close": 2190,
        "change": 20,
        "change_percent": 0.91,
        "open": 2195,
        "high": 2220,
        "low": 2185,
        "volume_shares": 18000000,
        "data_time": "2026-11-11T10:30:00+08:00",
        "data_date": "2026-11-11",
    },
    "candles": {
        "1": [], "5": [], "15": [],
        "60": [
            {"t": "2026-11-11T09:00:00+08:00", "label": "09:00", "open": 2195, "high": 2205, "low": 2185, "close": 2200, "volume": 7000000},
            {"t": "2026-11-11T10:00:00+08:00", "label": "10:00", "open": 2200, "high": 2220, "low": 2198, "close": 2210, "volume": 11000000},
        ],
    },
}


def wait_ready(page) -> None:
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
    page.wait_for_function("document.querySelector('#compareStatus')?.dataset.state === 'ok'", timeout=60_000)


def choose_period(page, value: str) -> None:
    page.locator("#periodSelect").select_option(value)
    page.locator("#periodSelect").dispatch_event("change")


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
    server.universe_path = UNIVERSE
    live_server.universe_state(UNIVERSE)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    report: dict[str, object] = {"port": server.server_port}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
                page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
                page.route("**/api/stocks/2330/live?*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(LIVE)))
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{base_url}/stock.html?code=2330", wait_until="domcontentloaded")
                wait_ready(page)

                conclusion = page.locator("#strategyConclusion").inner_text()
                assert any(action in conclusion for action in ("持有", "分批進場", "觀望", "減碼")), conclusion
                assert page.locator("#strategyDetails").get_attribute("open") is None
                assert page.locator("#compareBlock").evaluate("node => node.compareDocumentPosition(document.querySelector('#chartStudies')) & Node.DOCUMENT_POSITION_PRECEDING")
                body = page.locator("body").inner_text()
                for text in ("非買賣建議", "左右滑動切換", "價位怎麼來的", "方法說明"):
                    assert text not in body, text

                choose_period(page, "weekly")
                page.wait_for_function("document.querySelector('#stockChartContext').textContent.startsWith('週K')")
                assert "週K" in page.locator("#stockChartContext").inner_text()

                # 均線在 K 線上方那一行點名稱設定（本人 11-17；和首頁共用一組，存在 ma-lines）
                page.locator("#legendNames .legend-name.is-ma").first.click()
                inputs = page.locator(".ma-list input")
                assert inputs.evaluate_all("nodes => nodes.map(node => node.value)")[:2] == ["18", "45"]
                inputs.nth(0).fill("21")
                inputs.nth(0).dispatch_event("change")
                assert [line["period"] for line in page.evaluate("JSON.parse(localStorage.getItem('ma-lines'))")][:2] == [21, 45]
                page.reload(wait_until="domcontentloaded")
                wait_ready(page)
                assert "MA21" in page.locator("#legendNames").inner_text()
                page.evaluate("localStorage.removeItem('ma-lines')")

                choose_period(page, "daily")
                page.locator('[data-study="foreign"]').click()
                page.locator(".kc-study-pane-foreign:not([hidden])").wait_for(state="visible")
                foreign_text = page.locator(".kc-study-pane-foreign .kc-study-readout").inner_text()
                assert "外資" in foreign_text and "資料不足" not in foreign_text, foreign_text
                page.screenshot(path=str(SHOT_DIR / "stock-ui2b-desktop.png"), full_page=True)
                report["desktop"] = {"strategy": conclusion, "weekly": True, "ma_persisted": True, "foreign": foreign_text}

                page.set_viewport_size({"width": 375, "height": 812})
                choose_period(page, "60m")
                page.wait_for_function("document.querySelector('#stockChartContext').textContent.startsWith('60分K')")
                context = page.locator("#stockChartContext").inner_text()
                assert "2 根" in context, context
                overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                offenders = page.evaluate("""() => [...document.querySelectorAll('body *')].map(node => { const r = node.getBoundingClientRect(); return {tag: node.tagName, id: node.id, cls: node.className, left: r.left, right: r.right, width: r.width}; }).filter(item => item.right > innerWidth + 1 || item.left < -1).slice(0, 20)""")
                assert overflow <= 1, {"overflow": overflow, "offenders": offenders}
                page.screenshot(path=str(SHOT_DIR / "stock-ui2b-375.png"), full_page=True)
                assert not errors, errors
                report["mobile"] = {"context": context, "overflow": overflow, "errors": errors}
                page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    output = json.dumps(report, ensure_ascii=False, indent=2)
    (ROOT / ".tmp/lane-UI2b/browser-report.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
