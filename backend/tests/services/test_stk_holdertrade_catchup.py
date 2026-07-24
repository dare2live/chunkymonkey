"""Unit tests for stk_holdertrade tip-leap catchup (local raw → formal)."""
from __future__ import annotations

from unittest.mock import MagicMock

from services.data_sources.stk_holdertrade_catchup import (
    catchup_missing_holdertrade_ann_partitions,
    list_missing_holdertrade_ann_partitions,
)
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE,
    COMPATIBILITY_TABLE,
)


def test_list_missing_uses_plan_partition_catchup_tip_leap():
    conn = MagicMock()

    def _execute(sql, params=None):
        sql_l = str(sql).lower()
        result = MagicMock()
        if "information_schema.tables" in sql_l:
            result.fetchone.return_value = (1,)
            return result
        if f"from {COMPATIBILITY_TABLE.lower()}" in sql_l and "distinct" in sql_l:
            result.fetchall.return_value = [
                ("20260613",),
                ("20260701",),
                ("20260720",),
            ]
            return result
        if f"from {CANONICAL_TABLE.lower()}" in sql_l and "distinct" in sql_l:
            result.fetchall.return_value = [("20260720",)]
            return result
        if f"from {CANONICAL_TABLE.lower()}" in sql_l and "max(" in sql_l:
            result.fetchone.return_value = ("20260720",)
            return result
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        return result

    conn.execute.side_effect = _execute
    missing = list_missing_holdertrade_ann_partitions(conn, limit=40)
    assert missing == ["20260701", "20260613"]


def test_catchup_calls_formal_writer_for_due_partitions(monkeypatch):
    conn = MagicMock()
    calls: list[str] = []

    monkeypatch.setattr(
        "services.data_sources.stk_holdertrade_catchup.list_missing_holdertrade_ann_partitions",
        lambda _conn, limit=40, order="newest_first": ["20260613"],
    )

    def _rows(_conn, ann):
        return [
            {
                "ts_code": "600000.SH",
                "ann_date": ann,
                "holder_name": "X",
                "in_de": "IN",
                "holder_type": "G",
                "change_vol": 1.0,
                "change_ratio": 0.1,
                "after_share": 1.0,
                "after_ratio": 0.1,
                "avg_price": 1.0,
                "total_share": 1.0,
            }
        ]

    monkeypatch.setattr(
        "services.data_sources.stk_holdertrade_catchup._rows_for_ann",
        _rows,
    )

    class _Outcome:
        canonical_rows = 1

    def _write(_conn, rows, enable_legacy_mirror=False):
        calls.append(str(rows[0]["ann_date"]))
        return _Outcome()

    monkeypatch.setattr(
        "services.data_sources.disclosure_dual_write.write_stk_holdertrade_formal_then_mirror",
        _write,
    )
    out = catchup_missing_holdertrade_ann_partitions(conn)
    assert out["repaired_partitions"] == ["20260613"]
    assert calls == ["20260613"]
    assert out["catchup_law"] == "plan_partition_catchup"
