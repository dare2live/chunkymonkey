"""Tests for holders landing retention (F3 archive non-latest ACCEPTED)."""
from __future__ import annotations

from pathlib import Path

import duckdb

from services.data_sources.accepted_schema import ACCEPTED_PARTITION_DDL, INGEST_BATCH_DDL
from services.data_sources.holders_top10_schema import DATASET_ID, LANDING_TABLE
from services.holders_landing_retention import (
    apply_retention,
    build_retention_plan,
    ensure_deletion_record_table,
    slice_archive_plan,
)


def _setup(conn) -> None:
    conn.execute(INGEST_BATCH_DDL)
    conn.execute(ACCEPTED_PARTITION_DDL)
    conn.execute(
        f"""
        CREATE TABLE {LANDING_TABLE} (
            batch_id VARCHAR NOT NULL,
            row_ordinal INTEGER NOT NULL,
            request_json VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            row_hash VARCHAR NOT NULL,
            PRIMARY KEY (batch_id, row_ordinal)
        )
        """
    )


def _batch(
    conn,
    *,
    batch_id: str,
    partition: str,
    status: str,
    accepted_at: str,
    landing_rows: int,
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_batch (
          batch_id, dataset_id, contract_version, contract_hash, config_hash,
          writer_id, partition_value, source_name, status, request_json,
          fragment_outcomes_json, expected_fragment_count, completed_fragment_count,
          failed_fragment_count, landing_row_count, payload_hash, observed_at,
          available_at, landed_at, accepted_at
        ) VALUES (
          ?, ?, '2', 'ch', 'cfg', 'w', ?, 'miaoxiang', ?, '{}', '[]',
          1, 1, 0, ?, 'ph', ?, ?, ?, ?
        )
        """,
        [
            batch_id,
            DATASET_ID,
            partition,
            status,
            landing_rows,
            accepted_at,
            accepted_at,
            accepted_at,
            accepted_at if status == "ACCEPTED" else None,
        ],
    )
    for i in range(landing_rows):
        conn.execute(
            f"""
            INSERT INTO {LANDING_TABLE}
            VALUES (?, ?, '{{}}', '{{"n":{i}}}', ?)
            """,
            [batch_id, i, f"h-{batch_id}-{i}"],
        )


def _pointer(conn, *, batch_id: str, partition: str, accepted_at: str) -> None:
    conn.execute(
        """
        INSERT INTO accepted_partition (
          dataset_id, partition_value, batch_id, contract_version,
          contract_hash, config_hash, row_count, content_hash,
          observed_at, available_at, accepted_at
        ) VALUES (?, ?, ?, '2', 'ch', 'cfg', 1, 'h', ?, ?, ?)
        """,
        [DATASET_ID, partition, batch_id, accepted_at, accepted_at, accepted_at],
    )


def test_retention_keeps_latest_accepted_and_inflight(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    try:
        _setup(conn)
        _batch(
            conn,
            batch_id="old",
            partition="20250101",
            status="ACCEPTED",
            accepted_at="2026-07-20T00:00:00+00:00",
            landing_rows=3,
        )
        _batch(
            conn,
            batch_id="new",
            partition="20250101",
            status="ACCEPTED",
            accepted_at="2026-07-22T00:00:00+00:00",
            landing_rows=3,
        )
        _batch(
            conn,
            batch_id="inflight",
            partition="20250102",
            status="LANDED",
            accepted_at="2026-07-23T00:00:00+00:00",
            landing_rows=2,
        )
        plan = build_retention_plan(conn)
        assert plan.keep_batch_count == 2  # new + inflight
        assert plan.archive_batch_count == 1
        assert "old" in plan.archive_batch_ids
        assert "new" in plan.keep_batch_ids
        assert "inflight" in plan.keep_batch_ids
        assert plan.archive_landing_rows == 3

        ensure_deletion_record_table(conn)
        result = apply_retention(
            conn,
            plan=plan,
            archive_dir=tmp_path,
            run_id="test_retention_1",
        )
        assert result.deleted_rows == 3
        assert Path(result.archive_path).exists()
        assert (
            conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0] == 5
        )
        assert (
            conn.execute(
                f"SELECT COUNT(*) FROM {LANDING_TABLE} WHERE batch_id='old'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT deleted_rows FROM mart_data_deletion_record "
                "WHERE deletion_run_id='test_retention_1'"
            ).fetchone()[0]
            == 3
        )
    finally:
        conn.close()


def test_slice_archive_plan_limits_superseded_batches(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    try:
        _setup(conn)
        _batch(
            conn,
            batch_id="old-a",
            partition="20250101",
            status="ACCEPTED",
            accepted_at="2026-07-20T00:00:00+00:00",
            landing_rows=2,
        )
        _batch(
            conn,
            batch_id="new-a",
            partition="20250101",
            status="ACCEPTED",
            accepted_at="2026-07-22T00:00:00+00:00",
            landing_rows=2,
        )
        _batch(
            conn,
            batch_id="old-b",
            partition="20250102",
            status="ACCEPTED",
            accepted_at="2026-07-20T00:00:00+00:00",
            landing_rows=4,
        )
        _batch(
            conn,
            batch_id="new-b",
            partition="20250102",
            status="ACCEPTED",
            accepted_at="2026-07-22T00:00:00+00:00",
            landing_rows=4,
        )
        full = build_retention_plan(conn)
        assert full.archive_batch_count == 2
        sliced = slice_archive_plan(conn, full, 1)
        assert sliced.archive_batch_count == 1
        assert sliced.keep_batch_count == full.keep_batch_count
        assert sliced.archive_landing_rows in {2, 4}
        ensure_deletion_record_table(conn)
        result = apply_retention(
            conn,
            plan=sliced,
            archive_dir=tmp_path,
            run_id="test_retention_chunk",
        )
        assert result.deleted_rows == sliced.archive_landing_rows
        leftover = build_retention_plan(conn)
        archived_id = sliced.archive_batch_ids[0]
        assert (
            conn.execute(
                f"SELECT COUNT(*) FROM {LANDING_TABLE} WHERE batch_id=?",
                [archived_id],
            ).fetchone()[0]
            == 0
        )
        # Empty superseded ingest_batch rows are not re-queued; only payload remains.
        leftover_ids = set(leftover.archive_batch_ids)
        assert archived_id not in leftover_ids
        assert leftover.archive_landing_rows == full.archive_landing_rows - sliced.archive_landing_rows
    finally:
        conn.close()


def test_retention_noop_when_single_accepted_per_partition() -> None:
    conn = duckdb.connect(":memory:")
    try:
        _setup(conn)
        _batch(
            conn,
            batch_id="only",
            partition="20250101",
            status="ACCEPTED",
            accepted_at="2026-07-22T00:00:00+00:00",
            landing_rows=4,
        )
        plan = build_retention_plan(conn)
        assert plan.archive_batch_count == 0
        assert plan.archive_landing_rows == 0
        assert plan.keep_landing_rows == 4
    finally:
        conn.close()


def test_retention_keeps_accepted_partition_pointer_when_clock_tied() -> None:
    """Same accepted_at: keep the published pointer, not max(batch_id)."""

    conn = duckdb.connect(":memory:")
    try:
        _setup(conn)
        clock = "2026-08-26T10:00:00+00:00"
        _batch(
            conn,
            batch_id="holders_top10:20260826:aaa",
            partition="20260826",
            status="ACCEPTED",
            accepted_at=clock,
            landing_rows=2,
        )
        _batch(
            conn,
            batch_id="holders_top10:20260826:zzz",
            partition="20260826",
            status="ACCEPTED",
            accepted_at=clock,
            landing_rows=3,
        )
        _pointer(
            conn,
            batch_id="holders_top10:20260826:aaa",
            partition="20260826",
            accepted_at=clock,
        )
        plan = build_retention_plan(conn)
        assert "holders_top10:20260826:aaa" in plan.keep_batch_ids
        assert "holders_top10:20260826:zzz" in plan.archive_batch_ids
        assert plan.archive_landing_rows == 3
        assert plan.keep_landing_rows == 2
    finally:
        conn.close()
