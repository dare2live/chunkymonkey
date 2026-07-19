"""A4 landing purity: serve filter evidence; raw write preserves provider rows."""
from __future__ import annotations

import pytest

from services.data_sources.universe_serve_filter import (
    apply_universe_serve_filter,
    validate_universe_filter_column,
)
from services.data_sources import sync_runner as sr
from services.duck_adapter import connect
from services.universe import load_universe_policy


def test_serve_filter_records_policy_hash_and_exclusion_reasons() -> None:
    policy = load_universe_policy()
    kept, evidence = apply_universe_serve_filter(
        [
            {"ts_code": "600000.SH", "trade_date": "20260717"},
            {"ts_code": "830001.BJ", "trade_date": "20260717"},
            {"ts_code": "000001.SZ", "trade_date": "20260717"},
        ],
        policy=policy,
        filter_column="ts_code",
    )
    assert [row["ts_code"] for row in kept] == ["600000.SH", "000001.SZ"]
    assert evidence.excluded_row_count == 1
    assert evidence.policy_hash == policy.config_hash
    assert evidence.exclusion_reason_counts == (
        ("board_prefix_not_in_project_universe", 1),
    )


def test_landing_write_preserves_non_whitelist_provider_rows() -> None:
    conn = connect(":memory:")
    spec = {
        "domain": "t_test_dom",
        "universe_filter": True,
        "grain": ["ts_code", "ann_date"],
        "target_table": "raw_landing_purity",
        "write_mode": "merge_grain",
    }
    rows = [
        {"ts_code": "600000.SH", "ann_date": "20240101"},
        {"ts_code": "835180.BJ", "ann_date": "20240101"},
    ]
    written = sr._write_batch(conn, spec, rows)
    assert written == 2
    codes = {
        row[0]
        for row in conn.execute(
            "SELECT ts_code FROM raw_landing_purity ORDER BY ts_code"
        ).fetchall()
    }
    assert codes == {"600000.SH", "835180.BJ"}


def test_miswired_filter_column_still_fails_closed_without_dropping_semantics() -> None:
    validate_universe_filter_column(
        ["600000.SH", "000001.SZ"], filter_column="ts_code", table="ok"
    )
    with pytest.raises(ValueError, match="does not look like security codes"):
        validate_universe_filter_column(
            ["20240101", "20240102"], filter_column="ts_code", table="t_test"
        )

    conn = connect(":memory:")
    spec = {
        "domain": "t_test_dom",
        "universe_filter": True,
        "grain": ["ts_code", "ann_date"],
        "target_table": "raw_miswire",
        "write_mode": "merge_grain",
    }
    with pytest.raises(ValueError, match="does not look like security codes"):
        sr._write_batch(
            conn,
            spec,
            [
                {"ts_code": "20240101", "ann_date": "20240101"},
                {"ts_code": "20240102", "ann_date": "20240101"},
            ],
        )
