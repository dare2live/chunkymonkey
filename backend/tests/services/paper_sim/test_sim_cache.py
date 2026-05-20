from __future__ import annotations

from pathlib import Path

import duckdb

from services.paper_sim.ddl import apply_schema_migration
from services.paper_sim.sim_cache import check_cache, compute_config_hash, register_cache


def _create_kpi_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE mart_paper_sim_kpi (
            sim_run_id VARCHAR PRIMARY KEY,
            variant VARCHAR,
            period_start VARCHAR,
            period_end VARCHAR,
            n_days INTEGER,
            annual_return DOUBLE,
            sharpe DOUBLE,
            max_dd DOUBLE,
            monthly_win_rate DOUBLE,
            built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    apply_schema_migration(conn)


def test_compute_config_hash_deterministic(tmp_path: Path):
    cfg = tmp_path / "paper_sim.yaml"
    cfg.write_text("selection:\n  mode: ml_score\n", encoding="utf-8")

    h1 = compute_config_hash(cfg, "model_a", "2025-01-02", "2026-05-19", "p0a_v4")
    h2 = compute_config_hash(cfg, "model_a", "2025-01-02", "2026-05-19", "p0a_v4")
    h3 = compute_config_hash(cfg, "model_b", "2025-01-02", "2026-05-19", "p0a_v4")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 32


def test_check_cache_miss():
    conn = duckdb.connect(":memory:")
    try:
        _create_kpi_table(conn)
        assert check_cache(conn, "missing_hash") is None
    finally:
        conn.close()


def test_check_cache_hit():
    conn = duckdb.connect(":memory:")
    try:
        _create_kpi_table(conn)
        conn.execute(
            """
            INSERT INTO mart_paper_sim_kpi
            (sim_run_id, variant, period_start, period_end, n_days,
             annual_return, sharpe, max_dd, monthly_win_rate, sim_config_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "sim_1", "unit", "2025-01-02", "2026-05-19", 330,
                0.42, 1.25, -0.18, 0.61, "hash_1",
            ],
        )

        row = check_cache(conn, "hash_1")

        assert row is not None
        assert row["sim_run_id"] == "sim_1"
        assert row["annual_return"] == 0.42
        assert row["sim_config_hash"] == "hash_1"
    finally:
        conn.close()


def test_register_cache_idempotent():
    conn = duckdb.connect(":memory:")
    try:
        _create_kpi_table(conn)
        conn.execute(
            """
            INSERT INTO mart_paper_sim_kpi
            (sim_run_id, variant, period_start, period_end, n_days)
            VALUES (?, ?, ?, ?, ?)
            """,
            ["sim_1", "unit", "2025-01-02", "2026-05-19", 330],
        )

        register_cache(conn, "sim_1", "hash_1", "parent_1", {"x": [1, 2]})
        register_cache(conn, "sim_1", "hash_1", "parent_1", {"x": [1, 2]})

        rows = conn.execute(
            """
            SELECT sim_run_id, sim_config_hash, parent_sim_run_id, param_diff_json
            FROM mart_paper_sim_kpi
            """
        ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == "sim_1"
        assert rows[0][1] == "hash_1"
        assert rows[0][2] == "parent_1"
        assert rows[0][3] == '{"x":[1,2]}'
    finally:
        conn.close()


def test_apply_schema_migration_idempotent():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_paper_sim_kpi (
                sim_run_id VARCHAR PRIMARY KEY,
                variant VARCHAR,
                period_start VARCHAR,
                period_end VARCHAR,
                n_days INTEGER
            )
            """
        )

        apply_schema_migration(conn)
        apply_schema_migration(conn)

        cols = [row[0] for row in conn.execute("DESCRIBE mart_paper_sim_kpi").fetchall()]
        assert cols.count("sim_config_hash") == 1
        assert cols.count("parent_sim_run_id") == 1
        assert cols.count("param_diff_json") == 1
    finally:
        conn.close()
