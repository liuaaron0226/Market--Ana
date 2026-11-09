from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import candidate_snapshots as snapshots
from candidate_snapshots import SnapshotConflict, SnapshotIntegrityError, build_snapshot, list_snapshots, read_snapshot, write_snapshot


def _result(status: str = "ok") -> dict:
    candidate = {
        "code": "1101",
        "name": "合成水泥",
        "market": "TWSE",
        "method_version": "short-v1.1",
        "data_date": "2026-11-06",
        "horizon": "1～3 個交易日",
        "decision": "等待",
        "main_judgment": "量價突破已確認；仍等待下一交易日觸發，不視為立即買進。",
        "supporting_evidence": [{"factor": "量能", "value": "量比 1.32", "source": "bars", "data_date": "2026-11-06"}],
        "opposing_evidence": [{"factor": "追價風險", "value": "高於 57.7 不追價", "source": "bars", "data_date": "2026-11-06"}],
        "entry_trigger": {"status": "等待", "level": 56.6, "condition": "價格站上 56.6"},
        "do_not_chase": {"above": 57.7, "condition": "高於 57.7 不追"},
        "target_observation_zone": {"low": 57.1, "high": 58.2, "basis": "ATR 觀察區"},
        "defense": {"level": 54.0, "basis": "結構防守"},
        "invalidation": ["日收盤跌破 55.1"],
        "baseline": {"date": "2026-11-06", "close": 56.0},
        "metrics": {"volume_ratio": 1.32},
    }
    return {
        "method_version": "short-v1.1",
        "parameters": {"max_candidates": 5},
        "data_date": "2026-11-06",
        "market": "TWSE+TPEX",
        "status": status,
        "candidates": [candidate] if status == "ok" else [],
        "reconciliation": [
            {"code": "1101", "market": "TWSE", "outcome": "selected" if status == "ok" else "excluded",
             "reasons": [] if status == "ok" else [{"code": "market_below_ma20", "message": "x", "details": {}}]},
            {"code": "6488", "market": "TPEX", "outcome": "excluded",
             "reasons": [{"code": "chip_source_unavailable_for_market", "message": "x", "details": {}}]},
        ],
        "errors": [] if status in {"ok", "no_candidates"} else [{"source": "chips", "state": "failed", "message": "T86 讀取失敗"}],
        "counts": {"universe": 2, "evaluated": 2, "qualified": 1 if status == "ok" else 0,
                   "selected": 1 if status == "ok" else 0, "excluded": 1 if status == "ok" else 2, "not_evaluated": 0},
        "counts_by_market": {"TWSE": {"universe": 1}, "TPEX": {"universe": 1}},
        "market_environment": {"close": 92.5, "ma20": 107.06},
    }


PUBLISHED_AT = "2026-11-06T10:00:00Z"  # 台北 18:00，收盤後
FINGERPRINTS = {"inputs_sha256": "a" * 64}


class SnapshotBuildTests(unittest.TestCase):
    def test_snapshot_keeps_batch_identity_versions_baseline_and_reconciliation_summary(self) -> None:
        snapshot = build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)

        self.assertEqual("short", snapshot["kind"])
        self.assertEqual("short-2026-11-06", snapshot["batch_id"])
        self.assertEqual("2026-11-06", snapshot["publish_date"])
        self.assertEqual("2026-11-06", snapshot["data_date"])
        self.assertFalse(snapshot["late_publication"])
        self.assertEqual("short-v1.1", snapshot["method_version"])
        self.assertEqual("ok", snapshot["status"])
        self.assertEqual({"date": "2026-11-06", "close": 56.0}, snapshot["candidates"][0]["baseline"])
        self.assertEqual(["日收盤跌破 55.1"], snapshot["candidates"][0]["invalidation"])
        self.assertEqual(FINGERPRINTS, snapshot["input_fingerprints"])
        self.assertEqual({"TPEX": {"chip_source_unavailable_for_market": 1}}, snapshot["exclusion_summary"]["by_market"])
        self.assertEqual(64, len(snapshot["snapshot_sha256"]))

    def test_empty_failure_and_stale_batches_are_retained_with_distinct_status(self) -> None:
        for status in ("no_candidates", "source_failure", "stale"):
            with self.subTest(status=status):
                snapshot = build_snapshot("short", _result(status), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
                self.assertEqual(status, snapshot["status"])
                self.assertEqual([], snapshot["candidates"])
                if status in {"source_failure", "stale"}:
                    self.assertTrue(snapshot["errors"])

    def test_candidate_without_fixed_baseline_is_refused(self) -> None:
        result = _result()
        del result["candidates"][0]["baseline"]
        with self.assertRaisesRegex(ValueError, "基準"):
            build_snapshot("short", result, input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)

    def test_publication_before_data_date_close_is_refused_and_late_publication_is_flagged(self) -> None:
        with self.assertRaisesRegex(ValueError, "收盤"):
            build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at="2026-11-06T04:00:00Z")
        late = build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at="2026-11-09T02:00:00Z")
        self.assertEqual("2026-11-09", late["publish_date"])
        self.assertEqual("short-2026-11-09", late["batch_id"])
        self.assertTrue(late["late_publication"])

    def test_unknown_kind_and_missing_fingerprints_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind"):
            build_snapshot("mid", _result(), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        with self.assertRaisesRegex(ValueError, "指紋"):
            build_snapshot("short", _result(), input_fingerprints={}, published_at=PUBLISHED_AT)

    def test_snapshot_does_not_alias_the_screen_result(self) -> None:
        result = _result()
        snapshot = build_snapshot("short", result, input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        before = copy.deepcopy(snapshot)
        result["candidates"][0]["baseline"]["close"] = 999
        self.assertEqual(before, snapshot)


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_then_read_round_trip_at_kind_and_publish_date_path(self) -> None:
        snapshot = build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        outcome = write_snapshot(self.root, snapshot)
        self.assertEqual("written", outcome["outcome"])
        self.assertEqual(self.root / "short" / "2026-11-06.json", outcome["path"])
        self.assertEqual(snapshot, read_snapshot(self.root, "short", "2026-11-06"))
        self.assertEqual([], [path.name for path in (self.root / "short").iterdir() if path.suffix != ".json"])

    def test_identical_retry_is_idempotent_and_different_content_cannot_overwrite(self) -> None:
        snapshot = build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        write_snapshot(self.root, snapshot)
        target = self.root / "short" / "2026-11-06.json"
        original = target.read_bytes()

        self.assertEqual("unchanged", write_snapshot(self.root, copy.deepcopy(snapshot))["outcome"])

        changed = build_snapshot("short", _result("no_candidates"), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        with self.assertRaises(SnapshotConflict):
            write_snapshot(self.root, changed)
        self.assertEqual(original, target.read_bytes())
        self.assertEqual(["2026-11-06.json"], sorted(path.name for path in (self.root / "short").iterdir()))

    def test_tampered_or_unsealed_snapshot_is_rejected_on_read_and_write(self) -> None:
        snapshot = build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        write_snapshot(self.root, snapshot)
        target = self.root / "short" / "2026-11-06.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["candidates"][0]["baseline"]["close"] = 1.0
        target.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SnapshotIntegrityError):
            read_snapshot(self.root, "short", "2026-11-06")

        unsealed = dict(snapshot)
        unsealed["status"] = "stale"
        with self.assertRaises(SnapshotIntegrityError):
            write_snapshot(self.root / "other", unsealed)

    def test_list_snapshots_is_sorted_and_verified(self) -> None:
        for published_at in ("2026-11-09T10:00:00Z", "2026-11-06T10:00:00Z"):
            write_snapshot(self.root, build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at=published_at))
        self.assertEqual(["2026-11-06", "2026-11-09"], [item["publish_date"] for item in list_snapshots(self.root, "short")])
        self.assertEqual([], list_snapshots(self.root, "long"))

    def test_canonical_hash_is_stable_across_key_order(self) -> None:
        snapshot = build_snapshot("short", _result(), input_fingerprints=FINGERPRINTS, published_at=PUBLISHED_AT)
        reordered = json.loads(json.dumps(snapshot, sort_keys=False))
        self.assertEqual(snapshot["snapshot_sha256"], snapshots.seal_hash(dict(reversed(list(reordered.items())))))


if __name__ == "__main__":
    unittest.main()
