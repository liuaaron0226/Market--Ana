"""User-facing regression cases for the 2026-11-08 UI feedback package.

Each case drives the public browser interface on an owned ephemeral server. It never
uses port 8897, external quotes, deployment state, or the original cloud/dist tree.
"""
from __future__ import annotations

# 【已退役 2026-11-08，S3】本腳本驗的是個股頁 v1（三方案、總分、24 招或近期操作卡）。
# 本人定案「自創部分不保留」，個股頁已改用契約 v2；v2 驗收見 logs/check_search_journey.py --case all。
# 檔案保留不刪（刪除需本人確認），執行時直接說明並以非零碼結束，避免被誤當成通過。
raise SystemExit("RETIRED: v1 個股頁檢查已退役，改用 logs/check_search_journey.py --case all")

import argparse
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from check_chart_studies import OfflineHandler, ROOT, live_server

OUT = ROOT / "logs" / "ui-feedback-check.json"
SHOTS = ROOT / "logs" / "visual-preview"
LABEL_PREFIXES = ("壓力 ", "支撐 ", "防守 ", "觀察下緣 ", "觀察上緣 ")

CAPTURE_TEXT = r"""
(() => {
  const original = CanvasRenderingContext2D.prototype.fillText;
  window.__uiTextDraws = [];
  CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
    const value = String(text);
    if (["壓力 ", "支撐 ", "防守 ", "觀察下緣 ", "觀察上緣 "].some(prefix => value.startsWith(prefix))) {
      const width = this.measureText(value).width;
      const align = this.textAlign || "start";
      const left = align === "center" ? x - width / 2 : (align === "right" || align === "end") ? x - width : x;
      window.__uiTextDraws.push({ text: value, x, y, left, right: left + width, width, align, canvasWidth: this.canvas.getBoundingClientRect().width });
    }
    return original.call(this, text, x, y, maxWidth);
  };
  let KChartClass;
  Object.defineProperty(window, "KChart", {
    configurable: true,
    get: () => KChartClass,
    set: Base => {
      KChartClass = class extends Base {
        constructor(...args) {
          super(...args);
          window.__uiChart = this;
        }
      };
    }
  });
})();
"""


def label_state(page, name: str) -> dict:
    state = page.evaluate(
        """name => {
          const labels = (window.__uiTextDraws || []).slice(-5);
          const vertical = window.__kchartLabelBoxes || [];
          const verticalOverlap = vertical.some((a, i) => vertical.some((b, j) => j > i && a.y < b.y + b.h && b.y < a.y + a.h));
          const chart = window.__uiChart;
          const canvasWidth = chart?.canvas.getBoundingClientRect().width || 0;
          const plotWidth = chart?.geo?.pw || 0;
          return {
            name,
            labels,
            verticalOverlap,
            clipped: labels.filter(label => label.left < -0.5 || label.right > label.canvasWidth + 0.5).map(label => label.text),
            count: labels.length,
            plotWidth,
            canvasWidth,
            plotRatio: canvasWidth ? plotWidth / canvasWidth : 0
          };
        }""",
        name,
    )
    assert state["count"] >= 3, f"{name}: expected visible key-price labels, got {state['count']}"
    assert not state["verticalOverlap"], f"{name}: key-price labels overlap vertically"
    assert not state["clipped"], f"{name}: key-price labels are clipped after render: {state['clipped']}"
    assert state["plotRatio"] >= 0.65, (
        f"{name}: price-label gutter leaves only {state['plotRatio']:.1%} of the canvas for candles "
        f"({state['plotWidth']:.0f}/{state['canvasWidth']:.0f}px)"
    )
    return state


def market_label_state(page, name: str) -> dict:
    state = page.evaluate(
        """name => {
          const chart = window.__uiChart;
          const canvas = chart.canvas.getBoundingClientRect();
          const plotLeft = canvas.left + chart.geo.l;
          const buttons = ['lineUpper', 'lineLower'].map(id => {
            const node = document.getElementById(id), rect = node.getBoundingClientRect();
            return { id, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, text: node.innerText };
          });
          const overlap = buttons[0].top < buttons[1].bottom && buttons[1].top < buttons[0].bottom;
          return { name, plotLeft, buttons, overlap, coveringPlot: buttons.filter(button => button.right > plotLeft + 1).map(button => button.id) };
        }""",
        name,
    )
    assert not state["overlap"], f"{name}: market key-price labels overlap"
    assert not state["coveringPlot"], f"{name}: market labels cover the candle plot: {state['coveringPlot']}"
    assert all("—" not in button["text"] for button in state["buttons"]), f"{name}: market labels lost their values"
    return state


def check_labels(base: str) -> dict:
    result: dict = {"case": "labels", "width": 390, "states": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(CAPTURE_TEXT)
        page.goto(base + "/stock.html?code=3231", wait_until="networkidle")
        page.wait_for_function("!document.getElementById('content').hidden && document.getElementById('content').getAttribute('aria-busy') === 'false'")
        page.wait_for_timeout(150)
        market = None
        try:
            result["states"].append(label_state(page, "initial"))
            canvas = page.locator("#chartHost canvas")
            canvas.focus()
            page.evaluate("window.__uiTextDraws = []")
            page.keyboard.press("+")
            page.wait_for_timeout(100)
            result["states"].append(label_state(page, "zoomed"))
            page.evaluate("window.__uiTextDraws = []")
            page.keyboard.press("ArrowLeft")
            page.wait_for_timeout(100)
            result["states"].append(label_state(page, "panned"))
            assert not errors, errors

            market = browser.new_page(viewport={"width": 1440, "height": 900})
            market.add_init_script(CAPTURE_TEXT)
            market.goto(base + "/minimal.html", wait_until="networkidle")
            market.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
            market.wait_for_function("document.getElementById('serviceNote').textContent.includes('雲端版尚未提供盤中即時行情')")
            result["market_states"] = [market_label_state(market, "initial")]
            market_canvas = market.locator("#mainChart canvas")
            market_canvas.focus()
            market.keyboard.press("+")
            market.wait_for_timeout(100)
            result["market_states"].append(market_label_state(market, "zoomed"))
            market.keyboard.press("ArrowLeft")
            market.wait_for_timeout(100)
            result["market_states"].append(market_label_state(market, "panned"))
            page.locator(".sk-chart-card").screenshot(path=str(SHOTS / "ui-feedback-labels-stock-390.png"))
            market.locator(".chart-workspace").screenshot(path=str(SHOTS / "ui-feedback-labels-market-1440.png"))
            result["pass"] = True
        except Exception:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.locator(".sk-chart-card").screenshot(path=str(SHOTS / "ui-feedback-red-labels-390.png"))
            if market:
                market.locator(".chart-workspace").screenshot(path=str(SHOTS / "ui-feedback-red-labels-market-1440.png"))
            result["errors"] = errors
            result["pass"] = False
            raise
        finally:
            browser.close()
    return result


def aggregate_monthly(rows: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    for row in rows:
        month = row["date"][:7]
        if not grouped or grouped[-1]["month"] != month:
            grouped.append({
                "month": month,
                "date": row["date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row.get("volume"),
            })
            continue
        bar = grouped[-1]
        bar["date"] = row["date"]
        bar["high"] = max(bar["high"], row["high"])
        bar["low"] = min(bar["low"], row["low"])
        bar["close"] = row["close"]
        if bar["volume"] is None or row.get("volume") is None:
            bar["volume"] = None
        else:
            bar["volume"] += row["volume"]
    return grouped


def chart_ohlcv(page) -> list[dict]:
    return page.evaluate("""() => window.__uiChart.candles.map(c => ({
      date: c.t.slice(0, 10), open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume
    }))""")


def fake_live_payload() -> dict:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prices = [39880.0, 39910.0, 39895.0]
    return {
        "ok": True,
        "fetched_at": stamp,
        "served_at": stamp,
        "data_time": stamp,
        "market_open": True,
        "retry_after_seconds": 0,
        "data": {
            "price": prices[-1],
            "previous_close": 39800.0,
            "change": prices[-1] - 39800.0,
            "change_percent": (prices[-1] / 39800.0 - 1) * 100,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "turnover_billion": 100.0,
        },
        "series": [
            {"timestamp": stamp[:10] + f"T09:0{index + 1}:00+08:00", "price": price, "volume": 100 * (index + 1)}
            for index, price in enumerate(prices)
        ],
    }


def check_monthly(base: str) -> dict:
    result: dict = {"case": "monthly", "width": 390}
    with sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        try:
            market = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            market.add_init_script(CAPTURE_TEXT)
            market.goto(base + "/minimal.html", wait_until="networkidle")
            market.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
            market.wait_for_function("document.getElementById('serviceNote').textContent.includes('雲端版尚未提供盤中即時行情')")
            monthly_option = market.locator("#periodSelect option[value=monthly]")
            assert not monthly_option.evaluate("option => option.disabled"), "cloud JSON 503 must disable only minute periods, not the existing monthly history"
            market.select_option("#periodSelect", "monthly")
            market.wait_for_function("document.getElementById('periodSelect').value === 'monthly' && document.getElementById('chartContext').textContent.startsWith('月K')")
            market_expected = market.evaluate("""() => window.MARKET_ANALYSIS.monthly.map(c => ({
              date: String(c.date || c.timestamp).slice(0, 10), open: c.open, high: c.high, low: c.low, close: c.close,
              volume: c.volume == null ? null : c.volume
            }))""")
            market_actual = chart_ohlcv(market)
            assert market_actual == market_expected, "market monthly candles must retain their real OHLC history"
            market.screenshot(path=str(SHOTS / "ui-feedback-monthly-market-390.png"), full_page=False)

            # A later live-poll failure may disable minute charts, but must not evict or disable monthly history.
            poll_calls = 0
            polling = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            polling.add_init_script(CAPTURE_TEXT)

            def route_live(route):
                nonlocal poll_calls
                poll_calls += 1
                if poll_calls == 1:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(fake_live_payload()))
                else:
                    route.fulfill(status=503, content_type="application/json", body='{"error":"temporary"}')

            polling.route("**/api/live", route_live)
            polling.goto(base + "/minimal.html", wait_until="domcontentloaded")
            polling.wait_for_function("document.getElementById('marketState').textContent === '即時'")
            polling.select_option("#periodSelect", "monthly")
            polling.wait_for_function("document.getElementById('chartContext').textContent.startsWith('月K')")
            polling.wait_for_function("document.getElementById('serviceNote').textContent.includes('連線中斷')", timeout=12_000)
            assert polling.locator("#periodSelect").input_value() == "monthly"
            assert not polling.locator("#periodSelect option[value=monthly]").evaluate("option => option.disabled")
            assert polling.evaluate("window.MinimalApp.chart.unit") == "月"
            polling.close()

            stock = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            stock.add_init_script(CAPTURE_TEXT)
            stock.goto(base + "/stock.html?code=2330", wait_until="networkidle")
            stock.wait_for_function("!document.getElementById('content').hidden && document.getElementById('content').getAttribute('aria-busy') === 'false'")
            selector = stock.locator("#periodSelect")
            assert selector.count() == 1 and selector.is_visible(), "stock mobile page has no reachable period selector"
            assert selector.locator("option[value=monthly]").count() == 1, "stock mobile page has no monthly option"
            payload = stock.request.get(base + "/api/stocks/2330/analysis").json()
            expected = [{key: value for key, value in row.items() if key != "month"} for row in aggregate_monthly(payload["bars"])]
            selector.select_option("monthly")
            stock.wait_for_function("document.getElementById('periodSelect').value === 'monthly'")
            actual = chart_ohlcv(stock)
            assert actual == expected, (
                "stock monthly candles must be calendar-month OHLCV, not relabelled daily bars: "
                + json.dumps({"actual": actual, "expected": expected}, ensure_ascii=False)
            )
            assert len(actual) < len(payload["bars"]) and actual[-1]["date"] == payload["bars"][-1]["date"]
            context = stock.locator("#stockChartContext").inner_text()
            notice = stock.locator("#stockChartNotice").inner_text()
            assert payload["bars"][0]["date"] in context and payload["bars"][-1]["date"] in context
            assert "首月可能不完整" in notice and "尚未收定" in notice and payload["as_of"] in notice
            assert stock.locator("#chartStudies .kc-study-pane:visible").count() <= 1, "monthly view must never stack indicators"
            assert stock.locator("#chartStudies .kc-studies-notice").is_visible(), "monthly view must explain that the current indicator module is daily-only"
            stock.locator("#presetTabs [data-preset='MOMENTUM-V1']").click()
            stock.wait_for_function("document.getElementById('content').dataset.preset === 'MOMENTUM-V1' && document.getElementById('content').getAttribute('aria-busy') === 'false'")
            assert selector.input_value() == "monthly" and stock.evaluate("window.__uiChart.unit") == "月"
            assert "短線量價" in stock.locator("#stockChartNotice").inner_text()
            assert stock.locator("#chartStudies .kc-study-pane:visible").count() <= 1
            stock.locator(".sk-chart-card").screenshot(path=str(SHOTS / "ui-feedback-monthly-stock-390.png"))
            result.update({"market_bars": len(market_actual), "poll_calls": poll_calls, "stock_daily_bars": len(payload["bars"]), "stock_monthly_bars": len(actual), "pass": True})
        finally:
            browser.close()
    return result


def js_number(value) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def check_strategy(base: str) -> dict:
    presets = ("2TON-FIT-V1", "MOMENTUM-V1", "SWING-SAFE-V1")
    result: dict = {"case": "strategy", "width": 390, "presets": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        page.goto(base + "/stock.html?code=2330", wait_until="networkidle")
        page.wait_for_function("!document.getElementById('content').hidden && document.getElementById('content').getAttribute('aria-busy') === 'false'")
        try:
            assert page.locator("#recentPlan").is_visible(), "main presentation has no recent-operation plan"
            assert "價位看近" not in page.locator("#presetTabs").inner_text(), "strategy picker still leads with implementation parameters"
            for preset in presets:
                page.locator(f"#presetTabs [data-preset='{preset}']").click()
                page.wait_for_function("preset => document.getElementById('content').dataset.preset === preset && document.getElementById('content').getAttribute('aria-busy') === 'false'", arg=preset)
                payload = page.request.get(base + f"/api/stocks/2330/analysis?preset={preset}").json()
                text = page.locator("#recentPlan").inner_text()
                for phrase in ("目前", "可考慮介入", "不追價", "防守／失效"):
                    assert phrase in text, f"{preset}: recent plan misses {phrase}"
                summary = page.locator("#operationSummary").inner_text()
                assert payload["preset"]["name"] in summary
                assert payload["as_of"] in page.locator("#operationAsOf").inner_text()
                if payload.get("trend") == "risk":
                    assert "風險" in summary and "目前不介入" in page.locator("#planEntry").inner_text(), f"{preset}: risk classification must block an entry suggestion"
                for key in ("pressure1", "support1", "defense"):
                    value = payload["levels"].get(key)
                    if value is not None:
                        assert js_number(value) in text, f"{preset}: recent plan misses true {key} value"
                assert page.locator("#layer1").is_hidden(), "all evidence must remain collapsed by default"
                result["presets"][preset] = {"as_of": payload["as_of"], "text": text}
            etf_page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            etf_page.goto(base + "/stock.html?code=0050", wait_until="networkidle")
            etf_page.wait_for_function("!document.getElementById('content').hidden && document.getElementById('content').getAttribute('aria-busy') === 'false'")
            etf_summary = etf_page.locator("#operationSummary").inner_text()
            etf_entry = etf_page.locator("#planEntry").inner_text()
            assert "資料未到齊" not in etf_summary and "資料補齊" not in etf_entry, "ETF score-not-applicable must not be treated as missing analysis"
            assert "風險旗標不再成立" in etf_entry, "ETF entry condition must still respect its actual risk flags"
            etf_page.close()
            missing = page.request.get(base + "/api/stocks/2330/analysis?preset=2TON-FIT-V1").json()
            missing["levels"]["pressure1"] = None
            missing_page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
            missing_page.route("**/api/stocks/2330/analysis**", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(missing, ensure_ascii=False)))
            missing_page.goto(base + "/stock.html?code=2330", wait_until="networkidle")
            missing_page.wait_for_function("!document.getElementById('content').hidden && document.getElementById('content').getAttribute('aria-busy') === 'false'")
            missing_text = missing_page.locator("#recentPlan").inner_text()
            assert "資料不足" in missing_text and "壓力 0" not in missing_text, "missing levels must not become a zero-price instruction"
            missing_page.close()
            page.locator(".sk-hero").screenshot(path=str(SHOTS / "ui-feedback-strategy-390.png"))
            page.locator("#verdictButton").click()
            assert page.locator("#layer1").is_visible()
            assert page.locator("#strategyRows tr").count() == 24
            assert page.get_by_text("價位怎麼來的", exact=True).count() == 1
            result["pass"] = True
        except Exception:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS / "ui-feedback-red-strategy-390.png"), full_page=False)
            result["pass"] = False
            raise
        finally:
            browser.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("labels", "monthly", "strategy", "all"), default="all")
    args = parser.parse_args()
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), OfflineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    report: dict = {"base": "owned-ephemeral", "cases": {}}
    try:
        if args.case in ("labels", "all"):
            report["cases"]["labels"] = check_labels(base)
        if args.case in ("monthly", "all"):
            report["cases"]["monthly"] = check_monthly(base)
        if args.case in ("strategy", "all"):
            report["cases"]["strategy"] = check_strategy(base)
        report["pass"] = all(case.get("pass") for case in report["cases"].values())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS" if report["pass"] else "FAIL", OUT)


if __name__ == "__main__":
    main()
