from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_temporal_redundancy_clusters as subject


pytestmark = pytest.mark.pipeline


def test_temporal_redundancy_clusters_group_correlated_features_and_choose_representative():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_temporal_research_panel_quality (
                run_id TEXT,
                features_json TEXT,
                built_at TEXT
            );
            INSERT INTO mart_temporal_research_panel_quality VALUES
                ('temporal_unit', '["signal_a","signal_b","signal_c"]', '2026-05-06T08:00:00');

            CREATE TABLE mart_temporal_research_panel (
                run_id TEXT,
                stock_code TEXT,
                signal_date TEXT,
                signal_a DOUBLE,
                signal_b DOUBLE,
                signal_c DOUBLE
            );

            CREATE TABLE mart_feature_temporal_relevance (
                run_id TEXT,
                label_name TEXT,
                feature_name TEXT,
                rank_ic DOUBLE,
                directional_spread DOUBLE,
                stability_score DOUBLE
            );
            INSERT INTO mart_feature_temporal_relevance VALUES
                ('temporal_unit', 'forward_ret_20d', 'signal_a', 0.10, 0.03, 0.80),
                ('temporal_unit', 'forward_ret_20d', 'signal_b', 0.05, 0.01, 0.40),
                ('temporal_unit', 'forward_ret_20d', 'signal_c', 0.04, 0.01, 0.30);
            """
        )
        rows = []
        for idx in range(40):
            signal_a = float(idx)
            signal_b = signal_a * 2.0 + 0.001
            signal_c = float(idx % 2)
            rows.append(("temporal_unit", f"{idx:06d}", "2026-01-01", signal_a, signal_b, signal_c))
        conn.executemany("INSERT INTO mart_temporal_research_panel VALUES (?, ?, ?, ?, ?, ?)", rows)

        result = subject.build_temporal_redundancy_clusters(
            conn,
            source_run_id="temporal_unit",
            run_id="redundancy_unit",
            corr_threshold=0.95,
        )

        cluster_rows = conn.execute(
            """
            SELECT feature_name, representative_feature, cluster_size, redundancy_status
              FROM mart_feature_cluster_redundancy
             WHERE run_id = 'redundancy_unit'
             ORDER BY cluster_id, feature_name
            """
        ).fetchall()
        pair = conn.execute(
            """
            SELECT abs_corr, redundant
              FROM mart_feature_redundancy_pair
             WHERE run_id = 'redundancy_unit'
               AND feature_a = 'signal_a'
               AND feature_b = 'signal_b'
            """
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'redundancy_unit'"
        ).fetchone()

        by_feature = {row["feature_name"]: row for row in cluster_rows}
        assert result["feature_count"] == 3
        assert result["pair_count"] == 3
        assert result["redundant_feature_count"] == 1
        assert pair["abs_corr"] == pytest.approx(1.0)
        assert pair["redundant"] is True
        assert by_feature["signal_a"]["representative_feature"] == "signal_a"
        assert by_feature["signal_a"]["redundancy_status"] == "representative"
        assert by_feature["signal_b"]["representative_feature"] == "signal_a"
        assert by_feature["signal_b"]["redundancy_status"] == "redundant"
        assert by_feature["signal_c"]["cluster_size"] == 1
        assert json.loads(manifest["perf_summary_json"])["redundant_feature_count"] == 1
