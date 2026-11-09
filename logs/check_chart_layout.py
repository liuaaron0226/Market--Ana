"""Visible containment, not merely Playwright scroll-into-view reachability."""
import json
import threading
from playwright.sync_api import sync_playwright
from check_chart_studies import ROOT, OfflineHandler, live_server


def main():
    server = live_server.MarketHTTPServer(('127.0.0.1', 0), OfflineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (1440, 820, 390):
                    page = browser.new_page(viewport={'width': width, 'height': 900})
                    page.goto(f'http://127.0.0.1:{server.server_port}/minimal.html')
                    page.wait_for_function('window.MinimalApp && window.MinimalApp.ready')
                    for expanded in (False, True):
                        if expanded:
                            page.locator('[data-study="macd"]').click()
                            page.get_by_text('MACD怎麼看', exact=True).click()
                        box = page.locator('.chart-workspace').evaluate('''n => {
                          const parent = n.getBoundingClientRect(), child = n.querySelector('#chartStudies').getBoundingClientRect();
                          return {height: n.clientHeight, scroll: n.scrollHeight, bottom: parent.bottom,
                                  studiesBottom: child.bottom, scrollTop: n.scrollTop, overflow: getComputedStyle(n).overflow};
                        }''')
                        results.append({'width': width, 'expanded': expanded, **box})
                        assert box['scroll'] <= box['height'] + 2 and box['studiesBottom'] <= box['bottom'] + 1 and box['scrollTop'] == 0, results[-1]
                        if expanded and width == 390:
                            page.locator('.kc-study-pane-macd').screenshot(path=str(ROOT / 'logs/visual-preview/ticket01-teaching-390.png'))
                            page.locator('#themeToggle').click()
                            page.locator('.kc-study-pane-macd canvas').focus()
                            page.locator('.kc-study-pane-macd').screenshot(path=str(ROOT / 'logs/visual-preview/ticket01-teaching-dark-390.png'))
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        (ROOT / 'logs/chart-studies-layout.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: studies and expanded teaching remain within the chart workspace at 1440/820/390')


if __name__ == '__main__':
    main()
