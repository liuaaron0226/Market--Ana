import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path('C:/Users/User/Desktop/Market--Ana')
report = root / 'reports/EP124-首次真實試跑.html'
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 820, 'height': 1180}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(report.as_uri(), wait_until='load')
    assert page.title() == 'EP124 試跑報告'
    assert page.locator('section.evidence').count() == 4
    images = page.locator('main figure img')
    assert images.count() == 5
    for image in images.all():
        image.evaluate('(image) => { image.loading = "eager"; return image.decode(); }')
        assert image.evaluate('(image) => image.naturalWidth') == 1920
    page.locator('.image-button').first.click()
    assert page.locator('#image-dialog').is_visible()
    assert page.locator('#zoom-canvas img').evaluate('(image) => image.src.startsWith("data:image/jpeg;base64,")')
    page.get_by_role('button', name='關閉放大').click()
    assert not page.locator('#image-dialog').is_visible()
    page.locator('#nanya summary').click()
    assert page.locator('#nanya .full-record').is_visible()
    page.locator('#nanya summary').click()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.evaluate('window.scrollTo(0, 0)')
    page.screenshot(path=str(root / 'logs/html-report-tablet.png'), full_page=False)
    assert not errors, errors
    result = {'title': page.title(), 'offline_images_decoded': 5, 'evidence_sections': 4, 'image_zoom': 'passed', 'details_toggle': 'passed', 'tablet_no_horizontal_overflow': True, 'javascript_errors': errors, 'viewport': '820x1180', 'desktop_copy_matches': report.read_bytes() == Path('C:/Users/User/Desktop/Market--Ana-EP124-report.html').read_bytes()}
    (root / 'logs/html-report-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
