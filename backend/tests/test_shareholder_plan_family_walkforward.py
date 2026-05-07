from __future__ import annotations

from conftest import duck_mem
from services.shareholder_plan_family_walkforward import (
    FOLD_TABLE,
    SUMMARY_TABLE,
    build_shareholder_plan_family_walkforward,
)


def _seed_panel_and_sources(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date TEXT,
            follow_net_return_60d DOUBLE
        );
        INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-02', 0.10),
            ('000002', '2026-01-02', -0.10),
            ('000001', '2026-01-05', 0.12),
            ('000002', '2026-01-05', -0.08),
            ('000001', '2026-01-06', 0.11),
            ('000002', '2026-01-06', -0.09),
            ('000001', '2026-01-07', 0.09),
            ('000002', '2026-01-07', -0.06),
            ('000001', '2026-01-08', 0.08),
            ('000002', '2026-01-08', -0.05),
            ('000001', '2026-01-09', 0.07),
            ('000002', '2026-01-09', -0.04);

        CREATE TABLE fact_shareholder_plan_tdx_f10 (
            stock_code TEXT,
            source_available_date TEXT,
            direction TEXT,
            progress TEXT,
            target_amount_min DOUBLE,
            target_amount_max DOUBLE
        );
        INSERT INTO fact_shareholder_plan_tdx_f10 VALUES
            ('000001', '2026-01-06', '增持', '完成', 100.0, 200.0),
            ('000002', '2026-01-05', '减持', '进行中', 50.0, 80.0);

        CREATE TABLE mart_shareholder_plan_initial_event (
            stock_code TEXT,
            source_available_date TEXT,
            direction TEXT,
            target_amount_min DOUBLE,
            target_amount_max DOUBLE
        );
        INSERT INTO mart_shareholder_plan_initial_event VALUES
            ('000001', '2026-01-02', '增持', 100.0, 200.0),
            ('000002', '2026-01-04', '减持', 50.0, 80.0);

        CREATE TABLE mart_shareholder_plan_feature_family_eval (
            run_id TEXT,
            built_at TEXT
        );
        INSERT INTO mart_shareholder_plan_feature_family_eval VALUES
            ('family_eval_unit', '2026-01-09T00:00:00');
        """
    )


def test_shareholder_plan_family_walkforward_records_fold_and_summary_evidence() -> None:
    with duck_mem() as conn:
        _seed_panel_and_sources(conn)

        result = build_shareholder_plan_family_walkforward(
            conn,
            run_id="shareholder_plan_wf_unit",
            labels=["follow_net_return_60d"],
            fold_count=2,
            train_days=2,
            holdout_days=1,
            min_daily_count=1,
            min_folds=1,
            min_avg_signal_rank_ic=0.0,
            max_long_short_drawdown=1.0,
            min_active_pct=0.0,
        )

        assert result["status"] == "completed"
        assert result["source_eval_run_id"] == "family_eval_unit"
        assert result["inserted_summary_rows"] == 13
        assert result["inserted_fold_rows"] == 26
        assert "stage_timings" in result

        row = conn.execute(
            f"""
            SELECT gate_status, valid_fold_count, avg_signal_adjusted_holdout_rank_ic,
                   avg_holdout_long_short_spread
              FROM {SUMMARY_TABLE}
             WHERE run_id = 'shareholder_plan_wf_unit'
               AND source_family = 'initial_event'
               AND feature_name = 'shareholder_plan_increase_count_180d'
               AND label_name = 'follow_net_return_60d'
            """
        ).fetchone()
        assert row["gate_status"] == "candidate_for_multivariate_validation"
        assert row["valid_fold_count"] == 2
        assert row["avg_signal_adjusted_holdout_rank_ic"] > 0
        assert row["avg_holdout_long_short_spread"] > 0

        fold_row = conn.execute(
            f"""
            SELECT signal_direction, holdout_top_signal_rows, holdout_bottom_signal_rows
              FROM {FOLD_TABLE}
             WHERE run_id = 'shareholder_plan_wf_unit'
               AND source_family = 'initial_event'
               AND feature_name = 'shareholder_plan_decrease_count_180d'
             ORDER BY fold_id
             LIMIT 1
            """
        ).fetchone()
        assert fold_row["signal_direction"] == -1
        assert fold_row["holdout_top_signal_rows"] > 0
        assert fold_row["holdout_bottom_signal_rows"] > 0


def test_shareholder_plan_family_walkforward_handles_missing_source_families() -> None:
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                follow_net_return_60d DOUBLE
            );
            INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02', 0.10);
            """
        )

        result = build_shareholder_plan_family_walkforward(
            conn,
            run_id="shareholder_plan_wf_missing_unit",
            labels=["follow_net_return_60d"],
            fold_count=1,
            train_days=1,
            holdout_days=1,
            min_daily_count=1,
            min_folds=1,
        )

        assert result["inserted_fold_rows"] == 0
        assert result["inserted_summary_rows"] == 0
        assert {item["status"] for item in result["family_evidence"]} == {"missing_source_table"}
