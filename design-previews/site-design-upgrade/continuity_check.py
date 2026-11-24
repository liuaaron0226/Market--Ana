"""頁面之間的連續性：從這一頁點出去再回來，會不會回到同一個浮板、同一個清單、同一個捲動位置；
頁面上的返回鍵和瀏覽器的上一頁（手機的返回手勢）結果一不一樣。

只記錄現況，不修任何東西。對著本機預覽量（預設 http://127.0.0.1:5310，手機寬度 390×844，無頭 Chromium）。
注意：iPhone 的 Safari 對「上一頁」的處理和 Chromium 不同（它比較常把整頁原樣留著），這裡量到的不能代表真機。

用法：python design-previews/site-design-upgrade/continuity_check.py [網址]
輸出：.scratch/site-design-upgrade/continuity.json，並印出一張表。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch" / "site-design-upgrade" / "continuity.json"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5310"
PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)

STATE = r"""() => ({
  page: location.pathname.replace(/^\/$/, '/（首頁）') + location.search,
  scrollY: Math.round(scrollY),
  cat: document.querySelector('.hm-cats button[aria-expanded="true"]')?.textContent.trim() || null,
  filter: document.querySelector('#listFilter button[aria-pressed="true"]')?.textContent.trim().replace(/\s+/g, '') || null,
  panel: document.querySelector('#detailPanel.is-open #panelTitle')?.textContent.trim() || null,
  dialog: document.querySelector('dialog[open]')?.id || null,
  row: document.querySelector('.hv-row.is-open .hv-name, .depth-row.is-open .hv-name')?.textContent.trim().slice(0, 8) || null,
  tab: document.querySelector('.sf-tab[aria-pressed="true"]')?.textContent.trim() || null,
})"""
WAIT = "() => new Promise(r => setTimeout(r, 1800))"


def settle(page) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.evaluate(WAIT)


def click(page, selector: str) -> bool:
    target = page.locator(selector).first
    if target.count() == 0:
        return False
    target.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    target.click()
    return True


# 每一個情境：從哪一頁開始、先做哪些事（把頁面弄成「有狀態」）、點哪個連結離開、那一頁的返回鍵是哪一顆
SCENARIOS = [
    dict(name="首頁 籌碼選股清單 → 個股頁", start="/", steps=["#homeCats button[data-cat='screens']"], leave="#chipScreens a[href*='stock.html']", back_key=".bd-back"),
    dict(name="首頁 籌碼卡的明細板 → 個股頁", start="/", steps=["#homeCats button[data-cat='chips']", "#chipGrid .chip-card"], leave="#detailPanel a[href*='stock.html']", back_key=".bd-back"),
    dict(name="首頁 搜尋 → 個股頁", start="/", steps=["#searchToggle"], leave="#stockSearchDialog a[href*='stock.html']", back_key=".bd-back"),
    dict(name="首頁 消息 → 自選股與庫存", start="/", steps=["#homeCats button[data-cat='news']"], leave="#portfolioEntry", back_key=".pf-back"),
    dict(name="自選股與庫存（選「庫存」、展開一列）→ 個股頁", start="/portfolio.html", steps=["#listFilter button:nth-of-type(2)", "#holdingRows .hv-open"], leave="#holdingRows .hv-name", back_key=".bd-back"),
    dict(name="個股頁（展開「籌碼」、事實分頁選第二個）→ 大盤", start="/stock.html?code=2330", steps=["#stockCats button:nth-of-type(2)", "#stockFactsBlocks .sf-tab:nth-of-type(2)"], leave=".bd-back", back_key=None),
]


def run(browser, scenario: dict, how: str) -> dict:
    context = browser.new_context(color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei", **PHONE)
    page = context.new_page()
    page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
    page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
    out: dict[str, object] = {}
    try:
        page.goto(BASE + scenario["start"], wait_until="domcontentloaded")
        settle(page)
        for step in scenario["steps"]:
            if not click(page, step):
                out["錯誤"] = "找不到 " + step
                return out
            page.wait_for_timeout(900)
        link = page.locator(scenario["leave"]).first
        if link.count() == 0:
            out["錯誤"] = "找不到離開用的連結 " + scenario["leave"]
            return out
        link.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        before = page.evaluate(STATE)
        link.click()
        page.wait_for_url(lambda url: url != BASE + scenario["start"], timeout=8000)
        settle(page)
        out["離開前"] = before
        out["到了"] = page.evaluate(STATE)["page"]
        if how == "key":
            if not scenario["back_key"] or not click(page, scenario["back_key"]):
                out["錯誤"] = "這一頁沒有返回鍵"
                return out
        else:
            page.go_back()
        settle(page)
        after = page.evaluate(STATE)
        out["回來後"] = after
        out["回到同一頁"] = after["page"] == before["page"]
        out["不一樣的"] = [key for key in before if key != "page" and before[key] != after[key] and not (key == "scrollY" and abs(before[key] - after[key]) <= 4)]
    except Exception as exc:
        out["錯誤"] = str(exc)[:200]
    finally:
        context.close()
    return out


def main() -> None:
    report: dict[str, dict] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for scenario in SCENARIOS:
            report[scenario["name"]] = {"頁面上的返回鍵": run(browser, scenario, "key"), "瀏覽器的上一頁": run(browser, scenario, "history")}
            print(scenario["name"], flush=True)
            for how, result in report[scenario["name"]].items():
                if "錯誤" in result:
                    print(f"   {how}：{result['錯誤']}")
                    continue
                same = "回到同一頁" if result["回到同一頁"] else f"沒有回到原本那一頁（到了 {result['回來後']['page']}）"
                lost = "、".join(f"{key} {result['離開前'][key]}→{result['回來後'][key]}" for key in result["不一樣的"]) or "狀態都還在"
                print(f"   {how}：{same}；{lost}")
        browser.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
