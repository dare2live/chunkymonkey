"""Data-processing tool observability helpers."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any

from services.pipeline_manifest import git_commit_sha


DDL = """
CREATE TABLE IF NOT EXISTS mart_data_processing_tool_run (
    run_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    source_name TEXT,
    status TEXT NOT NULL,
    input_rows BIGINT NOT NULL DEFAULT 0,
    accepted_rows BIGINT NOT NULL DEFAULT 0,
    rejected_rows BIGINT NOT NULL DEFAULT 0,
    reason_counts_json TEXT,
    input_table TEXT,
    output_table TEXT,
    batch_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_s DOUBLE,
    commit_sha TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_data_processing_tool_run_tool_time
    ON mart_data_processing_tool_run(tool_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_processing_tool_run_source_time
    ON mart_data_processing_tool_run(source_name, started_at DESC);

CREATE TABLE IF NOT EXISTS mart_data_processing_tool_issue (
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    affected_table TEXT,
    affected_field TEXT,
    sample_rows_json TEXT,
    train_blocking BOOLEAN NOT NULL,
    production_blocking BOOLEAN NOT NULL,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_data_processing_tool_issue_run
    ON mart_data_processing_tool_issue(run_id);
CREATE INDEX IF NOT EXISTS idx_data_processing_tool_issue_reason
    ON mart_data_processing_tool_issue(reason_code);
"""


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_data_processing_monitor_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
        return
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass
class ProcessingToolStats:
    tool_name: str
    policy_id: str
    source_name: str | None = None
    input_rows: int = 0
    accepted_rows: int = 0
    rejected_rows: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)
    issue_samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    started_perf: float = field(default_factory=time.perf_counter)

    def accept(self) -> None:
        self.input_rows += 1
        self.accepted_rows += 1

    def reject(self, reasons: list[str], sample: dict[str, Any] | None = None) -> None:
        self.input_rows += 1
        self.rejected_rows += 1
        reason_list = reasons or ["unknown_rejection"]
        for reason in reason_list:
            self.reason_counts[reason] = self.reason_counts.get(reason, 0) + 1
            if sample is not None:
                samples = self.issue_samples.setdefault(reason, [])
                if len(samples) < 5:
                    samples.append(sample)

    def duration_s(self) -> float:
        return round(time.perf_counter() - self.started_perf, 6)


def record_data_processing_tool_run(
    conn: Any,
    *,
    stats: ProcessingToolStats,
    run_id: str,
    status: str = "completed",
    input_table: str | None = None,
    output_table: str | None = None,
    batch_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    record_clean_runs: bool = False,
) -> None:
    """Persist a processing tool run.

    Clean per-stock writes can be very frequent, so callers may skip clean
    runs and always record rejected runs.
    """
    if stats.rejected_rows <= 0 and not record_clean_runs:
        return
    ensure_data_processing_monitor_tables(conn)
    ended_at = utc_now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_data_processing_tool_run (
            run_id, tool_name, policy_id, source_name, status,
            input_rows, accepted_rows, rejected_rows, reason_counts_json,
            input_table, output_table, batch_id, started_at, ended_at,
            duration_s, commit_sha, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            stats.tool_name,
            stats.policy_id,
            stats.source_name,
            status,
            int(stats.input_rows),
            int(stats.accepted_rows),
            int(stats.rejected_rows),
            _json(stats.reason_counts),
            input_table,
            output_table,
            batch_id,
            stats.started_at,
            ended_at,
            stats.duration_s(),
            git_commit_sha(),
            _json(metadata),
        ),
    )
    conn.execute("DELETE FROM mart_data_processing_tool_issue WHERE run_id = ?", (run_id,))
    issue_rows = [
        (
            run_id,
            stats.tool_name,
            reason,
            "error",
            output_table,
            None,
            _json(samples),
            True,
            True,
            ended_at,
        )
        for reason, samples in sorted(stats.issue_samples.items())
    ]
    if issue_rows:
        conn.executemany(
            """
            INSERT INTO mart_data_processing_tool_issue (
                run_id, tool_name, reason_code, severity, affected_table,
                affected_field, sample_rows_json, train_blocking,
                production_blocking, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            issue_rows,
        )
    conn.commit()
