"""唯讀驗證 A2-2 兩市場建置：上櫃樣本直接分析一致性、上市分析與 v4 逐列相同、容量與搜尋讀列估算。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from twon_analysis.analyze import analyze  # noqa: E402
from twon_analysis.profiles import available_presets  # noqa: E402


def compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _analysis_rows(db: Path, market: str | None) -> dict[tuple[str, str], str]:
    connection = _ro(db)
    try:
        version = connection.execute("SELECT active_version FROM stock_data_state WHERE key = 'current'").fetchone()[0]
        sql = ("SELECT a.code, a.preset, a.payload FROM stock_analysis a "
               "LEFT JOIN listed_securities s ON s.version = a.version AND s.code = a.code WHERE a.version = ?")
        args: tuple = (version,)
        if market is not None:
            sql += " AND (s.market = ? OR s.code IS NULL)"
            args += (market,)
        return {(code, preset): payload for code, preset, payload in connection.execute(sql, args)}
    finally:
        connection.close()


def _strip_version_fields(payload: str) -> str:
    # 分析 payload 本身不含建置版本；保留原樣比對，任何差異都視為不同。
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate(new_dir: Path, old_dir: Path, otc_samples: list[str]) -> dict:
    manifest = json.loads((new_dir / "manifest.json").read_text(encoding="utf-8"))
    new_db = new_dir / "staging.sqlite"
    connection = _ro(new_db)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        version = manifest["version"]
        market_counts = dict(connection.execute(
            "SELECT market, COUNT(*) FROM listed_securities WHERE version = ? GROUP BY market", (version,)).fetchall())
        overlap = connection.execute(
            "SELECT COUNT(*) FROM (SELECT code FROM listed_securities WHERE version = ? GROUP BY code HAVING COUNT(*) > 1)",
            (version,)).fetchone()[0]
        samples = {}
        for code in otc_samples:
            stored = dict(connection.execute(
                "SELECT preset, payload FROM stock_analysis WHERE version = ? AND code = ?", (version, code)).fetchall())
            per_preset = {}
            for preset in (item["id"] for item in available_presets()):
                direct = compact(analyze(code, "9999-12-31", preset_id=preset))
                seeded = compact(json.loads(stored[preset])) if preset in stored else None
                per_preset[preset] = {
                    "equal": seeded == direct,
                    "sha256": hashlib.sha256(direct).hexdigest(),
                }
            samples[code] = per_preset
    finally:
        connection.close()

    new_listed = _analysis_rows(new_db, "上市")
    old_listed = _analysis_rows(old_dir / "staging.sqlite", None)
    differing = sorted(key for key in old_listed.keys() | new_listed.keys()
                       if _strip_version_fields(old_listed.get(key, "")) != _strip_version_fields(new_listed.get(key, "")))
    listed_digest = lambda rows: hashlib.sha256(
        b"".join(f"{c}|{p}|".encode() + rows[(c, p)].encode("utf-8") + b"\n" for c, p in sorted(rows))).hexdigest()

    otc_rows = connection_count = None
    connection = _ro(new_db)
    try:
        otc_rows = connection.execute(
            "SELECT COUNT(*) FROM stock_analysis a JOIN listed_securities s ON s.version = a.version AND s.code = a.code "
            "WHERE a.version = ? AND s.market = '上櫃'", (manifest["version"],)).fetchone()[0]
        connection_count = connection.execute(
            "SELECT COUNT(*) FROM listed_securities WHERE version = ?", (manifest["version"],)).fetchone()[0]
    finally:
        connection.close()

    return {
        "version": manifest["version"],
        "integrity_check": integrity,
        "market_counts": market_counts,
        "duplicate_codes_across_markets": overlap,
        "market_status_counts": manifest.get("market_status_counts"),
        "analysis_count": manifest["analysis_count"],
        "otc_analysis_rows": otc_rows,
        "catalog_rows": connection_count,
        "otc_samples": samples,
        "listed_vs_v4": {
            "old_rows": len(old_listed),
            "new_listed_rows": len(new_listed),
            "differing_keys": differing[:20],
            "differing_count": len(differing),
            "old_digest": listed_digest(old_listed),
            "new_digest": listed_digest(new_listed),
        },
        "capacity": manifest.get("capacity"),
        "files": manifest.get("files"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--otc-samples", default="6488")
    args = parser.parse_args()
    result = validate(args.new.resolve(), args.old.resolve(), [c.strip() for c in args.otc_samples.split(",") if c.strip()])
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
