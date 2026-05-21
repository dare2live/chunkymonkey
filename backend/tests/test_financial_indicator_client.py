import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402
from services import financial_indicator_client as subject  # noqa: E402


def test_upsert_indicator_records_batches_rows_and_updates_state():
    conn = duck_mem()
    subject.ensure_tables(conn)
    try:
        inserted = subject._upsert_indicator_records(
            conn,
            "000001",
            [
                {
                    "report_date": "2025-12-31",
                    "roe_ak": 12.3,
                    "source": "unit",
                    "updated_at": "2026-01-01T00:00:00",
                },
                {
                    "report_date": "2024-12-31",
                    "roa_ak": 4.5,
                    "source": "unit",
                    "updated_at": "2026-01-01T00:00:00",
                },
            ],
            "2026-01-01T00:00:00",
        )

        assert inserted == 2
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, MAX(report_date) AS latest
            FROM fact_financial_indicator_ak
            WHERE stock_code = '000001'
            """
        ).fetchone()
        assert row["n"] == 2
        assert row["latest"] == "2025-12-31"
        state = conn.execute(
            """
            SELECT history_rows, last_report_date, status
            FROM financial_indicator_sync_state
            WHERE stock_code = '000001'
            """
        ).fetchone()
        assert state["history_rows"] == 2
        assert state["last_report_date"] == "2025-12-31"
        assert state["status"] == "partial"
    finally:
        conn.close()


def test_upsert_indicator_records_handles_empty_rows():
    conn = duck_mem()
    subject.ensure_tables(conn)
    try:
        inserted = subject._upsert_indicator_records(
            conn,
            "000002",
            [],
            "2026-01-01T00:00:00",
            error="empty frame",
        )

        assert inserted == 0
        state = conn.execute(
            """
            SELECT history_rows, last_report_date, status, error
            FROM financial_indicator_sync_state
            WHERE stock_code = '000002'
            """
        ).fetchone()
        assert state["history_rows"] == 0
        assert state["last_report_date"] is None
        assert state["status"] == "failed"
        assert state["error"] == "empty frame"
    finally:
        conn.close()
