"""Lightweight deletion ledger for obsolete data assets.

The project policy is delete-after-verification, not long-lived archival of
obsolete experiment rows. This ledger keeps only the evidence needed to explain
why data was removed and how many rows/files were affected.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any

from services.schema_versions import record_actual_version


DDL = """
CREATE TABLE IF NOT EXISTS mart_data_deletion_record (
    record_id TEXT PRIMARY KEY,
    deletion_run_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    delete_scope TEXT NOT NULL,
    key_column TEXT,
    key_value TEXT,
    deleted_rows BIGINT DEFAULT 0,
    deleted_files BIGINT DEFAULT 0,
    deleted_bytes BIGINT DEFAULT 0,
    reason TEXT NOT NULL,
    verification_json TEXT,
    deleted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_data_deletion_run
    ON mart_data_deletion_record(deletion_run_id);
CREATE INDEX IF NOT EXISTS idx_data_deletion_table
    ON mart_data_deletion_record(table_name, delete_scope);
"""


def ensure_data_deletion_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
        return
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def record_data_deletion(
    conn: Any,
    *,
    deletion_run_id: str,
    table_name: str,
    delete_scope: str,
    reason: str,
    key_column: str | None = None,
    key_value: str | None = None,
    deleted_rows: int = 0,
    deleted_files: int = 0,
    deleted_bytes: int = 0,
    verification: dict[str, Any] | None = None,
    deleted_at: str | None = None,
) -> None:
    ensure_data_deletion_tables(conn)
    deleted_at = deleted_at or datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    record_id = "|".join(
        [
            deletion_run_id,
            table_name,
            delete_scope,
            key_column or "",
            key_value or "",
        ]
    )
    # 不用 INSERT OR REPLACE: 它依赖 record_id 上的主键约束。2026-09-11 实测 tushare_raw / market /
    # reference / feature_store 四个库里这张表都没有任何约束 (早年整表复制建出, CREATE TABLE IF NOT
    # EXISTS 不会补回主键), 只有 smartmoney 有; 无主键的表上 INSERT OR REPLACE 直接 Binder Error。
    # 先删同 record_id 再插, 有无主键都成立; 调用方在事务里调用时两句一起提交。
    conn.execute("DELETE FROM mart_data_deletion_record WHERE record_id = ?", (record_id,))
    conn.execute(
        """
        INSERT INTO mart_data_deletion_record (
            record_id, deletion_run_id, table_name, delete_scope,
            key_column, key_value, deleted_rows, deleted_files, deleted_bytes,
            reason, verification_json, deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            deletion_run_id,
            table_name,
            delete_scope,
            key_column,
            key_value,
            int(deleted_rows or 0),
            int(deleted_files or 0),
            int(deleted_bytes or 0),
            reason,
            json.dumps(verification or {}, ensure_ascii=False, sort_keys=True, default=str),
            deleted_at,
        ),
    )
    record_actual_version(conn, "mart_data_deletion_record")
