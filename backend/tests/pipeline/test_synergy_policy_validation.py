from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import validate_synergy_policy_candidate as subject


pytestmark = pytest.mark.pipeline


def _seed_validation_inputs(conn, *, label_name: str = "forward_ret_60d") -> None:
    conn.executescript(
        f"""
        CREATE TABLE mart_synergy_policy_candidate (
            run_id TEXT,
            source_run_id TEXT,
            label_name TEXT,
            objective_score DOUBLE,
            selected_features_json TEXT,
            selected_interactions_json TEXT,
            gate_status TEXT,
            notes_json TEXT,
            built_at TEXT
        );
        INSERT INTO mart_synergy_policy_candidate VALUES
            ('candidate_unit', 'temporal_unit', '{label_name}', 1.5,
             '["signal_a","signal_b"]',
             '[{{"feature_a":"signal_a","feature_b":"signal_b"}}]',
             'research_only', '{{"promotion_gate_required":true}}',
             '2026-05-06T08:00:00');

        CREATE TABLE mart_feature_temporal_relevance (
            run_id TEXT,
            label_name TEXT,
            feature_name TEXT,
            rank_ic DOUBLE
        );
        INSERT INTO mart_feature_temporal_relevance VALUES
            ('temporal_unit', '{label_name}', 'signal_a', 0.9),
            ('temporal_unit', '{label_name}', 'signal_b', 0.8);

        CREATE TABLE mart_temporal_research_panel (
            run_id TEXT,
            stock_code TEXT,
            date TEXT,
            signal_a DOUBLE,
            signal_b DOUBLE,
            {label_name} DOUBLE
        );
        """
    )
    rows = []
    for day_idx in range(8):
        date = f"2026-01-{day_idx + 1:02d}"
        for stock_idx in range(30):
            signal = float(stock_idx)
            label = stock_idx / 1000.0 + day_idx / 10000.0
            rows.append(("temporal_unit", f"{stock_idx:06d}", date, signal, signal, label))
    conn.executemany(
        f"INSERT INTO mart_temporal_research_panel VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_validate_synergy_policy_candidate_records_walkforward_gate_and_evidence():
    with duck_mem() as conn:
        _seed_validation_inputs(conn, label_name="forward_ret_60d")

        result = subject.validate_synergy_policy_candidate(
            conn,
            candidate_run_id="candidate_unit",
            run_id="synergy_wf_unit",
            folds=4,
            top_quantile=0.20,
            min_fold_count=4,
            min_avg_rank_ic=0.50,
            max_std_rank_ic=0.20,
            min_top_obs_count=1,
        )

        gate = conn.execute(
            "SELECT * FROM mart_synergy_policy_gate WHERE run_id = 'synergy_wf_unit'"
        ).fetchone()
        folds = conn.execute(
            "SELECT COUNT(*) AS n FROM mart_synergy_policy_walkforward WHERE run_id = 'synergy_wf_unit'"
        ).fetchone()["n"]
        evidence = conn.execute(
            "SELECT gate_json, fold_metrics_json FROM mart_synergy_policy_evidence_bundle WHERE run_id = 'synergy_wf_unit'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT gate_result, perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'synergy_wf_unit'"
        ).fetchone()

        assert result["validation_status"] == "pass"
        assert result["promotion_status"] == "production_candidate"
        assert result["production_eligible"] is True
        assert result["avg_turnover"] > 0
        assert result["avg_cost_adjusted_top_excess_return"] > 0
        assert gate["candidate_horizon_days"] == 60
        assert gate["avg_rank_ic"] > 0.9
        assert gate["avg_turnover"] > 0
        assert gate["avg_cost_adjusted_top_excess_return"] > 0
        assert gate["transaction_cost_bps"] == pytest.approx(10.0)
        assert folds == 4
        assert json.loads(evidence["gate_json"])["production_eligible"] is True
        assert len(json.loads(evidence["fold_metrics_json"])) == 4
        assert manifest["gate_result"] == "pass"
        manifest_summary = json.loads(manifest["perf_summary_json"])
        pricing_policy = conn.execute("SELECT * FROM mart_pricing_label_policy").fetchone()
        assert manifest_summary["promotion_status"] == "production_candidate"
        assert manifest_summary["avg_turnover"] > 0
        assert manifest_summary["pricing_policy_id"] == "pricing_label_policy_vwap_follow_v1"
        assert pricing_policy["follow_entry_price_mode"] == "entry_day_vwap_qfq"


def test_validate_synergy_policy_keeps_non_baseline_horizon_research_only():
    with duck_mem() as conn:
        _seed_validation_inputs(conn, label_name="forward_ret_5d")

        result = subject.validate_synergy_policy_candidate(
            conn,
            candidate_run_id="candidate_unit",
            run_id="synergy_wf_5d",
            folds=4,
            top_quantile=0.20,
            min_fold_count=4,
            min_avg_rank_ic=0.50,
            max_std_rank_ic=0.20,
            min_top_obs_count=1,
            min_avg_cost_adjusted_top_excess_return=0.0,
        )
        gate = conn.execute(
            "SELECT validation_status, promotion_status, production_eligible, candidate_horizon_days FROM mart_synergy_policy_gate WHERE run_id = 'synergy_wf_5d'"
        ).fetchone()

        assert result["validation_status"] == "pass"
        assert result["promotion_status"] == "research_only"
        assert result["production_eligible"] is False
        assert gate["validation_status"] == "pass"
        assert gate["promotion_status"] == "research_only"
        assert gate["production_eligible"] is False
        assert gate["candidate_horizon_days"] == 5


def test_validate_synergy_policy_scores_conditional_interactions_as_condition_gated_terms():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_synergy_policy_candidate (
                run_id TEXT,
                source_run_id TEXT,
                label_name TEXT,
                objective_score DOUBLE,
                selected_features_json TEXT,
                selected_interactions_json TEXT,
                gate_status TEXT,
                notes_json TEXT,
                built_at TEXT
            );
            INSERT INTO mart_synergy_policy_candidate VALUES
                ('candidate_conditional', 'temporal_conditional', 'forward_ret_60d', 1.5,
                 '["condition_feature","response_feature"]',
                 '[{"interaction_type":"conditional","feature_a":"condition_feature","feature_b":"response_feature"}]',
                 'research_only', '{"promotion_gate_required":true}',
                 '2026-05-06T08:00:00');

            CREATE TABLE mart_feature_temporal_relevance (
                run_id TEXT,
                label_name TEXT,
                feature_name TEXT,
                rank_ic DOUBLE
            );
            INSERT INTO mart_feature_temporal_relevance VALUES
                ('temporal_conditional', 'forward_ret_60d', 'condition_feature', 0.9),
                ('temporal_conditional', 'forward_ret_60d', 'response_feature', 0.8);

            CREATE TABLE mart_temporal_research_panel (
                run_id TEXT,
                stock_code TEXT,
                date TEXT,
                condition_feature DOUBLE,
                response_feature DOUBLE,
                forward_ret_60d DOUBLE
            );
            """
        )
        rows = []
        for day_idx in range(4):
            date = f"2026-01-{day_idx + 1:02d}"
            response_values = [100.0, 0.0, 1.0, 2.0, 3.0]
            for stock_idx, response in enumerate(response_values):
                rows.append(
                    (
                        "temporal_conditional",
                        f"{stock_idx:06d}",
                        date,
                        float(stock_idx),
                        response,
                        stock_idx / 100.0,
                    )
                )
        conn.executemany("INSERT INTO mart_temporal_research_panel VALUES (?, ?, ?, ?, ?, ?)", rows)

        subject.validate_synergy_policy_candidate(
            conn,
            candidate_run_id="candidate_conditional",
            run_id="synergy_wf_conditional",
            folds=1,
            top_quantile=0.40,
            min_fold_count=1,
            min_avg_rank_ic=-1.0,
            max_std_rank_ic=1.0,
            min_top_obs_count=1,
            conditional_threshold=0.80,
        )

        low_condition_high_response = conn.execute(
            """
            SELECT policy_score
              FROM synergy_policy_scored
             WHERE date = '2026-01-01'
               AND stock_code = '000000'
            """
        ).fetchone()["policy_score"]
        high_condition_response = conn.execute(
            """
            SELECT policy_score
              FROM synergy_policy_scored
             WHERE date = '2026-01-01'
               AND stock_code = '000004'
            """
        ).fetchone()["policy_score"]
        evidence = conn.execute(
            """
            SELECT selected_interactions_json
              FROM mart_synergy_policy_evidence_bundle
             WHERE run_id = 'synergy_wf_conditional'
            """
        ).fetchone()

        assert low_condition_high_response == pytest.approx((0.0 + 1.0 + 0.5) / 3.0)
        assert high_condition_response == pytest.approx((1.0 + 0.75 + 0.75) / 3.0)
        assert json.loads(evidence["selected_interactions_json"])[0]["interaction_type"] == "conditional"
