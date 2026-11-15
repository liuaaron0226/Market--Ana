"""大戶持股收在「籌碼」裡：收著的時候進場動態不能先播掉；點開才播。用十二週的假資料（才有小柱與動態）。"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5197"
ROWS = """(() => { const rows = []; let big = 57.5; for (let i = 0; i < 12; i += 1) { big += (i % 3 ? 0.2 : -0.1);
  const day = new Date(Date.UTC(2026, 7, 28) + i * 7 * 86400000).toISOString().slice(0, 10);
  rows.push({ date: day, pct_1000: Number(big.toFixed(2)), holders_1000: 170 + i, pct_400: 62.9 + i * 0.01, holders_400: 330, holders: 668000 + i }); } return rows; })()"""
fails = 0
with sync_playwright() as p:
    b = p.chromium.launch()
    for label, width in (("手機", 390), ("電腦", 1440)):
        ctx = b.new_context(viewport={"width": width, "height": 844 if width < 700 else 900}, reduced_motion="no-preference", locale="zh-TW")
        pg = ctx.new_page()
        pg.goto(BASE + "/stock.html?code=2330", wait_until="load")
        pg.locator("#content:not([hidden])").wait_for(state="visible", timeout=30000)
        pg.wait_for_timeout(800)
        pg.evaluate("rows => { const host = document.getElementById('bigHolders'); host.replaceChildren(); window.HoldersCard.render(host, null); window.HoldersCard.render(host, window.HoldersWeekly.read(rows)); }", pg.evaluate(ROWS))
        pg.wait_for_timeout(2500)   # 比「停穩才播」的 400ms 久得多
        cls = pg.evaluate("document.querySelector('#bigHolders .hold-card').className")
        waiting = "is-waiting" in cls
        print("OK  " if waiting else "FAIL", f"{label}：收著的時候還在等，沒有先播掉", cls)
        fails += not waiting
        key = pg.locator("#stockCats button[aria-controls='chipsPanel']")
        key.scroll_into_view_if_needed(); key.click()
        pg.evaluate("document.querySelector('#bigHolders .hold-card').scrollIntoView({ block: 'center' })")
        try:
            pg.wait_for_function("document.querySelector('#bigHolders .hold-card').classList.contains('is-entering')", timeout=4000)
            played = True
        except Exception:
            played = False
        print("OK  " if played else "FAIL", f"{label}：點開「籌碼」、捲到它之後才播進場", pg.evaluate("document.querySelector('#bigHolders .hold-card').className"))
        fails += not played
        ctx.close()
    b.close()
print("失敗", fails)
sys.exit(1 if fails else 0)
