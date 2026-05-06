from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_temporal_synergy_research as subject


pytestmark = pytest.mark.pipeline


def test_temporal_research_panel_filters_future_source_dates_and_scores_relevance():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_temporal_panel (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                source_available_date TEXT,
                good_feature DOUBLE,
                inverse_feature DOUBLE,
                forward_ret_20d DOUBLE
            )
            """
        )
        rows = []
        for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
            for idx in range(12):
                label = idx / 100.0
                rows.append(
                    (
                        "set_a",
                        f"000{idx:03d}",
                        day,
                        day,
                        float(idx),
                        float(-idx),
                        label,
                    )
                )
        rows.append(
            (
                "set_a",
                "999999",
                "2026-01-02",
                "2026-01-30",
                999.0,
                999.0,
                -9.0,
            )
        )
        conn.executemany("INSERT INTO fact_temporal_panel VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

        result = subject.build_temporal_synergy_research(
            conn,
            run_id="temporal_unit",
            panel_table="fact_temporal_panel",
            feature_set_id="set_a",
            features=["good_feature", "inverse_feature"],
            labels=["forward_ret_20d"],
            source_available_date_column="source_available_date",
            min_daily_count=6,
            bucket_count=4,
            folds=3,
            top_pair_features=2,
            max_pairs=1,
            min_pair_valid_rows=6,
            min_joint_obs=1,
        )
        quality = conn.execute(
            "SELECT * FROM mart_temporal_research_panel_quality WHERE run_id = 'temporal_unit'"
        ).fetchone()
        relevance = {
            row["feature_name"]: row
            for row in conn.execute(
                """
                SELECT feature_name, rank_ic, directional_spread, daily_count
                  FROM mart_feature_temporal_relevance
                 WHERE run_id = 'temporal_unit'
                """
            ).fetchall()
        }
        buckets = conn.execute(
            """
            SELECT bucket_index, avg_label
              FROM mart_feature_bucket_effect
             WHERE run_id = 'temporal_unit'
               AND feature_name = 'good_feature'
             ORDER BY bucket_index
            """
        ).fetchall()
        stability_count = conn.execute(
            "SELECT COUNT(*) AS n FROM mart_feature_relevance_stability WHERE run_id = 'temporal_unit'"
        ).fetchone()["n"]
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'temporal_unit'"
        ).fetchone()

        assert result["panel_rows"] == 36
        assert result["dropped_future_source_rows"] == 1
        assert quality["source_date_filter_applied"] is True
        assert json.loads(quality["features_json"]) == ["good_feature", "inverse_feature"]
        assert relevance["good_feature"]["rank_ic"] == pytest.approx(1.0)
        assert relevance["inverse_feature"]["rank_ic"] == pytest.approx(-1.0)
        assert relevance["good_feature"]["directional_spread"] > 0
        assert relevance["inverse_feature"]["directional_spread"] > 0
        assert buckets[-1]["avg_label"] > buckets[0]["avg_label"]
        assert stability_count == 6
        assert json.loads(manifest["perf_summary_json"])["dropped_future_source_rows"] == 1
    finally:
        conn.close()


def test_temporal_synergy_can_select_auxiliary_feature_role_for_research():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                shareholder_plan_increase_count_180d INTEGER,
                shareholder_plan_increase_amount_max_180d DOUBLE,
                forward_ret_20d DOUBLE
            )
            """
        )
        rows = []
        for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
            for idx in range(12):
                aux = 1 if idx >= 6 else 0
                rows.append(
                    (
                        f"000{idx:03d}",
                        day,
                        float(idx),
                        aux,
                        float(aux * 1000),
                        float(idx) / 100.0,
                    )
                )
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?)", rows)

        result = subject.build_temporal_synergy_research(
            conn,
            run_id="temporal_aux_role_unit",
            panel_table="fact_feature_panel",
            feature_roles=["capital_attention_auxiliary"],
            labels=["forward_ret_20d"],
            min_daily_count=6,
            bucket_count=4,
            top_pair_features=2,
            max_pairs=1,
            min_pair_valid_rows=6,
            min_joint_obs=1,
        )
        quality = conn.execute(
            "SELECT features_json FROM mart_temporal_research_panel_quality WHERE run_id = 'temporal_aux_role_unit'"
        ).fetchone()
        features = json.loads(quality["features_json"])

        assert result["feature_count"] == 2
        assert features == [
            "shareholder_plan_increase_count_180d",
            "shareholder_plan_increase_amount_max_180d",
        ]
    finally:
        conn.close()


def test_temporal_pair_synergy_detects_joint_effect_exceeding_standalone():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_temporal_panel (
                stock_code TEXT,
                date TEXT,
                signal_a DOUBLE,
                signal_b DOUBLE,
                noise_feature DOUBLE,
                forward_ret_20d DOUBLE
            )
            """
        )
        rows = []
        for day_idx, day in enumerate(("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")):
            for idx in range(40):
                a_group = 1 if idx % 4 in (2, 3) else 0
                b_group = 1 if idx % 4 in (1, 3) else 0
                signal_a = 10.0 * a_group + idx / 1000.0
                signal_b = 10.0 * b_group + idx / 1000.0
                if a_group and b_group:
                    label = 0.50
                elif a_group or b_group:
                    label = 0.20
                else:
                    label = 0.00
                rows.append(
                    (
                        f"{day_idx:02d}{idx:04d}",
                        day,
                        signal_a,
                        signal_b,
                        float((idx * 17) % 11),
                        label,
                    )
                )
        conn.executemany("INSERT INTO fact_temporal_panel VALUES (?, ?, ?, ?, ?, ?)", rows)

        result = subject.build_temporal_synergy_research(
            conn,
            run_id="temporal_pair_unit",
            panel_table="fact_temporal_panel",
            features=["signal_a", "signal_b", "noise_feature"],
            labels=["forward_ret_20d"],
            min_daily_count=20,
            bucket_count=4,
            folds=2,
            top_pair_features=3,
            max_pairs=3,
            min_pair_valid_rows=40,
            min_joint_obs=4,
            active_quantile=0.75,
            interaction_uplift_threshold=0.05,
        )
        pair = conn.execute(
            """
            SELECT feature_a, feature_b, joint_uplift, joint_obs_count,
                   feature_a_active_label_mean, feature_b_active_label_mean,
                   joint_active_label_mean
              FROM mart_feature_pair_synergy
             WHERE run_id = 'temporal_pair_unit'
               AND ((feature_a = 'signal_a' AND feature_b = 'signal_b')
                    OR (feature_a = 'signal_b' AND feature_b = 'signal_a'))
            """
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT selected, selection_reason
              FROM mart_feature_interaction_candidate
             WHERE run_id = 'temporal_pair_unit'
               AND ((feature_a = 'signal_a' AND feature_b = 'signal_b')
                    OR (feature_a = 'signal_b' AND feature_b = 'signal_a'))
            """
        ).fetchone()

        assert result["selected_interaction_rows"] >= 1
        assert pair["joint_obs_count"] >= 4
        assert pair["joint_active_label_mean"] > pair["feature_a_active_label_mean"]
        assert pair["joint_active_label_mean"] > pair["feature_b_active_label_mean"]
        assert pair["joint_uplift"] > 0.05
        assert candidate["selected"] is True
        assert candidate["selection_reason"] == "joint_effect_exceeds_standalone"
    finally:
        conn.close()


def test_temporal_conditional_synergy_detects_response_that_only_works_under_condition():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_temporal_panel (
                stock_code TEXT,
                date TEXT,
                condition_feature DOUBLE,
                response_feature DOUBLE,
                noise_feature DOUBLE,
                forward_ret_20d DOUBLE
            )
            """
        )
        rows = []
        for day_idx, day in enumerate(("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")):
            for idx in range(80):
                condition_active = idx % 4 in (2, 3)
                response_active = idx % 4 in (1, 3)
                condition_feature = 10.0 if condition_active else 0.0
                response_feature = 10.0 if response_active else 0.0
                if condition_active and response_active:
                    label = 0.40
                elif condition_active and not response_active:
                    label = 0.00
                elif response_active:
                    label = 0.05
                else:
                    label = 0.00
                rows.append(
                    (
                        f"{day_idx:02d}{idx:04d}",
                        day,
                        condition_feature + idx / 10000.0,
                        response_feature + idx / 10000.0,
                        float((idx * 11) % 7),
                        label,
                    )
                )
        conn.executemany("INSERT INTO fact_temporal_panel VALUES (?, ?, ?, ?, ?, ?)", rows)

        result = subject.build_temporal_synergy_research(
            conn,
            run_id="temporal_conditional_unit",
            panel_table="fact_temporal_panel",
            features=["condition_feature", "response_feature", "noise_feature"],
            labels=["forward_ret_20d"],
            min_daily_count=20,
            bucket_count=4,
            top_pair_features=3,
            max_pairs=3,
            max_conditional_pairs=6,
            min_pair_valid_rows=40,
            min_joint_obs=4,
            active_quantile=0.75,
            conditional_uplift_threshold=0.05,
        )
        row = conn.execute(
            """
            SELECT condition_feature, response_feature,
                   conditional_response_uplift, response_uplift,
                   incremental_uplift, conditional_response_obs_count,
                   selected, selection_reason
              FROM mart_feature_conditional_synergy
             WHERE run_id = 'temporal_conditional_unit'
               AND condition_feature = 'condition_feature'
               AND response_feature = 'response_feature'
            """
        ).fetchone()

        assert result["selected_conditional_rows"] >= 1
        assert row["conditional_response_obs_count"] >= 4
        assert row["conditional_response_uplift"] > row["response_uplift"]
        assert row["incremental_uplift"] > 0.05
        assert row["selected"] is True
        assert row["selection_reason"] == "conditional_response_exceeds_unconditional"
    finally:
        conn.close()
