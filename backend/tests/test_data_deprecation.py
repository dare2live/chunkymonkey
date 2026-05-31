import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.data_deprecation import record_data_deprecations


def test_record_data_deprecations_updates_metadata_without_dropping_tables():
    conn = duck_mem()
    try:
        retired_table = "market" + "_raw" + "_holdings"
        conn.execute(
            """
            CREATE TABLE dim_data_asset (
                table_name TEXT PRIMARY KEY,
                deprecation_status TEXT DEFAULT 'active',
                deprecated_at TEXT,
                deprecated_reason TEXT,
                replacement_table TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO dim_data_asset (table_name, deprecation_status) VALUES (?, 'active')",
            (retired_table,),
        )

        result = record_data_deprecations(conn)
        row = conn.execute(
            "SELECT deprecation_status, replacement_table FROM dim_data_asset WHERE table_name = ?",
            (retired_table,),
        ).fetchone()
        record_count = conn.execute(
            "SELECT COUNT(*) FROM mart_data_deprecation_record WHERE table_name = ?",
            (retired_table,),
        ).fetchone()[0]

        assert result["deprecated"][0]["table_name"] == retired_table
        assert row["deprecation_status"] == "deprecated"
        assert row["replacement_table"] == "fact_top10_holder_period"
        assert record_count == 1
    finally:
        conn.close()
