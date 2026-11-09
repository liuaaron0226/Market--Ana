
# 【已退役 2026-11-08，S3】本腳本驗的是個股頁 v1（三方案、總分、24 招或近期操作卡）。
# 本人定案「自創部分不保留」，個股頁已改用契約 v2；v2 驗收見 logs/check_search_journey.py --case all。
# 檔案保留不刪（刪除需本人確認），執行時直接說明並以非零碼結束，避免被誤當成通過。
raise SystemExit("RETIRED: v1 個股頁檢查已退役，改用 logs/check_search_journey.py --case all")
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'logs/visual-preview'
BASE = 'http://127.0.0.1:8876'
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height in [(1440, 1000), (820, 1180), (390, 844)]:
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(BASE + '/stock.html?code=2330&v=delivery-20261107', wait_until='domcontentloaded')
        page.wait_for_selector('#content:not([hidden])', timeout=30000)
        assert page.locator('#stockCode').inner_text() == '2330'
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        page.screenshot(path=str(OUT / f'twon-delivery-2330-{width}.png'), full_page=True)
        page.locator('#verdictButton').click()
        assert page.locator('#layer1').is_visible()
        assert page.locator('#strategyRows tr').count() == 24
        assert '本站自有' in page.locator('#layer1').inner_text()
        assert '資料不足' not in page.locator('#strategyRows').text_content()
        assert '不完整' not in page.locator('#dims').inner_text()
        assert '15' in page.locator('#scoreLine').inner_text()
        page.locator('details.sk-detail summary').first.click()
        eps_row = page.locator('#strategyRows tr').filter(has_text='每股盈餘能力').inner_text()
        assert '累計' in eps_row and '49.33' in eps_row, eps_row
        assert '估計' in page.locator('#notes').inner_text()
        page.screenshot(path=str(OUT / f'twon-delivery-2330-open-{width}.png'), full_page=True)
        page.locator('#verdictButton').click()
        assert page.locator('#layer1').is_hidden()
        page.locator('#codeInput').fill('2603')
        page.locator('#searchForm button').click()
        page.wait_for_function("document.getElementById('stockCode').textContent === '2603'")
        assert page.locator('#strategyRows tr').count() == 24
        page.locator('#codeInput').fill('0050')
        page.locator('#searchForm button').click()
        page.wait_for_function("document.getElementById('stockCode').textContent === '0050'")
        assert page.locator('#strategyRows tr').count() == 20
        assert '不適用' in page.locator('#scoreLine').text_content()
        page.locator('#codeInput').fill('9999')
        page.locator('#searchForm button').click()
        page.wait_for_function("!document.getElementById('status').textContent.startsWith('讀取')")
        assert '9999' in page.locator('#status').inner_text()
        assert not errors, errors
        results.append({'width': width, 'actual_8876': True, 'stock_24_available': True, 'five_dimensions_and_score': True, 'eps_cumulative_and_date_caveat': True, 'drilldown_and_collapse': True, 'search_2603': True, 'etf_no_fake_fundamentals': True, 'unknown_code': True, 'no_overflow': True, 'errors': errors})
        page.close()
    browser.close()
(ROOT / 'logs/twon-live-delivery-check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False))
