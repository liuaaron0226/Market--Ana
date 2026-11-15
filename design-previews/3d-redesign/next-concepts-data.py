"""把下一批立體概念樣品要用的數字，從本機預覽的同一包樣本資料取出來，存成 JSON 給 Blender 用。
六個地方：今日股票水位、庫存總覽（自選庫存頁）；價位階梯、大戶持股（個股頁）；隔日三區歷史分布、短中長期走勢（首頁）。
取不到的（樣本資料裡沒有）會標成 sample: false，由 Blender 那邊用示意的數字並在圖上註明。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/next-concepts-data.py <輸出 json>"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

out = Path(sys.argv[1])
HOME = """() => ({
  distribution: [...document.querySelectorAll('#distributionBar button')].map(b => ({ key: b.dataset.outcome, text: b.innerText.replace(/\\s+/g, ' ').trim(), share: (b.querySelector('[data-share]') || {}).textContent })),
  trend: [...document.querySelectorAll('#marketTrendGrid > *')].map(a => ({ state: a.dataset.state, text: a.innerText.replace(/\\s+/g, ' ').trim() })),
  trendDate: document.getElementById('marketTrendDate').textContent,
})"""
STOCK = """() => ({
  title: document.title,
  ladder: [...document.querySelectorAll('#ladder > li')].map(li => ({ cls: li.className, text: li.innerText.replace(/\\s+/g, ' ').trim() })),
  holders: (document.getElementById('bigHolders') || {}).innerText,
})"""
PORTFOLIO = """() => ({
  level: { current: document.getElementById('currentLevel').textContent, recommended: document.getElementById('recommendedLevel').textContent,
           conclusion: document.getElementById('levelConclusion').textContent, cash: document.getElementById('cashAmount').value },
  overview: [...document.querySelectorAll('main section')].map(s => ({ id: s.id, title: (s.querySelector('h2, h3') || {}).textContent, text: s.innerText.replace(/\\s+/g, ' ').trim().slice(0, 600) })),
  tables: [...document.querySelectorAll('table')].map(t => ({ id: t.id || t.closest('section')?.id, rows: [...t.querySelectorAll('tr')].slice(0, 12).map(r => [...r.children].map(c => c.innerText.replace(/\\s+/g, ' ').trim())) })),
})"""

data, seen = {}, []
proc, base = shoot.start_server("--features", "all")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="zh-TW", timezone_id="Asia/Taipei")
        page.clock.set_fixed_time(shoot.FIXED_NOW)

        def keep(response) -> None:
            if "/api/" in response.url and "json" in (response.headers.get("content-type") or ""):
                try:
                    seen.append((response.url.replace(base, ""), response.json()))
                except Exception:  # noqa: BLE001
                    pass
        page.on("response", keep)
        page.goto(base + "/", wait_until="domcontentloaded")
        shoot.ready_home(page)
        page.wait_for_timeout(600)
        data["home"] = page.evaluate(HOME)
        page.goto(base + f"/stock.html?code={shoot.STOCK}", wait_until="domcontentloaded")
        shoot.ready_stock(page)
        page.wait_for_timeout(800)
        data["stock"] = page.evaluate(STOCK)
        page.goto(base + "/portfolio.html", wait_until="domcontentloaded")
        shoot.ready_portfolio(page)
        page.wait_for_timeout(800)
        data["portfolio"] = page.evaluate(PORTFOLIO)
        browser.close()
finally:
    proc.kill()
    proc.wait()

# 個股與自選庫存的原始資料（大戶持股的逐週序列、庫存明細）在 API 回應裡，畫面上只寫了最新一筆
data["api"] = {}
for url, body in seen:
    text = json.dumps(body, ensure_ascii=False)
    if any(word in text for word in ("big_holders", "holdings", "pct_1000", "recommended_level")):
        data["api"][url.split("?")[0] + ("?" + url.split("?")[1][:40] if "?" in url else "")] = body
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in data.items() if k != "api"}, ensure_ascii=False, indent=1)[:5000])
print("API：", [(url, len(json.dumps(body))) for url, body in data["api"].items()])
