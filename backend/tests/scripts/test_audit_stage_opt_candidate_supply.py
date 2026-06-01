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
        dropped_unknown_stage_rows_by_formula_id={"formula_c": 7, "formula_a": 2},
        dropped_unknown_stage_rows_by_formula_variant={"variant_c": 7, "variant_a": 2},
        dropped_unknown_stage_examples=[
            {
                "stock_code": "000004",
                "signal_date": "2026-05-05",
                "formula_id": "formula_c",
                "formula_variant": "variant_c",
                "stage_bin": "?",
            }
        ],
    )

    assert summary["raw_signal_rows"] == 14
    assert summary["unique_keys"] == 3
    assert summary["ready_keys"] == 1
    assert summary["ready_coverage_pct"] == 33.33
    assert summary["blocked_reason_counts"] == {
        "below_min_signals": 1,
        "no_kline_bars": 1,
    }
    assert summary["blocked_reason_counts_by_formula_id"] == {
        "formula_a": {"below_min_signals": 1},
        "formula_b": {"no_kline_bars": 1},
    }
    assert summary["blocked_reason_counts_by_formula_variant"] == {
        "variant_a": {"below_min_signals": 1},
        "variant_b": {"no_kline_bars": 1},
    }
    assert summary["blocked_reason_counts_by_stage_bin"] == {
        "1": {"below_min_signals": 1},
        "2": {"no_kline_bars": 1},
    }
    assert summary["next_action_recommendation"] == {
        "priority": "P1",
        "focus": "upstream_candidate_supply",
        "reason": "below_min_signals dominates current blocked keys",
        "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
        "weakest_formula_ids": ["formula_b", "formula_a"],
        "weakest_stage_bins": ["2", "1"],
        "top_blocked_reason": "below_min_signals",
    }
    assert summary["rows_by_formula_id"] == {"formula_a": 9, "formula_b": 5}
    assert summary["rows_by_formula_variant"] == {"variant_a": 9, "variant_b": 5}
    assert summary["rows_by_stage_bin"] == {"1": 9, "2": 5}

    by_formula = {row["formula_id"]: row for row in summary["keys_by_formula_id"]}
    assert by_formula["formula_a"]["keys_total"] == 2
    assert by_formula["formula_a"]["keys_ready"] == 1
    assert by_formula["formula_b"]["keys_total"] == 1
    assert by_formula["formula_b"]["keys_ready"] == 0

    weakest_formula_ids = summary["weakest_keys_by_formula_id"]
    assert weakest_formula_ids[0]["formula_id"] == "formula_b"
    assert weakest_formula_ids[-1]["formula_id"] == "formula_a"

    weakest_variants = summary["weakest_keys_by_formula_variant"]
    assert weakest_variants[0]["formula_variant"] == "variant_b"

    weakest_stages = summary["weakest_keys_by_stage_bin"]
    assert weakest_stages[0]["stage_bin"] == "2"

    assert summary["dropped_unknown_stage_rows_by_formula_id"] == {
        "formula_a": 2,
        "formula_c": 7,
    }
    assert summary["dropped_unknown_stage_rows_by_formula_variant"] == {
        "variant_a": 2,
        "variant_c": 7,
    }
    assert summary["dropped_unknown_stage_examples"][0]["formula_id"] == "formula_c"

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


def test_compose_audit_result_preserves_raw_signal_rows() -> None:
    load_result = {
        "raw_rows": 10,
        "dropped_index_rows": 1,
        "dropped_unknown_stage_rows": 2,
        "dropped_unknown_stage_rows_by_formula_id": {"formula_a": 2},
        "dropped_unknown_stage_rows_by_formula_variant": {"variant_a": 2},
        "dropped_unknown_stage_examples": [{"stock_code": "000001"}],
    }
    summary = {
        "raw_signal_rows": 7,
        "unique_keys": 2,
        "ready_keys": 1,
        "ready_coverage_pct": 50.0,
        "blocked_reason_counts": {"below_min_signals": 1},
        "blocked_reason_counts_by_formula_id": {},
        "blocked_reason_counts_by_formula_variant": {},
        "blocked_reason_counts_by_stage_bin": {},
        "rows_by_formula_id": {},
        "rows_by_formula_variant": {},
        "rows_by_stage_bin": {},
        "keys_by_formula_id": [],
        "weakest_keys_by_formula_id": [],
        "keys_by_formula_variant": [],
        "weakest_keys_by_formula_variant": [],
        "keys_by_stage_bin": [],
        "weakest_keys_by_stage_bin": [],
        "blocked_examples": [],
    }

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        load_result,
        summary,
        start="2023-01-01",
        end="2026-05-29",
        min_signals=5,
        signal_rows=[{"stock_code": "000001"}] * 7,
        codes_total=3,
        codes_with_bars={"000001", "000002"},
    )

    assert result["raw_signal_rows"] == 10
    assert result["filtered_signal_rows"] == 7
    assert result["codes_with_bars"] == 2
    assert result["codes_without_bars"] == 1
