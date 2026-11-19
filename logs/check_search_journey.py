"""票 05／06 A3＋S3＋S4：首頁上市／上櫃大量股搜尋到個股頁（契約 v2）的聚焦 Playwright 驗收。

S4（2026-11-09）：本人「整個網站都只做大量股」。搜尋與個股分析只涵蓋 liquid_universe 母體（V2 快照），
母體外顯示「不在涵蓋範圍」。籌碼排行每列標 ETF。
W1（2026-11-09）：個股頁主畫面改為操作指引（一句話結論＋3～4 條有價位的重點），條件與指標收合；
畫面不得出現「參考站／會員／反推／吻合率」等研究過程字樣（吻合率只留在 API）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_server  # noqa: E402
from frozen_history import freeze_live_server  # noqa: E402

FROZEN_AS_OF = "2026-11-06"

SHOT_DIR = ROOT / ".tmp/lane-A3"


def goto_home(page: Page, base_url: str, search: bool = True) -> None:
    """首頁會管理行情輪詢，導航只等 DOM；真正可操作狀態用顯式 ready seam 判斷。

    11-14 起搜尋收在右上角的放大鏡後面（浮起的面板）。要操作搜尋的檢查先把面板打開；
    面板開著時點不到首頁其他地方，要看首頁區塊的檢查傳 search=False。
    """
    page.goto(f"{base_url}/minimal.html", wait_until="domcontentloaded")
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
    if search:
        page.locator("#searchToggle").click()
        page.locator("#stockSearchForm").wait_for(state="visible")
    else:
        page.locator("#homeCats").wait_for(state="visible")


def open_cat(page: Page, cat: str) -> None:
    """首頁的籌碼與資金、籌碼選股、產業資金流向、消息預設是收著的；點分類鍵展開（已開著就不動）。"""
    button = page.locator(f"#homeCats button[data-cat='{cat}']")
    if button.get_attribute("aria-expanded") != "true":
        button.click()


class SearchJourneyHandler(live_server.Handler):
    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        try:
            super()._send_json(payload, status)
        except (BrokenPipeError, ConnectionAbortedError):
            # 換頁或 AbortController 中止舊請求後，socket 關閉是預期行為。
            return

    def _send_stock_search(self, query: str) -> None:
        term = (urllib.parse.parse_qs(query).get("q") or [""])[0]
        if term == "台":
            time.sleep(0.7)
        if term == "錯誤":
            self._send_json({"error": "temporary", "message": "測試錯誤"}, 503)
            return
        if term in {"1234", "1235"}:
            stale = term == "1234"
            self._send_json({
                "source_date": "2026-11-08",
                "items": [{
                    "code": term,
                    "name": "舊資料測試" if stale else "資料不足測試",
                    "market": "上市",
                    "security_type": "普通股",
                    "latest_bar_date": "2026-10-30" if stale else "2026-11-06",
                    "available_bars": 300 if stale else 8,
                    "analysis_status": "stale" if stale else "insufficient",
                    "data_date": "2026-10-30" if stale else "2026-11-06",
                    "analysis_available": stale,
                    "source_date": "2026-11-08",
                }],
                "next_cursor": None,
            })
            return
        try:
            super()._send_stock_search(query)
        except (BrokenPipeError, ConnectionAbortedError):
            # AbortController 取消舊搜尋後，測試慢回應的 socket 會正常中止。
            return


# S3（2026-11-08）：個股頁改用契約 v2；方案、總分、24 招、操作卡與原站命名都不得再出現。
RETIRED_STOCK_TEXT = ("綜合觀察", "短線量價", "波段結構", "總分", "五維", "24 招", "近期操作手法", "可考慮介入",
                      "防守參考", "2TON", "漲停寶寶", "甜甜價", "甜價", "FIT-V1")


# W1（2026-11-09）本人：「不要放這種{條件文字取自參考站，判斷門檻依會員樣本反推，逐條附吻合率}敘述在我的正式網站頁面上」。
# 研究過程字樣不得出現在畫面（含收合區塊展開後）；吻合率與規則留在 API。
RESEARCH_TEXT = ("參考站", "會員", "反推", "吻合率", "擬合", "留出", "判斷式", "樣本", "原站未公開")


def assert_stock_v2(page: Page) -> dict[str, object]:
    assert page.locator("#content").get_attribute("data-schema") == "stock-analysis/v2"
    assert page.locator("#presetTabs").count() == 0
    assert page.locator("#undisclosed").count() == 0
    # UI-2b：首屏只露出一句明確策略；進場／加碼／停損／目標與單次證據強度預設收合。
    conclusion = page.locator("#strategyConclusion").inner_text()
    assert any(action in conclusion for action in ("持有", "分批進場", "觀望", "減碼")), conclusion
    assert page.locator("#strategyDetails").get_attribute("open") is None
    page.locator("#strategyDetails summary").click()
    prices = [page.locator(f"#{item}").inner_text() for item in
              ("strategyEntry", "strategyAdd", "strategyStop", "strategyTarget")]
    assert any(any(char.isdigit() for char in value) for value in prices), prices
    assert "證據強度：" in page.locator("#strategyEvidence").inner_text()
    for retired in ("#signalDetails", "#indicatorDetails", "#methodDetails", "#guideDisclaimer"):
        assert page.locator(retired).count() == 0, retired
    body = page.locator("body").inner_text()
    leaked = [word for word in RETIRED_STOCK_TEXT + RESEARCH_TEXT if word in body]
    supplemental = [word for word in ("非買賣建議", "左右滑動切換", "價位怎麼來的", "方法說明") if word in body]
    assert not leaked, leaked
    assert not supplemental, supplemental
    page.locator("#strategyDetails summary").click()
    return {"schema": "stock-analysis/v2", "conclusion": conclusion, "prices": prices,
            "resistance1": page.locator("#resistance1").inner_text(),
            "support1": page.locator("#support1").inner_text()}


def check_outside_universe(page: Page, base_url: str, width: int) -> dict[str, object]:
    """母體外：搜尋查不到；直接開個股頁顯示「不在本站涵蓋範圍」＋名次，不畫圖、不顯示數字。"""
    goto_home(page, base_url)
    page.locator("#stockSearchInput").fill("4441")
    page.wait_for_function("document.querySelector('#stockSearchStatus').dataset.state === 'no-results'")
    page.goto(f"{base_url}/stock.html?code=4441", wait_until="networkidle")
    page.locator("#outside:not([hidden])").wait_for(state="visible", timeout=30_000)
    text = page.locator("#outside").inner_text()
    assert "不在本站涵蓋範圍" in text and "名" in text and "本站只涵蓋大量股" in text, text
    assert page.locator("#content").is_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOT_DIR / f"stock-outside-4441-{width}.png"), full_page=True)
    page.goto(f"{base_url}/stock.html?code=5906", wait_until="networkidle")
    page.locator("#outside:not([hidden])").wait_for(state="visible", timeout=30_000)
    suspended = page.locator("#outsideReason").inner_text()
    assert "停牌" in suspended and "大量股" not in suspended, suspended
    return {"outside_4441": text.splitlines()[1] if len(text.splitlines()) > 1 else text, "outside_5906": suspended}


def check_shell(page: Page, base_url: str) -> dict[str, object]:
    goto_home(page, base_url)
    search = page.locator("#stockSearchInput")
    search.wait_for(state="visible")
    assert search.get_attribute("type") == "search"
    assert page.locator("#stockSearchClear").is_visible()
    page.wait_for_function("document.querySelector('#chipScreens').dataset.state !== 'loading'")
    # 選股看板預設收在分類鍵後面：四顆鍵都在，而且都還沒展開。
    assert page.locator("#homeCats button[aria-expanded='false']").count() == 4
    return {
        "input_type": search.get_attribute("type"),
        "home_screens_visible": True,
        "overflow": page.evaluate("document.documentElement.scrollWidth - innerWidth"),
    }


def check_journey(page: Page, base_url: str, width: int) -> dict[str, object]:
    goto_home(page, base_url)
    search = page.locator("#stockSearchInput")

    search.fill("1102")
    page.locator('#stockSearchResults a[data-code="1102"]').wait_for(state="visible")
    page.screenshot(path=str(SHOT_DIR / f"search-code-{width}.png"), full_page=True)
    page.locator('#stockSearchResults a[data-code="1102"]').click()
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    assert page.locator("#stockCode").inner_text() == "1102"
    v2_1102 = assert_stock_v2(page)
    page.screenshot(path=str(SHOT_DIR / f"stock-v2-1102-{width}.png"), full_page=True)

    goto_home(page, base_url)
    page.locator("#stockSearchInput").fill("積電")
    page.locator('#stockSearchResults a[data-code="2330"]').wait_for(state="visible")
    codes = page.locator("#stockSearchResults a").evaluate_all(
        "nodes => nodes.map(node => node.dataset.code)"
    )
    assert codes == ["2330", "6770"], codes
    page.screenshot(path=str(SHOT_DIR / f"search-partial-name-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator('#stockSearchResults a[data-code="2330"]').click()
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    assert page.locator("#stockCode").inner_text() == "2330"
    v2_2330 = assert_stock_v2(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(SHOT_DIR / f"stock-v2-2330-{width}.png"), full_page=True)
    return {"code": "1102", "v2_1102": v2_1102, "v2_2330": v2_2330, "partial_codes": codes, "overflow": 0}


def check_otc_journey(page: Page, base_url: str, width: int) -> dict[str, object]:
    """票06：上櫃以代號與中文部分名稱搜尋 → 個股頁三方案，市場別在搜尋結果與個股頁都看得到。"""
    goto_home(page, base_url)
    assert page.locator("#stockSearchTitle").inner_text() == "搜尋上市／上櫃普通股"
    search = page.locator("#stockSearchInput")

    search.fill("環球")
    page.locator('#stockSearchResults a[data-code="6488"]').wait_for(state="visible")
    codes = page.locator("#stockSearchResults a").evaluate_all("nodes => nodes.map(node => node.dataset.code)")
    # S4：4441 不在大量股母體，只剩 6488（第 20 名）。
    assert codes == ["6488"], codes
    otc_market = page.locator('#stockSearchResults a[data-code="6488"] .stock-search-market').inner_text()
    assert otc_market.startswith("上櫃普通股"), otc_market
    page.screenshot(path=str(SHOT_DIR / f"search-otc-partial-name-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    search.fill("6488")
    page.locator('#stockSearchResults a[data-code="6488"]').wait_for(state="visible")
    page.locator('#stockSearchResults a[data-code="6488"]').click()
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    assert page.locator("#stockCode").inner_text() == "6488"
    stock_market = page.locator("#stockMarket").inner_text()
    assert stock_market == "上櫃", stock_market
    assert_stock_v2(page)
    page.screenshot(path=str(SHOT_DIR / f"stock-v2-6488-{width}.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # 舊的上櫃過期（4804）與停牌（2064）樣本都不在大量股母體，改由 check_outside_universe 與 states fixture 驗。
    return {"partial_codes": codes, "otc_market": otc_market, "stock_market": stock_market}


def check_search_states(page: Page, base_url: str) -> dict[str, object]:
    goto_home(page, base_url)
    search = page.locator("#stockSearchInput")
    status = page.locator("#stockSearchStatus")

    with page.expect_request(lambda request: "q=%E5%8F%B0" in request.url):
        search.fill("台")
    assert status.get_attribute("data-state") == "loading"
    search.fill("積電")
    page.locator('#stockSearchResults a[data-code="2330"]').wait_for(state="visible")
    page.wait_for_timeout(900)
    race_codes = page.locator("#stockSearchResults a").evaluate_all(
        "nodes => nodes.map(node => node.dataset.code)"
    )
    assert race_codes == ["2330", "6770"], race_codes

    search.fill("不存在")
    page.wait_for_function("document.querySelector('#stockSearchStatus').dataset.state === 'no-results'")
    assert page.locator("#stockSearchResults a").count() == 0

    search.fill("錯誤")
    page.wait_for_function("document.querySelector('#stockSearchStatus').dataset.state === 'error'")

    search.fill("1234")
    stale = page.locator('#stockSearchResults a[data-code="1234"] .stock-search-meta')
    stale.wait_for(state="visible")
    assert stale.inner_text() == "可分析 · 資料至 2026-10-30，已過期"

    search.fill("1235")
    unavailable = page.locator('#stockSearchResults a[data-code="1235"] .stock-search-meta')
    unavailable.wait_for(state="visible")
    assert unavailable.inner_text() == "無法分析 · 歷史資料不足（8 根）"

    search.fill("1102")
    page.locator('#stockSearchResults a[data-code="1102"]').wait_for(state="visible")
    page.locator("#stockSearchClear").click()
    assert search.input_value() == ""
    assert status.get_attribute("data-state") == "empty"
    assert page.locator("#stockSearchResults a").count() == 0
    return {"race_codes": race_codes, "no_results": True, "error": True, "clear": True}


def check_stock_states(page: Page, base_url: str) -> dict[str, str]:
    states = {
        "9999": "not_found",
        "5555": "missing_data",
        "6666": "suspended",
        "7777": "just_listed",
    }
    labels = {
        "not_found": "查無此股",
        "missing_data": "缺少分析資料",
        "suspended": "停牌",
        "just_listed": "剛上市",
    }

    def fulfill(route) -> None:
        code = urllib.parse.urlsplit(route.request.url).path.split("/")[3]
        if code == "8888":
            response = route.fetch(url=route.request.url.replace("/8888/", "/1102/"))
            payload = response.json()
            payload.update({
                "code": "8888",
                "name": "舊資料測試",
                "analysis_status": "stale",
                "stock_state": "stale",
                "data_date": "2026-10-30",
            })
            route.fulfill(response=response, json=payload)
            return
        state = states[code]
        route.fulfill(
            status=404 if state == "not_found" else 200,
            content_type="application/json; charset=utf-8",
            body=json.dumps({
                "error": "insufficient-data",
                "message": "資料狀態測試",
                "stock_state": state,
                "qa": {"blocking": True},
            }, ensure_ascii=False),
        )

    page.route("**/api/stocks/*/analysis?*", fulfill)
    seen: dict[str, str] = {}
    for code, state in states.items():
        page.goto(f"{base_url}/stock.html?code={code}", wait_until="networkidle")
        page.wait_for_function(
            "expected => document.querySelector('#status').dataset.stockState === expected",
            arg=state,
        )
        text = page.locator("#status").inner_text()
        assert labels[state] in text, (state, text)
        assert page.locator("#content").is_hidden()
        seen[state] = text

    page.goto(f"{base_url}/stock.html?code=8888", wait_until="networkidle")
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    page.wait_for_function("document.querySelector('#status').dataset.stockState === 'stale'")
    stale_text = page.locator("#status").inner_text()
    assert "資料至 2026-10-30，已過期" in stale_text
    assert_stock_v2(page)
    seen["stale"] = stale_text
    return seen


def check_real_stale_and_unavailable(page: Page, base_url: str, width: int) -> dict[str, str]:
    """（S4 起不在 --case all）1589、5906 都不在大量股母體；只在全名錄模式（universe_enabled=False）有意義。"""
    goto_home(page, base_url)
    search = page.locator("#stockSearchInput")

    search.fill("1589")
    stale_meta = page.locator('#stockSearchResults a[data-code="1589"] .stock-search-meta')
    stale_meta.wait_for(state="visible")
    stale_search = stale_meta.inner_text()
    assert stale_search == "可分析 · 資料至 2026-04-02，已過期", stale_search
    page.screenshot(path=str(SHOT_DIR / f"search-real-stale-{width}.png"), full_page=True)
    page.locator('#stockSearchResults a[data-code="1589"]').click()
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    page.wait_for_function("document.querySelector('#status').dataset.stockState === 'stale'")
    stale_text = page.locator("#status").inner_text()
    assert "資料至 2026-04-02，已過期" in stale_text, stale_text
    assert_stock_v2(page)
    page.screenshot(path=str(SHOT_DIR / f"stock-real-stale-{width}.png"), full_page=True)

    goto_home(page, base_url)
    page.locator("#stockSearchInput").fill("5906")
    unavailable_meta = page.locator('#stockSearchResults a[data-code="5906"] .stock-search-meta')
    unavailable_meta.wait_for(state="visible")
    assert unavailable_meta.inner_text() == "無法分析 · 最新交易日停牌或無有效行情", unavailable_meta.inner_text()
    page.locator('#stockSearchResults a[data-code="5906"]').click()
    page.wait_for_function("document.querySelector('#status').dataset.stockState === 'suspended'")
    unavailable_text = page.locator("#status").inner_text()
    assert page.locator("#content").is_hidden()
    page.screenshot(path=str(SHOT_DIR / f"stock-real-unavailable-{width}.png"), full_page=True)
    return {"stale_search": stale_search, "stale_stock": stale_text, "unavailable_stock": unavailable_text}


SCREEN_DIR = ROOT / ".tmp/lane-G3"
SCREEN_ROOTS = {
    # S4：改用 V2 發布（大量股母體、上市 T86 完整 250 天、含 ETF 與相對低位階名單）。
    "real": ROOT / ".tmp/lane-V2/snapshots",
    "not_ready": SCREEN_DIR / "fixture-empty-root",
}
UNIVERSE_SNAPSHOT = ROOT / ".tmp/lane-V2/universe/2026-11-06.json"
ROW = "#chipScreenList li > :is(a, .screen-static)"
BANNED_UI_WORDS = ("Jimmy", "私藏", "甜價", "甜甜價", "投信關注", "主力", "輪動", "觀望", "退潮", "CP值", "CP 值", "抄底",
                   "金融獵場", "漲停寶寶", "候選名單")


def wait_block(page: Page, block_id: str) -> str:
    page.wait_for_function(
        f"!['loading', undefined].includes(document.querySelector('#{block_id}').dataset.state)", timeout=30_000
    )
    return page.locator(f"#{block_id}").get_attribute("data-state") or ""


def click_and_wait(page: Page, locator) -> str:
    """點選後等籌碼選股重新讀取完成（狀態先變 loading 再離開）。"""
    page.evaluate("document.querySelector('#chipScreens').dataset.state = 'loading'")
    locator.click()
    return wait_block(page, "chipScreens")


def check_home_screens_journey(page: Page, base_url: str, server, width: int) -> dict[str, object]:
    """首頁區塊：籌碼選股（可算／定義未公開／無合法來源）、產業資金流向（長期核心持有 11-19 換成消息）；名單可點入個股頁。"""
    result: dict[str, object] = {}
    server.screen_root = SCREEN_ROOTS["real"]
    goto_home(page, base_url, search=False)
    open_cat(page, "screens")
    assert wait_block(page, "chipScreens") in {"ok", "partial"}
    tabs = page.locator("#chipScreenTabs [role=tab]").all_inner_texts()
    assert tabs[:4] == ["外資淨買超排行", "投信淨買超排行", "外資連續淨買超", "投信連續淨買超"], tabs
    assert len(tabs) == 9, tabs
    assert page.locator(ROW).count() == 5
    assert "資料日 2026-11-06" in page.locator("#chipScreenStatus").inner_text()
    first_codes = page.locator(ROW).evaluate_all("nodes => nodes.map(node => node.dataset.code)")
    # S4：外資 1 日買超前 5 名全是 ETF（照參考站含 ETF）——每列標 ETF、不連到沒有分析的個股頁。
    etf_rows = page.locator("#chipScreenList .screen-static").count()
    etf_tags = page.locator("#chipScreenList .screen-etf").count()
    assert etf_rows == etf_tags == 5, (etf_rows, etf_tags)
    result["etf_rows_top5"] = etf_rows
    page.locator("#chipScreens").screenshot(path=str(SCREEN_DIR / f"home-chip-etf-{width}.png"))
    click_and_wait(page, page.locator("#chipScreenMore"))
    assert page.locator(ROW).count() == 50
    click_and_wait(page, page.locator('#chipScreenDays button[data-days="20"]'))
    assert page.locator('#chipScreenDays button[data-days="20"]').get_attribute("aria-pressed") == "true"
    assert page.locator(ROW).count() == 5
    click_and_wait(page, page.locator("#chipScreenInflow"))
    inflow_flags = page.locator(ROW).evaluate_all(
        "nodes => nodes.map(node => Boolean(node.querySelector('.screen-flag:not(.screen-etf)')))")
    assert inflow_flags and all(inflow_flags), inflow_flags
    click_and_wait(page, page.locator("#chipScreenInflow"))
    click_and_wait(page, page.locator('#chipScreenDays button[data-days="1"]'))
    result["foreign_1d_top5"] = first_codes

    open_cat(page, "rotation")
    assert wait_block(page, "rotationBlock") == "ok"
    assert page.locator("#rotationMeta").inner_text() == "資料日 2026-11-06"
    # 11-14 起象限泡泡圖與四色圖例換成資金地形：每個產業一棟樓，四個街區各標資金狀態的名稱。
    grounds = page.locator("#rotationTerrain .terrain-ground-label").all_text_contents()
    for label in ("資金領先", "資金減速", "資金回補", "資金落後"):
        assert label in grounds, grounds
    assert page.locator("#rotationTerrain .terrain-tower").count() >= 30
    assert page.locator("#rotationEfficiency li").count() == 5
    assert page.locator("#rotationRecovery li").count() >= 1
    # 排行與回溫訊號收在面板裡（上面兩行數的是面板裡的列，面板沒開也數得到）。用鍵盤選一棟樓，要出現它的明細。
    tower = page.locator("#rotationTerrain .terrain-tower").first
    tower.focus()
    page.keyboard.press("Enter")
    assert tower.get_attribute("aria-pressed") == "true"
    assert "今日成交額" in page.locator("#rotationTerrain .terrain-detail").inner_text()
    page.keyboard.press("Escape")

    # 本人 2026-11-19：「長期核心持有」換成「消息」（和個股功能重複）。本機伺服器沒有開 market_news_v1，消息鍵不出現。
    assert page.locator("#coreHoldings").count() == 0
    assert page.locator("#homeCats button", has_text="長期核心持有").count() == 0
    assert page.locator("#newsCatButton").is_hidden()
    result["core_holdings_retired"] = True
    page.mouse.move(0, 0)
    page.screenshot(path=str(SCREEN_DIR / f"home-screens-{width}.png"), full_page=True)

    # 三個區塊一次只展開一個，收起來的讀不到畫面文字，所以用 text_content 把三塊都掃過。
    body_text = page.locator("#marketScreens").text_content() or ""
    for banned in BANNED_UI_WORDS:
        assert banned not in body_text, banned
    result["banned_words_absent"] = True

    # R1＋V2：相對低位階已可計算（發布時注入族群與乖離），首頁不再顯示「定義未公開」。
    open_cat(page, "screens")
    state = click_and_wait(page, page.locator("#chipScreenTabs [role=tab]", has_text="資金流入族群・相對低位階"))
    assert state in {"ok", "partial"}, state
    assert page.locator(ROW).count() == 5
    relative_values = page.locator("#chipScreenList .screen-value").all_inner_texts()
    assert all(text.startswith("乖離 ") and text.endswith("%") for text in relative_values), relative_values
    assert "定義未公開" not in page.locator("#chipScreenStatus").inner_text()
    page.locator("#chipScreens").screenshot(path=str(SCREEN_DIR / f"home-chip-relative-{width}.png"))
    state = click_and_wait(page, page.locator("#chipScreenTabs [role=tab]", has_text="資金流入族群・ETF成分股"))
    assert state == "unknown", state
    assert "定義未公開" in page.locator("#chipScreenStatus").inner_text()
    assert page.locator(ROW).count() == 0
    page.locator("#chipScreens").screenshot(path=str(SCREEN_DIR / f"home-chip-unknown-{width}.png"))
    state = click_and_wait(page, page.locator("#chipScreenTabs [role=tab]", has_text="券商籌碼集中度"))
    assert state == "unavailable", state
    assert "無合法資料來源" in page.locator("#chipScreenStatus").inner_text()
    assert page.locator(ROW).count() == 0
    page.locator("#chipScreens").screenshot(path=str(SCREEN_DIR / f"home-chip-unavailable-{width}.png"))
    click_and_wait(page, page.locator("#chipScreenTabs [role=tab]", has_text="投信連續淨買超"))
    streak_value = page.locator("#chipScreenList .screen-value").first.inner_text()
    assert "天" in streak_value, streak_value

    # 11-14 本人定案：搜尋改在浮起的面板裡，「搜尋框有字時選股看板讓位」的行為取消。
    # 搜尋框留著字關掉面板，籌碼選股要照樣看得到。
    page.locator("#searchToggle").click()
    page.locator("#stockSearchInput").fill("2330")
    page.keyboard.press("Escape")
    assert page.locator("#chipScreenList").is_visible()

    target = page.locator("#chipScreenList a").first.get_attribute("data-code")
    page.locator("#chipScreenList a").first.click()
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    card = page.locator("#candidateReasons .sk-membership")
    card.wait_for(state="visible", timeout=15_000)
    card_text = card.inner_text()
    assert "出現在籌碼選股名單" in card_text, card_text
    assert "投信連續淨買超・連續淨買超：第 1 名" in card_text, card_text
    page.screenshot(path=str(SCREEN_DIR / f"stock-membership-{target}-{width}.png"), full_page=True)
    result["membership_code"] = target

    # 母體內、但不在任何籌碼名單的股票（S4 以 V2 封存查得 6168）不顯示名單卡。
    page.goto(f"{base_url}/stock.html?code=6168", wait_until="networkidle")
    page.locator("#content:not([hidden])").wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(800)
    result["non_member_6168_card_count"] = page.locator("#candidateReasons .sk-membership").count()
    assert result["non_member_6168_card_count"] == 0

    server.screen_root = SCREEN_ROOTS["not_ready"]
    goto_home(page, base_url, search=False)
    open_cat(page, "screens")
    assert wait_block(page, "chipScreens") == "not_ready"
    assert "尚未就緒" in page.locator("#chipScreenStatus").inner_text()
    assert wait_block(page, "rotationBlock") == "not_ready"
    assert wait_block(page, "coreHoldings") == "ok"  # 核心持有由本機日 K 直接計算，不依賴發布
    page.screenshot(path=str(SCREEN_DIR / f"home-not-ready-{width}.png"), full_page=True)
    server.screen_root = SCREEN_ROOTS["real"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    return result


def check_existing_homepage_regression(browser, base_url: str) -> dict[str, object]:
    path = ROOT / "logs/check_minimal.py"
    spec = importlib.util.spec_from_file_location("lane_a3_existing_minimal", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE_URL = base_url
    module.PAGE_URL = f"{base_url}/minimal.html"
    module.check_actual_service(browser)
    return {"group": "check_minimal.check_actual_service", "passed": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("shell", "journey", "otc", "states", "screens", "all"), default="shell")
    args = parser.parse_args()

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    SCREEN_ROOTS["not_ready"].mkdir(parents=True, exist_ok=True)
    # D2：行情庫每日增量後，固定的 11-06 封存會被判 stale；比照 T1 把行情庫凍結在 11-06（正式庫不動），斷言不放寬。
    restore_history = freeze_live_server(FROZEN_AS_OF)
    server = live_server.MarketHTTPServer(("127.0.0.1", 0), SearchJourneyHandler)
    # 旅程只讀隔離封存，不碰正式 data/market-screen-snapshots；母體固定用 V2 快照（not_ready 根目錄沒有 universe）。
    server.screen_root = SCREEN_ROOTS["real"]
    server.universe_path = UNIVERSE_SNAPSHOT
    live_server.universe_state(UNIVERSE_SNAPSHOT)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    result: dict[str, object] = {"port": server.server_port}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                if args.case == "shell":
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    result["shell"] = check_shell(page, base_url)
                    page.close()
                elif args.case == "journey":
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    result["journey"] = check_journey(page, base_url, 1440)
                    page.close()
                elif args.case == "otc":
                    result["otc_journeys"] = {}
                    for width, height in ((1440, 1000), (375, 812)):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        errors: list[str] = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        result["otc_journeys"][str(width)] = check_otc_journey(page, base_url, width)
                        assert not errors, errors
                        page.close()
                elif args.case == "screens":
                    result["screen_journeys"] = {}
                    for width, height in ((1440, 1000), (375, 812)):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        errors: list[str] = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        result["screen_journeys"][str(width)] = check_home_screens_journey(page, base_url, server, width)
                        assert not errors, errors
                        page.close()
                elif args.case == "states":
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    result["search_states"] = check_search_states(page, base_url)
                    result["stock_states"] = check_stock_states(page, base_url)
                    page.close()
                else:
                    result["journeys"] = {}
                    for width, height in ((1440, 1000), (375, 812)):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        errors: list[str] = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        result["journeys"][str(width)] = check_journey(page, base_url, width)
                        result["journeys"][f"{width}-outside"] = check_outside_universe(page, base_url, width)
                        result["journeys"][f"{width}-otc"] = check_otc_journey(page, base_url, width)
                        result["journeys"][f"{width}-screens"] = check_home_screens_journey(page, base_url, server, width)
                        assert not errors, errors
                        page.close()
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    result["search_states"] = check_search_states(page, base_url)
                    result["stock_states"] = check_stock_states(page, base_url)
                    page.close()
                    result["existing_homepage_regression"] = check_existing_homepage_regression(browser, base_url)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        restore_history()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    (SHOT_DIR / "search-journey-results.json").write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
