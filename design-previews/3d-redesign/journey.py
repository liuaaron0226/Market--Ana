"""立體重設計的使用旅程檢查：用真瀏覽器把三個頁面最主要的操作各走一遍，確認換皮後功能還在。

專案既有的旅程腳本（logs/check_search_journey.py、cloud/test/browser_portfolio_journey.py）要本機的行情資料庫，
這個工作目錄沒有；這支改用 preview-server.mjs（雲端打包＋假資料庫＋固定樣本），在工作目錄裡就能跑。
它只驗看得到、操作得到的結果，不驗樣式類別以外的內部實作；細項仍以 .scratch/3d-redesign/parity-checklist.md 為準。

用法（在工作目錄根目錄）：python design-previews/3d-redesign/journey.py
全部通過結束碼 0；有任何一項失敗結束碼 1，並印出失敗的項目。
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    results.append((name, bool(condition), str(detail)))


def step(name: str, action) -> None:
    """把一段操作包起來：中途丟例外（找不到元素、逾時）算這一項失敗，不中斷後面的檢查。"""
    try:
        action()
    except Exception as error:  # noqa: BLE001
        check(name, False, str(error).splitlines()[0][:160])


PANEL_OPEN = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'"
PANEL_CLOSED = "document.getElementById('detailPanel').getAttribute('aria-hidden') === 'true'"


def no_overflow(page: Page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth - innerWidth") == 0


def study_pressed(page: Page, study: str) -> bool:
    """指標切換鈕本身就寫著指標名稱，所以要看的是它有沒有變成選中，不是畫面上有沒有那幾個字。"""
    button = page.locator(f".kc-study-choice[data-study='{study}']")
    button.click()
    page.wait_for_timeout(200)
    return button.get_attribute("aria-pressed") == "true"


# 週期切換的選中狀態是一塊凸起的滑塊（畫在切換軌道上的那一塊）。量它實際停的位置，和被選中的鈕比。
THUMB_ON_CHECKED = """value => {
  const button = document.querySelector(`.hm-period button[data-value='${value}']`);
  const track = button.closest('.hm-period-seg');
  const thumb = getComputedStyle(track, '::before');
  if (thumb.content === 'none' || Number(thumb.opacity) === 0) return 'no thumb';
  const shift = new DOMMatrixReadOnly(thumb.transform === 'none' ? '' : thumb.transform).m41;
  const trackBox = track.getBoundingClientRect(), box = button.getBoundingClientRect();
  const left = trackBox.left + parseFloat(getComputedStyle(track).borderLeftWidth) + parseFloat(thumb.left) + shift;
  const off = Math.max(Math.abs(left - box.left), Math.abs(parseFloat(thumb.width) - box.width));
  return off <= 1.5 ? 'ok' : `off by ${off.toFixed(1)}px`;
}"""


# 指到 K 棒時浮出來的當天明細：要看得到、有開高低收，而且底色不透明（壓在 K 線上數字才讀得清楚）。
TIP_STATE = """() => {
  const tip = document.querySelector('#mainChart .kc-tip'), style = getComputedStyle(tip);
  if (!tip.classList.contains('show') || Number(style.opacity) < 1) return 'hidden';
  if (!/收.*開.*高.*低/.test(tip.textContent)) return 'no ohlc: ' + tip.textContent;
  const parts = style.backgroundColor.match(/[\d.]+/g) || [];
  return parts.length < 4 || Number(parts[3]) === 1 ? 'ok' : 'translucent ' + style.backgroundColor;
}"""


def open_panel(page: Page, tag: str, selector: str, title_part: str) -> None:
    def run() -> None:
        target = page.locator(selector).first
        target.scroll_into_view_if_needed()
        target.click()
        page.wait_for_function(PANEL_OPEN)
        page.locator("#panelTitle").wait_for(state="visible")
        title = (page.locator("#panelTitle").text_content() or "").strip()
        # 籌碼卡的面板標題跟著資料走，沒有固定字樣，只要求有標題。
        check(f"{tag}：點了會開面板「{title_part or '有標題'}」", title_part in title if title_part else bool(title), title)
        check(f"{tag}：面板「{title_part or '籌碼'}」有內容", len(page.locator("#panelBody").inner_text().strip()) > 20)
        box = page.locator("#detailPanel").bounding_box()
        view = page.viewport_size
        inside = box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= view["width"] + 1 and box["y"] + box["height"] <= view["height"] + 1
        check(f"{tag}：面板完全在可見範圍內", inside, box)
        centered = (abs(box["x"] + box["width"] / 2 - view["width"] / 2) <= 2
                    and abs(box["y"] + box["height"] / 2 - view["height"] / 2) <= 2)
        check(f"{tag}：面板置中", centered, box)
        page.keyboard.press("Escape")
        page.wait_for_function(PANEL_CLOSED)
        check(f"{tag}：按 Esc 關閉面板", page.evaluate(PANEL_CLOSED))
    step(f"{tag}：開關面板", run)


def home(page: Page, base: str, tag: str, mobile: bool) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    shoot.ready_home(page)
    check(f"{tag}：現價有數字", page.locator("#currentPrice").inner_text().strip() not in ("", "—"))
    check(f"{tag}：沒有橫向捲動", no_overflow(page))

    def periods() -> None:
        thumb = []
        for value, label in (("weekly", "週"), ("monthly", "月"), ("daily", "日")):
            page.locator(f".hm-period button[data-value='{value}']").click()
            page.wait_for_function("label => document.getElementById('chartContext').textContent.includes(label)", arg=label)
            thumb.append(f"{label}：{page.evaluate(THUMB_ON_CHECKED, value)}")
        check(f"{tag}：週期切換日／週／月", True)
        check(f"{tag}：週期滑塊停在選中的週期上", all(item.endswith("ok") for item in thumb), thumb)
    step(f"{tag}：週期切換", periods)

    def zoom() -> None:
        if mobile:
            page.locator("#chartToolsToggle").click()
        page.locator("#zoomIn").click()
        page.locator("#zoomOut").click()
        check(f"{tag}：K 線縮放鈕可按、圖還在", page.locator("#mainChart canvas").count() >= 1)
    step(f"{tag}：K 線縮放", zoom)

    def studies() -> None:
        check(f"{tag}：指標切到 MACD", study_pressed(page, "macd"))
        check(f"{tag}：指標切回成交金額", study_pressed(page, "volume"))
    step(f"{tag}：指標切換", studies)

    open_panel(page, tag, "#priceSummary", "現價")
    open_panel(page, tag, "#conclusionTrigger", "結論")
    open_panel(page, tag, "#lineUpper", "收復線")
    open_panel(page, tag, "#readCard", "直白分析")
    open_panel(page, tag, ".chip-card", "")

    def candle() -> None:
        canvas = page.locator("#mainChart canvas").first
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        x, y = box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.45
        if mobile:
            # 手機：點 K 棒先選取，再按「這一天的詳情」才開明細板。
            page.touchscreen.tap(x, y)
            page.locator("#mobileCandleDetails").wait_for(state="visible")
        else:
            page.mouse.move(x, y)
        tip = page.evaluate(TIP_STATE)
        check(f"{tag}：指到 K 棒浮出當天明細，底色不透明", tip == "ok", tip)
        if mobile:
            page.locator("#mobileCandleDetails").click()
        else:
            page.mouse.click(x, y)
        page.wait_for_function(PANEL_OPEN)
        page.locator("#panelTitle").wait_for(state="visible")
        check(f"{tag}：點 K 棒開當天的明細板", page.locator("#detailPanel").get_attribute("data-kind") == "candle"
              and len(page.locator("#panelBody").inner_text().strip()) > 20, page.locator("#panelTitle").text_content())
        page.mouse.click(4, 4)
        page.wait_for_function(PANEL_CLOSED)
        check(f"{tag}：點明細板以外的地方會收回", True)
    step(f"{tag}：K 棒明細", candle)

    def deep_layers() -> None:
        page.locator("#lineUpper").scroll_into_view_if_needed()
        page.locator("#lineUpper").click()
        page.wait_for_function(PANEL_OPEN)
        seen = []
        for kind in ("method", "sources", "samples", "table", "news"):
            # 從深入頁返回時，面板會記得「看詳細數字」原本是開著的；關著才需要點開。
            if not page.evaluate("document.querySelector('#panelBody .more-detail').open"):
                page.locator("#panelBody .more-detail > summary").click()
            page.locator(f"#panelBody [data-deep='{kind}']").click()
            page.wait_for_function("document.getElementById('panelPath').textContent.includes('深入')")
            seen.append((kind, len(page.locator("#panelBody").inner_text().strip())))
            page.locator("#panelBack").click()
            page.wait_for_function("!document.getElementById('panelPath').textContent.includes('深入')")
        check(f"{tag}：方法、來源、樣本、資料表、新聞五個深入頁都有內容且回得來", all(size > 20 for _, size in seen), seen)
    step(f"{tag}：面板深入頁", deep_layers)
    # 中途失敗也要把面板關掉，否則後面每一項都會被面板擋住而跟著失敗。
    page.keyboard.press("Escape")
    page.wait_for_function(PANEL_CLOSED)

    def screens() -> None:
        check(f"{tag}：籌碼選股有分頁", page.locator("#chipScreenTabs [role=tab]").count() >= 2)
        before = page.locator("#chipScreenList > li").count()
        check(f"{tag}：籌碼選股有結果", before >= 1, before)
        # 樣本資料的第一個分頁有 50 檔，「展開到 50 名」一定要在；被藏掉就是少了功能。
        page.locator("#chipScreenMore").click()
        page.wait_for_function("n => document.querySelectorAll('#chipScreenList > li').length > n", arg=before)
        check(f"{tag}：展開到 50 名", page.locator("#chipScreenList > li").count() > before)
        page.locator("#chipScreenSide button[data-side='sell']").click()
        page.wait_for_function("document.querySelector(\"#chipScreenSide button[data-side='sell']\").getAttribute('aria-pressed') === 'true'")
        check(f"{tag}：買超／賣超可切換", True)
        page.locator("#chipScreenSide button[data-side='buy']").click()
    step(f"{tag}：籌碼選股", screens)

    def rotation() -> None:
        check(f"{tag}：產業資金流向顯示資料日", "資料日" in page.locator("#rotationMeta").inner_text())
        check(f"{tag}：四種資金狀態圖例", page.locator("#rotationLegend > li").count() == 4)
        check(f"{tag}：象限圖有產業點", page.locator("#rotationChart circle").count() >= 10, page.locator("#rotationChart circle").count())
        check(f"{tag}：資金效率排行有列", page.locator("#rotationEfficiency > li").count() >= 1)
        check(f"{tag}：低基期回溫訊號有列", page.locator("#rotationRecovery > li").count() >= 1)
        page.locator(".rotation-table > summary").click()
        page.locator("#rotationTable").wait_for(state="visible")
        rows = page.locator("#rotationTable tr").count() - 1  # 扣掉表頭
        check(f"{tag}：表格檢視列出全部產業", rows >= 30, rows)
    step(f"{tag}：產業資金流向", rotation)

    def core() -> None:
        check(f"{tag}：長期核心持有有卡片", page.locator("#coreCards > *").count() >= 2)
        page.locator("#corePeriodSelect").select_option("1y")
        page.wait_for_timeout(400)
        check(f"{tag}：比較期間可切換、走勢圖還在", page.locator("#coreChart svg, #coreChart canvas").count() >= 1)
    step(f"{tag}：長期核心持有", core)

    def theme() -> None:
        page.locator("#themeToggle").click()
        check(f"{tag}：主題切到深色", "dark" in page.evaluate("getComputedStyle(document.documentElement).colorScheme"))
        page.locator("#themeToggle").click()
    step(f"{tag}：主題切換", theme)

    def search() -> None:
        page.locator("#stockSearchInput").fill("2330")
        page.locator("#stockSearchForm button[type=submit]").click()
        link = page.locator("#stockSearchResults a[data-code='2330']")
        link.wait_for()
        check(f"{tag}：搜尋時選股看板讓位", page.locator("#marketScreens").is_hidden())
        link.click()
        page.wait_for_url("**/stock.html?code=2330")
        shoot.ready_stock(page)
        check(f"{tag}：搜尋到個股頁", "台積電" in page.locator("#stockName").inner_text())
    step(f"{tag}：搜尋到個股", search)


def stock(page: Page, base: str, tag: str) -> None:
    page.goto(base + f"/stock.html?code={shoot.STOCK}", wait_until="domcontentloaded")
    shoot.ready_stock(page)
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    check(f"{tag}：報價有收盤價", page.locator("#close").inner_text().strip() not in ("", "—"))
    check(f"{tag}：操作策略有結論", page.locator("#strategy").is_visible() and page.locator("#strategyConclusion").inner_text().strip() != "—")
    check(f"{tag}：大戶持股有內容", "千張" in page.locator("#bigHolders").inner_text())
    ladder = page.locator("#ladder .lad b").all_inner_texts()
    check(f"{tag}：價位階梯五列都有價位", len(ladder) == 5 and all(text.strip() not in ("", "—") for text in ladder), ladder)

    def periods() -> None:
        for value, label in (("weekly", "週K"), ("monthly", "月K"), ("daily", "日K")):
            page.locator(f".hm-period button[data-value='{value}']").click()
            page.wait_for_function("label => document.getElementById('stockChartContext').textContent.includes(label)", arg=label)
        check(f"{tag}：週期切換日／週／月", True)
    step(f"{tag}：週期切換", periods)

    def averages() -> None:
        before = page.locator("#maControls input").count()
        page.locator("#addMa").click()
        check(f"{tag}：新增均線", page.locator("#maControls input").count() == before + 1)
    step(f"{tag}：均線", averages)

    def studies() -> None:
        check(f"{tag}：指標切到 MACD", study_pressed(page, "macd"))
        check(f"{tag}：指標切到外資", study_pressed(page, "foreign"))
    step(f"{tag}：指標切換", studies)

    def wheel_zoom() -> None:
        # 只確認滾輪縮小不會把圖弄壞；縮放的格數與歷史往前載入由 cloud/test 的 K 線手勢測試保護。
        canvas = page.locator("#chartHost canvas").first
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        for _ in range(12):
            page.mouse.wheel(0, 400)
        page.wait_for_timeout(600)
        check(f"{tag}：滾輪縮小後圖還在", canvas.is_visible() and canvas.bounding_box()["height"] > 100)
    step(f"{tag}：K 線滾輪", wheel_zoom)

    def compare() -> None:
        check(f"{tag}：和 0050 對照表有列", page.locator("#compareTableBody tr").count() >= 3)
        check(f"{tag}：報酬走勢圖有畫", page.locator("#compareChart svg, #compareChart canvas").count() >= 1)
    step(f"{tag}：和 0050 對照", compare)

    def outside() -> None:
        page.goto(base + f"/stock.html?code={shoot.OUTSIDE}", wait_until="domcontentloaded")
        shoot.ready_stock(page)
        check(f"{tag}：母體外顯示不在涵蓋範圍", page.locator("#outside").is_visible() and page.locator("#content").is_hidden())
    step(f"{tag}：母體外個股", outside)


def portfolio(page: Page, base: str, tag: str) -> None:
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(base + "/portfolio.html", wait_until="domcontentloaded")
    shoot.ready_portfolio(page)
    check(f"{tag}：沒有橫向捲動", no_overflow(page))
    check(f"{tag}：庫存總覽有市值", page.locator("#totalMarketValue").inner_text().strip() not in ("", "—"))

    def watchlist() -> None:
        before = page.locator("#watchlistRows").get_by_role("button", name="刪除").count()
        page.locator("#watchCode").fill("2327")
        page.locator("#watchHorizon").select_option("short")
        page.locator("#watchlistForm button[type=submit]").click()
        page.get_by_text("2327 國巨").first.wait_for()
        check(f"{tag}：加入自選", page.locator("#watchlistRows").get_by_role("button", name="刪除").count() == before + 1)
        page.get_by_role("button", name="從自選股刪除 2327").click()
        page.wait_for_function("n => document.querySelectorAll('#watchlistRows button').length === n", arg=before)
        check(f"{tag}：刪除自選", True)
    step(f"{tag}：自選股", watchlist)

    def holdings() -> None:
        before_value = page.locator("#totalMarketValue").inner_text()
        page.locator("#holdingCode").fill("6669")
        page.locator("#holdingShares").fill("100")
        page.locator("#holdingCost").fill("3000")
        page.locator("#holdingNote").fill("旅程檢查")
        page.locator("#holdingForm button[type=submit]").click()
        page.locator("#holdingRows").get_by_text("6669 緯穎").wait_for()
        check(f"{tag}：新增庫存後總覽更新", page.locator("#totalMarketValue").inner_text() != before_value)
        page.get_by_role("button", name="刪除庫存 6669").click()
        page.wait_for_function("value => document.getElementById('totalMarketValue').textContent === value", arg=before_value)
        check(f"{tag}：刪除庫存後總覽還原", True)
    step(f"{tag}：我的庫存", holdings)

    def theme() -> None:
        page.locator("#portfolioTheme").click()
        check(f"{tag}：主題切到深色並記住", page.evaluate("localStorage.getItem('market-theme')") == "dark")
        page.locator("#portfolioTheme").click()
    step(f"{tag}：主題切換", theme)


def main() -> None:
    proc, base = shoot.start_server("--features", "core")
    errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for size, (width, height) in shoot.SIZES.items():
                context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce",
                                              locale="zh-TW", timezone_id="Asia/Taipei", has_touch=size == "390")
                page = context.new_page()
                page.set_default_timeout(15_000)
                page.clock.set_fixed_time(shoot.FIXED_NOW)
                page.on("pageerror", lambda error: errors.append(str(error)))
                mobile = size == "390"
                step(f"首頁 {size}", lambda: home(page, base, f"首頁 {size}", mobile))
                step(f"個股 {size}", lambda: stock(page, base, f"個股 {size}"))
                step(f"自選庫存 {size}", lambda: portfolio(page, base, f"自選庫存 {size}"))
                context.close()
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    check("全程沒有頁面腳本錯誤", not errors, errors[:3])
    failed = [(name, detail) for name, ok, detail in results if not ok]
    print(f"{len(results) - len(failed)}／{len(results)} 項通過")
    for name, detail in failed:
        print(f"✗ {name}　{detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
