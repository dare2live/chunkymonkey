"""Pipeline run manifest helpers.

This module keeps batch/model runs auditable without coupling every script to
the full FastAPI app. Scripts can call ``record_pipeline_run`` after a run and
the data-health UI can read ``mart_pipeline_run_manifest`` as the single
runtime ledger.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DDL = """
CREATE TABLE IF NOT EXISTS mart_pipeline_run_manifest (
    run_id                TEXT PRIMARY KEY,
    pipeline_name         TEXT NOT NULL,
    status                TEXT NOT NULL,
    started_at            TIMESTAMP,
    ended_at              TIMESTAMP,
    duration_s            DOUBLE,
    commit_sha            TEXT,
    command               TEXT,
    cwd                   TEXT,
    input_tables_json     TEXT,
    output_tables_json    TEXT,
    input_row_counts_json TEXT,
    output_row_counts_json TEXT,
    model_id              TEXT,
    feature_group         TEXT,
    label_name            TEXT,
    holding_period        INTEGER,
    gate_result           TEXT,
    blockers_json         TEXT,
    perf_summary_json     TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pipeline_manifest_started
    ON mart_pipeline_run_manifest(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_manifest_name_status
    ON mart_pipeline_run_manifest(pipeline_name, status);
"""


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def ensure_pipeline_manifest_schema(conn) -> None:
    conn.executescript(DDL)


def current_command(argv: Iterable[str] | None = None) -> str:
    parts = list(sys.argv if argv is None else argv)
    return " ".join(str(part) for part in parts)


def git_commit_sha(cwd: str | Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(cwd or os.getcwd()),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _quote_table_name(name: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in name.split("."))


def table_exists(conn, table_name: str) -> bool:
    parts = table_name.split(".")
    if len(parts) == 2:
        schema, table = parts
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = ? AND table_name = ?
             LIMIT 1
            """,
            (schema, table),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = ?
             LIMIT 1
            """,
            (table_name,),
        ).fetchone()
    return row is not None


def table_row_counts(conn, tables: Iterable[str]) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    for table in tables:
        try:
            if table_exists(conn, table):
                row = conn.execute(f"SELECT COUNT(*) FROM {_quote_table_name(table)}").fetchone()
                counts[table] = int(row[0]) if row else 0
            else:
                counts[table] = None
        except Exception:
            counts[table] = None
    return counts


def record_pipeline_run(
    conn,
    *,
    run_id: str,
    pipeline_name: str,
    status: str,
    started_at: str | None = None,
    ended_at: str | None = None,
    duration_s: float | None = None,
    commit_sha: str | None = None,
    command: str | None = None,
    cwd: str | None = None,
    input_tables: Iterable[str] | None = None,
    output_tables: Iterable[str] | None = None,
    input_row_counts: dict[str, int | None] | None = None,
    output_row_counts: dict[str, int | None] | None = None,
    model_id: str | None = None,
    feature_group: str | None = None,
    label_name: str | None = None,
    holding_period: int | None = None,
    gate_result: str | None = None,
    blockers: list[str] | dict[str, Any] | None = None,
    perf_summary: dict[str, Any] | None = None,
) -> None:
    ensure_pipeline_manifest_schema(conn)
    input_list = list(input_tables or [])
    output_list = list(output_tables or [])
    if input_row_counts is None and input_list:
        input_row_counts = table_row_counts(conn, input_list)
    if output_row_counts is None and output_list:
        output_row_counts = table_row_counts(conn, output_list)
    perf_summary_out = dict(perf_summary or {})
    duckdb_lock_wait_s = float(getattr(conn, "duckdb_lock_wait_s", 0.0) or 0.0)
    connect_mutex_wait_s = float(getattr(conn, "connect_mutex_wait_s", 0.0) or 0.0)
    if duckdb_lock_wait_s:
        perf_summary_out["duckdb_lock_wait_s"] = round(duckdb_lock_wait_s, 6)
    if connect_mutex_wait_s:
        perf_summary_out["connect_mutex_wait_s"] = round(connect_mutex_wait_s, 6)

    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pipeline_run_manifest (
            run_id, pipeline_name, status, started_at, ended_at, duration_s,
            commit_sha, command, cwd,
            input_tables_json, output_tables_json,
            input_row_counts_json, output_row_counts_json,
            model_id, feature_group, label_name, holding_period,
            gate_result, blockers_json, perf_summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            pipeline_name,
            status,
            started_at,
            ended_at,
            duration_s,
            commit_sha if commit_sha is not None else git_commit_sha(cwd),
            command if command is not None else current_command(),
            cwd if cwd is not None else os.getcwd(),
            _json(input_list),
            _json(output_list),
            _json(input_row_counts),
            _json(output_row_counts),
            model_id,
            feature_group,
            label_name,
            holding_period,
            gate_result,
            _json(blockers),
            _json(perf_summary_out or None),
        ),
    )
    conn.commit()
