#!/usr/bin/env python3
"""Print a config-driven storage cleanup dry-run report."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import shutil
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.duck_adapter import DuckConn, connect as duck_connect  # noqa: E402
from services.storage_retention import (  # noqa: E402
    execute_storage_cleanup,
    load_storage_retention_policy,
    plan_storage_cleanup,
)


def prepare_db_copy(*, source: str | None, target: str | None, overwrite: bool) -> None:
    if not source:
        return
    if not target:
        raise RuntimeError("--copy-from requires --db-path")
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.exists():
        raise RuntimeError(f"copy source does not exist: {source_path}")
    if target_path.exists() and not overwrite:
        raise RuntimeError(f"copy target already exists; pass --overwrite-copy: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)


@contextmanager
def open_retention_connection(db_path: str | None = None) -> Iterator[DuckConn]:
    conn = duck_connect(str(Path(db_path))) if db_path else get_conn()
    try:
        yield conn
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--db-path", default=None, help="Open an explicit DuckDB path instead of production smartmoney.duckdb")
    parser.add_argument("--copy-from", default=None, help="Copy this DuckDB file to --db-path before planning/execution")
    parser.add_argument("--overwrite-copy", action="store_true", help="Allow --copy-from to replace an existing --db-path target")
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    prepare_db_copy(source=args.copy_from, target=args.db_path, overwrite=args.overwrite_copy)
    policy = load_storage_retention_policy(args.config)
    with open_retention_connection(args.db_path) as conn:
        if args.execute_approved:
            report = execute_storage_cleanup(
                conn,
                policy,
                approve=True,
                run_id=args.run_id,
            )
        else:
            report = plan_storage_cleanup(conn, policy)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
