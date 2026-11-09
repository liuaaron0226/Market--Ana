import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright

root = Path('C:/Users/User/Desktop/Market--Ana')
build = json.loads((root / 'logs/html-analysis-build.json').read_text(encoding='utf-8'))
reports = root / 'reports'
errors = []
results = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 820, 'height': 1180}, device_scale_factor=1)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('https://**/*', lambda route: route.abort())
    desktop = Path('C:/Users/User/Desktop/Market--Ana-直播分析.html')
    page.goto(desktop.as_uri(), wait_until='load')
    assert page.title() == '直播分析總覽'
    assert page.locator('tbody tr').count() == build['target']
    assert str(build['ai_reviews']) + ' / ' + str(build['target']) in page.locator('.stats').first.inner_text()
    links = page.locator('.review-head h2 a')
    assert links.count() == build['ai_reviews']
    for anchor in links.all():
        resolved = page.evaluate('(a) => a.href', anchor.element_handle())
        target = unquote(urlparse(resolved).path).lstrip('/')
        assert Path(target).is_file(), target
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(root / 'logs/analysis-index-phone.png'), full_page=False)
    for item in build['reports']:
        file = reports / item['file']
        page.set_viewport_size({'width': 820, 'height': 1180})
        page.goto(file.as_uri(), wait_until='load')
        assert page.locator('article.record').count() == item['records']
        images = page.locator('article.record figure img')
        assert images.count() == item['embedded_images']
        for image in images.all():
            image.evaluate('(image) => { image.loading = "eager"; return image.decode(); }')
            assert image.evaluate('(image) => image.naturalWidth === 1920 && image.naturalHeight === 1080')
        page.locator('.image-button').first.click()
        assert page.locator('#image-dialog').is_visible()
        page.locator('#zoom-canvas img').evaluate('(image) => image.decode()')
        page.get_by_role('button', name='關閉放大').click()
        assert not page.locator('#image-dialog').is_visible()
        detail = page.locator('article.record details:has(.full-record)').first
        detail.locator('summary').click()
        assert detail.locator('.full-record').is_visible()
        detail.locator('summary').click()
        timeline = page.locator('#timeline details')
        timeline.locator('summary').click()
        assert timeline.locator('li').count() > 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.emulate_media(color_scheme='dark')
        assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(19, 29, 42)'
        page.emulate_media(color_scheme='light')
        if item['video_id'] == 'G4vcmeBFDm0':
            page.set_viewport_size({'width': 820, 'height': 1180})
            page.locator('article.record').first.scroll_into_view_if_needed()
            page.screenshot(path=str(root / 'logs/analysis-report-tablet.png'), full_page=False)
        results.append({'video_id': item['video_id'], 'records': item['records'], 'offline_native_images': images.count(), 'image_zoom': 'passed', 'details': 'passed', 'timeline': 'passed', 'phone_tablet_no_overflow': True, 'system_dark_theme': 'passed'})
    browser.close()
assert not errors, errors
result = {'report_count': len(results), 'evidence_records': sum(item['records'] for item in results), 'desktop_relative_links': 'passed', 'offline_network_blocked': True, 'viewports': ['390x844', '820x1180'], 'javascript_errors': errors, 'results': results}
(root / 'logs/analysis-html-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
