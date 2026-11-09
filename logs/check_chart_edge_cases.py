"""Observable chart edge cases through data boundaries and the public KChart interface."""
import json
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from check_chart_studies import OfflineHandler, ROOT, live_server


def main():
    server = live_server.MarketHTTPServer(('127.0.0.1', 0), OfflineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    source = (ROOT / 'web/analysis-data.js').read_text(encoding='utf-8')
    prefix, payload = source.split('=', 1)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for amount, expected in [(1_000_000, '0.01 億元'), (None, '資料不足'), (0, '0 億元')]:
                    data = json.loads(payload.strip().rstrip(';'))
                    data['daily'][-1]['amount'] = amount
                    data['daily'][-1]['turnover'] = amount
                    page = browser.new_page(viewport={'width': 390, 'height': 844})
                    page.route('**/analysis-data.js', lambda r: r.fulfill(content_type='application/javascript', body=prefix + '=' + json.dumps(data, ensure_ascii=False) + ';'))
                    page.goto(base + '/minimal.html')
                    page.wait_for_function('window.MinimalApp && window.MinimalApp.ready')
                    actual = page.locator('.kc-study-pane-volume .kc-study-readout').inner_text()
                    assert expected in actual, (amount, expected, actual)
                    page.close()
                data = json.loads(payload.strip().rstrip(';'))
                for field in ('open', 'high', 'low', 'close'):
                    data['daily'][-1][field] = None
                page = browser.new_page(viewport={'width': 390, 'height': 844})
                page.route('**/analysis-data.js', lambda r: r.fulfill(content_type='application/javascript', body=prefix + '=' + json.dumps(data, ensure_ascii=False) + ';'))
                page.goto(base + '/minimal.html')
                page.wait_for_function('window.MinimalApp && window.MinimalApp.ready')
                assert page.evaluate('window.MinimalApp.chart.candles.at(-1).close') is None, 'missing close must not become zero'
                assert '資料不足' in page.locator('.kc-study-pane-macd .kc-study-readout').inner_text()
                page.evaluate('window.MinimalApp.chart.setData([])')
                assert '尚無日線資料' in page.locator('.kc-study-pane-volume .kc-study-readout').inner_text(), 'empty replacement must clear old studies'
                page.close()
                page = browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)
                page.goto(base + '/minimal.html')
                page.wait_for_function('window.MinimalApp && window.MinimalApp.ready')
                page.locator('.kc-study-pane-volume canvas').tap(position={'x': 60, 'y': 40})
                page.locator('#mobileCandleDetails').click()
                assert page.locator('#detailPanel').evaluate("n => n.classList.contains('is-open')"), 'date details must remain clickable after selecting a study'
                page.locator('#panelClose').click()
                canvas = page.locator('.kc-study-pane-volume canvas')
                canvas.focus()
                for _ in range(12):
                    page.keyboard.press('ArrowLeft')
                assert page.locator('#mobileCandleDetails').is_hidden(), 'offscreen selection must not leave a stale date action'
                page.close()
                from twon_analysis.analyze import analyze
                for mismatch in ('parameters', 'dates'):
                    response = analyze('2330', '2026-11-06')
                    if mismatch == 'parameters':
                        response['chart']['parameters']['precision'] = 7
                    else:
                        response['chart']['macd'][0]['date'] = '1900-01-01'
                    page = browser.new_page(viewport={'width': 390, 'height': 844})
                    page.route('**/api/stocks/2330/analysis**', lambda r: r.fulfill(content_type='application/json', body=json.dumps(response, ensure_ascii=False)))
                    page.goto(base + '/stock.html?code=2330')
                    page.wait_for_function("!document.getElementById('content').hidden")
                    assert '版本或日期不符' in page.locator('.kc-study-pane-macd .kc-study-readout').inner_text()
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    print('PASS: amount contract distinguishes yuan, zero and missing data')


if __name__ == '__main__':
    main()
