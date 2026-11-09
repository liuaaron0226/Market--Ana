import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).parent
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height in [(1440, 1080), (820, 1180), (390, 844)]:
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto((root / 'integrated-layout.html').as_uri())
        page.wait_for_load_state('networkidle')
        page.locator('#close').wait_for()
        assert page.locator('#close').inner_text() == '41,574.72'
        assert page.locator('#chart rect').count() > 40
        overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not overflow, f'Overflow at {width}'
        page.locator('[data-scene="2"]').click()
        assert page.locator('#sceneTitle').inner_text() == '跌破防守線，先控風險'
        assert '兩線之間' in page.locator('.snapshot-position').inner_text()
        page.locator('[data-scene="0"]').click()
        assert '站回收復線' in page.locator('#sceneTitle').inner_text()
        page.locator('[data-period="week"]').click()
        assert '週K' in page.locator('#chart').get_attribute('aria-label')
        page.locator('[data-period="day"]').click()
        page.locator('[data-range="22"]').click()
        assert '22筆' in page.locator('#chart').get_attribute('aria-label')
        page.locator('[data-range="64"]').click()
        page.locator('#ma').click()
        assert page.locator('#ma').get_attribute('aria-pressed') == 'false'
        page.locator('#ma').click()
        page.locator('#chart').click(position={"x": 70, "y": 100})
        assert '開' in page.locator('#readout').inner_text()
        page.locator('[data-scene="1"]').click()
        page.locator('#theme').click()
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        if width == 1440:
            page.screenshot(path=str(root / 'preview-desktop-dark.png'), full_page=True)
        page.locator('#theme').click()
        page.emulate_media(reduced_motion='reduce')
        page.locator('[data-scene="0"]').click()
        assert page.locator('#flat').is_visible()
        page.locator('[data-scene="1"]').click()
        page.emulate_media(reduced_motion='no-preference')
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(root / f'preview-{width}.png'), full_page=True)
        assert errors == [], errors
        results.append({"width": width, "overflow": False, "scenario_sync": True, "snapshot_position_unchanged": True, "period_and_range": True, "chart_inspection": True, "themes": True, "reduced_motion": True, "errors": errors})
        page.close()
    page = browser.new_page()
    page.route('**/snapshot.js', lambda route: route.abort())
    page.goto((root / 'integrated-layout.html').as_uri())
    assert '快照未載入' in page.locator('.failure').inner_text()
    results.append({"missing_snapshot_blocked": True})
    browser.close()
(root / 'check-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False))
