from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_feature_association_duck as subject


pytestmark = pytest.mark.pipeline


def _seed_panel(conn) -> None:
    conn.execute(
        """
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date TEXT,
            good_feature DOUBLE,
            duplicate_good DOUBLE,
            inverse_feature DOUBLE,
            sparse_feature DOUBLE,
            constant_feature DOUBLE,
            regime_flag TEXT,
            forward_ret_5d DOUBLE,
            forward_ret_20d DOUBLE
        )
        """
    )
    rows = []
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        for idx in range(12):
            label = float(idx) / 100.0
            rows.append(
                (
                    f"000{idx:03d}",
                    day,
                    float(idx),
                    float(idx),
                    float(-idx),
                    float(idx) if idx >= 6 else None,
                    1.0,
                    "up",
                    label * 0.5,
                    label,
                )
            )
    conn.executemany(
        "INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_build_feature_association_stats_scores_features_and_clusters():
    conn = duck_mem()
    try:
        _seed_panel(conn)

        result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_unit",
            features=[
                "good_feature",
                "duplicate_good",
                "inverse_feature",
                "sparse_feature",
                "constant_feature",
                "regime_flag",
            ],
            horizon_labels=["forward_ret_5d", "forward_ret_20d"],
            min_daily_count=6,
            corr_threshold=0.99,
        )
        rows = conn.execute(
            """
            SELECT feature_name, rank_ic, coverage_pct, fold_count,
                   fold_same_sign_rate, long_short_spread,
                   horizon_sensitivity_json
              FROM mart_feature_association_stat
             WHERE run_id = 'assoc_unit'
             ORDER BY feature_name
            """
        ).fetchall()
        clusters = conn.execute(
            """
            SELECT feature_name, cluster_id, representative_feature, corr_to_representative
              FROM mart_feature_correlation_cluster
             WHERE run_id = 'assoc_unit'
            """
        ).fetchall()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'assoc_unit'"
        ).fetchone()

        by_feature = {row["feature_name"]: row for row in rows}
        by_cluster = {row["feature_name"]: row for row in clusters}

        assert result["features"] == 5  # text regime_flag is ignored.
        assert result["rows"] == 5
        assert result["corr_pairs"] == 10
        assert by_feature["good_feature"]["rank_ic"] == pytest.approx(1.0)
        assert by_feature["inverse_feature"]["rank_ic"] == pytest.approx(-1.0)
        assert by_feature["sparse_feature"]["coverage_pct"] == pytest.approx(50.0)
        assert by_feature["constant_feature"]["rank_ic"] is None
        assert by_feature["good_feature"]["fold_count"] == 3
        assert by_feature["good_feature"]["fold_same_sign_rate"] == pytest.approx(1.0)
        assert by_feature["good_feature"]["long_short_spread"] > 0
        sensitivity = json.loads(by_feature["good_feature"]["horizon_sensitivity_json"])
        assert sensitivity["forward_ret_5d"] == pytest.approx(1.0)
        assert by_cluster["good_feature"]["cluster_id"] == by_cluster["duplicate_good"]["cluster_id"]
        assert by_cluster["inverse_feature"]["cluster_id"] == by_cluster["duplicate_good"]["cluster_id"]
        assert abs(by_cluster["duplicate_good"]["corr_to_representative"]) == pytest.approx(1.0)
        assert abs(by_cluster["inverse_feature"]["corr_to_representative"]) == pytest.approx(1.0)
        perf_summary = json.loads(manifest["perf_summary_json"])
        assert perf_summary["features"] == 5
        assert set(perf_summary["stage_timings"]) >= {
            "schema_and_feature_selection_s",
            "prepare_base_table_s",
            "feature_stats_s",
            "correlation_clusters_s",
            "fold_associations_s",
            "total_before_manifest_s",
        }
        assert perf_summary["slowest_features"][0]["feature_name"] in by_feature
    finally:
        conn.close()


def test_default_feature_selection_uses_registry_and_numeric_columns_only():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                regime_flag TEXT,
                forward_ret_20d DOUBLE
            )
            """
        )
        rows = []
        for day in ("2026-01-01", "2026-01-02"):
            for idx in range(10):
                rows.append((f"000{idx:03d}", day, float(idx), "up", float(idx)))
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?)", rows)

        result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_registry_unit",
            min_daily_count=5,
        )
        stored_features = {
            row["feature_name"]
            for row in conn.execute(
                "SELECT feature_name FROM mart_feature_association_stat WHERE run_id = 'assoc_registry_unit'"
            ).fetchall()
        }

        assert result["features"] == 1
        assert stored_features == {"ret_20d"}
    finally:
        conn.close()


def test_feature_association_can_select_auxiliary_feature_role_for_research():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                shareholder_plan_increase_count_180d INTEGER,
                days_since_shareholder_plan_increase INTEGER,
                forward_ret_20d DOUBLE
            )
            """
        )
        rows = []
        for day in ("2026-01-01", "2026-01-02"):
            for idx in range(10):
                aux = 1 if idx >= 5 else 0
                rows.append((f"000{idx:03d}", day, float(idx), aux, idx if aux else -1, float(idx)))
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?)", rows)

        default_result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_default_inputs_unit",
            min_daily_count=5,
            build_clusters=False,
        )
        aux_result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_aux_role_unit",
            feature_roles=["capital_attention_auxiliary"],
            min_daily_count=5,
            build_clusters=False,
        )
        aux_features = {
            row["feature_name"]
            for row in conn.execute(
                "SELECT feature_name FROM mart_feature_association_stat WHERE run_id = 'assoc_aux_role_unit'"
            ).fetchall()
        }

        assert default_result["features"] == 1
        assert aux_result["features"] == 2
        assert aux_features == {
            "shareholder_plan_increase_count_180d",
            "days_since_shareholder_plan_increase",
        }
    finally:
        conn.close()


def test_feature_association_filters_candidate_panel_by_feature_set_id():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                candidate_good DOUBLE,
                candidate_inverse DOUBLE,
                forward_ret_20d DOUBLE,
                built_at TEXT
            )
            """
        )
        rows = []
        for feature_set_id, sign in (("set_a", 1.0), ("set_b", -1.0)):
            for day in ("2026-01-01", "2026-01-02"):
                for idx in range(10):
                    label = float(idx)
                    rows.append(
                        (
                            feature_set_id,
                            f"000{idx:03d}",
                            day,
                            sign * label,
                            -sign * label,
                            label,
                            "2026-01-03",
                        )
                    )
        conn.executemany(
            "INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        result = subject.build_feature_association_stats(
            conn,
            panel_table="fact_feature_panel_candidate",
            feature_set_id="set_a",
            run_id="assoc_candidate_set_a",
            min_daily_count=5,
            build_clusters=False,
        )
        by_feature = {
            row["feature_name"]: row
            for row in conn.execute(
                """
                SELECT feature_name, total_rows, rank_ic
                  FROM mart_feature_association_stat
                 WHERE run_id = 'assoc_candidate_set_a'
                """
            ).fetchall()
        }

        assert result["feature_set_id"] == "set_a"
        assert result["total_rows"] == 20
        assert set(by_feature) == {"candidate_good", "candidate_inverse"}
        assert by_feature["candidate_good"]["rank_ic"] == pytest.approx(1.0)
        assert by_feature["candidate_inverse"]["rank_ic"] == pytest.approx(-1.0)
    finally:
        conn.close()


def test_feature_association_can_skip_cluster_build_for_full_stat_runs():
    conn = duck_mem()
    try:
        _seed_panel(conn)

        result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_no_cluster_unit",
            features=["good_feature", "duplicate_good"],
            min_daily_count=6,
            build_clusters=False,
        )
        cluster_count = conn.execute(
            "SELECT COUNT(*) FROM mart_feature_correlation_cluster WHERE run_id = 'assoc_no_cluster_unit'"
        ).fetchone()[0]

        assert result["rows"] == 2
        assert result["cluster_rows"] == 0
        assert result["corr_pairs"] == 0
        assert result["fold_rows"] == 0
        assert cluster_count == 0
    finally:
        conn.close()


def test_feature_association_writes_holdout_fold_windows():
    conn = duck_mem()
    try:
        _seed_panel(conn)

        result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_fold_unit",
            features=["good_feature", "inverse_feature", "constant_feature"],
            min_daily_count=6,
            build_clusters=False,
            folds=3,
        )
        rows = conn.execute(
            """
            SELECT fold_id, train_start, train_end, holdout_start, holdout_end,
                   feature_name, rank_ic, daily_count
              FROM mart_feature_association_fold
             WHERE run_id = 'assoc_fold_unit'
             ORDER BY fold_id, feature_name
            """
        ).fetchall()
        by_key = {(row["fold_id"], row["feature_name"]): row for row in rows}

        assert result["folds"] == 3
        assert result["fold_rows"] == 9
        assert by_key[("fold_001", "good_feature")]["train_start"] is None
        assert by_key[("fold_001", "good_feature")]["holdout_start"] == "2026-01-01"
        assert by_key[("fold_002", "good_feature")]["train_end"] == "2026-01-01"
        assert by_key[("fold_003", "good_feature")]["holdout_end"] == "2026-01-03"
        assert by_key[("fold_001", "good_feature")]["rank_ic"] == pytest.approx(1.0)
        assert by_key[("fold_001", "inverse_feature")]["rank_ic"] == pytest.approx(-1.0)
        assert by_key[("fold_001", "constant_feature")]["rank_ic"] is None
        assert by_key[("fold_001", "good_feature")]["daily_count"] == 1
    finally:
        conn.close()


def test_feature_association_records_kline_source_distribution():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                forward_ret_20d DOUBLE,
                kline_source_name TEXT,
                kline_source_tier SMALLINT,
                kline_is_fallback BOOLEAN
            )
            """
        )
        rows = []
        for day in ("2026-01-01", "2026-01-02"):
            for idx in range(10):
                fallback = idx == 9
                rows.append(
                    (
                        f"000{idx:03d}",
                        day,
                        float(idx),
                        float(idx) / 100.0,
                        "akshare_multi_source" if fallback else "tdxhub",
                        3 if fallback else 1,
                        fallback,
                    )
                )
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

        result = subject.build_feature_association_stats(
            conn,
            run_id="assoc_source_unit",
            features=["ret_20d"],
            min_daily_count=5,
            build_clusters=False,
        )
        row = conn.execute(
            """
            SELECT source_fallback_pct, source_distribution_json
              FROM mart_feature_association_stat
             WHERE run_id = 'assoc_source_unit'
               AND feature_name = 'ret_20d'
            """
        ).fetchone()
        distribution = json.loads(row["source_distribution_json"])

        assert result["source_fallback_pct"] == pytest.approx(10.0)
        assert row["source_fallback_pct"] == pytest.approx(10.0)
        assert {item["source_tier"] for item in distribution} == {1, 3}
        assert any(item["is_fallback"] and item["rows"] == 2 for item in distribution)
    finally:
        conn.close()
