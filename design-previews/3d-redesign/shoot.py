"""立體重設計的截圖巡檢：三個頁面 × 淺色／深色 × 桌機 1440／手機 390，外加幾個平常看不到的狀態。

每次自己起預覽伺服器（隨機埠、即時行情離線、固定樣本資料與固定時間），拍完就關，不碰其他埠與任何使用者資料。
換皮前跑一次留基準，之後每張票換完再跑一次，用同名檔案逐張對照。
頁面的標題字型來自 Google Fonts（web/bold.css），所以拍的時候要有網路；沒載到時 report.json 的
serif_font_loaded 會是 false，那一批的頁高與標題字形不能拿來和有載到的批次比。
每次執行會先清掉輸出資料夾裡舊的 PNG，避免上一輪留下的圖混進來。

用法（在工作目錄根目錄）：
    python design-previews/3d-redesign/shoot.py --out after-04              # 存到 shots/after-04/
    python design-previews/3d-redesign/shoot.py --out after-04 --only core  # 只拍必要的 12 張

--out 一定要給。shots/baseline/ 是換皮前的基準，PNG 不入版控、蓋掉就沒了，換皮後不要再拍進去。
有頁面錯誤、非預期的失敗請求、橫向捲動、主題不對或字型沒載到時，結束碼是 1。

PNG 不入版控（.gitignore 的 design-previews/**/*.png）；每個資料夾的 report.json 會入版控，
記每張圖的頁面錯誤、主控台錯誤、失敗的請求與有沒有橫向捲動。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# 伺服器與瀏覽器都固定在這一刻（週六、收盤）：開盤／收盤判斷與「幾天前」之類的文字每次相同。
FIXED_NOW = "2026-11-14T10:00:00+08:00"
SIZES = {"1440": (1440, 900), "390": (390, 844)}
THEMES = ("light", "dark")
STOCK = "2330"
OUTSIDE = "1232"
# 母體外樣本的代號要和 preview-server.mjs 的 OUTSIDE 一致。
# 預覽伺服器的即時行情離線，大盤即時行情一定回 503；母體外的個股照設計回 404。只有這兩種組合是預期狀態。
EXPECTED_FAILURES = {"/api/live": 503, f"/api/stocks/{OUTSIDE}/analysis": 404}


def start_server(*extra: str) -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(
        ["node", "--no-warnings", str(HERE / "preview-server.mjs"), "--port", "0", "--now", FIXED_NOW, *extra],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )
    line = proc.stdout.readline()
    found = re.search(r"http://127\.0\.0\.1:\d+", line)
    if not found:
        rest = proc.stdout.read()
        proc.kill()
        raise SystemExit(f"預覽伺服器沒有起來：\n{line}{rest}")
    return proc, found.group(0)


def ready_home(page: Page) -> None:
    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
    page.wait_for_selector("#mainChart canvas")
    for block in ("chipScreens", "rotationBlock", "coreHoldings"):
        page.wait_for_function(f"document.getElementById('{block}').dataset.state !== 'loading'")


def ready_stock(page: Page) -> None:
    page.wait_for_function("!document.getElementById('content').hidden || !document.getElementById('outside').hidden")
    if page.evaluate("!document.getElementById('content').hidden"):
        page.wait_for_selector("#chartHost canvas")
        page.wait_for_function("document.getElementById('compareStatus').dataset.state !== 'loading'")


def ready_portfolio(page: Page) -> None:
    page.wait_for_function("document.getElementById('portfolioFreshness').textContent.trim() !== '讀取中'")


def ready_account(page: Page) -> None:
    page.wait_for_load_state("networkidle")


PAGES = {
    "home": ("/", ready_home),
    "stock": (f"/stock.html?code={STOCK}", ready_stock),
    "portfolio": ("/portfolio.html", ready_portfolio),
    "stock-outside": (f"/stock.html?code={OUTSIDE}", ready_stock),
    "apply": ("/apply/", ready_account),
    "admin": ("/admin/", ready_account),
}


def settle(page: Page) -> None:
    """等字型與版面停下來：標題字型是網路字型，晚到會讓標題變高、整頁往下推。"""
    stable, last = 0, -1
    for _ in range(40):
        page.evaluate("document.fonts.ready.then(() => true)")
        height = page.evaluate("document.documentElement.scrollHeight")
        stable = stable + 1 if height == last else 0
        if stable >= 3:
            return
        last = height
        page.wait_for_timeout(300)


def open_price_panel(page: Page) -> None:
    page.locator("#priceSummary").click()
    page.wait_for_function("document.getElementById('detailPanel').getAttribute('aria-hidden') === 'false'")


# 首頁的四類預設是收著的（A 包第 4 片）；要看某一類的內容得先點它的分類鍵。
HOME_CATS = ("chips", "screens", "rotation", "core")


def open_cat(page: Page, cat: str) -> None:
    """展開首頁的一類；已經開著就不動（再點一次會收回）。"""
    button = page.locator(f"#homeCats button[data-cat='{cat}']")
    button.scroll_into_view_if_needed()
    if button.get_attribute("aria-expanded") != "true":
        button.click()
    page.wait_for_function("cat => document.querySelector(`#homeCats button[data-cat='${cat}']`).getAttribute('aria-expanded') === 'true'", arg=cat)


def grown_terrain(page: Page) -> None:
    """展開產業資金流向，等資金地形的樓長好，並把地形捲到畫面內。"""
    open_cat(page, "rotation")
    page.locator("#rotationTerrain .terrain-stage").scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('#rotationTerrain .terrain-city').classList.contains('is-grown')")
    page.wait_for_timeout(400)


def roof_of(page: Page, rank: str) -> dict:
    """資金地形裡某一棟樓的名稱與樓頂中心在畫面上的位置。rank："largest" 成交額最大（樓頂一定露在外面）、"smallest" 最小。"""
    return page.evaluate("""rank => {
      const towers = [...document.querySelectorAll('#rotationTerrain .terrain-tower')].sort((a, b) => a.dataset.rank - b.dataset.rank);
      const tower = rank === 'smallest' ? towers[towers.length - 1] : towers[0];
      const box = tower.querySelector('.top').getBoundingClientRect();
      return { name: tower.dataset.name, x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }""", rank)


def pick_tallest_tower(page: Page) -> None:
    """點成交額最大的那一棟樓。"""
    grown_terrain(page)
    roof = roof_of(page, "largest")
    page.mouse.click(roof["x"], roof["y"])
    page.wait_for_timeout(300)


def open_rotation_table(page: Page) -> None:
    grown_terrain(page)
    page.locator("#rotationBlock .rotation-open").click()
    page.locator("#rotationTable").wait_for(state="visible")


def open_search(page: Page) -> None:
    page.locator("#searchToggle").click()
    page.locator("#stockSearchInput").wait_for(state="visible")


def shoot(browser, base: str, out: Path, report: dict, name: str, page_key: str, size: str, theme: str,
          after=None, full_page: bool = True) -> None:
    path, ready = PAGES[page_key]
    width, height = SIZES[size]
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                  reduced_motion="reduce", locale="zh-TW", timezone_id="Asia/Taipei")
    page = context.new_page()
    page.set_default_timeout(30_000)
    page.clock.set_fixed_time(FIXED_NOW)
    entry: dict[str, object] = {"page_errors": [], "console_errors": [], "failed_requests": []}
    page.on("pageerror", lambda error: entry["page_errors"].append(str(error)))
    page.on("console", lambda message: entry["console_errors"].append(message.text) if message.type == "error" else None)

    def on_response(response) -> None:
        url = response.url.replace(base, "")
        if response.status >= 400 and EXPECTED_FAILURES.get(url.split("?")[0]) != response.status:
            entry["failed_requests"].append(f"{response.status} {url}")

    def on_request_failed(request) -> None:
        # 連不上、被中斷這種根本沒有回應的請求；換頁時瀏覽器自己取消的不算。
        if "ERR_ABORTED" not in (request.failure or ""):
            entry["failed_requests"].append(f"沒有回應 {request.url.replace(base, '')}")

    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    # 個股頁與自選庫存頁記得上次的主題；首頁不記，得像使用者一樣按切換鈕。
    page.add_init_script(f"try{{localStorage.setItem('market-theme','{theme}')}}catch(e){{}}")
    try:
        page.goto(base + path, wait_until="domcontentloaded")
        ready(page)
        # 首頁也讀存起來的主題了（2026-11-15 修正換頁主題跑掉），不用再另外按一次切換鍵
        if after:
            after(page)
        page.mouse.move(2, 2)
        settle(page)
        entry["serif_font_loaded"] = page.evaluate("document.fonts.check('600 24px \"Noto Serif TC\"', '台股')")
        entry["theme"] = page.evaluate("getComputedStyle(document.documentElement).colorScheme")
        entry["theme_ok"] = theme in entry["theme"]
        entry["h_overflow_px"] = page.evaluate("document.documentElement.scrollWidth - innerWidth")
        entry["page_height"] = page.evaluate("document.documentElement.scrollHeight")
        page.screenshot(path=str(out / f"{name}.png"), full_page=full_page)
    except Exception as error:  # 單張失敗不擋其他張，但要留在報告裡。
        entry["shot_failed"] = str(error).splitlines()[0]
    # 離線造成的主控台 503 訊息不帶網址，與 failed_requests 重複，只留真正的腳本錯誤。
    entry["console_errors"] = [text for text in entry["console_errors"] if "Failed to load resource" not in text]
    report[name] = entry
    context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="shots/ 底下的資料夾名稱，例如 after-04")
    parser.add_argument("--only", choices=("core", "expired", "flags", "states", "cats"), help="只拍其中一組")
    args = parser.parse_args()
    out = HERE / "shots" / args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    report: dict[str, object] = {}
    groups = [args.only] if args.only else ["core", "expired", "flags", "states", "cats"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        def run(server_args: tuple[str, ...], jobs) -> None:
            proc, base = start_server(*server_args)
            try:
                jobs(base)
            finally:
                proc.kill()
                proc.wait()

        def each(base: str, prefix: str, page_keys: tuple[str, ...], themes=THEMES, **kwargs) -> None:
            for page_key in page_keys:
                for size in SIZES:
                    for theme in themes:
                        shoot(browser, base, out, report, f"{page_key}{prefix}-{size}-{theme}", page_key, size, theme, **kwargs)

        if "core" in groups or "states" in groups or "cats" in groups:
            def core_jobs(base: str) -> None:
                # 必要的 12 張：正式站目前的功能旗標，樣本資料的劇本與直白分析都還沒過期。
                if "core" in groups:
                    each(base, "", ("home", "stock", "portfolio"))
                if "states" in groups:
                    each(base, "", ("stock-outside", "apply", "admin"), themes=("light",))
                    for size in SIZES:
                        for theme in THEMES:
                            shoot(browser, base, out, report, f"home-panel-price-{size}-{theme}", "home", size, theme,
                                  after=open_price_panel, full_page=False)
                if "cats" in groups:
                    # 首頁四類各展開一次（整頁），加上搜尋面板打開的樣子（只拍可見範圍）。
                    for size in SIZES:
                        for theme in THEMES:
                            for cat in HOME_CATS:
                                shoot(browser, base, out, report, f"home-cat-{cat}-{size}-{theme}", "home", size, theme,
                                      after=grown_terrain if cat == "rotation" else (lambda page, cat=cat: open_cat(page, cat)))
                            shoot(browser, base, out, report, f"home-search-{size}-{theme}", "home", size, theme,
                                  after=open_search, full_page=False)
                            # 資金地形：點了一棟樓、排行表格的面板打開（都只拍可見範圍）
                            shoot(browser, base, out, report, f"home-terrain-picked-{size}-{theme}", "home", size, theme,
                                  after=pick_tallest_tower, full_page=False)
                            shoot(browser, base, out, report, f"home-rotation-table-{size}-{theme}", "home", size, theme,
                                  after=open_rotation_table, full_page=False)
            run(("--features", "core"), core_jobs)
        if "expired" in groups:
            # 劇本與直白分析過期時的首頁：人寫的文字（劇本日、直白分析）不顯示；收復線／防守線每天算，照樣有。
            run(("--features", "core", "--expired"), lambda base: each(base, "-expired", ("home",)))
        if "flags" in groups:
            # 功能旗標全開：今日 AI 解讀（無樣本，未就緒狀態）與今日股票水位（有樣本）。
            run(("--features", "all"), lambda base: each(base, "-all", ("home", "portfolio")))
        browser.close()

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    problems = {name: entry for name, entry in report.items()
                if entry.get("shot_failed") or entry["page_errors"] or entry["console_errors"]
                or entry["failed_requests"] or entry.get("h_overflow_px") or not entry.get("serif_font_loaded")
                or not entry.get("theme_ok")}
    print(f"{len(report)} 張，存到 {out}")
    print(json.dumps(problems, ensure_ascii=False, indent=1) if problems else "沒有頁面錯誤、失敗請求或橫向捲動，主題正確，標題字型都有載到")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
