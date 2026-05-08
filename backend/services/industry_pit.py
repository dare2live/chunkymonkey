"""PIT-safe industry membership governance."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.schema_versions import record_actual_version


REPO = Path(__file__).resolve().parent.parent.parent
PIT_TABLE = "mart_stock_industry_pit"
QUALITY_TABLE = "mart_industry_pit_quality"
HISTORY_TABLE = "dim_stock_tdx_industry_history"
CURRENT_TABLE = "dim_stock_tdx_industry"
FAR_FUTURE = "9999-12-31"
DEFAULT_FALLBACK_START = "1900-01-01"

DDL = f"""
CREATE TABLE IF NOT EXISTS {PIT_TABLE} (
    stock_code TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT NOT NULL,
    tdx_l1 TEXT,
    tdx_l2 TEXT,
    tdx_l3 TEXT,
    tdx_l1_name TEXT,
    tdx_l2_name TEXT,
    tdx_l3_name TEXT,
    source TEXT NOT NULL,
    source_snapshot_date TEXT,
    confidence_level TEXT NOT NULL,
    is_historical_pit BOOLEAN NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (stock_code, effective_from, effective_to, source)
);
CREATE INDEX IF NOT EXISTS idx_stock_industry_pit_lookup
    ON {PIT_TABLE}(stock_code, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS {QUALITY_TABLE} (
    run_id TEXT PRIMARY KEY,
    signal_table TEXT NOT NULL,
    signal_stock_column TEXT NOT NULL,
    signal_date_column TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    signal_row_count BIGINT,
    signal_stock_count BIGINT,
    signal_date_count BIGINT,
    min_signal_date TEXT,
    max_signal_date TEXT,
    pit_row_count BIGINT,
    pit_stock_count BIGINT,
    history_snapshot_count BIGINT,
    history_min_snapshot_date TEXT,
    history_max_snapshot_date TEXT,
    matched_signal_rows BIGINT,
    observed_pit_signal_rows BIGINT,
    fallback_signal_rows BIGINT,
    missing_pit_rows BIGINT,
    missing_tdx_l1_rows BIGINT,
    fallback_ratio DOUBLE,
    missing_ratio DOUBLE,
    pit_eligible BOOLEAN NOT NULL,
    blockers_json TEXT,
    stage_timings_json TEXT,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_industry_pit_quality_built
    ON {QUALITY_TABLE}(built_at DESC);
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


def _row_value(row: Any, key: str, idx: int) -> Any:
    try:
        return row[key]
    except Exception:
        return row[idx]


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {_quote_relation(table_name)} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(_row_value(row, "column_name", 0))
        for row in conn.execute(f"DESCRIBE {_quote_relation(table_name)}").fetchall()
    }


def ensure_industry_pit_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _build_pit_rows(conn: Any, *, fallback_start: str, built_at: str) -> dict[str, Any]:
    ensure_industry_pit_tables(conn)
    conn.execute(f"DELETE FROM {PIT_TABLE}")

    has_history = _table_exists(conn, HISTORY_TABLE)
    has_current = _table_exists(conn, CURRENT_TABLE)
    if has_history:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {PIT_TABLE} (
                stock_code, effective_from, effective_to,
                tdx_l1, tdx_l2, tdx_l3,
                tdx_l1_name, tdx_l2_name, tdx_l3_name,
                source, source_snapshot_date, confidence_level,
                is_historical_pit, built_at
            )
            WITH hist AS (
                SELECT stock_code,
                       CAST(snapshot_date AS DATE) AS snapshot_date,
                       tdx_l1, tdx_l2, tdx_l3,
                       tdx_l1_name, tdx_l2_name, tdx_l3_name
                  FROM {HISTORY_TABLE}
                 WHERE stock_code IS NOT NULL
                   AND snapshot_date IS NOT NULL
            ),
            ranged AS (
                SELECT *,
                       LEAD(snapshot_date) OVER (
                           PARTITION BY stock_code ORDER BY snapshot_date
                       ) AS next_snapshot_date
                  FROM hist
            )
            SELECT stock_code,
                   CAST(snapshot_date AS VARCHAR) AS effective_from,
                   STRFTIME(COALESCE(next_snapshot_date - INTERVAL 1 DAY, DATE '{FAR_FUTURE}'), '%Y-%m-%d')
                       AS effective_to,
                   tdx_l1, tdx_l2, tdx_l3,
                   tdx_l1_name, tdx_l2_name, tdx_l3_name,
                   'tdx_industry_history_snapshot' AS source,
                   CAST(snapshot_date AS VARCHAR) AS source_snapshot_date,
                   'observed_snapshot' AS confidence_level,
                   TRUE AS is_historical_pit,
                   ? AS built_at
              FROM ranged
            """,
            (built_at,),
        )

    if has_current:
        if has_history:
            min_snapshot_expr = (
                f"(SELECT MIN(CAST(snapshot_date AS DATE)) FROM {HISTORY_TABLE} "
                "WHERE snapshot_date IS NOT NULL)"
            )
            has_hist_expr = (
                f"EXISTS (SELECT 1 FROM {HISTORY_TABLE} h "
                f"WHERE h.stock_code = c.stock_code AND h.snapshot_date IS NOT NULL)"
            )
        else:
            min_snapshot_expr = "NULL"
            has_hist_expr = "FALSE"
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {PIT_TABLE} (
                stock_code, effective_from, effective_to,
                tdx_l1, tdx_l2, tdx_l3,
                tdx_l1_name, tdx_l2_name, tdx_l3_name,
                source, source_snapshot_date, confidence_level,
                is_historical_pit, built_at
            )
            SELECT c.stock_code,
                   ? AS effective_from,
                   STRFTIME(
                       CASE
                         WHEN ({has_hist_expr}) AND ({min_snapshot_expr}) IS NOT NULL
                           THEN ({min_snapshot_expr}) - INTERVAL 1 DAY
                         ELSE DATE '{FAR_FUTURE}'
                       END,
                       '%Y-%m-%d'
                   ) AS effective_to,
                   c.tdx_l1, c.tdx_l2, c.tdx_l3,
                   c.tdx_l1_name, c.tdx_l2_name, c.tdx_l3_name,
                   'current_label_fallback' AS source,
                   NULL AS source_snapshot_date,
                   'current_label_fallback' AS confidence_level,
                   FALSE AS is_historical_pit,
                   ? AS built_at
              FROM {CURRENT_TABLE} c
             WHERE c.stock_code IS NOT NULL
            """,
            (fallback_start, built_at),
        )
        conn.execute(
            f"""
            DELETE FROM {PIT_TABLE}
             WHERE source = 'current_label_fallback'
               AND CAST(effective_to AS DATE) < CAST(effective_from AS DATE)
            """
        )

    pit_row = conn.execute(
        f"""
        SELECT COUNT(*) AS pit_rows,
               COUNT(DISTINCT stock_code) AS pit_stocks,
               SUM(CASE WHEN source = 'current_label_fallback' THEN 1 ELSE 0 END) AS fallback_rows,
               SUM(CASE WHEN is_historical_pit THEN 1 ELSE 0 END) AS observed_rows
          FROM {PIT_TABLE}
        """
    ).fetchone()
    hist_row = (
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT snapshot_date) AS snapshot_count,
                   MIN(snapshot_date) AS min_snapshot_date,
                   MAX(snapshot_date) AS max_snapshot_date
              FROM {HISTORY_TABLE}
            """
        ).fetchone()
        if has_history
        else None
    )
    return {
        "pit_rows": int(_row_value(pit_row, "pit_rows", 0) or 0),
        "pit_stocks": int(_row_value(pit_row, "pit_stocks", 1) or 0),
        "fallback_rows": int(_row_value(pit_row, "fallback_rows", 2) or 0),
        "observed_rows": int(_row_value(pit_row, "observed_rows", 3) or 0),
        "history_snapshot_count": int(_row_value(hist_row, "snapshot_count", 0) or 0) if hist_row else 0,
        "history_min_snapshot_date": _row_value(hist_row, "min_snapshot_date", 1) if hist_row else None,
        "history_max_snapshot_date": _row_value(hist_row, "max_snapshot_date", 2) if hist_row else None,
        "has_history": has_history,
        "has_current": has_current,
    }


def _build_signal_scope(
    conn: Any,
    *,
    signal_table: str,
    signal_stock_column: str,
    signal_date_column: str,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    if not _table_exists(conn, signal_table):
        return {"exists": False, "blocker": "missing_signal_table"}
    cols = _columns(conn, signal_table)
    missing_cols = [
        col for col in (signal_stock_column, signal_date_column) if col not in cols
    ]
    if missing_cols:
        return {"exists": True, "blocker": "missing_signal_columns", "missing_columns": missing_cols}

    filters: list[str] = [
        f"{_quote_ident(signal_stock_column)} IS NOT NULL",
        f"{_quote_ident(signal_date_column)} IS NOT NULL",
    ]
    params: list[Any] = []
    if start_date:
        filters.append(f"CAST({_quote_ident(signal_date_column)} AS DATE) >= CAST(? AS DATE)")
        params.append(start_date)
    if end_date:
        filters.append(f"CAST({_quote_ident(signal_date_column)} AS DATE) <= CAST(? AS DATE)")
        params.append(end_date)
    where_sql = " AND ".join(filters)
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE tmp_industry_pit_signal_scope AS
        SELECT DISTINCT
               CAST({_quote_ident(signal_stock_column)} AS VARCHAR) AS stock_code,
               CAST(CAST({_quote_ident(signal_date_column)} AS DATE) AS VARCHAR) AS signal_date
          FROM {_quote_relation(signal_table)}
         WHERE {where_sql}
        """,
        params,
    )
    return {"exists": True, "blocker": None}


def _quality_from_signal_scope(
    conn: Any,
    *,
    run_id: str,
    signal_table: str,
    signal_stock_column: str,
    signal_date_column: str,
    start_date: str | None,
    end_date: str | None,
    pit_build: dict[str, Any],
    stage_timings: dict[str, float],
    built_at: str,
) -> dict[str, Any]:
    scope = _build_signal_scope(
        conn,
        signal_table=signal_table,
        signal_stock_column=signal_stock_column,
        signal_date_column=signal_date_column,
        start_date=start_date,
        end_date=end_date,
    )
    blockers: list[str] = []
    if scope.get("blocker"):
        blockers.append(str(scope["blocker"]))

    if blockers:
        metrics = {
            "signal_row_count": 0,
            "signal_stock_count": 0,
            "signal_date_count": 0,
            "min_signal_date": None,
            "max_signal_date": None,
            "matched_signal_rows": 0,
            "observed_pit_signal_rows": 0,
            "fallback_signal_rows": 0,
            "missing_pit_rows": 0,
            "missing_tdx_l1_rows": 0,
            "fallback_ratio": None,
            "missing_ratio": None,
        }
    else:
        row = conn.execute(
            f"""
            WITH resolved AS (
                SELECT stock_code,
                       signal_date,
                       source,
                       tdx_l1,
                       tdx_l1_name,
                       is_historical_pit,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, signal_date
                           ORDER BY is_historical_pit DESC, effective_from DESC
                       ) AS rn
                  FROM (
                    SELECT s.stock_code,
                           s.signal_date,
                           p.source,
                           p.tdx_l1,
                           p.tdx_l1_name,
                           p.is_historical_pit,
                           p.effective_from
                      FROM tmp_industry_pit_signal_scope s
                      LEFT JOIN {PIT_TABLE} p
                        ON p.stock_code = s.stock_code
                       AND CAST(s.signal_date AS DATE) >= CAST(p.effective_from AS DATE)
                       AND CAST(s.signal_date AS DATE) <= CAST(p.effective_to AS DATE)
                  )
            )
            SELECT COUNT(*) AS signal_row_count,
                   COUNT(DISTINCT stock_code) AS signal_stock_count,
                   COUNT(DISTINCT signal_date) AS signal_date_count,
                   MIN(signal_date) AS min_signal_date,
                   MAX(signal_date) AS max_signal_date,
                   SUM(CASE WHEN source IS NOT NULL THEN 1 ELSE 0 END) AS matched_signal_rows,
                   SUM(CASE WHEN is_historical_pit THEN 1 ELSE 0 END) AS observed_pit_signal_rows,
                   SUM(CASE WHEN source = 'current_label_fallback' THEN 1 ELSE 0 END) AS fallback_signal_rows,
                   SUM(CASE WHEN source IS NULL THEN 1 ELSE 0 END) AS missing_pit_rows,
                   SUM(CASE WHEN source IS NULL OR tdx_l1 IS NULL OR TRIM(CAST(tdx_l1 AS VARCHAR)) = ''
                            THEN 1 ELSE 0 END) AS missing_tdx_l1_rows
              FROM resolved
             WHERE rn = 1
            """
        ).fetchone()
        signal_rows = int(_row_value(row, "signal_row_count", 0) or 0)
        fallback_rows = int(_row_value(row, "fallback_signal_rows", 7) or 0)
        missing_rows = int(_row_value(row, "missing_pit_rows", 8) or 0)
        missing_tdx_l1_rows = int(_row_value(row, "missing_tdx_l1_rows", 9) or 0)
        if signal_rows <= 0:
            blockers.append("empty_signal_scope")
        if fallback_rows > 0:
            blockers.append("industry_current_label_fallback_in_signal_window")
        if missing_rows > 0:
            blockers.append("missing_industry_pit_rows")
        if missing_tdx_l1_rows > 0:
            blockers.append("missing_tdx_l1_in_resolved_pit")
        metrics = {
            "signal_row_count": signal_rows,
            "signal_stock_count": int(_row_value(row, "signal_stock_count", 1) or 0),
            "signal_date_count": int(_row_value(row, "signal_date_count", 2) or 0),
            "min_signal_date": _row_value(row, "min_signal_date", 3),
            "max_signal_date": _row_value(row, "max_signal_date", 4),
            "matched_signal_rows": int(_row_value(row, "matched_signal_rows", 5) or 0),
            "observed_pit_signal_rows": int(_row_value(row, "observed_pit_signal_rows", 6) or 0),
            "fallback_signal_rows": fallback_rows,
            "missing_pit_rows": missing_rows,
            "missing_tdx_l1_rows": missing_tdx_l1_rows,
            "fallback_ratio": fallback_rows / signal_rows if signal_rows else None,
            "missing_ratio": missing_rows / signal_rows if signal_rows else None,
        }

    pit_eligible = not blockers
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {QUALITY_TABLE} (
            run_id, signal_table, signal_stock_column, signal_date_column,
            window_start, window_end,
            signal_row_count, signal_stock_count, signal_date_count,
            min_signal_date, max_signal_date,
            pit_row_count, pit_stock_count,
            history_snapshot_count, history_min_snapshot_date, history_max_snapshot_date,
            matched_signal_rows, observed_pit_signal_rows, fallback_signal_rows,
            missing_pit_rows, missing_tdx_l1_rows,
            fallback_ratio, missing_ratio, pit_eligible,
            blockers_json, stage_timings_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            signal_table,
            signal_stock_column,
            signal_date_column,
            start_date,
            end_date,
            metrics["signal_row_count"],
            metrics["signal_stock_count"],
            metrics["signal_date_count"],
            metrics["min_signal_date"],
            metrics["max_signal_date"],
            pit_build["pit_rows"],
            pit_build["pit_stocks"],
            pit_build["history_snapshot_count"],
            pit_build["history_min_snapshot_date"],
            pit_build["history_max_snapshot_date"],
            metrics["matched_signal_rows"],
            metrics["observed_pit_signal_rows"],
            metrics["fallback_signal_rows"],
            metrics["missing_pit_rows"],
            metrics["missing_tdx_l1_rows"],
            metrics["fallback_ratio"],
            metrics["missing_ratio"],
            pit_eligible,
            json.dumps(blockers, ensure_ascii=False, sort_keys=True),
            json.dumps(stage_timings, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return {
        "run_id": run_id,
        "pit_eligible": pit_eligible,
        "blockers": blockers,
        **metrics,
        **pit_build,
    }


def build_industry_pit(
    conn: Any,
    *,
    run_id: str | None = None,
    signal_table: str = "mart_shareholder_plan_initial_feature_panel",
    signal_stock_column: str = "stock_code",
    signal_date_column: str = "date",
    start_date: str | None = None,
    end_date: str | None = None,
    fallback_start: str = DEFAULT_FALLBACK_START,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = time.perf_counter()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    run_id = run_id or f"industry_pit_{built_at.replace(':', '').replace('-', '')}"
    stage_timings: dict[str, float] = {}

    stage_started = time.perf_counter()
    pit_build = _build_pit_rows(conn, fallback_start=fallback_start, built_at=built_at)
    stage_timings["build_pit_rows_s"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    quality = _quality_from_signal_scope(
        conn,
        run_id=run_id,
        signal_table=signal_table,
        signal_stock_column=signal_stock_column,
        signal_date_column=signal_date_column,
        start_date=start_date,
        end_date=end_date,
        pit_build=pit_build,
        stage_timings=stage_timings,
        built_at=built_at,
    )
    stage_timings["quality_scan_s"] = round(time.perf_counter() - stage_started, 3)
    duration_s = time.perf_counter() - started
    stage_timings["total_s"] = round(duration_s, 3)

    conn.execute(
        f"UPDATE {QUALITY_TABLE} SET stage_timings_json = ? WHERE run_id = ?",
        (json.dumps(stage_timings, ensure_ascii=False, sort_keys=True), run_id),
    )
    record_actual_version(conn, PIT_TABLE)
    record_actual_version(conn, QUALITY_TABLE)
    ended_at = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_industry_pit",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=[CURRENT_TABLE, HISTORY_TABLE, signal_table],
        output_tables=[PIT_TABLE, QUALITY_TABLE],
        gate_result="pass" if quality["pit_eligible"] else "blocked",
        blockers=quality["blockers"],
        perf_summary={
            "stage_timings": stage_timings,
            "signal_table": signal_table,
            "pit_eligible": quality["pit_eligible"],
            "fallback_ratio": quality.get("fallback_ratio"),
            "missing_ratio": quality.get("missing_ratio"),
        },
    )
    try:
        conn.commit()
    except Exception:
        pass
    return {**quality, "duration_s": round(duration_s, 3), "stage_timings": stage_timings}
