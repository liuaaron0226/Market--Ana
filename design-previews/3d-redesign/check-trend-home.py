"""首頁「短中長期走勢」裡旅程檢查看不到的部分（旅程檢查開著「減少動態效果」，而且樣本資料只有一種情況）：
1. 有動態時：進場要等捲到這一塊才播、只播一次；切換 K 線週期不重播；點開時小柱飛下來、收起來時飛回去，最後的狀態要對。
2. 樣本資料沒有的三種情況，直接把改過的資料交給畫面畫：沒有方向的票、資料不足的期、票與結論對不上的期；以及今天沒有走勢判讀。
3. 資料換了重畫：開著的那一期與選中的鍵照原樣留著。
用法：先開預覽（node design-previews/3d-redesign/preview-server.mjs），再跑 python design-previews/3d-redesign/check-trend-home.py [網址，預設 http://127.0.0.1:5197/]"""
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5197/"
fails = []


def check(label, ok, detail=""):
    print("OK  " if ok else "FAIL", label, "" if ok else detail)
    if not ok:
        fails.append(label)


CLASS = "document.getElementById('marketTrend').className"
MARKS = "[...document.querySelectorAll('#marketTrend .trend-row')].map(row => row.querySelector('.blk') ? getComputedStyle(row.querySelector('.blk')).opacity : null)"
OPEN = "[...document.querySelectorAll('#marketTrend .trend-row')].map(row => row.classList.contains('is-open') && !row.classList.contains('is-closing'))"
# 把目前的走勢資料改一下再交給畫面畫，回傳畫出來的樣子
REDRAW = """change => { const trend = JSON.parse(JSON.stringify(window.MinimalApp.trend)); if (change !== 'none') new Function('trend', change)(trend);
  const section = document.getElementById('marketTrend'); window.TrendMatrix.render(section, change === 'none' ? null : window.TrendVotes.read(trend));
  return { align: section.querySelector('.trend-align').hidden ? null : section.querySelector('.trend-align').innerText.replace(/\\s+/g, ' ').trim(),
    message: section.querySelector('.market-trend-error') ? section.querySelector('.market-trend-error').textContent : null,
    rows: [...section.querySelectorAll('.trend-row')].map(row => ({ state: row.dataset.state, name: row.querySelector('.trend-name').innerText.replace(/\\s+/g, ' ').trim(),
      button: Boolean(row.querySelector('button.trend-top')), sentence: row.querySelector('.trend-sentence') ? row.querySelector('.trend-sentence').textContent : null,
      marks: row.querySelectorAll('.blk').length, open: row.classList.contains('is-open'),
      cells: [...row.querySelectorAll('.cell')].map(cell => [cell.querySelector('.cell-name').textContent, cell.className.replace('cell ', ''), cell.getAttribute('aria-pressed')]),
      read: row.querySelector('.trend-read') && !row.querySelector('.trend-read').hidden ? row.querySelector('.trend-read').textContent : null })) }; }"""

with sync_playwright() as p:
    browser = p.chromium.launch()

    # ---- 1. 有動態
    for label, width, height in (("手機", 390, 760), ("桌機", 1440, 700)):
        ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=width < 700, reduced_motion="no-preference", locale="zh-TW")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector("#marketTrend .trend-row")
        below = page.evaluate("document.getElementById('marketTrendGrid').getBoundingClientRect().top > innerHeight")
        check(f"{label}：這一塊一開始在畫面下方", below)
        check(f"{label}：還沒捲到之前在等（小柱先藏著）", "is-waiting" in page.evaluate(CLASS) and set(page.evaluate(MARKS)) == {"0"}, page.evaluate(CLASS))
        page.locator("#marketTrendGrid").scroll_into_view_if_needed()
        page.wait_for_function(f"{CLASS}.includes('is-entering')", timeout=3000)
        check(f"{label}：捲到之後開始播", True)
        page.wait_for_function(f"!{CLASS}.includes('is-entering') && !{CLASS}.includes('is-waiting')", timeout=8000)
        check(f"{label}：播完之後小柱都在、沒有留下進場的記號", set(page.evaluate(MARKS)) == {"1"} and page.evaluate(CLASS).strip() == "market-trend", page.evaluate(CLASS))
        # 切換 K 線週期再切回來：不重播
        for value in ("weekly", "daily"):
            page.locator(".chart-chip[aria-controls='periodMenu'], #periodChip").first.click()
            page.locator(f".hm-period [data-value='{value}']").click()
            page.wait_for_timeout(500)
        check(f"{label}：切換 K 線週期再切回來不重播", page.evaluate(CLASS).strip() == "market-trend" and set(page.evaluate(MARKS)) == {"1"}, page.evaluate(CLASS))
        page.locator("#marketTrendGrid").scroll_into_view_if_needed()
        tops = page.locator("#marketTrend button.trend-top")
        tops.nth(2).click()
        page.wait_for_timeout(1300)
        check(f"{label}：點開長期，小柱飛完之後鍵裡的小柱沒有殘留位移",
              page.evaluate("[...document.querySelectorAll('#marketTrend .trend-row.is-open .tag')].every(tag => getComputedStyle(tag).transform === 'none')")
              and page.evaluate(OPEN) == [False, False, True], page.evaluate(OPEN))
        tops.nth(2).click()
        page.wait_for_timeout(60)
        tops.nth(2).click()   # 收到一半再點一次：不該卡住
        page.wait_for_timeout(1300)
        tops.nth(0).click()
        page.wait_for_timeout(1300)
        check(f"{label}：收到一半連點之後狀態一致", page.evaluate(OPEN) == [True, False, False]
              and page.evaluate("[...document.querySelectorAll('#marketTrend .trend-row')].every(row => !row.classList.contains('is-closing'))"), page.evaluate(OPEN))
        tops.nth(0).click()
        page.wait_for_timeout(900)
        check(f"{label}：收起來之後小柱都回到橫條上", page.evaluate(OPEN) == [False, False, False] and set(page.evaluate(MARKS)) == {"1"}, page.evaluate(MARKS))
        check(f"{label}：沒有橫向捲動、沒有腳本錯誤", not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth") and not errors, errors)
        ctx.close()

    # ---- 2、3. 樣本資料沒有的情況，與資料換了重畫
    ctx = browser.new_context(viewport={"width": 390, "height": 760}, has_touch=True, reduced_motion="reduce", locale="zh-TW")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector("#marketTrend .trend-row")
    page.locator("#marketTrendGrid").scroll_into_view_if_needed()

    drawn = page.evaluate(REDRAW, "trend.periods[1].metrics.institutional_20d_billion = null;")
    medium = drawn["rows"][1]
    check("沒有方向的票：橫條上不畫，展開後是中間一顆沒有方向的鍵",
          medium["marks"] == 3 and medium["cells"] == [["近 20 日", "bear", "false"], ["MA20/60", "bear", "false"], ["MA20", "bear", "false"], ["法人", "none", "false"]], medium)
    page.locator("#marketTrend button.trend-top").nth(1).click()
    page.locator("#marketTrend .trend-row.is-open .cell.none").click()
    check("沒有方向的鍵點了寫沒有資料", page.locator("#marketTrend .trend-row.is-open .trend-read").inner_text() == "近 20 日外資加投信合計：沒有資料")

    # 資料換了重畫（這裡把長期改成資料不足）：開著的中期與選中的鍵照原樣留著
    drawn = page.evaluate(REDRAW, """trend.periods[1].metrics.institutional_20d_billion = null;
      trend.periods[2] = { key: 'long', label: '長期', state: 'insufficient', conclusion: '資料不足', sentence: '資料不足：需要至少 240 根完整日 K，目前只有 180 根。', metrics: {} };""")
    check("資料換了重畫：開著的那一期與選中的鍵照原樣留著", drawn["rows"][1]["open"] and ["法人", "none", "true"] in drawn["rows"][1]["cells"]
          and drawn["rows"][1]["read"] == "近 20 日外資加投信合計：沒有資料", drawn["rows"][1])
    long = drawn["rows"][2]
    check("資料不足的期：只寫說明，不能展開", long["state"] == "none" and long["name"] == "長期 資料不足" and not long["button"] and long["marks"] == 0
          and long["sentence"] == "需要至少 240 根完整日 K，目前只有 180 根。", long)
    check("資料不足時最上面那一句", drawn["align"] == "長期資料不足 短線偏空、中線偏空", drawn["align"])

    drawn = page.evaluate(REDRAW, "trend.periods[2].state = 'bullish'; trend.periods[2].conclusion = '偏多';")
    long = drawn["rows"][2]
    check("票與結論對不上的期：不畫小柱，退回原本的文字", long["state"] == "bull" and long["name"] == "長期 偏多" and not long["button"] and long["marks"] == 0
          and long["sentence"].startswith("收盤 39,701.64 低於 MA120"), long)

    drawn = page.evaluate(REDRAW, "none")
    check("今天沒有走勢判讀：寫尚未完成，最上面那一句不出現", drawn["message"] == "走勢判讀尚未隨每日資料完成。" and drawn["align"] is None and not drawn["rows"], drawn)
    drawn = page.evaluate(REDRAW, "")
    check("資料回來之後照常畫", [row["marks"] for row in drawn["rows"]] == [5, 4, 4] and drawn["align"] == "三期不同調 短線偏空、中線偏空、長線盤整", drawn["align"])
    check("沒有腳本錯誤", not errors, errors)
    ctx.close()
    browser.close()

print("失敗", len(fails))
sys.exit(1 if fails else 0)
