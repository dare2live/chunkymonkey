from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_feature_association_duck as exact_subject
from scripts import build_feature_rank_matrix_duck as subject


pytestmark = pytest.mark.pipeline


def _seed_dense_panel(conn) -> None:
    conn.execute(
        """
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date TEXT,
            good_feature DOUBLE,
            inverse_feature DOUBLE,
            forward_ret_20d DOUBLE,
            follow_net_return_60d DOUBLE
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
                    float(-idx),
                    label,
                    label * 2.0,
                )
            )
    conn.executemany(
        "INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_rank_matrix_proxy_matches_exact_dense_association():
    conn = duck_mem()
    try:
        _seed_dense_panel(conn)
        exact_subject.build_feature_association_stats(
            conn,
            run_id="exact_dense",
            features=["good_feature", "inverse_feature"],
            label_name="forward_ret_20d",
            horizon_labels=["follow_net_return_60d"],
            min_daily_count=6,
            build_clusters=False,
        )

        result = subject.build_feature_rank_matrix_proxy(
            conn,
            run_id="rank_matrix_dense",
            exact_run_id="exact_dense",
            features=["good_feature", "inverse_feature"],
            label_name="forward_ret_20d",
            horizon_labels=["follow_net_return_60d"],
            min_daily_count=6,
        )
        rows = conn.execute(
            """
            SELECT label_name, feature_name, rank_ic, exact_rank_ic, abs_rank_ic_delta
              FROM mart_feature_rank_matrix_proxy_stat
             WHERE run_id = 'rank_matrix_dense'
             ORDER BY label_name, feature_name
            """
        ).fetchall()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'rank_matrix_dense'"
        ).fetchone()
        benchmark = conn.execute(
            """
            SELECT feature_count, label_count, proxy_rows, compared_pairs,
                   max_abs_rank_ic_delta, gate_status, gate_blockers_json,
                   config_json, stage_timings_json
              FROM mart_feature_rank_matrix_benchmark
             WHERE run_id = 'rank_matrix_dense'
            """
        ).fetchone()

        assert result["features"] == 2
        assert result["labels"] == ["forward_ret_20d", "follow_net_return_60d"]
        assert result["proxy_rows"] == 4
        assert result["compared_pairs"] == 4
        assert result["max_abs_rank_ic_delta"] == pytest.approx(0.0)
        assert sorted(row["rank_ic"] for row in rows) == pytest.approx([-1.0, -1.0, 1.0, 1.0])
        assert all(row["abs_rank_ic_delta"] == pytest.approx(0.0) for row in rows)
        assert benchmark["feature_count"] == 2
        assert benchmark["label_count"] == 2
        assert benchmark["proxy_rows"] == 4
        assert benchmark["compared_pairs"] == 4
        assert benchmark["max_abs_rank_ic_delta"] == pytest.approx(0.0)
        assert benchmark["gate_status"] == "pass"
        assert json.loads(benchmark["gate_blockers_json"]) == []
        assert json.loads(benchmark["config_json"])["proxy_association_mode"] == "per_feature_multi_label"
        assert json.loads(benchmark["config_json"])["rank_matrix_cache"]["status"] == "miss"
        assert "rank_matrix_build_s" in json.loads(benchmark["stage_timings_json"])
        manifest_perf = json.loads(manifest["perf_summary_json"])
        assert manifest_perf["proxy_association_mode"] == "per_feature_multi_label"
        assert manifest_perf["rank_matrix_cache"]["status"] == "miss"
        assert "stage_timings" in manifest_perf
    finally:
        conn.close()


def test_rank_matrix_proxy_reuses_persistent_rank_cache():
    conn = duck_mem()
    try:
        _seed_dense_panel(conn)
        exact_subject.build_feature_association_stats(
            conn,
            run_id="exact_dense_cache",
            features=["good_feature", "inverse_feature"],
            label_name="forward_ret_20d",
            horizon_labels=["follow_net_return_60d"],
            min_daily_count=6,
            build_clusters=False,
        )

        first = subject.build_feature_rank_matrix_proxy(
            conn,
            run_id="rank_matrix_cache_first",
            exact_run_id="exact_dense_cache",
            features=["good_feature", "inverse_feature"],
            label_name="forward_ret_20d",
            horizon_labels=["follow_net_return_60d"],
            min_daily_count=6,
        )
        second = subject.build_feature_rank_matrix_proxy(
            conn,
            run_id="rank_matrix_cache_second",
            exact_run_id="exact_dense_cache",
            features=["good_feature", "inverse_feature"],
            label_name="forward_ret_20d",
            horizon_labels=["follow_net_return_60d"],
            min_daily_count=6,
        )
        cache_key = first["rank_matrix_cache"]["cache_key"]
        table_name = first["rank_matrix_cache"]["table_name"]
        manifest = conn.execute(
            """
            SELECT cache_key, table_name, hit_count, row_count
              FROM mart_feature_rank_matrix_cache_manifest
             WHERE cache_key = ?
            """,
            [cache_key],
        ).fetchone()
        cached_rows = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

        assert first["rank_matrix_cache"]["status"] == "miss"
        assert second["rank_matrix_cache"]["status"] == "hit"
        assert second["rank_matrix_cache"]["cache_key"] == cache_key
        assert second["max_abs_rank_ic_delta"] == pytest.approx(0.0)
        assert manifest["hit_count"] == 1
        assert manifest["row_count"] == first["rank_matrix_rows"]
        assert cached_rows == first["rank_matrix_rows"]
    finally:
        conn.close()


def test_rank_matrix_cache_prunes_old_entries_by_direct_delete():
    conn = duck_mem()
    try:
        _seed_dense_panel(conn)

        first = subject.build_feature_rank_matrix_proxy(
            conn,
            run_id="rank_matrix_cache_prune_first",
            features=["good_feature"],
            label_name="forward_ret_20d",
            min_daily_count=6,
            rank_cache_max_entries=1,
        )
        second = subject.build_feature_rank_matrix_proxy(
            conn,
            run_id="rank_matrix_cache_prune_second",
            features=["inverse_feature"],
            label_name="forward_ret_20d",
            min_daily_count=6,
            rank_cache_max_entries=1,
        )
        manifest_count = conn.execute("SELECT COUNT(*) FROM mart_feature_rank_matrix_cache_manifest").fetchone()[0]

        assert manifest_count == 1
        assert not subject._cache_table_exists(conn, first["rank_matrix_cache"]["table_name"])
        assert subject._cache_table_exists(conn, second["rank_matrix_cache"]["table_name"])
    finally:
        conn.close()


def test_rank_matrix_proxy_records_without_exact_run():
    conn = duck_mem()
    try:
        _seed_dense_panel(conn)

        result = subject.build_feature_rank_matrix_proxy(
            conn,
            run_id="rank_matrix_no_exact",
            features=["good_feature"],
            label_name="forward_ret_20d",
            min_daily_count=6,
        )
        row = conn.execute(
            """
            SELECT rank_ic, exact_rank_ic, abs_rank_ic_delta
              FROM mart_feature_rank_matrix_proxy_stat
             WHERE run_id = 'rank_matrix_no_exact'
               AND feature_name = 'good_feature'
            """
        ).fetchone()

        assert result["proxy_rows"] == 1
        assert result["compared_pairs"] == 0
        assert result["proxy_gate_status"] == "blocked"
        assert "exact_run_id_missing" in result["proxy_gate_blockers"]
        assert row["rank_ic"] == pytest.approx(1.0)
        assert row["exact_rank_ic"] is None
        assert row["abs_rank_ic_delta"] is None
    finally:
        conn.close()
