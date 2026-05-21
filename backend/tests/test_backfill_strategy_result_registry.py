from __future__ import annotations

import json

import duckdb

from scripts.backfill_strategy_result_registry import (
    LAMBDAMART_COMPARE_TABLE,
    PAPER_SIM_KPI_TABLE,
    REGISTRY_TABLE,
    backfill,
    collect_registry_rows,
    ensure_registry_table,
    table_exists,
)
from services.duck_adapter import connect as duck_connect


def _seed_source_tables(conn) -> None:
    ddl = f"""
        CREATE TABLE {PAPER_SIM_KPI_TABLE} (
            sim_run_id TEXT PRIMARY KEY,
            variant TEXT,
            period_start TEXT,
            period_end TEXT,
            n_days INTEGER,
            annual_return DOUBLE,
            max_dd DOUBLE,
            sharpe DOUBLE,
            monthly_win_rate DOUBLE,
            annual_turnover DOUBLE,
            all_kpi_pass BOOLEAN,
            config_snapshot TEXT,
            sim_config_hash TEXT,
            parent_sim_run_id TEXT,
            param_diff_json TEXT,
            lineage_url TEXT,
            built_at TIMESTAMP
        );
        CREATE TABLE {LAMBDAMART_COMPARE_TABLE} (
            comparison_id TEXT,
            model_label TEXT,
            model_id TEXT,
            prediction_table TEXT,
            sim_run_id TEXT,
            period_start TEXT,
            period_end TEXT,
            rank_ic DOUBLE,
            rank_ic_n_dates INTEGER,
            sharpe DOUBLE,
            ann_ret DOUBLE,
            max_dd DOUBLE,
            monthly_win_rate DOUBLE,
            source_kpi_built_at TIMESTAMP,
            built_at TIMESTAMP,
            PRIMARY KEY (comparison_id, model_label)
        );
        """
    if hasattr(conn, "executescript"):
        conn.executescript(ddl)
    else:
        for statement in ddl.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
    conn.execute(
        f"""
        INSERT INTO {PAPER_SIM_KPI_TABLE}
        VALUES (
            'sim_a', 'swap_v1', '2023-01-03', '2026-05-19', 820,
            0.31, -0.12, 1.42, 0.61, 4.7, TRUE,
            '{{"portfolio": {{"max_positions": 5}}}}',
            'hash_a', 'sim_parent', '{{"portfolio": {{"max_positions": [4, 5]}}}}',
            'file:///tmp/sim_a.md', TIMESTAMP '2026-05-20 01:02:03'
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO {LAMBDAMART_COMPARE_TABLE}
        VALUES (
            'cmp_1', 'v4_baseline', 'baseline_model', 'mart_p0b_oos_predictions',
            'cmp_1_baseline', '2023-01-03', '2026-05-19', 0.021, 300,
            1.7, 0.52, -0.13, 0.66,
            TIMESTAMP '2026-05-20 01:02:03', TIMESTAMP '2026-05-20 02:03:04'
        );
        INSERT INTO {LAMBDAMART_COMPARE_TABLE}
        VALUES (
            'cmp_1', 'v6', 'lambdamart_v6', 'mart_p0b_oos_predictions_v6',
            'cmp_1_v6', '2023-01-03', '2026-05-19', 0.047, 300,
            1.5, 0.33, -0.11, 0.50,
            TIMESTAMP '2026-05-20 01:02:03', TIMESTAMP '2026-05-20 02:03:04'
        )
        """
    )
    conn.commit()


def test_ensure_registry_table_creates_required_columns():
    conn = duck_connect(":memory:")
    try:
        ensure_registry_table(conn)

        cols = {
            row["column_name"]
            for row in conn.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_name = ?
                """,
                [REGISTRY_TABLE],
            ).fetchall()
        }

        assert {
            "result_id",
            "source_table",
            "source_pk",
            "result_type",
            "model_id",
            "sim_run_id",
            "comparison_id",
            "variant",
            "model_label",
            "period_start",
            "period_end",
            "annual_return",
            "max_dd",
            "sharpe",
            "monthly_win_rate",
            "rank_ic",
            "turnover",
            "leakage_flag",
            "parent_result_id",
            "baseline_result_id",
            "sim_config_hash",
            "param_diff_json",
            "params_json",
            "lineage_url",
            "source_artifact_uri",
            "production_status",
            "decision",
            "decision_reason",
            "evidence_json",
            "built_at",
            "registered_at",
        }.issubset(cols)
    finally:
        conn.close()


def test_backfill_supports_native_duckdb_tuple_rows():
    conn = duckdb.connect(":memory:")
    try:
        _seed_source_tables(conn)

        result = backfill(conn, dry_run=False)
        count = conn.execute(f"SELECT COUNT(*) FROM {REGISTRY_TABLE}").fetchone()[0]

        assert result["upserted_rows"] == 3
        assert count == 3
    finally:
        conn.close()


def test_collect_registry_rows_maps_paper_sim_and_compare_sources():
    conn = duck_connect(":memory:")
    try:
        _seed_source_tables(conn)

        rows = collect_registry_rows(conn, registered_at="2026-05-20T00:00:00Z")

        assert len(rows) == 3
        paper = next(row for row in rows if row["source_table"] == PAPER_SIM_KPI_TABLE)
        compare = next(
            row
            for row in rows
            if row["source_table"] == LAMBDAMART_COMPARE_TABLE and row["model_label"] == "v6"
        )
        baseline = next(
            row
            for row in rows
            if row["source_table"] == LAMBDAMART_COMPARE_TABLE and row["model_label"] == "v4_baseline"
        )

        assert paper["source_pk"] == "sim_a"
        assert paper["result_type"] == "paper_sim_kpi"
        assert paper["variant"] == "swap_v1"
        assert paper["annual_return"] == 0.31
        assert paper["turnover"] == 4.7
        assert paper["production_status"] == "candidate_passed"
        assert paper["decision"] == "candidate_passed"
        assert paper["sim_config_hash"] == "hash_a"
        assert paper["param_diff_json"] == '{"portfolio": {"max_positions": [4, 5]}}'
        assert json.loads(paper["params_json"])["portfolio"]["max_positions"] == 5
        assert paper["lineage_url"] == "file:///tmp/sim_a.md"
        assert paper["parent_result_id"].startswith("strategy_result:")
        assert json.loads(paper["evidence_json"])["sim_config_hash"] == "hash_a"

        assert compare["source_pk"] == "cmp_1:v6"
        assert compare["result_type"] == "model_compare"
        assert compare["model_id"] == "lambdamart_v6"
        assert compare["sim_run_id"] == "cmp_1_v6"
        assert compare["comparison_id"] == "cmp_1"
        assert compare["model_label"] == "v6"
        assert compare["annual_return"] == 0.33
        assert compare["rank_ic"] == 0.047
        assert compare["production_status"] == "challenger_hold_reject"
        assert compare["decision"] == "hold_reject"
        assert compare["baseline_result_id"] == baseline["result_id"]
        assert compare["source_artifact_uri"] == "mart_p0b_oos_predictions_v6"
        assert json.loads(compare["params_json"])["prediction_table"] == "mart_p0b_oos_predictions_v6"
        assert "monthly_win_rate" in compare["decision_reason"]
        assert json.loads(compare["evidence_json"])["rank_ic_n_dates"] == 300
        assert baseline["production_status"] == "baseline_reference"
        assert baseline["decision"] == "reference"
    finally:
        conn.close()


def test_dry_run_outputs_candidates_without_creating_registry():
    conn = duck_connect(":memory:")
    try:
        _seed_source_tables(conn)

        result = backfill(conn, dry_run=True)

        assert result["mode"] == "dry-run"
        assert result["candidate_rows"] == 3
        assert result["upserted_rows"] == 0
        assert len(result["rows"]) == 3
        assert not table_exists(conn, REGISTRY_TABLE)
    finally:
        conn.close()


def test_apply_backfill_is_idempotent_upsert():
    conn = duck_connect(":memory:")
    try:
        _seed_source_tables(conn)

        first = backfill(conn, dry_run=False)
        first_registered_at = conn.execute(
            f"SELECT registered_at FROM {REGISTRY_TABLE} WHERE source_table = ? AND source_pk = ?",
            [PAPER_SIM_KPI_TABLE, "sim_a"],
        ).fetchone()["registered_at"]

        second = backfill(conn, dry_run=False)
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {REGISTRY_TABLE}").fetchone()["n"]
        second_registered_at = conn.execute(
            f"SELECT registered_at FROM {REGISTRY_TABLE} WHERE source_table = ? AND source_pk = ?",
            [PAPER_SIM_KPI_TABLE, "sim_a"],
        ).fetchone()["registered_at"]

        assert first["upserted_rows"] == 3
        assert second["upserted_rows"] == 3
        assert count == 3
        assert second_registered_at == first_registered_at
    finally:
        conn.close()
