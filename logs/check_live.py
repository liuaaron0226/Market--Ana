from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, Route, sync_playwright

ROOT = Path("C:/Users/User/Desktop/Market--Ana")
PAGE_FILE = ROOT / "web/visual-preview.html"
SERVER_FILE = ROOT / "live_server.py"
OUT = ROOT / "logs/visual-preview"
OUT.mkdir(exist_ok=True)

HOST = "127.0.0.1"
PORT = 8876
BASE_URL = f"http://{HOST}:{PORT}"
PAGE_URL = f"{BASE_URL}/visual-preview.html"
_DASH = json.loads((ROOT / "web/dashboard-data.js").read_text(encoding="utf-8").strip()[len("window.MARKET_DASHBOARD = "):].rstrip(";"))
UPPER = float(_DASH["thresholds"]["upper"])
LOWER = float(_DASH["thresholds"]["lower"])
SNAP_LABEL = f"{_DASH['market_date'][5:7]}/{_DASH['market_date'][8:10]}"
MID_PRICE = round((UPPER + LOWER) / 2, 2)
UP_PRICE = round(UPPER + 44.18, 2)


def money(value: float) -> str:
    return f"{value:,.2f}"
QUOTE_URL = (
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    "?ex_ch=tse_t00.tw&json=1&delay=0"
)
TAIPEI = ZoneInfo("Asia/Taipei")
FONT_PATTERN = re.compile(r"^https://fonts\..*")


def get_json(url: str, timeout: float = 20) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://mis.twse.com.tw/stock/",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def wait_for_server(process: subprocess.Popen, timeout: float = 12) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"live_server.py 提前結束，exit={process.returncode}")
        try:
            return get_json(f"{BASE_URL}/health", timeout=1.5)
        except Exception as error:  # server may still be binding
            last_error = error
            time.sleep(0.2)
    raise AssertionError(f"本機即時服務未啟動：{last_error}")


def direct_quote() -> dict:
    payload = get_json(f"{QUOTE_URL}&_={int(time.time() * 1000)}")
    assert payload.get("rtcode") == "0000", payload
    rows = payload.get("msgArray")
    assert isinstance(rows, list) and rows, "證交所 MIS 沒有 msgArray"
    return rows[0]


def expected_market_open(data_time: str) -> bool:
    now = datetime.now(TAIPEI)
    stamp = datetime.fromisoformat(data_time)
    return (
        stamp.date() == now.date()
        and now.weekday() < 5
        and clock_time(9, 0) <= now.time().replace(tzinfo=None) <= clock_time(13, 30)
    )


def assert_api(api: dict) -> None:
    required = {
        "ok",
        "source",
        "fetched_at",
        "data_time",
        "market_open",
        "market_phase",
        "delay",
        "error",
        "data",
        "series",
    }
    assert required <= api.keys(), f"/api/live 缺欄位：{sorted(required - api.keys())}"
    assert "證券交易所" in api["source"]
    assert "5 秒" in api["delay"] and "15 秒" in api["delay"]
    assert api["data"] is not None, api.get("error")
    assert api["data_time"] and api["fetched_at"]
    assert isinstance(api["series"], list) and api["series"], "盤中序列不得為空"
    assert api["market_open"] is expected_market_open(api["data_time"])

    data = api["data"]
    for key in ("price", "previous_close", "change", "change_percent", "open", "high", "low"):
        assert isinstance(data.get(key), (int, float)), f"data.{key} 不是數字"
    assert data["low"] <= data["price"] <= data["high"]
    assert abs((data["price"] - data["previous_close"]) - data["change"]) < 0.001
    if data.get("turnover_value") is not None:
        assert data["turnover_value"] > 0
        assert abs(data["turnover_billion"] - data["turnover_value"] / 100_000_000) < 1e-8


def assert_matches_twse(api: dict, upstream: dict) -> None:
    source = api["data"]["source_fields"]["quote"]
    assert source["d"] == upstream["d"]
    assert float(source["y"]) == float(upstream["y"])
    assert float(source["o"]) == float(upstream["o"])
    assert int(source["tlong"]) <= int(upstream["tlong"])
    assert int(upstream["tlong"]) - int(source["tlong"]) <= 180_000
    # The direct request happens after the server request. Day high can only rise
    # and day low can only fall during that short interval.
    assert float(source["h"]) <= float(upstream["h"]) + 1e-9
    assert float(source["l"]) >= float(upstream["l"]) - 1e-9


def block_fonts(page: Page) -> None:
    page.route(FONT_PATTERN, lambda route: route.abort())


def wait_live_section(page: Page) -> None:
    page.wait_for_function(
        "document.getElementById('liveBadgeText').textContent !== '連線中'",
        timeout=25_000,
    )


def assert_no_overflow(page: Page, width: int) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{width}px 橫向溢出"
    clipped = page.evaluate(
        """
        [...document.querySelectorAll('#liveMarket .num, #liveZoneText')]
          .filter(e => e.offsetParent !== null && e.scrollWidth > e.clientWidth + 1)
          .map(e => ({text:e.textContent.trim(), client:e.clientWidth, scroll:e.scrollWidth}))
        """
    )
    assert not clipped, f"{width}px 即時數字被截斷：{clipped}"


def expected_zone(price: float) -> tuple[str, str]:
    if price >= UPPER:
        return "liveZoneUp", "轉強觀察"
    if price >= LOWER:
        return "liveZoneMid", "基準"
    return "liveZoneDown", "續弱觀察"


def check_http_page(browser, width: int, height: int, api: dict, screenshots: list[str]) -> None:
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme="dark")
    block_fonts(page)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    wait_live_section(page)

    assert page.locator("#livePrice").inner_text() != "—"
    assert "證券交易所" in page.locator("#liveSource").inner_text()
    assert page.locator("#liveChart").count() == 1
    assert page.locator("#liveChartSummary").inner_text() != "尚未取得盤中分時序列。"
    assert page.locator("#liveServiceStatus").get_attribute("role") == "status"

    if api["market_open"] and api["ok"]:
        assert "LIVE" in page.locator("#liveBadgeText").inner_text()
        assert "盤中更新中" in page.locator("#liveServiceStatus").inner_text()
    elif api["market_phase"] == "closed":
        assert page.locator("#liveBadgeText").inner_text() == "已收盤"
        assert "已收盤，資料時間" in page.locator("#liveServiceStatus").inner_text()
    elif api["market_phase"] == "preopen":
        assert page.locator("#liveBadgeText").inner_text() == "尚未開盤"
    else:
        assert page.locator("#liveBadgeText").inner_text() == "非交易時段"

    zone_id, zone_text = expected_zone(api["data"]["price"])
    assert "is-current" in (page.locator(f"#{zone_id}").get_attribute("class") or "")
    assert zone_text in page.locator("#liveZoneText").inner_text()
    assert_no_overflow(page, width)
    assert not errors, errors

    page.locator("#liveMarket").scroll_into_view_if_needed()
    page.wait_for_timeout(250)
    screenshot = OUT / f"live-http-{width}.png"
    page.locator("#liveMarket").screenshot(path=str(screenshot))
    screenshots.append(str(screenshot))
    page.close()


def check_file_page(browser, screenshots: list[str]) -> None:
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    block_fonts(page)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(PAGE_FILE.as_uri(), wait_until="domcontentloaded")
    page.wait_for_timeout(700)
    status = page.locator("#liveServiceStatus").inner_text()
    assert f"即時服務未啟動，目前顯示 {SNAP_LABEL} 盤後快照" in status
    assert "Market--Ana-Live.bat" in status
    assert page.locator("#liveBadgeText").inner_text() == "非即時"
    assert page.locator("#livePrice").inner_text() == "—"
    assert_no_overflow(page, 390)
    assert not errors, errors
    screenshot = OUT / "live-file-390.png"
    page.locator("#liveMarket").screenshot(path=str(screenshot))
    screenshots.append(str(screenshot))
    page.close()


def fake_payload(price: float, stamp: datetime) -> dict:
    previous_close = 41574.72
    change = price - previous_close
    series = [
        {"timestamp": stamp.replace(hour=9, minute=0, second=0).isoformat(), "time": "09:00", "price": MID_PRICE - 80.0, "volume": 100},
        {"timestamp": stamp.replace(hour=10, minute=0, second=0).isoformat(), "time": "10:00", "price": MID_PRICE, "volume": 200},
        {"timestamp": stamp.replace(hour=11, minute=0, second=0).isoformat(), "time": "11:00", "price": price, "volume": 300},
    ]
    return {
        "ok": True,
        "source": "臺灣證券交易所基本市況報導（MIS）",
        "fetched_at": stamp.isoformat(timespec="seconds"),
        "served_at": stamp.isoformat(timespec="seconds"),
        "data_time": stamp.isoformat(timespec="seconds"),
        "market_open": True,
        "market_phase": "open",
        "delay": "MIS 主報價 userDelay=5000ms；本機主報價至少間隔 5 秒，官方分時與成交金額端點 userDelay=15000ms，至少間隔 15 秒。",
        "delay_seconds": {"quote": 5, "chart": 15},
        "error": None,
        "warning": None,
        "last_success_at": stamp.isoformat(timespec="seconds"),
        "retry_after_seconds": 0,
        "data": {
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_percent": change / previous_close * 100,
            "open": MID_PRICE - 100.0,
            "high": max(UPPER + 94.0, price),
            "low": LOWER - 33.0,
            "turnover_value": 543_210_000_000,
            "turnover_billion": 5432.1,
            "trade_volume": 1,
            "transactions": 1,
            "source_fields": {"quote": {}, "chart": {}},
        },
        "series": series,
    }


def check_simulated_live(browser, screenshots: list[str]) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="dark")
    block_fonts(page)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    state = {"payload": fake_payload(MID_PRICE, datetime.now(TAIPEI)), "requests": 0}

    def route_live(route: Route) -> None:
        state["requests"] += 1
        route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(state["payload"], ensure_ascii=False),
        )

    page.route("**/api/live", route_live)
    page.goto(PAGE_URL, wait_until="domcontentloaded")
    wait_live_section(page)
    assert "LIVE" in page.locator("#liveBadgeText").inner_text()
    assert money(MID_PRICE) in page.locator("#livePrice").inner_text()
    assert "is-current" in (page.locator("#liveZoneMid").get_attribute("class") or "")

    initial_requests = state["requests"]
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); window.LiveMarket.refreshNow()")
    page.wait_for_timeout(250)
    assert state["requests"] == initial_requests, "分頁隱藏時仍發出即時請求"

    state["payload"] = fake_payload(UP_PRICE, datetime.now(TAIPEI))
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function(f"document.getElementById('livePrice').textContent.includes('{money(UP_PRICE)}')")
    assert "is-current" in (page.locator("#liveZoneUp").get_attribute("class") or "")
    assert "轉強觀察" in page.locator("#liveZoneText").inner_text()
    assert "tick-changed" in (page.locator("#livePrice").get_attribute("class") or "")
    assert_no_overflow(page, 1440)
    assert not errors, errors

    page.locator("#liveMarket").scroll_into_view_if_needed()
    page.wait_for_timeout(250)
    screenshot = OUT / "live-simulated-1440.png"
    page.locator("#liveMarket").screenshot(path=str(screenshot))
    screenshots.append(str(screenshot))
    page.close()


def check_live_guards(browser) -> None:
    stale_page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    block_fonts(stale_page)
    stale = fake_payload(MID_PRICE, datetime.now(TAIPEI) - timedelta(seconds=45))
    stale_page.route(
        "**/api/live",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(stale, ensure_ascii=False),
        ),
    )
    stale_page.goto(PAGE_URL, wait_until="domcontentloaded")
    wait_live_section(stale_page)
    assert stale_page.locator("#liveBadgeText").inner_text() == "資料逾時"
    assert "超過 30 秒" in stale_page.locator("#liveServiceStatus").inner_text()
    assert "LIVE" not in stale_page.locator("#liveBadgeText").inner_text()
    stale_page.close()

    reduced_page = browser.new_page(
        viewport={"width": 390, "height": 844},
        color_scheme="dark",
        reduced_motion="reduce",
    )
    block_fonts(reduced_page)
    state = {"payload": fake_payload(MID_PRICE, datetime.now(TAIPEI))}
    reduced_page.route(
        "**/api/live",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json; charset=utf-8",
            body=json.dumps(state["payload"], ensure_ascii=False),
        ),
    )
    reduced_page.goto(PAGE_URL, wait_until="domcontentloaded")
    wait_live_section(reduced_page)
    state["payload"] = fake_payload(UP_PRICE, datetime.now(TAIPEI))
    reduced_page.evaluate("window.LiveMarket.refreshNow()")
    reduced_page.wait_for_function(f"document.getElementById('livePrice').textContent.includes('{money(UP_PRICE)}')")
    assert reduced_page.locator("#livePrice").evaluate("element => getComputedStyle(element).animationName") == "none"
    assert reduced_page.locator("#liveBadge .live-dot").evaluate("element => getComputedStyle(element).animationName") == "none"
    reduced_page.close()


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    assert PAGE_FILE.is_file(), "缺少 web/visual-preview.html"
    assert SERVER_FILE.is_file(), "缺少 live_server.py"

    process = subprocess.Popen(
        [sys.executable, str(SERVER_FILE)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    screenshots: list[str] = []
    try:
        health = wait_for_server(process)
        assert health == {
            "ok": True,
            "host": HOST,
            "port": PORT,
            "web_root": str(ROOT / "web"),
        }

        api = get_json(f"{BASE_URL}/api/live", timeout=30)
        assert_api(api)
        cached = get_json(f"{BASE_URL}/api/live", timeout=10)
        assert cached["fetched_at"] == api["fetched_at"], "5 秒內不應再次向證交所取主報價"
        assert cached["data"]["source_fields"]["quote"]["tlong"] == api["data"]["source_fields"]["quote"]["tlong"]

        upstream = direct_quote()
        assert_matches_twse(api, upstream)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            check_http_page(browser, 390, 844, api, screenshots)
            check_http_page(browser, 1440, 900, api, screenshots)
            check_file_page(browser, screenshots)
            check_simulated_live(browser, screenshots)
            check_live_guards(browser)
            browser.close()

        print(json.dumps({
            "api": {
                "market_open": api["market_open"],
                "market_phase": api["market_phase"],
                "data_time": api["data_time"],
                "price": api["data"]["price"],
                "previous_close": api["data"]["previous_close"],
                "turnover_billion": api["data"]["turnover_billion"],
                "series_points": len(api["series"]),
                "cache_reused_within_5_seconds": True,
                "matched_direct_twse": True,
            },
            "browser": {
                "http_390": True,
                "http_1440": True,
                "file_offline_prompt": True,
                "simulated_live_update": True,
                "hidden_page_pauses_polling": True,
                "stale_data_never_shows_live": True,
                "reduced_motion_disables_live_animation": True,
                "javascript_errors": 0,
                "horizontal_overflow": 0,
            },
            "screenshots": screenshots,
        }, ensure_ascii=False, indent=2))
    finally:
        stop_server(process)


if __name__ == "__main__":
    main()
