"""首頁「各產業強勢股」（本人 2026-11-20，排法 A）在本機預覽上的實際檢查與截圖：1440／390 × 淺色／深色。

用法：python design-previews/3d-redesign/check-rotation-strong.py
先備：照 preview-server.mjs 開頭備好資料包並跑過 node cloud/build.mjs；cloud/.screens-pack.json 的 rotation 要帶 strong_stocks
（本機舊資料包沒有的話，檢查會在第一項就停下來說明）。
輸出：design-previews/rotation-strong/shots/live-{1440,390}-{light,dark}.png（PNG 不入版控），最後印出每一項檢查的結果。
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

import shoot

OUT = Path(__file__).resolve().parents[1] / "rotation-strong" / "shots"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((name, bool(ok), str(detail)))
    print(("  ok  " if ok else "  FAIL ") + name + (f"｜{detail}" if detail != "" and not ok else ""))


# 名單裡被擠掉的東西：名稱被截、代號或漲停記號跑出名稱那一格、產業名稱折行或被截
SQUEEZED = """() => {
  const out = [];
  for (const row of document.querySelectorAll('#rotationStrong .strong-row')) {
    const cell = row.querySelector('.strong-name'), name = cell.querySelector('b');
    const edge = cell.getBoundingClientRect().right + 0.5;
    if (name.scrollWidth > name.clientWidth) out.push(name.textContent);
    for (const part of cell.querySelectorAll('small, i')) if (part.getBoundingClientRect().right > edge) out.push(`${name.textContent} ${part.textContent}`);
  }
  for (const title of document.querySelectorAll('#rotationStrong .strong-card h5')) {
    if (title.scrollWidth > title.clientWidth || title.getBoundingClientRect().height > 26) out.push(title.textContent);
  }
  return out;
}"""


def open_rotation(page, base: str) -> None:
    page.goto(base + "/")
    page.locator("[data-cat='rotation']").click()
    page.locator("#rotationTerrain .terrain-stage").scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('#rotationTerrain .terrain-city').classList.contains('is-grown')")


def run(browser, base: str, size: str, width: int, height: int, scheme: str) -> None:
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce",
                                  locale="zh-TW", timezone_id="Asia/Taipei", device_scale_factor=2)
    page = context.new_page()
    page.add_init_script(f"try{{localStorage.setItem('market-theme','{scheme}')}}catch(e){{}}")
    page.clock.set_fixed_time(shoot.FIXED_NOW)
    tag = f"{size} {scheme}"
    open_rotation(page, base)
    strong = page.locator("#rotationStrong")
    if not strong.is_visible():
        raise SystemExit("預覽的資料包沒有 strong_stocks：先把 cloud/.screens-pack.json 的 rotation 補上再跑")
    page.wait_for_timeout(600)
    page.locator("#rotationBlock").screenshot(path=str(OUT / f"live-{size}-{scheme}.png"))

    tabs = strong.locator(".strong-seg button")
    check(f"{tag}：四個資金狀態鍵", tabs.count() == 4)
    check(f"{tag}：一進來停在第一個（資金領先）", tabs.nth(0).get_attribute("aria-selected") == "true")
    cards = strong.locator(".strong-card")
    check(f"{tag}：每張卡 1～3 檔",
          cards.count() >= 1 and all(cards.nth(i).locator(".strong-row").count() in (1, 2, 3) for i in range(cards.count())))
    first = cards.first.locator(".strong-row").first
    check(f"{tag}：每一列連到個股頁", first.get_attribute("href").startswith("stock.html?code="))
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check(f"{tag}：頁面沒有橫向捲動（多出 {overflow}px）", overflow == 0)
    box = strong.bounding_box()
    check(f"{tag}：整段在畫面寬度內", box["x"] >= 0 and box["x"] + box["width"] <= width)
    for index in range(4):
        tabs.nth(index).click()
        squeezed = page.evaluate(SQUEEZED)
        check(f"{tag}：第 {index + 1} 個資金狀態的名稱、代號、漲停記號與產業名稱都沒有被擠掉", not squeezed, squeezed)
    tabs.nth(0).click()
    # 換到最後一個資金狀態：內容跟著換，鍵的選取狀態也跟著換
    before = strong.locator(".strong-panel").inner_text()
    tabs.nth(3).click()
    check(f"{tag}：點另一個鍵就換過去",
          tabs.nth(3).get_attribute("aria-selected") == "true" and tabs.nth(0).get_attribute("aria-selected") == "false")
    check(f"{tag}：名單那一塊標明是哪一個鍵的內容",
          strong.locator(".strong-panel").get_attribute("aria-labelledby") == tabs.nth(3).get_attribute("id"))
    check(f"{tag}：換鍵之後名單內容不同", strong.locator(".strong-panel").inner_text() != before)
    tabs.nth(0).click()

    # 點樓：明細裡也有這個產業的強勢股，而且點得到
    tower = page.locator("#rotationTerrain .terrain-tower[data-rank='0']")
    tower.focus()
    page.keyboard.press("Enter")
    detail = page.locator("#rotationTerrain .terrain-detail")
    detail.locator(".strong-detail").wait_for()
    name = tower.get_attribute("data-name")
    expected = page.evaluate("""async name => (await (await fetch('/api/rotation')).json()).strong_stocks.groups[name].map(s => s.code)""", name)
    shown = [href.split("code=")[1] for href in detail.locator(".strong-row").evaluate_all("rows => rows.map(r => r.getAttribute('href'))")]
    check(f"{tag}：點樓明細列出 {name} 的強勢股 {shown}", shown == expected)
    if expected:
        detail.locator(".strong-row").first.scroll_into_view_if_needed()
        detail.locator(".strong-row").first.click()
        page.wait_for_url("**/stock.html?code=*")
        check(f"{tag}：點明細裡的股票進到個股頁 {page.url.split('/')[-1]}", f"code={expected[0]}" in page.url)
        open_rotation(page, base)

    # 名單裡的一列點了也進個股頁
    row = page.locator("#rotationStrong .strong-row").first
    code = row.get_attribute("href").split("code=")[1]
    row.scroll_into_view_if_needed()
    row.click()
    page.wait_for_url("**/stock.html?code=*")
    check(f"{tag}：點名單裡的股票進到個股頁", f"code={code}" in page.url)

    # 舊的發布結果沒有強勢股：整段不出現，地形與點樓明細照舊
    def without_strong(route):
        response = route.fetch()
        body = response.json()
        body["strong_stocks"] = None
        route.fulfill(response=response, body=json.dumps(body, ensure_ascii=False))
    page.route("**/api/rotation", without_strong)
    open_rotation(page, base)
    check(f"{tag}：沒有強勢股資料時整段不顯示", not page.locator("#rotationStrong").is_visible())
    page.locator("#rotationTerrain .terrain-tower[data-rank='0']").focus()
    page.keyboard.press("Enter")
    page.locator("#rotationTerrain .terrain-detail h4").wait_for()
    check(f"{tag}：沒有資料時點樓明細照舊", page.locator("#rotationTerrain .terrain-detail .strong-detail").count() == 0)
    context.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    proc, base = shoot.start_server("--features", "core")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for size, (width, height) in shoot.SIZES.items():
                for scheme in ("light", "dark"):
                    run(browser, base, size, width, height, scheme)
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    failed = [name for name, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)}／{len(results)} 項通過")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
