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


def _create_candidate_supply_schema(
    conn: duckdb.DuckDBPyConnection,
    *,
    include_macd: bool = True,
    include_technical_stage: bool = True,
    include_kline_adjust: bool = True,
) -> None:
    conn.execute("CREATE SCHEMA sm")
    conn.execute(
        """
        CREATE TABLE sm.fact_technical_trigger (
            stock_code TEXT,
            date TEXT,
            formula_id TEXT,
            formula_variant TEXT
        )
        """
    )
    technical_stage_column = ", technical_stage TEXT" if include_technical_stage else ""
    conn.execute(
        f"""
        CREATE TABLE sm.fact_signal_context (
            stock_code TEXT,
            date TEXT
            {technical_stage_column}
        )
        """
    )
    if include_macd:
        conn.execute(
            """
            CREATE TABLE sm.mart_macd_state_history (
                stock_code TEXT,
                date TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                state TEXT
            )
            """
        )
    adjust_column = ", adjust TEXT" if include_kline_adjust else ""
    conn.execute(
        f"""
        CREATE TABLE v_price_kline_qfq (
            code TEXT,
            date TEXT,
            freq TEXT
            {adjust_column}
        )
        """
    )


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
    assert summary["signal_rows_with_bars"] == 9
    assert summary["signal_rows_without_bars"] == 5
    assert summary["signal_kline_coverage_pct"] == 64.29
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
    assert summary["blocked_reason_counts_by_formula_family"] == {
        "formula": {"below_min_signals": 1, "no_kline_bars": 1},
    }
    assert summary["blocked_reason_counts_by_registry_family"] == {
        "unregistered|formula": {"below_min_signals": 1, "no_kline_bars": 1},
    }
    assert summary["next_action_recommendation"] == {
        "priority": "P1",
        "focus": "kline_signal_coverage",
        "reason": "signal-date K-line blockers are current limiting defects",
        "recommended_lever": "repair signal-date daily/qfq bar coverage before re-running candidate supply",
        "weakest_formula_ids": ["formula_b", "formula_a"],
        "weakest_stage_bins": ["2", "1"],
        "top_blocked_reason": "no_kline_bars",
    }
    assert summary["rows_by_formula_id"] == {"formula_a": 9, "formula_b": 5}
    assert summary["rows_by_formula_variant"] == {"variant_a": 9, "variant_b": 5}
    assert summary["rows_by_formula_family"] == {"formula": 14}
    assert summary["rows_by_stage_bin"] == {"1": 9, "2": 5}
    assert summary["rows_by_registry_family"] == {"unregistered|formula": 14}

    by_formula = {row["formula_id"]: row for row in summary["keys_by_formula_id"]}
    assert by_formula["formula_a"]["keys_total"] == 2
    assert by_formula["formula_a"]["keys_ready"] == 1
    assert by_formula["formula_a"]["keys_blocked"] == 1
    assert by_formula["formula_a"]["blocked_pct"] == 50.0
    assert by_formula["formula_a"]["formula_family"] == "formula"
    assert by_formula["formula_a"]["registry_scopes"] == ["unregistered"]
    assert by_formula["formula_a"]["blocked_reason_counts"] == {"below_min_signals": 1}
    assert by_formula["formula_a"]["top_blocked_reason"] == "below_min_signals"
    assert by_formula["formula_b"]["keys_total"] == 1
    assert by_formula["formula_b"]["keys_ready"] == 0

    family_attrition = summary["formula_family_attrition"]
    assert family_attrition == [
        {
            "formula_family": "formula",
            "keys_total": 3,
            "keys_ready": 1,
            "keys_blocked": 2,
            "ready_coverage_pct": 33.33,
            "blocked_pct": 66.67,
            "signal_rows": 14,
            "blocked_reason_counts": {"below_min_signals": 1, "no_kline_bars": 1},
            "top_blocked_reason": "below_min_signals",
        }
    ]

    matrix = {
        (row["stage_bin"], row["formula_id"]): row
        for row in summary["blocked_matrix_by_stage_formula"]
    }
    assert matrix[("1", "formula_a")]["keys_blocked"] == 1
    assert matrix[("1", "formula_a")]["blocked_reason_counts"] == {"below_min_signals": 1}
    assert matrix[("2", "formula_b")]["keys_blocked"] == 1
    assert matrix[("2", "formula_b")]["blocked_reason_counts"] == {"no_kline_bars": 1}
    assert [row["formula_id"] for row in summary["top_blocked_stage_formula_cells"]] == [
        "formula_b",
        "formula_a",
    ]
    assert summary["top_blocked_registry_family_cells"] == [
        {
            "registry_scope": "unregistered",
            "formula_family": "formula",
            "keys_total": 3,
            "keys_ready": 1,
            "keys_blocked": 2,
            "ready_coverage_pct": 33.33,
            "blocked_pct": 66.67,
            "signal_rows": 14,
            "blocked_reason_counts": {"below_min_signals": 1, "no_kline_bars": 1},
            "top_blocked_reason": "below_min_signals",
        }
    ]

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
    assert blocked_examples[0]["kline_signal_rows"] == 0
    assert blocked_examples[0]["missing_kline_signal_rows"] == 5
    assert blocked_examples[0]["source_ids"] == ["unknown"]
    assert blocked_examples[0]["blocked_reasons"] == ["no_kline_bars"]
    assert blocked_examples[1]["stock_code"] == "000001"
    assert blocked_examples[1]["kline_signal_rows"] == 4
    assert blocked_examples[1]["missing_kline_signal_rows"] == 0
    assert blocked_examples[1]["source_ids"] == ["unknown"]
    assert blocked_examples[1]["blocked_reasons"] == ["below_min_signals"]


def test_summarize_stage_opt_candidate_supply_reports_source_density_buckets() -> None:
    signal_rows = [
        *[
            {
                "stock_code": "000101",
                "signal_date": f"2026-05-{10 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
                "source_id": "fact_technical_trigger",
            }
            for idx in range(4)
        ],
        *[
            {
                "stock_code": "000102",
                "signal_date": f"2026-05-{14 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
                "source_id": "mart_macd_state_history",
            }
            for idx in range(2)
        ],
        *[
            {
                "stock_code": "000103",
                "signal_date": f"2026-05-{20 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
                "source_id": "fact_technical_trigger",
            }
            for idx in range(5)
        ],
    ]

    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000101", "000102", "000103"},
        min_signals=5,
    )

    assert summary["ready_keys"] == 1
    assert summary["rows_by_source_id"] == {
        "fact_technical_trigger": 9,
        "mart_macd_state_history": 2,
    }
    cells = {
        (row["source_id"], row["stage_bin"]): row
        for row in summary["top_blocked_source_registry_family_stage_cells"]
    }
    assert cells[("fact_technical_trigger", "1")] == {
        "source_id": "fact_technical_trigger",
        "registry_scope": "unregistered",
        "formula_family": "formula",
        "stage_bin": "1",
        "keys_total": 2,
        "keys_ready": 1,
        "keys_blocked": 1,
        "ready_coverage_pct": 50.0,
        "blocked_pct": 50.0,
        "source_signal_rows": 9,
        "blocked_reason_counts": {"below_min_signals": 1},
        "top_blocked_reason": "below_min_signals",
        "kline_signal_row_buckets": {"4": 1},
    }
    assert cells[("mart_macd_state_history", "1")] == {
        "source_id": "mart_macd_state_history",
        "registry_scope": "unregistered",
        "formula_family": "formula",
        "stage_bin": "1",
        "keys_total": 1,
        "keys_ready": 0,
        "keys_blocked": 1,
        "ready_coverage_pct": 0.0,
        "blocked_pct": 100.0,
        "source_signal_rows": 2,
        "blocked_reason_counts": {"below_min_signals": 1},
        "top_blocked_reason": "below_min_signals",
        "kline_signal_row_buckets": {"2": 1},
    }


def test_source_density_buckets_are_source_specific_for_mixed_source_keys() -> None:
    signal_rows = [
        *[
            {
                "stock_code": "000201",
                "signal_date": f"2026-05-{10 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
                "source_id": "fact_technical_trigger",
            }
            for idx in range(2)
        ],
        *[
            {
                "stock_code": "000201",
                "signal_date": f"2026-05-{12 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
                "source_id": "mart_macd_state_history",
            }
            for idx in range(2)
        ],
    ]

    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000201"},
        min_signals=5,
    )

    cells = {
        row["source_id"]: row
        for row in summary["top_blocked_source_registry_family_stage_cells"]
    }
    assert summary["blocked_examples"][0]["kline_signal_rows"] == 4
    assert summary["blocked_examples"][0]["source_ids"] == [
        "fact_technical_trigger",
        "mart_macd_state_history",
    ]
    assert cells["fact_technical_trigger"]["source_signal_rows"] == 2
    assert cells["fact_technical_trigger"]["kline_signal_row_buckets"] == {"2": 1}
    assert cells["mart_macd_state_history"]["source_signal_rows"] == 2
    assert cells["mart_macd_state_history"]["kline_signal_row_buckets"] == {"2": 1}


def test_summarize_stage_opt_candidate_supply_blocks_partial_signal_date_kline_gaps() -> None:
    signal_rows = [
        {
            "stock_code": "000010",
            "signal_date": f"2026-05-{10 + idx:02d}",
            "formula_id": "formula_gap",
            "formula_variant": "variant_gap",
            "stage_bin": "1",
            "has_kline_bar": idx < 4,
        }
        for idx in range(5)
    ]

    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000010"},
        min_signals=5,
    )

    assert summary["raw_signal_rows"] == 5
    assert summary["signal_rows_with_bars"] == 4
    assert summary["signal_rows_without_bars"] == 1
    assert summary["signal_kline_coverage_pct"] == 80.0
    assert summary["ready_keys"] == 0
    assert summary["blocked_reason_counts"] == {
        "below_min_signals": 1,
        "missing_signal_kline_bars": 1,
    }
    assert summary["blocked_reason_counts_by_formula_id"] == {
        "formula_gap": {
            "below_min_signals": 1,
            "missing_signal_kline_bars": 1,
        }
    }
    assert summary["blocked_examples"] == [
        {
            "stock_code": "000010",
            "formula_id": "formula_gap",
            "formula_variant": "variant_gap",
            "stage_bin": "1",
            "signal_rows": 5,
            "kline_signal_rows": 4,
            "missing_kline_signal_rows": 1,
            "has_bars": True,
            "source_ids": ["unknown"],
            "blocked_reasons": ["below_min_signals", "missing_signal_kline_bars"],
        }
    ]
    assert summary["next_action_recommendation"]["focus"] == "kline_signal_coverage"
    assert summary["next_action_recommendation"]["top_blocked_reason"] == "missing_signal_kline_bars"


def test_summarize_stage_opt_candidate_supply_emits_structural_notes_for_macd() -> None:
    signal_rows = [
        *[
            {
                "stock_code": "000001",
                "signal_date": f"2026-05-{10 + idx:02d}",
                "formula_id": "macd_golden_cross",
                "formula_variant": "macd_golden_cross_above_zero",
                "stage_bin": "1",
            }
            for idx in range(4)
        ],
        *[
            {
                "stock_code": "000002",
                "signal_date": f"2026-05-{20 + idx:02d}",
                "formula_id": "reversal_1m_deep",
                "formula_variant": "reversal_1m_deep",
                "stage_bin": "1",
            }
            for idx in range(5)
        ],
    ]
    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000001", "000002"},
        min_signals=5,
    )
    recommendation = summary["next_action_recommendation"]

    assert recommendation["focus"] == "upstream_candidate_supply"
    assert recommendation["top_blocked_reason"] == "below_min_signals"
    assert recommendation["structural_notes"] == [
        "macd_golden_cross is capped by fact_technical_trigger PRIMARY KEY (stock_code, date, formula_id); extra MACD state rows need schema evolution, not a state-only formula tweak"
    ]

    load_result = {
        "raw_rows": len(signal_rows),
        "dropped_index_rows": 0,
        "dropped_unknown_stage_rows": 0,
        "dropped_unknown_stage_rows_by_formula_id": {},
        "dropped_unknown_stage_rows_by_formula_variant": {},
        "dropped_unknown_stage_examples": [],
    }
    result = audit_stage_opt_candidate_supply._compose_audit_result(
        load_result,
        summary,
        start="2026-05-01",
        end="2026-05-29",
        min_signals=5,
        signal_rows=signal_rows,
        codes_total=2,
        codes_with_bars={"000001", "000002"},
    )
    markdown = audit_stage_opt_candidate_supply._render_markdown(result)

    assert "## Structural Notes" in markdown
    assert "fact_technical_trigger PRIMARY KEY" in markdown
    assert "## Attrition Funnel" in markdown
    assert "## Candidate Supply Contract" in markdown
    assert "readiness.min_signals_per_key: 5" in markdown
    assert "## Formula Family Attrition" in markdown
    assert "## Top Blocked Stage x Formula Cells" in markdown
    assert "## Top Blocked Registry x Family Cells" in markdown


def test_summarize_stage_opt_candidate_supply_can_skip_detail_matrices() -> None:
    signal_rows = [
        {
            "stock_code": "000001",
            "signal_date": f"2026-05-{10 + idx:02d}",
            "formula_id": "formula_a",
            "formula_variant": "variant_a",
            "stage_bin": "1",
        }
        for idx in range(4)
    ]

    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000001"},
        min_signals=5,
        include_attrition_detail=False,
    )

    assert summary["ready_keys"] == 0
    assert summary["blocked_reason_counts"] == {"below_min_signals": 1}
    assert "blocked_matrix_by_stage_formula" not in summary
    assert "top_blocked_stage_formula_cells" not in summary
    assert "blocked_matrix_by_registry_family" not in summary
    assert "top_blocked_registry_family_cells" not in summary
    assert "blocked_matrix_by_source_registry_family_stage" not in summary
    assert "top_blocked_source_registry_family_stage_cells" not in summary


def test_load_signal_rows_includes_macd_state_history_rows() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA sm")
        conn.execute(
            """
            CREATE TABLE sm.fact_technical_trigger (
                stock_code TEXT,
                date TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                strength DOUBLE,
                state TEXT,
                reason_codes_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sm.fact_signal_context (
                stock_code TEXT,
                date TEXT,
                technical_stage TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sm.mart_macd_state_history (
                stock_code TEXT,
                date TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                state TEXT,
                strength DOUBLE,
                reason_codes_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE v_price_kline_qfq (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO sm.fact_technical_trigger
              (stock_code, date, formula_id, formula_variant, strength, state, reason_codes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "2026-05-10", "macd_golden_cross", "macd_golden_cross_above_zero", 0.8, "just_crossed", "[]"),
                ("000001", "2026-05-11", "macd_golden_cross", "macd_golden_cross_above_zero", 0.7, "just_crossed", "[]"),
                ("000001", "2026-05-12", "macd_golden_cross", "macd_golden_cross_above_zero", 0.6, "just_crossed", "[]"),
                ("000001", "2026-05-13", "macd_golden_cross", "macd_golden_cross_above_zero", 0.5, "just_crossed", "[]"),
            ],
        )
        conn.executemany(
            "INSERT INTO sm.fact_signal_context VALUES (?, ?, ?)",
            [
                ("000001", "2026-05-10", "1"),
                ("000001", "2026-05-11", "1"),
                ("000001", "2026-05-12", "1"),
                ("000001", "2026-05-13", "1"),
                ("000001", "2026-05-14", "1"),
                ("000001", "2026-05-15", "1"),
            ],
        )
        conn.executemany(
            "INSERT INTO v_price_kline_qfq VALUES (?, ?, 'daily', 'qfq')",
            [(("000001", f"2026-05-{10 + idx:02d}")) for idx in range(6)],
        )
        conn.executemany(
            """
            INSERT INTO sm.mart_macd_state_history
              (stock_code, date, formula_id, formula_variant, state, strength, reason_codes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", "2026-05-14", "macd_golden_cross", "macd_golden_cross_above_zero", "holding", 0.4, "[]"),
                ("000001", "2026-05-15", "macd_golden_cross", "macd_golden_cross_above_zero", "imminent", 0.3, "[]"),
            ],
        )

        load_result = audit_stage_opt_candidate_supply._load_signal_rows(
            conn,
            start="2026-05-10",
            end="2026-05-15",
            formula=["macd_golden_cross"],
        )
        assert load_result["raw_trigger_rows"] == 4
        assert load_result["raw_state_history_rows"] == 2
        assert load_result["raw_rows"] == 6
        assert load_result["source_load_errors"] == []
        assert len(load_result["signal_rows"]) == 6
        assert all(row["has_kline_bar"] is True for row in load_result["signal_rows"])
        assert [row["source_id"] for row in load_result["signal_rows"]] == [
            "fact_technical_trigger",
            "fact_technical_trigger",
            "fact_technical_trigger",
            "fact_technical_trigger",
            "mart_macd_state_history",
            "mart_macd_state_history",
        ]
        assert load_result["dropped_unknown_stage_rows"] == 0
    finally:
        conn.close()


def test_load_signal_rows_marks_missing_signal_date_kline_bar() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _create_candidate_supply_schema(conn)
        trigger_rows = [
            ("000010", f"2026-05-{10 + idx:02d}", "formula_gap", "variant_gap")
            for idx in range(5)
        ]
        context_rows = [
            ("000010", f"2026-05-{10 + idx:02d}", "1")
            for idx in range(5)
        ]
        kline_rows = [
            ("000010", f"2026-05-{10 + idx:02d}", "daily", "qfq")
            for idx in range(4)
        ]
        conn.executemany("INSERT INTO sm.fact_technical_trigger VALUES (?, ?, ?, ?)", trigger_rows)
        conn.executemany("INSERT INTO sm.fact_signal_context VALUES (?, ?, ?)", context_rows)
        conn.executemany("INSERT INTO v_price_kline_qfq VALUES (?, ?, ?, ?)", kline_rows)

        load_result = audit_stage_opt_candidate_supply._load_signal_rows(
            conn,
            start="2026-05-10",
            end="2026-05-14",
            formula=["formula_gap"],
        )

        assert [row["has_kline_bar"] for row in load_result["signal_rows"]] == [
            True,
            True,
            True,
            True,
            False,
        ]
        assert {row["source_id"] for row in load_result["signal_rows"]} == {"fact_technical_trigger"}
    finally:
        conn.close()


def test_compose_audit_result_reports_source_freshness_window_mismatch() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _create_candidate_supply_schema(conn, include_macd=False)
        conn.execute(
            """
            CREATE TABLE sm.dim_trading_calendar (
                trade_date TEXT,
                is_trading INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO sm.dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-06-01", 1),
                ("2026-06-02", 1),
                ("2026-06-03", 1),
                ("2026-06-04", 1),
                ("2026-06-05", 1),
            ],
        )
        conn.executemany(
            "INSERT INTO sm.fact_technical_trigger VALUES (?, ?, ?, ?)",
            [
                ("000001", "2026-06-01", "reversal_1w", "reversal_1w"),
                ("000001", "2026-06-02", "reversal_1w", "reversal_1w"),
            ],
        )
        conn.executemany(
            "INSERT INTO sm.fact_signal_context VALUES (?, ?, ?)",
            [
                ("000001", "2026-06-01", "4"),
                ("000001", "2026-06-02", "4"),
            ],
        )
        conn.executemany(
            "INSERT INTO v_price_kline_qfq VALUES (?, ?, ?, ?)",
            [
                ("000001", "2026-06-01", "daily", "qfq"),
                ("000001", "2026-06-02", "daily", "qfq"),
                ("000001", "2026-06-03", "daily", "qfq"),
                ("000001", "2026-06-04", "daily", "qfq"),
            ],
        )

        load_result = audit_stage_opt_candidate_supply._load_signal_rows(
            conn,
            start="2026-06-01",
            end="2026-06-05",
            min_signals=5,
            formula=["reversal_1w"],
        )
        signal_rows = load_result["signal_rows"]
        codes_with_bars = audit_stage_opt_candidate_supply._load_kline_codes(
            conn,
            codes=["000001"],
            start="2026-06-01",
            end="2026-06-05",
        )
        summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
            signal_rows,
            codes_with_bars,
            min_signals=5,
        )
        result = audit_stage_opt_candidate_supply._compose_audit_result(
            load_result,
            summary,
            start="2026-06-01",
            end="2026-06-05",
            min_signals=5,
            signal_rows=signal_rows,
            codes_total=1,
            codes_with_bars=codes_with_bars,
        )
        markdown = audit_stage_opt_candidate_supply._render_markdown(result)

        warnings = result["source_freshness"]["warnings"]
        assert {warning["reason"] for warning in warnings} == {
            "source_max_date_before_kline_max",
            "source_window_signal_dates_below_min_signals",
        }
        assert result["source_freshness"]["sources"][0]["max_signal_date"] == "2026-06-02"
        assert result["source_freshness"]["sources"][0]["kline_dates_after_max_signal_date_sample"] == [
            "2026-06-03",
            "2026-06-04",
        ]
        assert result["next_action_recommendation"]["focus"] == "candidate_supply_freshness"
        assert result["next_action_recommendation"]["top_blocked_reason"] == "source_freshness_window"
        assert result["attrition_funnel"]["source_freshness_warning_count"] == 2
        assert "## Source Freshness" in markdown
        assert "reversal_1w:max=2026-06-02,dates=2,rows=2" in markdown
    finally:
        conn.close()


def test_load_signal_rows_default_freshness_reports_trigger_and_macd_sources() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _create_candidate_supply_schema(conn)
        conn.execute(
            """
            CREATE TABLE sm.dim_trading_calendar (
                trade_date TEXT,
                is_trading INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO sm.dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-06-01", 1),
                ("2026-06-02", 1),
                ("2026-06-03", 1),
                ("2026-06-04", 1),
                ("2026-06-05", 1),
            ],
        )
        conn.executemany(
            "INSERT INTO sm.fact_technical_trigger VALUES (?, ?, ?, ?)",
            [
                ("000001", "2026-06-01", "formula_a", "variant_a"),
            ],
        )
        conn.executemany(
            "INSERT INTO sm.mart_macd_state_history VALUES (?, ?, ?, ?, ?)",
            [
                ("000002", "2026-06-03", "macd_golden_cross", "macd_golden_cross_above_zero", "holding"),
            ],
        )
        conn.executemany(
            "INSERT INTO sm.fact_signal_context VALUES (?, ?, ?)",
            [
                ("000001", "2026-06-01", "1"),
                ("000002", "2026-06-03", "1"),
            ],
        )
        conn.executemany(
            "INSERT INTO v_price_kline_qfq VALUES (?, ?, ?, ?)",
            [
                ("000001", "2026-06-01", "daily", "qfq"),
                ("000002", "2026-06-03", "daily", "qfq"),
                ("000001", "2026-06-04", "daily", "qfq"),
            ],
        )

        load_result = audit_stage_opt_candidate_supply._load_signal_rows(
            conn,
            start="2026-06-01",
            end="2026-06-05",
            min_signals=5,
            formula=None,
        )

        sources = {
            row["source_id"]: row
            for row in load_result["source_freshness"]["sources"]
        }
        assert set(sources) == {"fact_technical_trigger", "mart_macd_state_history"}
        assert sources["fact_technical_trigger"]["max_signal_date"] == "2026-06-01"
        assert sources["mart_macd_state_history"]["max_signal_date"] == "2026-06-03"
        assert {
            (warning["source_id"], warning["reason"])
            for warning in load_result["source_freshness"]["warnings"]
        } == {
            ("fact_technical_trigger", "source_max_date_before_kline_max"),
            ("fact_technical_trigger", "source_window_signal_dates_below_min_signals"),
            ("mart_macd_state_history", "source_max_date_before_kline_max"),
            ("mart_macd_state_history", "source_window_signal_dates_below_min_signals"),
        }
    finally:
        conn.close()


def test_compose_audit_result_does_not_route_to_freshness_for_unblocked_stale_source() -> None:
    load_result = {
        "raw_rows": 4,
        "raw_trigger_rows": 4,
        "raw_state_history_rows": 0,
        "source_freshness": {
            "warnings": [
                {
                    "source_id": "mart_macd_state_history",
                    "reason": "source_max_date_before_kline_max",
                    "max_signal_date": "2026-06-01",
                    "kline_max_date": "2026-06-04",
                    "signal_date_count": 1,
                    "min_signals": 5,
                }
            ]
        },
        "dropped_index_rows": 0,
        "dropped_unknown_stage_rows": 0,
        "dropped_unknown_stage_rows_by_formula_id": {},
        "dropped_unknown_stage_rows_by_formula_variant": {},
        "dropped_unknown_stage_examples": [],
    }
    expected_recommendation = {
        "priority": "P1",
        "focus": "upstream_candidate_supply",
        "reason": "below_min_signals dominates current blocked keys",
        "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
        "weakest_formula_ids": ["reversal_1w"],
        "weakest_stage_bins": ["4"],
        "top_blocked_reason": "below_min_signals",
    }
    summary = {
        "unique_keys": 1,
        "ready_keys": 0,
        "ready_coverage_pct": 0.0,
        "blocked_reason_counts": {"below_min_signals": 1},
        "blocked_matrix_by_source_registry_family_stage": [
            {
                "source_id": "fact_technical_trigger",
                "blocked_reason_counts": {"below_min_signals": 1},
            }
        ],
        "next_action_recommendation": expected_recommendation,
    }

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        load_result,
        summary,
        start="2026-06-01",
        end="2026-06-05",
        min_signals=5,
        signal_rows=[{"stock_code": "000001"}] * 4,
        codes_total=1,
        codes_with_bars={"000001"},
    )

    assert result["verdict"] == "WARN"
    assert result["attrition_funnel"]["source_freshness_warning_count"] == 1
    assert result["next_action_recommendation"] == expected_recommendation


def test_load_signal_rows_reports_macd_state_history_load_errors() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE SCHEMA sm")
        conn.execute(
            """
            CREATE TABLE sm.fact_technical_trigger (
                stock_code TEXT,
                date TEXT,
                formula_id TEXT,
                formula_variant TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sm.fact_signal_context (
                stock_code TEXT,
                date TEXT,
                technical_stage TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE v_price_kline_qfq (
                code TEXT,
                date TEXT,
                freq TEXT,
                adjust TEXT
            )
            """
        )

        load_result = audit_stage_opt_candidate_supply._load_signal_rows(
            conn,
            start="2026-05-10",
            end="2026-05-15",
            formula=["macd_golden_cross"],
        )

        assert load_result["raw_state_history_rows"] == 0
        assert load_result["source_load_errors"][0]["source_id"] == "mart_macd_state_history"
        assert load_result["source_load_errors"][0]["table"] == "sm.mart_macd_state_history"
    finally:
        conn.close()


def test_audit_source_schema_respects_formula_filter_for_diagnostic_source() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _create_candidate_supply_schema(conn, include_macd=False)

        result = audit_stage_opt_candidate_supply._audit_source_schema(
            conn,
            formula=["reversal_1m_deep"],
        )

        assert result["source_schema_errors"] == []
        assert {
            (row["source_id"], row["table"])
            for row in result["source_schema_checks"]
        } == {
            ("fact_technical_trigger", "sm.fact_technical_trigger"),
            ("fact_technical_trigger", "sm.fact_signal_context"),
            ("fact_technical_trigger", "v_price_kline_qfq"),
        }
    finally:
        conn.close()


def test_audit_source_schema_reports_missing_join_columns() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _create_candidate_supply_schema(conn, include_technical_stage=False)

        result = audit_stage_opt_candidate_supply._audit_source_schema(
            conn,
            formula=["reversal_1m_deep"],
        )

        assert result["source_schema_errors"] == [
            {
                "source_id": "fact_technical_trigger",
                "table": "sm.fact_signal_context",
                "check_type": "join_table",
                "missing_columns": ["technical_stage"],
            }
        ]
    finally:
        conn.close()


def test_min_signals_sensitivity_reports_threshold_lift() -> None:
    signal_rows = [
        *[
            {
                "stock_code": "000001",
                "signal_date": f"2026-05-{10 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
            }
            for idx in range(5)
        ],
        *[
            {
                "stock_code": "000002",
                "signal_date": f"2026-05-{20 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
            }
            for idx in range(4)
        ],
        *[
            {
                "stock_code": "000003",
                "signal_date": f"2026-05-{30 + idx:02d}",
                "formula_id": "formula_a",
                "formula_variant": "variant_a",
                "stage_bin": "1",
            }
            for idx in range(3)
        ],
    ]
    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000001", "000002", "000003"},
        min_signals=5,
    )
    sensitivity = audit_stage_opt_candidate_supply._build_min_signals_sensitivity(
        signal_rows,
        {"000001", "000002", "000003"},
        baseline_min_signals=5,
    )

    assert summary["ready_keys"] == 1
    assert sensitivity == [
        {
            "min_signals": 4,
            "ready_keys": 2,
            "ready_coverage_pct": 66.67,
            "delta_ready_keys": 1,
            "delta_ready_coverage_pct": 33.34,
            "below_min_signals": 1,
            "delta_below_min_signals": -1,
            "no_kline_bars": 0,
            "delta_no_kline_bars": 0,
            "missing_signal_kline_bars": 0,
            "delta_missing_signal_kline_bars": 0,
            "next_action_recommendation": {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "top_blocked_reason": "below_min_signals",
            },
        },
        {
            "min_signals": 3,
            "ready_keys": 3,
            "ready_coverage_pct": 100.0,
            "delta_ready_keys": 2,
            "delta_ready_coverage_pct": 66.67,
            "below_min_signals": 0,
            "delta_below_min_signals": -2,
            "no_kline_bars": 0,
            "delta_no_kline_bars": 0,
            "missing_signal_kline_bars": 0,
            "delta_missing_signal_kline_bars": 0,
            "next_action_recommendation": {
                "priority": "P2",
                "focus": "candidate_supply_monitoring",
                "reason": "no blocking reasons detected in current slice",
                "recommended_lever": "keep monitoring upstream supply and PIT coverage",
                "top_blocked_reason": None,
            },
        },
        {
            "min_signals": 2,
            "ready_keys": 3,
            "ready_coverage_pct": 100.0,
            "delta_ready_keys": 2,
            "delta_ready_coverage_pct": 66.67,
            "below_min_signals": 0,
            "delta_below_min_signals": -2,
            "no_kline_bars": 0,
            "delta_no_kline_bars": 0,
            "missing_signal_kline_bars": 0,
            "delta_missing_signal_kline_bars": 0,
            "next_action_recommendation": {
                "priority": "P2",
                "focus": "candidate_supply_monitoring",
                "reason": "no blocking reasons detected in current slice",
                "recommended_lever": "keep monitoring upstream supply and PIT coverage",
                "top_blocked_reason": None,
            },
        },
    ]

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        {
            "raw_rows": len(signal_rows),
            "dropped_index_rows": 0,
            "dropped_unknown_stage_rows": 0,
            "dropped_unknown_stage_rows_by_formula_id": {},
            "dropped_unknown_stage_rows_by_formula_variant": {},
            "dropped_unknown_stage_examples": [],
        },
        summary,
        start="2026-05-01",
        end="2026-05-29",
        min_signals=5,
        signal_rows=signal_rows,
        codes_total=3,
        codes_with_bars={"000001", "000002", "000003"},
    )
    result["min_signals_sensitivity"] = sensitivity
    markdown = audit_stage_opt_candidate_supply._render_markdown(result)

    assert "## Min Signals Sensitivity" in markdown
    assert "min_signals=4" in markdown
    assert "min_signals=3" in markdown
    assert "min_signals=2" in markdown
    assert "Δmissing_signal_kline=+0" in markdown


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
    assert result["schema_version"] == 1
    assert result["candidate_supply_contract"]["version"] == 1
    assert result["candidate_supply_contract"]["readiness"] == {"min_signals_per_key": 5}
    assert {
        source["source_id"]
        for source in result["candidate_supply_contract"]["sources"]
    } == {"fact_technical_trigger", "mart_macd_state_history"}
    assert result["filtered_signal_rows"] == 7
    assert result["codes_with_bars"] == 2
    assert result["codes_without_bars"] == 1
    assert result["verdict"] == "WARN"
    assert result["attrition_funnel"] == {
        "raw_rows": 10,
        "raw_trigger_rows": 10,
        "raw_state_history_rows": 0,
        "dropped_index_rows": 1,
        "dropped_unknown_stage_rows": 2,
        "filtered_signal_rows": 7,
        "signal_rows_with_bars": 0,
        "signal_rows_without_bars": 0,
        "signal_kline_coverage_pct": 0.0,
        "source_freshness_warning_count": 0,
        "unique_keys": 2,
        "blocked_keys": 1,
        "ready_keys": 1,
        "ready_coverage_pct": 50.0,
        "blocked_reason_counts": {"below_min_signals": 1},
    }


def test_compose_audit_result_treats_empty_candidate_supply_as_warn() -> None:
    load_result = {
        "raw_rows": 0,
        "raw_trigger_rows": 0,
        "raw_state_history_rows": 0,
        "dropped_index_rows": 0,
        "dropped_unknown_stage_rows": 0,
        "dropped_unknown_stage_rows_by_formula_id": {},
        "dropped_unknown_stage_rows_by_formula_variant": {},
        "dropped_unknown_stage_examples": [],
    }
    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        [],
        set(),
        min_signals=5,
    )

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        load_result,
        summary,
        start="2099-01-01",
        end="2099-01-02",
        min_signals=5,
        signal_rows=[],
        codes_total=0,
        codes_with_bars=set(),
    )

    assert result["verdict"] == "WARN"
    assert result["raw_signal_rows"] == 0
    assert result["unique_keys"] == 0
    assert result["next_action_recommendation"] == {
        "priority": "P1",
        "focus": "candidate_supply_coverage",
        "reason": "no candidate keys were available for the selected audit scope",
        "recommended_lever": "check date, formula, stock filters, and upstream signal inputs before treating supply as clean",
        "weakest_formula_ids": [],
        "weakest_stage_bins": [],
        "top_blocked_reason": "no_candidate_supply",
    }


def test_compose_audit_result_treats_source_load_errors_as_warn() -> None:
    signal_rows = [
        {
            "stock_code": "000001",
            "signal_date": f"2026-05-{10 + idx:02d}",
            "formula_id": "macd_golden_cross",
            "formula_variant": "macd_golden_cross_above_zero",
            "stage_bin": "1",
        }
        for idx in range(5)
    ]
    load_result = {
        "raw_rows": 5,
        "raw_trigger_rows": 5,
        "raw_state_history_rows": 0,
        "source_load_errors": [
            {
                "source_id": "mart_macd_state_history",
                "table": "sm.mart_macd_state_history",
                "error_type": "CatalogException",
                "error": "missing table",
            }
        ],
        "dropped_index_rows": 0,
        "dropped_unknown_stage_rows": 0,
        "dropped_unknown_stage_rows_by_formula_id": {},
        "dropped_unknown_stage_rows_by_formula_variant": {},
        "dropped_unknown_stage_examples": [],
    }
    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        signal_rows,
        {"000001"},
        min_signals=5,
    )

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        load_result,
        summary,
        start="2026-05-10",
        end="2026-05-15",
        min_signals=5,
        signal_rows=signal_rows,
        codes_total=1,
        codes_with_bars={"000001"},
    )
    markdown = audit_stage_opt_candidate_supply._render_markdown(result)

    assert result["verdict"] == "WARN"
    assert result["source_load_errors"] == load_result["source_load_errors"]
    assert result["next_action_recommendation"]["focus"] == "candidate_supply_source_load"
    assert "## Source Load Errors" in markdown
    assert "mart_macd_state_history" in markdown


def test_compose_audit_result_treats_source_schema_errors_as_warn() -> None:
    load_result = audit_stage_opt_candidate_supply._empty_load_result(
        {
            "source_schema_checks": [
                {
                    "source_id": "fact_technical_trigger",
                    "table": "sm.fact_signal_context",
                    "check_type": "join_table",
                    "required_columns": ["stock_code", "date", "technical_stage"],
                    "missing_columns": ["technical_stage"],
                    "status": "FAIL",
                }
            ],
            "source_schema_errors": [
                {
                    "source_id": "fact_technical_trigger",
                    "table": "sm.fact_signal_context",
                    "check_type": "join_table",
                    "missing_columns": ["technical_stage"],
                }
            ],
        }
    )
    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        [],
        set(),
        min_signals=5,
    )

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        load_result,
        summary,
        start="2026-06-01",
        end="2026-06-02",
        min_signals=5,
        signal_rows=[],
        codes_total=0,
        codes_with_bars=set(),
    )
    markdown = audit_stage_opt_candidate_supply._render_markdown(result)

    assert result["verdict"] == "WARN"
    assert result["source_schema_errors"] == load_result["source_schema_errors"]
    assert result["next_action_recommendation"]["focus"] == "candidate_supply_source_schema"
    assert result["next_action_recommendation"]["top_blocked_reason"] == "source_schema_error"
    assert "## Source Schema Errors" in markdown
    assert "technical_stage" in markdown


def test_limit_stock_filter_scopes_unknown_stage_verdict() -> None:
    load_result = {
        "raw_rows": 12,
        "raw_trigger_rows": 9,
        "raw_state_history_rows": 3,
        "_raw_rows_by_stock_code": {"000001": 5, "000002": 7},
        "_raw_trigger_rows_by_stock_code": {"000001": 4, "000002": 5},
        "_raw_state_history_rows_by_stock_code": {"000001": 1, "000002": 2},
        "dropped_index_rows": 0,
        "_dropped_index_rows_by_stock_code": {},
        "dropped_unknown_stage_rows": 4,
        "dropped_unknown_stage_rows_by_formula_id": {"formula_a": 4},
        "dropped_unknown_stage_rows_by_formula_variant": {"variant_a": 4},
        "dropped_unknown_stage_examples": [{"stock_code": "000002", "formula_id": "formula_a"}],
        "_dropped_unknown_stage_rows_by_stock_code": {"000002": 4},
        "_dropped_unknown_stage_rows_by_stock_formula_id": {("000002", "formula_a"): 4},
        "_dropped_unknown_stage_rows_by_stock_formula_variant": {("000002", "variant_a"): 4},
    }
    scoped_load_result = audit_stage_opt_candidate_supply._filter_load_result_for_stock_codes(
        load_result,
        {"000001"},
    )
    summary = {
        "unique_keys": 1,
        "ready_keys": 1,
        "ready_coverage_pct": 100.0,
        "blocked_reason_counts": {},
    }

    result = audit_stage_opt_candidate_supply._compose_audit_result(
        scoped_load_result,
        summary,
        start="2023-01-01",
        end="2026-05-29",
        min_signals=5,
        signal_rows=[{"stock_code": "000001"}] * 5,
        codes_total=1,
        codes_with_bars={"000001"},
    )

    assert scoped_load_result["dropped_unknown_stage_rows"] == 0
    assert scoped_load_result["dropped_unknown_stage_rows_by_formula_id"] == {}
    assert scoped_load_result["raw_rows"] == 5
    assert scoped_load_result["raw_trigger_rows"] == 4
    assert scoped_load_result["raw_state_history_rows"] == 1
    assert result["verdict"] == "PASS"
    assert result["attrition_funnel"]["raw_rows"] == 5
    assert result["attrition_funnel"]["dropped_unknown_stage_rows"] == 0


def test_limit_stock_candidates_keep_unknown_stage_only_slice_warn() -> None:
    load_result = {
        "raw_rows": 5,
        "raw_trigger_rows": 5,
        "raw_state_history_rows": 0,
        "_raw_rows_by_stock_code": {"000001": 5},
        "_raw_trigger_rows_by_stock_code": {"000001": 5},
        "_raw_state_history_rows_by_stock_code": {},
        "dropped_index_rows": 0,
        "_dropped_index_rows_by_stock_code": {},
        "dropped_unknown_stage_rows": 5,
        "dropped_unknown_stage_rows_by_formula_id": {"formula_a": 5},
        "dropped_unknown_stage_rows_by_formula_variant": {"variant_a": 5},
        "dropped_unknown_stage_examples": [{"stock_code": "000001", "formula_id": "formula_a"}],
        "_dropped_unknown_stage_rows_by_stock_code": {"000001": 5},
        "_dropped_unknown_stage_rows_by_stock_formula_id": {("000001", "formula_a"): 5},
        "_dropped_unknown_stage_rows_by_stock_formula_variant": {("000001", "variant_a"): 5},
        "signal_rows": [],
    }

    codes = audit_stage_opt_candidate_supply._limit_candidate_stock_codes(load_result, [])[:3]
    scoped_load_result = audit_stage_opt_candidate_supply._filter_load_result_for_stock_codes(
        load_result,
        set(codes),
    )
    summary = audit_stage_opt_candidate_supply.summarize_stage_opt_candidate_supply(
        scoped_load_result["signal_rows"],
        set(),
        min_signals=5,
    )
    result = audit_stage_opt_candidate_supply._compose_audit_result(
        scoped_load_result,
        summary,
        start="2026-06-01",
        end="2026-06-02",
        min_signals=5,
        signal_rows=scoped_load_result["signal_rows"],
        codes_total=len(codes),
        codes_with_bars=set(),
    )

    assert codes == ["000001"]
    assert scoped_load_result["raw_rows"] == 5
    assert scoped_load_result["dropped_unknown_stage_rows"] == 5
    assert result["verdict"] == "WARN"
    assert result["next_action_recommendation"] == {
        "priority": "P1",
        "focus": "stage_context_coverage",
        "reason": "unknown-stage rows were dropped before candidate-key evaluation",
        "recommended_lever": "repair missing or invalid technical_stage coverage before treating candidate supply as clean",
        "weakest_formula_ids": ["formula_a"],
        "weakest_stage_bins": ["unknown"],
        "top_blocked_reason": "unknown_stage",
    }
    assert result["attrition_funnel"]["dropped_unknown_stage_rows"] == 5
