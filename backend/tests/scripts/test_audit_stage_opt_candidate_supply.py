from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_stage_opt_candidate_supply.py"
SPEC = importlib.util.spec_from_file_location("audit_stage_opt_candidate_supply", SCRIPT_PATH)
audit_stage_opt_candidate_supply = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_stage_opt_candidate_supply
SPEC.loader.exec_module(audit_stage_opt_candidate_supply)


def test_summarize_stage_opt_candidate_supply_tracks_ready_and_blocked_keys() -> None:
    signal_rows = [
        *[
            {
                "stock_code": "000001",
                "signal_date": f"2026-05-{10 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
            }
            for idx in range(4)
        ],
        *[
            {
                "stock_code": "000002",
                "signal_date": f"2026-05-{20 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
            }
            for idx in range(5)
        ],
        *[
            {
                "stock_code": "000003",
                "signal_date": f"2026-05-{30 + idx:02d}",
                "formula_id": "formula_b",
                "formula_variant": "variant_b",
                "stage_bin": "2",
            }
            for idx in range(5)
        ],
    ]
    codes_with_bars = {"000001", "000002"}

    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        codes_with_bars,
        min_signals=5,
        max_examples=3,
    )

    assert summary["raw_signal_rows"] == 14
    assert summary["unique_keys"] == 3
    assert summary["ready_keys"] == 1
    assert summary["ready_coverage_pct"] == 33.33
    assert summary["blocked_reason_counts"] == {
        "below_min_signals": 1,
        "no_kline_bars": 1,
    }
    assert summary["rows_by_formula_id"] == {"formula_a": 9, "formula_b": 5}
    assert summary["rows_by_formula_variant"] == {"variant_a": 9, "variant_b": 5}
    assert summary["rows_by_stage_bin"] == {"1": 9, "2": 5}

    by_formula = {row["formula_id"]: row for row in summary["keys_by_formula_id"]}
    assert by_formula["formula_a"]["keys_total"] == 2
    assert by_formula["formula_a"]["keys_ready"] == 1
    assert by_formula["formula_b"]["keys_total"] == 1
    assert by_formula["formula_b"]["keys_ready"] == 0

    blocked_examples = summary["blocked_examples"]
    assert len(blocked_examples) == 2
    assert blocked_examples[0]["stock_code"] == "000003"
    assert blocked_examples[0]["blocked_reasons"] == ["no_kline_bars"]
    assert blocked_examples[1]["stock_code"] == "000001"
    assert blocked_examples[1]["blocked_reasons"] == ["below_min_signals"]


def test_latest_closed_trade_date_uses_existing_calendar_connection() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE dim_trading_calendar (
                trade_date TEXT,
                is_trading INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dim_trading_calendar VALUES
            ('2026-05-28', 1),
            ('2026-05-29', 1),
            ('2026-05-30', 0)
            """
        )

        assert audit_stage_opt_candidate_supply._latest_closed_trade_date(conn) == "2026-05-29"
    finally:
        conn.close()
