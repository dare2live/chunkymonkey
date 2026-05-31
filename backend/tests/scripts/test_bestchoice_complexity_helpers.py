from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _load_script(name: str):
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


context_exit = _load_script("build_bestchoice_context_exit_policy")
daily_feed = _load_script("build_bestchoice_phase2_daily_feed")


def test_context_policy_rows_select_train_best_and_report_test_metrics() -> None:
    train_bucket = {
        "below_zero_rebound_probe": [
            {"hold_days": 5, "ret": 0.10, "max_dd": -0.02},
            {"hold_days": 5, "ret": 0.20, "max_dd": -0.03},
            {"hold_days": 10, "ret": 0.01, "max_dd": -0.04},
            {"hold_days": 10, "ret": 0.02, "max_dd": -0.05},
        ]
    }
    test_bucket = {
        "below_zero_rebound_probe": [
            {"hold_days": 5, "ret": 0.03, "max_dd": -0.01},
            {"hold_days": 5, "ret": -0.01, "max_dd": -0.03},
            {"hold_days": 10, "ret": 0.99, "max_dd": -0.50},
        ]
    }

    rows = context_exit._policy_rows_for_candidate(
        "policy_run",
        "300616",
        "formula_a",
        train_bucket,
        test_bucket,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["best_sell_rule"] == "fixed_5"
    assert row["holding_days"] == 5
    assert row["n_train_signals"] == 2
    assert np.isclose(row["avg_ret"], 0.01)
    assert row["win_rate"] == 0.5
    assert np.isclose(row["avg_max_dd"], -0.02)
    assert row["confidence"] == "low"


def test_context_append_entry_metrics_preserves_fixed_hold_metrics() -> None:
    target: dict[str, list[dict]] = {}

    context_exit._append_entry_metrics(
        target,
        "dead_cross",
        "2025-01-06",
        100.0,
        np.array([100.0, 105.0, 90.0], dtype=float),
        [1, 2],
    )

    assert [row["hold_days"] for row in target["dead_cross"]] == [1, 2]
    assert np.isclose(target["dead_cross"][0]["ret"], 0.05)
    assert np.isclose(target["dead_cross"][1]["ret"], -0.10)


def test_daily_feed_uses_strict_t_plus_one_buy_date() -> None:
    trading_days = [
        pd.Timestamp("2025-01-02"),
        pd.Timestamp("2025-01-03"),
        pd.Timestamp("2025-01-06"),
    ]

    assert daily_feed._buy_date_after_signal(pd.Timestamp("2025-01-02"), trading_days) == pd.Timestamp("2025-01-03")
    assert daily_feed._buy_date_after_signal(pd.Timestamp("2025-01-03"), trading_days) == pd.Timestamp("2025-01-06")
    assert daily_feed._buy_date_after_signal(pd.Timestamp("2025-01-06"), trading_days) is None


def test_daily_feed_appends_rows_for_entries_with_calendar_bounds() -> None:
    df = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])})
    feed_rows: list[tuple] = []
    row_template = (
        "run_id",
        "300616",
        "formula_a",
        "variant_a",
        "fixed_5",
        5,
        1.2,
        0.03,
        -0.04,
        0.55,
        0.50,
        "2026-06-01 00:00:00",
    )

    daily_feed._append_feed_rows_for_entries(
        feed_rows,
        np.array([0, 2]),
        df,
        pd.Timestamp("2025-01-02"),
        pd.Timestamp("2025-01-06"),
        [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-06")],
        row_template,
    )

    assert len(feed_rows) == 1
    assert feed_rows[0][:7] == (
        "run_id",
        pd.Timestamp("2025-01-02").date(),
        pd.Timestamp("2025-01-03").date(),
        "300616",
        "formula_a",
        "variant_a",
        "fixed_5",
    )


def test_daily_feed_ranks_before_deduplicating_same_day_stock() -> None:
    signal_date = pd.Timestamp("2025-01-02").date()
    buy_date = pd.Timestamp("2025-01-03").date()
    rows = [
        ("run", signal_date, buy_date, "300616", "formula_a", "v1", "fixed_5", 5, 0.90, 0.1, -0.1, 0.6, 0.5, None, "now"),
        ("run", signal_date, buy_date, "300616", "formula_b", "v1", "fixed_5", 5, 0.80, 0.1, -0.1, 0.6, 0.5, None, "now"),
        ("run", signal_date, buy_date, "600000", "formula_c", "v1", "fixed_5", 5, 0.70, 0.1, -0.1, 0.6, 0.5, None, "now"),
    ]

    feed_df, dedup_dropped = daily_feed._rank_and_deduplicate_feed_rows(rows)

    assert dedup_dropped == 1
    assert feed_df.loc[feed_df["stock_code"] == "300616", "rank_in_date"].item() == 1
    assert feed_df.loc[feed_df["stock_code"] == "600000", "rank_in_date"].item() == 3
