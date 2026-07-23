"""Holders landing retention helpers (archive non-latest ACCEPTED landing).

Keep:
  - latest ACCEPTED batch landing per partition_value
  - all non-ACCEPTED batch landing (LANDED/REJECTED in-flight)

Archive+DELETE:
  - older ACCEPTED batch landing rows only

Canonical / accepted_partition / ingest_batch metadata stay; landing payload for
archived batches moves to parquet cold fuse.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.data_sources.holders_top10_schema import DATASET_ID, LANDING_TABLE


@dataclass(frozen=True)
class RetentionPlan:
    partition_count: int
    keep_batch_count: int
    archive_batch_count: int
    total_landing_rows: int
    keep_landing_rows: int
    archive_landing_rows: int
    keep_batch_ids: tuple[str, ...]
    archive_batch_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetentionResult:
    archive_path: str
    archived_rows: int
    deleted_rows: int
    run_id: str
    keep_batch_count: int
    archive_batch_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "archived_rows": self.archived_rows,
            "deleted_rows": self.deleted_rows,
            "run_id": self.run_id,
            "keep_batch_count": self.keep_batch_count,
            "archive_batch_count": self.archive_batch_count,
        }


def ensure_deletion_record_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mart_data_deletion_record ("
        "record_id VARCHAR, deletion_run_id VARCHAR, table_name VARCHAR, "
        "delete_scope VARCHAR, key_column VARCHAR, key_value VARCHAR, "
        "deleted_rows BIGINT, deleted_files BIGINT, deleted_bytes BIGINT, "
        "reason VARCHAR, verification_json VARCHAR, deleted_at VARCHAR)"
    )


def build_retention_plan(conn) -> RetentionPlan:
    """Compute keep vs archive batch sets from ingest_batch + landing counts."""
    keep_rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT batch_id, partition_value, status,
                 ROW_NUMBER() OVER (
                   PARTITION BY partition_value
                   ORDER BY COALESCE(accepted_at, landed_at) DESC, batch_id DESC
                 ) AS rn
          FROM ingest_batch
          WHERE dataset_id = ?
            AND status = 'ACCEPTED'
        ),
        keep_accepted AS (
          SELECT batch_id FROM ranked WHERE rn = 1
        ),
        keep_inflight AS (
          SELECT batch_id FROM ingest_batch
          WHERE dataset_id = ?
            AND status <> 'ACCEPTED'
        )
        SELECT batch_id FROM keep_accepted
        UNION
        SELECT batch_id FROM keep_inflight
        ORDER BY 1
        """,
        [DATASET_ID, DATASET_ID],
    ).fetchall()
    keep_ids = tuple(str(r[0]) for r in keep_rows)

    archive_rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT batch_id,
                 ROW_NUMBER() OVER (
                   PARTITION BY partition_value
                   ORDER BY COALESCE(accepted_at, landed_at) DESC, batch_id DESC
                 ) AS rn
          FROM ingest_batch
          WHERE dataset_id = ?
            AND status = 'ACCEPTED'
        )
        SELECT batch_id FROM ranked WHERE rn > 1
        ORDER BY 1
        """,
        [DATASET_ID],
    ).fetchall()
    archive_ids = tuple(str(r[0]) for r in archive_rows)

    parts = conn.execute(
        """
        SELECT COUNT(DISTINCT partition_value)
        FROM ingest_batch
        WHERE dataset_id = ? AND status = 'ACCEPTED'
        """,
        [DATASET_ID],
    ).fetchone()[0]

    total = int(conn.execute(f'SELECT COUNT(*) FROM "{LANDING_TABLE}"').fetchone()[0])
    if keep_ids:
        placeholders = ",".join("?" * len(keep_ids))
        keep_n = int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{LANDING_TABLE}" '
                f"WHERE batch_id IN ({placeholders})",
                list(keep_ids),
            ).fetchone()[0]
        )
    else:
        keep_n = 0
    archive_n = total - keep_n
    # Prefer counting archive ids directly when present (orphan landing safety).
    if archive_ids:
        placeholders = ",".join("?" * len(archive_ids))
        archive_n = int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{LANDING_TABLE}" '
                f"WHERE batch_id IN ({placeholders})",
                list(archive_ids),
            ).fetchone()[0]
        )

    return RetentionPlan(
        partition_count=int(parts or 0),
        keep_batch_count=len(keep_ids),
        archive_batch_count=len(archive_ids),
        total_landing_rows=total,
        keep_landing_rows=keep_n,
        archive_landing_rows=archive_n,
        keep_batch_ids=keep_ids,
        archive_batch_ids=archive_ids,
    )


def apply_retention(
    conn,
    *,
    plan: RetentionPlan,
    archive_dir: Path,
    run_id: str,
) -> RetentionResult:
    """Archive archive_batch landing to parquet, DELETE those rows, record deletion."""
    if not plan.archive_batch_ids:
        return RetentionResult(
            archive_path="",
            archived_rows=0,
            deleted_rows=0,
            run_id=run_id,
            keep_batch_count=plan.keep_batch_count,
            archive_batch_count=0,
        )

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{run_id}.parquet"
    if archive_path.exists():
        raise RuntimeError(f"archive already exists: {archive_path}")

    placeholders = ",".join("?" * len(plan.archive_batch_ids))
    ids = list(plan.archive_batch_ids)

    # COPY archive rows first (verify count), then DELETE same set.
    conn.execute(
        f"""
        COPY (
          SELECT * FROM "{LANDING_TABLE}"
          WHERE batch_id IN ({placeholders})
          ORDER BY batch_id, row_ordinal
        ) TO '{archive_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        ids,
    )
    archived = int(
        conn.execute(
            f'SELECT COUNT(*) FROM "{LANDING_TABLE}" WHERE batch_id IN ({placeholders})',
            ids,
        ).fetchone()[0]
    )
    # Round-trip count from parquet
    pq_n = int(
        conn.execute(
            f"SELECT COUNT(*) FROM read_parquet('{archive_path.as_posix()}')"
        ).fetchone()[0]
    )
    if pq_n != archived:
        raise RuntimeError(
            f"archive row mismatch: landing={archived} parquet={pq_n}; refusing DELETE"
        )

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            f'DELETE FROM "{LANDING_TABLE}" WHERE batch_id IN ({placeholders})',
            ids,
        )
        deleted = archived
        remaining = int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{LANDING_TABLE}" WHERE batch_id IN ({placeholders})',
                ids,
            ).fetchone()[0]
        )
        if remaining != 0:
            raise RuntimeError(f"DELETE incomplete: {remaining} archive rows remain")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO mart_data_deletion_record VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                f"{run_id}:landing",
                run_id,
                LANDING_TABLE,
                "non_latest_accepted_batches",
                "batch_id",
                f"n={len(plan.archive_batch_ids)}",
                deleted,
                1,
                int(archive_path.stat().st_size),
                (
                    "F3 holders landing retention: archive non-latest ACCEPTED "
                    "landing; keep latest ACCEPTED + inflight; skip-land prevents "
                    "recurrence"
                ),
                json_dumps(
                    {
                        "keep_batch_count": plan.keep_batch_count,
                        "archive_batch_count": plan.archive_batch_count,
                        "archive_path": str(archive_path),
                        "parquet_rows": pq_n,
                    }
                ),
                now,
            ],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return RetentionResult(
        archive_path=str(archive_path),
        archived_rows=archived,
        deleted_rows=deleted,
        run_id=run_id,
        keep_batch_count=plan.keep_batch_count,
        archive_batch_count=plan.archive_batch_count,
    )


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


__all__ = [
    "RetentionPlan",
    "RetentionResult",
    "apply_retention",
    "build_retention_plan",
    "ensure_deletion_record_table",
]
