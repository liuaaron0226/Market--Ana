import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).parent
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height in [(1440, 1000), (820, 1180), (390, 844)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto((root / 'integrated-layout.html').as_uri())
        page.wait_for_load_state('networkidle')
        assert page.locator('[data-view="flow"]').count() == 1, '資金尚未改成圖表入口'
        assert not page.locator('#details').get_attribute('open')
        assert page.locator('#close').inner_text() == '41,574.72'
        assert page.locator('#chart .candle').count() == 44
        assert page.locator('#chart .ref').count() == 2
        page.locator('[data-scene="2"]').click()
        assert '不攤平' in page.locator('#action').inner_text()
        assert page.locator('#position').inner_text() == '快照：兩線之間'
        assert page.locator('#ladder [data-price="41574.72"]').count() == 1
        page.locator('[data-view="flow"]').click()
        assert page.locator('#chart .value-bar').count() == 2
        assert '−1,024.61' in page.locator('#chart').text_content()
        page.screenshot(path=str(root / f'chart-first-flow-{width}.png'), full_page=True)
        page.locator('[data-view="sector"]').click()
        assert '+0.41%' in page.locator('#chart').text_content()
        page.locator('[data-view="price"]').click()
        page.locator('[data-period="week"]').click()
        assert '週K' in page.locator('#chart').get_attribute('aria-label')
        page.locator('[data-period="day"]').click()
        page.locator('#chart').click(position={"x": 65, "y": 130})
        assert '開' in page.locator('#readout').inner_text()
        page.locator('#details summary').click()
        assert page.locator('#holding').is_visible()
        assert page.locator('#dataTable tbody tr').count() > 0
        page.locator('#details summary').click()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        page.locator('#theme').click()
        page.screenshot(path=str(root / f'chart-first-dark-{width}.png'), full_page=True)
        page.locator('#theme').click()
        page.emulate_media(reduced_motion='reduce')
        page.locator('[data-scene="0"]').click()
        assert page.locator('#action').is_visible()
        page.locator('[data-scene="1"]').click()
        page.emulate_media(reduced_motion='no-preference')
        page.mouse.move(0, 0)
        page.evaluate('scrollTo(0,0)')
        page.screenshot(path=str(root / f'chart-first-{width}.png'), full_page=True)
        assert errors == [], errors
        results.append({"width": width, "price_flow_sector": True, "snapshot_marker_fixed": True, "details_collapsed": True, "period_switch": True, "data_table": True, "no_overflow": True, "themes": True, "reduced_motion": True, "errors": errors})
        page.close()
    browser.close()
(root / 'chart-first-check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False))
