"""F6 org_holding pointer integrity: both directions + content_hash."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from services.data_sources.accepted_schema import ACCEPTED_PARTITION_DDL, ACCEPTED_TABLE
from services.data_sources.disclosure_event_partition import (
    partition_accepted_pointer_stats,
)
from services.data_sources.org_holding_acceptance import DOMAIN
from services.data_sources.org_holding_schema import CANONICAL_TABLE, DATASET_ID
from services.data_sources.security_day_partition import sha256_text, stable_json
from services.duck_adapter import connect
from services.org_holding_pointer_integrity import count_org_pointer_mismatches

REPO = Path(__file__).resolve().parents[3]


def _load_repair_module():
    path = REPO / "backend" / "scripts" / "repair_org_holding_accepted_pointers.py"
    spec = importlib.util.spec_from_file_location("repair_org_holding_pointers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn():
    database = connect(":memory:")
    database.execute(ACCEPTED_PARTITION_DDL)
    database.execute(
        f"""
        CREATE TABLE {CANONICAL_TABLE} (
            available_date VARCHAR NOT NULL,
            report_date VARCHAR,
            stock_code VARCHAR,
            holder_code VARCHAR,
            fund_derivecode VARCHAR,
            holder_name VARCHAR,
            org_type_name VARCHAR,
            total_shares DOUBLE,
            free_shares_ratio DOUBLE
        )
        """
    )
    yield database
    database.close()


def _insert_pointer(conn, partition: str, *, row_count: int, content_hash: str) -> None:
    conn.execute(
        f"""
        INSERT INTO {ACCEPTED_TABLE} (
            dataset_id, partition_value, batch_id, contract_version, contract_hash,
            config_hash, row_count, content_hash, observed_at, available_at, accepted_at
        ) VALUES (?, ?, ?, '1', 'ch', 'cfg', ?, ?,
                  TIMESTAMPTZ '2026-01-01', TIMESTAMPTZ '2026-01-01', TIMESTAMPTZ '2026-01-01')
        """,
        [DATASET_ID, partition, f"batch-{partition}", row_count, content_hash],
    )


def _insert_canon(conn, partition: str, n: int) -> None:
    for i in range(n):
        conn.execute(
            f"""
            INSERT INTO {CANONICAL_TABLE}
            (available_date, report_date, stock_code, holder_code, fund_derivecode,
             holder_name, org_type_name, total_shares, free_shares_ratio)
            VALUES (?, '20260331', ?, 'h1', '', '机构', '基金', 100.0, 0.00000052)
            """,
            [partition, f"s{i:04d}"],
        )


def test_pointer_missing_and_canonical_missing(conn) -> None:
    _insert_pointer(conn, "20260430", row_count=1, content_hash="orphan")
    _insert_canon(conn, "20250131", 2)
    mismatches = {r["partition_value"]: r for r in count_org_pointer_mismatches(conn)}
    assert mismatches["20260430"]["reason"] == "canonical_missing"
    assert mismatches["20250131"]["reason"] == "pointer_missing"


def test_content_hash_mismatch_when_counts_agree(conn) -> None:
    _insert_pointer(conn, "20260430", row_count=2, content_hash="stale-hash")
    _insert_canon(conn, "20260430", 2)
    with patch(
        "services.org_holding_pointer_integrity.partition_accepted_pointer_stats",
        return_value=(2, "fresh-hash"),
    ):
        mismatches = count_org_pointer_mismatches(conn)
    assert len(mismatches) == 1
    assert mismatches[0]["reason"] == "content_hash_mismatch"
    assert mismatches[0]["canonical_content_hash"] == "fresh-hash"


def test_row_count_mismatch(conn) -> None:
    _insert_pointer(conn, "20260430", row_count=1, content_hash="h")
    _insert_canon(conn, "20260430", 3)
    with patch(
        "services.org_holding_pointer_integrity.partition_accepted_pointer_stats",
        return_value=(3, "canon-h"),
    ):
        mismatches = count_org_pointer_mismatches(conn)
    assert len(mismatches) == 1
    assert mismatches[0]["reason"] == "row_count_mismatch"


def test_partition_hash_matches_stable_json_contract(conn) -> None:
    _insert_canon(conn, "20260430", 2)
    rows = conn.execute(
        f"""
        SELECT {", ".join(DOMAIN.content_hash_fields)}
          FROM {CANONICAL_TABLE}
         WHERE available_date = ?
         ORDER BY report_date, stock_code, holder_code, fund_derivecode
        """,
        ["20260430"],
    ).fetchall()
    payload = [
        dict(zip(DOMAIN.content_hash_fields, tuple(row), strict=True))
        for row in rows
    ]
    n, content_hash = partition_accepted_pointer_stats(
        conn, DOMAIN, "20260430"
    )
    assert n == 2
    assert content_hash == sha256_text(stable_json(payload))


def test_repair_connection_is_atomic_and_post_verifies(conn, monkeypatch) -> None:
    module = _load_repair_module()
    _insert_pointer(conn, "20260430", row_count=1, content_hash="stale")
    mismatch = {
        "partition_value": "20260430",
        "pointer_row_count": 1,
        "canonical_row_count": 2,
        "pointer_content_hash": "stale",
        "canonical_content_hash": "fresh",
        "reason": "row_count_mismatch",
    }
    calls = iter([[mismatch], []])
    monkeypatch.setattr(module, "_count_mismatches", lambda _conn: next(calls))
    monkeypatch.setattr(
        module, "partition_accepted_pointer_stats", lambda *_args: (2, "fresh")
    )

    result = module.repair_connection(conn, dry_run=False)
    assert result["repaired"] == 1
    assert tuple(
        conn.execute(
            f"SELECT row_count, content_hash FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [DATASET_ID],
        ).fetchone()
    ) == (2, "fresh")


def test_repair_connection_rolls_back_and_rejects_missing_sides(
    conn, monkeypatch
) -> None:
    module = _load_repair_module()
    _insert_pointer(conn, "20260430", row_count=1, content_hash="stale")
    mismatch = {
        "partition_value": "20260430",
        "pointer_row_count": 1,
        "canonical_row_count": 2,
        "pointer_content_hash": "stale",
        "canonical_content_hash": "fresh",
        "reason": "row_count_mismatch",
    }
    monkeypatch.setattr(module, "_count_mismatches", lambda _conn: [mismatch])
    monkeypatch.setattr(
        module, "partition_accepted_pointer_stats", lambda *_args: (2, "fresh")
    )

    with pytest.raises(RuntimeError, match="injected failure"):
        module.repair_connection(
            conn,
            dry_run=False,
            after_update=lambda _partition: (_ for _ in ()).throw(
                RuntimeError("injected failure")
            ),
        )
    assert tuple(
        conn.execute(
            f"SELECT row_count, content_hash FROM {ACCEPTED_TABLE} WHERE dataset_id = ?",
            [DATASET_ID],
        ).fetchone()
    ) == (1, "stale")

    missing = {**mismatch, "pointer_row_count": None, "reason": "pointer_missing"}
    monkeypatch.setattr(module, "_count_mismatches", lambda _conn: [missing])
    with pytest.raises(RuntimeError, match="pointer_missing"):
        module.repair_connection(conn, dry_run=False)
