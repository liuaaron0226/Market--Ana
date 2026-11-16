"""產生 .tmp/3d-baseline/signals-preview.json：預覽（preview-server.mjs）個股頁的事實區塊與五塊新功能樣本。

工作樹沒有 data/。這支直接 import 主資料夾的 twon_analysis 與 cloud/build_stock_pack.py，讀主資料夾的 data/（只讀），
不用 mklink 接連結；結果只寫到工作樹。產業別照正式打包（attach_news）用 with_news 補上，「消息」籤才會出現。
沒有這個檔時個股頁沒有事實區塊，旅程「報價卡只剩價格」「技術」那幾項會失敗或逾時（D 包 2026-11-17 查到的原因）。

用法（在工作樹根目錄）：
  PYTHONDONTWRITEBYTECODE=1 python -B design-previews/3d-redesign/make-signals-preview.py <主資料夾> .tmp/3d-baseline/signals-preview.json
程式自己設 sys.dont_write_bytecode，主資料夾不會多出 __pycache__（-B 只是保險）。"""
import json
import sys
from pathlib import Path

main_root, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.dont_write_bytecode = True   # 下面 import 的是主資料夾的程式：不在主資料夾留下 __pycache__（不靠呼叫的人記得加 -B）
sys.path.insert(0, str(main_root))
sys.path.insert(0, str(main_root / "cloud"))
from twon_analysis.analyze_v2 import analyze_v2  # noqa: E402
from twon_analysis.facts import with_news  # noqa: E402
from build_stock_pack import catalog_securities  # noqa: E402

INDUSTRY = {item.code: item.industry for item in catalog_securities()}

CODES = ["2330", "2603", "1802", "3017", "2317", "2327", "6669", "8299", "6488", "3105", "0050"]
AS_OF = "2026-11-09"
result = {}
for code in CODES:
    payload = analyze_v2(code, AS_OF)
    if isinstance(payload.get("facts"), dict):
        payload["facts"] = with_news(payload["facts"], industry=INDUSTRY.get(code))
    result[code] = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    print(code, "facts" if payload.get("facts") else "-", "signals" if payload.get("signals") else "-")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
print("wrote", out, out.stat().st_size)
