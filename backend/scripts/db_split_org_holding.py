#!/usr/bin/env python3
"""Split org_holding land/canonical/raw out of smartmoney into alias org_holding.

MASTER §5.6: split for write-lock isolation from smartmoney daily facts, not
layering. Land→canonical accept stays in the destination file.

Copy uses source DDL + INSERT (never CTAS). ingest_batch / accepted_partition
copy only dataset_id = org_holding. --drop-source runs after dest parity.

Usage:
  PYTHONPATH=backend python backend/scripts/db_split_org_holding.py
  PYTHONPATH=backend python backend/scripts/db_split_org_holding.py --execute
  PYTHONPATH=backend python backend/scripts/db_split_org_holding.py --drop-source
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.data_sources.org_holding_schema import (  # noqa: E402
    CANONICAL_TABLE,
    COMPATIBILITY_TABLE,
    DATASET_ID,
    LANDING_TABLE,
)
from services.duck_adapter import connect as duck_connect  # noqa: E402

MANIFEST = REPO / "backend" / "config" / "database_manifest.yaml"
DATA_TABLES = (LANDING_TABLE, CANONICAL_TABLE, COMPATIBILITY_TABLE)
CONTROL_TABLES = ("ingest_batch", "accepted_partition")


def _db_path(alias: str) -> Path:
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return REPO / m["databases"][alias]["path"]


def _count_map(con, *, dataset_id: str = DATASET_ID) -> dict[str, int]:
    out: dict[str, int] = {}
    for tab in DATA_TABLES:
        out[tab] = int(con.execute(f'SELECT count(*) FROM "{tab}"').fetchone()[0])
    out["ingest_batch"] = int(
        con.execute(
            "SELECT count(*) FROM ingest_batch WHERE dataset_id = ?",
            [dataset_id],
        ).fetchone()[0]
    )
    out["accepted_partition"] = int(
        con.execute(
            "SELECT count(*) FROM accepted_partition WHERE dataset_id = ?",
            [dataset_id],
        ).fetchone()[0]
    )
    return out


def _table_sql(con, tab: str) -> str:
    row = con.execute(
        "SELECT sql FROM duckdb_tables() WHERE schema_name='main' AND table_name=?",
        [tab],
    ).fetchone()
    sql = str(row[0] or "") if row else ""
    if not sql.strip():
        raise RuntimeError(f"table {tab} has empty duckdb_tables.sql")
    return sql


def _index_sqls(con, tab: str) -> list[str]:
    rows = con.execute(
        "SELECT sql FROM duckdb_indexes() WHERE table_name=? AND sql IS NOT NULL",
        [tab],
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def copy_org_holding(
    *,
    src: Path,
    dest: Path,
    execute: bool,
    dataset_id: str = DATASET_ID,
) -> dict[str, int]:
    if not src.is_file():
        raise FileNotFoundError(src)
    s = duck_connect(str(src), read_only=True)
    try:
        missing = [
            tab
            for tab in (*DATA_TABLES, *CONTROL_TABLES)
            if s.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='main' AND table_name=?",
                [tab],
            ).fetchone()[0]
            == 0
        ]
        if missing:
            raise RuntimeError(f"source missing tables: {missing}")
        baseline = _count_map(s, dataset_id=dataset_id)
        ddls = {tab: _table_sql(s, tab) for tab in (*DATA_TABLES, *CONTROL_TABLES)}
        indexes = {tab: _index_sqls(s, tab) for tab in (*DATA_TABLES, *CONTROL_TABLES)}
    finally:
        s.close()

    print("=== split org_holding smartmoney → alias org_holding ===")
    for name, n in baseline.items():
        print(f"  src {name}: {n:,}")
    if not execute:
        print("  DRY-RUN: --execute copies DDL+rows; --drop-source drops from smartmoney.")
        return baseline
    if dest.exists():
        raise RuntimeError(f"dest already exists: {dest}")

    t = duck_connect(str(dest), read_only=False)
    try:
        t.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
        for tab in DATA_TABLES:
            t.execute(ddls[tab])
            t.execute(f'INSERT INTO "{tab}" SELECT * FROM src."{tab}"')
            for isql in indexes[tab]:
                try:
                    t.execute(isql)
                except Exception as exc:  # noqa: BLE001 — PK already created the unique index
                    msg = str(exc).lower()
                    if "already exists" in msg or "duplicate" in msg:
                        continue
                    raise
        for tab in CONTROL_TABLES:
            t.execute(ddls[tab])
            t.execute(
                f'INSERT INTO "{tab}" SELECT * FROM src."{tab}" WHERE dataset_id = ?',
                [dataset_id],
            )
            for isql in indexes[tab]:
                try:
                    t.execute(isql)
                except Exception as exc:  # noqa: BLE001 — PK already created the unique index
                    msg = str(exc).lower()
                    if "already exists" in msg or "duplicate" in msg:
                        continue
                    raise
        t.execute("CHECKPOINT")
        t.execute("DETACH src")
        got = _count_map(t, dataset_id=dataset_id)
    finally:
        t.close()

    bad = [k for k in baseline if got[k] != baseline[k]]
    print("  dest parity:")
    for name, n in got.items():
        print(f"    {name}: {n:,}")
    if bad:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"dest row mismatch: {bad}")
    print(f"  wrote {dest}")
    return got


def drop_source_org_holding(
    *,
    src: Path,
    dest: Path,
    dataset_id: str = DATASET_ID,
) -> dict[str, int]:
    if not dest.is_file():
        raise FileNotFoundError(f"dest missing; copy first: {dest}")
    s = duck_connect(str(src), read_only=True)
    d = duck_connect(str(dest), read_only=True)
    try:
        src_counts = _count_map(s, dataset_id=dataset_id)
        dest_counts = _count_map(d, dataset_id=dataset_id)
    finally:
        s.close()
        d.close()
    bad = [k for k in src_counts if dest_counts[k] != src_counts[k]]
    if bad:
        raise RuntimeError(
            f"refuse drop: dest != src for {bad} src={src_counts} dest={dest_counts}"
        )

    w = duck_connect(str(src), read_only=False)
    try:
        w.execute("DELETE FROM ingest_batch WHERE dataset_id = ?", [dataset_id])
        w.execute("DELETE FROM accepted_partition WHERE dataset_id = ?", [dataset_id])
        for tab in DATA_TABLES:
            w.execute(f'DROP TABLE "{tab}"')
        w.execute("CHECKPOINT")
    finally:
        w.close()
    print("  dropped org tables + org ingest/accepted rows from smartmoney")
    return dest_counts


def run(*, execute: bool, drop_source: bool) -> int:
    src = _db_path("smartmoney")
    dest = _db_path("org_holding")
    try:
        if drop_source and dest.is_file() and not execute:
            drop_source_org_holding(src=src, dest=dest)
            return 0
        copy_org_holding(src=src, dest=dest, execute=execute)
        if execute and drop_source:
            drop_source_org_holding(src=src, dest=dest)
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="copy to the org_holding alias")
    ap.add_argument(
        "--drop-source",
        action="store_true",
        help="DROP org tables from smartmoney after dest parity",
    )
    args = ap.parse_args(argv)
    return run(execute=bool(args.execute), drop_source=bool(args.drop_source))


if __name__ == "__main__":
    raise SystemExit(main())
