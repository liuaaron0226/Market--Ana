"""用真的時間（不凍結動畫）走一遍 ⑤ 樣品的互動：開、換一期、連點、收，最後的狀態要一致。"""
from pathlib import Path
from playwright.sync_api import sync_playwright
sample = Path("design-previews/3d-redesign/pro-samples/trend-matrix.html").resolve()
STATE = """() => [...document.querySelectorAll('.trend-row')].map(r => ({
  open: r.classList.contains('is-open'), closing: r.classList.contains('is-closing'),
  expanded: r.querySelector('.trend-top').getAttribute('aria-expanded'), inert: r.querySelector('.trend-panel').inert,
  panel: Math.round(r.querySelector('.trend-clip').getBoundingClientRect().height),
  blk: getComputedStyle(r.querySelector('.blk')).opacity, runs: r.querySelector('.cell').getAnimations({ subtree: true }).length,
  read: !r.querySelector('.trend-read').hidden }))"""
fails = []
def check(label, got, want):
    ok = got == want
    print("OK " if ok else "FAIL", label, "" if ok else f"got {got} want {want}")
    if not ok: fails.append(label)
with sync_playwright() as p:
    b = p.chromium.launch()
    for motion in ("no-preference", "reduce"):
        ctx = b.new_context(viewport={"width": 390, "height": 760}, has_touch=True, reduced_motion=motion)
        pg = ctx.new_page(); errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(sample.as_uri(), wait_until="networkidle"); pg.wait_for_timeout(3200)
        tops = pg.locator(".trend-top")
        s = pg.evaluate(STATE)
        check(f"[{motion}] 一開始三列都收著", [(r["open"], r["inert"], r["panel"], r["blk"]) for r in s], [(False, True, 0, "1")] * 3)
        tops.nth(1).tap(); pg.wait_for_timeout(1300)
        s = pg.evaluate(STATE)
        check(f"[{motion}] 點中期：只有中期開", [r["open"] for r in s], [False, True, False])
        check(f"[{motion}] 中期的格子可以走到、小塊藏起來", (s[1]["inert"], s[1]["expanded"], s[1]["blk"], s[1]["panel"] > 60), (False, "true", "0", True))
        # 鍵落位後：偏多的小柱在名稱上面、偏空的在名稱下面；鍵都在同一個高度
        marks = pg.evaluate("""() => [...document.querySelectorAll('.trend-row')[1].querySelectorAll('.cell')].map(c => [c.classList.contains('bull'),
          c.querySelector('.tag').getBoundingClientRect().top < c.querySelector('.cell-name').getBoundingClientRect().top, Math.round(c.getBoundingClientRect().top)])""")
        check(f"[{motion}] 偏多的小柱在名稱上面、偏空的在下面", [is_bull == above for is_bull, above, _ in marks], [True] * 4)
        check(f"[{motion}] 四顆鍵同一個高度", len({top for _, _, top in marks}), 1)
        pg.locator(".trend-row").nth(1).locator(".cell").nth(2).tap(); pg.wait_for_timeout(150)
        check(f"[{motion}] 點一格出現讀數", pg.locator(".trend-row").nth(1).locator(".trend-read").inner_text(), "MA20 39,850 高於 MA60 38,900")
        tops.nth(2).tap(); pg.wait_for_timeout(1300)
        s = pg.evaluate(STATE)
        check(f"[{motion}] 換點長期：中期收起來、長期開", [(r["open"], r["closing"]) for r in s], [(False, False), (False, False), (True, False)])
        check(f"[{motion}] 中期收好：小塊回來、讀數收掉、動畫清掉", (s[1]["blk"], s[1]["read"], s[1]["panel"], s[1]["inert"], s[1]["runs"]), ("1", False, 0, True, 0))
        # 連點：開著的長期連點三下（收、收到一半再點、再點）
        tops.nth(2).tap(); pg.wait_for_timeout(60); tops.nth(2).tap(); pg.wait_for_timeout(60); tops.nth(0).tap(); pg.wait_for_timeout(1500)
        s = pg.evaluate(STATE)
        check(f"[{motion}] 連點之後狀態一致", [(r["open"], r["closing"], r["expanded"], r["inert"]) for r in s][1:], [(False, False, "false", True)] * 2)
        check(f"[{motion}] 短期開著", (s[0]["open"], s[0]["expanded"]), (True, "true"))
        check(f"[{motion}] 沒有橫向捲動", pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"), False)
        check(f"[{motion}] 沒有腳本錯誤", errors, [])
        ctx.close()
    b.close()
print("失敗", len(fails))
