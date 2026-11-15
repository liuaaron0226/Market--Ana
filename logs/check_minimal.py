from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, Route, sync_playwright

ROOT = Path("C:/Users/User/Desktop/Market--Ana")
PAGE_FILE = ROOT / "web/minimal.html"
SERVER_FILE = ROOT / "live_server.py"
OUT = ROOT / "logs/visual-preview"
HOST = "127.0.0.1"
PORT = int(os.environ.get("MARKET_ANA_PORT", "8876"))
BASE_URL = f"http://{HOST}:{PORT}"
PAGE_URL = f"{BASE_URL}/minimal.html"
FONT_PATTERN = re.compile(r"^https://fonts\..*")


def read_assignment(path: Path, prefix: str) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8").strip()
    assert raw.startswith(prefix), f"{path.name} 缺少 {prefix}"
    return json.loads(raw[len(prefix):].rstrip(";"))


ANALYSIS = read_assignment(ROOT / "web/analysis-data.js", "window.MARKET_ANALYSIS = ")
PLAIN = json.loads((ROOT / "web/plain-data.js").read_text(encoding="utf-8").strip()[len("window.MARKET_PLAIN = "):].rstrip(";"))
DASHBOARD = read_assignment(ROOT / "web/dashboard-data.js", "window.MARKET_DASHBOARD = ")
UPPER = float(DASHBOARD["thresholds"]["upper"])
LOWER = float(DASHBOARD["thresholds"]["lower"])
DEFAULT_SAMPLE = DASHBOARD.get("default_sample") or ANALYSIS["backtest"]["as_of"]["default_sample"]
TARGET_MATCH = re.search(r"\d{4}-\d{2}-\d{2}", str(DASHBOARD["forecast"].get("horizon", "")))
TARGET_DAY = TARGET_MATCH.group(0) if TARGET_MATCH else str(DASHBOARD["forecast"]["expiry"])[:10]


def get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def existing_server() -> bool:
    try:
        payload = get_json(f"{BASE_URL}/health")
    except Exception:
        return False
    assert payload.get("web_root") == str(ROOT / "web"), f"{PORT} 已被其他服務占用；不終止別人的行程"
    return True


def wait_for_server(process: subprocess.Popen[Any], timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"live_server.py 提前結束，exit={process.returncode}")
        try:
            payload = get_json(f"{BASE_URL}/health", timeout=1.0)
            assert payload.get("web_root") == str(ROOT / "web")
            return
        except Exception as error:
            last_error = error
            time.sleep(0.2)
    raise AssertionError(f"本機服務未啟動：{last_error}")


def stop_own_server(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def fake_live_payload() -> dict[str, Any]:
    price = (UPPER + LOWER) / 2
    previous_close = float(DASHBOARD["latest"]["close"])
    now = datetime.now().astimezone()
    stamp = now.isoformat(timespec="seconds")
    prices = [
        price - (UPPER - LOWER) * 0.16,
        price - (UPPER - LOWER) * 0.08,
        price,
        price + (UPPER - LOWER) * 0.05,
        price + (UPPER - LOWER) * 0.02,
    ]
    series = []
    for index, value in enumerate(prices):
        series.append({
            "timestamp": f"{TARGET_DAY}T09:{index + 1:02d}:00+08:00",
            "time": f"09:{index + 1:02d}",
            "price": value,
            "volume": (index + 1) * 100,
        })
    return {
        "ok": True,
        "source": "臺灣證券交易所基本市況報導（MIS）",
        "fetched_at": stamp,
        "served_at": stamp,
        "data_time": stamp,
        "market_open": True,
        "market_phase": "open",
        "live": True,
        "delay": "MIS 主報價至少間隔 5 秒，分時至少間隔 15 秒。",
        "delay_seconds": {"quote": 5, "chart": 15},
        "error": None,
        "warning": None,
        "last_success_at": stamp,
        "retry_after_seconds": 0,
        "data": {
            "price": price,
            "previous_close": previous_close,
            "change": price - previous_close,
            "change_percent": (price / previous_close - 1) * 100,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "turnover_value": float(DASHBOARD["latest"]["turnover"]) * 100_000_000 * 0.35,
            "turnover_billion": float(DASHBOARD["latest"]["turnover"]) * 0.35,
            "trade_volume": 1,
            "transactions": 1,
            "source_fields": {"quote": {}, "chart": {}},
        },
        "series": series,
    }


def block_fonts(page: Page) -> None:
    page.route(FONT_PATTERN, lambda route: route.abort())


def route_live(page: Page) -> None:
    payload = fake_live_payload()

    def fulfill(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/live", fulfill)


def assert_no_overflow(page: Page, width: int) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{width}px 有橫向溢出"


def click_visible_candle(page: Page) -> dict[str, Any]:
    info = page.evaluate(
        """
        (() => {
          const chart = window.MinimalApp.chart;
          const index = Math.max(chart.view.end - 3, chart.view.end - chart.view.count);
          const rect = chart.canvas.getBoundingClientRect();
          const x = rect.left + chart.geo.l + chart.geo.step * (index - chart.geo.start + .5);
          const y = rect.top + Math.min(100, rect.height * .25);
          return {index, x, y, candle: chart.candles[index]};
        })()
        """
    )
    page.mouse.click(info["x"], info["y"])
    page.locator("#detailPanel[data-kind='candle']").wait_for(state="visible")
    return info


def assert_candle_panel(page: Page, candle: dict[str, Any]) -> None:
    for field in ("open", "high", "low", "close"):
        actual = float(page.locator(f"[data-ohlc='{field}']").get_attribute("data-raw"))
        assert abs(actual - float(candle[field])) < 1e-8, f"K棒面板 {field} 不等於資料"


def check_actual_service(browser: Browser) -> None:
    page = browser.new_page(viewport={"width": 820, "height": 900}, color_scheme="dark")
    block_fonts(page)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    page.wait_for_function("document.getElementById('marketState').textContent !== '讀取中'", timeout=25_000)
    assert page.locator("#marketState").inner_text() in {"即時", "延遲", "休市"}
    assert page.locator("#mainChart canvas").count() == 1
    assert not errors, errors
    page.close()


def check_standard_width(browser: Browser, width: int, height: int, screenshots: list[str]) -> dict[str, Any]:
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme="light")
    block_fonts(page)
    route_live(page)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    page.wait_for_function("document.getElementById('marketState').textContent.startsWith('即時')")

    assert page.locator("h1").inner_text() == "台股加權指數"
    assert page.locator("#mainChart canvas").count() == 1
    canvas_box = page.locator("#mainChart canvas").bounding_box()
    upper_box = page.locator("#lineUpper").bounding_box()
    lower_box = page.locator("#lineLower").bounding_box()
    assert canvas_box and canvas_box["height"] >= 44
    if width > 760:
        assert page.evaluate("window.MinimalApp.chart.geo.step >= 44"), "桌面／平板單根K棒命中寬度不足44px"
    assert upper_box and upper_box["height"] >= 44 and lower_box and lower_box["height"] >= 44
    assert page.locator("#marketState").inner_text().startswith("即時")
    assert page.locator("#conclusionText").evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 17
    conclusion_box = page.locator("#conclusionText").evaluate("el => ({scroll:el.scrollHeight, client:el.clientHeight, overflow:getComputedStyle(el).overflow, text:el.textContent})")
    assert conclusion_box["scroll"] <= conclusion_box["client"] + 1, f"結論句被截斷：{conclusion_box}"
    assert "詳情" in page.locator("#currentPoint").inner_text()
    assert page.locator("#analysisDate").get_attribute("title") == ANALYSIS["meta"]["cutoff_date"]
    assert page.locator("#analysisDate").inner_text() == ANALYSIS["meta"]["cutoff_date"][5:].replace("-", "/")
    assert page.locator("#scenarioDate").get_attribute("title") == TARGET_DAY
    assert page.locator("#scenarioDate").inner_text() == TARGET_DAY[5:].replace("-", "/")
    trend = page.evaluate("window.MinimalApp.trend")
    assert trend["schema"] == "market-trend/v1"
    assert trend["data_date"] == ANALYSIS["meta"]["cutoff_date"]
    assert [row["key"] for row in trend["periods"]] == ["short", "medium", "long"]
    assert page.locator("#marketTrendRows .trend-row").count() == 3
    assert page.locator("#marketTrendDate").inner_text() == f"資料日 {trend['data_date']}"
    study_labels = page.locator("#chartStudies .kc-study-choice").all_inner_texts()
    assert study_labels == ["成交金額", "MACD", "KD", "RSI"], study_labels
    for kind in ("kd", "rsi", "volume"):
        button = page.locator(f'#chartStudies [data-study="{kind}"]')
        button.click()
        assert button.get_attribute("aria-pressed") == "true"
        assert page.locator("#chartStudies .kc-study-pane:not([hidden])").count() == 1
        assert page.locator(f"#chartStudies .kc-study-pane-{kind}").is_visible()
    assert page.locator(".chip-card").count() == 6, "籌碼卡應有 6 張"
    assert page.locator("#readLine").inner_text() == PLAIN["one_line"]
    expected_n = int(ANALYSIS["backtest"]["results"][DEFAULT_SAMPLE]["next_day_outcomes"]["sample_count"])
    assert_no_overflow(page, width)

    if width == 1440:
        chart_box = page.locator("#chartStage").bounding_box()
        assert chart_box
        assert chart_box["y"] >= 0 and chart_box["y"] + chart_box["height"] <= height, "主K線未在 1440×900 首屏內"

    if width in (375, 1440):
        shot = OUT / f"minimal-{width}.png"
        page.screenshot(path=str(shot), full_page=False)
        screenshots.append(str(shot))

    if width == 1440:
        candle = click_visible_candle(page)
        assert_candle_panel(page, candle["candle"])
        page.wait_for_timeout(400)
        candle_panel_box = page.locator("#detailPanel").bounding_box()
        assert candle_panel_box
        # 本人 2026-11-06 更正：桌面細部視窗置中
        center_x = candle_panel_box["x"] + candle_panel_box["width"] / 2
        center_y = candle_panel_box["y"] + candle_panel_box["height"] / 2
        assert abs(center_x - 720) <= 2 and abs(center_y - 450) <= 2, f"桌面面板沒有置中：{center_x},{center_y}"
        assert "這代表" in page.locator("#panelBody").inner_text(), "面板缺白話條列"
        page.locator("#panelClose").click()

        page.locator("#lineLower").click()
        page.wait_for_timeout(280)
        expected = DASHBOARD["actions"]["續弱觀察"]
        assert page.locator("#actionFlat").inner_text() == expected["flat"]
        assert page.locator("#actionHolding").inner_text() == expected["holding"]
        panel_shot = OUT / "minimal-panel-line-1440.png"
        page.screenshot(path=str(panel_shot), full_page=False)
        screenshots.append(str(panel_shot))
        page.keyboard.press("Escape")
        assert page.evaluate("document.activeElement && document.activeElement.id") == "lineLower", "Esc 後焦點未回入口"

        page.locator("#lineLower").click()
        assert int(page.locator("#panelBody .ob-bar").count()) == 1, "判斷線視窗缺歷史比例條"
        page.locator("#panelBody [data-outcome-panel='continue_break']").click()
        assert "不是明天的機率" in page.locator("#panelBody").inner_text(), "分布面板缺白話提醒"
        page.locator("#panelBody .more-detail > summary").click()
        result = ANALYSIS["backtest"]["results"][DEFAULT_SAMPLE]
        assert page.locator("#histogram").count() == 1
        expected_outcome_count = int(result["next_day_outcomes"]["counts"]["continue_break"])
        assert int(page.locator("#histogram").get_attribute("data-sample-count")) == expected_outcome_count
        assert int(page.locator("#histogram").get_attribute("data-expected-count")) == expected_outcome_count
        assert page.locator("#histogram svg").count() == 1
        assert "一般交易日" in page.locator("#histogram").inner_text()
        page.keyboard.press("Escape")

        # 六組樣本、三個 outcome 都要由資料規則重算出相同分母。
        for sample in ANALYSIS["backtest"]["samples"]:
            page.locator("#lineLower").click()
            page.locator("#panelBody [data-outcome-panel='between']").click()
            page.locator("#panelBody .more-detail > summary").click()
            page.locator("#panelBody [data-deep='samples']").click()
            page.locator(f"#panelBody [data-sample='{sample['key']}']").click()
            page.keyboard.press("Escape")
            for outcome in ("stand_back", "between", "continue_break"):
                page.locator("#lineLower").click()
                page.locator(f"#panelBody [data-outcome-panel='{outcome}']").click()
                page.locator("#panelBody .more-detail > summary").click()
                expected_count = int(ANALYSIS["backtest"]["results"][sample["key"]]["next_day_outcomes"]["counts"][outcome])
                assert int(page.locator("#histogram").get_attribute("data-sample-count")) == expected_count
                assert int(page.locator("#histogram").get_attribute("data-expected-count")) == expected_count
                page.keyboard.press("Escape")

        # 第2層入口都能進入並返回，樣本數量與資料一致。
        page.locator("#conclusionTrigger").click()
        page.locator("[data-evidence='trend']").click()
        assert page.locator("#panelBack").is_visible()
        assert page.locator(".detail-chart svg").count() == 4
        page.locator("#panelBack").click()
        assert page.locator("[data-evidence='trend']").is_visible()
        page.keyboard.press("Escape")
        checks_deep = {
            "method": lambda: "回測規則" in page.locator("#panelBody").inner_text(),
            "sources": lambda: "分析截止日" in page.locator("#panelBody").inner_text(),
            "samples": lambda: page.locator("#panelBody [data-sample]").count() == len(ANALYSIS["backtest"]["samples"]),
            "table": lambda: page.locator("#panelBody table").count() == 1 and page.locator("#panelBody tbody tr").count() == page.evaluate("window.MinimalApp.chart.candles.length"),
            "news": lambda: page.locator("#panelBody .timeline article").count() == len(DASHBOARD["news"]),
        }
        for layer, ok in checks_deep.items():
            page.locator("#lineLower").click()
            page.locator("#panelBody .more-detail > summary").click()
            page.locator(f"#panelBody [data-deep='{layer}']").click()
            assert ok(), f"第2層 {layer} 內容不符"
            assert page.locator("#panelBack").is_visible()
            page.keyboard.press("Escape")
        page.locator("#lineLower").click()
        page.locator("#periodSelect").focus()
        page.keyboard.press("Tab")
        assert page.locator("#detailPanel").evaluate("panel => panel.contains(document.activeElement)"), "焦點可逃出面板"
        page.keyboard.press("Escape")

        # 盤中週期使用即時序列，趨勢週期回到盤後資料。
        page.locator("#periodSelect").select_option("m1")
        assert page.locator("#estimateBadge").is_visible()
        assert "1 分K" in page.locator("#chartContext").inner_text()
        page.locator("#periodSelect").select_option("h60")
        assert not page.locator("#estimateBadge").is_visible()
        assert "60 分K" in page.locator("#chartContext").inner_text()
        page.locator("#periodSelect").select_option("daily")

    assert not errors, f"{width}px JavaScript 錯誤：{errors}"
    result = {"width": width, "no_overflow": True, "javascript_errors": 0}
    page.close()
    return result


def check_mobile_sheet(browser: Browser, screenshots: list[str]) -> None:
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="light")
    block_fonts(page)
    route_live(page)
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    page.wait_for_function("document.getElementById('marketState').textContent.startsWith('即時')")
    page.locator("#mainChart canvas").scroll_into_view_if_needed()
    selection = page.evaluate(
        """
        (() => {
          const chart = window.MinimalApp.chart;
          const index = chart.view.end - 3;
          const rect = chart.canvas.getBoundingClientRect();
          return {
            x: rect.left + chart.geo.l + chart.geo.step * (index - chart.geo.start + .5),
            y: rect.top + Math.min(90, rect.height * .22)
          };
        })()
        """
    )
    page.mouse.move(selection["x"], selection["y"])
    page.mouse.down()
    page.mouse.move(selection["x"] + 7, selection["y"])
    page.mouse.up()
    assert page.locator("#mobileCandleDetails").is_hidden(), "位移達 7px 不應算點擊"
    page.mouse.move(selection["x"], selection["y"])
    page.mouse.down()
    page.mouse.move(selection["x"] + 5, selection["y"])
    page.mouse.up()
    assert page.locator("#mobileCandleDetails").is_visible(), "位移 5px 應選定K棒"
    assert page.locator("#detailPanel").get_attribute("aria-hidden") == "true", "手機應先選定K棒，不應立刻開面板"
    page.locator("#mobileCandleDetails").click()
    page.locator("#detailPanel[data-kind='candle']").wait_for(state="visible")
    page.wait_for_timeout(280)
    position = page.locator("#detailPanel").evaluate("el => getComputedStyle(el).position")
    assert position == "fixed"
    panel_box = page.locator("#detailPanel").bounding_box()
    marker_box = page.locator("#selectedCandleMarker").bounding_box()
    assert panel_box and marker_box
    # 本人 11-07 更正：手機浮窗也要置中（取代舊的底部面板＋露出選取 K 棒規則）。
    vw, vh = 390, 844
    assert abs(panel_box["x"] - (vw - panel_box["x"] - panel_box["width"])) <= 2, f"手機浮窗未水平置中：{panel_box}"
    assert abs(panel_box["y"] - (vh - panel_box["y"] - panel_box["height"])) <= 2, f"手機浮窗未垂直置中：{panel_box}"
    assert panel_box["x"] >= 8 and panel_box["y"] >= 8, f"手機浮窗超出可視範圍：{panel_box}"
    shot = OUT / "minimal-panel-mobile-390.png"
    page.screenshot(path=str(shot), full_page=False)
    screenshots.append(str(shot))
    max_scroll = page.locator("#panelBody").evaluate("el => el.scrollHeight - el.clientHeight")
    if max_scroll > 0:
        target_scroll = min(40, max_scroll)
        page.locator("#panelBody").evaluate("(el, y) => { el.scrollTop = y; }", target_scroll)
        page.locator("#panelBody .more-detail > summary").click()
        page.locator("#panelBody").evaluate("(el, y) => { el.scrollTop = y; }", target_scroll)
        page.locator("#panelBody [data-deep='method']").click()
        page.locator("#panelBack").click()
        page.wait_for_timeout(50)
        restored = page.locator("#panelBody").evaluate("el => el.scrollTop")
        assert abs(restored - target_scroll) <= 1, "第2層返回未保留第1層捲動位置"
    page.close()


def check_reduced_motion(browser: Browser) -> None:
    page = browser.new_page(
        viewport={"width": 820, "height": 900},
        color_scheme="dark",
        reduced_motion="reduce",
    )
    block_fonts(page)
    route_live(page)
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    page.locator("#lineUpper").click()
    panel = page.locator("#detailPanel")
    panel.wait_for(state="visible")
    assert panel.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert panel.locator("#actionFlat").is_visible()
    page.close()


def check_live_freshness_guards(browser: Browser) -> None:
    stale_page = browser.new_page(viewport={"width": 820, "height": 900}, color_scheme="dark")
    block_fonts(stale_page)
    stale = fake_live_payload()
    stale["data_time"] = (datetime.now().astimezone() - timedelta(seconds=45)).isoformat(timespec="seconds")
    stale_page.route(
        "**/api/live",
        lambda route: route.fulfill(status=200, content_type="application/json; charset=utf-8", body=json.dumps(stale, ensure_ascii=False)),
    )
    stale_page.goto(PAGE_URL, wait_until="domcontentloaded")
    stale_page.wait_for_function("document.getElementById('marketState').textContent !== '讀取中'")
    assert stale_page.locator("#marketState").inner_text() == "延遲", "舊行情時間不可標成即時"
    stale_page.close()

    closed_page = browser.new_page(viewport={"width": 820, "height": 900}, color_scheme="dark")
    block_fonts(closed_page)
    closed = fake_live_payload()
    closed["market_open"] = False
    closed["market_phase"] = "closed"
    closed["live"] = False
    closed_requests = 0

    def fulfill_closed(route: Route) -> None:
        nonlocal closed_requests
        closed_requests += 1
        route.fulfill(status=200, content_type="application/json; charset=utf-8", body=json.dumps(closed, ensure_ascii=False))

    closed_page.route("**/api/live", fulfill_closed)
    closed_page.goto(PAGE_URL, wait_until="domcontentloaded")
    closed_page.wait_for_function("document.getElementById('marketState').textContent === '休市'")
    closed_page.wait_for_function("window.MinimalApp.nextPollDelay === 0")
    assert closed_requests == 1, closed_requests
    closed_page.wait_for_timeout(1200)
    assert closed_requests == 1, "休市後不應繼續輪詢即時行情 API"
    closed_page.close()


def check_official_date_alignment(browser: Browser) -> None:
    """舊 dashboard／劇本不得蓋掉實際日 K 與分析資料日，也不得把今天冒充資料日。"""
    fixture = json.loads(json.dumps(ANALYSIS))
    fixture["meta"]["cutoff_date"] = "2026-11-10"
    fixture["daily"][-1]["date"] = "2026-11-10"
    analysis_script = "window.MARKET_ANALYSIS = " + json.dumps(fixture, ensure_ascii=False) + ";\n"
    unavailable = {"error": "live-not-available", "message": "明確盤後 fixture，不提供即時行情"}
    page = browser.new_page(viewport={"width": 820, "height": 900}, color_scheme="dark")
    block_fonts(page)
    page.route(
        "**/analysis-data.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript; charset=utf-8", body=analysis_script),
    )
    page.route(
        "**/api/live",
        lambda route: route.fulfill(status=503, content_type="application/json; charset=utf-8", body=json.dumps(unavailable, ensure_ascii=False)),
    )
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("document.getElementById('marketState').textContent === '盤後'")
    assert page.locator("#marketTime").inner_text() == "2026-11-10"
    assert page.locator("#analysisDate").inner_text() == "11/10"
    assert page.locator("#analysisDate").get_attribute("title") == "2026-11-10"
    # 本人 11-14「過期就隱藏」：劇本比資料日舊時，日期、結論、兩條價位線與直白分析都不顯示。
    for selector in ("#scenarioDate", "#conclusionTrigger", "#lineUpper", "#lineLower", "#readCard"):
        assert not page.locator(selector).is_visible(), f"過期內容仍顯示：{selector}"
    assert "已過期" not in page.locator("body").inner_text()
    assert page.locator("#marketTrendDate").inner_text() == "資料日 2026-11-10"
    page.close()


def check_file_mode(browser: Browser) -> None:
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    block_fonts(page)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(PAGE_FILE.as_uri(), wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    assert "即時服務未啟動" in page.locator("#serviceNote").inner_text()
    assert page.locator("#marketState").inner_text() == "即時服務未啟動"
    assert page.locator("#mainChart canvas").count() == 1
    assert not errors, errors
    page.close()


def check_chip_drilldown(browser: Browser) -> None:
    """本人 2026-11-06：籌碼卡圖上每一天都能點，顯示該天前 20 名。"""
    detail = read_assignment(ROOT / "web/detail-data.js", "window.MARKET_DETAIL = ")
    first_key = {"trust": "trust_buy", "foreign": "foreign_buy", "margin": "margin_up", "short": "short_up", "volume": "turnover_top", "futures": "foreign_sell"}
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    block_fonts(page)
    route_live(page)
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    for key, field in first_key.items():
        page.locator(f".chip-card[data-chip='{key}']").click()
        # 11-07 起細部預設只給「目前前 10」，每天明細要切過去。
        assert page.locator("#nowBlock .now-table").count() == 1, f"{key} 預設應只有一張目前前 10"
        page.locator("#panelBody .chip-seg[data-seg=view] [data-value=daily]").click()
        bars = page.locator("#panelBody .bar-hit")
        assert bars.count() == 20, f"{key} 應有 20 天可點"
        for index in (0, bars.count() - 1):
            bars.nth(index).click()
            day = bars.nth(index).get_attribute("data-day")
            expected = detail["by_date"][day][field]
            first_row = page.locator("#drillDetail tbody tr").first.inner_text()
            assert expected and expected[0]["name"] in first_row, f"{key} {day} 第一名不符"
            assert page.locator("#drillDetail tbody tr").count() >= min(20, len(expected)), f"{key} {day} 列數不足"
        page.keyboard.press("Escape")
    page.close()


def check_dark(browser: Browser, screenshots: list[str]) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="dark")
    block_fonts(page)
    route_live(page)
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
    # 本人 2026-11-06 選 B 淺色卡片：預設淺色，深色只在按切換鈕後出現
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(243, 241, 236)", "預設應為淺色"
    page.locator("#themeToggle").click()
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert background == "rgb(11, 11, 14)", f"深色背景不正確：{background}"
    shot = OUT / "minimal-dark-1440.png"
    page.screenshot(path=str(shot), full_page=False)
    screenshots.append(str(shot))
    page.close()


def check_panel_return(browser: Browser, width: int) -> None:
    page = browser.new_page(viewport={"width": width, "height": 900})
    block_fonts(page)
    route_live(page)
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
    page.wait_for_function("document.getElementById('marketState').textContent.startsWith('即時')")
    original_view = page.evaluate("({...window.MinimalApp.chart.view})")
    page.locator("#lineUpper").click()
    page.locator("#panelBody .more-detail > summary").click()
    page.locator("#panelBody").evaluate("el => el.scrollTop = 80")
    page.locator("#panelBody [data-outcome-panel='between']").click()
    assert page.locator("#panelBack").is_visible(), "收復線進入歷史結果後缺少返回上一層"
    page.locator("#panelBody .more-detail > summary").click()
    page.locator("#panelBody [data-deep='samples']").click()
    page.locator("#panelBack").click()
    assert "歷史結果" in page.locator("#panelTitle").inner_text()
    assert page.locator("#panelBody .more-detail").get_attribute("open") is not None
    page.locator("#panelBack").click()
    assert page.locator("#panelTitle").inner_text() == "收復線"
    assert page.locator("#panelBody .more-detail").get_attribute("open") is not None
    page.locator("#panelBack").click()
    assert page.locator("#detailPanel").get_attribute("aria-hidden") == "true"
    assert page.evaluate("document.activeElement.id") == "lineUpper"
    assert page.evaluate("({...window.MinimalApp.chart.view})") == original_view, "返回主圖改變了縮放或歷史位置"

    page.locator(".chip-card[data-chip='trust']").click()
    page.locator("#panelBody .chip-seg[data-seg=view] [data-value=daily]").click()
    page.locator("#panelBody .bar-hit").first.click()
    day = page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day")
    # Exercise the same deep-layer event used by panel detail controls.
    page.locator("#panelNav [data-layer='method']").dispatch_event("click")
    page.locator("#panelBack").click()
    assert page.locator("#panelBody .bar-hit.is-on").get_attribute("data-day") == day
    page.locator("#panelBack").click()
    page.locator("#lineLower").click()
    page.locator("#panelBack").wait_for(state="visible")
    assert "回到主圖" in page.locator("#panelBack").inner_text(), "關閉後不得殘留上一個面板的返回歷史"
    page.keyboard.press("Escape")
    page.close()


def check_half_window_header(browser: Browser) -> None:
    for width in (375, 760, 761, 820, 960, 1120, 1280, 1440):
        page = browser.new_page(viewport={"width": width, "height": 900})
        block_fonts(page)
        route_live(page)
        page.goto(PAGE_URL, wait_until="domcontentloaded")
        page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
        overlap = page.evaluate("""() => {
            const conclusion = document.getElementById('conclusionTrigger').getBoundingClientRect();
            return ['currentPrice', 'currentChange', 'statusTrigger', 'analysisDate', 'scenarioDate'].filter(id => {
                const r = document.getElementById(id).getBoundingClientRect();
                return Math.min(r.right, conclusion.right) - Math.max(r.left, conclusion.left) > 1 &&
                    Math.min(r.bottom, conclusion.bottom) - Math.max(r.top, conclusion.top) > 1;
            });
        }""")
        assert not overlap, f"{width}px 結論卡蓋住 {overlap}"
        assert_no_overflow(page, width)
        if width in (375, 820, 1440):
            page.screenshot(path=str(OUT / f"minimal-header-fixed-{width}.png"), full_page=False)
        page.close()


def main() -> None:
    assert PAGE_FILE.is_file(), "缺少 web/minimal.html"
    assert SERVER_FILE.is_file(), "缺少 live_server.py"
    OUT.mkdir(parents=True, exist_ok=True)

    process: subprocess.Popen[Any] | None = None
    if not existing_server():
        process = subprocess.Popen(
            [sys.executable, str(SERVER_FILE), "--port", str(PORT)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_server(process)

    screenshots: list[str] = []
    checks: list[dict[str, Any]] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width in (375, 820, 1440):
                check_panel_return(browser, width)
            check_half_window_header(browser)
            check_actual_service(browser)
            for width, height in ((375, 812), (820, 900), (1440, 900)):
                checks.append(check_standard_width(browser, width, height, screenshots))
            check_mobile_sheet(browser, screenshots)
            check_reduced_motion(browser)
            check_live_freshness_guards(browser)
            check_official_date_alignment(browser)
            check_file_mode(browser)
            check_dark(browser, screenshots)
            check_chip_drilldown(browser)
            browser.close()
    finally:
        stop_own_server(process)

    print(json.dumps({
        "widths": checks,
        "panel_return_preserves_parent_date_details_and_focus": True,
        "half_window_header_no_overlap_widths": [375, 760, 761, 820, 960, 1120, 1280, 1440],
        "actual_live_server_integration": True,
        "first_screen_1440": True,
        "candle_ohlc_matches_data": True,
        "defense_action_matches_dashboard": True,
        "distribution_matches_backtest": True,
        "escape_restores_focus": True,
        "mobile_panel_centered": True,
        "reduced_motion_content_visible": True,
        "stale_data_never_shows_live": True,
        "chip_drilldown_top20_matches_data": True,
        "closed_market_stops_live_polling": True,
        "official_date_alignment_fixture": "analysis/daily 2026-11-10; old scenario 2026-11-09",
        "market_studies": ["成交金額", "MACD", "KD", "RSI"],
        "file_service_message": True,
        "screenshots": screenshots,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
