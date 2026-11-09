"""唯讀驗證 0004 隔離建置產物（契約 v2，每檔一筆）、指定代號與直接分析的一致性，以及舊 309 檔包指紋。

S2（2026-11-08）：本人定案「自創部分不保留」，個股分析改為 twon_analysis.analyze_v2，不再有三方案。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from twon_analysis.analyze_v2 import SCHEMA, analyze_v2  # noqa: E402

REFERENCE_NAMES = ("2TON", "漲停寶寶", "甜甜價", "甜價", "私藏", "CP值", "抄底", "FIT-V1")


def compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(seed_dir: Path, legacy_pack: Path, expected_legacy_sha256: str, codes: tuple[str, ...]) -> dict:
    manifest = json.loads((seed_dir / "manifest.json").read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{(seed_dir / 'staging.sqlite').as_posix()}?mode=ro", uri=True)
    try:
        version = manifest["version"]
        scalar = lambda sql, *args: connection.execute(sql, args).fetchone()[0]
        integrity = scalar("PRAGMA integrity_check")
        state = connection.execute(
            "SELECT active_version, previous_version FROM stock_data_state WHERE key = 'current'"
        ).fetchone()
        columns = [row[1] for row in connection.execute("PRAGMA table_info(stock_analysis)")]
        catalog_count = scalar("SELECT COUNT(*) FROM listed_securities WHERE version = ?", version)
        analysis_count = scalar("SELECT COUNT(*) FROM stock_analysis WHERE version = ?", version)
        distinct_codes = scalar("SELECT COUNT(DISTINCT code) FROM stock_analysis WHERE version = ?", version)
        analyzable = scalar(
            "SELECT COUNT(*) FROM listed_securities WHERE version = ? AND analysis_status IN ('available', 'stale')", version
        )
        etf_rows = scalar("SELECT COUNT(*) FROM listed_securities WHERE version = ? AND code = '0050'", version)
        etf_analysis_rows = scalar("SELECT COUNT(*) FROM stock_analysis WHERE version = ? AND code = '0050'", version)
        blocked_analysis_rows = scalar(
            "SELECT COUNT(*) FROM stock_analysis a JOIN listed_securities s "
            "ON s.version = a.version AND s.code = a.code "
            "WHERE a.version = ? AND s.analysis_status IN ('insufficient', 'unavailable')", version
        )
        market_mismatch = scalar(
            "SELECT COUNT(*) FROM stock_analysis a JOIN listed_securities s "
            "ON s.version = a.version AND s.code = a.code "
            "WHERE a.version = ? AND json_extract(a.payload, '$.market') IS NOT s.market", version
        )
        non_v2 = scalar(
            "SELECT COUNT(*) FROM stock_analysis WHERE version = ? AND json_extract(payload, '$.schema') IS NOT ?",
            version, SCHEMA,
        )
        reference_hits = {
            name: scalar("SELECT COUNT(*) FROM stock_analysis WHERE version = ? AND instr(payload, ?) > 0", version, name)
            for name in REFERENCE_NAMES
        }
        stored = {
            code: json.loads(payload)
            for code, payload in connection.execute(
                f"SELECT code, payload FROM stock_analysis WHERE version = ? AND code IN ({','.join('?' * len(codes))})",
                (version, *codes),
            )
        }
    finally:
        connection.close()

    equal: dict[str, bool] = {}
    hashes: dict[str, dict[str, str]] = {}
    for code in codes:
        direct = analyze_v2(code, "9999-12-31")
        row = stored.get(code)
        equal[code] = row is not None and compact(row) == compact(direct)
        hashes[code] = {
            "stored": hashlib.sha256(compact(row)).hexdigest() if row is not None else "missing",
            "direct": hashlib.sha256(compact(direct)).hexdigest(),
        }

    legacy_sha256 = sha256_file(legacy_pack)
    legacy = json.loads(legacy_pack.read_text(encoding="utf-8"))
    checks = {
        "integrity_ok": integrity == "ok",
        "active_is_built_version": state == (manifest["version"], None),
        "stock_analysis_has_no_preset_column": columns == ["version", "code", "as_of", "payload"],
        "catalog_count_matches": catalog_count == manifest["catalog_count"],
        "analysis_count_matches": analysis_count == manifest["analysis_count"],
        "one_row_per_code": distinct_codes == analysis_count,
        "analyzable_plus_0050": analysis_count == analyzable + 1,
        "all_payloads_v2": non_v2 == 0,
        "payload_market_matches_catalog": market_mismatch == 0,
        "no_reference_names": not any(reference_hits.values()),
        "selected_codes_equal_direct_analyze_v2": all(equal.values()),
        "legacy_sha256_unchanged": legacy_sha256 == expected_legacy_sha256,
        "legacy_code_count_is_309": len(legacy["results"]) == 309,
        "versioned_0050_has_one_row": etf_analysis_rows == 1,
        "insufficient_and_unavailable_have_no_analysis": blocked_analysis_rows == 0,
        "listed_catalog_excludes_0050": etf_rows == 0,
    }
    if not all(checks.values()):
        raise AssertionError(json.dumps({"checks": checks, "reference_hits": reference_hits}, ensure_ascii=False, sort_keys=True))
    return {
        "checks": checks,
        "version": manifest["version"],
        "catalog_count": catalog_count,
        "analysis_count": analysis_count,
        "codes": {"equal": equal, "sha256": hashes},
        "legacy_pack": {"codes": len(legacy["results"]), "sha256": legacy_sha256},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed_dir", type=Path)
    parser.add_argument("--legacy-pack", type=Path, default=ROOT / "cloud/.stock-pack.json")
    parser.add_argument("--expected-legacy-sha256", required=True)
    parser.add_argument("--codes", default="2330,6488,1102,0050")
    args = parser.parse_args()
    codes = tuple(code.strip() for code in args.codes.split(",") if code.strip())
    print(json.dumps(validate(args.seed_dir, args.legacy_pack, args.expected_legacy_sha256, codes), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
