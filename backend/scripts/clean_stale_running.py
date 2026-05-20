#!/usr/bin/env python3
"""Mark stale Optuna SQLite RUNNING trials as FAIL.

Use this after a VM preemption or hard crash leaves Optuna RDB trials in
RUNNING state, which can block clean resume semantics.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _sqlite_path(value: str | None) -> Path:
    if not value:
        raise ValueError("either --db or --storage is required")
    if value.startswith("sqlite:///"):
        return Path(value.removeprefix("sqlite:///"))
    return Path(value)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _study_id(conn: sqlite3.Connection, study_name: str | None) -> int | None:
    if not study_name:
        return None
    row = conn.execute("SELECT study_id FROM studies WHERE study_name = ?", (study_name,)).fetchone()
    if row is None:
        raise ValueError(f"study not found: {study_name}")
    return int(row[0])


def _where_clause(
    *,
    columns: set[str],
    study_id: int | None,
    older_than_min: int,
) -> tuple[str, list[object]]:
    clauses = ["state = 'RUNNING'"]
    params: list[object] = []
    if study_id is not None:
        clauses.append("study_id = ?")
        params.append(study_id)
    if older_than_min > 0:
        if "datetime_start" not in columns:
            raise ValueError("trials.datetime_start is required when --older-than-min is set")
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=older_than_min)
        clauses.append("datetime_start < ?")
        params.append(cutoff.isoformat(sep=" "))
    return " AND ".join(clauses), params


def clean_stale_running(
    *,
    db_path: Path,
    study_name: str | None,
    older_than_min: int,
    dry_run: bool,
) -> int:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        columns = _table_columns(conn, "trials")
        required = {"trial_id", "number", "study_id", "state"}
        missing = required - columns
        if missing:
            raise ValueError(f"not an Optuna trials table, missing columns: {sorted(missing)}")

        where_sql, params = _where_clause(
            columns=columns,
            study_id=_study_id(conn, study_name),
            older_than_min=older_than_min,
        )
        rows = conn.execute(
            f"SELECT trial_id, number, study_id FROM trials WHERE {where_sql} ORDER BY trial_id",
            params,
        ).fetchall()
        if dry_run or not rows:
            print(f"RUNNING trials matched: {len(rows)}")
            for trial_id, number, study_id in rows:
                print(f"  trial_id={trial_id} number={number} study_id={study_id}")
            return len(rows)

        complete_expr = ", datetime_complete = ?" if "datetime_complete" in columns else ""
        update_params: list[object] = []
        if complete_expr:
            update_params.append(datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "))
        update_params.extend(params)
        conn.execute(f"UPDATE trials SET state = 'FAIL'{complete_expr} WHERE {where_sql}", update_params)
        conn.commit()
        print(f"marked FAIL: {len(rows)}")
        for trial_id, number, study_id in rows:
            print(f"  trial_id={trial_id} number={number} study_id={study_id}")
        return len(rows)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark Optuna SQLite RUNNING trials as FAIL")
    parser.add_argument("--db", default=None, help="Path to Optuna SQLite DB")
    parser.add_argument("--storage", default=None, help="Optuna storage URL, e.g. sqlite:///data/reports/optuna/run.db")
    parser.add_argument("--study-name", default=None, help="Optional Optuna study_name filter")
    parser.add_argument("--older-than-min", type=int, default=0, help="Only fail RUNNING trials older than N minutes")
    parser.add_argument("--dry-run", action="store_true", help="List matched RUNNING trials without updating")
    args = parser.parse_args()

    try:
        db_path = _sqlite_path(args.db or args.storage)
        clean_stale_running(
            db_path=db_path,
            study_name=args.study_name,
            older_than_min=args.older_than_min,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
