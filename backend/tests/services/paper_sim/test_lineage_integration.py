from __future__ import annotations

from pathlib import Path

from services.duck_adapter import connect
from services.paper_sim import reporter
from services.paper_sim.config import load_config
from services.paper_sim.ddl import ensure_paper_sim_tables
from scripts import trace_lineage


def _seed_nav(conn, sim_run_id: str) -> None:
    conn.executemany(
        """
        INSERT INTO mart_paper_sim_nav
        (sim_run_id, date, total_value, cash, positions_value, n_positions, hs300_nav)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (sim_run_id, "2026-01-02", 1_000_000.0, 1_000_000.0, 0.0, 0, 1.00),
            (sim_run_id, "2026-01-05", 1_010_000.0, 1_010_000.0, 0.0, 0, 1.01),
            (sim_run_id, "2026-01-06", 1_020_000.0, 1_020_000.0, 0.0, 0, 1.01),
        ],
    )
    conn.commit()


def test_ddl_adds_nullable_lineage_url_column():
    conn = connect(":memory:")
    try:
        ensure_paper_sim_tables(conn)
        cols = {r[0] for r in conn.execute("DESCRIBE mart_paper_sim_kpi").fetchall()}
        assert "lineage_url" in cols
    finally:
        conn.close()


def test_lineage_url_migration_is_duplicate_safe():
    conn = connect(":memory:")
    try:
        ensure_paper_sim_tables(conn)
        ensure_paper_sim_tables(conn)
        cols = [r[0] for r in conn.execute("DESCRIBE mart_paper_sim_kpi").fetchall()]
        assert cols.count("lineage_url") == 1
    finally:
        conn.close()


def test_trace_lineage_output_file_writes_markdown(tmp_path: Path):
    sim_run_id = "lineage_cli_smoke"
    db_path = tmp_path / "trace.duckdb"
    output_file = tmp_path / "reports" / "lineage" / f"{sim_run_id}.md"
    lineage_url = f"file://{output_file.resolve()}"

    conn = connect(str(db_path))
    try:
        ensure_paper_sim_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_paper_sim_kpi
            (sim_run_id, variant, period_start, period_end, n_days, lineage_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [sim_run_id, "unit", "2026-01-02", "2026-01-06", 3, lineage_url],
        )
        conn.commit()
    finally:
        conn.close()

    rc = trace_lineage.main([
        "--sim-run-id", sim_run_id,
        "--db-path", str(db_path),
        "--output-file", str(output_file),
    ])

    assert rc == 0
    text = output_file.read_text(encoding="utf-8")
    assert "# Data Lineage Trace" in text
    assert f"- sim_run_id: {sim_run_id}" in text
    assert f"- lineage_url: {lineage_url}" in text


def test_write_kpi_summary_persists_lineage_url_and_file(tmp_path: Path, monkeypatch):
    sim_run_id = "paper_sim_lineage_unit"
    db_path = tmp_path / "paper_sim.duckdb"
    report_dir = tmp_path / "lineage"
    monkeypatch.setattr(reporter, "LINEAGE_REPORT_DIR", report_dir)

    conn = connect(str(db_path))
    try:
        ensure_paper_sim_tables(conn)
        _seed_nav(conn, sim_run_id)

        result = reporter.write_kpi_summary(
            conn,
            sim_run_id=sim_run_id,
            variant="unit",
            cfg=load_config(),
        )

        expected_file = (report_dir / f"{sim_run_id}.md").resolve()
        expected_url = f"file://{expected_file}"
        row = conn.execute(
            "SELECT lineage_url FROM mart_paper_sim_kpi WHERE sim_run_id = ?",
            [sim_run_id],
        ).fetchone()

        assert row["lineage_url"] == expected_url
        assert result["lineage_url"] == expected_url
        assert expected_file.exists()
        text = expected_file.read_text(encoding="utf-8")
        assert f"- sim_run_id: {sim_run_id}" in text
        assert f"- lineage_url: {expected_url}" in text
    finally:
        conn.close()


def test_existing_kpi_rows_can_keep_null_lineage_url():
    conn = connect(":memory:")
    try:
        ensure_paper_sim_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_paper_sim_kpi
            (sim_run_id, variant, period_start, period_end, n_days)
            VALUES (?, ?, ?, ?, ?)
            """,
            ["legacy_without_lineage", "legacy", "2026-01-02", "2026-01-06", 3],
        )
        row = conn.execute(
            "SELECT lineage_url FROM mart_paper_sim_kpi WHERE sim_run_id = ?",
            ["legacy_without_lineage"],
        ).fetchone()
        assert row["lineage_url"] is None
    finally:
        conn.close()
