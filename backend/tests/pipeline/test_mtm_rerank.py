from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import rerank_optuna_synergy_mtm as subject


pytestmark = pytest.mark.pipeline


def test_candidate_fingerprint_normalizes_interaction_order() -> None:
    left = subject._candidate_fingerprint(
        '["b","a"]',
        '[{"feature_a":"x","feature_b":"y","interaction_type":"pair"},'
        '{"feature_a":"c","feature_b":"d","interaction_type":"conditional"}]',
    )
    right = subject._candidate_fingerprint(
        '["a","b"]',
        '[{"feature_a":"c","feature_b":"d","interaction_type":"conditional"},'
        '{"feature_a":"x","feature_b":"y","interaction_type":"pair"}]',
    )

    assert left == right


def _seed_optuna_trials(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE mart_optuna_synergy_study_summary (
            run_id TEXT,
            source_run_id TEXT,
            label_name TEXT,
            best_trial_number INTEGER,
            objective_score DOUBLE,
            trials INTEGER,
            study_total_trials INTEGER,
            selected_features_json TEXT,
            selected_interactions_json TEXT,
            config_json TEXT,
            built_at TEXT
        );
        INSERT INTO mart_optuna_synergy_study_summary VALUES
            ('optuna_unit', 'temporal_unit', 'follow_net_return_60d',
             1, 3.0, 2, 2, '["signal_a"]', '[]', '{}', '2026-05-07T00:00:00');

        CREATE TABLE mart_optuna_synergy_trial (
            run_id TEXT,
            source_run_id TEXT,
            label_name TEXT,
            trial_number INTEGER,
            objective_value DOUBLE,
            selected_count INTEGER,
            selected_interaction_count INTEGER,
            selected_features_json TEXT,
            selected_interactions_json TEXT,
            params_json TEXT,
            metrics_json TEXT,
            built_at TEXT
        );
        INSERT INTO mart_optuna_synergy_trial VALUES
            ('optuna_unit', 'temporal_unit', 'follow_net_return_60d',
             1, 3.0, 1, 0, '["signal_a"]', '[]', '{}', '{}', '2026-05-07T00:00:01'),
            ('optuna_unit', 'temporal_unit', 'follow_net_return_60d',
             2, 2.0, 1, 0, '["signal_b"]', '[]', '{}', '{}', '2026-05-07T00:00:02');
        """
    )


def test_mtm_rerank_records_trial_scores_and_best(monkeypatch) -> None:
    with duck_mem() as conn:
        _seed_optuna_trials(conn)

        def fake_mtm(_conn, *, candidate_run_id: str, run_id: str, **kwargs):
            if candidate_run_id.endswith("_trial_1"):
                return {
                    "run_id": run_id,
                    "validation_status": "blocked",
                    "promotion_status": "research_only",
                    "production_eligible": False,
                    "blockers": ["excessive_mark_to_market_drawdown"],
                    "signal_count": 100,
                    "repeated_signal_suppressed_count": 60,
                    "position_count": 40,
                    "total_return": 0.30,
                    "annualized_return": 0.10,
                    "max_drawdown": -0.50,
                    "sharpe": 0.20,
                    "avg_active_positions": 800.0,
                    "position_hit_rate": 0.45,
                    "missing_entry_price_count": 0,
                    "missing_exit_price_count": 0,
                    "missing_path_price_count": 0,
                    "non_tdxhub_kline_count": 0,
                }
            return {
                "run_id": run_id,
                "validation_status": "pass",
                "promotion_status": "research_only",
                "production_eligible": False,
                "blockers": [],
                "signal_count": 100,
                "repeated_signal_suppressed_count": 10,
                "position_count": 60,
                "total_return": 0.20,
                "annualized_return": 0.08,
                "max_drawdown": -0.10,
                "sharpe": 0.60,
                "avg_active_positions": 300.0,
                "position_hit_rate": 0.55,
                "missing_entry_price_count": 0,
                "missing_exit_price_count": 0,
                "missing_path_price_count": 0,
                "non_tdxhub_kline_count": 0,
            }

        monkeypatch.setattr(subject, "validate_synergy_policy_mark_to_market", fake_mtm)

        result = subject.rerank_optuna_synergy_mtm(
            conn,
            optuna_run_id="optuna_unit",
            run_id="mtm_rerank_unit",
            max_trials=2,
            progress=False,
        )

        rows = conn.execute(
            """
            SELECT trial_number, validation_status, mtm_objective,
                   repeated_signal_suppression_ratio
              FROM mart_synergy_policy_mtm_rerank
             WHERE run_id = 'mtm_rerank_unit'
             ORDER BY trial_number
            """
        ).fetchall()
        summary = conn.execute(
            "SELECT * FROM mart_synergy_policy_mtm_rerank_summary WHERE run_id = 'mtm_rerank_unit'"
        ).fetchone()
        candidate = conn.execute(
            "SELECT * FROM mart_synergy_policy_candidate WHERE run_id = 'mtm_rerank_unit_trial_2'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'mtm_rerank_unit'"
        ).fetchone()

        assert result["evaluated_trials"] == 2
        assert result["best"]["trial_number"] == 2
        assert rows[0]["validation_status"] == "blocked"
        assert rows[1]["validation_status"] == "pass"
        assert rows[1]["mtm_objective"] > rows[0]["mtm_objective"]
        assert rows[0]["repeated_signal_suppression_ratio"] == pytest.approx(0.6)
        assert summary["best_trial_number"] == 2
        assert summary["best_validation_status"] == "pass"
        assert json.loads(summary["best_blockers_json"]) == []
        assert candidate["gate_status"] == "research_only"
        assert json.loads(candidate["notes_json"])["origin"] == "post_optuna_mtm_rerank"
        manifest_summary = json.loads(manifest["perf_summary_json"])
        assert manifest_summary["evaluated_trials"] == 2
        assert "stage_timings" in manifest_summary
        assert manifest_summary["stage_timings"]["total_s"] >= 0


def test_mtm_rerank_skips_duplicate_candidate_fingerprints(monkeypatch) -> None:
    with duck_mem() as conn:
        _seed_optuna_trials(conn)
        conn.execute(
            """
            INSERT INTO mart_optuna_synergy_trial VALUES
                ('optuna_unit', 'temporal_unit', 'follow_net_return_60d',
                 3, 1.0, 1, 0, '["signal_b"]', '[]', '{}', '{}', '2026-05-07T00:00:03')
            """
        )
        calls = []

        def fake_mtm(_conn, *, candidate_run_id: str, run_id: str, **kwargs):
            calls.append(candidate_run_id)
            return {
                "run_id": run_id,
                "validation_status": "pass",
                "promotion_status": "research_only",
                "production_eligible": False,
                "blockers": [],
                "signal_count": 100,
                "repeated_signal_suppressed_count": 10,
                "position_count": 60,
                "total_return": 0.20,
                "annualized_return": 0.08,
                "max_drawdown": -0.10,
                "sharpe": 0.60,
                "avg_active_positions": 300.0,
                "position_hit_rate": 0.55,
                "missing_entry_price_count": 0,
                "missing_exit_price_count": 0,
                "missing_path_price_count": 0,
                "non_tdxhub_kline_count": 0,
            }

        monkeypatch.setattr(subject, "validate_synergy_policy_mark_to_market", fake_mtm)

        result = subject.rerank_optuna_synergy_mtm(
            conn,
            optuna_run_id="optuna_unit",
            run_id="mtm_rerank_dedupe_unit",
            max_trials=3,
            progress=False,
        )

        row_count = conn.execute(
            "SELECT COUNT(*) AS n FROM mart_synergy_policy_mtm_rerank WHERE run_id = 'mtm_rerank_dedupe_unit'"
        ).fetchone()["n"]
        summary = conn.execute(
            "SELECT config_json FROM mart_synergy_policy_mtm_rerank_summary WHERE run_id = 'mtm_rerank_dedupe_unit'"
        ).fetchone()

        assert result["evaluated_trials"] == 2
        assert len(calls) == 2
        assert row_count == 2
        assert json.loads(summary["config_json"])["input_trial_count"] == 3
        assert json.loads(summary["config_json"])["dedupe_candidates"] is True
