"""Snapshot nominal accepted pointer/hash bind (fail closed)."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from services.data_sources.nominal_ohlcv_schema import PROVIDER_FIELDS
from services.data_sources.security_day_partition import canonical_content_hash
from services.snapshot_nominal_bind import (
    SnapshotNominalBindError,
    assert_b0_run_matches_snapshot,
    assert_live_nominal_pointer_matches_snapshot,
    assert_live_nominal_matches_snapshot,
)


class _FakeConn:
    def __init__(
        self,
        pointer_rows: list[tuple[Any, ...]],
        canonical_rows: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self._pointer_rows = pointer_rows
        self._canonical_rows = canonical_rows or []
        self._rows: list[tuple[Any, ...]] = []
        self.queries: list[str] = []

    def execute(self, sql: str, _params=None):
        self.queries.append(sql)
        self._rows = (
            self._canonical_rows
            if "canonical_nominal_ohlcv_daily" in sql
            else self._pointer_rows
        )
        return self

    def fetchall(self):
        return list(self._rows)


def _bar(day: str, *, close: float = 10.0) -> dict[str, Any]:
    return {
        "ts_code": "000001.SZ",
        "trade_date": date(int(day[:4]), int(day[4:6]), int(day[6:8])),
        "open": 9.8,
        "high": 10.2,
        "low": 9.7,
        "close": close,
        "pre_close": 9.9,
        "change": close - 9.9,
        "pct_chg": (close / 9.9 - 1.0) * 100.0,
        "vol": 1000.0,
        "amount": 10000.0,
    }


def _hash(day: str, *, close: float = 10.0) -> str:
    return canonical_content_hash([_bar(day, close=close)], PROVIDER_FIELDS)


def _canonical_tuple(day: str, *, close: float = 10.0) -> tuple[Any, ...]:
    row = _bar(day, close=close)
    return tuple(row[field] for field in PROVIDER_FIELDS)


def _snap(days: list[str], *, hashes: dict[str, str] | None = None) -> dict:
    hashes = hashes or {d: _hash(d) for d in days}
    return {
        "domains": {
            "nominal_ohlcv": {
                "accepted": [
                    {
                        "partition": d,
                        "content_hash": hashes[d],
                        "row_count": 1,
                        "config_hash": "cfg1",
                        "contract_hash": "contract1",
                        "batch_id": f"batch-{d}",
                        "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                    }
                    for d in days
                ]
            }
        }
    }


def test_bind_matches_live_accepted() -> None:
    days = ["20250401", "20250402"]
    conn = _FakeConn(
        [
            ("20250401", "batch-20250401", 1, _hash("20250401"), "contract1", "cfg1"),
            ("20250402", "batch-20250402", 1, _hash("20250402"), "contract1", "cfg1"),
        ],
        [_canonical_tuple("20250401"), _canonical_tuple("20250402")],
    )
    assert_live_nominal_matches_snapshot(_snap(days), conn, days=days)


def test_pointer_preflight_does_not_read_canonical_outcomes() -> None:
    day = "20250401"
    conn = _FakeConn(
        [(day, f"batch-{day}", 1, _hash(day), "contract1", "cfg1")],
        [_canonical_tuple(day, close=999.0)],
    )
    assert_live_nominal_pointer_matches_snapshot(_snap([day]), conn, days=[day])


def test_snapshot_bound_loader_reads_canonical_once() -> None:
    from services.institution_follow_nominal_bars import load_nominal_bars_by_day

    day = "20250401"
    conn = _FakeConn(
        [(day, f"batch-{day}", 1, _hash(day), "contract1", "cfg1")],
        [_canonical_tuple(day)],
    )
    bars = load_nominal_bars_by_day(conn, [day], snapshot=_snap([day]))
    canonical_queries = [
        sql for sql in conn.queries if "canonical_nominal_ohlcv_daily" in sql
    ]
    assert len(canonical_queries) == 1
    assert bars[day][0]["ts_code"] == "000001.SZ"
    assert "trade_date" not in bars[day][0]


def test_b0_binding_rejects_same_id_with_different_disclosure_content() -> None:
    from services.research_runtime import dataset_snapshot_from_disclosure

    snapshot = {
        "snapshot_id": "same-id",
        "scope": "bounded_accepted_partitions",
        "phase_e_ablation": "eligible",
        "domains": {
            "holders_top10": {
                "dataset_id": "tier0.disclosure.holders_top10",
                "date_set": ["20250401"],
                "content_hash": "first-content",
            }
        },
    }
    boundary = dataset_snapshot_from_disclosure(snapshot).boundary_dict()
    run = SimpleNamespace(
        snapshot_id="same-id",
        artifact_manifest={"research_runtime_snapshot": boundary},
    )
    assert_b0_run_matches_snapshot(run, snapshot)
    drifted = {
        **snapshot,
        "domains": {
            "holders_top10": {
                **snapshot["domains"]["holders_top10"],
                "content_hash": "different-content",
                "accepted": [
                    {
                        "dataset_id": "tier0.disclosure.holders_top10",
                        "partition": "20250401",
                        "content_hash": "different-content",
                        "config_hash": "cfg",
                    }
                ],
            }
        },
    }
    with pytest.raises(SnapshotNominalBindError, match="binding violated"):
        assert_b0_run_matches_snapshot(run, drifted)


def test_b0_binding_rejects_same_id_with_different_main_rally_content() -> None:
    from services.main_rally_dataset_snapshot import dataset_snapshot_from_main_rally

    snapshot = {
        "snapshot_id": "same-rally-id",
        "scope": "bounded_accepted_partitions",
        "phase_f_ablation": "eligible",
        "strategy_package": "main_rally_v1",
        "domains": {
            "nominal_ohlcv": {
                "dataset_id": "tier0.market_data.nominal_ohlcv_daily",
                "date_set": ["20250401"],
                "content_hash": "nominal-content",
                "config_hash": "nominal-config",
            },
            "rally_gt": {
                "taxonomy_version": "v1",
                "config_hash": "gt-config",
                "tables": {
                    "fact_rally_ground_truth": {
                        "row_count": 1,
                        "content_hash": "gt-content",
                    }
                },
            },
        },
    }
    adapted = dataset_snapshot_from_main_rally(snapshot)
    assert all(not item.dataset_id.startswith("tier3.") for item in adapted.inputs)
    assert "gt_evidence_omitted_from_development_inputs" in adapted.notes
    boundary = adapted.boundary_dict()
    run = SimpleNamespace(
        snapshot_id="same-rally-id",
        artifact_manifest={"research_runtime_snapshot": boundary},
    )
    assert_b0_run_matches_snapshot(run, snapshot)
    # GT stays freeze evidence, not a development input: label-hash drift must
    # not look like a snapshot bind break.
    gt_drifted = {
        **snapshot,
        "domains": {
            **snapshot["domains"],
            "rally_gt": {
                **snapshot["domains"]["rally_gt"],
                "tables": {
                    "fact_rally_ground_truth": {
                        "row_count": 1,
                        "content_hash": "different-gt-content",
                    }
                },
            },
        },
    }
    assert_b0_run_matches_snapshot(run, gt_drifted)
    nominal_drifted = {
        **snapshot,
        "domains": {
            **snapshot["domains"],
            "nominal_ohlcv": {
                **snapshot["domains"]["nominal_ohlcv"],
                "content_hash": "different-nominal-content",
            },
        },
    }
    with pytest.raises(SnapshotNominalBindError, match="binding violated"):
        assert_b0_run_matches_snapshot(run, nominal_drifted)


def test_bind_fails_on_content_hash_drift() -> None:
    days = ["20250401"]
    conn = _FakeConn(
        [("20250401", "batch-20250401", 1, "live-other", "contract1", "cfg1")]
    )
    with pytest.raises(SnapshotNominalBindError, match="content_hash drift"):
        assert_live_nominal_matches_snapshot(_snap(days), conn, days=days)


def test_bind_fails_when_accepted_missing_from_snapshot() -> None:
    with pytest.raises(SnapshotNominalBindError, match="accepted missing"):
        assert_live_nominal_matches_snapshot({"domains": {}}, _FakeConn([]), days=["20250401"])


def test_bind_fails_when_live_partition_missing() -> None:
    days = ["20250401"]
    with pytest.raises(SnapshotNominalBindError, match="live accepted_partition missing"):
        assert_live_nominal_matches_snapshot(_snap(days), _FakeConn([]), days=days)


def test_bind_rejects_blank_config_duplicate_and_canonical_drift() -> None:
    day = "20250401"
    blank_cfg = _FakeConn(
        [(day, f"batch-{day}", 1, _hash(day), "contract1", "")],
        [_canonical_tuple(day)],
    )
    with pytest.raises(SnapshotNominalBindError, match="config_hash drift"):
        assert_live_nominal_matches_snapshot(_snap([day]), blank_cfg, days=[day])

    duplicate = _snap([day])
    duplicate["domains"]["nominal_ohlcv"]["accepted"].append(
        dict(duplicate["domains"]["nominal_ohlcv"]["accepted"][0])
    )
    with pytest.raises(SnapshotNominalBindError, match="duplicate partition"):
        assert_live_nominal_matches_snapshot(duplicate, _FakeConn([]), days=[day])

    pointer_hash = _hash(day)
    canonical_drift = _FakeConn(
        [(day, f"batch-{day}", 1, pointer_hash, "contract1", "cfg1")],
        [_canonical_tuple(day, close=11.0)],
    )
    with pytest.raises(SnapshotNominalBindError, match="canonical content_hash drift"):
        assert_live_nominal_matches_snapshot(
            _snap([day]), canonical_drift, days=[day]
        )
