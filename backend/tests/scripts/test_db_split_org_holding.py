"""org_holding DB split: copy filtered control rows, then drop from source."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from services.data_sources.accepted_schema import ACCEPTED_PARTITION_DDL, INGEST_BATCH_DDL
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE,
    COMPATIBILITY_TABLE,
    DATASET_ID,
    LANDING_TABLE,
)
from services.duck_adapter import connect as duck_connect

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "db_split_org_holding", REPO / "backend" / "scripts" / "db_split_org_holding.py"
)
split = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(split)

HOLDERS_DATASET = "tier0.disclosure.top10_float_holders_period"


def _batch_row(batch_id: str, dataset_id: str, partition: str) -> list:
    return [
        batch_id,
        dataset_id,
        "1",
        "ch",
        "cfg",
        "writer",
        partition,
        "miaoxiang",
        "ACCEPTED",
        "{}",
        "[]",
        1,
        1,
        0,
        1,
        1,
        "ph",
        "chash",
        "2026-08-01 00:00:00+00",
        "2026-08-01 00:00:00+00",
        "2026-08-01 00:00:00+00",
        "2026-08-01 00:00:00+00",
        "2026-08-01 00:00:00+00",
        None,
        None,
    ]


def _build_src(path: Path) -> None:
    c = duck_connect(str(path), read_only=False)
    try:
        c.execute(INGEST_BATCH_DDL)
        c.execute(ACCEPTED_PARTITION_DDL)
        c.execute(
            f"CREATE TABLE {LANDING_TABLE} (batch_id VARCHAR, row_ordinal INTEGER, payload VARCHAR)"
        )
        c.execute(
            f"CREATE TABLE {CANONICAL_TABLE} (stock_code VARCHAR, report_date VARCHAR)"
        )
        c.execute(
            f"CREATE TABLE {COMPATIBILITY_TABLE} (stock_code VARCHAR, report_date VARCHAR)"
        )
        c.execute(
            f"CREATE INDEX idx_org_land_batch ON {LANDING_TABLE}(batch_id)"
        )
        cols = (
            "batch_id, dataset_id, contract_version, contract_hash, config_hash, "
            "writer_id, partition_value, source_name, status, request_json, "
            "fragment_outcomes_json, expected_fragment_count, completed_fragment_count, "
            "failed_fragment_count, landing_row_count, canonical_row_count, payload_hash, "
            "canonical_hash, observed_at, available_at, landed_at, validated_at, "
            "accepted_at, rejection_code, rejection_detail"
        )
        c.execute(
            f"INSERT INTO ingest_batch ({cols}) VALUES ({','.join(['?'] * 25)})",
            _batch_row("org-b1", DATASET_ID, "20250430"),
        )
        c.execute(
            f"INSERT INTO ingest_batch ({cols}) VALUES ({','.join(['?'] * 25)})",
            _batch_row("hold-b1", HOLDERS_DATASET, "20250531"),
        )
        c.execute(
            """
            INSERT INTO accepted_partition (
              dataset_id, partition_value, batch_id, contract_version, contract_hash,
              config_hash, row_count, content_hash, observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, '1', 'ch', 'cfg', 2, 'h', '2026-08-01 00:00:00+00',
                      '2026-08-01 00:00:00+00', '2026-08-01 00:00:00+00')
            """,
            [DATASET_ID, "20250430", "org-b1"],
        )
        c.execute(
            """
            INSERT INTO accepted_partition (
              dataset_id, partition_value, batch_id, contract_version, contract_hash,
              config_hash, row_count, content_hash, observed_at, available_at, accepted_at
            ) VALUES (?, ?, ?, '1', 'ch', 'cfg', 1, 'h', '2026-08-01 00:00:00+00',
                      '2026-08-01 00:00:00+00', '2026-08-01 00:00:00+00')
            """,
            [HOLDERS_DATASET, "20250531", "hold-b1"],
        )
        c.execute(f"INSERT INTO {LANDING_TABLE} VALUES ('org-b1', 1, 'a'), ('org-b1', 2, 'b')")
        c.execute(f"INSERT INTO {CANONICAL_TABLE} VALUES ('600519', '20250331'), ('000001', '20250331')")
        c.execute(f"INSERT INTO {COMPATIBILITY_TABLE} VALUES ('600519', '20250331')")
        c.execute("CHECKPOINT")
    finally:
        c.close()


def test_copy_filters_control_rows_then_drop_leaves_holders(tmp_path):
    src = tmp_path / "smartmoney.duckdb"
    dest = tmp_path / "org_holding.duckdb"
    _build_src(src)

    counts = split.copy_org_holding(src=src, dest=dest, execute=True)
    assert counts[LANDING_TABLE] == 2
    assert counts[CANONICAL_TABLE] == 2
    assert counts[COMPATIBILITY_TABLE] == 1
    assert counts["ingest_batch"] == 1
    assert counts["accepted_partition"] == 1

    d = duck_connect(str(dest), read_only=True)
    try:
        leftover = [
            r[0]
            for r in d.execute("SELECT DISTINCT dataset_id FROM ingest_batch").fetchall()
        ]
        assert leftover == [DATASET_ID]
        idx = d.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE table_name=? AND sql IS NOT NULL",
            [LANDING_TABLE],
        ).fetchone()[0]
        assert idx >= 1
    finally:
        d.close()

    split.drop_source_org_holding(src=src, dest=dest)
    s = duck_connect(str(src), read_only=True)
    try:
        tabs = {
            r[0]
            for r in s.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' AND table_type='BASE TABLE'"
            ).fetchall()
        }
        assert LANDING_TABLE not in tabs
        assert CANONICAL_TABLE not in tabs
        assert COMPATIBILITY_TABLE not in tabs
        n_ib = s.execute("SELECT count(*) FROM ingest_batch").fetchone()[0]
        n_ap = s.execute("SELECT count(*) FROM accepted_partition").fetchone()[0]
        ds = s.execute("SELECT dataset_id FROM ingest_batch").fetchone()[0]
        assert n_ib == 1
        assert n_ap == 1
        assert ds == HOLDERS_DATASET
    finally:
        s.close()
