"""Evaluate shareholder-plan latest-state and initial-event feature families."""
from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Any

from services.schema_versions import record_actual_version


EVAL_TABLE = "mart_shareholder_plan_feature_family_eval"
DEFAULT_PANEL_TABLE = "fact_feature_panel"
DEFAULT_LABELS = (
    "follow_net_return_5d",
    "follow_net_return_10d",
    "follow_net_return_20d",
    "follow_net_return_60d",
    "follow_net_return_90d",
)

DDL = f"""
CREATE TABLE IF NOT EXISTS {EVAL_TABLE} (
    run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    source_family TEXT NOT NULL,
    source_table TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_purpose TEXT NOT NULL,
    label_name TEXT NOT NULL,
    window_days INTEGER,
    total_rows BIGINT,
    valid_rows BIGINT,
    coverage_pct DOUBLE,
    nondefault_rows BIGINT,
    nondefault_pct DOUBLE,
    event_rows BIGINT,
    distinct_event_stocks BIGINT,
    ic DOUBLE,
    rank_ic DOUBLE,
    rank_ic_std_by_date DOUBLE,
    daily_rank_ic_count INTEGER,
    positive_rank_ic_share DOUBLE,
    feature_mean DOUBLE,
    label_mean_when_active DOUBLE,
    label_mean_when_inactive DOUBLE,
    active_inactive_label_spread DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_family, feature_name, label_name)
);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_family_eval_run
    ON {EVAL_TABLE}(run_id);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_family_eval_rank
    ON {EVAL_TABLE}(label_name, rank_ic);
"""


FAMILIES = (
    {
        "source_family": "latest_state",
        "source_table": "fact_shareholder_plan_tdx_f10",
        "progress_column": "progress",
        "feature_purpose": "current_latest_state_context",
        "include_completed": True,
    },
    {
        "source_family": "initial_event",
        "source_table": "mart_shareholder_plan_initial_event",
        "progress_column": None,
        "feature_purpose": "initial_notice_capital_attention_candidate",
        "include_completed": False,
    },
)

BASE_FEATURES = (
    ("shareholder_plan_increase_count_180d", "increase_count_180d", 180, "increase"),
    ("shareholder_plan_decrease_count_180d", "decrease_count_180d", 180, "decrease"),
    ("shareholder_plan_increase_amount_max_180d", "increase_amount_max_180d", 180, "increase"),
    ("shareholder_plan_decrease_amount_max_180d", "decrease_amount_max_180d", 180, "decrease"),
    ("days_since_shareholder_plan_increase", "days_since_increase", None, "increase"),
    ("days_since_shareholder_plan_decrease", "days_since_decrease", None, "decrease"),
)
COMPLETED_FEATURE = ("shareholder_plan_completed_count_180d", "completed_count_180d", 180, "completed")


def _progress(message: str) -> None:
    print(f"[shareholder_plan_family_eval] {datetime.now(UTC).isoformat()} {message}", flush=True)


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
    try:
        return row[key]
    except Exception:
        return row[index] if row is not None else None


def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {_quote_relation(table_name)} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(f"DESCRIBE {_quote_relation(table_name)}").fetchall()
    return {str(_row_value(row, "column_name", 0)) for row in rows}


def _compact_date_expr(column_expr: str) -> str:
    return (
        "substr("
        f"regexp_replace(COALESCE(CAST({column_expr} AS VARCHAR), ''), '[^0-9]', '', 'g'), "
        "1, 8)"
    )


def _date_expr(column_expr: str) -> str:
    compact = _compact_date_expr(column_expr)
    iso = (
        f"substr({compact},1,4) || '-' || "
        f"substr({compact},5,2) || '-' || "
        f"substr({compact},7,2)"
    )
    return f"CASE WHEN length({compact}) = 8 THEN TRY_CAST({iso} AS DATE) ELSE NULL END"


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def ensure_shareholder_plan_feature_family_eval_table(conn: Any) -> None:
    _execute_script(conn, DDL)


def _prepare_panel(
    conn: Any,
    *,
    panel_table: str,
    labels: list[str],
    start_date: str | None,
    end_date: str | None,
) -> int:
    label_select = ", ".join(_quote_ident(label) for label in labels)
    filters = ["stock_code IS NOT NULL", "date IS NOT NULL", f"{_date_expr('date')} IS NOT NULL"]
    params: list[Any] = []
    if start_date:
        filters.append(f"{_date_expr('date')} >= TRY_CAST(? AS DATE)")
        params.append(start_date)
    if end_date:
        filters.append(f"{_date_expr('date')} <= TRY_CAST(? AS DATE)")
        params.append(end_date)
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_panel")
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_shareholder_plan_family_panel AS
        SELECT stock_code,
               CAST(date AS VARCHAR) AS date,
               {_date_expr('date')} AS date_dt,
               {label_select}
          FROM {_quote_relation(panel_table)}
         WHERE {' AND '.join(filters)}
        """,
        params,
    )
    row = conn.execute("SELECT COUNT(*) AS n FROM tmp_shareholder_plan_family_panel").fetchone()
    return int(_row_value(row, "n", 0) or 0)


def _source_value_expr(columns: set[str]) -> str:
    amount_terms = []
    for column in ("target_amount_max", "target_amount_min"):
        if column in columns:
            amount_terms.append(f"TRY_CAST({_quote_ident(column)} AS DOUBLE)")
    if not amount_terms:
        return "0.0"
    return f"COALESCE({', '.join(amount_terms)}, 0.0)"


def _prepare_family_features(conn: Any, family: dict[str, Any]) -> dict[str, Any]:
    source_table = str(family["source_table"])
    columns = _table_columns(conn, source_table)
    if not {"stock_code", "source_available_date"} <= columns:
        return {
            "source_family": family["source_family"],
            "source_table": source_table,
            "status": "missing_required_columns",
            "missing_columns": sorted({"stock_code", "source_available_date"} - columns),
        }
    direction_expr = _quote_ident("direction") if "direction" in columns else "NULL"
    progress_column = family.get("progress_column")
    progress_expr = _quote_ident(str(progress_column)) if progress_column and progress_column in columns else "NULL"
    value_expr = _source_value_expr(columns)
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_events")
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_shareholder_plan_family_events AS
        SELECT stock_code,
               {_date_expr(_quote_ident('source_available_date'))} AS event_dt,
               CAST({direction_expr} AS VARCHAR) AS direction,
               CAST({progress_expr} AS VARCHAR) AS progress,
               {value_expr} AS event_value,
               ROW_NUMBER() OVER () AS event_id
          FROM {_quote_relation(source_table)}
         WHERE stock_code IS NOT NULL
           AND source_available_date IS NOT NULL
           AND {_date_expr(_quote_ident('source_available_date'))} IS NOT NULL
        """
    )
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_features")
    conn.execute(
        """
        CREATE TEMP TABLE tmp_shareholder_plan_family_features AS
        WITH panel_bounds AS (
            SELECT MIN(date_dt) AS min_panel_date,
                   MAX(date_dt) AS max_panel_date
              FROM tmp_shareholder_plan_family_panel
        ),
        ev_aligned AS (
            SELECT e.stock_code,
                   MIN(p.date) AS date,
                   ANY_VALUE(e.direction) AS direction,
                   ANY_VALUE(e.progress) AS progress,
                   ANY_VALUE(e.event_value) AS event_value
              FROM tmp_shareholder_plan_family_events e
              JOIN tmp_shareholder_plan_family_panel p
                ON p.stock_code = e.stock_code
               AND p.date_dt >= e.event_dt
              CROSS JOIN panel_bounds b
             WHERE e.event_dt >= b.min_panel_date
               AND e.event_dt <= b.max_panel_date
             GROUP BY e.stock_code, e.event_id
        ),
        ev_daily AS (
            SELECT stock_code,
                   date,
                   SUM(CASE
                           WHEN direction LIKE '%增持%' OR lower(COALESCE(direction, '')) LIKE '%increase%'
                           THEN 1 ELSE 0
                       END)::INTEGER AS increase_n,
                   SUM(CASE
                           WHEN direction LIKE '%减持%' OR lower(COALESCE(direction, '')) LIKE '%decrease%'
                           THEN 1 ELSE 0
                       END)::INTEGER AS decrease_n,
                   SUM(CASE
                           WHEN progress LIKE '%完成%' OR lower(COALESCE(progress, '')) LIKE '%complete%'
                           THEN 1 ELSE 0
                       END)::INTEGER AS completed_n,
                   SUM(CASE
                           WHEN direction LIKE '%增持%' OR lower(COALESCE(direction, '')) LIKE '%increase%'
                           THEN COALESCE(event_value, 0.0) ELSE 0.0
                       END)::DOUBLE AS increase_v,
                   SUM(CASE
                           WHEN direction LIKE '%减持%' OR lower(COALESCE(direction, '')) LIKE '%decrease%'
                           THEN COALESCE(event_value, 0.0) ELSE 0.0
                       END)::DOUBLE AS decrease_v
              FROM ev_aligned
             GROUP BY stock_code, date
        ),
        panel_daily AS (
            SELECT p.*,
                   COALESCE(e.increase_n, 0) AS increase_n,
                   COALESCE(e.decrease_n, 0) AS decrease_n,
                   COALESCE(e.completed_n, 0) AS completed_n,
                   COALESCE(e.increase_v, 0.0) AS increase_v,
                   COALESCE(e.decrease_v, 0.0) AS decrease_v
              FROM tmp_shareholder_plan_family_panel p
              LEFT JOIN ev_daily e ON e.stock_code = p.stock_code AND e.date = p.date
        ),
        rolled AS (
            SELECT *,
                   SUM(increase_n) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS 179 PRECEDING
                   )::INTEGER AS increase_count_180d,
                   SUM(decrease_n) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS 179 PRECEDING
                   )::INTEGER AS decrease_count_180d,
                   SUM(completed_n) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS 179 PRECEDING
                   )::INTEGER AS completed_count_180d,
                   SUM(increase_v) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS 179 PRECEDING
                   )::DOUBLE AS increase_amount_max_180d,
                   SUM(decrease_v) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS 179 PRECEDING
                   )::DOUBLE AS decrease_amount_max_180d,
                   MAX(CASE WHEN increase_n > 0 THEN date_dt END) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS last_increase_dt,
                   MAX(CASE WHEN decrease_n > 0 THEN date_dt END) OVER (
                       PARTITION BY stock_code ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS last_decrease_dt
              FROM panel_daily
        )
        SELECT *,
               CASE WHEN last_increase_dt IS NULL THEN -1
                    ELSE (date_dt - last_increase_dt)::INTEGER END AS days_since_increase,
               CASE WHEN last_decrease_dt IS NULL THEN -1
                    ELSE (date_dt - last_decrease_dt)::INTEGER END AS days_since_decrease
          FROM rolled
        """
    )
    event_row = conn.execute(
        """
        SELECT COUNT(*) AS event_rows,
               COUNT(DISTINCT stock_code) AS distinct_event_stocks
          FROM tmp_shareholder_plan_family_events
        """
    ).fetchone()
    aligned_row = conn.execute(
        """
        SELECT COUNT(*) AS aligned_event_rows,
               COUNT(DISTINCT stock_code) AS aligned_event_stocks
          FROM (
              SELECT stock_code, date
                FROM tmp_shareholder_plan_family_features
               WHERE increase_n > 0 OR decrease_n > 0 OR completed_n > 0
          )
        """
    ).fetchone()
    return {
        "source_family": family["source_family"],
        "source_table": source_table,
        "status": "prepared",
        "event_rows": int(_row_value(event_row, "event_rows", 0) or 0),
        "distinct_event_stocks": int(_row_value(event_row, "distinct_event_stocks", 1) or 0),
        "aligned_event_rows": int(_row_value(aligned_row, "aligned_event_rows", 0) or 0),
        "aligned_event_stocks": int(_row_value(aligned_row, "aligned_event_stocks", 1) or 0),
    }


def _feature_event_counts(conn: Any, feature_kind: str) -> tuple[int, int]:
    if feature_kind == "increase":
        where = "direction LIKE '%增持%' OR lower(COALESCE(direction, '')) LIKE '%increase%'"
    elif feature_kind == "decrease":
        where = "direction LIKE '%减持%' OR lower(COALESCE(direction, '')) LIKE '%decrease%'"
    elif feature_kind == "completed":
        where = "progress LIKE '%完成%' OR lower(COALESCE(progress, '')) LIKE '%complete%'"
    else:
        where = "FALSE"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS event_rows,
               COUNT(DISTINCT stock_code) AS distinct_event_stocks
          FROM tmp_shareholder_plan_family_events
         WHERE {where}
        """
    ).fetchone()
    return int(_row_value(row, "event_rows", 0) or 0), int(_row_value(row, "distinct_event_stocks", 1) or 0)


def _compute_stats(
    conn: Any,
    *,
    feature_col: str,
    label_name: str,
    total_rows: int,
    min_daily_count: int,
) -> dict[str, Any]:
    feature_q = _quote_ident(feature_col)
    label_q = _quote_ident(label_name)
    valid_where = (
        f"{feature_q} IS NOT NULL AND {label_q} IS NOT NULL "
        f"AND ISFINITE(CAST({feature_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({label_q} AS DOUBLE))"
    )
    active_where = (
        f"CAST({feature_q} AS DOUBLE) >= 0"
        if feature_col.startswith("days_since_")
        else f"CAST({feature_q} AS DOUBLE) > 0"
    )
    coverage_row = conn.execute(
        f"""
        SELECT SUM(CASE WHEN {valid_where} THEN 1 ELSE 0 END) AS valid_rows,
               SUM(CASE
                       WHEN {feature_q} IS NOT NULL
                        AND ISFINITE(CAST({feature_q} AS DOUBLE))
                        AND {active_where}
                       THEN 1 ELSE 0
                   END) AS nondefault_rows,
               AVG(CASE WHEN {feature_q} IS NOT NULL
                         AND ISFINITE(CAST({feature_q} AS DOUBLE))
                        THEN CAST({feature_q} AS DOUBLE) END) AS feature_mean,
               AVG(CASE WHEN {label_q} IS NOT NULL
                         AND ISFINITE(CAST({label_q} AS DOUBLE))
                         AND {feature_q} IS NOT NULL
                         AND ISFINITE(CAST({feature_q} AS DOUBLE))
                         AND {active_where}
                        THEN CAST({label_q} AS DOUBLE) END) AS active_label_mean,
               AVG(CASE WHEN {label_q} IS NOT NULL
                         AND ISFINITE(CAST({label_q} AS DOUBLE))
                         AND {feature_q} IS NOT NULL
                         AND ISFINITE(CAST({feature_q} AS DOUBLE))
                         AND NOT ({active_where})
                        THEN CAST({label_q} AS DOUBLE) END) AS inactive_label_mean
          FROM tmp_shareholder_plan_family_features
        """
    ).fetchone()
    valid_rows = int(_row_value(coverage_row, "valid_rows", 0) or 0)
    nondefault_rows = int(_row_value(coverage_row, "nondefault_rows", 1) or 0)
    feature_mean = _finite_float(_row_value(coverage_row, "feature_mean", 2))
    active_mean = _finite_float(_row_value(coverage_row, "active_label_mean", 3))
    inactive_mean = _finite_float(_row_value(coverage_row, "inactive_label_mean", 4))
    ic_row = conn.execute(
        f"""
        SELECT corr(CAST({feature_q} AS DOUBLE), CAST({label_q} AS DOUBLE)) AS ic
          FROM tmp_shareholder_plan_family_features
         WHERE {valid_where}
        """
    ).fetchone()
    rank_row = conn.execute(
        f"""
        WITH valid AS (
            SELECT date,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM tmp_shareholder_plan_family_features
             WHERE {valid_where}
        ),
        ranked AS (
            SELECT date,
                   PERCENT_RANK() OVER (PARTITION BY date ORDER BY feature_value) AS feature_rank,
                   PERCENT_RANK() OVER (PARTITION BY date ORDER BY label_value) AS label_rank
              FROM valid
        ),
        daily AS (
            SELECT date,
                   COUNT(*) AS n,
                   corr(feature_rank, label_rank) AS rank_ic
              FROM ranked
             GROUP BY date
            HAVING COUNT(*) >= ?
        ),
        valid_daily AS (
            SELECT rank_ic
              FROM daily
             WHERE rank_ic IS NOT NULL
               AND ISFINITE(rank_ic)
        )
        SELECT AVG(rank_ic) AS rank_ic,
               CASE WHEN COUNT(rank_ic) >= 2
                    THEN SQRT(
                        GREATEST(
                            (SUM(rank_ic * rank_ic) - SUM(rank_ic) * SUM(rank_ic) / COUNT(rank_ic))
                            / (COUNT(rank_ic) - 1),
                            0
                        )
                    )
                    ELSE NULL END AS rank_ic_std,
               COUNT(rank_ic) AS daily_rank_ic_count,
               AVG(CASE WHEN rank_ic > 0 THEN 1.0 ELSE 0.0 END) AS positive_rank_ic_share
          FROM valid_daily
        """,
        [min_daily_count],
    ).fetchone()
    spread = active_mean - inactive_mean if active_mean is not None and inactive_mean is not None else None
    return {
        "valid_rows": valid_rows,
        "coverage_pct": 100.0 * valid_rows / total_rows if total_rows else 0.0,
        "nondefault_rows": nondefault_rows,
        "nondefault_pct": 100.0 * nondefault_rows / total_rows if total_rows else 0.0,
        "ic": _finite_float(_row_value(ic_row, "ic", 0) if ic_row else None),
        "rank_ic": _finite_float(_row_value(rank_row, "rank_ic", 0) if rank_row else None),
        "rank_ic_std_by_date": _finite_float(_row_value(rank_row, "rank_ic_std", 1) if rank_row else None),
        "daily_rank_ic_count": int(_row_value(rank_row, "daily_rank_ic_count", 2) or 0) if rank_row else 0,
        "positive_rank_ic_share": _finite_float(
            _row_value(rank_row, "positive_rank_ic_share", 3) if rank_row else None
        ),
        "feature_mean": feature_mean,
        "label_mean_when_active": active_mean,
        "label_mean_when_inactive": inactive_mean,
        "active_inactive_label_spread": spread,
    }


def build_shareholder_plan_feature_family_eval(
    conn: Any,
    *,
    run_id: str | None = None,
    panel_table: str = DEFAULT_PANEL_TABLE,
    labels: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_daily_count: int = 30,
) -> dict[str, Any]:
    ensure_shareholder_plan_feature_family_eval_table(conn)
    if not _table_exists(conn, panel_table):
        raise RuntimeError(f"panel table missing: {panel_table}")
    panel_columns = _table_columns(conn, panel_table)
    selected_labels = [
        label for label in dict.fromkeys(labels or list(DEFAULT_LABELS)) if label in panel_columns
    ]
    if not selected_labels:
        raise RuntimeError(f"no requested follow labels exist in {panel_table}")

    run_id = run_id or f"shareholder_plan_family_eval_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    started = time.perf_counter()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    _progress(
        f"start run_id={run_id} panel={panel_table} labels={','.join(selected_labels)} "
        f"start_date={start_date or '*'} end_date={end_date or '*'}"
    )
    stage_started = time.perf_counter()
    _progress("prepare_panel start")
    total_rows = _prepare_panel(
        conn,
        panel_table=panel_table,
        labels=selected_labels,
        start_date=start_date,
        end_date=end_date,
    )
    _progress(f"prepare_panel done rows={total_rows} elapsed={time.perf_counter() - stage_started:.3f}s")
    if total_rows <= 0:
        raise RuntimeError("panel selection produced no rows")

    conn.execute(f"DELETE FROM {EVAL_TABLE} WHERE run_id = ?", (run_id,))
    rows: list[tuple[Any, ...]] = []
    family_evidence = []
    for family in FAMILIES:
        if not _table_exists(conn, str(family["source_table"])):
            family_evidence.append(
                {
                    "source_family": family["source_family"],
                    "source_table": family["source_table"],
                    "status": "missing_source_table",
                }
            )
            continue
        stage_started = time.perf_counter()
        _progress(f"prepare_family start family={family['source_family']} source={family['source_table']}")
        evidence = _prepare_family_features(conn, family)
        _progress(
            f"prepare_family done family={family['source_family']} status={evidence['status']} "
            f"elapsed={time.perf_counter() - stage_started:.3f}s"
        )
        family_evidence.append(evidence)
        if evidence["status"] != "prepared":
            continue
        feature_defs = list(BASE_FEATURES)
        if family.get("include_completed"):
            feature_defs.append(COMPLETED_FEATURE)
        for feature_name, feature_col, window_days, feature_kind in feature_defs:
            feature_started = time.perf_counter()
            _progress(f"feature_stats start family={family['source_family']} feature={feature_name}")
            event_rows, distinct_event_stocks = _feature_event_counts(conn, feature_kind)
            for label in selected_labels:
                stats = _compute_stats(
                    conn,
                    feature_col=feature_col,
                    label_name=label,
                    total_rows=total_rows,
                    min_daily_count=min_daily_count,
                )
                rows.append(
                    (
                        run_id,
                        panel_table,
                        family["source_family"],
                        family["source_table"],
                        feature_name,
                        family["feature_purpose"],
                        label,
                        window_days,
                        total_rows,
                        stats["valid_rows"],
                        stats["coverage_pct"],
                        stats["nondefault_rows"],
                        stats["nondefault_pct"],
                        event_rows,
                        distinct_event_stocks,
                        stats["ic"],
                        stats["rank_ic"],
                        stats["rank_ic_std_by_date"],
                        stats["daily_rank_ic_count"],
                        stats["positive_rank_ic_share"],
                        stats["feature_mean"],
                        stats["label_mean_when_active"],
                        stats["label_mean_when_inactive"],
                        stats["active_inactive_label_spread"],
                        built_at,
                    )
                )
            _progress(
                f"feature_stats done family={family['source_family']} feature={feature_name} "
                f"labels={len(selected_labels)} elapsed={time.perf_counter() - feature_started:.3f}s"
            )
    if rows:
        stage_started = time.perf_counter()
        _progress(f"write_results start rows={len(rows)}")
        conn.executemany(
            f"""
            INSERT INTO {EVAL_TABLE}
            (run_id, panel_table, source_family, source_table, feature_name,
             feature_purpose, label_name, window_days, total_rows, valid_rows,
             coverage_pct, nondefault_rows, nondefault_pct, event_rows,
             distinct_event_stocks, ic, rank_ic, rank_ic_std_by_date,
             daily_rank_ic_count, positive_rank_ic_share, feature_mean,
             label_mean_when_active, label_mean_when_inactive,
             active_inactive_label_spread, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        _progress(f"write_results done elapsed={time.perf_counter() - stage_started:.3f}s")
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_features")
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_events")
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_panel")
    record_actual_version(conn, EVAL_TABLE)
    conn.commit()
    _progress(f"done run_id={run_id} rows={len(rows)} elapsed={time.perf_counter() - started:.3f}s")
    return {
        "run_id": run_id,
        "status": "completed",
        "panel_table": panel_table,
        "labels": selected_labels,
        "panel_rows": total_rows,
        "family_evidence": family_evidence,
        "inserted_rows": len(rows),
        "duration_s": round(time.perf_counter() - started, 3),
        "built_at": built_at,
    }


__all__ = [
    "DEFAULT_LABELS",
    "DEFAULT_PANEL_TABLE",
    "EVAL_TABLE",
    "build_shareholder_plan_feature_family_eval",
    "ensure_shareholder_plan_feature_family_eval_table",
]
