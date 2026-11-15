"""P4：個股頁「和 0050 對照」公開旅程與截圖驗收。

真 live_server 隨機埠；驗單檔、多檔、上櫃未含現金股利、歷史不足，以及桌面／375px。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402

SHOT_DIR = ROOT / ".tmp/lane-P4/screenshots"
UNIVERSE = ROOT / "data/liquid-universe/2026-11-09.json"


def wait_compare(page: Page) -> None:
    page.wait_for_function("document.querySelector('#compareStatus')?.dataset.state === 'ok'", timeout=60_000)


def open_stock(page: Page, base_url: str, code: str) -> None:
    page.goto(f"{base_url}/stock.html?code={code}", wait_until="domcontentloaded")
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
    wait_compare(page)


def assert_no_overflow(page: Page) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    assert overflow <= 1, overflow


def force_otc_unincluded(page: Page) -> None:
    """B2 可能已補上櫃股利；P4 仍以真回應驗 dividends_included:false 的畫面分支。"""
    def single(route) -> None:
        response = route.fetch()
        payload = response.json()
        if payload.get("item", {}).get("code") == "6488":
            payload["item"]["dividends_included"] = False
        time.sleep(0.4)
        route.fulfill(response=response, json=payload)

    def multi(route) -> None:
        response = route.fetch()
        payload = response.json()
        for item in payload.get("items", []):
            if item.get("code") == "6488":
                item["dividends_included"] = False
        route.fulfill(response=response, json=payload)

    page.route("**/api/stocks/6488/vs-0050", single)
    page.route("**/api/compare-0050?*", multi)


def assert_single(page: Page, code: str, *, comparable: bool = True) -> dict[str, object]:
    # 和 0050 對照收在「對照 0050」那一類裡（本人 2026-11-15）：先展開
    key = page.locator("#stockCats button[aria-controls='comparePanel']")
    if key.get_attribute("aria-expanded") != "true":
        key.click()
    assert page.locator("#compareBlock").is_visible()
    conclusion = page.locator("#compareConclusion").inner_text()
    if comparable:
        assert code in conclusion and "0050" in conclusion, conclusion
        assert not any(label in conclusion for label in ("不可比較", "配息未完整", "配息狀態未知")), conclusion
    else:
        assert "配息未完整" in conclusion and "不可比較" in conclusion, conclusion
        assert not any(label in conclusion for label in ("領先", "落後", "贏過", "百分點")), conclusion
    rows = page.locator("#compareTableBody tr")
    assert rows.count() == 5, rows.count()
    labels = rows.locator("th").all_inner_texts()
    assert labels == ["近 1 個月", "近 3 個月", "近 1 年", "近 3 年", "近 5 年"], labels
    assert page.locator("#compareChart svg").count() == 1
    assert page.locator("#compareLegend button").count() == (2 if comparable else 1)
    assert page.locator('#compareChips [data-code="%s"]' % code).count() == 1
    if not comparable:
        table_text = page.locator("#compareTable").inner_text()
        assert "配息未完整" in table_text and "不可比" in table_text, table_text
    assert_no_overflow(page)
    return {"conclusion": conclusion, "rows": labels, "comparable": comparable}


def add_code(page: Page, code: str) -> None:
    page.locator("#compareInput").fill(code)
    page.locator("#compareAdd").click()
    wait_compare(page)


def select_by_name(page: Page, query: str, code: str) -> None:
    page.locator("#compareInput").fill(query)
    option = page.locator(f'#compareSearchResults [role="option"]', has_text=code)
    option.wait_for(state="visible", timeout=30_000)
    page.locator("#compareInput").press("ArrowDown")
    assert page.locator("#compareInput").get_attribute("aria-activedescendant")
    page.locator("#compareInput").press("Enter")
    wait_compare(page)
    assert page.locator(f'#compareChips [data-code="{code}"]').count() == 1


def check_desktop(page: Page, base_url: str) -> dict[str, object]:
    result: dict[str, object] = {}

    open_stock(page, base_url, "2330")
    result["single"] = assert_single(page, "2330")
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-single-2330-desktop.png"))

    select_by_name(page, "聯發", "2454")
    assert page.locator("#compareChips [data-code]").count() == 2
    assert page.locator("#compareLegend button").count() == 3
    assert page.locator("#compareTableBody tr").count() == 10
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-multi-2330-2454-desktop.png"))
    result["multi"] = page.locator("#compareConclusion").inner_text()
    for code in ("1303", "2308", "2327"):
        add_code(page, code)
    assert page.locator("#compareChips [data-code]").count() == 5
    page.locator("#compareInput").fill("3037")
    page.locator("#compareAdd").click()
    page.wait_for_function("document.querySelector('#compareStatus').dataset.state === 'error'")
    assert "最多同時比較 5 檔" in page.locator("#compareStatus").inner_text()
    assert page.locator("#compareChips [data-code]").count() == 5
    result["max_five"] = True
    page.locator('#compareChips [data-code="2454"] button').click()
    wait_compare(page)
    add_code(page, "3037")
    colors = page.locator("#compareChips [data-code]").evaluate_all(
        "nodes => nodes.map(node => getComputedStyle(node).getPropertyValue('--chip-color').trim())"
    )
    assert len(colors) == len(set(colors)) == 5, colors
    result["color_reuse_without_collision"] = True

    page.goto(f"{base_url}/stock.html?code=6488", wait_until="domcontentloaded")
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
    state = page.locator("#compareStatus").get_attribute("data-state")
    assert state in {"loading", "ok"}, state
    if state == "loading":
        assert page.locator("#compareTableBody tr").count() == 0
        wait_compare(page)
    assert_single(page, "6488", comparable=False)
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-otc-6488-desktop.png"))
    result["otc"] = "配息未完整時不宣告可比差距"

    open_stock(page, base_url, "7769")
    assert_single(page, "7769")
    table_text = page.locator("#compareTable").inner_text()
    assert "資料不足" in table_text, table_text
    assert "null" not in table_text.lower()
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-insufficient-7769-desktop.png"))
    result["insufficient"] = True
    return result


def check_mobile(page: Page, base_url: str) -> dict[str, object]:
    open_stock(page, base_url, "2330")
    assert_single(page, "2330")
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-single-2330-375.png"))
    chart = page.locator("#compareChart svg")
    chart.focus()
    chart.press("ArrowLeft")
    assert page.locator("#compareTooltip").is_visible()
    assert page.locator("#compareTooltip").get_attribute("aria-live") == "polite"
    add_code(page, "6488")
    assert page.locator("#compareChips [data-code]").count() == 2
    compare_text = page.locator("#compareTable").inner_text()
    assert "配息未完整" in compare_text and "不可比" in compare_text, compare_text
    conclusion = page.locator("#compareConclusion").inner_text()
    assert "配息未完整標的未納入比較" in conclusion, conclusion
    assert_no_overflow(page)
    hit = page.locator("#compareChart .cmp-hit")
    box = hit.bounding_box()
    assert box
    hit.dispatch_event("pointerdown", {"clientX": box["x"] + box["width"] / 2, "clientY": box["y"] + box["height"] / 2, "pointerType": "touch"})
    assert page.locator("#compareTooltip").is_visible()
    page.evaluate("document.querySelector('#compareTooltip').hidden = true")
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-multi-2330-6488-375.png"))
    page.locator("#themeToggle").click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert_no_overflow(page)
    page.locator("#compareBlock").screenshot(path=str(SHOT_DIR / "compare-multi-2330-6488-375-dark.png"))
    return {
        "chips": page.locator("#compareChips [data-code]").count(),
        "legend": page.locator("#compareLegend button").count(),
        "overflow": 0,
        "dark_theme": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("desktop", "mobile", "all"), default="all")
    args = parser.parse_args()
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    server = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
    server.universe_path = UNIVERSE
    live_server.universe_state(UNIVERSE)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    with urllib.request.urlopen(f"{base_url}/api/stocks/6488/vs-0050", timeout=60) as response:
        actual_otc = json.load(response)
    result: dict[str, object] = {
        "port": server.server_port,
        "actual_otc_dividends_included": actual_otc["item"]["dividends_included"],
        "false_branch_tested_with_real_response": True,
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                if args.case in {"desktop", "all"}:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
                    page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
                    force_otc_unincluded(page)
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    result["desktop"] = check_desktop(page, base_url)
                    assert not errors, errors
                    page.close()
                if args.case in {"mobile", "all"}:
                    page = browser.new_page(viewport={"width": 375, "height": 812})
                    page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
                    page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
                    force_otc_unincluded(page)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    result["mobile"] = check_mobile(page, base_url)
                    assert not errors, errors
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    (ROOT / ".tmp/lane-P4/compare-check.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
