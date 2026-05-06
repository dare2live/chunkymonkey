#!/usr/bin/env python3
"""Prune feature-panel rows that no longer map to valid canonical K-line rows."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.analytics import duck_connection  # noqa: E402
from services.market_db import canonical_kline_daily_qfq_sql  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DEFAULT_FEATURE_TABLES = ["fact_feature_panel", "fact_feature_panel_candidate"]

DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_panel_prune_run (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_count BIGINT NOT NULL,
    missing_signal_count BIGINT NOT NULL,
    pruned_count BIGINT NOT NULL,
    after_count BIGINT NOT NULL,
    min_date TEXT,
    max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table)
);
CREATE INDEX IF NOT EXISTS idx_feature_panel_prune_table
    ON mart_feature_panel_prune_run(feature_table, built_at);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        return row[index]


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name.rsplit(".", 1)[-1],),
    ).fetchone()
    return row is not None


def _where_clause(start_date: str | None, end_date: str | None, *, alias: str = "t") -> str:
    filters: list[str] = []
    if start_date:
        filters.append(f"{alias}.date >= ?")
    if end_date:
        filters.append(f"{alias}.date <= ?")
    return (" AND " + " AND ".join(filters)) if filters else ""


def _date_params(start_date: str | None, end_date: str | None) -> list[str]:
    params: list[str] = []
    if start_date:
        params.append(start_date)
    if end_date:
        params.append(end_date)
    return params


def _count_rows(conn: Any, relation: str, start_date: str | None, end_date: str | None) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM {relation} AS t
         WHERE 1=1 {_where_clause(start_date, end_date)}
        """,
        _date_params(start_date, end_date),
    ).fetchone()
    return int(_row_value(row, "n", 0) or 0)


def prune_feature_panel_to_canonical_kline(
    conn: Any,
    *,
    feature_tables: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    run_id: str | None = None,
    reason: str = "canonical_kline_valid_price_contract",
) -> dict[str, Any]:
    feature_tables = feature_tables or DEFAULT_FEATURE_TABLES[:]
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    run_id = run_id or f"feature_panel_prune_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    _execute_script(conn, DDL)
    kline_sql = canonical_kline_daily_qfq_sql()
    summaries: list[dict[str, Any]] = []
    for feature_table in feature_tables:
        if not _table_exists(conn, feature_table):
            raise RuntimeError(f"feature table missing: {feature_table}")
        relation = _quote_relation(feature_table)
        before_count = _count_rows(conn, relation, start_date, end_date)
        row = conn.execute(
            f"""
            WITH k AS ({kline_sql})
            SELECT COUNT(*) AS missing_signal_count,
                   MIN(CAST(t.date AS TEXT)) AS min_date,
                   MAX(CAST(t.date AS TEXT)) AS max_date
              FROM {relation} AS t
              LEFT JOIN k
                ON t.stock_code = k.code
               AND CAST(t.date AS TEXT) = CAST(k.date AS TEXT)
             WHERE k.code IS NULL
               {_where_clause(start_date, end_date)}
            """,
            _date_params(start_date, end_date),
        ).fetchone()
        missing_signal_count = int(_row_value(row, "missing_signal_count", 0) or 0)
        min_date = _row_value(row, "min_date", 1)
        max_date = _row_value(row, "max_date", 2)
        conn.execute(
            f"""
            DELETE FROM {relation} AS t
             WHERE NOT EXISTS (
                    SELECT 1
                      FROM ({kline_sql}) AS k
                     WHERE k.code = t.stock_code
                       AND CAST(k.date AS TEXT) = CAST(t.date AS TEXT)
                   )
               {_where_clause(start_date, end_date)}
            """,
            _date_params(start_date, end_date),
        )
        after_count = _count_rows(conn, relation, start_date, end_date)
        pruned_count = before_count - after_count
        summary = {
            "feature_table": feature_table,
            "before_count": before_count,
            "missing_signal_count": missing_signal_count,
            "pruned_count": pruned_count,
            "after_count": after_count,
            "min_date": min_date,
            "max_date": max_date,
        }
        summaries.append(summary)
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_feature_panel_prune_run (
                run_id, feature_table, reason, before_count, missing_signal_count,
                pruned_count, after_count, min_date, max_date, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                feature_table,
                reason,
                before_count,
                missing_signal_count,
                pruned_count,
                after_count,
                min_date,
                max_date,
                built_at,
            ),
        )
        try:
            record_actual_version(conn, feature_table.rsplit(".", 1)[-1])
        except Exception:
            pass
    record_actual_version(conn, "mart_feature_panel_prune_run")
    return {
        "run_id": run_id,
        "reason": reason,
        "feature_tables": summaries,
        "built_at": built_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-table", action="append", dest="feature_tables")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    with duck_connection(writable=True) as conn:
        result = prune_feature_panel_to_canonical_kline(
            conn,
            feature_tables=args.feature_tables,
            start_date=args.start_date,
            end_date=args.end_date,
            run_id=args.run_id,
        )
        conn.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
