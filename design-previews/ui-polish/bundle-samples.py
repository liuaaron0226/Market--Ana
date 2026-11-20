"""把樣品頁包成「單一檔案」（樣式與程式都寫進去），放到一個只有樣品的資料夾，給手機開或貼到別處用。

用法（工作目錄根目錄）：python design-previews/ui-polish/bundle-samples.py [輸出資料夾]
輸出：<out>/index.html（兩個樣品的入口）、motion.html、dark.html。資料夾裡只有這三個檔，可以放心對區網開。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / ".tmp" / "ui-polish" / "share"
OUT.mkdir(parents=True, exist_ok=True)


def bundle(name: str) -> None:
    html = (HERE / name).read_text(encoding="utf-8")

    def style(match: re.Match) -> str:
        return "<style>\n" + (HERE / match.group(1)).resolve().read_text(encoding="utf-8") + "\n</style>"

    def script(match: re.Match) -> str:
        return "<script>\n" + (HERE / match.group(1)).resolve().read_text(encoding="utf-8").replace("</script", "<\/script") + "\n</script>"

    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', style, html)
    html = re.sub(r'<script src="([^"]+)"></script>', script, html)
    (OUT / name).write_text(html, encoding="utf-8", newline="\n")
    print(name, f"{len(html.encode('utf-8')) // 1024} KB")


for page in ("motion.html", "dark.html"):
    bundle(page)
(OUT / "index.html").write_text("""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>樣品｜台股決策鏡</title>
<style>body{margin:0;min-height:100dvh;display:grid;place-content:center;gap:14px;padding:24px;background:#e9e5dc;font:600 17px "Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif}
a{display:block;padding:18px 22px;border-radius:18px;background:#fffdf9;color:#1d1b20;text-decoration:none;box-shadow:inset 0 1px 0 #fff,0 4px 0 #d6cfc0,0 14px 20px -6px rgb(60 45 20/16%)}
small{display:block;margin-top:4px;color:#5b5762;font-weight:400;font-size:13px}</style>
<a href="motion.html">動效樣品<small>常駐效果、換頁轉場（三種轉法）、B 資料會動、C 板有重量</small></a>
<a href="dark.html">暗色桌面配色樣品<small>墨黑（現在）、深藍灰、炭褐、石墨、暮紫</small></a></html>
""", encoding="utf-8", newline="\n")
print("out", OUT)
