"""Lightweight pipeline lock ledger for production batch entrypoints."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


DDL = """
CREATE TABLE IF NOT EXISTS mart_pipeline_lock (
    lock_name TEXT PRIMARY KEY,
    owner_run_id TEXT NOT NULL,
    owner_pid INTEGER,
    phase TEXT,
    status TEXT NOT NULL,
    started_at TIMESTAMP,
    heartbeat_at TIMESTAMP,
    released_at TIMESTAMP,
    stale_after_s INTEGER,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_lock_status
    ON mart_pipeline_lock(status, heartbeat_at);
"""


class PipelineLockError(RuntimeError):
    def __init__(self, message: str, active_lock: dict[str, Any] | None = None):
        super().__init__(message)
        self.active_lock = active_lock or {}


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def ensure_pipeline_lock_schema(conn) -> None:
    conn.executescript(DDL)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _active_lock(conn, lock_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT lock_name, owner_run_id, owner_pid, phase, status, started_at,
               heartbeat_at, released_at, stale_after_s, metadata_json
          FROM mart_pipeline_lock
         WHERE lock_name = ?
           AND released_at IS NULL
           AND status = 'running'
         LIMIT 1
        """,
        (lock_name,),
    ).fetchone()
    return dict(row) if row else None


def _heartbeat_age_s(lock: dict[str, Any]) -> float | None:
    heartbeat_at = _parse_dt(lock.get("heartbeat_at") or lock.get("started_at"))
    if not heartbeat_at:
        return None
    return max(0.0, (datetime.utcnow() - heartbeat_at).total_seconds())


def acquire_pipeline_lock(
    conn,
    *,
    lock_name: str,
    owner_run_id: str,
    phase: str,
    stale_after_s: int = 600,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    ensure_pipeline_lock_schema(conn)
    now = utc_now_iso()
    active = _active_lock(conn, lock_name)
    stale_released = False
    if active:
        age = _heartbeat_age_s(active)
        active_stale_after = int(active.get("stale_after_s") or stale_after_s or 0)
        if active_stale_after > 0 and age is not None and age > active_stale_after:
            conn.execute(
                """
                UPDATE mart_pipeline_lock
                   SET status = 'stale_released', released_at = ?
                 WHERE lock_name = ? AND owner_run_id = ?
                """,
                (now, lock_name, active["owner_run_id"]),
            )
            stale_released = True
        else:
            raise PipelineLockError(f"pipeline lock {lock_name} is already held", active)

    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pipeline_lock (
            lock_name, owner_run_id, owner_pid, phase, status, started_at,
            heartbeat_at, released_at, stale_after_s, metadata_json
        ) VALUES (?, ?, ?, ?, 'running', ?, ?, NULL, ?, ?)
        """,
        (
            lock_name,
            owner_run_id,
            os.getpid(),
            phase,
            now,
            now,
            stale_after_s,
            _json(metadata),
        ),
    )
    if commit:
        conn.commit()
    return {
        "lock_name": lock_name,
        "owner_run_id": owner_run_id,
        "owner_pid": os.getpid(),
        "phase": phase,
        "started_at": now,
        "heartbeat_at": now,
        "stale_released_previous": stale_released,
    }


def heartbeat_pipeline_lock(
    conn,
    *,
    lock_name: str,
    owner_run_id: str,
    phase: str | None = None,
    commit: bool = False,
) -> bool:
    ensure_pipeline_lock_schema(conn)
    now = utc_now_iso()
    if phase is None:
        cursor = conn.execute(
            """
            UPDATE mart_pipeline_lock
               SET heartbeat_at = ?
             WHERE lock_name = ? AND owner_run_id = ? AND released_at IS NULL
            """,
            (now, lock_name, owner_run_id),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE mart_pipeline_lock
               SET heartbeat_at = ?, phase = ?
             WHERE lock_name = ? AND owner_run_id = ? AND released_at IS NULL
            """,
            (now, phase, lock_name, owner_run_id),
        )
    if commit:
        conn.commit()
    try:
        return bool(cursor.rowcount)
    except Exception:
        return True


def release_pipeline_lock(
    conn,
    *,
    lock_name: str,
    owner_run_id: str,
    status: str = "released",
    commit: bool = False,
) -> bool:
    ensure_pipeline_lock_schema(conn)
    now = utc_now_iso()
    cursor = conn.execute(
        """
        UPDATE mart_pipeline_lock
           SET status = ?, released_at = ?, heartbeat_at = ?
         WHERE lock_name = ? AND owner_run_id = ? AND released_at IS NULL
        """,
        (status, now, now, lock_name, owner_run_id),
    )
    if commit:
        conn.commit()
    try:
        return bool(cursor.rowcount)
    except Exception:
        return True


def get_pipeline_lock(conn, *, lock_name: str) -> dict[str, Any] | None:
    ensure_pipeline_lock_schema(conn)
    row = conn.execute(
        """
        SELECT lock_name, owner_run_id, owner_pid, phase, status, started_at,
               heartbeat_at, released_at, stale_after_s, metadata_json
          FROM mart_pipeline_lock
         WHERE lock_name = ?
         LIMIT 1
        """,
        (lock_name,),
    ).fetchone()
    return dict(row) if row else None
