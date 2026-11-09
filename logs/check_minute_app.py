import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path('C:/Users/User/Desktop/Market--Ana')
page_file = root / 'web/index.html'
assert page_file.is_file(), '缺少一分鐘看盤工具入口'
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    for width, height in [(390, 844), (768, 1024), (1440, 900)]:
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('https://**/*', lambda route: route.abort())
        page.goto(page_file.as_uri(), wait_until='load')
        assert page.locator('h1').inner_text() == '先防守，等確認'
        assert '2026/11/05' in page.locator('#asof').inner_text()
        assert '41,574.72' in page.locator('#close').inner_text()
        page.wait_for_function('Number.isFinite(document.querySelector("audio").duration)')
        duration = page.locator('audio').evaluate('(a)=>a.duration')
        assert 0 < duration <= 60, duration
        page.get_by_role('button', name='播放一分鐘').click()
        page.wait_for_function('!document.querySelector("audio").paused')
        page.get_by_role('button', name='暫停播報').click()
        assert page.locator('audio').evaluate('(a)=>a.paused')
        page.get_by_role('button', name='持有部位', exact=True).click()
        assert '個股' in page.locator('#response').inner_text()
        assert page.get_by_role('button', name='持有部位', exact=True).get_attribute('aria-pressed') == 'true'
        page.get_by_role('button', name='空手觀望', exact=True).click()
        assert '不急著接' in page.locator('#response').inner_text()
        page.locator('#evidence summary').click()
        assert page.locator('#evidence .sources').is_visible()
        assert page.locator('.sources a').count() >= 4
        page.locator('#evidence summary').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        page.screenshot(path=str(root / f'logs/minute-app-{width}.png'), full_page=True)
        page.emulate_media(color_scheme='dark', reduced_motion='reduce')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(19, 21, 23)'
        page.evaluate('window.updateFreshness(new Date("2026-11-07T12:00:00+08:00"))')
        assert '已過期' in page.locator('#freshness').inner_text()
        assert not errors, errors
        results.append({'width': width, 'audio_duration_seconds': duration, 'play_pause': True, 'position_switch': True, 'sources_expand': True, 'no_overflow': True, 'dark_mode': True, 'expiry_warning': True, 'javascript_errors': errors})
        page.close()
    browser.close()
(root / 'logs/minute-app-check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False, indent=2))
