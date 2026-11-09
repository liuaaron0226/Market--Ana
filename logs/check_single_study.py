"""One indicator at a time, with gesture and button switching on real pages."""
import json
import threading
from playwright.sync_api import sync_playwright
from check_chart_studies import ROOT, OfflineHandler, live_server


def swipe(page, canvas, left=True, touch=False):
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    start = box['x'] + box['width'] * (.8 if left else .2)
    end = box['x'] + box['width'] * (.2 if left else .8)
    y = box['y'] + box['height'] / 2
    if touch:
        cdp = page.context.new_cdp_session(page)
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': start, 'y': y}]})
        for i in range(1, 9):
            cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': start + (end - start) * i / 8, 'y': y}]})
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        cdp.detach()
    else:
        page.mouse.move(start, y); page.mouse.down()
        page.mouse.move(end, y, steps=8); page.mouse.up()


def main():
    server = live_server.MarketHTTPServer(('127.0.0.1', 0), OfflineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    results = []
    try:
        with sync_playwright() as p:
            for engine, width in [('webkit', 375), ('webkit', 390), ('chromium', 1440), ('chromium', 390)]:
                browser = getattr(p, engine).launch(headless=True)
                try:
                    for stock in (False, True):
                        page = browser.new_page(viewport={'width': width, 'height': 900}, has_touch=width < 600)
                        errors = []
                        page.on('pageerror', lambda e: errors.append(str(e)))
                        page.goto(f'http://127.0.0.1:{server.server_port}/' + ('stock.html?code=2330' if stock else 'minimal.html'), wait_until='networkidle')
                        page.wait_for_function("!document.getElementById('content').hidden" if stock else 'window.MinimalApp && window.MinimalApp.ready')
                        studies = page.locator('#chartStudies')
                        assert studies.locator('.kc-study-pane:visible').count() == 1, 'only one indicator may be shown'
                        assert studies.locator('.kc-study-pane-volume').is_visible(), 'default to volume'
                        assert studies.locator('details[open]').count() == 0
                        before = studies.locator('.kc-study-pane-volume .kc-study-readout').inner_text().split('｜')[0]
                        swipe(page, studies.locator('.kc-study-pane-volume canvas'), touch=engine == 'chromium' and width == 390)
                        assert studies.locator('.kc-study-pane-macd').is_visible()
                        assert studies.locator('.kc-study-pane:visible').count() == 1
                        assert before == studies.locator('.kc-study-pane-macd .kc-study-readout').inner_text().split('｜')[0]
                        page.get_by_text('MACD怎麼看', exact=True).click()
                        assert studies.locator('.kc-study-pane-macd details').get_attribute('open') is not None
                        swipe(page, studies.locator('.kc-study-pane-macd canvas'), left=False, touch=engine == 'chromium' and width == 390)
                        assert studies.locator('.kc-study-pane-volume').is_visible()
                        assert studies.locator('.kc-study-pane:visible').count() == 1
                        studies.locator('[data-study="macd"]').click()
                        assert studies.locator('.kc-study-pane-macd').is_visible()
                        studies.locator('[data-study="volume"]').focus(); page.keyboard.press('Enter')
                        assert studies.locator('.kc-study-pane-volume').is_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                        main_canvas = page.locator('#chartHost canvas' if stock else '#mainChart canvas')
                        main_canvas.scroll_into_view_if_needed()
                        box = main_canvas.bounding_box()
                        x, y = box['x'] + 50, box['y'] + 60
                        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x, y + 4); page.mouse.up()
                        assert studies.locator('.kc-study-clear').is_visible(), 'minor vertical movement must retain main-chart selection'
                        if engine == 'chromium' and width == 390:
                            page.get_by_text('區間數據', exact=True).click()
                            rows = studies.locator('tbody tr'); rows.first.wait_for(state='visible')
                            count = rows.count()
                            canvas = studies.locator('.kc-study-pane-volume canvas')
                            canvas.scroll_into_view_if_needed(); b = canvas.bounding_box()
                            cdp = page.context.new_cdp_session(page)
                            points = lambda gap: [{'x': b['x'] + b['width'] * (.5 - gap), 'y': b['y'] + 50, 'id': 1}, {'x': b['x'] + b['width'] * (.5 + gap), 'y': b['y'] + 50, 'id': 2}]
                            cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': points(.15)})
                            cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': points(.35)})
                            cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []}); cdp.detach()
                            assert rows.count() == count, 'multi-touch in the indicator slot must not change the main-chart range'
                        assert not errors, errors
                        results.append({'engine': engine, 'width': width, 'stock': stock, 'pass': True})
                        if width == 390 and engine == 'webkit':
                            page.locator('.sk-chart-card' if stock else '.chart-workspace').screenshot(path=str(ROOT / 'logs/visual-preview' / ('single-study-stock.png' if stock else 'single-study-market.png')))
                        page.close()
                finally:
                    browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        (ROOT / 'logs/single-study-check.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: one pane, swipe both ways, buttons, keyboard, date preservation and actual Chromium touch')


if __name__ == '__main__':
    main()
