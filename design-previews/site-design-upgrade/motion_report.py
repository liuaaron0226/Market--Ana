"""把 motion.json 印成好讀的清單：每個互動「開的時候跑了什麼、關的時候跑了什麼」。常駐循環的動畫另外標出來。

用法：python design-previews/site-design-upgrade/motion_report.py [名稱裡要有的字]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def line(a: dict) -> str:
    kind = {"CSSTransition": "過場", "CSSAnimation": "動畫", "Animation": "程式"}.get(a["kind"], a["kind"])
    # CSS 動畫的曲線寫在關鍵影格裡，這裡拿不到，所以不印；過場與程式動畫的曲線是準的
    curve = "" if a["kind"] == "CSSAnimation" else " " + str(a["easing"]).replace("cubic-bezier", "cb")
    loop = " 循環" if a["iterations"] == "infinite" else (f" ×{a['iterations']}" if a["iterations"] not in (1, 1.0) else "")
    return f"{kind} {a['what'][:24]} ← {a['who'][:46]} {a['duration']}ms+{a['delay']}{curve}{loop}" + (f" ×{a['count']}個" if a["count"] > 1 else "") + ("" if a["onScreen"] else "（畫面外）")


def main() -> None:
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    data = json.loads((ROOT / ".scratch" / "site-design-upgrade" / "motion.json").read_text(encoding="utf-8"))
    for name, entry in data.items():
        if want not in name:
            continue
        print(f"== {name}｜{entry.get('note', '')}" + (f"｜{entry['skipped']}" if entry.get("skipped") else "") + (f"｜錯誤 {entry['error'][:80]}" if entry.get("error") else ""))
        for key, title in (("animations", "開"), ("close_animations", "關")):
            if key not in entry:
                continue
            rows = [a for a in entry[key] if a["iterations"] != "infinite"]
            print(f"  {title}：" + ("" if rows else "沒有任何動畫（瞬間）"))
            for a in rows[:12]:
                print("    " + line(a))
            if len(rows) > 12:
                print(f"    …另有 {len(rows) - 12} 種")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
