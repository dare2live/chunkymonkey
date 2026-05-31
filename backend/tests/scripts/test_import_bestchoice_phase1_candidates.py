from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "import_bestchoice_phase1_candidates.py"
SPEC = importlib.util.spec_from_file_location("import_bestchoice_phase1_candidates", SCRIPT_PATH)
import_bestchoice_phase1_candidates = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = import_bestchoice_phase1_candidates
SPEC.loader.exec_module(import_bestchoice_phase1_candidates)


def _columns(conn) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name = ?
        """,
        [import_bestchoice_phase1_candidates.TARGET_TABLE],
    ).fetchall()
    return {row[0] for row in rows}


def test_ensure_target_schema_creates_pit_columns() -> None:
    conn = duckdb.connect(":memory:")
    try:
        import_bestchoice_phase1_candidates.ensure_target_schema(conn)
        cols = _columns(conn)
    finally:
        conn.close()

    assert "as_of_date" in cols
    assert "built_at" in cols


def test_ensure_target_schema_backfills_legacy_rows() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            f"""
            CREATE TABLE {import_bestchoice_phase1_candidates.TARGET_TABLE} (
                run_id VARCHAR,
                source_data_latest_date DATE,
                created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {import_bestchoice_phase1_candidates.TARGET_TABLE}
            VALUES ('legacy_run', DATE '2026-05-19', TIMESTAMP '2026-05-22 02:38:52')
            """
        )

        import_bestchoice_phase1_candidates.ensure_target_schema(conn)

        row = conn.execute(
            f"""
            SELECT as_of_date, built_at
            FROM {import_bestchoice_phase1_candidates.TARGET_TABLE}
            WHERE run_id = 'legacy_run'
            """
        ).fetchone()
    finally:
        conn.close()

    assert str(row[0]) == "2026-05-19"
    assert str(row[1]) == "2026-05-22 02:38:52"
