from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ANALYSIS_DIR = ROOT / "analysis"
DEFAULT_RESEARCH_CACHE = ANALYSIS_DIR / "research_cache.duckdb"
DEFAULT_DB = ANALYSIS_DIR / "incremental_eval.duckdb"


def _latest_data_date(research_cache_path: Path) -> str:
    from scripts.research_cache_build import _latest_data_date as latest

    try:
        return latest()
    except Exception:
        if not research_cache_path.exists():
            raise
        with duckdb.connect(str(research_cache_path), read_only=True) as con:
            rows = con.execute(
                """
                SELECT data_latest_date
                FROM research_cache
                WHERE data_latest_date IS NOT NULL
                  AND data_latest_date <> ''
                GROUP BY data_latest_date
                ORDER BY data_latest_date DESC
                """
            ).fetchall()
        if len(rows) == 1 and rows[0][0]:
            return str(rows[0][0])
        raise


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS incremental_eval_state (
            stock_code VARCHAR,
            formula_id VARCHAR,
            params_hash VARCHAR,
            sell_rule VARCHAR,
            source_cache_key VARCHAR,
            source_type VARCHAR,
            last_eval_data_date VARCHAR,
            target_data_date VARCHAR,
            dirty_reason VARCHAR,
            status VARCHAR,
            updated_at VARCHAR,
            PRIMARY KEY (stock_code, formula_id, params_hash, sell_rule)
        )
        """
    )
    con.execute("CREATE TABLE IF NOT EXISTS cache_manifest (key VARCHAR PRIMARY KEY, value TEXT)")


def _sql_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_incremental_state(
    *,
    db_path: Path,
    research_cache_path: Path,
    replace: bool = True,
) -> dict[str, Any]:
    if not research_cache_path.exists():
        raise FileNotFoundError(f"research cache not found: {research_cache_path}")
    target_data_date = _latest_data_date(research_cache_path)
    generated_at = datetime.now(timezone.utc).isoformat()
    with duckdb.connect(str(db_path)) as con:
        _ensure_schema(con)
        if replace:
            con.execute("DELETE FROM incremental_eval_state")
            con.execute("DELETE FROM cache_manifest")
        con.execute(f"ATTACH {_sql_string(research_cache_path)} AS rc")
        con.execute(
            """
            INSERT OR REPLACE INTO incremental_eval_state
            SELECT
                stock_code,
                formula_id,
                params_hash,
                sell_rule,
                cache_key AS source_cache_key,
                source_type,
                data_latest_date AS last_eval_data_date,
                ? AS target_data_date,
                CASE
                    WHEN data_latest_date IS NULL OR data_latest_date = '' THEN 'missing_cache_data_date'
                    WHEN data_latest_date <> ? THEN 'market_data_date_changed'
                    ELSE ''
                END AS dirty_reason,
                CASE
                    WHEN data_latest_date IS NULL OR data_latest_date = '' THEN 'dirty'
                    WHEN data_latest_date <> ? THEN 'dirty'
                    ELSE 'clean'
                END AS status,
                ? AS updated_at
            FROM rc.research_cache
            WHERE stock_code IS NOT NULL
              AND formula_id IS NOT NULL
              AND params_hash IS NOT NULL
              AND sell_rule IS NOT NULL
            """,
            [target_data_date, target_data_date, target_data_date, generated_at],
        )
        con.execute("DETACH rc")
        con.execute("INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)", ("target_data_date", target_data_date))
        con.execute("INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)", ("generated_at", generated_at))
        row = con.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT stock_code) AS stock_count,
                SUM(CASE WHEN status = 'clean' THEN 1 ELSE 0 END) AS clean_count,
                SUM(CASE WHEN status = 'dirty' THEN 1 ELSE 0 END) AS dirty_count,
                COUNT(DISTINCT source_cache_key) AS source_cache_count
            FROM incremental_eval_state
            """
        ).fetchone()
        reason_rows = con.execute(
            """
            SELECT dirty_reason, COUNT(*)
            FROM incremental_eval_state
            WHERE dirty_reason <> ''
            GROUP BY dirty_reason
            ORDER BY COUNT(*) DESC, dirty_reason
            """
        ).fetchall()
    return {
        "db_path": str(db_path),
        "row_count": int(row[0] or 0),
        "stock_count": int(row[1] or 0),
        "clean_count": int(row[2] or 0),
        "dirty_count": int(row[3] or 0),
        "source_cache_count": int(row[4] or 0),
        "dirty_reason_counts": {str(k): int(v) for k, v in reason_rows},
        "target_data_date": target_data_date,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build incremental evaluator state from Research Cache.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--research-cache", type=Path, default=DEFAULT_RESEARCH_CACHE)
    parser.add_argument("--append", action="store_true", help="Append/replace by primary key instead of rebuilding state.")
    args = parser.parse_args()
    summary = build_incremental_state(
        db_path=args.db,
        research_cache_path=args.research_cache,
        replace=not args.append,
    )
    print(
        "incremental_eval_build: "
        f"rows={summary['row_count']} stocks={summary['stock_count']} "
        f"clean={summary['clean_count']} dirty={summary['dirty_count']} "
        f"source_cache={summary['source_cache_count']} target_data_date={summary['target_data_date']} "
        f"dirty_reasons={json.dumps(summary['dirty_reason_counts'], sort_keys=True)}"
    )


if __name__ == "__main__":
    main()
