#!/usr/bin/env python3
"""Batch reparse TDX F10 extra facts from already-captured raw F10 text."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.tdx_f10_extra_client import ensure_tables, sync_tdx_f10_extra_facts  # noqa: E402


DERIVED_TABLES = [
    "raw_tdx_f10_holder_count_history",
    "fact_holder_count_period",
    "fact_shareholder_trade_tdx_b",
    "fact_common_major_holder_stock",
    "fact_fund_holding_tdx_f10",
    "fact_controlling_shareholder",
    "raw_tdx_f10_extra_parse_status",
]


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
        LIMIT 1
        """,
        (table,),
    ).fetchone()
    return bool(row)


def _delete_derived_rows(conn: Any, stock_codes: list[str] | None) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table in DERIVED_TABLES:
        if not _table_exists(conn, table):
            continue
        before = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if stock_codes:
            placeholders = ",".join(["?"] * len(stock_codes))
            conn.execute(f'DELETE FROM "{table}" WHERE stock_code IN ({placeholders})', stock_codes)
        else:
            conn.execute(f'DELETE FROM "{table}"')
        after = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        deleted[table] = int(before - after)
    conn.commit()
    return deleted


def _raw_stock_codes(conn: Any, *, stock_codes: list[str] | None, limit: int) -> list[str]:
    params: list[Any] = []
    where = ["stock_code IS NOT NULL"]
    if stock_codes:
        placeholders = ",".join(["?"] * len(stock_codes))
        where.append(f"stock_code IN ({placeholders})")
        params.extend(stock_codes)
    sql = f"""
        SELECT DISTINCT stock_code
        FROM raw_tdx_f10_holder_research
        WHERE {' AND '.join(where)}
        ORDER BY stock_code
    """
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [row[0] for row in conn.execute(sql, params).fetchall()]


def _merge_stats(total: dict[str, Any], batch: dict[str, Any]) -> None:
    for key, value in batch.items():
        if isinstance(value, int):
            total[key] = int(total.get(key, 0)) + value
        elif key == "errors":
            total.setdefault("errors", []).extend(value or [])
        elif key == "status":
            continue
        else:
            total[key] = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-new", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stock-code", action="append", default=None)
    args = parser.parse_args()

    conn = get_conn(timeout=300)
    try:
        ensure_tables(conn)
        target_codes = _raw_stock_codes(conn, stock_codes=args.stock_code, limit=args.limit)
        print(json.dumps({"target_stock_count": len(target_codes)}, ensure_ascii=False))
        if args.force:
            deleted = _delete_derived_rows(conn, args.stock_code)
            print(json.dumps({"force_deleted": deleted}, ensure_ascii=False))

        total: dict[str, Any] = {"errors": []}
        batch_size = max(int(args.batch_size or 200), 1)
        only_new = bool(args.only_new and not args.force)
        for start in range(0, len(target_codes), batch_size):
            batch_codes = target_codes[start:start + batch_size]
            result = sync_tdx_f10_extra_facts(
                conn,
                stock_codes=batch_codes,
                only_new=only_new,
                limit=0,
            )
            _merge_stats(total, result)
            print(
                json.dumps(
                    {
                        "batch": start // batch_size + 1,
                        "stock_count": len(batch_codes),
                        "stats": result,
                    },
                    ensure_ascii=False,
                )
            )

        total["status"] = "partial" if total.get("errors") else (
            "completed_with_rejections" if total.get("fund_holding_rejected_rows") else "completed"
        )
        print(json.dumps({"total": total}, ensure_ascii=False))
        return 1 if total.get("errors") else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
