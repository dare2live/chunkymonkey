#!/usr/bin/env python3
"""Install K1 serve-read identity views into a DuckDB file.

Run once after deploy; views live in the DuckDB file (gitignored), DDL lives
in git (``backend/config/serve_read_views.yaml``). Does not rebuild
``v_sw_industry_pit``.

  PYTHONPATH=backend python backend/scripts/ensure_serve_read_views.py
  PYTHONPATH=backend python backend/scripts/ensure_serve_read_views.py --db tushare_raw
  PYTHONPATH=backend python backend/scripts/ensure_serve_read_views.py --verify
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.serve_read_views import (  # noqa: E402
    ensure_serve_read_views,
    iter_specs_for_db,
)


def _connect(alias: str, *, read_only: bool):
    path = get_database_manifest().path_for(alias)
    return connect(str(path), read_only=read_only)


def _qualify_existing(conn, name: str) -> str | None:
    rows = conn.execute(
        """
        SELECT table_schema
        FROM information_schema.tables
        WHERE table_name = ? AND table_type = 'VIEW'
        ORDER BY CASE table_schema WHEN 'main' THEN 0 WHEN 'tr' THEN 1 ELSE 2 END
        """,
        [name],
    ).fetchall()
    if not rows:
        return None
    schema = str(rows[0][0])
    if schema == "main":
        return f'"{name}"'
    return f'"{schema}"."{name}"'


def verify(conn, *, db: str) -> int:
    bad = 0
    for spec in iter_specs_for_db(db):
        qname = _qualify_existing(conn, spec.name)
        if qname is None:
            print(f"FAIL {spec.name}: view missing", file=sys.stderr)
            bad += 1
            continue
        try:
            desc = conn.execute(f"DESCRIBE {qname}").fetchall()
            n = conn.execute(f"SELECT count(*) FROM {qname}").fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — verify must fail closed
            print(f"FAIL {spec.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            bad += 1
            continue
        print(f"OK {spec.name}: {len(desc)} cols, {n} rows")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="tushare_raw", help="database_manifest alias (default tushare_raw)")
    ap.add_argument("--verify", action="store_true", help="only DESCRIBE/count; do not CREATE")
    args = ap.parse_args(argv)
    if args.verify:
        conn = _connect(args.db, read_only=True)
        try:
            return verify(conn, db=args.db)
        finally:
            conn.close()
    conn = _connect(args.db, read_only=False)
    try:
        installed = ensure_serve_read_views(conn, db=args.db)
    finally:
        conn.close()
    print(f"[done] ensure_serve_read_views db={args.db} installed={len(installed)}")
    for name in installed:
        print(f"  {name}")
    skipped = [s.name for s in iter_specs_for_db(args.db) if s.name not in set(installed)]
    if skipped:
        print(f"[skip] source missing: {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
