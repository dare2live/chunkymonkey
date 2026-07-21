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

def test_serve_filter_excludes_b_share_and_bj_prefixes() -> None:
    """沪深A whitelist must drop B (90/20) and BJ (92) — not a denylist of boards."""
    policy = load_universe_policy()
    kept, evidence = apply_universe_serve_filter(
        [
            {"ts_code": "600000.SH"},
            {"ts_code": "900901.SH"},
            {"ts_code": "200002.SZ"},
            {"ts_code": "920819.BJ"},
            {"ts_code": "300001.SZ"},
        ],
        policy=policy,
        filter_column="ts_code",
    )
    assert [row["ts_code"] for row in kept] == ["600000.SH", "300001.SZ"]
    assert evidence.excluded_row_count == 3


def test_normalize_bare_bj_code_before_shape_gate() -> None:
    from services.data_sources.universe_serve_filter import (
        normalize_provider_security_code,
        validate_universe_filter_column,
    )

    assert normalize_provider_security_code("874075") == "874075.BJ"
    assert normalize_provider_security_code("600000") == "600000.SH"
    assert normalize_provider_security_code("300999.SZ") == "300999.SZ"
    validate_universe_filter_column(
        ["300999.SZ", normalize_provider_security_code("874075")],
        filter_column="ts_code",
        table="share_float",
    )
    with pytest.raises(ValueError, match="does not look like security codes"):
        validate_universe_filter_column(
            ["20260720", "20260720"], filter_column="ts_code", table="share_float"
        )


def test_prepare_batch_normalizes_bare_bj_without_dropping() -> None:
    conn = connect(":memory:")
    spec = {
        "domain": "share_float",
        "universe_filter": True,
        "grain": ["ts_code", "ann_date"],
        "target_table": "raw_tushare_share_float",
        "write_mode": "merge_grain",
    }
    written = sr._write_batch(
        conn,
        spec,
        [
            {"ts_code": "300999.SZ", "ann_date": "20260720"},
            {"ts_code": "874075", "ann_date": "20260720"},
        ],
    )
    assert written == 2
    codes = {
        row[0]
        for row in conn.execute(
            "SELECT ts_code FROM raw_tushare_share_float ORDER BY ts_code"
        ).fetchall()
    }
    assert codes == {"300999.SZ", "874075.BJ"}

