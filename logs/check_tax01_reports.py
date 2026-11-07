"""ETF 證交稅改 0.1% 後，兩份回測報告以 1280／390 實渲染：JS 錯誤 0、無橫向溢出、截圖留存。"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / '.scratch' / 'virtual-portfolio-cloud'
PAGES = ['strategy-compare.html', 'backtest-v0.1.html']
results = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    for name in PAGES:
        for width in (1280, 390):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto((DIR / name).as_uri())
            overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
            text = page.inner_text('body')
            shot = DIR / f'{name.removesuffix(".html")}-{width}.png'
            page.screenshot(path=str(shot), full_page=True)
            results.append({'page': name, 'width': width, 'js_errors': errors, 'overflow': overflow,
                            'mentions_etf_01': '0.1%' in text, 'stale_pending': '待本人決定' in text,
                            'screenshot': shot.name})
            page.close()
    browser.close()
out = ROOT / 'logs' / 'tax01-reports-check.json'
out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
ok = all(not r['js_errors'] and not r['overflow'] and r['mentions_etf_01'] and not r['stale_pending'] for r in results)
print('PASS' if ok else 'FAIL', out)
