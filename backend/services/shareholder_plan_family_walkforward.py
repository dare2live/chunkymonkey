"""Controlled walk-forward validation for shareholder-plan feature families."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import UTC, datetime
from typing import Any

from services.schema_versions import record_actual_version
from services.shareholder_plan_feature_family_eval import (
    BASE_FEATURES,
    COMPLETED_FEATURE,
    DEFAULT_LABELS,
    DEFAULT_PANEL_TABLE,
    EVAL_TABLE as FAMILY_EVAL_TABLE,
    FAMILIES,
    _execute_script,
    _finite_float,
    _prepare_family_features,
    _prepare_panel,
    _quote_ident,
    _quote_relation,
    _row_value,
    _table_columns,
    _table_exists,
)


FOLD_TABLE = "mart_shareholder_plan_family_walkforward"
SUMMARY_TABLE = "mart_shareholder_plan_family_walkforward_summary"

DEFAULT_FOLD_COUNT = 4
DEFAULT_HOLDOUT_DAYS = 90
DEFAULT_TRAIN_DAYS = 360

DDL = f"""
CREATE TABLE IF NOT EXISTS {FOLD_TABLE} (
    run_id TEXT NOT NULL,
    source_eval_run_id TEXT,
    panel_table TEXT NOT NULL,
    source_family TEXT NOT NULL,
    source_table TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_purpose TEXT NOT NULL,
    label_name TEXT NOT NULL,
    window_days INTEGER,
    fold_id INTEGER NOT NULL,
    train_start_date TEXT,
    train_end_date TEXT,
    holdout_start_date TEXT NOT NULL,
    holdout_end_date TEXT NOT NULL,
    train_total_rows BIGINT,
    train_valid_rows BIGINT,
    train_active_rows BIGINT,
    train_active_pct DOUBLE,
    train_rank_ic DOUBLE,
    train_daily_rank_ic_count INTEGER,
    train_active_inactive_spread DOUBLE,
    signal_direction INTEGER,
    holdout_total_rows BIGINT,
    holdout_valid_rows BIGINT,
    holdout_active_rows BIGINT,
    holdout_active_pct DOUBLE,
    holdout_rank_ic DOUBLE,
    holdout_rank_ic_std_by_date DOUBLE,
    holdout_daily_rank_ic_count INTEGER,
    holdout_positive_rank_ic_share DOUBLE,
    holdout_active_inactive_spread DOUBLE,
    holdout_top_quantile_return DOUBLE,
    holdout_bottom_quantile_return DOUBLE,
    holdout_long_short_spread DOUBLE,
    holdout_long_short_max_drawdown DOUBLE,
    holdout_avg_turnover DOUBLE,
    holdout_top_signal_rows BIGINT,
    holdout_bottom_signal_rows BIGINT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_family, feature_name, label_name, fold_id)
);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_wf_run
    ON {FOLD_TABLE}(run_id);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_wf_feature
    ON {FOLD_TABLE}(source_family, feature_name, label_name);

CREATE TABLE IF NOT EXISTS {SUMMARY_TABLE} (
    run_id TEXT NOT NULL,
    source_eval_run_id TEXT,
    panel_table TEXT NOT NULL,
    source_family TEXT NOT NULL,
    source_table TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_purpose TEXT NOT NULL,
    label_name TEXT NOT NULL,
    window_days INTEGER,
    fold_count INTEGER,
    valid_fold_count INTEGER,
    avg_train_rank_ic DOUBLE,
    avg_holdout_rank_ic DOUBLE,
    avg_signal_adjusted_holdout_rank_ic DOUBLE,
    avg_holdout_rank_ic_std DOUBLE,
    positive_signal_rank_ic_fold_share DOUBLE,
    avg_train_active_inactive_spread DOUBLE,
    avg_holdout_active_inactive_spread DOUBLE,
    avg_holdout_long_short_spread DOUBLE,
    positive_long_short_fold_share DOUBLE,
    worst_holdout_long_short_max_drawdown DOUBLE,
    avg_holdout_top_quantile_return DOUBLE,
    avg_holdout_bottom_quantile_return DOUBLE,
    avg_holdout_turnover DOUBLE,
    avg_holdout_active_pct DOUBLE,
    min_holdout_active_rows BIGINT,
    gate_status TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    cautions_json TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_family, feature_name, label_name)
);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_wf_summary_run
    ON {SUMMARY_TABLE}(run_id);
CREATE INDEX IF NOT EXISTS idx_shareholder_plan_wf_summary_gate
    ON {SUMMARY_TABLE}(gate_status, label_name);
"""


def _progress(message: str) -> None:
    print(f"[shareholder_plan_family_wf] {datetime.now(UTC).isoformat()} {message}", flush=True)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _feature_defs_for_family(family: dict[str, Any]) -> list[tuple[str, str, int | None, str]]:
    out = list(BASE_FEATURES)
    if family.get("include_completed"):
        out.append(COMPLETED_FEATURE)
    return out


def ensure_shareholder_plan_family_walkforward_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _latest_family_eval_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, FAMILY_EVAL_TABLE):
        return None
    return _row_value(
        conn.execute(
            f"""
            SELECT run_id
              FROM {_quote_relation(FAMILY_EVAL_TABLE)}
             ORDER BY built_at DESC, run_id DESC
             LIMIT 1
            """
        ).fetchone(),
        "run_id",
        0,
    )


def _date_folds_for_label(
    conn: Any,
    *,
    label_name: str,
    fold_count: int,
    train_days: int,
    holdout_days: int,
    min_daily_count: int,
) -> list[dict[str, Any]]:
    label_q = _quote_ident(label_name)
    rows = conn.execute(
        f"""
        SELECT date, date_dt, COUNT(*) AS valid_rows
          FROM tmp_shareholder_plan_family_panel
         WHERE {label_q} IS NOT NULL
           AND ISFINITE(CAST({label_q} AS DOUBLE))
         GROUP BY date, date_dt
        HAVING COUNT(*) >= ?
         ORDER BY date_dt
        """,
        (int(min_daily_count),),
    ).fetchall()
    dates = [
        {
            "date": str(_row_value(row, "date", 0)),
            "date_dt": _row_value(row, "date_dt", 1),
            "valid_rows": int(_row_value(row, "valid_rows", 2) or 0),
        }
        for row in rows
    ]
    if not dates:
        return []
    folds: list[dict[str, Any]] = []
    n = len(dates)
    for fold_id in range(1, int(fold_count) + 1):
        holdout_end_idx = n - (int(fold_count) - fold_id) * int(holdout_days) - 1
        holdout_start_idx = holdout_end_idx - int(holdout_days) + 1
        train_end_idx = holdout_start_idx - 1
        train_start_idx = train_end_idx - int(train_days) + 1
        if holdout_end_idx < 0 or holdout_start_idx < 0 or train_end_idx < 0:
            continue
        train_start_idx = max(0, train_start_idx)
        if train_start_idx > train_end_idx:
            continue
        folds.append(
            {
                "fold_id": fold_id,
                "train_start": dates[train_start_idx]["date"],
                "train_end": dates[train_end_idx]["date"],
                "holdout_start": dates[holdout_start_idx]["date"],
                "holdout_end": dates[holdout_end_idx]["date"],
            }
        )
    return folds


def _active_where(feature_col: str, feature_q: str) -> str:
    if feature_col.startswith("days_since_"):
        return f"CAST({feature_q} AS DOUBLE) >= 0"
    return f"CAST({feature_q} AS DOUBLE) > 0"


def _valid_where(feature_q: str, label_q: str) -> str:
    return (
        f"{feature_q} IS NOT NULL AND {label_q} IS NOT NULL "
        f"AND ISFINITE(CAST({feature_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({label_q} AS DOUBLE))"
    )


def _period_stats(
    conn: Any,
    *,
    feature_col: str,
    label_name: str,
    start_date: str,
    end_date: str,
    min_daily_count: int,
) -> dict[str, Any]:
    feature_q = _quote_ident(feature_col)
    label_q = _quote_ident(label_name)
    valid_where = _valid_where(feature_q, label_q)
    active_where = _active_where(feature_col, feature_q)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_rows,
               SUM(CASE WHEN {valid_where} THEN 1 ELSE 0 END) AS valid_rows,
               SUM(CASE WHEN {valid_where} AND {active_where} THEN 1 ELSE 0 END) AS active_rows,
               AVG(CASE WHEN {valid_where} AND {active_where}
                        THEN CAST({label_q} AS DOUBLE) END) AS active_label_mean,
               AVG(CASE WHEN {valid_where} AND NOT ({active_where})
                        THEN CAST({label_q} AS DOUBLE) END) AS inactive_label_mean
          FROM tmp_shareholder_plan_family_features
         WHERE date_dt >= TRY_CAST(? AS DATE)
           AND date_dt <= TRY_CAST(? AS DATE)
        """,
        (start_date, end_date),
    ).fetchone()
    total_rows = int(_row_value(row, "total_rows", 0) or 0)
    valid_rows = int(_row_value(row, "valid_rows", 1) or 0)
    active_rows = int(_row_value(row, "active_rows", 2) or 0)
    active_mean = _finite_float(_row_value(row, "active_label_mean", 3))
    inactive_mean = _finite_float(_row_value(row, "inactive_label_mean", 4))
    rank_row = conn.execute(
        f"""
        WITH valid AS (
            SELECT date,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM tmp_shareholder_plan_family_features
             WHERE date_dt >= TRY_CAST(? AS DATE)
               AND date_dt <= TRY_CAST(? AS DATE)
               AND {valid_where}
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
        (start_date, end_date, int(min_daily_count)),
    ).fetchone()
    return {
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "active_rows": active_rows,
        "active_pct": 100.0 * active_rows / total_rows if total_rows else 0.0,
        "active_inactive_spread": (
            active_mean - inactive_mean if active_mean is not None and inactive_mean is not None else None
        ),
        "rank_ic": _finite_float(_row_value(rank_row, "rank_ic", 0) if rank_row else None),
        "rank_ic_std": _finite_float(_row_value(rank_row, "rank_ic_std", 1) if rank_row else None),
        "daily_rank_ic_count": int(_row_value(rank_row, "daily_rank_ic_count", 2) or 0) if rank_row else 0,
        "positive_rank_ic_share": _finite_float(
            _row_value(rank_row, "positive_rank_ic_share", 3) if rank_row else None
        ),
    }


def _signal_direction(train_stats: dict[str, Any]) -> int:
    rank_ic = train_stats.get("rank_ic")
    if rank_ic is not None and math.isfinite(float(rank_ic)) and abs(float(rank_ic)) > 1e-12:
        return 1 if float(rank_ic) > 0 else -1
    spread = train_stats.get("active_inactive_spread")
    if spread is not None and math.isfinite(float(spread)) and abs(float(spread)) > 1e-12:
        return 1 if float(spread) > 0 else -1
    return 1


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        if not math.isfinite(value):
            continue
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _holding_period_from_label(label_name: str) -> int | None:
    match = re.search(r"_(\d+)d$", str(label_name))
    if not match:
        return None
    return int(match.group(1))


def _holdout_signal_stats(
    conn: Any,
    *,
    feature_col: str,
    label_name: str,
    start_date: str,
    end_date: str,
    signal_direction: int,
    min_daily_count: int,
    top_quantile: float,
) -> dict[str, Any]:
    feature_q = _quote_ident(feature_col)
    label_q = _quote_ident(label_name)
    valid_where = _valid_where(feature_q, label_q)
    direction = 1.0 if int(signal_direction) >= 0 else -1.0
    top_cut = 1.0 - float(top_quantile)
    bottom_cut = float(top_quantile)
    daily_rows = conn.execute(
        f"""
        WITH valid AS (
            SELECT date,
                   stock_code,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM tmp_shareholder_plan_family_features
             WHERE date_dt >= TRY_CAST(? AS DATE)
               AND date_dt <= TRY_CAST(? AS DATE)
               AND {valid_where}
        ),
        eligible_dates AS (
            SELECT date
              FROM valid
             GROUP BY date
            HAVING COUNT(*) >= ?
               AND COUNT(DISTINCT feature_value) >= 2
        ),
        ranked AS (
            SELECT v.date,
                   v.stock_code,
                   v.label_value,
                   PERCENT_RANK() OVER (
                       PARTITION BY v.date ORDER BY ({direction} * v.feature_value)
                   ) AS signal_rank
              FROM valid v
              JOIN eligible_dates e ON e.date = v.date
        ),
        daily AS (
            SELECT date,
                   AVG(CASE WHEN signal_rank >= ? THEN label_value END) AS top_return,
                   AVG(CASE WHEN signal_rank <= ? THEN label_value END) AS bottom_return,
                   SUM(CASE WHEN signal_rank >= ? THEN 1 ELSE 0 END) AS top_rows,
                   SUM(CASE WHEN signal_rank <= ? THEN 1 ELSE 0 END) AS bottom_rows
              FROM ranked
             GROUP BY date
        )
        SELECT date, top_return, bottom_return, top_rows, bottom_rows,
               top_return - bottom_return AS long_short
          FROM daily
         WHERE top_return IS NOT NULL
           AND bottom_return IS NOT NULL
         ORDER BY date
        """,
        (
            start_date,
            end_date,
            int(min_daily_count),
            top_cut,
            bottom_cut,
            top_cut,
            bottom_cut,
        ),
    ).fetchall()
    long_short_values = [
        float(_row_value(row, "long_short", 5))
        for row in daily_rows
        if _finite_float(_row_value(row, "long_short", 5)) is not None
    ]
    horizon_days = _holding_period_from_label(label_name)
    drawdown_values = [
        value / horizon_days for value in long_short_values
    ] if horizon_days and horizon_days > 0 else long_short_values
    top_values = [
        float(_row_value(row, "top_return", 1))
        for row in daily_rows
        if _finite_float(_row_value(row, "top_return", 1)) is not None
    ]
    bottom_values = [
        float(_row_value(row, "bottom_return", 2))
        for row in daily_rows
        if _finite_float(_row_value(row, "bottom_return", 2)) is not None
    ]
    top_rows = sum(int(_row_value(row, "top_rows", 3) or 0) for row in daily_rows)
    bottom_rows = sum(int(_row_value(row, "bottom_rows", 4) or 0) for row in daily_rows)
    turnover_row = conn.execute(
        f"""
        WITH valid AS (
            SELECT date,
                   stock_code,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM tmp_shareholder_plan_family_features
             WHERE date_dt >= TRY_CAST(? AS DATE)
               AND date_dt <= TRY_CAST(? AS DATE)
               AND {valid_where}
        ),
        eligible_dates AS (
            SELECT date
              FROM valid
             GROUP BY date
            HAVING COUNT(*) >= ?
               AND COUNT(DISTINCT feature_value) >= 2
        ),
        ranked AS (
            SELECT DENSE_RANK() OVER (ORDER BY v.date) AS date_idx,
                   v.stock_code,
                   PERCENT_RANK() OVER (
                       PARTITION BY v.date ORDER BY ({direction} * v.feature_value)
                   ) AS signal_rank
              FROM valid v
              JOIN eligible_dates e ON e.date = v.date
        ),
        top_set AS (
            SELECT date_idx, stock_code
              FROM ranked
             WHERE signal_rank >= ?
        ),
        top_turnover_overlap AS (
            SELECT c.date_idx,
                   COUNT(*) AS current_n,
                   SUM(CASE WHEN p.stock_code IS NOT NULL THEN 1 ELSE 0 END) AS overlap_n
              FROM top_set c
              LEFT JOIN top_set p
                ON p.stock_code = c.stock_code
               AND p.date_idx = c.date_idx - 1
             GROUP BY c.date_idx
        )
        SELECT AVG(CASE WHEN current_n > 0
                        THEN 1.0 - CAST(overlap_n AS DOUBLE) / current_n
                   END) AS avg_turnover
          FROM top_turnover_overlap
         WHERE date_idx > (SELECT MIN(date_idx) FROM top_set)
        """,
        (start_date, end_date, int(min_daily_count), top_cut),
    ).fetchone()
    return {
        "top_quantile_return": sum(top_values) / len(top_values) if top_values else None,
        "bottom_quantile_return": sum(bottom_values) / len(bottom_values) if bottom_values else None,
        "long_short_spread": sum(long_short_values) / len(long_short_values) if long_short_values else None,
        "long_short_max_drawdown": _max_drawdown(drawdown_values),
        "avg_turnover": _finite_float(_row_value(turnover_row, "avg_turnover", 0) if turnover_row else None),
        "top_signal_rows": top_rows,
        "bottom_signal_rows": bottom_rows,
        "long_short_fold_day_count": len(long_short_values),
    }


def _avg(values: list[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(finite) / len(finite) if finite else None


def _share(values: list[bool]) -> float | None:
    return sum(1 for value in values if value) / len(values) if values else None


def _summarize_feature(
    rows: list[dict[str, Any]],
    *,
    min_folds: int,
    min_avg_signal_rank_ic: float,
    min_avg_long_short_spread: float,
    min_positive_fold_share: float,
    max_long_short_drawdown: float,
    min_active_pct: float,
) -> dict[str, Any]:
    valid_rows = [row for row in rows if int(row.get("holdout_daily_rank_ic_count") or 0) > 0]
    signal_rank_ics = [
        (float(row["holdout_rank_ic"]) * int(row["signal_direction"]))
        for row in valid_rows
        if row.get("holdout_rank_ic") is not None
    ]
    long_short_values = [
        float(row["holdout_long_short_spread"])
        for row in valid_rows
        if row.get("holdout_long_short_spread") is not None
    ]
    drawdowns = [
        float(row["holdout_long_short_max_drawdown"])
        for row in valid_rows
        if row.get("holdout_long_short_max_drawdown") is not None
    ]
    active_pcts = [
        float(row["holdout_active_pct"])
        for row in valid_rows
        if row.get("holdout_active_pct") is not None
    ]
    avg_signal_rank_ic = _avg(signal_rank_ics)
    avg_long_short = _avg(long_short_values)
    worst_drawdown = min(drawdowns) if drawdowns else None
    positive_signal_share = _share([value > 0 for value in signal_rank_ics])
    positive_long_short_share = _share([value > 0 for value in long_short_values])
    avg_active_pct = _avg(active_pcts)
    min_active_rows = min(
        (int(row["holdout_active_rows"]) for row in valid_rows if row.get("holdout_active_rows") is not None),
        default=0,
    )
    blockers: list[str] = []
    cautions: list[str] = []
    if len(valid_rows) < int(min_folds):
        blockers.append("insufficient_valid_walkforward_folds")
    if avg_signal_rank_ic is None or avg_signal_rank_ic < float(min_avg_signal_rank_ic):
        blockers.append("weak_signal_adjusted_rank_ic")
    if avg_long_short is None or avg_long_short <= float(min_avg_long_short_spread):
        blockers.append("weak_or_negative_holdout_long_short_spread")
    if len(long_short_values) < int(min_folds):
        blockers.append("insufficient_long_short_fold_evidence")
    if positive_long_short_share is None or positive_long_short_share < float(min_positive_fold_share):
        blockers.append("inconsistent_positive_long_short_folds")
    if worst_drawdown is not None and worst_drawdown < -abs(float(max_long_short_drawdown)):
        blockers.append("excessive_holdout_long_short_drawdown")
    if avg_active_pct is None or avg_active_pct < float(min_active_pct):
        cautions.append("sparse_activation_requires_auxiliary_or_context_use")
    if min_active_rows <= 0:
        cautions.append("some_folds_have_no_active_signal_rows")
    if blockers:
        gate_status = "blocked"
    elif cautions:
        gate_status = "research_only"
    else:
        gate_status = "candidate_for_multivariate_validation"
    return {
        "fold_count": len(rows),
        "valid_fold_count": len(valid_rows),
        "avg_train_rank_ic": _avg([row.get("train_rank_ic") for row in rows]),
        "avg_holdout_rank_ic": _avg([row.get("holdout_rank_ic") for row in valid_rows]),
        "avg_signal_adjusted_holdout_rank_ic": avg_signal_rank_ic,
        "avg_holdout_rank_ic_std": _avg([row.get("holdout_rank_ic_std_by_date") for row in valid_rows]),
        "positive_signal_rank_ic_fold_share": positive_signal_share,
        "avg_train_active_inactive_spread": _avg(
            [row.get("train_active_inactive_spread") for row in rows]
        ),
        "avg_holdout_active_inactive_spread": _avg(
            [row.get("holdout_active_inactive_spread") for row in valid_rows]
        ),
        "avg_holdout_long_short_spread": avg_long_short,
        "positive_long_short_fold_share": positive_long_short_share,
        "worst_holdout_long_short_max_drawdown": worst_drawdown,
        "avg_holdout_top_quantile_return": _avg(
            [row.get("holdout_top_quantile_return") for row in valid_rows]
        ),
        "avg_holdout_bottom_quantile_return": _avg(
            [row.get("holdout_bottom_quantile_return") for row in valid_rows]
        ),
        "avg_holdout_turnover": _avg([row.get("holdout_avg_turnover") for row in valid_rows]),
        "avg_holdout_active_pct": avg_active_pct,
        "min_holdout_active_rows": min_active_rows,
        "gate_status": gate_status,
        "blockers": blockers,
        "cautions": cautions,
    }


def build_shareholder_plan_family_walkforward(
    conn: Any,
    *,
    run_id: str | None = None,
    source_eval_run_id: str | None = None,
    panel_table: str = DEFAULT_PANEL_TABLE,
    labels: list[str] | None = None,
    source_families: list[str] | None = None,
    fold_count: int = DEFAULT_FOLD_COUNT,
    train_days: int = DEFAULT_TRAIN_DAYS,
    holdout_days: int = DEFAULT_HOLDOUT_DAYS,
    min_daily_count: int = 30,
    top_quantile: float = 0.10,
    min_folds: int = 3,
    min_avg_signal_rank_ic: float = 0.001,
    min_avg_long_short_spread: float = 0.0,
    min_positive_fold_share: float = 0.50,
    max_long_short_drawdown: float = 0.50,
    min_active_pct: float = 0.05,
) -> dict[str, Any]:
    ensure_shareholder_plan_family_walkforward_tables(conn)
    if not _table_exists(conn, panel_table):
        raise RuntimeError(f"panel table missing: {panel_table}")
    panel_columns = _table_columns(conn, panel_table)
    selected_labels = [
        label for label in dict.fromkeys(labels or list(DEFAULT_LABELS)) if label in panel_columns
    ]
    if not selected_labels:
        raise RuntimeError(f"no requested follow labels exist in {panel_table}")
    selected_families = set(source_families or [str(family["source_family"]) for family in FAMILIES])
    families = [family for family in FAMILIES if str(family["source_family"]) in selected_families]
    if not families:
        raise RuntimeError("no requested source families are configured")

    source_eval_run_id = source_eval_run_id or _latest_family_eval_run_id(conn)
    run_id = run_id or f"shareholder_plan_family_wf_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    started = time.perf_counter()
    stage_timings: dict[str, float] = {}

    _progress(
        f"start run_id={run_id} panel={panel_table} labels={','.join(selected_labels)} "
        f"families={','.join(sorted(selected_families))}"
    )
    stage_started = time.perf_counter()
    panel_rows = _prepare_panel(
        conn,
        panel_table=panel_table,
        labels=selected_labels,
        start_date=None,
        end_date=None,
    )
    stage_timings["prepare_panel_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(f"prepare_panel done rows={panel_rows} elapsed={stage_timings['prepare_panel_s']:.3f}s")
    if panel_rows <= 0:
        raise RuntimeError("panel selection produced no rows")

    label_folds: dict[str, list[dict[str, Any]]] = {}
    for label in selected_labels:
        label_folds[label] = _date_folds_for_label(
            conn,
            label_name=label,
            fold_count=fold_count,
            train_days=train_days,
            holdout_days=holdout_days,
            min_daily_count=min_daily_count,
        )
    family_evidence: list[dict[str, Any]] = []
    fold_rows: list[tuple[Any, ...]] = []
    summary_rows: list[tuple[Any, ...]] = []
    status_counts: dict[str, int] = {}

    conn.execute(f"DELETE FROM {FOLD_TABLE} WHERE run_id = ?", (run_id,))
    conn.execute(f"DELETE FROM {SUMMARY_TABLE} WHERE run_id = ?", (run_id,))

    for family in families:
        source_table = str(family["source_table"])
        if not _table_exists(conn, source_table):
            family_evidence.append(
                {
                    "source_family": family["source_family"],
                    "source_table": source_table,
                    "status": "missing_source_table",
                }
            )
            continue
        stage_started = time.perf_counter()
        _progress(f"prepare_family start family={family['source_family']} source={source_table}")
        evidence = _prepare_family_features(conn, family)
        elapsed = round(time.perf_counter() - stage_started, 3)
        stage_timings[f"prepare_family_{family['source_family']}_s"] = elapsed
        _progress(
            f"prepare_family done family={family['source_family']} status={evidence['status']} "
            f"elapsed={elapsed:.3f}s"
        )
        family_evidence.append(evidence)
        if evidence.get("status") != "prepared":
            continue

        for feature_name, feature_col, window_days, _feature_kind in _feature_defs_for_family(family):
            feature_started = time.perf_counter()
            _progress(f"walkforward feature start family={family['source_family']} feature={feature_name}")
            for label in selected_labels:
                per_feature_rows: list[dict[str, Any]] = []
                folds = label_folds.get(label) or []
                for fold in folds:
                    train_stats = _period_stats(
                        conn,
                        feature_col=feature_col,
                        label_name=label,
                        start_date=fold["train_start"],
                        end_date=fold["train_end"],
                        min_daily_count=min_daily_count,
                    )
                    signal_direction = _signal_direction(train_stats)
                    holdout_stats = _period_stats(
                        conn,
                        feature_col=feature_col,
                        label_name=label,
                        start_date=fold["holdout_start"],
                        end_date=fold["holdout_end"],
                        min_daily_count=min_daily_count,
                    )
                    signal_stats = _holdout_signal_stats(
                        conn,
                        feature_col=feature_col,
                        label_name=label,
                        start_date=fold["holdout_start"],
                        end_date=fold["holdout_end"],
                        signal_direction=signal_direction,
                        min_daily_count=min_daily_count,
                        top_quantile=top_quantile,
                    )
                    row_payload = {
                        "train_rank_ic": train_stats["rank_ic"],
                        "train_active_inactive_spread": train_stats["active_inactive_spread"],
                        "signal_direction": signal_direction,
                        "holdout_rank_ic": holdout_stats["rank_ic"],
                        "holdout_rank_ic_std_by_date": holdout_stats["rank_ic_std"],
                        "holdout_daily_rank_ic_count": holdout_stats["daily_rank_ic_count"],
                        "holdout_active_inactive_spread": holdout_stats["active_inactive_spread"],
                        "holdout_long_short_spread": signal_stats["long_short_spread"],
                        "holdout_long_short_max_drawdown": signal_stats["long_short_max_drawdown"],
                        "holdout_active_pct": holdout_stats["active_pct"],
                        "holdout_active_rows": holdout_stats["active_rows"],
                        "holdout_avg_turnover": signal_stats["avg_turnover"],
                        "holdout_top_quantile_return": signal_stats["top_quantile_return"],
                        "holdout_bottom_quantile_return": signal_stats["bottom_quantile_return"],
                    }
                    per_feature_rows.append(row_payload)
                    fold_rows.append(
                        (
                            run_id,
                            source_eval_run_id,
                            panel_table,
                            family["source_family"],
                            source_table,
                            feature_name,
                            family["feature_purpose"],
                            label,
                            window_days,
                            fold["fold_id"],
                            fold["train_start"],
                            fold["train_end"],
                            fold["holdout_start"],
                            fold["holdout_end"],
                            train_stats["total_rows"],
                            train_stats["valid_rows"],
                            train_stats["active_rows"],
                            train_stats["active_pct"],
                            train_stats["rank_ic"],
                            train_stats["daily_rank_ic_count"],
                            train_stats["active_inactive_spread"],
                            signal_direction,
                            holdout_stats["total_rows"],
                            holdout_stats["valid_rows"],
                            holdout_stats["active_rows"],
                            holdout_stats["active_pct"],
                            holdout_stats["rank_ic"],
                            holdout_stats["rank_ic_std"],
                            holdout_stats["daily_rank_ic_count"],
                            holdout_stats["positive_rank_ic_share"],
                            holdout_stats["active_inactive_spread"],
                            signal_stats["top_quantile_return"],
                            signal_stats["bottom_quantile_return"],
                            signal_stats["long_short_spread"],
                            signal_stats["long_short_max_drawdown"],
                            signal_stats["avg_turnover"],
                            signal_stats["top_signal_rows"],
                            signal_stats["bottom_signal_rows"],
                            built_at,
                        )
                    )
                summary = _summarize_feature(
                    per_feature_rows,
                    min_folds=min_folds,
                    min_avg_signal_rank_ic=min_avg_signal_rank_ic,
                    min_avg_long_short_spread=min_avg_long_short_spread,
                    min_positive_fold_share=min_positive_fold_share,
                    max_long_short_drawdown=max_long_short_drawdown,
                    min_active_pct=min_active_pct,
                )
                status_counts[summary["gate_status"]] = status_counts.get(summary["gate_status"], 0) + 1
                summary_rows.append(
                    (
                        run_id,
                        source_eval_run_id,
                        panel_table,
                        family["source_family"],
                        source_table,
                        feature_name,
                        family["feature_purpose"],
                        label,
                        window_days,
                        summary["fold_count"],
                        summary["valid_fold_count"],
                        summary["avg_train_rank_ic"],
                        summary["avg_holdout_rank_ic"],
                        summary["avg_signal_adjusted_holdout_rank_ic"],
                        summary["avg_holdout_rank_ic_std"],
                        summary["positive_signal_rank_ic_fold_share"],
                        summary["avg_train_active_inactive_spread"],
                        summary["avg_holdout_active_inactive_spread"],
                        summary["avg_holdout_long_short_spread"],
                        summary["positive_long_short_fold_share"],
                        summary["worst_holdout_long_short_max_drawdown"],
                        summary["avg_holdout_top_quantile_return"],
                        summary["avg_holdout_bottom_quantile_return"],
                        summary["avg_holdout_turnover"],
                        summary["avg_holdout_active_pct"],
                        summary["min_holdout_active_rows"],
                        summary["gate_status"],
                        _json(summary["blockers"]),
                        _json(summary["cautions"]),
                        built_at,
                    )
                )
            elapsed = round(time.perf_counter() - feature_started, 3)
            stage_timings[f"feature_{family['source_family']}_{feature_col}_s"] = elapsed
            _progress(
                f"walkforward feature done family={family['source_family']} feature={feature_name} "
                f"labels={len(selected_labels)} elapsed={elapsed:.3f}s"
            )

    if fold_rows:
        stage_started = time.perf_counter()
        _progress(f"write_fold_rows start rows={len(fold_rows)}")
        conn.executemany(
            f"""
            INSERT INTO {FOLD_TABLE} (
                run_id, source_eval_run_id, panel_table, source_family, source_table,
                feature_name, feature_purpose, label_name, window_days, fold_id,
                train_start_date, train_end_date, holdout_start_date, holdout_end_date,
                train_total_rows, train_valid_rows, train_active_rows, train_active_pct,
                train_rank_ic, train_daily_rank_ic_count, train_active_inactive_spread,
                signal_direction, holdout_total_rows, holdout_valid_rows,
                holdout_active_rows, holdout_active_pct, holdout_rank_ic,
                holdout_rank_ic_std_by_date, holdout_daily_rank_ic_count,
                holdout_positive_rank_ic_share, holdout_active_inactive_spread,
                holdout_top_quantile_return, holdout_bottom_quantile_return,
                holdout_long_short_spread, holdout_long_short_max_drawdown,
                holdout_avg_turnover, holdout_top_signal_rows,
                holdout_bottom_signal_rows, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fold_rows,
        )
        stage_timings["write_fold_rows_s"] = round(time.perf_counter() - stage_started, 3)
    if summary_rows:
        stage_started = time.perf_counter()
        _progress(f"write_summary_rows start rows={len(summary_rows)}")
        conn.executemany(
            f"""
            INSERT INTO {SUMMARY_TABLE} (
                run_id, source_eval_run_id, panel_table, source_family, source_table,
                feature_name, feature_purpose, label_name, window_days,
                fold_count, valid_fold_count, avg_train_rank_ic, avg_holdout_rank_ic,
                avg_signal_adjusted_holdout_rank_ic, avg_holdout_rank_ic_std,
                positive_signal_rank_ic_fold_share, avg_train_active_inactive_spread,
                avg_holdout_active_inactive_spread, avg_holdout_long_short_spread,
                positive_long_short_fold_share, worst_holdout_long_short_max_drawdown,
                avg_holdout_top_quantile_return, avg_holdout_bottom_quantile_return,
                avg_holdout_turnover, avg_holdout_active_pct, min_holdout_active_rows,
                gate_status, blockers_json, cautions_json, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            summary_rows,
        )
        stage_timings["write_summary_rows_s"] = round(time.perf_counter() - stage_started, 3)

    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_features")
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_events")
    conn.execute("DROP TABLE IF EXISTS tmp_shareholder_plan_family_panel")
    record_actual_version(conn, FOLD_TABLE)
    record_actual_version(conn, SUMMARY_TABLE)
    conn.commit()
    duration_s = round(time.perf_counter() - started, 3)
    stage_timings["total_s"] = duration_s
    _progress(
        f"done run_id={run_id} fold_rows={len(fold_rows)} summary_rows={len(summary_rows)} "
        f"elapsed={duration_s:.3f}s"
    )
    return {
        "run_id": run_id,
        "status": "completed",
        "source_eval_run_id": source_eval_run_id,
        "panel_table": panel_table,
        "labels": selected_labels,
        "source_families": [str(family["source_family"]) for family in families],
        "panel_rows": panel_rows,
        "fold_count": int(fold_count),
        "train_days": int(train_days),
        "holdout_days": int(holdout_days),
        "top_quantile": float(top_quantile),
        "family_evidence": family_evidence,
        "inserted_fold_rows": len(fold_rows),
        "inserted_summary_rows": len(summary_rows),
        "summary_status_counts": status_counts,
        "stage_timings": stage_timings,
        "duration_s": duration_s,
        "built_at": built_at,
    }


__all__ = [
    "DEFAULT_FOLD_COUNT",
    "DEFAULT_HOLDOUT_DAYS",
    "DEFAULT_TRAIN_DAYS",
    "FOLD_TABLE",
    "SUMMARY_TABLE",
    "build_shareholder_plan_family_walkforward",
    "ensure_shareholder_plan_family_walkforward_tables",
]
