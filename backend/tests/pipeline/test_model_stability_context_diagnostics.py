from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_model_stability_context_diagnostics as subject


pytestmark = pytest.mark.pipeline


def _seed_stability_context_inputs(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE mart_model_stability_search_summary (
            run_id TEXT,
            model_selection_run_id TEXT,
            feature_table TEXT,
            feature_set_id TEXT,
            label_name TEXT,
            best_trial_number INTEGER,
            config_json TEXT
        );
        CREATE TABLE mart_model_stability_search_trial (
            run_id TEXT,
            trial_number INTEGER,
            objective_value DOUBLE,
            status TEXT,
            holdout_rank_ic DOUBLE,
            holdout_long_short_spread DOUBLE,
            walkforward_avg_rank_ic DOUBLE,
            walkforward_std_rank_ic DOUBLE,
            fold_metrics_json TEXT,
            holdout_topk_net_return DOUBLE,
            holdout_topk_turnover DOUBLE,
            holdout_topk_max_drawdown DOUBLE,
            holdout_feature_drift_psi_max DOUBLE,
            walkforward_worst_topk_drawdown DOUBLE,
            walkforward_worst_feature_drift_psi DOUBLE,
            model_family TEXT
        );
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date DATE,
            feature_set_id TEXT,
            forward_ret_60d DOUBLE,
            hs300_ret_60d DOUBLE,
            regime_flag TEXT
        );
        """
    )
    config = {
        "start": "2025-01-01",
        "end": "2025-01-20",
        "model_family": "lightgbm",
    }
    conn.execute(
        """
        INSERT INTO mart_model_stability_search_summary
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source_60d_unit",
            "selection_unit",
            "fact_feature_panel",
            "stable_set",
            "forward_ret_60d",
            3,
            json.dumps(config, ensure_ascii=False),
        ),
    )
    folds = [
        {
            "fold_id": 1,
            "test_start": "2025-01-01",
            "test_end": "2025-01-10",
            "rank_ic": -0.05,
            "spread": 0.01,
            "topk_net_return": 0.03,
            "topk_turnover": 0.40,
            "topk_max_drawdown": -0.02,
            "feature_drift_psi_max": 0.10,
        },
        {
            "fold_id": 2,
            "test_start": "2025-01-11",
            "test_end": "2025-01-17",
            "rank_ic": 0.02,
            "spread": 0.02,
            "topk_net_return": 0.04,
            "topk_turnover": 0.35,
            "topk_max_drawdown": -0.03,
            "feature_drift_psi_max": 0.12,
        },
    ]
    conn.execute(
        """
        INSERT INTO mart_model_stability_search_trial
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source_60d_unit",
            3,
            0.11,
            "fail",
            0.007,
            0.016,
            0.028,
            0.039,
            json.dumps(folds, ensure_ascii=False),
            0.05,
            0.30,
            -0.04,
            0.14,
            -0.05,
            0.14,
            "lightgbm",
        ),
    )
    rows = []
    for day in range(1, 21):
        date = f"2025-01-{day:02d}"
        regime = "rally" if day <= 10 else "range"
        market_ret = 0.12 if day <= 10 else 0.02
        rows.append(("000001.SZ", date, "stable_set", 0.04, market_ret, regime))
        rows.append(("000002.SZ", date, "stable_set", 0.02 if day <= 10 else -0.01, market_ret, regime))
    conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?)", rows)


def test_build_model_stability_context_diagnostics_persists_context_and_manifest():
    with duck_mem() as conn:
        _seed_stability_context_inputs(conn)

        result = subject.build_model_stability_context_diagnostics(
            conn,
            run_id="context_diag_unit",
            source_run_ids=["source_60d_unit"],
        )

        assert result["detail_rows"] == 3
        assert result["summary_rows"] == 1
        assert result["summaries"][0]["recommendation"] == (
            "regime_split_or_holdout_rank_calibration_before_larger_study"
        )

        detail = conn.execute(
            """
            SELECT scope, fold_id, diagnosis, row_count, date_count,
                   label_positive_rate, dominant_regime
              FROM mart_model_stability_context_diagnostic
             WHERE run_id = 'context_diag_unit'
             ORDER BY scope, fold_id
            """
        ).fetchall()
        assert len(detail) == 3
        first_fold = [row for row in detail if row["scope"] == "walkforward_fold" and row["fold_id"] == 1][0]
        assert first_fold["diagnosis"] == "broad_rally_rank_inversion"
        assert first_fold["row_count"] == 20
        assert first_fold["date_count"] == 10
        assert first_fold["label_positive_rate"] == 1.0
        assert first_fold["dominant_regime"] == "rally"

        summary = conn.execute(
            """
            SELECT fold_count, negative_rank_ic_folds, weak_rank_ic_periods,
                   low_holdout_rank_ic, high_walkforward_std,
                   drift_gate_pass, drawdown_gate_pass,
                   context_diagnosis_counts_json, main_blockers_json,
                   recommendation
              FROM mart_model_stability_context_summary
             WHERE run_id = 'context_diag_unit'
            """
        ).fetchone()
        assert summary["fold_count"] == 2
        assert summary["negative_rank_ic_folds"] == 1
        assert summary["weak_rank_ic_periods"] == 2
        assert summary["low_holdout_rank_ic"] is True
        assert summary["high_walkforward_std"] is True
        assert summary["drift_gate_pass"] is True
        assert summary["drawdown_gate_pass"] is True
        assert json.loads(summary["main_blockers_json"]) == [
            "market_phase_rank_inversion",
            "low_holdout_rank_ic",
            "high_walkforward_std_rank_ic",
        ]
        assert json.loads(summary["context_diagnosis_counts_json"]) == {
            "broad_rally_rank_inversion": 1,
            "ok": 1,
            "spread_ok_rank_weak": 1,
        }

        manifest = conn.execute(
            """
            SELECT pipeline_name, status, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'context_diag_unit'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "build_model_stability_context_diagnostics"
        assert manifest["status"] == "success"
        assert json.loads(manifest["perf_summary_json"])["detail_rows"] == 3

        versions = {
            row["table_name"]: row["actual_version"]
            for row in conn.execute(
                """
                SELECT table_name, actual_version
                  FROM dim_schema_version
                 WHERE table_name LIKE 'mart_model_stability_context%'
                """
            ).fetchall()
        }
        assert versions == {
            "mart_model_stability_context_diagnostic": "v1",
            "mart_model_stability_context_summary": "v2",
        }
