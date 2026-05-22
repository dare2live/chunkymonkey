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
DEFAULT_INCREMENTAL_EVAL = ANALYSIS_DIR / "incremental_eval.duckdb"
DEFAULT_DB = ANALYSIS_DIR / "drift_trigger.duckdb"


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS drift_trigger (
            stock_code VARCHAR,
            formula_id VARCHAR,
            params_hash VARCHAR,
            source_cache_key VARCHAR,
            check_date VARCHAR,
            latest_data_date VARCHAR,
            source_type VARCHAR,
            adoption_decision VARCHAR,
            signal_count INTEGER,
            validation_signal_count INTEGER,
            score DOUBLE,
            validation_score DOUBLE,
            score_delta DOUBLE,
            validation_score_delta DOUBLE,
            incremental_status VARCHAR,
            incremental_dirty_reason VARCHAR,
            drift_level VARCHAR,
            trigger_action VARCHAR,
            reason_json TEXT,
            created_at VARCHAR,
            PRIMARY KEY (stock_code, formula_id, params_hash, check_date)
        )
        """
    )
    con.execute("CREATE TABLE IF NOT EXISTS cache_manifest (key VARCHAR PRIMARY KEY, value TEXT)")


def _sql_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_drift_triggers(
    *,
    db_path: Path,
    research_cache_path: Path,
    incremental_eval_path: Path,
    min_signal_count: int = 3,
    min_validation_signal_count: int = 3,
    min_validation_score_delta: float = 0.0,
    replace: bool = True,
) -> dict[str, Any]:
    if not research_cache_path.exists():
        raise FileNotFoundError(f"research cache not found: {research_cache_path}")
    if not incremental_eval_path.exists():
        raise FileNotFoundError(f"incremental eval state not found: {incremental_eval_path}")
    generated_at = datetime.now(timezone.utc).isoformat()
    check_date = generated_at[:10]
    with duckdb.connect(str(db_path)) as con:
        _ensure_schema(con)
        if replace:
            con.execute("DELETE FROM drift_trigger")
            con.execute("DELETE FROM cache_manifest")
        con.execute(f"ATTACH {_sql_string(research_cache_path)} AS rc")
        con.execute(f"ATTACH {_sql_string(incremental_eval_path)} AS ie")
        target_row = con.execute("SELECT value FROM ie.cache_manifest WHERE key = 'target_data_date'").fetchone()
        latest_data_date = str(target_row[0]) if target_row and target_row[0] else ""
        con.execute(
            """
            INSERT OR REPLACE INTO drift_trigger
            SELECT
                rc.stock_code,
                rc.formula_id,
                rc.params_hash,
                rc.cache_key AS source_cache_key,
                ? AS check_date,
                ? AS latest_data_date,
                rc.source_type,
                rc.adoption_decision,
                rc.signal_count,
                rc.validation_signal_count,
                rc.score,
                rc.validation_score,
                rc.score_delta,
                rc.validation_score_delta,
                ie.status AS incremental_status,
                ie.dirty_reason AS incremental_dirty_reason,
                CASE
                    WHEN ie.status = 'dirty' THEN 'reevaluate'
                    WHEN rc.adoption_decision = 'candidate'
                         AND (COALESCE(rc.validation_signal_count, 0) < ? OR COALESCE(rc.validation_score_delta, 0) <= ?) THEN 'watch'
                    WHEN COALESCE(rc.signal_count, 0) < ? THEN 'watch'
                    ELSE 'none'
                END AS drift_level,
                CASE
                    WHEN ie.status = 'dirty' THEN 'run_incremental_eval'
                    WHEN rc.adoption_decision = 'candidate'
                         AND (COALESCE(rc.validation_signal_count, 0) < ? OR COALESCE(rc.validation_score_delta, 0) <= ?) THEN 'watch_candidate'
                    WHEN COALESCE(rc.signal_count, 0) < ? THEN 'watch_low_signal'
                    ELSE 'none'
                END AS trigger_action,
                CASE
                    WHEN ie.status = 'dirty' THEN json_object('reason', ie.dirty_reason, 'source', 'incremental_eval')
                    WHEN rc.adoption_decision = 'candidate'
                         AND COALESCE(rc.validation_signal_count, 0) < ? THEN json_object('reason', 'candidate_validation_signal_count_below_threshold', 'threshold', ?)
                    WHEN rc.adoption_decision = 'candidate'
                         AND COALESCE(rc.validation_score_delta, 0) <= ? THEN json_object('reason', 'candidate_validation_delta_not_positive', 'threshold', ?)
                    WHEN COALESCE(rc.signal_count, 0) < ? THEN json_object('reason', 'signal_count_below_threshold', 'threshold', ?)
                    ELSE json_object('reason', 'none')
                END AS reason_json,
                ? AS created_at
            FROM rc.research_cache rc
            JOIN ie.incremental_eval_state ie
              ON rc.cache_key = ie.source_cache_key
            """,
            [
                check_date,
                latest_data_date,
                min_validation_signal_count,
                min_validation_score_delta,
                min_signal_count,
                min_validation_signal_count,
                min_validation_score_delta,
                min_signal_count,
                min_validation_signal_count,
                min_validation_signal_count,
                min_validation_score_delta,
                min_validation_score_delta,
                min_signal_count,
                min_signal_count,
                generated_at,
            ],
        )
        con.execute("DETACH rc")
        con.execute("DETACH ie")
        con.execute("INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)", ("check_date", check_date))
        con.execute("INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)", ("latest_data_date", latest_data_date))
        con.execute("INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)", ("generated_at", generated_at))
        con.execute(
            "INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)",
            (
                "thresholds",
                json.dumps(
                    {
                        "min_signal_count": min_signal_count,
                        "min_validation_signal_count": min_validation_signal_count,
                        "min_validation_score_delta": min_validation_score_delta,
                    },
                    sort_keys=True,
                ),
            ),
        )
        row = con.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT stock_code) AS stock_count,
                SUM(CASE WHEN drift_level = 'none' THEN 1 ELSE 0 END) AS none_count,
                SUM(CASE WHEN drift_level = 'watch' THEN 1 ELSE 0 END) AS watch_count,
                SUM(CASE WHEN drift_level = 'reevaluate' THEN 1 ELSE 0 END) AS reevaluate_count,
                SUM(CASE WHEN drift_level = 'reoptimize' THEN 1 ELSE 0 END) AS reoptimize_count,
                SUM(CASE WHEN drift_level = 'disable_candidate' THEN 1 ELSE 0 END) AS disable_candidate_count
            FROM drift_trigger
            """
        ).fetchone()
        action_rows = con.execute(
            """
            SELECT trigger_action, COUNT(*)
            FROM drift_trigger
            GROUP BY trigger_action
            ORDER BY COUNT(*) DESC, trigger_action
            """
        ).fetchall()
    return {
        "db_path": str(db_path),
        "row_count": int(row[0] or 0),
        "stock_count": int(row[1] or 0),
        "none_count": int(row[2] or 0),
        "watch_count": int(row[3] or 0),
        "reevaluate_count": int(row[4] or 0),
        "reoptimize_count": int(row[5] or 0),
        "disable_candidate_count": int(row[6] or 0),
        "action_counts": {str(k): int(v) for k, v in action_rows},
        "check_date": check_date,
        "latest_data_date": latest_data_date,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build drift trigger state from Research Cache and Incremental Eval state.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--research-cache", type=Path, default=DEFAULT_RESEARCH_CACHE)
    parser.add_argument("--incremental-eval", type=Path, default=DEFAULT_INCREMENTAL_EVAL)
    parser.add_argument("--min-signal-count", type=int, default=3)
    parser.add_argument("--min-validation-signal-count", type=int, default=3)
    parser.add_argument("--min-validation-score-delta", type=float, default=0.0)
    parser.add_argument("--append", action="store_true", help="Append/replace by primary key instead of rebuilding state.")
    args = parser.parse_args()
    summary = build_drift_triggers(
        db_path=args.db,
        research_cache_path=args.research_cache,
        incremental_eval_path=args.incremental_eval,
        min_signal_count=args.min_signal_count,
        min_validation_signal_count=args.min_validation_signal_count,
        min_validation_score_delta=args.min_validation_score_delta,
        replace=not args.append,
    )
    print(
        "drift_trigger_build: "
        f"rows={summary['row_count']} stocks={summary['stock_count']} "
        f"none={summary['none_count']} watch={summary['watch_count']} "
        f"reevaluate={summary['reevaluate_count']} reoptimize={summary['reoptimize_count']} "
        f"disable_candidate={summary['disable_candidate_count']} latest_data_date={summary['latest_data_date']} "
        f"actions={json.dumps(summary['action_counts'], sort_keys=True)}"
    )


if __name__ == "__main__":
    main()
