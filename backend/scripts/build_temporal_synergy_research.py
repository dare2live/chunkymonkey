#!/usr/bin/env python3
"""Point-in-time temporal relevance and feature synergy research."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_feature_panel_duck import feature_input_columns  # noqa: E402
from services.db import get_conn  # noqa: E402
from services.feature_registry import load_feature_registry  # noqa: E402
from services.model_feature_schema import holding_period_from_label  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DEFAULT_LABELS = [
    "forward_ret_5d",
    "forward_ret_10d",
    "forward_ret_20d",
    "forward_ret_60d",
    "forward_ret_90d",
]

NUMERIC_TYPES = ("DOUBLE", "REAL", "FLOAT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "DECIMAL")

DDL = """
CREATE TABLE IF NOT EXISTS mart_temporal_research_panel_quality (
    run_id TEXT PRIMARY KEY,
    source_panel_table TEXT NOT NULL,
    feature_set_id TEXT,
    source_available_date_column TEXT,
    source_date_filter_applied BOOLEAN,
    input_rows BIGINT,
    panel_rows BIGINT,
    dropped_future_source_rows BIGINT,
    stock_count BIGINT,
    min_signal_date TEXT,
    max_signal_date TEXT,
    feature_count INTEGER,
    label_count INTEGER,
    labels_json TEXT,
    features_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_feature_temporal_relevance (
    run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    feature_name TEXT NOT NULL,
    total_rows BIGINT,
    valid_rows BIGINT,
    coverage_pct DOUBLE,
    missing_pct DOUBLE,
    ic DOUBLE,
    rank_ic DOUBLE,
    rank_ic_std_by_date DOUBLE,
    daily_count INTEGER,
    same_sign_rate DOUBLE,
    top_bucket_label_mean DOUBLE,
    bottom_bucket_label_mean DOUBLE,
    long_short_spread DOUBLE,
    directional_spread DOUBLE,
    stability_score DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_name, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_temporal_relevance_rank
    ON mart_feature_temporal_relevance(run_id, label_name, directional_spread);

CREATE TABLE IF NOT EXISTS mart_feature_bucket_effect (
    run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    feature_name TEXT NOT NULL,
    bucket_index INTEGER NOT NULL,
    bucket_count INTEGER NOT NULL,
    obs_count BIGINT,
    avg_label DOUBLE,
    median_label DOUBLE,
    win_rate DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_name, feature_name, bucket_index)
);
CREATE INDEX IF NOT EXISTS idx_temporal_bucket_feature
    ON mart_feature_bucket_effect(run_id, label_name, feature_name);

CREATE TABLE IF NOT EXISTS mart_feature_relevance_stability (
    run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    feature_name TEXT NOT NULL,
    fold_id TEXT NOT NULL,
    holdout_start TEXT NOT NULL,
    holdout_end TEXT NOT NULL,
    valid_rows BIGINT,
    rank_ic DOUBLE,
    long_short_spread DOUBLE,
    directional_spread DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_name, feature_name, fold_id)
);
CREATE INDEX IF NOT EXISTS idx_temporal_stability_run
    ON mart_feature_relevance_stability(run_id, label_name, fold_id);

CREATE TABLE IF NOT EXISTS mart_feature_pair_synergy (
    run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    feature_a TEXT NOT NULL,
    feature_b TEXT NOT NULL,
    sign_a INTEGER,
    sign_b INTEGER,
    valid_rows BIGINT,
    joint_obs_count BIGINT,
    baseline_label_mean DOUBLE,
    feature_a_active_label_mean DOUBLE,
    feature_b_active_label_mean DOUBLE,
    best_standalone_label_mean DOUBLE,
    joint_active_label_mean DOUBLE,
    joint_uplift DOUBLE,
    feature_corr DOUBLE,
    interaction_score DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_name, feature_a, feature_b)
);
CREATE INDEX IF NOT EXISTS idx_temporal_pair_score
    ON mart_feature_pair_synergy(run_id, label_name, interaction_score);

CREATE TABLE IF NOT EXISTS mart_feature_interaction_candidate (
    run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    feature_a TEXT NOT NULL,
    feature_b TEXT NOT NULL,
    selected BOOLEAN NOT NULL,
    selection_reason TEXT,
    joint_uplift DOUBLE,
    interaction_score DOUBLE,
    valid_rows BIGINT,
    joint_obs_count BIGINT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_name, feature_a, feature_b)
);

CREATE TABLE IF NOT EXISTS mart_feature_conditional_synergy (
    run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    condition_feature TEXT NOT NULL,
    response_feature TEXT NOT NULL,
    condition_sign INTEGER,
    response_sign INTEGER,
    valid_rows BIGINT,
    condition_obs_count BIGINT,
    response_active_obs_count BIGINT,
    conditional_response_obs_count BIGINT,
    baseline_label_mean DOUBLE,
    condition_label_mean DOUBLE,
    response_active_label_mean DOUBLE,
    conditional_response_label_mean DOUBLE,
    response_uplift DOUBLE,
    conditional_response_uplift DOUBLE,
    incremental_uplift DOUBLE,
    feature_corr DOUBLE,
    interaction_score DOUBLE,
    selected BOOLEAN NOT NULL,
    selection_reason TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label_name, condition_feature, response_feature)
);
CREATE INDEX IF NOT EXISTS idx_conditional_synergy_score
    ON mart_feature_conditional_synergy(run_id, label_name, interaction_score);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _progress(message: str) -> None:
    print(f"[temporal_synergy] {utc_now_iso()} {message}", flush=True)


def _sum_count_dicts(items: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                out[key] = out.get(key, 0) + value
    return out


def _quote_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _table_columns(conn: Any, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE {_quote_relation(table)}").fetchall()
    }


def _is_numeric_type(type_name: str) -> bool:
    upper = type_name.upper()
    return any(token in upper for token in NUMERIC_TYPES)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _registry_role_features(
    columns: dict[str, str],
    labels: list[str],
    feature_roles: list[str],
) -> list[str]:
    label_set = set(labels)
    role_set = set(feature_roles)
    registry = load_feature_registry()
    return [
        name
        for name, spec in registry.features.items()
        if spec.enabled
        and not spec.label
        and spec.feature_role in role_set
        and name in columns
        and name not in label_set
        and _is_numeric_type(columns[name])
    ]


def _default_features(
    columns: dict[str, str],
    labels: list[str],
    *,
    feature_roles: list[str] | None = None,
) -> list[str]:
    label_set = set(labels)
    if feature_roles:
        role_features = _registry_role_features(columns, labels, feature_roles)
        if role_features:
            return role_features
    registry_features = [
        feature
        for feature in feature_input_columns()
        if feature in columns
        and feature not in label_set
        and _is_numeric_type(columns[feature])
    ]
    if registry_features:
        return registry_features
    excluded = {
        "stock_code",
        "stock_name",
        "date",
        "signal_date",
        "feature_set_id",
        "built_at",
        "updated_at",
        "source_available_date",
        "max_source_available_date",
        "source_date",
        "regime_flag",
        "kline_source_name",
        "kline_source_tier",
        "kline_is_fallback",
        *label_set,
    }
    return [
        feature
        for feature, dtype in columns.items()
        if feature not in excluded and _is_numeric_type(dtype)
    ]


def _build_base_filters(
    *,
    columns: dict[str, str],
    feature_set_id: str | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[list[str], list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if feature_set_id:
        if "feature_set_id" not in columns:
            raise RuntimeError("feature_set_id was provided but source panel has no feature_set_id column")
        filters.append("feature_set_id = ?")
        params.append(feature_set_id)
    if start_date:
        filters.append("date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("date <= ?")
        params.append(end_date)
    return filters, params


def _date_filter_sql(filters: list[str]) -> str:
    return " AND ".join(filters) if filters else "TRUE"


def _source_date_filter(source_available_date_column: str | None) -> str:
    if not source_available_date_column:
        return "TRUE"
    source_q = _quote_ident(source_available_date_column)
    return (
        f"{source_q} IS NULL "
        f"OR TRY_CAST({source_q} AS DATE) IS NULL "
        f"OR TRY_CAST({source_q} AS DATE) <= TRY_CAST(date AS DATE)"
    )


def _create_temporal_panel(
    conn: Any,
    *,
    run_id: str,
    panel_table: str,
    feature_set_id: str | None,
    features: list[str],
    labels: list[str],
    base_filters: list[str],
    params: list[Any],
    source_available_date_column: str | None,
    materialize_panel: bool,
    built_at: str,
) -> dict[str, Any]:
    base_where_sql = _date_filter_sql(base_filters)
    source_filter_sql = _source_date_filter(source_available_date_column)
    select_cols = [
        "stock_code",
        "date AS signal_date",
        *[_quote_ident(label) for label in labels],
        *[_quote_ident(feature) for feature in features],
    ]
    if source_available_date_column:
        select_cols.append(f"{_quote_ident(source_available_date_column)} AS max_source_available_date")
    if feature_set_id is not None:
        select_cols.append(f"{_quote_literal(feature_set_id)} AS feature_set_id")
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE __temporal_research_base AS
        SELECT {', '.join(select_cols)}
          FROM {_quote_relation(panel_table)}
         WHERE {base_where_sql}
           AND {source_filter_sql}
        """,
        params,
    )
    if materialize_panel:
        conn.execute(
            f"""
            CREATE OR REPLACE TABLE mart_temporal_research_panel AS
            SELECT {_quote_literal(run_id)} AS run_id,
                   {_quote_literal(panel_table)} AS source_panel_table,
                   *
              FROM __temporal_research_base
            """
        )
    input_rows = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {_quote_relation(panel_table)} WHERE {base_where_sql}",
            params,
        ).fetchone()[0] or 0
    )
    dropped_future_rows = 0
    if source_available_date_column:
        source_q = _quote_ident(source_available_date_column)
        dropped_future_rows = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM {_quote_relation(panel_table)}
                 WHERE {base_where_sql}
                   AND {source_q} IS NOT NULL
                   AND TRY_CAST({source_q} AS DATE) > TRY_CAST(date AS DATE)
                """,
                params,
            ).fetchone()[0] or 0
        )
    quality = conn.execute(
        """
        SELECT COUNT(*) AS panel_rows,
               COUNT(DISTINCT stock_code) AS stock_count,
               MIN(signal_date) AS min_signal_date,
               MAX(signal_date) AS max_signal_date
          FROM __temporal_research_base
        """
    ).fetchone()
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_temporal_research_panel_quality (
            run_id, source_panel_table, feature_set_id,
            source_available_date_column, source_date_filter_applied,
            input_rows, panel_rows, dropped_future_source_rows,
            stock_count, min_signal_date, max_signal_date,
            feature_count, label_count, labels_json, features_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            panel_table,
            feature_set_id,
            source_available_date_column,
            bool(source_available_date_column),
            input_rows,
            int(quality["panel_rows"] or 0),
            dropped_future_rows,
            int(quality["stock_count"] or 0),
            quality["min_signal_date"],
            quality["max_signal_date"],
            len(features),
            len(labels),
            json.dumps(labels, ensure_ascii=False),
            json.dumps(features, ensure_ascii=False),
            built_at,
        ),
    )
    return {
        "input_rows": input_rows,
        "panel_rows": int(quality["panel_rows"] or 0),
        "dropped_future_source_rows": dropped_future_rows,
        "stock_count": int(quality["stock_count"] or 0),
    }


def _valid_where(feature: str, label: str) -> str:
    feature_q = _quote_ident(feature)
    label_q = _quote_ident(label)
    return (
        f"{feature_q} IS NOT NULL AND {label_q} IS NOT NULL "
        f"AND ISFINITE(CAST({feature_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({label_q} AS DOUBLE))"
    )


def _compute_relevance(
    conn: Any,
    *,
    feature: str,
    label: str,
    total_rows: int,
    min_daily_count: int,
    bucket_count: int,
    base_table: str = "__temporal_research_base",
) -> dict[str, Any]:
    feature_q = _quote_ident(feature)
    label_q = _quote_ident(label)
    valid_where = _valid_where(feature, label)
    coverage_row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN {feature_q} IS NOT NULL AND ISFINITE(CAST({feature_q} AS DOUBLE)) THEN 1 ELSE 0 END) AS covered,
            SUM(CASE WHEN {valid_where} THEN 1 ELSE 0 END) AS valid_rows
          FROM {base_table}
        """
    ).fetchone()
    covered = int(coverage_row["covered"] or 0)
    valid_rows = int(coverage_row["valid_rows"] or 0)
    coverage_pct = 100.0 * covered / total_rows if total_rows else 0.0
    ic_row = conn.execute(
        f"""
        SELECT corr(CAST({feature_q} AS DOUBLE), CAST({label_q} AS DOUBLE)) AS ic
          FROM {base_table}
         WHERE {valid_where}
        """
    ).fetchone()
    rank_row = conn.execute(
        f"""
        WITH valid AS (
            SELECT signal_date,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM {base_table}
             WHERE {valid_where}
        ),
        ranked AS (
            SELECT signal_date,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY feature_value) AS feature_rank,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY label_value) AS label_rank
              FROM valid
        ),
        daily AS (
            SELECT signal_date,
                   COUNT(*) AS n,
                   corr(feature_rank, label_rank) AS rank_ic
              FROM ranked
             GROUP BY signal_date
            HAVING COUNT(*) >= ?
        ),
        valid_daily AS (
            SELECT rank_ic
              FROM daily
             WHERE rank_ic IS NOT NULL AND ISFINITE(rank_ic)
        )
        SELECT AVG(rank_ic) AS rank_ic,
               CASE WHEN COUNT(rank_ic) >= 2
                    THEN STDDEV_SAMP(rank_ic)
                    ELSE NULL END AS rank_ic_std,
               COUNT(rank_ic) AS daily_count,
               AVG(CASE WHEN rank_ic > 0 THEN 1.0 ELSE 0.0 END) AS same_sign_rate
          FROM valid_daily
        """,
        [min_daily_count],
    ).fetchone()
    bucket_row = conn.execute(
        f"""
        WITH valid AS (
            SELECT signal_date,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM {base_table}
             WHERE {valid_where}
        ),
        bucketed AS (
            SELECT label_value,
                   NTILE({int(bucket_count)}) OVER (
                       PARTITION BY signal_date
                       ORDER BY feature_value
                   ) AS bucket_index
              FROM valid
        )
        SELECT AVG(CASE WHEN bucket_index = {int(bucket_count)} THEN label_value END) AS top_mean,
               AVG(CASE WHEN bucket_index = 1 THEN label_value END) AS bottom_mean
          FROM bucketed
        """
    ).fetchone()
    rank_ic = _finite_float(rank_row["rank_ic"] if rank_row else None)
    rank_ic_std = _finite_float(rank_row["rank_ic_std"] if rank_row else None)
    top_mean = _finite_float(bucket_row["top_mean"] if bucket_row else None)
    bottom_mean = _finite_float(bucket_row["bottom_mean"] if bucket_row else None)
    long_short = top_mean - bottom_mean if top_mean is not None and bottom_mean is not None else None
    direction = 1 if (rank_ic or 0.0) >= 0 else -1
    directional_spread = direction * long_short if long_short is not None else None
    same_sign_rate = _finite_float(rank_row["same_sign_rate"] if rank_row else None)
    daily_count = int(rank_row["daily_count"] or 0) if rank_row else 0
    stability_score = None
    if rank_ic is not None and rank_ic_std is not None:
        stability_score = abs(rank_ic) / (1.0 + rank_ic_std)
    elif rank_ic is not None:
        stability_score = abs(rank_ic)
    return {
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "coverage_pct": coverage_pct,
        "missing_pct": 100.0 - coverage_pct,
        "ic": _finite_float(ic_row["ic"] if ic_row else None),
        "rank_ic": rank_ic,
        "rank_ic_std_by_date": rank_ic_std,
        "daily_count": daily_count,
        "same_sign_rate": same_sign_rate,
        "top_bucket_label_mean": top_mean,
        "bottom_bucket_label_mean": bottom_mean,
        "long_short_spread": long_short,
        "directional_spread": directional_spread,
        "stability_score": stability_score,
    }


def _write_bucket_effects(
    conn: Any,
    *,
    run_id: str,
    feature: str,
    label: str,
    bucket_count: int,
    built_at: str,
) -> int:
    feature_q = _quote_ident(feature)
    label_q = _quote_ident(label)
    valid_where = _valid_where(feature, label)
    rows = conn.execute(
        f"""
        WITH valid AS (
            SELECT signal_date,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM __temporal_research_base
             WHERE {valid_where}
        ),
        bucketed AS (
            SELECT label_value,
                   NTILE({int(bucket_count)}) OVER (
                       PARTITION BY signal_date
                       ORDER BY feature_value
                   ) AS bucket_index
              FROM valid
        )
        SELECT bucket_index,
               COUNT(*) AS obs_count,
               AVG(label_value) AS avg_label,
               MEDIAN(label_value) AS median_label,
               AVG(CASE WHEN label_value > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
          FROM bucketed
         GROUP BY bucket_index
         ORDER BY bucket_index
        """
    ).fetchall()
    horizon_days = holding_period_from_label(label)
    payload = [
        (
            run_id,
            label,
            horizon_days,
            feature,
            int(row["bucket_index"]),
            int(bucket_count),
            int(row["obs_count"] or 0),
            _finite_float(row["avg_label"]),
            _finite_float(row["median_label"]),
            _finite_float(row["win_rate"]),
            built_at,
        )
        for row in rows
    ]
    if payload:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_bucket_effect (
                run_id, label_name, horizon_days, feature_name,
                bucket_index, bucket_count, obs_count, avg_label,
                median_label, win_rate, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
    return len(payload)


def _fold_ranges(conn: Any, *, label: str, folds: int) -> list[dict[str, str]]:
    folds = max(int(folds), 0)
    if folds <= 0:
        return []
    label_q = _quote_ident(label)
    dates = [
        str(row[0])
        for row in conn.execute(
            f"""
            SELECT DISTINCT signal_date
              FROM __temporal_research_base
             WHERE {label_q} IS NOT NULL
             ORDER BY signal_date
            """
        ).fetchall()
    ]
    if not dates:
        return []
    folds = min(folds, len(dates))
    out = []
    for idx in range(folds):
        start = idx * len(dates) // folds
        end = (idx + 1) * len(dates) // folds
        holdout = dates[start:end]
        if holdout:
            out.append(
                {
                    "fold_id": f"fold_{idx + 1:03d}",
                    "holdout_start": holdout[0],
                    "holdout_end": holdout[-1],
                }
            )
    return out


def _write_stability(
    conn: Any,
    *,
    run_id: str,
    feature: str,
    label: str,
    folds: int,
    min_daily_count: int,
    bucket_count: int,
    built_at: str,
) -> int:
    ranges = _fold_ranges(conn, label=label, folds=folds)
    rows = []
    horizon_days = holding_period_from_label(label)
    for fold in ranges:
        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE __temporal_research_fold AS
            SELECT *
              FROM __temporal_research_base
             WHERE signal_date >= ? AND signal_date <= ?
            """,
            [fold["holdout_start"], fold["holdout_end"]],
        )
        total_rows = int(conn.execute("SELECT COUNT(*) FROM __temporal_research_fold").fetchone()[0] or 0)
        stats = _compute_relevance_for_table(
            conn,
            feature=feature,
            label=label,
            total_rows=total_rows,
            min_daily_count=min_daily_count,
            bucket_count=bucket_count,
            base_table="__temporal_research_fold",
        )
        rows.append(
            (
                run_id,
                label,
                horizon_days,
                feature,
                fold["fold_id"],
                fold["holdout_start"],
                fold["holdout_end"],
                stats["valid_rows"],
                stats["rank_ic"],
                stats["long_short_spread"],
                stats["directional_spread"],
                built_at,
            )
        )
    conn.execute("DROP TABLE IF EXISTS __temporal_research_fold")
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_relevance_stability (
                run_id, label_name, horizon_days, feature_name, fold_id,
                holdout_start, holdout_end, valid_rows, rank_ic,
                long_short_spread, directional_spread, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _compute_relevance_for_table(
    conn: Any,
    *,
    feature: str,
    label: str,
    total_rows: int,
    min_daily_count: int,
    bucket_count: int,
    base_table: str,
) -> dict[str, Any]:
    return _compute_relevance(
        conn,
        feature=feature,
        label=label,
        total_rows=total_rows,
        min_daily_count=min_daily_count,
        bucket_count=bucket_count,
        base_table=base_table,
    )


def _insert_relevance_rows(
    conn: Any,
    *,
    run_id: str,
    labels: list[str],
    features: list[str],
    min_daily_count: int,
    bucket_count: int,
    folds: int,
    built_at: str,
) -> dict[str, Any]:
    total_rows = int(conn.execute("SELECT COUNT(*) FROM __temporal_research_base").fetchone()[0] or 0)
    relevance_rows = []
    bucket_rows = 0
    stability_rows = 0
    stats_by_label_feature: dict[tuple[str, str], dict[str, Any]] = {}
    for label in labels:
        horizon_days = holding_period_from_label(label)
        _progress(f"relevance label_start label={label} features={len(features)}")
        for idx, feature in enumerate(features, start=1):
            feature_t0 = time.perf_counter()
            _progress(f"relevance feature_start label={label} feature={feature} {idx}/{len(features)}")
            stats = _compute_relevance(
                conn,
                feature=feature,
                label=label,
                total_rows=total_rows,
                min_daily_count=min_daily_count,
                bucket_count=bucket_count,
            )
            stats_by_label_feature[(label, feature)] = stats
            relevance_rows.append(
                (
                    run_id,
                    label,
                    horizon_days,
                    feature,
                    stats["total_rows"],
                    stats["valid_rows"],
                    stats["coverage_pct"],
                    stats["missing_pct"],
                    stats["ic"],
                    stats["rank_ic"],
                    stats["rank_ic_std_by_date"],
                    stats["daily_count"],
                    stats["same_sign_rate"],
                    stats["top_bucket_label_mean"],
                    stats["bottom_bucket_label_mean"],
                    stats["long_short_spread"],
                    stats["directional_spread"],
                    stats["stability_score"],
                    built_at,
                )
            )
            bucket_rows += _write_bucket_effects(
                conn,
                run_id=run_id,
                feature=feature,
                label=label,
                bucket_count=bucket_count,
                built_at=built_at,
            )
            _progress(
                f"relevance feature_done label={label} feature={feature} "
                f"valid_rows={stats.get('valid_rows')} "
                f"rank_ic={stats.get('rank_ic')} elapsed={time.perf_counter() - feature_t0:.3f}s"
            )
            stability_rows += _write_stability(
                conn,
                run_id=run_id,
                feature=feature,
                label=label,
                folds=folds,
                min_daily_count=min_daily_count,
                bucket_count=bucket_count,
                built_at=built_at,
            )
    if relevance_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_temporal_relevance (
                run_id, label_name, horizon_days, feature_name,
                total_rows, valid_rows, coverage_pct, missing_pct,
                ic, rank_ic, rank_ic_std_by_date, daily_count,
                same_sign_rate, top_bucket_label_mean, bottom_bucket_label_mean,
                long_short_spread, directional_spread, stability_score, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            relevance_rows,
        )
    return {
        "relevance_rows": len(relevance_rows),
        "bucket_rows": bucket_rows,
        "stability_rows": stability_rows,
        "stats": stats_by_label_feature,
    }


def _pair_metrics(
    conn: Any,
    *,
    label: str,
    feature_a: str,
    feature_b: str,
    sign_a: int,
    sign_b: int,
    active_quantile: float,
) -> dict[str, Any]:
    label_q = _quote_ident(label)
    a_q = _quote_ident(feature_a)
    b_q = _quote_ident(feature_b)
    valid_where = (
        f"{label_q} IS NOT NULL AND {a_q} IS NOT NULL AND {b_q} IS NOT NULL "
        f"AND ISFINITE(CAST({label_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({a_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({b_q} AS DOUBLE))"
    )
    active_a = "rank_a" if sign_a >= 0 else "(1.0 - rank_a)"
    active_b = "rank_b" if sign_b >= 0 else "(1.0 - rank_b)"
    row = conn.execute(
        f"""
        WITH valid AS (
            SELECT signal_date,
                   CAST({label_q} AS DOUBLE) AS label_value,
                   CAST({a_q} AS DOUBLE) AS feature_a,
                   CAST({b_q} AS DOUBLE) AS feature_b
              FROM __temporal_research_base
             WHERE {valid_where}
        ),
        ranked AS (
            SELECT *,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY feature_a) AS rank_a,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY feature_b) AS rank_b
              FROM valid
        ),
        active AS (
            SELECT *,
                   {active_a} AS active_a,
                   {active_b} AS active_b
              FROM ranked
        )
        SELECT COUNT(*) AS valid_rows,
               SUM(CASE WHEN active_a >= ? AND active_b >= ? THEN 1 ELSE 0 END) AS joint_obs,
               AVG(label_value) AS baseline_mean,
               AVG(CASE WHEN active_a >= ? THEN label_value END) AS a_mean,
               AVG(CASE WHEN active_b >= ? THEN label_value END) AS b_mean,
               AVG(CASE WHEN active_a >= ? AND active_b >= ? THEN label_value END) AS joint_mean,
               corr(feature_a, feature_b) AS feature_corr
          FROM active
        """,
        [
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
        ],
    ).fetchone()
    a_mean = _finite_float(row["a_mean"] if row else None)
    b_mean = _finite_float(row["b_mean"] if row else None)
    joint_mean = _finite_float(row["joint_mean"] if row else None)
    best_standalone = None
    if a_mean is not None or b_mean is not None:
        best_standalone = max(value for value in (a_mean, b_mean) if value is not None)
    joint_uplift = joint_mean - best_standalone if joint_mean is not None and best_standalone is not None else None
    joint_obs = int(row["joint_obs"] or 0) if row else 0
    interaction_score = None
    if joint_uplift is not None:
        interaction_score = joint_uplift * math.sqrt(max(joint_obs, 1))
    return {
        "valid_rows": int(row["valid_rows"] or 0) if row else 0,
        "joint_obs_count": joint_obs,
        "baseline_label_mean": _finite_float(row["baseline_mean"] if row else None),
        "feature_a_active_label_mean": a_mean,
        "feature_b_active_label_mean": b_mean,
        "best_standalone_label_mean": best_standalone,
        "joint_active_label_mean": joint_mean,
        "joint_uplift": joint_uplift,
        "feature_corr": _finite_float(row["feature_corr"] if row else None),
        "interaction_score": interaction_score,
    }


def _write_pair_synergy(
    conn: Any,
    *,
    run_id: str,
    label: str,
    features: list[str],
    stats: dict[tuple[str, str], dict[str, Any]],
    top_pair_features: int,
    max_pairs: int,
    min_pair_valid_rows: int,
    min_joint_obs: int,
    active_quantile: float,
    interaction_uplift_threshold: float,
    built_at: str,
) -> dict[str, int]:
    scored = []
    for feature in features:
        item = stats.get((label, feature), {})
        rank_ic = item.get("rank_ic")
        directional_spread = item.get("directional_spread")
        score = abs(float(rank_ic or 0.0)) + abs(float(directional_spread or 0.0))
        scored.append((score, feature))
    candidate_features = [
        feature
        for _, feature in sorted(scored, key=lambda item: (-item[0], item[1]))[: max(int(top_pair_features), 0)]
    ]
    pairs = list(itertools.combinations(candidate_features, 2))[: max(int(max_pairs), 0)]
    horizon_days = holding_period_from_label(label)
    pair_rows = []
    candidate_rows = []
    for feature_a, feature_b in pairs:
        sign_a = 1 if float(stats.get((label, feature_a), {}).get("rank_ic") or 0.0) >= 0 else -1
        sign_b = 1 if float(stats.get((label, feature_b), {}).get("rank_ic") or 0.0) >= 0 else -1
        metrics = _pair_metrics(
            conn,
            label=label,
            feature_a=feature_a,
            feature_b=feature_b,
            sign_a=sign_a,
            sign_b=sign_b,
            active_quantile=active_quantile,
        )
        pair_rows.append(
            (
                run_id,
                label,
                horizon_days,
                feature_a,
                feature_b,
                sign_a,
                sign_b,
                metrics["valid_rows"],
                metrics["joint_obs_count"],
                metrics["baseline_label_mean"],
                metrics["feature_a_active_label_mean"],
                metrics["feature_b_active_label_mean"],
                metrics["best_standalone_label_mean"],
                metrics["joint_active_label_mean"],
                metrics["joint_uplift"],
                metrics["feature_corr"],
                metrics["interaction_score"],
                built_at,
            )
        )
        selected = (
            metrics["valid_rows"] >= min_pair_valid_rows
            and metrics["joint_obs_count"] >= min_joint_obs
            and metrics["joint_uplift"] is not None
            and metrics["joint_uplift"] >= interaction_uplift_threshold
        )
        if metrics["valid_rows"] < min_pair_valid_rows:
            reason = "low_valid_rows"
        elif metrics["joint_obs_count"] < min_joint_obs:
            reason = "low_joint_observation"
        elif metrics["joint_uplift"] is None:
            reason = "missing_joint_uplift"
        elif metrics["joint_uplift"] < interaction_uplift_threshold:
            reason = "uplift_below_threshold"
        else:
            reason = "joint_effect_exceeds_standalone"
        candidate_rows.append(
            (
                run_id,
                label,
                horizon_days,
                feature_a,
                feature_b,
                bool(selected),
                reason,
                metrics["joint_uplift"],
                metrics["interaction_score"],
                metrics["valid_rows"],
                metrics["joint_obs_count"],
                built_at,
            )
        )
    if pair_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_pair_synergy (
                run_id, label_name, horizon_days, feature_a, feature_b,
                sign_a, sign_b, valid_rows, joint_obs_count,
                baseline_label_mean, feature_a_active_label_mean,
                feature_b_active_label_mean, best_standalone_label_mean,
                joint_active_label_mean, joint_uplift, feature_corr,
                interaction_score, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            pair_rows,
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_interaction_candidate (
                run_id, label_name, horizon_days, feature_a, feature_b,
                selected, selection_reason, joint_uplift, interaction_score,
                valid_rows, joint_obs_count, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            candidate_rows,
        )
    return {
        "pair_rows": len(pair_rows),
        "interaction_candidate_rows": len(candidate_rows),
        "selected_interaction_rows": sum(1 for row in candidate_rows if row[5]),
    }


def _conditional_metrics(
    conn: Any,
    *,
    label: str,
    condition_feature: str,
    response_feature: str,
    condition_sign: int,
    response_sign: int,
    active_quantile: float,
) -> dict[str, Any]:
    label_q = _quote_ident(label)
    condition_q = _quote_ident(condition_feature)
    response_q = _quote_ident(response_feature)
    valid_where = (
        f"{label_q} IS NOT NULL AND {condition_q} IS NOT NULL AND {response_q} IS NOT NULL "
        f"AND ISFINITE(CAST({label_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({condition_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({response_q} AS DOUBLE))"
    )
    condition_active = "condition_rank" if condition_sign >= 0 else "(1.0 - condition_rank)"
    response_active = "response_rank" if response_sign >= 0 else "(1.0 - response_rank)"
    row = conn.execute(
        f"""
        WITH valid AS (
            SELECT signal_date,
                   CAST({label_q} AS DOUBLE) AS label_value,
                   CAST({condition_q} AS DOUBLE) AS condition_value,
                   CAST({response_q} AS DOUBLE) AS response_value
              FROM __temporal_research_base
             WHERE {valid_where}
        ),
        ranked AS (
            SELECT *,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY condition_value) AS condition_rank,
                   PERCENT_RANK() OVER (PARTITION BY signal_date ORDER BY response_value) AS response_rank
              FROM valid
        ),
        active AS (
            SELECT *,
                   {condition_active} AS condition_active,
                   {response_active} AS response_active
              FROM ranked
        )
        SELECT COUNT(*) AS valid_rows,
               SUM(CASE WHEN condition_active >= ? THEN 1 ELSE 0 END) AS condition_obs,
               SUM(CASE WHEN response_active >= ? THEN 1 ELSE 0 END) AS response_obs,
               SUM(CASE WHEN condition_active >= ? AND response_active >= ? THEN 1 ELSE 0 END) AS conditional_response_obs,
               AVG(label_value) AS baseline_mean,
               AVG(CASE WHEN condition_active >= ? THEN label_value END) AS condition_mean,
               AVG(CASE WHEN response_active >= ? THEN label_value END) AS response_mean,
               AVG(CASE WHEN condition_active >= ? AND response_active >= ? THEN label_value END) AS conditional_response_mean,
               corr(condition_value, response_value) AS feature_corr
          FROM active
        """,
        [
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
            active_quantile,
        ],
    ).fetchone()
    baseline_mean = _finite_float(row["baseline_mean"] if row else None)
    condition_mean = _finite_float(row["condition_mean"] if row else None)
    response_mean = _finite_float(row["response_mean"] if row else None)
    conditional_response_mean = _finite_float(row["conditional_response_mean"] if row else None)
    response_uplift = response_mean - baseline_mean if response_mean is not None and baseline_mean is not None else None
    conditional_uplift = (
        conditional_response_mean - condition_mean
        if conditional_response_mean is not None and condition_mean is not None
        else None
    )
    incremental_uplift = (
        conditional_uplift - response_uplift
        if conditional_uplift is not None and response_uplift is not None
        else None
    )
    conditional_obs = int(row["conditional_response_obs"] or 0) if row else 0
    interaction_score = None
    if incremental_uplift is not None:
        interaction_score = incremental_uplift * math.sqrt(max(conditional_obs, 1))
    return {
        "valid_rows": int(row["valid_rows"] or 0) if row else 0,
        "condition_obs_count": int(row["condition_obs"] or 0) if row else 0,
        "response_active_obs_count": int(row["response_obs"] or 0) if row else 0,
        "conditional_response_obs_count": conditional_obs,
        "baseline_label_mean": baseline_mean,
        "condition_label_mean": condition_mean,
        "response_active_label_mean": response_mean,
        "conditional_response_label_mean": conditional_response_mean,
        "response_uplift": response_uplift,
        "conditional_response_uplift": conditional_uplift,
        "incremental_uplift": incremental_uplift,
        "feature_corr": _finite_float(row["feature_corr"] if row else None),
        "interaction_score": interaction_score,
    }


def _write_conditional_synergy(
    conn: Any,
    *,
    run_id: str,
    label: str,
    features: list[str],
    stats: dict[tuple[str, str], dict[str, Any]],
    top_pair_features: int,
    max_conditional_pairs: int,
    min_pair_valid_rows: int,
    min_joint_obs: int,
    active_quantile: float,
    conditional_uplift_threshold: float,
    built_at: str,
) -> dict[str, int]:
    if max_conditional_pairs <= 0:
        return {"conditional_synergy_rows": 0, "selected_conditional_rows": 0}
    scored = []
    for feature in features:
        item = stats.get((label, feature), {})
        rank_ic = item.get("rank_ic")
        directional_spread = item.get("directional_spread")
        score = abs(float(rank_ic or 0.0)) + abs(float(directional_spread or 0.0))
        scored.append((score, feature))
    candidate_features = [
        feature
        for _, feature in sorted(scored, key=lambda item: (-item[0], item[1]))[: max(int(top_pair_features), 0)]
    ]
    ordered_pairs = list(itertools.permutations(candidate_features, 2))[: max(int(max_conditional_pairs), 0)]
    horizon_days = holding_period_from_label(label)
    rows = []
    for condition_feature, response_feature in ordered_pairs:
        condition_sign = 1 if float(stats.get((label, condition_feature), {}).get("rank_ic") or 0.0) >= 0 else -1
        response_sign = 1 if float(stats.get((label, response_feature), {}).get("rank_ic") or 0.0) >= 0 else -1
        metrics = _conditional_metrics(
            conn,
            label=label,
            condition_feature=condition_feature,
            response_feature=response_feature,
            condition_sign=condition_sign,
            response_sign=response_sign,
            active_quantile=active_quantile,
        )
        selected = (
            metrics["valid_rows"] >= min_pair_valid_rows
            and metrics["conditional_response_obs_count"] >= min_joint_obs
            and metrics["incremental_uplift"] is not None
            and metrics["incremental_uplift"] >= conditional_uplift_threshold
        )
        if metrics["valid_rows"] < min_pair_valid_rows:
            reason = "low_valid_rows"
        elif metrics["conditional_response_obs_count"] < min_joint_obs:
            reason = "low_conditional_observation"
        elif metrics["incremental_uplift"] is None:
            reason = "missing_incremental_uplift"
        elif metrics["incremental_uplift"] < conditional_uplift_threshold:
            reason = "incremental_uplift_below_threshold"
        else:
            reason = "conditional_response_exceeds_unconditional"
        rows.append(
            (
                run_id,
                label,
                horizon_days,
                condition_feature,
                response_feature,
                condition_sign,
                response_sign,
                metrics["valid_rows"],
                metrics["condition_obs_count"],
                metrics["response_active_obs_count"],
                metrics["conditional_response_obs_count"],
                metrics["baseline_label_mean"],
                metrics["condition_label_mean"],
                metrics["response_active_label_mean"],
                metrics["conditional_response_label_mean"],
                metrics["response_uplift"],
                metrics["conditional_response_uplift"],
                metrics["incremental_uplift"],
                metrics["feature_corr"],
                metrics["interaction_score"],
                bool(selected),
                reason,
                built_at,
            )
        )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_conditional_synergy (
                run_id, label_name, horizon_days, condition_feature,
                response_feature, condition_sign, response_sign, valid_rows,
                condition_obs_count, response_active_obs_count,
                conditional_response_obs_count, baseline_label_mean,
                condition_label_mean, response_active_label_mean,
                conditional_response_label_mean, response_uplift,
                conditional_response_uplift, incremental_uplift,
                feature_corr, interaction_score, selected,
                selection_reason, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return {
        "conditional_synergy_rows": len(rows),
        "selected_conditional_rows": sum(1 for row in rows if row[20]),
    }


def build_temporal_synergy_research(
    conn: Any,
    *,
    run_id: str,
    panel_table: str = "fact_feature_panel_candidate",
    feature_set_id: str | None = None,
    features: list[str] | None = None,
    feature_roles: list[str] | None = None,
    labels: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source_available_date_column: str | None = None,
    min_daily_count: int = 10,
    bucket_count: int = 5,
    folds: int = 0,
    top_pair_features: int = 20,
    max_pairs: int = 200,
    min_pair_valid_rows: int = 50,
    min_joint_obs: int = 10,
    active_quantile: float = 0.8,
    interaction_uplift_threshold: float = 0.0,
    max_conditional_pairs: int = 0,
    conditional_uplift_threshold: float = 0.0,
    materialize_panel: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    t0 = time.perf_counter()
    started_at = utc_now_iso()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    _progress(
        f"start run_id={run_id} panel={panel_table} "
        f"feature_roles={','.join(feature_roles or []) or 'model_inputs'}"
    )
    columns = _table_columns(conn, panel_table)
    if "date" not in columns or "stock_code" not in columns:
        raise RuntimeError(f"{panel_table} must contain stock_code and date columns")
    labels = [label for label in (labels or DEFAULT_LABELS) if label in columns]
    if not labels:
        raise RuntimeError(f"no requested labels exist in {panel_table}")
    requested_features = features or _default_features(
        columns,
        labels,
        feature_roles=feature_roles,
    )
    usable_features = [
        feature
        for feature in dict.fromkeys(requested_features)
        if feature in columns
        and feature not in labels
        and _is_numeric_type(columns[feature])
    ]
    if not usable_features:
        raise RuntimeError(f"no numeric research features exist in {panel_table}")
    if source_available_date_column and source_available_date_column not in columns:
        raise RuntimeError(f"source date column is missing from {panel_table}: {source_available_date_column}")

    _progress(f"resolved inputs features={len(usable_features)} labels={len(labels)}")
    stage_timings: dict[str, float] = {}
    for table in (
        "mart_temporal_research_panel_quality",
        "mart_feature_temporal_relevance",
        "mart_feature_bucket_effect",
        "mart_feature_relevance_stability",
        "mart_feature_pair_synergy",
        "mart_feature_interaction_candidate",
        "mart_feature_conditional_synergy",
    ):
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))

    base_filters, params = _build_base_filters(
        columns=columns,
        feature_set_id=feature_set_id,
        start_date=start_date,
        end_date=end_date,
    )
    _progress("temporal_panel start")
    stage_started = time.perf_counter()
    quality = _create_temporal_panel(
        conn,
        run_id=run_id,
        panel_table=panel_table,
        feature_set_id=feature_set_id,
        features=usable_features,
        labels=labels,
        base_filters=base_filters,
        params=params,
        source_available_date_column=source_available_date_column,
        materialize_panel=materialize_panel,
        built_at=built_at,
    )
    _progress(
        f"temporal_panel done rows={quality.get('panel_rows')} "
        f"dropped_future_source_rows={quality.get('dropped_future_source_rows')}"
    )
    stage_timings["temporal_panel_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(f"relevance start features={len(usable_features)} labels={len(labels)} folds={folds}")
    stage_started = time.perf_counter()
    relevance = _insert_relevance_rows(
        conn,
        run_id=run_id,
        labels=labels,
        features=usable_features,
        min_daily_count=min_daily_count,
        bucket_count=bucket_count,
        folds=folds,
        built_at=built_at,
    )
    _progress(f"relevance done rows={relevance.get('relevance_rows')}")
    stage_timings["relevance_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(
        f"pair_synergy start labels={len(labels)} top_pair_features={top_pair_features} max_pairs={max_pairs}"
    )
    stage_started = time.perf_counter()
    pair_count_items = []
    for label in labels:
        _progress(f"pair_synergy label_start label={label}")
        item = _write_pair_synergy(
            conn,
            run_id=run_id,
            label=label,
            features=usable_features,
            stats=relevance["stats"],
            top_pair_features=top_pair_features,
            max_pairs=max_pairs,
            min_pair_valid_rows=min_pair_valid_rows,
            min_joint_obs=min_joint_obs,
            active_quantile=active_quantile,
            interaction_uplift_threshold=interaction_uplift_threshold,
            built_at=built_at,
        )
        pair_count_items.append(item)
        _progress(
            f"pair_synergy label_done label={label} rows={item.get('pair_rows')} "
            f"selected={item.get('selected_interaction_rows')}"
        )
    pair_counts = _sum_count_dicts(pair_count_items)
    _progress(
        f"pair_synergy done rows={pair_counts.get('pair_rows')} "
        f"selected={pair_counts.get('selected_interaction_rows')}"
    )
    stage_timings["pair_synergy_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(
        f"conditional_synergy start top_pair_features={top_pair_features} "
        f"max_pairs={max_conditional_pairs}"
    )
    stage_started = time.perf_counter()
    conditional_count_items = []
    for label in labels:
        if max_conditional_pairs <= 0:
            break
        _progress(f"conditional_synergy label_start label={label}")
        item = _write_conditional_synergy(
            conn,
            run_id=run_id,
            label=label,
            features=usable_features,
            stats=relevance["stats"],
            top_pair_features=top_pair_features,
            max_conditional_pairs=max_conditional_pairs,
            min_pair_valid_rows=min_pair_valid_rows,
            min_joint_obs=min_joint_obs,
            active_quantile=active_quantile,
            conditional_uplift_threshold=conditional_uplift_threshold,
            built_at=built_at,
        )
        conditional_count_items.append(item)
        _progress(
            f"conditional_synergy label_done label={label} "
            f"rows={item.get('conditional_synergy_rows')} "
            f"selected={item.get('selected_conditional_rows')}"
        )
    conditional_counts = _sum_count_dicts(conditional_count_items)
    conditional_counts.setdefault("conditional_synergy_rows", 0)
    conditional_counts.setdefault("selected_conditional_rows", 0)
    _progress(
        f"conditional_synergy done rows={conditional_counts.get('conditional_synergy_rows')} "
        f"selected={conditional_counts.get('selected_conditional_rows')}"
    )
    stage_timings["conditional_synergy_s"] = round(time.perf_counter() - stage_started, 3)
    for table in (
        "mart_temporal_research_panel_quality",
        "mart_feature_temporal_relevance",
        "mart_feature_bucket_effect",
        "mart_feature_relevance_stability",
        "mart_feature_pair_synergy",
        "mart_feature_interaction_candidate",
        "mart_feature_conditional_synergy",
    ):
        record_actual_version(conn, table)
    if materialize_panel:
        record_actual_version(conn, "mart_temporal_research_panel")
    duration_s = time.perf_counter() - t0
    stage_timings["total_s"] = round(duration_s, 3)
    output_tables = [
        "mart_temporal_research_panel_quality",
        "mart_feature_temporal_relevance",
        "mart_feature_bucket_effect",
        "mart_feature_relevance_stability",
        "mart_feature_pair_synergy",
        "mart_feature_interaction_candidate",
        "mart_feature_conditional_synergy",
    ]
    if materialize_panel:
        output_tables.insert(0, "mart_temporal_research_panel")
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_temporal_synergy_research",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[panel_table],
        output_tables=output_tables,
        label_name=",".join(labels),
        feature_group="temporal_synergy_research",
        perf_summary={
            "panel_table": panel_table,
            "feature_set_id": feature_set_id,
            "labels": labels,
            "features": usable_features,
            "feature_roles": feature_roles or [],
            "source_available_date_column": source_available_date_column,
            "min_daily_count": min_daily_count,
            "bucket_count": bucket_count,
            "folds": folds,
            "top_pair_features": top_pair_features,
            "max_pairs": max_pairs,
            "min_pair_valid_rows": min_pair_valid_rows,
            "min_joint_obs": min_joint_obs,
            "active_quantile": active_quantile,
            "interaction_uplift_threshold": interaction_uplift_threshold,
            "max_conditional_pairs": max_conditional_pairs,
            "conditional_uplift_threshold": conditional_uplift_threshold,
            "duration_s": duration_s,
            "stage_timings": stage_timings,
            **quality,
            **{key: value for key, value in relevance.items() if key != "stats"},
            **pair_counts,
            **conditional_counts,
        },
    )
    conn.commit()
    _progress(f"done run_id={run_id} duration_s={duration_s:.3f}")
    return {
        "run_id": run_id,
        "panel_table": panel_table,
        "feature_set_id": feature_set_id,
        "labels": labels,
        "feature_count": len(usable_features),
        **quality,
        **{key: value for key, value in relevance.items() if key != "stats"},
        **pair_counts,
        **conditional_counts,
        "duration_s": duration_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--panel-table", default="fact_feature_panel_candidate")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--features", default=None)
    parser.add_argument(
        "--feature-roles",
        default=None,
        help="comma-separated registry feature_role values to evaluate, e.g. core_model_input,capital_attention_auxiliary",
    )
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--source-available-date-column", default=None)
    parser.add_argument("--min-daily-count", type=int, default=10)
    parser.add_argument("--bucket-count", type=int, default=5)
    parser.add_argument(
        "--folds",
        type=int,
        default=0,
        help="Optional holdout stability folds. Keep 0 for fast smoke runs; use scheduled/offline runs for full fold scans.",
    )
    parser.add_argument("--top-pair-features", type=int, default=20)
    parser.add_argument("--max-pairs", type=int, default=200)
    parser.add_argument("--min-pair-valid-rows", type=int, default=50)
    parser.add_argument("--min-joint-obs", type=int, default=10)
    parser.add_argument("--active-quantile", type=float, default=0.8)
    parser.add_argument("--interaction-uplift-threshold", type=float, default=0.0)
    parser.add_argument("--max-conditional-pairs", type=int, default=0)
    parser.add_argument("--conditional-uplift-threshold", type=float, default=0.0)
    parser.add_argument("--skip-panel-materialization", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_temporal_synergy_research(
            conn,
            run_id=args.run_id,
            panel_table=args.panel_table,
            feature_set_id=args.feature_set_id,
            features=_parse_csv(args.features) or None,
            feature_roles=_parse_csv(args.feature_roles) or None,
            labels=_parse_csv(args.labels),
            start_date=args.start_date,
            end_date=args.end_date,
            source_available_date_column=args.source_available_date_column,
            min_daily_count=args.min_daily_count,
            bucket_count=args.bucket_count,
            folds=args.folds,
            top_pair_features=args.top_pair_features,
            max_pairs=args.max_pairs,
            min_pair_valid_rows=args.min_pair_valid_rows,
            min_joint_obs=args.min_joint_obs,
            active_quantile=args.active_quantile,
            interaction_uplift_threshold=args.interaction_uplift_threshold,
            max_conditional_pairs=args.max_conditional_pairs,
            conditional_uplift_threshold=args.conditional_uplift_threshold,
            materialize_panel=not args.skip_panel_materialization,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
