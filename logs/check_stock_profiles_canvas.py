"""Observe the real KChart while switching profiles; never modify app source."""

# 【已退役 2026-11-08，S3】本腳本驗的是個股頁 v1（三方案、總分、24 招或近期操作卡）。
# 本人定案「自創部分不保留」，個股頁已改用契約 v2；v2 驗收見 logs/check_search_journey.py --case all。
# 檔案保留不刪（刪除需本人確認），執行時直接說明並以非零碼結束，避免被誤當成通過。
raise SystemExit("RETIRED: v1 個股頁檢查已退役，改用 logs/check_search_journey.py --case all")
import hashlib
import json
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
import os
import sys
import threading

sys.path.insert(0, str(ROOT))
# 8897 已因記憶體不足停用且不得重啟：未指定時，在本程序內開一支隔離 live_server（自動埠號，程序結束即關）。
if os.environ.get('STOCK_BASE'):
    BASE = os.environ['STOCK_BASE']
else:
    import live_server
    _iso = live_server.MarketHTTPServer(('127.0.0.1', 0), live_server.Handler)
    threading.Thread(target=_iso.serve_forever, daemon=True).start()
    BASE = f'http://127.0.0.1:{_iso.server_port}'
IDS = ['2TON-FIT-V1', 'MOMENTUM-V1', 'SWING-SAFE-V1']
OUT = ROOT / 'logs/visual-preview'


def state(page):
    return page.evaluate('''() => {
      const chart = window.__profileChart, c = chart.canvas;
      const pixels = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let painted = 0;
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i]) painted++;
      return {width:c.width, height:c.height, clientWidth:c.clientWidth,
        clientHeight:c.clientHeight, hostWidth:chart.host.clientWidth,
        dpr:Math.max(1,Math.min(3,devicePixelRatio)), painted,
        bars:chart.candles.length, view:{...chart.view}, overlays:chart.overlays,
        overflow:document.documentElement.scrollWidth > innerWidth,
        label:c.getAttribute('aria-label')};
    }''')


def wait_ready(page, preset):
    page.wait_for_function('''id => {
      const el = document.getElementById('content');
      return el.dataset.preset === id && !el.hidden && el.getAttribute('aria-busy') === 'false';
    }''', arg=preset)
    try:
        page.wait_for_function('''() => {
          const c = window.__profileChart;
          return c && c.canvas.width === Math.floor(c.host.clientWidth * Math.max(1,Math.min(3,devicePixelRatio)));
        }''', timeout=2000)
    except Exception:
        print(json.dumps({'failed_preset': preset, 'chart': state(page)}, ensure_ascii=False), flush=True)
        raise
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')


def run():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for width, height in [(390, 844), (1440, 1000)]:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script('''(() => {
                  let original;
                  Object.defineProperty(window,'KChart', {
                    configurable:true, get:()=>original,
                    set:K => { original = class extends K {
                      constructor(...args) { super(...args); window.__profileChart = this; }
                    }; }
                  });
                })();''')
                page.goto(BASE + '/stock.html?code=2330&as_of=2026-11-06')
                page.wait_for_load_state('networkidle')
                wait_ready(page, IDS[0])
                initial = state(page)
                canvas = page.locator('#chartHost canvas')
                canvas.focus()
                page.keyboard.press('+')
                page.keyboard.press('ArrowLeft')
                saved_view = state(page)['view']
                assert saved_view != initial['view'], 'zoom/pan must change the actual chart view'
                modes = []
                for preset in IDS + [IDS[0]]:
                    page.select_option('#presetSelect', preset)
                    wait_ready(page, preset)
                    response = page.request.get(BASE + f'/api/stocks/2330/analysis?as_of=2026-11-06&preset={preset}')
                    assert response.status == 200
                    data = response.json()
                    chart = state(page)
                    assert chart['view'] == saved_view, 'profile switch reset zoom/pan'
                    assert chart['hostWidth'] == chart['clientWidth'] and chart['hostWidth'] > 200
                    assert chart['height'] >= 280 and chart['painted'] > 1000
                    assert not chart['overflow']
                    assert chart['bars'] == len(data['bars'])
                    expected = [data['levels'][key] for key in ('pressure1', 'support1', 'defense') if data['levels'][key]]
                    sweet = data['levels']['sweet']
                    if sweet and sweet['formed']:
                        expected.extend([sweet['low'], sweet['high']])
                    assert [line['value'] for line in chart['overlays']] == expected
                    assert data['preset']['name'] in chart['label']
                    assert page.locator('#support1').inner_text().replace(',', '') == str(data['levels']['support1']).removesuffix('.0')
                    assert page.locator('#pressure1').inner_text().replace(',', '') == str(data['levels']['pressure1']).removesuffix('.0')
                    chart['image_sha256'] = hashlib.sha256(canvas.evaluate('c => c.toDataURL()').encode()).hexdigest()
                    image = canvas.screenshot()
                    if len(modes) < 3:
                        image_path = OUT / f'profiles-canvas-{width}-{preset}.png'
                        image_path.write_bytes(image)
                        with Image.open(image_path) as img:
                            if img.width > 900:
                                img.resize((900, round(img.height * 900 / img.width))).save(image_path)
                        chart['image'] = str(image_path)
                    modes.append({'preset': preset, 'score': data['score'], 'chart': chart})
                assert modes[0]['chart']['image_sha256'] == modes[-1]['chart']['image_sha256'], 'returning to default must restore the rendered chart'
                assert len({m['chart']['image_sha256'] for m in modes[:3]}) > 1
                responses = {pid: page.request.get(BASE + f'/api/stocks/2330/analysis?as_of=2026-11-06&preset={pid}').json() for pid in IDS}
                page.evaluate('''responses => {
                  window.__originalFetch = window.fetch;
                  window.fetch = async url => new Response(JSON.stringify(responses[new URL(url,location.href).searchParams.get('preset')]),
                    {status:200,headers:{'Content-Type':'application/json'}});
                }''', responses)
                for preset in IDS:
                    page.select_option('#presetSelect', preset)
                    wait_ready(page, preset)
                page.evaluate('() => { window.fetch = window.__originalFetch; delete window.__originalFetch; }')
                page.select_option('#presetSelect', IDS[1])
                page.select_option('#presetSelect', IDS[2])
                page.select_option('#presetSelect', IDS[0])
                wait_ready(page, IDS[0])
                page.wait_for_load_state('networkidle')
                assert page.locator('#content').get_attribute('data-preset') == IDS[0]
                canvas.focus()
                page.keyboard.press('End')
                page.locator('#themeToggle').click()
                dark = state(page)
                assert dark['painted'] > 1000 and not dark['overflow']
                page.locator('#themeToggle').click()
                assert not errors, errors
                results.append({'width': width, 'modes': modes, 'zoom_pan_preserved': True,
                    'default_pixels_restored': True, 'rapid_switch_latest_wins': True,
                    'dark_theme_rendered': True, 'errors': errors})
                page.close()
        finally:
            browser.close()
    report = ROOT / 'logs/stock-profiles-canvas-check.json'
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'widths':[r['width'] for r in results], 'all_passed':True, 'report':str(report)}, ensure_ascii=False))


if __name__ == '__main__':
    run()
