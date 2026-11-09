"""短期／長期候選的發布留存快照（票07首次留存、票09／10到期評估共用）。

每次發布一批就寫一份不可覆寫的 JSON：``<root>/<kind>/<publish_date>.json``。
內容含批次識別、方法版本、資料日、候選完整輸出（含固定基準價與成立／失效條件）、
排除對帳摘要與輸入指紋，並以正規化 JSON 的 SHA-256 封存。空結果、來源故障與過期
同樣留存，狀態彼此不同。同批重試內容相同則不動；內容不同一律拒絕，不覆寫舊判斷。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "candidate-snapshot-v1"
KINDS = ("short", "long")
STATUSES = ("ok", "no_candidates", "source_failure", "stale")
TAIPEI = timezone(timedelta(hours=8))
# 台股收盤 13:30；資料日當天收盤前不得發布以該日為資料日的批次。
MARKET_CLOSE_HOUR_MINUTE = (13, 30)


class SnapshotConflict(Exception):
    """同一 kind／發布日已有內容不同的快照；舊判斷不得覆寫。"""


class SnapshotIntegrityError(Exception):
    """快照封存雜湊不符（被改動或未經 build_snapshot 封存）。"""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal_hash(snapshot: Mapping[str, Any]) -> str:
    """不含 ``snapshot_sha256`` 欄位本身的正規化 JSON SHA-256。"""
    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _verify(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("snapshot_sha256") != seal_hash(snapshot):
        raise SnapshotIntegrityError("快照封存雜湊不符：內容被改動或未經 build_snapshot 封存")


def _publish_date(data_date: str, published_at: str) -> tuple[str, bool]:
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("published_at 必須帶時區")
    local = moment.astimezone(TAIPEI)
    hour, minute = MARKET_CLOSE_HOUR_MINUTE
    close_of_data_date = datetime.fromisoformat(data_date).replace(hour=hour, minute=minute, tzinfo=TAIPEI)
    if local < close_of_data_date:
        raise ValueError("發布時間早於資料日收盤，不能發布以該日為資料日的批次")
    publish_date = local.date().isoformat()
    return publish_date, publish_date != data_date


def _exclusion_summary(reconciliation: list[Mapping[str, Any]]) -> dict[str, Any]:
    total: Counter[str] = Counter()
    by_market: dict[str, Counter[str]] = defaultdict(Counter)
    outcomes: Counter[str] = Counter()
    for row in reconciliation:
        outcomes[str(row.get("outcome"))] += 1
        market = str(row.get("market") or "UNKNOWN")
        for reason in row.get("reasons", []):
            code = reason.get("code")
            if code:
                total[code] += 1
                by_market[market][code] += 1
    return {
        "outcomes": dict(sorted(outcomes.items())),
        "reasons": dict(sorted(total.items())),
        "by_market": {market: dict(sorted(counts.items())) for market, counts in sorted(by_market.items())},
    }


def build_snapshot(
    kind: str,
    result: Mapping[str, Any],
    *,
    input_fingerprints: Mapping[str, str],
    published_at: str,
) -> dict[str, Any]:
    """把一次篩選結果封存成不可變快照；不寫檔。"""
    if kind not in KINDS:
        raise ValueError(f"kind 必須是 {KINDS} 之一")
    if not input_fingerprints:
        raise ValueError("缺少輸入資料指紋，無法重現本批判斷")
    status = result.get("status")
    if status not in STATUSES:
        raise ValueError(f"未知狀態：{status}")
    data_date = str(result["data_date"])
    publish_date, late = _publish_date(data_date, published_at)
    candidates = copy.deepcopy(list(result.get("candidates", [])))
    for candidate in candidates:
        baseline = candidate.get("baseline") or {}
        if baseline.get("date") is None or baseline.get("close") is None:
            raise ValueError(f'{candidate.get("code")} 缺固定基準交易日／收盤價，不能發布')
    reconciliation = list(result.get("reconciliation", []))
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "batch_id": f"{kind}-{publish_date}",
        "publish_date": publish_date,
        "published_at": published_at,
        "late_publication": late,
        "data_date": data_date,
        "method_version": result["method_version"],
        "parameters": copy.deepcopy(dict(result.get("parameters", {}))),
        "market": result.get("market"),
        "status": status,
        "market_environment": copy.deepcopy(result.get("market_environment")),
        "candidates": candidates,
        "counts": copy.deepcopy(dict(result.get("counts", {}))),
        "counts_by_market": copy.deepcopy(result.get("counts_by_market")),
        "exclusion_summary": _exclusion_summary(reconciliation),
        "errors": copy.deepcopy(list(result.get("errors", []))),
        "input_fingerprints": dict(input_fingerprints),
    }
    snapshot["snapshot_sha256"] = seal_hash(snapshot)
    return snapshot


def _path(root: Path, kind: str, publish_date: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"kind 必須是 {KINDS} 之一")
    datetime.fromisoformat(publish_date)
    return root / kind / f"{publish_date}.json"


def write_snapshot(root: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """原子寫入；同內容重試回 ``unchanged``，不同內容丟 ``SnapshotConflict``。"""
    _verify(snapshot)
    target = _path(root, snapshot["kind"], snapshot["publish_date"])
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if target.exists():
        existing = read_snapshot(root, snapshot["kind"], snapshot["publish_date"])
        if existing["snapshot_sha256"] == snapshot["snapshot_sha256"]:
            return {"outcome": "unchanged", "path": target, "snapshot_sha256": snapshot["snapshot_sha256"]}
        raise SnapshotConflict(f"{target} 已有不同內容的快照；不覆寫舊判斷")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.partial")
    partial.write_bytes(data)
    try:
        # os.link 在目標已存在時失敗，避免兩個發布者競爭時後者覆寫前者。
        os.link(partial, target)
    except FileExistsError:
        existing = read_snapshot(root, snapshot["kind"], snapshot["publish_date"])
        if existing["snapshot_sha256"] != snapshot["snapshot_sha256"]:
            raise SnapshotConflict(f"{target} 已被另一次發布寫入不同內容") from None
        return {"outcome": "unchanged", "path": target, "snapshot_sha256": snapshot["snapshot_sha256"]}
    finally:
        partial.unlink(missing_ok=True)
    return {"outcome": "written", "path": target, "snapshot_sha256": snapshot["snapshot_sha256"]}


def read_snapshot(root: Path, kind: str, publish_date: str) -> dict[str, Any]:
    snapshot = json.loads(_path(root, kind, publish_date).read_text(encoding="utf-8"))
    _verify(snapshot)
    return snapshot


def list_snapshots(root: Path, kind: str) -> list[dict[str, Any]]:
    """依發布日排序回傳已驗封存的快照，供到期評估讀取。"""
    folder = root / kind
    if kind not in KINDS:
        raise ValueError(f"kind 必須是 {KINDS} 之一")
    if not folder.is_dir():
        return []
    return [read_snapshot(root, kind, path.stem) for path in sorted(folder.glob("*.json"))]
