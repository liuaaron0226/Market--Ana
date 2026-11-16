"""用真的時間（不凍結動畫）走一遍 ⑥ 大戶持股與 ③ 庫存總覽兩個樣品的互動，有動態與「減少動態效果」各一遍。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/check-holders-holdings.py"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

here = Path(__file__).resolve().parent
fails = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


OVERFLOW = "document.documentElement.scrollWidth > document.documentElement.clientWidth"
with sync_playwright() as p:
    browser = p.chromium.launch()
    for motion in ("no-preference", "reduce"):
        for width in (320, 390, 1100):
            ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 700, reduced_motion=motion)
            tag = f"[{motion} {width}]"

            # ---- ⑥ 大戶持股
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto((here / "holders-weekly.html").as_uri(), wait_until="networkidle")
            page.wait_for_timeout(3200)
            card = page.locator("#holdCard")
            check(f"{tag} 大戶：一句結論與這一週的數字", page.locator("#holdAlign").inner_text().replace("\n", " ") == "連 4 週增加 千張以上大戶"
                  and page.locator("#nowValue").inner_text() == "58.56%" and page.locator("#nowChange").inner_text() == "較前一週 +0.13 個百分點",
                  [page.locator("#holdAlign").inner_text(), page.locator("#nowValue").inner_text(), page.locator("#nowChange").inner_text()])
            check(f"{tag} 大戶：這一週增加，這一張是浮起的；進場的記號已拿掉", card.get_attribute("data-state") == "bull" and "is-entering" not in (card.get_attribute("class") or ""))
            kinds = page.evaluate("[...document.querySelectorAll('#holdBars .col')].map(col => col.className.replace('col ', ''))")
            check(f"{tag} 大戶：每週增減一根小柱，最後四根是增加", len(kinds) == 12 and kinds[-4:] == ["up"] * 4 and kinds[-5] == "down", kinds)
            check(f"{tag} 大戶：一開始收著，收著的鍵鍵盤走不到", page.evaluate("document.getElementById('holdPanel').inert") and not card.evaluate("node => node.classList.contains('is-open')"))
            page.locator("#holdTop").click()
            page.wait_for_timeout(900)
            check(f"{tag} 大戶：點開後三個級距各一顆鍵", page.locator("#holdStrip .cell").all_inner_texts() == ["千張以上", "400 張以上", "全部股東"]
                  and not page.evaluate("document.getElementById('holdPanel').inert"), page.locator("#holdStrip .cell").all_inner_texts())
            check(f"{tag} 大戶：還沒換級距時下面寫千張以上的人數", page.locator("#holdRead").inner_text() == "持股 58.56%，179 人（人數 +4）", page.locator("#holdRead").inner_text())
            page.locator("#holdStrip .cell").nth(1).click()
            page.wait_for_timeout(500)
            check(f"{tag} 大戶：點 400 張以上，上面的數字與下面那一行都換過去",
                  page.locator("#nowValue").inner_text() == "63.30%" and page.locator("#nowChange").inner_text() == "和前一週一樣"
                  and page.locator("#holdRead").inner_text() == "持股 63.30%，337 人（人數 0）"
                  and page.locator("#holdStrip .cell").nth(1).get_attribute("aria-pressed") == "true",
                  [page.locator("#nowValue").inner_text(), page.locator("#nowChange").inner_text(), page.locator("#holdRead").inner_text()])
            kinds = page.evaluate("[...document.querySelectorAll('#holdBars .col')].map(col => col.className.replace('col ', ''))")
            check(f"{tag} 大戶：小柱換成那個級距的每週增減", kinds[-1] == "none" and kinds[-2] == "up", kinds)
            fits = page.evaluate("""[...document.querySelectorAll('#holdStrip .cell')].every(cell => { const c = cell.getBoundingClientRect(), n = cell.querySelector('.cell-name').getBoundingClientRect();
              return n.left >= c.left - .5 && n.right <= c.right + .5; })""")
            check(f"{tag} 大戶：鍵的名稱沒有凸出鍵外、沒有橫向捲動、沒有腳本錯誤", fits and not page.evaluate(OVERFLOW) and not errors, [fits, errors])
            page.locator("#holdTop").click()
            page.wait_for_timeout(700)
            check(f"{tag} 大戶：再點收起來", page.evaluate("document.getElementById('holdPanel').inert")
                  and page.evaluate("Math.round(document.querySelector('.hold-clip').getBoundingClientRect().height)") == 0)
            page.close()

            # ---- ③ 庫存總覽
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto((here / "holdings-overview.html").as_uri(), wait_until="networkidle")
            page.wait_for_timeout(3200)
            check(f"{tag} 庫存：總數那一行", page.locator("#hvTotal").inner_text().replace("\n", " ") == "$3,207,350 成本 $1,795,200 賺 +$1,412,150（+78.66%）", page.locator("#hvTotal").inner_text())
            rows = page.evaluate("""[...document.querySelectorAll('.hv-row')].map(row => ({ state: row.dataset.state, name: row.querySelector('.hv-name strong').textContent,
              ret: row.querySelector('.hv-ret').textContent, bars: [...row.querySelectorAll('.hv-bar i')].map(bar => [bar.className, Math.round(bar.getBoundingClientRect().width)]),
              open: row.classList.contains('is-open'), inert: row.querySelector('.hv-panel').inert }))""")
            check(f"{tag} 庫存：一列一檔，照市值由大到小；賺的浮起、賠的凹下", [(row["name"], row["state"], row["ret"]) for row in rows]
                  == [("台積電", "bull", "+128.06%"), ("長榮", "bull", "+14.19%"), ("元大台灣50", "bull", "+66.87%"), ("賠錢的一檔", "bear", "−10.00%")], rows)
            check(f"{tag} 庫存：橫條兩段——賺錢是成本＋賺的，賠錢是市值＋還差的；每一段至少看得到", [[kind for kind, _ in row["bars"]] for row in rows]
                  == [["base", "gain"]] * 3 + [["base", "lack"]] and all(width >= 2 for row in rows for _, width in row["bars"]), [row["bars"] for row in rows])
            check(f"{tag} 庫存：最大的那一檔橫條最長", rows[0]["bars"][0][1] + rows[0]["bars"][1][1] > rows[1]["bars"][0][1] + rows[1]["bars"][1][1] > rows[3]["bars"][0][1], [row["bars"] for row in rows])
            check(f"{tag} 庫存：一開始都收著，進場的記號已拿掉", all(not row["open"] and row["inert"] for row in rows) and not page.evaluate("document.getElementById('hvRows').classList.contains('is-entering')"))
            tops = page.locator(".hv-top")
            tops.nth(0).click()
            page.wait_for_timeout(700)
            facts = page.locator(".hv-row.is-open .hv-facts li").all_inner_texts()
            check(f"{tag} 庫存：點台積電看金額", [fact.replace("\n", " ") for fact in facts] == ["市值 $2,235,000", "成本 $980,000", "賺 +$1,255,000", "占庫存 69.7%"], facts)
            tops.nth(3).click()
            page.wait_for_timeout(700)
            opened = page.evaluate("[...document.querySelectorAll('.hv-row')].map(row => row.classList.contains('is-open'))")
            facts = page.locator(".hv-row.is-open .hv-facts li").all_inner_texts()
            check(f"{tag} 庫存：換點賠錢的那一檔，只開那一檔", opened == [False, False, False, True]
                  and [fact.replace("\n", " ") for fact in facts] == ["市值 $189,000", "成本 $210,000", "賠 −$21,000", "占庫存 5.9%"], [opened, facts])
            tops.nth(3).click()
            page.wait_for_timeout(600)
            check(f"{tag} 庫存：再點收起來", not page.locator(".hv-row.is-open").count())
            names_fit = page.evaluate("""[...document.querySelectorAll('.hv-top')].every(top => { const t = top.getBoundingClientRect();
              return [...top.children].every(child => { const c = child.getBoundingClientRect(); return c.left >= t.left - .5 && c.right <= t.right + .5; }); })""")
            check(f"{tag} 庫存：每一列的東西都在列裡面、沒有橫向捲動、沒有腳本錯誤", names_fit and not page.evaluate(OVERFLOW) and not errors, [names_fit, errors])
            page.close()
            ctx.close()
    browser.close()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
