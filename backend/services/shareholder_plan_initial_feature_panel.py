"""Research-only daily panel for initial shareholder-plan events."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.schema_versions import record_actual_version
from services.shareholder_plan_initial_event import MART_TABLE as INITIAL_EVENT_TABLE


PANEL_TABLE = "mart_shareholder_plan_initial_feature_panel"
QUALITY_TABLE = "mart_shareholder_plan_initial_feature_panel_quality"
BASE_PANEL_TABLE = "fact_feature_panel"
CALENDAR_TABLE = "dim_trading_calendar"

DEFAULT_FEATURE_SET_ID = "shareholder_plan_initial_event_research_v1"
DEFAULT_WINDOW_DAYS = 180

INITIAL_FEATURE_COLUMNS = [
    "sp_initial_event_count_180d",
    "sp_initial_increase_count_180d",
    "sp_initial_decrease_count_180d",
    "sp_initial_increase_amount_max_sum_180d",
    "sp_initial_decrease_amount_max_sum_180d",
    "sp_initial_net_amount_max_sum_180d",
    "sp_initial_days_since_any",
    "sp_initial_days_since_increase",
    "sp_initial_days_since_decrease",
    "sp_initial_event_freshness_180d",
]

REGIME_FLAG_COLUMNS = ["regime_up_flag", "regime_flat_flag", "regime_down_flag"]

DEFAULT_LABELS = ["follow_net_return_60d", "follow_net_return_90d"]

DEFAULT_CONTEXT_FEATURES = [
    "ma_ratio_20",
    "ma_ratio_60",
    "ma_ratio_250",
    "klen",
    "kup",
    "ksft",
    "ret_20d",
    "ret_60d",
    "ret_20d_rank",
    "ret_60d_rank",
    "ret_20d_tdx_l1_rel",
    "ret_60d_tdx_l1_rel",
    "momentum_diff",
    "range_pos_60",
    "vol_z20d",
    "vol_z20d_rank",
    "vol_z20d_tdx_l1_rel",
    "vol_ratio_5_20",
    "amount_chg_5d",
    "amount_chg_5d_rank",
    "inst_event_count_60d",
    "lhb_inst_buy_count_60d",
    "jgdy_count_60d",
    "dzjy_count_60d",
]

DDL = f"""
CREATE TABLE IF NOT EXISTS {PANEL_TABLE} (
    feature_set_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
    source_max_available_date TEXT,
    sp_initial_event_count_180d BIGINT NOT NULL DEFAULT 0,
    sp_initial_increase_count_180d BIGINT NOT NULL DEFAULT 0,
    sp_initial_decrease_count_180d BIGINT NOT NULL DEFAULT 0,
    sp_initial_increase_amount_max_sum_180d DOUBLE NOT NULL DEFAULT 0,
    sp_initial_decrease_amount_max_sum_180d DOUBLE NOT NULL DEFAULT 0,
    sp_initial_net_amount_max_sum_180d DOUBLE NOT NULL DEFAULT 0,
    sp_initial_days_since_any INTEGER NOT NULL DEFAULT -1,
    sp_initial_days_since_increase INTEGER NOT NULL DEFAULT -1,
    sp_initial_days_since_decrease INTEGER NOT NULL DEFAULT -1,
    sp_initial_event_freshness_180d DOUBLE NOT NULL DEFAULT 0,
    regime_up_flag SMALLINT,
    regime_flat_flag SMALLINT,
    regime_down_flag SMALLINT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (feature_set_id, stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_sp_initial_feature_panel_date
    ON {PANEL_TABLE}(feature_set_id, date);
CREATE INDEX IF NOT EXISTS idx_sp_initial_feature_panel_active
    ON {PANEL_TABLE}(feature_set_id, sp_initial_event_count_180d);

CREATE TABLE IF NOT EXISTS {QUALITY_TABLE} (
    run_id TEXT PRIMARY KEY,
    feature_set_id TEXT NOT NULL,
    base_panel_table TEXT NOT NULL,
    initial_event_table TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    input_rows BIGINT,
    panel_rows BIGINT,
    stock_count BIGINT,
    date_count BIGINT,
    min_date TEXT,
    max_date TEXT,
    initial_event_rows BIGINT,
    matched_event_rows BIGINT,
    active_rows BIGINT,
    active_pct DOUBLE,
    dropped_invalid_date_rows BIGINT,
    dropped_incomplete_label_rows BIGINT,
    dropped_incomplete_context_rows BIGINT,
    calendar_mismatch_rows BIGINT,
    labels_json TEXT,
    context_features_json TEXT,
    initial_features_json TEXT,
    require_complete_labels BOOLEAN,
    require_complete_context BOOLEAN,
    stage_timings_json TEXT,
    built_at TEXT NOT NULL
);
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


def _table_exists(conn: Any, table: str) -> bool:
    parts = table.split(".")
    if len(parts) == 2:
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = ? AND table_name = ?
             LIMIT 1
            """,
            (parts[0], parts[1]),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = ?
             LIMIT 1
            """,
            (table,),
        ).fetchone()
    return row is not None


def _table_columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {
        str(row["column_name"] if hasattr(row, "keys") else row[0])
        for row in conn.execute(f"DESCRIBE {_quote_relation(table)}").fetchall()
    }


def _parse_columns(values: list[str] | tuple[str, ...] | None, defaults: list[str]) -> list[str]:
    out: list[str] = []
    for value in values or defaults:
        for item in str(value).split(","):
            name = item.strip()
            if name and name not in out:
                out.append(name)
    return out


def _ensure_numeric_columns(conn: Any, columns: list[str]) -> None:
    if not columns:
        return
    _execute_script(
        conn,
        "\n".join(
            f"ALTER TABLE {PANEL_TABLE} ADD COLUMN IF NOT EXISTS {_quote_ident(col)} DOUBLE;"
            for col in columns
        ),
    )


def ensure_shareholder_plan_initial_feature_panel_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _finite_filter(alias: str, columns: list[str]) -> str:
    if not columns:
        return "TRUE"
    prefix = f"{alias}." if alias else ""
    return " AND ".join(
        f"{prefix}{_quote_ident(col)} IS NOT NULL AND ISFINITE(CAST({prefix}{_quote_ident(col)} AS DOUBLE))"
        for col in columns
    )


def _base_where(
    *,
    start_date: str | None,
    end_date: str | None,
    labels: list[str],
    context_features: list[str],
    require_complete_labels: bool,
    require_complete_context: bool,
) -> tuple[str, list[Any]]:
    filters = ["TRY_CAST(date AS DATE) IS NOT NULL"]
    params: list[Any] = []
    if start_date:
        filters.append("date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("date <= ?")
        params.append(end_date)
    if require_complete_labels:
        filters.append(_finite_filter("", labels))
    if require_complete_context:
        filters.append(_finite_filter("", context_features))
    return " AND ".join(filters), params


def _insert_select_columns(labels: list[str], context_features: list[str], has_regime: bool) -> tuple[list[str], list[str]]:
    insert_cols = [
        "feature_set_id",
        "stock_code",
        "date",
        "source_max_available_date",
        *INITIAL_FEATURE_COLUMNS,
    ]
    if has_regime:
        insert_cols.extend(REGIME_FLAG_COLUMNS)
    insert_cols.extend(context_features)
    insert_cols.extend(labels)
    insert_cols.append("built_at")

    select_cols = [
        "? AS feature_set_id",
        "stock_code",
        "date",
        "COALESCE(source_max_available_date, date) AS source_max_available_date",
        *INITIAL_FEATURE_COLUMNS,
    ]
    if has_regime:
        select_cols.extend(REGIME_FLAG_COLUMNS)
    select_cols.extend(_quote_ident(col) for col in context_features)
    select_cols.extend(_quote_ident(col) for col in labels)
    select_cols.append("? AS built_at")
    return insert_cols, select_cols


def build_shareholder_plan_initial_feature_panel(
    conn: Any,
    *,
    run_id: str | None = None,
    feature_set_id: str = DEFAULT_FEATURE_SET_ID,
    base_panel_table: str = BASE_PANEL_TABLE,
    initial_event_table: str = INITIAL_EVENT_TABLE,
    labels: list[str] | None = None,
    context_features: list[str] | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    start_date: str | None = None,
    end_date: str | None = None,
    require_complete_labels: bool = True,
    require_complete_context: bool = True,
) -> dict[str, Any]:
    """Materialize initial-event encodings on the production daily panel grain.

    The output is research-only and intentionally separate from
    ``fact_feature_panel``. Event absence is encoded as zero/count or ``-1``
    days-since values so sparse event data cannot become unexplained NULLs.
    """

    ensure_shareholder_plan_initial_feature_panel_tables(conn)
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    stage_timings: dict[str, float] = {}
    run_id = run_id or f"sp_initial_feature_panel_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    labels = _parse_columns(labels, DEFAULT_LABELS)
    context_features = _parse_columns(context_features, DEFAULT_CONTEXT_FEATURES)
    window_days = int(window_days)
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    if not _table_exists(conn, base_panel_table):
        raise RuntimeError(f"base panel table is missing: {base_panel_table}")
    base_cols = _table_columns(conn, base_panel_table)
    missing_base = sorted({"stock_code", "date", *labels, *context_features} - base_cols)
    if missing_base:
        raise RuntimeError(f"{base_panel_table} missing required columns: {', '.join(missing_base)}")
    has_regime = "regime_flag" in base_cols
    existing_panel = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               SUM(CASE WHEN feature_set_id <> ? THEN 1 ELSE 0 END) AS other_feature_rows
          FROM {PANEL_TABLE}
        """,
        (feature_set_id,),
    ).fetchone()
    if int(existing_panel["row_count"] or 0) > 0 and int(existing_panel["other_feature_rows"] or 0) == 0:
        conn.execute(f"DROP TABLE IF EXISTS {PANEL_TABLE}")
        _execute_script(conn, DDL)
    _ensure_numeric_columns(conn, [*context_features, *labels])

    stage_started = time.perf_counter()
    where_sql, params = _base_where(
        start_date=start_date,
        end_date=end_date,
        labels=labels,
        context_features=context_features,
        require_complete_labels=require_complete_labels,
        require_complete_context=require_complete_context,
    )
    conn.execute("DROP TABLE IF EXISTS tmp_sp_initial_feature_base_all")
    base_select = [
        "stock_code",
        "CAST(date AS VARCHAR) AS date",
        "TRY_CAST(date AS DATE) AS signal_dt",
        *[_quote_ident(col) for col in context_features],
        *[_quote_ident(col) for col in labels],
    ]
    if has_regime:
        base_select.append("regime_flag")
    base_filter = []
    base_params: list[Any] = []
    if start_date:
        base_filter.append("date >= ?")
        base_params.append(start_date)
    if end_date:
        base_filter.append("date <= ?")
        base_params.append(end_date)
    base_filter_sql = "WHERE " + " AND ".join(base_filter) if base_filter else ""
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_sp_initial_feature_base_all AS
        SELECT {', '.join(base_select)}
          FROM {_quote_relation(base_panel_table)}
          {base_filter_sql}
        """,
        base_params,
    )
    conn.execute("DROP TABLE IF EXISTS tmp_sp_initial_feature_base")
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_sp_initial_feature_base AS
        SELECT *
          FROM tmp_sp_initial_feature_base_all
         WHERE {where_sql}
        """,
        params,
    )
    stage_timings["prepare_base_s"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    conn.execute("DROP TABLE IF EXISTS tmp_sp_initial_feature_events")
    if _table_exists(conn, initial_event_table):
        event_cols = _table_columns(conn, initial_event_table)
        missing_event = sorted({"stock_code", "source_available_date", "direction"} - event_cols)
        if missing_event:
            raise RuntimeError(f"{initial_event_table} missing required columns: {', '.join(missing_event)}")
        amount_expr = "target_amount_max" if "target_amount_max" in event_cols else "NULL"
        conn.execute(
            f"""
            CREATE TEMP TABLE tmp_sp_initial_feature_events AS
            SELECT stock_code,
                   TRY_CAST(source_available_date AS DATE) AS source_available_dt,
                   CAST(source_available_date AS VARCHAR) AS source_available_date,
                   CAST(direction AS VARCHAR) AS direction,
                   COALESCE(TRY_CAST({amount_expr} AS DOUBLE), 0.0) AS target_amount_max
              FROM {_quote_relation(initial_event_table)}
             WHERE TRY_CAST(source_available_date AS DATE) IS NOT NULL
            """
        )
    else:
        conn.execute(
            """
            CREATE TEMP TABLE tmp_sp_initial_feature_events (
                stock_code TEXT,
                source_available_dt DATE,
                source_available_date TEXT,
                direction TEXT,
                target_amount_max DOUBLE
            )
            """
        )
    stage_timings["prepare_events_s"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    group_cols = ["b.stock_code", "b.date", "b.signal_dt"]
    group_cols.extend(f"b.{_quote_ident(col)}" for col in context_features)
    group_cols.extend(f"b.{_quote_ident(col)}" for col in labels)
    if has_regime:
        group_cols.append("b.regime_flag")
    aggregate_select = [
        "b.stock_code",
        "b.date",
        "b.signal_dt",
        "MAX(e.source_available_date) AS source_max_available_date",
        "COUNT(e.stock_code) AS sp_initial_event_count_180d",
        "SUM(CASE WHEN e.direction LIKE '%增持%' THEN 1 ELSE 0 END) AS sp_initial_increase_count_180d",
        "SUM(CASE WHEN e.direction LIKE '%减持%' THEN 1 ELSE 0 END) AS sp_initial_decrease_count_180d",
        (
            "SUM(CASE WHEN e.direction LIKE '%增持%' THEN e.target_amount_max ELSE 0 END) "
            "AS sp_initial_increase_amount_max_sum_180d"
        ),
        (
            "SUM(CASE WHEN e.direction LIKE '%减持%' THEN e.target_amount_max ELSE 0 END) "
            "AS sp_initial_decrease_amount_max_sum_180d"
        ),
        "MAX(e.source_available_dt) AS max_any_dt",
        "MAX(CASE WHEN e.direction LIKE '%增持%' THEN e.source_available_dt ELSE NULL END) AS max_increase_dt",
        "MAX(CASE WHEN e.direction LIKE '%减持%' THEN e.source_available_dt ELSE NULL END) AS max_decrease_dt",
    ]
    if has_regime:
        aggregate_select.extend(
            [
                "CASE WHEN b.regime_flag = 'up' THEN 1 ELSE 0 END AS regime_up_flag",
                "CASE WHEN b.regime_flag = 'flat' THEN 1 ELSE 0 END AS regime_flat_flag",
                "CASE WHEN b.regime_flag = 'down' THEN 1 ELSE 0 END AS regime_down_flag",
            ]
        )
    aggregate_select.extend(f"b.{_quote_ident(col)} AS {_quote_ident(col)}" for col in context_features)
    aggregate_select.extend(f"b.{_quote_ident(col)} AS {_quote_ident(col)}" for col in labels)
    conn.execute("DROP TABLE IF EXISTS tmp_sp_initial_feature_aggregated")
    conn.execute(
        f"""
        CREATE TEMP TABLE tmp_sp_initial_feature_aggregated AS
        SELECT {', '.join(aggregate_select)}
          FROM tmp_sp_initial_feature_base b
          LEFT JOIN tmp_sp_initial_feature_events e
            ON e.stock_code = b.stock_code
           AND e.source_available_dt <= b.signal_dt
           AND e.source_available_dt > b.signal_dt - INTERVAL {window_days} DAY
         GROUP BY {', '.join(group_cols)}
        """
    )
    conn.execute("DROP TABLE IF EXISTS tmp_sp_initial_feature_final")
    final_select = [
        "stock_code",
        "date",
        "source_max_available_date",
        "CAST(sp_initial_event_count_180d AS BIGINT) AS sp_initial_event_count_180d",
        "CAST(sp_initial_increase_count_180d AS BIGINT) AS sp_initial_increase_count_180d",
        "CAST(sp_initial_decrease_count_180d AS BIGINT) AS sp_initial_decrease_count_180d",
        (
            "COALESCE(sp_initial_increase_amount_max_sum_180d, 0.0) "
            "AS sp_initial_increase_amount_max_sum_180d"
        ),
        (
            "COALESCE(sp_initial_decrease_amount_max_sum_180d, 0.0) "
            "AS sp_initial_decrease_amount_max_sum_180d"
        ),
        (
            "COALESCE(sp_initial_increase_amount_max_sum_180d, 0.0) "
            "- COALESCE(sp_initial_decrease_amount_max_sum_180d, 0.0) "
            "AS sp_initial_net_amount_max_sum_180d"
        ),
        (
            "CASE WHEN max_any_dt IS NULL THEN -1 ELSE CAST(date_diff('day', max_any_dt, signal_dt) AS INTEGER) END "
            "AS sp_initial_days_since_any"
        ),
        (
            "CASE WHEN max_increase_dt IS NULL THEN -1 ELSE CAST(date_diff('day', max_increase_dt, signal_dt) AS INTEGER) END "
            "AS sp_initial_days_since_increase"
        ),
        (
            "CASE WHEN max_decrease_dt IS NULL THEN -1 ELSE CAST(date_diff('day', max_decrease_dt, signal_dt) AS INTEGER) END "
            "AS sp_initial_days_since_decrease"
        ),
        (
            "CASE "
            "WHEN max_any_dt IS NULL THEN 0.0 "
            f"ELSE GREATEST(0.0, 1.0 - CAST(date_diff('day', max_any_dt, signal_dt) AS DOUBLE) / {float(window_days)}) "
            "END AS sp_initial_event_freshness_180d"
        ),
    ]
    if has_regime:
        final_select.extend(REGIME_FLAG_COLUMNS)
    final_select.extend(_quote_ident(col) for col in [*context_features, *labels])
    conn.execute(
        """
        CREATE TEMP TABLE tmp_sp_initial_feature_final AS
        SELECT """ + ", ".join(final_select) + """
          FROM tmp_sp_initial_feature_aggregated
        """
    )
    stage_timings["aggregate_features_s"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    insert_cols, select_cols = _insert_select_columns(labels, context_features, has_regime)
    conn.execute(f"DELETE FROM {PANEL_TABLE} WHERE feature_set_id = ?", (feature_set_id,))
    conn.execute(
        f"""
        INSERT INTO {PANEL_TABLE}
        ({', '.join(_quote_ident(col) for col in insert_cols)})
        SELECT {', '.join(select_cols)}
          FROM tmp_sp_initial_feature_final
        """,
        (feature_set_id, built_at),
    )
    stage_timings["write_panel_s"] = round(time.perf_counter() - stage_started, 3)

    stage_started = time.perf_counter()
    input_rows = int(conn.execute("SELECT COUNT(*) FROM tmp_sp_initial_feature_base_all").fetchone()[0] or 0)
    invalid_date_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM tmp_sp_initial_feature_base_all WHERE signal_dt IS NULL"
        ).fetchone()[0]
        or 0
    )
    label_incomplete = 0
    if labels:
        label_incomplete = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM tmp_sp_initial_feature_base_all
                 WHERE signal_dt IS NOT NULL
                   AND NOT ({_finite_filter('tmp_sp_initial_feature_base_all', labels)})
                """
            ).fetchone()[0]
            or 0
        )
    context_incomplete = 0
    if context_features:
        context_incomplete = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM tmp_sp_initial_feature_base_all
                 WHERE signal_dt IS NOT NULL
                   AND {'TRUE' if not require_complete_labels else _finite_filter('tmp_sp_initial_feature_base_all', labels)}
                   AND NOT ({_finite_filter('tmp_sp_initial_feature_base_all', context_features)})
                """
            ).fetchone()[0]
            or 0
        )
    panel_summary = conn.execute(
        f"""
        SELECT COUNT(*) AS panel_rows,
               COUNT(DISTINCT stock_code) AS stock_count,
               COUNT(DISTINCT date) AS date_count,
               MIN(date) AS min_date,
               MAX(date) AS max_date,
               SUM(CASE WHEN sp_initial_event_count_180d > 0 THEN 1 ELSE 0 END) AS active_rows
          FROM {PANEL_TABLE}
         WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()
    event_rows = int(conn.execute("SELECT COUNT(*) FROM tmp_sp_initial_feature_events").fetchone()[0] or 0)
    matched_event_rows = int(
        conn.execute(
            """
            SELECT COUNT(*)
              FROM tmp_sp_initial_feature_events e
             WHERE EXISTS (
                   SELECT 1
                     FROM tmp_sp_initial_feature_base b
                    WHERE e.stock_code = b.stock_code
                      AND e.source_available_dt <= b.signal_dt
                      AND e.source_available_dt > b.signal_dt - INTERVAL {window_days} DAY
             )
            """.format(window_days=window_days)
        ).fetchone()[0]
        or 0
    )
    calendar_mismatch_rows = 0
    if _table_exists(conn, CALENDAR_TABLE):
        calendar_mismatch_rows = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM {PANEL_TABLE} p
                  LEFT JOIN {CALENDAR_TABLE} c
                    ON c.trade_date = p.date AND COALESCE(c.is_trading, 0) = 1
                 WHERE p.feature_set_id = ?
                   AND c.trade_date IS NULL
                """,
                (feature_set_id,),
            ).fetchone()[0]
            or 0
        )
    active_rows = int(panel_summary["active_rows"] or 0)
    panel_rows = int(panel_summary["panel_rows"] or 0)
    active_pct = 100.0 * active_rows / panel_rows if panel_rows else 0.0
    stage_timings["quality_s"] = round(time.perf_counter() - stage_started, 3)

    duration_s = round(time.perf_counter() - t0, 3)
    stage_timings["total_s"] = duration_s
    quality_payload = (
        run_id,
        feature_set_id,
        base_panel_table,
        initial_event_table,
        window_days,
        input_rows,
        panel_rows,
        int(panel_summary["stock_count"] or 0),
        int(panel_summary["date_count"] or 0),
        panel_summary["min_date"],
        panel_summary["max_date"],
        event_rows,
        matched_event_rows,
        active_rows,
        active_pct,
        invalid_date_rows,
        label_incomplete if require_complete_labels else 0,
        context_incomplete if require_complete_context else 0,
        calendar_mismatch_rows,
        json.dumps(labels, ensure_ascii=False),
        json.dumps(context_features, ensure_ascii=False),
        json.dumps(INITIAL_FEATURE_COLUMNS, ensure_ascii=False),
        require_complete_labels,
        require_complete_context,
        json.dumps(stage_timings, ensure_ascii=False, sort_keys=True),
        built_at,
    )
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {QUALITY_TABLE} (
            run_id, feature_set_id, base_panel_table, initial_event_table,
            window_days, input_rows, panel_rows, stock_count, date_count,
            min_date, max_date, initial_event_rows, matched_event_rows,
            active_rows, active_pct, dropped_invalid_date_rows,
            dropped_incomplete_label_rows, dropped_incomplete_context_rows,
            calendar_mismatch_rows, labels_json, context_features_json,
            initial_features_json, require_complete_labels,
            require_complete_context, stage_timings_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        quality_payload,
    )
    record_actual_version(conn, PANEL_TABLE)
    record_actual_version(conn, QUALITY_TABLE)
    ended_at = utc_now_iso()
    result = {
        "run_id": run_id,
        "feature_set_id": feature_set_id,
        "panel_rows": panel_rows,
        "stock_count": int(panel_summary["stock_count"] or 0),
        "date_count": int(panel_summary["date_count"] or 0),
        "min_date": panel_summary["min_date"],
        "max_date": panel_summary["max_date"],
        "initial_event_rows": event_rows,
        "matched_event_rows": matched_event_rows,
        "active_rows": active_rows,
        "active_pct": active_pct,
        "calendar_mismatch_rows": calendar_mismatch_rows,
        "dropped_invalid_date_rows": invalid_date_rows,
        "dropped_incomplete_label_rows": label_incomplete if require_complete_labels else 0,
        "dropped_incomplete_context_rows": context_incomplete if require_complete_context else 0,
        "labels": labels,
        "context_features": context_features,
        "initial_features": INITIAL_FEATURE_COLUMNS,
        "stage_timings": stage_timings,
        "duration_s": duration_s,
        "built_at": built_at,
    }
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_shareholder_plan_initial_feature_panel",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parents[2]),
        input_tables=[base_panel_table, initial_event_table, CALENDAR_TABLE],
        output_tables=[PANEL_TABLE, QUALITY_TABLE],
        feature_group="shareholder_plan_initial_event_research",
        perf_summary=result,
    )
    conn.commit()
    for table in (
        "tmp_sp_initial_feature_base_all",
        "tmp_sp_initial_feature_base",
        "tmp_sp_initial_feature_events",
        "tmp_sp_initial_feature_aggregated",
        "tmp_sp_initial_feature_final",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    return result


__all__ = [
    "BASE_PANEL_TABLE",
    "DEFAULT_CONTEXT_FEATURES",
    "DEFAULT_FEATURE_SET_ID",
    "DEFAULT_LABELS",
    "INITIAL_FEATURE_COLUMNS",
    "PANEL_TABLE",
    "QUALITY_TABLE",
    "build_shareholder_plan_initial_feature_panel",
    "ensure_shareholder_plan_initial_feature_panel_tables",
]
