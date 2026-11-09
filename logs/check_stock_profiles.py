"""Profile-switch browser regression on an isolated local server; leaves 8876 alone."""

# 【已退役 2026-11-08，S3】本腳本驗的是個股頁 v1（三方案、總分、24 招或近期操作卡）。
# 本人定案「自創部分不保留」，個股頁已改用契約 v2；v2 驗收見 logs/check_search_journey.py --case all。
# 檔案保留不刪（刪除需本人確認），執行時直接說明並以非零碼結束，避免被誤當成通過。
raise SystemExit("RETIRED: v1 個股頁檢查已退役，改用 logs/check_search_journey.py --case all")
import json
import sys
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_server

IDS = ['2TON-FIT-V1', 'MOMENTUM-V1', 'SWING-SAFE-V1']

def run():
    server = live_server.MarketHTTPServer(('127.0.0.1', 0), live_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for width, height in [(1440, 1000), (820, 1180), (390, 844)]:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(base + '/stock.html?code=2330&as_of=2026-11-06')
                page.wait_for_selector('#content:not([hidden])', timeout=30000)
                assert page.locator('#presetSelect').count() == 1, '缺少整套分析方案選擇器'
                baseline = None
                mode_results = []
                for preset in IDS + [IDS[0]]:
                    page.select_option('#presetSelect', preset)
                    page.wait_for_function('(id) => document.getElementById("content").dataset.preset === id && !document.getElementById("content").hidden', arg=preset)
                    response = page.request.get(base + f'/api/stocks/2330/analysis?as_of=2026-11-06&preset={preset}')
                    assert response.status == 200
                    data = response.json()
                    assert data['preset']['id'] == preset
                    assert page.locator('#pressure1').inner_text().replace(',', '') == str(data['levels']['pressure1']).removesuffix('.0')
                    assert page.locator('#strategyRows tr').count() == 24
                    assert data['preset']['name'] in page.locator('#appliedPreset').inner_text()
                    assert data['preset']['name'] in page.locator('#verdict').inner_text()
                    assert data['preset']['name'] in page.locator('#chartHost canvas').get_attribute('aria-label')
                    assert page.locator('#content').get_attribute('data-params-hash') == data['params_hash']
                    if baseline is None:
                        baseline = (data['score'], data['levels'])
                    elif preset == IDS[0]:
                        assert baseline == (data['score'], data['levels']), '切回預設必須恢復相同結果'
                    mode_results.append({'preset': preset, 'score': data['score'], 'levels': data['levels'], 'hash': data['params_hash']})
                assert len({r['hash'] for r in mode_results[:3]}) == 3
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                page.select_option('#presetSelect', IDS[1])
                page.wait_for_function('document.getElementById("content").dataset.preset === "MOMENTUM-V1"')
                page.fill('#codeInput', '2603')
                page.locator('#searchForm button').click()
                page.wait_for_function('document.getElementById("stockCode").textContent === "2603"')
                assert page.locator('#content').get_attribute('data-preset') == IDS[1]
                assert 'as_of=2026-11-06' in page.url
                page.reload()
                page.wait_for_selector('#content:not([hidden])')
                assert page.locator('#presetSelect').input_value() == IDS[1]
                page.select_option('#presetSelect', IDS[2])
                page.wait_for_function('document.getElementById("content").dataset.preset === "SWING-SAFE-V1"')
                page.go_back()
                page.wait_for_function('document.getElementById("content").dataset.preset === "MOMENTUM-V1"')
                page.screenshot(path=str(ROOT / f'logs/visual-preview/stock-profiles-{width}.png'), full_page=True)
                assert not errors, errors
                results.append({'width': width, 'modes': mode_results, 'search_reload_back': True, 'no_overflow': True, 'errors': errors})
                page.close()
            page = browser.new_page()
            assert page.request.get(base + '/api/stocks/2330/analysis?preset=unknown').status == 400
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (ROOT / 'logs/stock-profiles-check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'widths': [r['width'] for r in results], 'all_passed': True}, ensure_ascii=False))

if __name__ == '__main__':
    run()
