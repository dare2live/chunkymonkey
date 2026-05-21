#!/usr/bin/env python3
"""DuckDB rank-matrix proxy benchmark for feature association.

This does not replace ``build_feature_association_duck``. It materializes a
run-local rank matrix so large feature searches can be benchmarked against the
current exact pairwise association path before any default behavior changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_feature_association_duck import (  # noqa: E402
    _default_features,
    _finite_float,
    _is_numeric_type,
    _parse_csv,
    _prepare_base_table,
    _quote_ident,
    _quote_relation,
    _table_columns,
)
from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.pipeline_timing import PipelineTimer  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_rank_matrix_proxy_stat (
    run_id TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    total_rows BIGINT,
    valid_rank_rows BIGINT,
    daily_count INTEGER,
    rank_ic DOUBLE,
    top_decile_label_mean DOUBLE,
    bottom_decile_label_mean DOUBLE,
    long_short_spread DOUBLE,
    exact_rank_ic DOUBLE,
    abs_rank_ic_delta DOUBLE,
    built_at TEXT,
    PRIMARY KEY (run_id, label_name, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_rank_proxy_run
    ON mart_feature_rank_matrix_proxy_stat(run_id);

CREATE TABLE IF NOT EXISTS mart_feature_rank_matrix_benchmark (
    run_id TEXT PRIMARY KEY,
    panel_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    feature_count INTEGER,
    label_count INTEGER,
    total_rows BIGINT,
    rank_matrix_rows BIGINT,
    proxy_rows BIGINT,
    exact_run_id TEXT,
    exact_duration_s DOUBLE,
    matrix_duration_s DOUBLE,
    rank_matrix_build_s DOUBLE,
    proxy_association_s DOUBLE,
    compared_pairs INTEGER,
    max_abs_rank_ic_delta DOUBLE,
    avg_abs_rank_ic_delta DOUBLE,
    gate_status TEXT,
    gate_blockers_json TEXT,
    gate_config_json TEXT,
    config_json TEXT,
    stage_timings_json TEXT,
    built_at TEXT
);
ALTER TABLE mart_feature_rank_matrix_benchmark ADD COLUMN IF NOT EXISTS gate_status TEXT;
ALTER TABLE mart_feature_rank_matrix_benchmark ADD COLUMN IF NOT EXISTS gate_blockers_json TEXT;
ALTER TABLE mart_feature_rank_matrix_benchmark ADD COLUMN IF NOT EXISTS gate_config_json TEXT;

CREATE TABLE IF NOT EXISTS mart_feature_rank_matrix_cache_manifest (
    cache_key TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    panel_table TEXT NOT NULL,
    feature_set_id TEXT,
    features_json TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    panel_signature_json TEXT NOT NULL,
    row_count BIGINT,
    rank_column_count INTEGER,
    build_duration_s DOUBLE,
    created_at TEXT,
    last_used_at TEXT,
    hit_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rank_matrix_cache_last_used
    ON mart_feature_rank_matrix_cache_manifest(last_used_at DESC);
ALTER TABLE mart_feature_rank_matrix_cache_manifest ADD COLUMN IF NOT EXISTS hit_count INTEGER DEFAULT 0;
ALTER TABLE mart_feature_rank_matrix_cache_manifest ADD COLUMN IF NOT EXISTS build_duration_s DOUBLE;
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


def _progress(message: str) -> None:
    print(f"[rank_matrix] {utc_now_iso()} {message}", flush=True)


def _safe_alias(prefix: str, name: str, idx: int) -> str:
    stem = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_").lower() or "col"
    return f"{prefix}_{idx:03d}_{stem[:48]}"


def _drop_rank_matrix_relation(conn: Any) -> None:
    conn.execute("DROP VIEW IF EXISTS __feature_rank_matrix")
    conn.execute("DROP TABLE IF EXISTS __feature_rank_matrix")


def _cache_table_exists(conn: Any, table_name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {_quote_ident(table_name)} LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _cache_table_name(cache_key: str) -> str:
    return f"mart_feature_rank_matrix_cache_{cache_key[:20]}"


def _rank_matrix_panel_signature(
    conn: Any,
    *,
    panel_table: str,
    feature_set_id: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    table_cols = _table_columns(conn, panel_table)
    where = []
    params: list[str] = []
    if feature_set_id:
        where.append("feature_set_id = ?")
        params.append(feature_set_id)
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    max_built_expr = "MAX(CAST(built_at AS VARCHAR)) AS max_built_at" if "built_at" in table_cols else "NULL AS max_built_at"
    stock_expr = "COUNT(DISTINCT stock_code) AS stock_count" if "stock_code" in table_cols else "NULL AS stock_count"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               CAST(MIN(date) AS VARCHAR) AS min_date,
               CAST(MAX(date) AS VARCHAR) AS max_date,
               {stock_expr},
               {max_built_expr}
          FROM {_quote_relation(panel_table)}
        {where_sql}
        """,
        params,
    ).fetchone()
    return {
        "row_count": int(row["row_count"] or 0) if row else 0,
        "min_date": row["min_date"] if row else None,
        "max_date": row["max_date"] if row else None,
        "stock_count": int(row["stock_count"] or 0) if row and row["stock_count"] is not None else None,
        "max_built_at": row["max_built_at"] if row else None,
    }


def _rank_matrix_cache_key(
    *,
    panel_table: str,
    feature_set_id: str | None,
    features: list[str],
    labels: list[str],
    start_date: str | None,
    end_date: str | None,
    panel_signature: dict[str, Any],
) -> str:
    payload = {
        "version": 1,
        "rank_mode": "percent_rank_by_date_nulls_last",
        "panel_table": panel_table,
        "feature_set_id": feature_set_id,
        "features": features,
        "labels": labels,
        "start_date": start_date,
        "end_date": end_date,
        "panel_signature": panel_signature,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _rank_matrix_aliases(features: list[str], labels: list[str]) -> dict[str, str]:
    rank_columns = list(dict.fromkeys([*features, *labels]))
    return {
        column: _safe_alias("rank", column, idx)
        for idx, column in enumerate(rank_columns, start=1)
    }


def _build_rank_matrix(
    conn: Any,
    *,
    features: list[str],
    labels: list[str],
    target_table: str = "__feature_rank_matrix",
    temporary: bool = True,
) -> tuple[dict[str, str], int]:
    rank_columns = list(dict.fromkeys([*features, *labels]))
    rank_aliases = _rank_matrix_aliases(features, labels)
    select_exprs = ["b.stock_code", "b.date"]
    for label in labels:
        select_exprs.append(f"b.{_quote_ident(label)} AS {_quote_ident(label)}")
    for idx, column in enumerate(rank_columns, start=1):
        column_q = _quote_ident(column)
        rank_alias = _quote_ident(rank_aliases[column])
        valid_expr = f"{column_q} IS NOT NULL AND ISFINITE(CAST({column_q} AS DOUBLE))"
        order_expr = f"CASE WHEN {valid_expr} THEN CAST({column_q} AS DOUBLE) ELSE NULL END"
        select_exprs.append(
            f"""
            CASE WHEN {valid_expr}
                 THEN PERCENT_RANK() OVER (
                     PARTITION BY b.date
                     ORDER BY {order_expr} NULLS LAST
                 )
                 ELSE NULL END AS {rank_alias}
            """
        )
    table_q = _quote_ident(target_table)
    if temporary:
        _drop_rank_matrix_relation(conn)
        create_kind = "TEMP TABLE"
    else:
        conn.execute(f"DROP TABLE IF EXISTS {table_q}")
        create_kind = "TABLE"
    conn.execute(
        f"""
        CREATE {create_kind} {table_q} AS
        SELECT {", ".join(select_exprs)}
          FROM __feature_assoc_base b
        """
    )
    row = conn.execute(f"SELECT COUNT(*) FROM {table_q}").fetchone()
    return rank_aliases, int(row[0] or 0)


def _load_or_build_rank_matrix(
    conn: Any,
    *,
    features: list[str],
    labels: list[str],
    panel_table: str,
    feature_set_id: str | None,
    start_date: str | None,
    end_date: str | None,
    use_rank_cache: bool,
    rank_cache_max_entries: int,
) -> tuple[dict[str, str], int, dict[str, Any]]:
    if not use_rank_cache:
        rank_aliases, rank_matrix_rows = _build_rank_matrix(conn, features=features, labels=labels)
        return rank_aliases, rank_matrix_rows, {"status": "disabled", "cache_key": None, "table_name": None}

    panel_signature = _rank_matrix_panel_signature(
        conn,
        panel_table=panel_table,
        feature_set_id=feature_set_id,
        start_date=start_date,
        end_date=end_date,
    )
    cache_key = _rank_matrix_cache_key(
        panel_table=panel_table,
        feature_set_id=feature_set_id,
        features=features,
        labels=labels,
        start_date=start_date,
        end_date=end_date,
        panel_signature=panel_signature,
    )
    table_name = _cache_table_name(cache_key)
    manifest = conn.execute(
        """
        SELECT table_name, row_count, hit_count
          FROM mart_feature_rank_matrix_cache_manifest
         WHERE cache_key = ?
        """,
        [cache_key],
    ).fetchone()
    if manifest and _cache_table_exists(conn, str(manifest["table_name"])):
        _drop_rank_matrix_relation(conn)
        conn.execute(f"CREATE TEMP VIEW __feature_rank_matrix AS SELECT * FROM {_quote_ident(str(manifest['table_name']))}")
        conn.execute(
            """
            UPDATE mart_feature_rank_matrix_cache_manifest
               SET last_used_at = ?, hit_count = COALESCE(hit_count, 0) + 1
             WHERE cache_key = ?
            """,
            [utc_now_iso(), cache_key],
        )
        rank_aliases = _rank_matrix_aliases(features, labels)
        return rank_aliases, int(manifest["row_count"] or 0), {
            "status": "hit",
            "cache_key": cache_key,
            "table_name": manifest["table_name"],
            "panel_signature": panel_signature,
            "hit_count": int(manifest["hit_count"] or 0) + 1,
        }

    started = time.perf_counter()
    rank_aliases, rank_matrix_rows = _build_rank_matrix(
        conn,
        features=features,
        labels=labels,
        target_table=table_name,
        temporary=False,
    )
    build_duration_s = time.perf_counter() - started
    _drop_rank_matrix_relation(conn)
    conn.execute(f"CREATE TEMP VIEW __feature_rank_matrix AS SELECT * FROM {_quote_ident(table_name)}")
    now = utc_now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_feature_rank_matrix_cache_manifest
        (cache_key, table_name, panel_table, feature_set_id, features_json,
         labels_json, panel_signature_json, row_count, rank_column_count,
         build_duration_s, created_at, last_used_at, hit_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            cache_key,
            table_name,
            panel_table,
            feature_set_id,
            json.dumps(features, ensure_ascii=False),
            json.dumps(labels, ensure_ascii=False),
            json.dumps(panel_signature, ensure_ascii=False, sort_keys=True),
            rank_matrix_rows,
            len(rank_aliases),
            build_duration_s,
            now,
            now,
            0,
        ],
    )
    _prune_rank_matrix_cache(conn, max_entries=rank_cache_max_entries, keep_cache_key=cache_key)
    return rank_aliases, rank_matrix_rows, {
        "status": "miss",
        "cache_key": cache_key,
        "table_name": table_name,
        "panel_signature": panel_signature,
        "build_duration_s": build_duration_s,
    }


def _prune_rank_matrix_cache(conn: Any, *, max_entries: int, keep_cache_key: str) -> None:
    if max_entries <= 0:
        return
    rows = conn.execute(
        """
        WITH ranked_cache AS (
            SELECT cache_key,
                   table_name,
                   ROW_NUMBER() OVER (
                       ORDER BY COALESCE(last_used_at, created_at) DESC, cache_key DESC
                   ) AS cache_rank
              FROM mart_feature_rank_matrix_cache_manifest
        )
        SELECT cache_key, table_name
          FROM ranked_cache
         WHERE cache_rank > ?
           AND cache_key <> ?
        """,
        [max_entries, keep_cache_key],
    ).fetchall()
    if not rows:
        return

    expired_cache_keys = [str(row["cache_key"]) for row in rows]
    drop_sql = ";\n".join(
        f"DROP TABLE IF EXISTS {_quote_ident(str(row['table_name']))}"
        for row in rows
    )
    conn.execute(f"{drop_sql};")
    if expired_cache_keys:
        placeholders = ", ".join("?" for _ in expired_cache_keys)
        conn.execute(
            f"DELETE FROM mart_feature_rank_matrix_cache_manifest WHERE cache_key IN ({placeholders})",
            expired_cache_keys,
        )


def _compute_proxy_stat(
    conn: Any,
    *,
    feature_rank_column: str,
    label_rank_column: str,
    label: str,
    total_rows: int,
    min_daily_count: int,
) -> dict[str, Any]:
    feature_rank_q = _quote_ident(feature_rank_column)
    label_rank_q = _quote_ident(label_rank_column)
    label_q = _quote_ident(label)
    row = conn.execute(
        f"""
        WITH valid AS (
            SELECT date,
                   {feature_rank_q} AS feature_rank,
                   {label_rank_q} AS label_rank,
                   {label_q} AS label_value
              FROM __feature_rank_matrix
             WHERE {feature_rank_q} IS NOT NULL
               AND {label_rank_q} IS NOT NULL
        ),
        daily AS (
            SELECT date,
                   COUNT(*) AS n,
                   corr(feature_rank, label_rank) AS rank_ic
              FROM valid
             GROUP BY date
            HAVING COUNT(*) >= ?
        ),
        valid_daily AS (
            SELECT rank_ic
              FROM daily
             WHERE rank_ic IS NOT NULL
        )
        SELECT
            (SELECT COUNT(*) FROM valid) AS valid_rank_rows,
            AVG(rank_ic) AS rank_ic,
            COUNT(rank_ic) AS daily_count,
            (SELECT AVG(CASE WHEN feature_rank >= 0.9 THEN label_value END) FROM valid) AS top_mean,
            (SELECT AVG(CASE WHEN feature_rank <= 0.1 THEN label_value END) FROM valid) AS bottom_mean
          FROM valid_daily
        """,
        [min_daily_count],
    ).fetchone()
    top_mean = _finite_float(row["top_mean"] if row else None)
    bottom_mean = _finite_float(row["bottom_mean"] if row else None)
    return {
        "total_rows": total_rows,
        "valid_rank_rows": int(row["valid_rank_rows"] or 0) if row else 0,
        "daily_count": int(row["daily_count"] or 0) if row else 0,
        "rank_ic": _finite_float(row["rank_ic"] if row else None),
        "top_decile_label_mean": top_mean,
        "bottom_decile_label_mean": bottom_mean,
        "long_short_spread": top_mean - bottom_mean if top_mean is not None and bottom_mean is not None else None,
    }


def _compute_proxy_stats_for_feature(
    conn: Any,
    *,
    feature_rank_column: str,
    label_rank_columns: dict[str, str],
    labels: list[str],
    total_rows: int,
    min_daily_count: int,
) -> dict[str, dict[str, Any]]:
    feature_rank_q = _quote_ident(feature_rank_column)
    valid_select = [
        "date",
        f"{feature_rank_q} AS feature_rank",
    ]
    overall_exprs = []
    daily_exprs = []
    daily_summary_exprs = []
    for idx, label in enumerate(labels):
        label_rank_alias = f"label_rank_{idx}"
        label_value_alias = f"label_value_{idx}"
        label_rank_q = _quote_ident(label_rank_columns[label])
        label_q = _quote_ident(label)
        valid_select.append(f"{label_rank_q} AS {label_rank_alias}")
        valid_select.append(f"{label_q} AS {label_value_alias}")
        overall_exprs.extend(
            [
                f"COUNT({label_rank_alias}) AS valid_rank_rows_{idx}",
                (
                    f"AVG(CASE WHEN {label_rank_alias} IS NOT NULL "
                    f"AND feature_rank >= 0.9 THEN {label_value_alias} END) AS top_mean_{idx}"
                ),
                (
                    f"AVG(CASE WHEN {label_rank_alias} IS NOT NULL "
                    f"AND feature_rank <= 0.1 THEN {label_value_alias} END) AS bottom_mean_{idx}"
                ),
            ]
        )
        daily_exprs.extend(
            [
                f"COUNT({label_rank_alias}) AS n_{idx}",
                f"corr(feature_rank, {label_rank_alias}) AS rank_ic_{idx}",
            ]
        )
        daily_summary_exprs.extend(
            [
                (
                    f"AVG(CASE WHEN n_{idx} >= {int(min_daily_count)} "
                    f"THEN rank_ic_{idx} END) AS rank_ic_{idx}"
                ),
                (
                    f"COUNT(CASE WHEN n_{idx} >= {int(min_daily_count)} "
                    f"AND rank_ic_{idx} IS NOT NULL THEN 1 END) AS daily_count_{idx}"
                ),
            ]
        )
    row = conn.execute(
        f"""
        WITH valid AS (
            SELECT {", ".join(valid_select)}
              FROM __feature_rank_matrix
             WHERE {feature_rank_q} IS NOT NULL
        ),
        overall AS (
            SELECT {", ".join(overall_exprs)}
              FROM valid
        ),
        daily AS (
            SELECT date,
                   {", ".join(daily_exprs)}
              FROM valid
             GROUP BY date
        ),
        daily_summary AS (
            SELECT {", ".join(daily_summary_exprs)}
              FROM daily
        )
        SELECT *
          FROM overall
         CROSS JOIN daily_summary
        """
    ).fetchone()
    out: dict[str, dict[str, Any]] = {}
    for idx, label in enumerate(labels):
        top_mean = _finite_float(row[f"top_mean_{idx}"] if row else None)
        bottom_mean = _finite_float(row[f"bottom_mean_{idx}"] if row else None)
        out[label] = {
            "total_rows": total_rows,
            "valid_rank_rows": int(row[f"valid_rank_rows_{idx}"] or 0) if row else 0,
            "daily_count": int(row[f"daily_count_{idx}"] or 0) if row else 0,
            "rank_ic": _finite_float(row[f"rank_ic_{idx}"] if row else None),
            "top_decile_label_mean": top_mean,
            "bottom_decile_label_mean": bottom_mean,
            "long_short_spread": (
                top_mean - bottom_mean
                if top_mean is not None and bottom_mean is not None
                else None
            ),
        }
    return out


def _load_exact_rank_ic(conn: Any, exact_run_id: str | None) -> tuple[dict[tuple[str, str], float | None], float | None]:
    if not exact_run_id:
        return {}, None
    exact: dict[tuple[str, str], float | None] = {}
    rows = conn.execute(
        """
        SELECT label_name, feature_name, rank_ic, horizon_sensitivity_json
          FROM mart_feature_association_stat
         WHERE run_id = ?
        """,
        [exact_run_id],
    ).fetchall()
    for row in rows:
        feature = str(row["feature_name"])
        exact[(feature, str(row["label_name"]))] = _finite_float(row["rank_ic"])
        try:
            sensitivity = json.loads(row["horizon_sensitivity_json"] or "{}")
        except Exception:
            sensitivity = {}
        for label, value in sensitivity.items():
            exact[(feature, str(label))] = _finite_float(value)
    manifest = conn.execute(
        """
        SELECT duration_s
          FROM mart_pipeline_run_manifest
         WHERE run_id = ?
        """,
        [exact_run_id],
    ).fetchone()
    exact_duration_s = _finite_float(manifest["duration_s"] if manifest else None)
    return exact, exact_duration_s


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    value = abs(float(left) - float(right))
    return value if math.isfinite(value) else None


def _evaluate_proxy_gate(
    *,
    exact_run_id: str | None,
    compared_pairs: int,
    max_delta: float | None,
    avg_delta: float | None,
    min_compared_pairs: int,
    max_abs_rank_ic_delta: float,
    max_avg_abs_rank_ic_delta: float,
) -> tuple[str, list[str], dict[str, Any]]:
    config = {
        "min_compared_pairs": int(min_compared_pairs),
        "max_abs_rank_ic_delta": float(max_abs_rank_ic_delta),
        "max_avg_abs_rank_ic_delta": float(max_avg_abs_rank_ic_delta),
    }
    blockers: list[str] = []
    if not exact_run_id:
        blockers.append("exact_run_id_missing")
    if compared_pairs < min_compared_pairs:
        blockers.append(f"compared_pairs {compared_pairs} < {min_compared_pairs}")
    if max_delta is None:
        blockers.append("max_abs_rank_ic_delta_missing")
    elif max_delta > max_abs_rank_ic_delta:
        blockers.append(f"max_abs_rank_ic_delta {max_delta:.8f} > {max_abs_rank_ic_delta:.8f}")
    if avg_delta is None:
        blockers.append("avg_abs_rank_ic_delta_missing")
    elif avg_delta > max_avg_abs_rank_ic_delta:
        blockers.append(f"avg_abs_rank_ic_delta {avg_delta:.8f} > {max_avg_abs_rank_ic_delta:.8f}")
    return ("pass" if not blockers else "blocked", blockers, config)


def build_feature_rank_matrix_proxy(
    conn: Any,
    *,
    panel_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    label_name: str = "forward_ret_20d",
    horizon_labels: list[str] | None = None,
    run_id: str | None = None,
    exact_run_id: str | None = None,
    features: list[str] | None = None,
    feature_roles: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_daily_count: int = 10,
    limit_features: int | None = None,
    min_compared_pairs: int = 1,
    max_abs_rank_ic_delta: float = 0.001,
    max_avg_abs_rank_ic_delta: float = 0.0002,
    use_rank_cache: bool = True,
    rank_cache_max_entries: int = 4,
) -> dict[str, Any]:
    ensure_tables(conn)
    run_id = run_id or f"feature_rank_matrix_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = time.perf_counter()
    timer = PipelineTimer()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="microseconds")
    _progress(f"start run_id={run_id} panel={panel_table} label={label_name}")

    _progress("schema_and_feature_selection start")
    with timer.stage("schema_and_feature_selection_s"):
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
    _progress(f"schema_and_feature_selection done features={len(usable_features)} labels={len(labels)}")

    _progress("prepare_base_table start")
    with timer.stage("prepare_base_table_s"):
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

    _progress("rank_matrix_build start")
    with timer.stage("rank_matrix_build_s"):
        rank_aliases, rank_matrix_rows, rank_cache = _load_or_build_rank_matrix(
            conn,
            features=usable_features,
            labels=labels,
            panel_table=panel_table,
            feature_set_id=feature_set_id,
            start_date=start_date,
            end_date=end_date,
            use_rank_cache=use_rank_cache,
            rank_cache_max_entries=rank_cache_max_entries,
        )
    _progress(f"rank_matrix_build done rows={rank_matrix_rows} cache={rank_cache.get('status')}")

    exact_rank_ic, exact_duration_s = _load_exact_rank_ic(conn, exact_run_id)
    proxy_rows = []
    deltas: list[float] = []
    _progress("proxy_association start")
    with timer.stage("proxy_association_s"):
        for feature in usable_features:
            feature_stats = _compute_proxy_stats_for_feature(
                conn,
                feature_rank_column=rank_aliases[feature],
                label_rank_columns={label: rank_aliases[label] for label in labels},
                labels=labels,
                total_rows=total_rows,
                min_daily_count=min_daily_count,
            )
            for label in labels:
                stat = feature_stats[label]
                exact_value = exact_rank_ic.get((feature, label))
                delta = _delta(stat["rank_ic"], exact_value)
                if delta is not None:
                    deltas.append(delta)
                proxy_rows.append(
                    (
                        run_id,
                        panel_table,
                        label,
                        feature,
                        stat["total_rows"],
                        stat["valid_rank_rows"],
                        stat["daily_count"],
                        stat["rank_ic"],
                        stat["top_decile_label_mean"],
                        stat["bottom_decile_label_mean"],
                        stat["long_short_spread"],
                        exact_value,
                        delta,
                        built_at,
                    )
                )
    _progress(f"proxy_association done rows={len(proxy_rows)} compared_pairs={len(deltas)}")

    _progress("write_proxy_stats start")
    with timer.stage("write_proxy_stats_s"):
        conn.execute("DELETE FROM mart_feature_rank_matrix_proxy_stat WHERE run_id = ?", [run_id])
        if proxy_rows:
            conn.executemany(
                """
                INSERT INTO mart_feature_rank_matrix_proxy_stat
                (run_id, panel_table, label_name, feature_name, total_rows,
                 valid_rank_rows, daily_count, rank_ic, top_decile_label_mean,
                 bottom_decile_label_mean, long_short_spread, exact_rank_ic,
                 abs_rank_ic_delta, built_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                proxy_rows,
            )
    _progress("write_proxy_stats done")

    duration_s = time.perf_counter() - started
    timer.record("total_before_manifest_s", duration_s)
    stage_timings = dict(timer.stage_timings)
    rank_matrix_build_s = stage_timings.get("rank_matrix_build_s")
    proxy_association_s = stage_timings.get("proxy_association_s")
    compared_pairs = len(deltas)
    max_delta = max(deltas) if deltas else None
    avg_delta = sum(deltas) / len(deltas) if deltas else None
    gate_status, gate_blockers, gate_config = _evaluate_proxy_gate(
        exact_run_id=exact_run_id,
        compared_pairs=compared_pairs,
        max_delta=max_delta,
        avg_delta=avg_delta,
        min_compared_pairs=min_compared_pairs,
        max_abs_rank_ic_delta=max_abs_rank_ic_delta,
        max_avg_abs_rank_ic_delta=max_avg_abs_rank_ic_delta,
    )
    config = {
        "feature_set_id": feature_set_id,
        "feature_roles": feature_roles or [],
        "features": usable_features,
        "labels": labels,
        "start_date": start_date,
        "end_date": end_date,
        "min_daily_count": min_daily_count,
        "limit_features": limit_features,
        "rank_matrix_mode": "single_select_nulls_last_proxy",
        "proxy_association_mode": "per_feature_multi_label",
        "proxy_gate": gate_config,
        "rank_matrix_cache": {
            "enabled": bool(use_rank_cache),
            "status": rank_cache.get("status"),
            "cache_key": rank_cache.get("cache_key"),
            "table_name": rank_cache.get("table_name"),
            "max_entries": int(rank_cache_max_entries),
        },
    }

    _progress("write_benchmark_summary start")
    with timer.stage("write_benchmark_summary_s"):
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_feature_rank_matrix_benchmark
            (run_id, panel_table, label_name, feature_count, label_count,
             total_rows, rank_matrix_rows, proxy_rows, exact_run_id,
             exact_duration_s, matrix_duration_s, rank_matrix_build_s,
             proxy_association_s, compared_pairs, max_abs_rank_ic_delta,
             avg_abs_rank_ic_delta, gate_status, gate_blockers_json,
             gate_config_json, config_json, stage_timings_json, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                panel_table,
                label_name,
                len(usable_features),
                len(labels),
                total_rows,
                rank_matrix_rows,
                len(proxy_rows),
                exact_run_id,
                exact_duration_s,
                duration_s,
                rank_matrix_build_s,
                proxy_association_s,
                compared_pairs,
                max_delta,
                avg_delta,
                gate_status,
                json.dumps(gate_blockers, ensure_ascii=False, sort_keys=True),
                json.dumps(gate_config, ensure_ascii=False, sort_keys=True),
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                json.dumps(stage_timings, ensure_ascii=False, sort_keys=True),
                built_at,
            ],
        )
        record_actual_version(conn, "mart_feature_rank_matrix_proxy_stat")
        record_actual_version(conn, "mart_feature_rank_matrix_benchmark")
        record_actual_version(conn, "mart_feature_rank_matrix_cache_manifest")
    _progress("write_benchmark_summary done")

    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_feature_rank_matrix_duck",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[panel_table, "mart_feature_association_stat", "mart_pipeline_run_manifest"],
        output_tables=[
            "mart_feature_rank_matrix_proxy_stat",
            "mart_feature_rank_matrix_benchmark",
        ],
        label_name=label_name,
        gate_result=gate_status,
        blockers=gate_blockers,
        perf_summary=timer.summary(
            {
                "features": len(usable_features),
                "labels": labels,
                "rows": total_rows,
                "rank_matrix_rows": rank_matrix_rows,
                "proxy_rows": len(proxy_rows),
                "exact_run_id": exact_run_id,
                "exact_duration_s": exact_duration_s,
                "matrix_duration_s": duration_s,
                "compared_pairs": compared_pairs,
                "max_abs_rank_ic_delta": max_delta,
                "avg_abs_rank_ic_delta": avg_delta,
                "proxy_gate_status": gate_status,
                "proxy_gate_blockers": gate_blockers,
                "proxy_gate_config": gate_config,
                "proxy_association_mode": "per_feature_multi_label",
                "rank_matrix_cache": rank_cache,
            }
        ),
    )
    conn.commit()
    _progress(f"done run_id={run_id} duration_s={duration_s:.3f}")
    return {
        "run_id": run_id,
        "panel_table": panel_table,
        "label_name": label_name,
        "features": len(usable_features),
        "labels": labels,
        "total_rows": total_rows,
        "rank_matrix_rows": rank_matrix_rows,
        "proxy_rows": len(proxy_rows),
        "exact_run_id": exact_run_id,
        "exact_duration_s": exact_duration_s,
        "matrix_duration_s": duration_s,
        "compared_pairs": compared_pairs,
        "max_abs_rank_ic_delta": max_delta,
        "avg_abs_rank_ic_delta": avg_delta,
        "proxy_gate_status": gate_status,
        "proxy_gate_blockers": gate_blockers,
        "proxy_gate_config": gate_config,
        "rank_matrix_cache": rank_cache,
        "stage_timings": timer.stage_timings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--label", default="forward_ret_20d")
    parser.add_argument("--horizon-labels", default="forward_ret_5d,forward_ret_10d,forward_ret_20d,forward_ret_60d,forward_ret_90d")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--exact-run-id", default=None)
    parser.add_argument("--features", default=None)
    parser.add_argument("--feature-roles", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-daily-count", type=int, default=10)
    parser.add_argument("--limit-features", type=int, default=None)
    parser.add_argument("--min-compared-pairs", type=int, default=1)
    parser.add_argument("--max-abs-rank-ic-delta", type=float, default=0.001)
    parser.add_argument("--max-avg-abs-rank-ic-delta", type=float, default=0.0002)
    parser.add_argument("--no-rank-cache", action="store_true")
    parser.add_argument("--rank-cache-max-entries", type=int, default=4)
    args = parser.parse_args()

    features = _parse_csv(args.features) or None
    feature_roles = _parse_csv(args.feature_roles) or None
    horizon_labels = _parse_csv(args.horizon_labels)
    with get_conn() as conn:
        result = build_feature_rank_matrix_proxy(
            conn,
            panel_table=args.panel_table,
            feature_set_id=args.feature_set_id,
            label_name=args.label,
            horizon_labels=horizon_labels,
            run_id=args.run_id,
            exact_run_id=args.exact_run_id,
            features=features,
            feature_roles=feature_roles,
            start_date=args.start_date,
            end_date=args.end_date,
            min_daily_count=args.min_daily_count,
            limit_features=args.limit_features,
            min_compared_pairs=args.min_compared_pairs,
            max_abs_rank_ic_delta=args.max_abs_rank_ic_delta,
            max_avg_abs_rank_ic_delta=args.max_avg_abs_rank_ic_delta,
            use_rank_cache=not args.no_rank_cache,
            rank_cache_max_entries=args.rank_cache_max_entries,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
