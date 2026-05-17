#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
from google.cloud import storage


DEFAULT_TABLE = "mart_p1_optuna_trials"
CANONICAL_COLUMNS = [
    "run_id",
    "trial_number",
    "state",
    "value",
    "rank_ic_mean",
    "rank_ic_std",
    "n_windows",
    "params_json",
    "duration_s",
    "built_at",
    "user_attrs_json",
    "pruned_at_window",
]


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {uri}")
    rest = uri[5:]
    bucket, _, name = rest.partition("/")
    if not bucket or not name:
        raise ValueError(f"Invalid gs:// URI: {uri}")
    return bucket, name.rstrip("/")


def list_trial_blobs(prefix_uri: str) -> list[tuple[str, str]]:
    bucket_name, prefix = parse_gs_uri(prefix_uri)
    client = storage.Client()
    blobs = client.list_blobs(bucket_name, prefix=prefix)
    return [(bucket_name, b.name) for b in blobs if b.name.endswith("/trials.jsonl")]


def read_jsonl_blob(bucket_name: str, blob_name: str) -> list[dict[str, Any]]:
    client = storage.Client()
    text = client.bucket(bucket_name).blob(blob_name).download_as_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    try:
        rows = con.execute(f"DESCRIBE {table}").fetchall()
    except duckdb.CatalogException as exc:
        raise SystemExit(f"Local table {table} does not exist. Refusing to create or alter schema: {exc}") from exc
    return [r[0] for r in rows]


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    out = {col: row.get(col) for col in CANONICAL_COLUMNS}
    if out["params_json"] is None and isinstance(row.get("params"), dict):
        out["params_json"] = json.dumps(row["params"], sort_keys=True)
    if out["user_attrs_json"] is None and isinstance(row.get("user_attrs"), dict):
        out["user_attrs_json"] = json.dumps(row["user_attrs"], sort_keys=True)
    return out


def upsert_rows(db_path: Path, table: str, rows: list[dict[str, Any]], dry_run: bool) -> int:
    if not rows:
        return 0
    con = duckdb.connect(str(db_path))
    try:
        cols = [c for c in CANONICAL_COLUMNS if c in table_columns(con, table)]
        if "run_id" not in cols or "trial_number" not in cols:
            raise SystemExit(f"{table} must contain run_id and trial_number")
        normalized = [normalize(r) for r in rows]
        if dry_run:
            return len(normalized)
        placeholders = ", ".join(["?"] * len(cols))
        col_sql = ", ".join(cols)
        sql = f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})"
        values = [[row.get(c) for c in cols] for row in normalized]
        con.executemany(sql, values)
        return len(values)
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull GCS Batch results into local DuckDB mart_p1_optuna_trials.")
    parser.add_argument("--results-uri", required=True, help="Example: gs://YOUR_BUCKET_NAME/chunkymonkey/results/BATCH_ID")
    parser.add_argument("--db", default="data/smartmoney.duckdb")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-files", type=int, default=0)
    args = parser.parse_args()

    blobs = list_trial_blobs(args.results_uri)
    if args.limit_files:
        blobs = blobs[: args.limit_files]
    all_rows: list[dict[str, Any]] = []
    for bucket_name, blob_name in blobs:
        rows = read_jsonl_blob(bucket_name, blob_name)
        print(f"Read {len(rows)} trial rows from gs://{bucket_name}/{blob_name}")
        all_rows.extend(rows)

    count = upsert_rows(Path(args.db), args.table, all_rows, args.dry_run)
    action = "Would upsert" if args.dry_run else "Upserted"
    print(f"{action} {count} rows into {args.db}:{args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
