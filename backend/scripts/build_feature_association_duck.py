#!/usr/bin/env python3
"""Registry-driven feature association statistics for production panels."""
from __future__ import annotations

import argparse
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
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


STAT_DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_association_stat (
    run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    total_rows BIGINT,
    valid_rows BIGINT,
    coverage_pct DOUBLE,
    missing_pct DOUBLE,
    ic DOUBLE,
    rank_ic DOUBLE,
    rank_ic_std_by_date DOUBLE,
    fold_count INTEGER,
    fold_same_sign_rate DOUBLE,
    top_decile_label_mean DOUBLE,
    bottom_decile_label_mean DOUBLE,
    long_short_spread DOUBLE,
    horizon_sensitivity_json TEXT,
    source_fallback_pct DOUBLE,
    source_distribution_json TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, label_name, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_assoc_run
    ON mart_feature_association_stat(run_id);
CREATE INDEX IF NOT EXISTS idx_feature_assoc_rank
    ON mart_feature_association_stat(label_name, rank_ic);

CREATE TABLE IF NOT EXISTS mart_feature_correlation_cluster (
    run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    representative_feature TEXT NOT NULL,
    corr_to_representative DOUBLE,
    built_at TEXT,
    PRIMARY KEY (run_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_cluster_run_cluster
    ON mart_feature_correlation_cluster(run_id, cluster_id);

CREATE TABLE IF NOT EXISTS mart_feature_association_fold (
    run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    fold_id TEXT NOT NULL,
    train_start TEXT,
    train_end TEXT,
    holdout_start TEXT NOT NULL,
    holdout_end TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    total_rows BIGINT,
    valid_rows BIGINT,
    coverage_pct DOUBLE,
    missing_pct DOUBLE,
    ic DOUBLE,
    rank_ic DOUBLE,
    rank_ic_std_by_date DOUBLE,
    daily_count INTEGER,
    fold_same_sign_rate DOUBLE,
    top_decile_label_mean DOUBLE,
    bottom_decile_label_mean DOUBLE,
    long_short_spread DOUBLE,
    built_at TEXT,
    PRIMARY KEY (run_id, label_name, fold_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_assoc_fold_run
    ON mart_feature_association_fold(run_id, fold_id);
ALTER TABLE mart_feature_association_stat ADD COLUMN IF NOT EXISTS source_distribution_json TEXT;
"""


NUMERIC_TYPES = ("DOUBLE", "REAL", "FLOAT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "DECIMAL")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _progress(message: str) -> None:
    print(f"[feature_assoc] {utc_now_iso()} {message}", flush=True)


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, STAT_DDL)


def _table_columns(conn: Any, table: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]).upper()
        for row in conn.execute(f"DESCRIBE {_quote_relation(table)}").fetchall()
    }


def _is_numeric_type(type_name: str) -> bool:
    upper = type_name.upper()
    return any(token in upper for token in NUMERIC_TYPES)


def _registry_group_map() -> dict[str, str]:
    registry = load_feature_registry()
    return {name: spec.group for name, spec in registry.features.items()}


def _registry_role_features(
    *,
    columns: dict[str, str],
    label_names: list[str],
    feature_roles: list[str],
) -> list[str]:
    labels = set(label_names)
    roles = set(feature_roles)
    registry = load_feature_registry()
    return [
        name
        for name, spec in registry.features.items()
        if spec.enabled
        and not spec.label
        and spec.feature_role in roles
        and name in columns
        and name not in labels
        and _is_numeric_type(columns[name])
    ]


def _default_features(
    conn: Any,
    panel_table: str,
    label_names: list[str],
    *,
    feature_roles: list[str] | None = None,
) -> list[str]:
    columns = _table_columns(conn, panel_table)
    labels = set(label_names)
    if feature_roles:
        role_features = _registry_role_features(
            columns=columns,
            label_names=label_names,
            feature_roles=feature_roles,
        )
        if role_features:
            return role_features
    registry_features = [
        feature
        for feature in feature_input_columns()
        if feature in columns
        and feature not in labels
        and _is_numeric_type(columns[feature])
    ]
    if registry_features:
        return registry_features
    excluded = {
        "stock_code",
        "date",
        "feature_set_id",
        "built_at",
        "is_fallback",
        "source_tier",
        "source_name",
        "kline_is_fallback",
        "kline_source_tier",
        "kline_source_name",
        *labels,
    }
    return [
        feature
        for feature, dtype in columns.items()
        if feature not in excluded and _is_numeric_type(dtype)
    ]


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _prepare_base_table(
    conn: Any,
    *,
    panel_table: str,
    feature_set_id: str | None,
    features: list[str],
    labels: list[str],
    start_date: str | None,
    end_date: str | None,
) -> int:
    table_cols = _table_columns(conn, panel_table)
    columns = ["date", *features, *labels]
    if "stock_code" in table_cols:
        columns.insert(0, "stock_code")
    select_exprs = [_quote_ident(col) for col in dict.fromkeys(columns)]
    if "kline_is_fallback" in table_cols:
        select_exprs.append(f"{_quote_ident('kline_is_fallback')} AS is_fallback")
    elif "is_fallback" in table_cols:
        select_exprs.append(_quote_ident("is_fallback"))
    if "kline_source_tier" in table_cols:
        select_exprs.append(f"{_quote_ident('kline_source_tier')} AS source_tier")
    elif "source_tier" in table_cols:
        select_exprs.append(_quote_ident("source_tier"))
    if "kline_source_name" in table_cols:
        select_exprs.append(f"{_quote_ident('kline_source_name')} AS source_name")
    elif "source_name" in table_cols:
        select_exprs.append(_quote_ident("source_name"))
    select_cols = ", ".join(select_exprs)
    where = []
    params: list[str] = []
    if feature_set_id:
        if "feature_set_id" not in table_cols:
            raise RuntimeError(f"{panel_table} has no feature_set_id column")
        where.append("feature_set_id = ?")
        params.append(feature_set_id)
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    conn.execute("DROP TABLE IF EXISTS __feature_assoc_base")
    conn.execute(
        f"""
        CREATE TEMP TABLE __feature_assoc_base AS
        SELECT {select_cols}
          FROM {_quote_relation(panel_table)}
        {where_sql}
        """,
        params,
    )
    row = conn.execute("SELECT COUNT(*) FROM __feature_assoc_base").fetchone()
    return int(row[0] or 0)


def _compute_feature_label_stats(
    conn: Any,
    *,
    feature: str,
    label: str,
    total_rows: int,
    min_daily_count: int,
    include_deciles: bool,
    base_table: str = "__feature_assoc_base",
) -> dict[str, Any]:
    feature_q = _quote_ident(feature)
    label_q = _quote_ident(label)
    valid_where = (
        f"{feature_q} IS NOT NULL AND {label_q} IS NOT NULL "
        f"AND ISFINITE(CAST({feature_q} AS DOUBLE)) "
        f"AND ISFINITE(CAST({label_q} AS DOUBLE))"
    )
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
    missing_pct = 100.0 - coverage_pct

    ic_row = conn.execute(
        f"""
        SELECT corr(CAST({feature_q} AS DOUBLE), CAST({label_q} AS DOUBLE)) AS ic
          FROM {base_table}
         WHERE {valid_where}
        """
    ).fetchone()
    ic = _finite_float(ic_row["ic"] if ic_row else None)

    rank_row = conn.execute(
        f"""
        WITH valid AS (
            SELECT date,
                   CAST({feature_q} AS DOUBLE) AS feature_value,
                   CAST({label_q} AS DOUBLE) AS label_value
              FROM {base_table}
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
               COUNT(rank_ic) AS fold_count,
               AVG(CASE WHEN rank_ic > 0 THEN 1.0 ELSE 0.0 END) AS same_sign_rate
          FROM valid_daily
        """,
        [min_daily_count],
    ).fetchone()
    rank_ic = _finite_float(rank_row["rank_ic"] if rank_row else None)
    rank_ic_std = _finite_float(rank_row["rank_ic_std"] if rank_row else None)
    fold_count = int(rank_row["fold_count"] or 0) if rank_row else 0
    same_sign_rate = _finite_float(rank_row["same_sign_rate"] if rank_row else None)

    top_mean = bottom_mean = spread = None
    if include_deciles:
        decile_row = conn.execute(
            f"""
            WITH valid AS (
                SELECT date,
                       CAST({feature_q} AS DOUBLE) AS feature_value,
                       CAST({label_q} AS DOUBLE) AS label_value
                  FROM {base_table}
                 WHERE {valid_where}
            ),
            ranked AS (
                SELECT date, label_value,
                       PERCENT_RANK() OVER (PARTITION BY date ORDER BY feature_value) AS feature_rank
                  FROM valid
            )
            SELECT AVG(CASE WHEN feature_rank >= 0.9 THEN label_value END) AS top_mean,
                   AVG(CASE WHEN feature_rank <= 0.1 THEN label_value END) AS bottom_mean
              FROM ranked
            """
        ).fetchone()
        top_mean = _finite_float(decile_row["top_mean"] if decile_row else None)
        bottom_mean = _finite_float(decile_row["bottom_mean"] if decile_row else None)
        if top_mean is not None and bottom_mean is not None:
            spread = top_mean - bottom_mean

    return {
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "coverage_pct": coverage_pct,
        "missing_pct": missing_pct,
        "ic": ic,
        "rank_ic": rank_ic,
        "rank_ic_std_by_date": rank_ic_std,
        "fold_count": fold_count,
        "fold_same_sign_rate": same_sign_rate,
        "top_decile_label_mean": top_mean,
        "bottom_decile_label_mean": bottom_mean,
        "long_short_spread": spread,
    }


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _source_fallback_pct(conn: Any) -> float | None:
    cols = _table_columns(conn, "__feature_assoc_base")
    if "is_fallback" not in cols:
        return None
    row = conn.execute(
        """
        SELECT AVG(CASE WHEN is_fallback THEN 1.0 ELSE 0.0 END) AS fallback_pct
          FROM __feature_assoc_base
        """
    ).fetchone()
    value = _finite_float(row["fallback_pct"] if row else None)
    return None if value is None else value * 100.0


def _source_distribution_json(conn: Any) -> str | None:
    cols = _table_columns(conn, "__feature_assoc_base")
    if not {"source_tier", "is_fallback"}.issubset(cols):
        return None
    total = int(conn.execute("SELECT COUNT(*) FROM __feature_assoc_base").fetchone()[0] or 0)
    if total <= 0:
        return "[]"
    name_expr = "source_name" if "source_name" in cols else "'unknown'"
    rows = conn.execute(
        f"""
        SELECT
            COALESCE({name_expr}, 'unknown') AS source_name,
            source_tier,
            COALESCE(is_fallback, FALSE) AS is_fallback,
            COUNT(*) AS row_count,
            COUNT(DISTINCT date) AS date_count
          FROM __feature_assoc_base
         GROUP BY 1, 2, 3
         ORDER BY row_count DESC, source_tier NULLS LAST, source_name
        """
    ).fetchall()
    distribution = []
    for row in rows:
        row_count = int(row["row_count"] or 0)
        distribution.append(
            {
                "source_name": row["source_name"],
                "source_tier": int(row["source_tier"]) if row["source_tier"] is not None else None,
                "is_fallback": bool(row["is_fallback"]),
                "rows": row_count,
                "row_pct": row_count / total,
                "dates": int(row["date_count"] or 0),
            }
        )
    return json.dumps(distribution, ensure_ascii=False, sort_keys=True)


def _fold_ranges(conn: Any, *, label: str, folds: int) -> list[dict[str, str | None]]:
    folds = max(int(folds), 0)
    if folds <= 0:
        return []
    label_q = _quote_ident(label)
    dates = [
        str(row[0])
        for row in conn.execute(
            f"""
            SELECT DISTINCT date
              FROM __feature_assoc_base
             WHERE {label_q} IS NOT NULL
             ORDER BY date
            """
        ).fetchall()
    ]
    if not dates:
        return []
    folds = min(folds, len(dates))
    ranges = []
    for idx in range(folds):
        start = idx * len(dates) // folds
        end = (idx + 1) * len(dates) // folds
        holdout = dates[start:end]
        if not holdout:
            continue
        train = dates[:start]
        ranges.append(
            {
                "fold_id": f"fold_{idx + 1:03d}",
                "train_start": train[0] if train else None,
                "train_end": train[-1] if train else None,
                "holdout_start": holdout[0],
                "holdout_end": holdout[-1],
            }
        )
    return ranges


def _build_fold_associations(
    conn: Any,
    *,
    run_id: str,
    panel_table: str,
    label: str,
    features: list[str],
    group_map: dict[str, str],
    folds: int,
    min_daily_count: int,
    built_at: str,
) -> int:
    ranges = _fold_ranges(conn, label=label, folds=folds)
    rows = []
    for fold in ranges:
        conn.execute("DROP TABLE IF EXISTS __feature_assoc_fold_base")
        conn.execute(
            """
            CREATE TEMP TABLE __feature_assoc_fold_base AS
            SELECT *
              FROM __feature_assoc_base
             WHERE date >= ? AND date <= ?
            """,
            [fold["holdout_start"], fold["holdout_end"]],
        )
        total_rows = int(conn.execute("SELECT COUNT(*) FROM __feature_assoc_fold_base").fetchone()[0] or 0)
        for feature in features:
            stats = _compute_feature_label_stats(
                conn,
                feature=feature,
                label=label,
                total_rows=total_rows,
                min_daily_count=min_daily_count,
                include_deciles=True,
                base_table="__feature_assoc_fold_base",
            )
            rows.append(
                (
                    run_id,
                    panel_table,
                    label,
                    fold["fold_id"],
                    fold["train_start"],
                    fold["train_end"],
                    fold["holdout_start"],
                    fold["holdout_end"],
                    feature,
                    group_map.get(feature, "unregistered"),
                    stats["total_rows"],
                    stats["valid_rows"],
                    stats["coverage_pct"],
                    stats["missing_pct"],
                    stats["ic"],
                    stats["rank_ic"],
                    stats["rank_ic_std_by_date"],
                    stats["fold_count"],
                    stats["fold_same_sign_rate"],
                    stats["top_decile_label_mean"],
                    stats["bottom_decile_label_mean"],
                    stats["long_short_spread"],
                    built_at,
                )
            )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_association_fold
            (run_id, panel_table, label_name, fold_id, train_start, train_end,
             holdout_start, holdout_end, feature_name, feature_group,
             total_rows, valid_rows, coverage_pct, missing_pct, ic, rank_ic,
             rank_ic_std_by_date, daily_count, fold_same_sign_rate,
             top_decile_label_mean, bottom_decile_label_mean,
             long_short_spread, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    conn.execute("DROP TABLE IF EXISTS __feature_assoc_fold_base")
    return len(rows)


def _feature_value_expr(feature: str) -> str:
    q = _quote_ident(feature)
    return f"CASE WHEN {q} IS NOT NULL AND ISFINITE(CAST({q} AS DOUBLE)) THEN CAST({q} AS DOUBLE) END"


def _feature_corr_matrix(
    conn: Any,
    features: list[str],
    *,
    batch_size: int = 400,
) -> dict[tuple[str, str], float | None]:
    """Compute pairwise feature correlations in DuckDB expression batches."""

    pairs = [
        (left, right)
        for left_idx, left in enumerate(features)
        for right in features[left_idx + 1 :]
    ]
    matrix: dict[tuple[str, str], float | None] = {}
    batch_size = max(int(batch_size), 1)
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start : start + batch_size]
        select_exprs = []
        for idx, (left, right) in enumerate(batch):
            select_exprs.append(
                f"corr({_feature_value_expr(left)}, {_feature_value_expr(right)}) AS {_quote_ident(f'c_{idx}')}"
            )
        row = conn.execute(f"SELECT {', '.join(select_exprs)} FROM __feature_assoc_base").fetchone()
        for idx, pair in enumerate(batch):
            matrix[pair] = _finite_float(row[f"c_{idx}"] if row else None)
    return matrix


def _build_correlation_clusters(
    conn: Any,
    *,
    run_id: str,
    panel_table: str,
    stats: list[dict[str, Any]],
    corr_threshold: float,
    built_at: str,
) -> int:
    ordered = sorted(
        stats,
        key=lambda item: (
            -abs(float(item["rank_ic"] or 0.0)),
            -float(item["coverage_pct"] or 0.0),
            str(item["feature_name"]),
        ),
    )
    unassigned = {str(item["feature_name"]) for item in ordered}
    ordered_features = [str(item["feature_name"]) for item in ordered]
    corr_matrix = _feature_corr_matrix(conn, ordered_features)
    rows = []
    cluster_idx = 0
    for item in ordered:
        representative = str(item["feature_name"])
        if representative not in unassigned:
            continue
        cluster_idx += 1
        cluster_id = f"cluster_{cluster_idx:03d}"
        unassigned.remove(representative)
        rows.append((run_id, panel_table, cluster_id, representative, representative, 1.0, built_at))
        for feature in list(unassigned):
            corr = corr_matrix.get((representative, feature))
            if corr is None:
                corr = corr_matrix.get((feature, representative))
            if corr is not None and abs(corr) >= corr_threshold:
                unassigned.remove(feature)
                rows.append((run_id, panel_table, cluster_id, feature, representative, corr, built_at))
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_feature_correlation_cluster
            (run_id, panel_table, cluster_id, feature_name, representative_feature,
             corr_to_representative, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def build_feature_association_stats(
    conn: Any,
    *,
    panel_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    label_name: str = "forward_ret_20d",
    horizon_labels: list[str] | None = None,
    run_id: str | None = None,
    features: list[str] | None = None,
    feature_roles: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_daily_count: int = 10,
    corr_threshold: float = 0.95,
    limit_features: int | None = None,
    build_clusters: bool = True,
    folds: int = 0,
) -> dict[str, Any]:
    ensure_tables(conn)
    run_id = run_id or f"feature_assoc_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    _progress(
        f"start run_id={run_id} panel={panel_table} label={label_name} "
        f"feature_roles={','.join(feature_roles or []) or 'model_inputs'}"
    )
    columns = _table_columns(conn, panel_table)
    labels = [label_name, *(horizon_labels or [])]
    labels = [label for label in dict.fromkeys(labels) if label in columns]
    if label_name not in labels:
        raise RuntimeError(f"label {label_name} is missing from {panel_table}")

    requested = list(
        features
        or _default_features(
            conn,
            panel_table,
            labels,
            feature_roles=feature_roles,
        )
    )
    usable_features = [
        feature
        for feature in dict.fromkeys(requested)
        if feature in columns
        and feature not in set(labels)
        and _is_numeric_type(columns[feature])
    ]
    if limit_features is not None:
        usable_features = usable_features[: max(int(limit_features), 0)]
    if not usable_features:
        raise RuntimeError(f"no numeric model-input features available in {panel_table}")

    _progress(f"prepare_base_table start features={len(usable_features)} labels={len(labels)}")
    total_rows = _prepare_base_table(
        conn,
        panel_table=panel_table,
        feature_set_id=feature_set_id,
        features=usable_features,
        labels=labels,
        start_date=start_date,
        end_date=end_date,
    )
    _progress(f"prepare_base_table done rows={total_rows}")
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    group_map = _registry_group_map()
    source_fallback_pct = _source_fallback_pct(conn)
    source_distribution_json = _source_distribution_json(conn)
    rows = []
    stats: list[dict[str, Any]] = []
    for idx, feature in enumerate(usable_features, start=1):
        feature_t0 = time.perf_counter()
        if idx == 1 or idx == len(usable_features) or idx % 5 == 0:
            _progress(f"feature_stats start {idx}/{len(usable_features)} feature={feature}")
        primary = _compute_feature_label_stats(
            conn,
            feature=feature,
            label=label_name,
            total_rows=total_rows,
            min_daily_count=min_daily_count,
            include_deciles=True,
        )
        sensitivity = {}
        for label in labels:
            label_stats = (
                primary
                if label == label_name
                else _compute_feature_label_stats(
                    conn,
                    feature=feature,
                    label=label,
                    total_rows=total_rows,
                    min_daily_count=min_daily_count,
                    include_deciles=False,
                )
            )
            sensitivity[label] = label_stats["rank_ic"]
        row = {
            "feature_name": feature,
            "feature_group": group_map.get(feature, "unregistered"),
            **primary,
            "horizon_sensitivity_json": json.dumps(sensitivity, ensure_ascii=False, sort_keys=True),
        }
        stats.append(row)
        if idx == 1 or idx == len(usable_features) or idx % 5 == 0:
            _progress(
                f"feature_stats done {idx}/{len(usable_features)} feature={feature} "
                f"valid_rows={primary.get('valid_rows')} rank_ic={primary.get('rank_ic')} "
                f"elapsed={time.perf_counter() - feature_t0:.3f}s"
            )
        rows.append(
            (
                run_id,
                panel_table,
                label_name,
                feature,
                row["feature_group"],
                primary["total_rows"],
                primary["valid_rows"],
                primary["coverage_pct"],
                primary["missing_pct"],
                primary["ic"],
                primary["rank_ic"],
                primary["rank_ic_std_by_date"],
                primary["fold_count"],
                primary["fold_same_sign_rate"],
                primary["top_decile_label_mean"],
                primary["bottom_decile_label_mean"],
                primary["long_short_spread"],
                row["horizon_sensitivity_json"],
                source_fallback_pct,
                source_distribution_json,
                built_at,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_association_stat
        (run_id, panel_table, label_name, feature_name, feature_group, total_rows,
         valid_rows, coverage_pct, missing_pct, ic, rank_ic, rank_ic_std_by_date,
         fold_count, fold_same_sign_rate, top_decile_label_mean,
         bottom_decile_label_mean, long_short_spread, horizon_sensitivity_json,
         source_fallback_pct, source_distribution_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    cluster_rows = 0
    corr_pairs = len(stats) * max(len(stats) - 1, 0) // 2 if build_clusters else 0
    if build_clusters:
        _progress(f"correlation_clusters start features={len(stats)} threshold={corr_threshold}")
        cluster_rows = _build_correlation_clusters(
            conn,
            run_id=run_id,
            panel_table=panel_table,
            stats=stats,
            corr_threshold=corr_threshold,
            built_at=built_at,
        )
        _progress(f"correlation_clusters done rows={cluster_rows}")
    else:
        _progress("correlation_clusters skipped")
    _progress(f"fold_associations start folds={folds}")
    fold_rows = _build_fold_associations(
        conn,
        run_id=run_id,
        panel_table=panel_table,
        label=label_name,
        features=usable_features,
        group_map=group_map,
        folds=folds,
        min_daily_count=min_daily_count,
        built_at=built_at,
    )
    _progress(f"fold_associations done rows={fold_rows}")
    duration_s = time.perf_counter() - t0
    record_actual_version(conn, "mart_feature_association_stat")
    record_actual_version(conn, "mart_feature_correlation_cluster")
    record_actual_version(conn, "mart_feature_association_fold")
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_feature_association_duck",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[panel_table],
        output_tables=[
            "mart_feature_association_stat",
            "mart_feature_correlation_cluster",
            "mart_feature_association_fold",
        ],
        label_name=label_name,
        perf_summary={
            "features": len(usable_features),
            "labels": labels,
            "rows": len(rows),
            "cluster_rows": cluster_rows,
            "corr_pairs": corr_pairs,
            "folds": int(folds),
            "fold_rows": fold_rows,
            "corr_threshold": corr_threshold,
            "build_clusters": build_clusters,
            "start_date": start_date,
            "end_date": end_date,
            "feature_set_id": feature_set_id,
            "feature_roles": feature_roles or [],
            "limit_features": limit_features,
            "source_fallback_pct": source_fallback_pct,
            "source_distribution_json": source_distribution_json,
            "duration_s": duration_s,
        },
    )
    conn.commit()
    _progress(f"done run_id={run_id} rows={len(rows)} duration_s={duration_s:.3f}")
    return {
        "run_id": run_id,
        "panel_table": panel_table,
        "feature_set_id": feature_set_id,
        "label_name": label_name,
        "features": len(usable_features),
        "labels": labels,
        "rows": len(rows),
        "cluster_rows": cluster_rows,
        "corr_pairs": corr_pairs,
        "folds": int(folds),
        "fold_rows": fold_rows,
        "total_rows": total_rows,
        "source_fallback_pct": source_fallback_pct,
        "source_distribution_json": source_distribution_json,
        "duration_s": duration_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--label", default="forward_ret_20d")
    parser.add_argument("--horizon-labels", default="forward_ret_5d,forward_ret_10d,forward_ret_20d,forward_ret_60d,forward_ret_90d")
    parser.add_argument("--features", default=None, help="comma-separated feature list; defaults to registry inputs")
    parser.add_argument(
        "--feature-roles",
        default=None,
        help="comma-separated registry feature_role values to evaluate, e.g. core_model_input,capital_attention_auxiliary",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-daily-count", type=int, default=10)
    parser.add_argument("--corr-threshold", type=float, default=0.95)
    parser.add_argument("--limit-features", type=int, default=None)
    parser.add_argument("--skip-clusters", action="store_true")
    parser.add_argument("--folds", type=int, default=0, help="write holdout-window feature association rows")
    args = parser.parse_args()

    features = _parse_csv(args.features) or None
    feature_roles = _parse_csv(args.feature_roles) or None
    horizon_labels = _parse_csv(args.horizon_labels)
    with get_conn() as conn:
        result = build_feature_association_stats(
            conn,
            panel_table=args.panel_table,
            feature_set_id=args.feature_set_id,
            label_name=args.label,
            horizon_labels=horizon_labels,
            run_id=args.run_id,
            features=features,
            feature_roles=feature_roles,
            start_date=args.start_date,
            end_date=args.end_date,
            min_daily_count=args.min_daily_count,
            corr_threshold=args.corr_threshold,
            limit_features=args.limit_features,
            build_clusters=not args.skip_clusters,
            folds=args.folds,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
