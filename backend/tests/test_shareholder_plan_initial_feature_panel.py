from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_temporal_synergy_research
from services.shareholder_plan_initial_feature_panel import (
    INITIAL_FEATURE_COLUMNS,
    PANEL_TABLE,
    QUALITY_TABLE,
    build_shareholder_plan_initial_feature_panel,
)


def _create_calendar(conn, dates: list[str]) -> None:
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading BIGINT)")
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)", [(date,) for date in dates])


def test_initial_shareholder_plan_feature_panel_encodes_sparse_events_without_nulls() -> None:
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d_rank DOUBLE,
                follow_net_return_60d DOUBLE,
                regime_flag TEXT
            )
            """
        )
        rows = [
            ("000001", "2026-01-02", 0.90, 0.10, "up"),
            ("000001", "2026-01-03", 0.80, 0.12, "flat"),
            ("000002", "2026-01-02", 0.20, 0.01, "down"),
            ("000002", "2026-01-03", 0.30, 0.02, "flat"),
            ("000003", "2026-01-02", 0.40, None, "up"),
            ("000004", "2026-01-02", None, 0.03, "up"),
        ]
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?)", rows)
        conn.execute(
            """
            CREATE TABLE mart_shareholder_plan_initial_event (
                stock_code TEXT,
                source_available_date TEXT,
                direction TEXT,
                target_amount_max BIGINT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_shareholder_plan_initial_event VALUES (?, ?, ?, ?)",
            [
                ("000001", "2025-12-15", "增持计划", 100),
                ("000001", "2026-01-02", "减持计划", 50),
                ("000002", "2025-01-01", "减持计划", 999),
            ],
        )
        _create_calendar(conn, ["2026-01-02", "2026-01-03"])

        result = build_shareholder_plan_initial_feature_panel(
            conn,
            run_id="sp_initial_panel_unit",
            labels=["follow_net_return_60d"],
            context_features=["ret_20d_rank"],
            require_complete_labels=True,
            require_complete_context=True,
        )
        latest = conn.execute(
            f"""
            SELECT *
              FROM {PANEL_TABLE}
             WHERE feature_set_id = ?
               AND stock_code = '000001'
               AND date = '2026-01-03'
            """,
            (result["feature_set_id"],),
        ).fetchone()
        quiet = conn.execute(
            f"""
            SELECT *
              FROM {PANEL_TABLE}
             WHERE feature_set_id = ?
               AND stock_code = '000002'
               AND date = '2026-01-03'
            """,
            (result["feature_set_id"],),
        ).fetchone()
        quality = conn.execute(
            f"SELECT * FROM {QUALITY_TABLE} WHERE run_id = 'sp_initial_panel_unit'"
        ).fetchone()
        null_count = conn.execute(
            f"""
            SELECT SUM(
                       CASE WHEN {' OR '.join(f'{col} IS NULL' for col in INITIAL_FEATURE_COLUMNS)}
                            THEN 1 ELSE 0 END
                   ) AS n
              FROM {PANEL_TABLE}
             WHERE feature_set_id = ?
            """,
            (result["feature_set_id"],),
        ).fetchone()["n"]

        assert result["panel_rows"] == 4
        assert result["dropped_incomplete_label_rows"] == 1
        assert result["dropped_incomplete_context_rows"] == 1
        assert result["calendar_mismatch_rows"] == 0
        assert latest["sp_initial_event_count_180d"] == 2
        assert latest["sp_initial_increase_count_180d"] == 1
        assert latest["sp_initial_decrease_count_180d"] == 1
        assert latest["sp_initial_net_amount_max_sum_180d"] == 50
        assert latest["sp_initial_days_since_decrease"] == 1
        assert latest["sp_initial_event_freshness_180d"] == pytest.approx(1.0 - 1.0 / 180.0)
        assert latest["regime_flat_flag"] == 1
        assert quiet["sp_initial_event_count_180d"] == 0
        assert quiet["sp_initial_days_since_any"] == -1
        assert quiet["sp_initial_event_freshness_180d"] == 0
        assert quiet["source_max_available_date"] == "2026-01-03"
        assert null_count == 0
        assert json.loads(quality["initial_features_json"]) == INITIAL_FEATURE_COLUMNS
    finally:
        conn.close()


def test_initial_shareholder_plan_feature_panel_feeds_temporal_synergy_research() -> None:
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d_rank DOUBLE,
                follow_net_return_60d DOUBLE
            )
            """
        )
        panel_rows = []
        dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
        for day in dates:
            for idx in range(40):
                event_group = idx % 4 in (2, 3)
                trend_group = idx % 4 in (1, 3)
                if event_group and trend_group:
                    label = 0.40
                elif event_group or trend_group:
                    label = 0.08
                else:
                    label = 0.00
                panel_rows.append((f"000{idx:03d}", day, 1.0 if trend_group else 0.0, label))
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?)", panel_rows)
        conn.execute(
            """
            CREATE TABLE mart_shareholder_plan_initial_event (
                stock_code TEXT,
                source_available_date TEXT,
                direction TEXT,
                target_amount_max BIGINT
            )
            """
        )
        event_rows = [
            (f"000{idx:03d}", "2025-12-31", "减持计划", 100)
            for idx in range(40)
            if idx % 4 in (2, 3)
        ]
        conn.executemany("INSERT INTO mart_shareholder_plan_initial_event VALUES (?, ?, ?, ?)", event_rows)
        _create_calendar(conn, dates)

        panel_result = build_shareholder_plan_initial_feature_panel(
            conn,
            run_id="sp_initial_panel_temporal_unit",
            labels=["follow_net_return_60d"],
            context_features=["ret_20d_rank"],
        )
        result = build_temporal_synergy_research.build_temporal_synergy_research(
            conn,
            run_id="sp_initial_temporal_unit",
            panel_table=PANEL_TABLE,
            feature_set_id=panel_result["feature_set_id"],
            features=["sp_initial_decrease_count_180d", "ret_20d_rank"],
            labels=["follow_net_return_60d"],
            source_available_date_column="source_max_available_date",
            min_daily_count=20,
            bucket_count=4,
            folds=2,
            top_pair_features=2,
            max_pairs=1,
            min_pair_valid_rows=40,
            min_joint_obs=4,
            active_quantile=0.5,
            interaction_uplift_threshold=0.05,
        )
        candidate = conn.execute(
            """
            SELECT selected, selection_reason, joint_uplift
              FROM mart_feature_interaction_candidate
             WHERE run_id = 'sp_initial_temporal_unit'
            """
        ).fetchone()

        assert result["selected_interaction_rows"] == 1
        assert candidate["selected"] is True
        assert candidate["selection_reason"] == "joint_effect_exceeds_standalone"
        assert candidate["joint_uplift"] > 0.05
    finally:
        conn.close()
