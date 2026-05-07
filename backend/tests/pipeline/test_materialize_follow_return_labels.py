from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import pytest

from scripts.materialize_follow_return_labels import materialize_follow_return_labels
from services.pricing_policy import load_pricing_label_policy, record_pricing_label_data_readiness_gate


pytestmark = pytest.mark.pipeline


def _seed_price_rows(conn, *, days: int = 100) -> None:
    conn.execute("CREATE SCHEMA market")
    conn.execute(
        """
        CREATE TABLE market.price_kline_tdxhub (
            code TEXT,
            date TEXT,
            freq TEXT,
            adjust TEXT,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            factor DOUBLE
        )
        """
    )
    start = date(2026, 1, 1)
    rows = []
    for idx in range(days):
        trade_date = (start + timedelta(days=idx)).isoformat()
        close = 10.0 + idx
        rows.append(
            (
                "000001",
                trade_date,
                "daily",
                "qfq",
                close - 0.2,
                close + 0.3,
                close - 0.4,
                close,
                100.0,
                close * 100.0,
                1.0,
            )
        )
    conn.executemany("INSERT INTO market.price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.execute(
        """
        CREATE VIEW market.v_price_kline_qfq AS
        SELECT code, date, freq, adjust, open, high, low, close, volume, amount, factor
          FROM market.price_kline_tdxhub
        """
    )


def test_materialize_follow_return_labels_updates_panels_and_records_policy_build():
    conn = duckdb.connect(":memory:")
    try:
        _seed_price_rows(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                forward_ret_60d DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_60d DOUBLE
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-01', 0.0)")
        conn.execute("INSERT INTO fact_feature_panel_candidate VALUES ('candidate', '000001', '2026-01-01', 0.0)")

        result = materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel", "fact_feature_panel_candidate"],
            horizons=[5, 10, 20, 60, 90],
            run_id="follow_label_unit",
        )
        policy = load_pricing_label_policy()
        row = conn.execute(
            """
            SELECT follow_net_return_5d, follow_net_return_90d
              FROM fact_feature_panel
             WHERE stock_code = '000001'
               AND date = '2026-01-01'
            """
        ).fetchone()
        candidate = conn.execute(
            """
            SELECT follow_net_return_5d
              FROM fact_feature_panel_candidate
             WHERE feature_set_id = 'candidate'
            """
        ).fetchone()
        build = conn.execute(
            """
            SELECT policy_hash, labels_json, label_non_null_json
              FROM mart_follow_return_label_build
             WHERE run_id = 'follow_label_unit'
               AND feature_table = 'fact_feature_panel'
            """
        ).fetchone()
        quality = conn.execute(
            """
            SELECT row_count,
                   non_null_count,
                   null_count,
                   mature_null_count,
                   unclassified_null_count
              FROM mart_follow_return_label_quality
             WHERE run_id = 'follow_label_unit'
               AND feature_table = 'fact_feature_panel'
               AND label_name = 'follow_net_return_90d'
            """
        ).fetchone()

        assert result["policy_hash"] == policy.policy_hash()
        assert row[0] == pytest.approx(15.0 / 10.0 - 1 - 0.001)
        assert row[1] == pytest.approx(100.0 / 10.0 - 1 - 0.001)
        assert candidate[0] == pytest.approx(row[0])
        assert build[0] == policy.policy_hash()
        assert "follow_net_return_60d" in build[1]
        assert '"follow_net_return_5d": 1' in build[2]
        assert quality == (1, 1, 0, 0, 0)
    finally:
        conn.close()


def test_materialize_follow_return_labels_skips_when_policy_quality_is_current():
    conn = duckdb.connect(":memory:")
    try:
        _seed_price_rows(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-01')")
        first = materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5, 10],
            run_id="follow_label_skip_first",
        )
        second = materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5, 10],
            run_id="follow_label_skip_second",
        )
        second_build_rows = conn.execute(
            """
            SELECT COUNT(*)
              FROM mart_follow_return_label_build
             WHERE run_id = 'follow_label_skip_second'
            """
        ).fetchone()[0]
        manifest = conn.execute(
            """
            SELECT perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'follow_label_skip_second'
            """
        ).fetchone()[0]

        assert first["feature_tables"][0].get("skipped") is not True
        assert second["feature_tables"][0]["skipped"] is True
        assert second["feature_tables"][0]["latest_build_run_id"] == "follow_label_skip_first"
        assert second["stage_timing"]["build_daily_label_table_seconds"] == 0.0
        assert second["stage_timing"]["fact_feature_panel.set_seconds"] == 0.0
        assert second_build_rows == 0
        assert '"skipped": true' in manifest
    finally:
        conn.close()


def test_scoped_follow_return_labels_record_full_table_build_and_quality():
    conn = duckdb.connect(":memory:")
    try:
        _seed_price_rows(conn, days=100)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-01')")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-02')")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-03')")
        materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5, 10, 20, 60, 90],
            run_id="follow_label_scoped_full_first",
        )
        conn.execute(
            """
            UPDATE fact_feature_panel
               SET follow_net_return_5d = NULL,
                   follow_net_return_10d = NULL,
                   follow_net_return_20d = NULL,
                   follow_net_return_60d = NULL,
                   follow_net_return_90d = NULL
             WHERE date = '2026-01-02'
            """
        )

        result = materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5, 10, 20, 60, 90],
            start_date="2026-01-02",
            end_date="2026-01-02",
            run_id="follow_label_scoped_full_second",
        )
        build = conn.execute(
            """
            SELECT row_count, min_date, max_date, label_non_null_json
              FROM mart_follow_return_label_build
             WHERE run_id = 'follow_label_scoped_full_second'
               AND feature_table = 'fact_feature_panel'
            """
        ).fetchone()
        quality = conn.execute(
            """
            SELECT row_count,
                   non_null_count,
                   null_count,
                   mature_null_count,
                   unclassified_null_count
              FROM mart_follow_return_label_quality
             WHERE run_id = 'follow_label_scoped_full_second'
               AND feature_table = 'fact_feature_panel'
               AND label_name = 'follow_net_return_90d'
            """
        ).fetchone()
        gate = record_pricing_label_data_readiness_gate(
            conn,
            gate_run_id="follow_label_scoped_full_gate",
            feature_tables=["fact_feature_panel"],
        )

        table_summary = result["feature_tables"][0]
        assert table_summary["update_scope"]["row_count"] == 1
        assert table_summary["row_count"] == 3
        assert table_summary["profile_scope"] == "full_table"
        assert table_summary["quality_scope"] == "full_table"
        assert build[0:3] == (3, "2026-01-01", "2026-01-03")
        assert json.loads(build[3]) == {
            "follow_net_return_5d": 3,
            "follow_net_return_10d": 3,
            "follow_net_return_20d": 3,
            "follow_net_return_60d": 3,
            "follow_net_return_90d": 3,
        }
        assert quality == (3, 3, 0, 0, 0)
        assert gate["gate_status"] == "pass"
        assert gate["evidence"]["feature_tables"]["fact_feature_panel"]["follow_label_build"][
            "run_id"
        ] == "follow_label_scoped_full_second"
    finally:
        conn.close()


def test_pricing_data_readiness_uses_follow_label_build_policy_hash():
    conn = duckdb.connect(":memory:")
    try:
        _seed_price_rows(conn)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-01')")
        materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5, 10, 20, 60, 90],
            run_id="follow_label_gate_unit",
        )

        result = record_pricing_label_data_readiness_gate(
            conn,
            gate_run_id="follow_label_gate",
            feature_tables=["fact_feature_panel"],
        )

        assert "follow_return_labels_missing" not in result["blockers"]
        assert "fact_feature_panel_follow_label_build_missing" not in result["blockers"]
        assert result["evidence"]["feature_tables"]["fact_feature_panel"]["follow_label_build"][
            "policy_hash_match"
        ] is True
        quality = result["evidence"]["feature_tables"]["fact_feature_panel"]["follow_label_quality"]
        assert quality["quality_exists"] is True
        assert quality["unclassified_null_labels"] == []
    finally:
        conn.close()


def test_materialize_follow_return_labels_classifies_recent_rows_as_immature():
    conn = duckdb.connect(":memory:")
    try:
        _seed_price_rows(conn, days=12)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-01')")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-01-10')")

        materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5],
            run_id="follow_label_immature_unit",
        )

        quality = conn.execute(
            """
            SELECT row_count,
                   non_null_count,
                   null_count,
                   immature_null_count,
                   mature_null_count,
                   unclassified_null_count
              FROM mart_follow_return_label_quality
             WHERE run_id = 'follow_label_immature_unit'
               AND feature_table = 'fact_feature_panel'
               AND label_name = 'follow_net_return_5d'
            """
        ).fetchone()

        assert quality == (2, 1, 1, 1, 0, 0)
    finally:
        conn.close()


def test_materialize_follow_return_labels_classifies_missing_signal_kline_as_mature_defect():
    conn = duckdb.connect(":memory:")
    try:
        _seed_price_rows(conn, days=20)
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT
            )
            """
        )
        conn.execute("INSERT INTO fact_feature_panel VALUES ('999999', '2026-01-01')")

        materialize_follow_return_labels(
            conn,
            feature_tables=["fact_feature_panel"],
            horizons=[5],
            run_id="follow_label_missing_signal_unit",
        )

        quality = conn.execute(
            """
            SELECT row_count,
                   null_count,
                   immature_null_count,
                   mature_null_count,
                   missing_signal_kline_count,
                   unclassified_null_count
              FROM mart_follow_return_label_quality
             WHERE run_id = 'follow_label_missing_signal_unit'
               AND feature_table = 'fact_feature_panel'
               AND label_name = 'follow_net_return_5d'
            """
        ).fetchone()

        assert quality == (1, 1, 0, 1, 1, 0)
    finally:
        conn.close()
