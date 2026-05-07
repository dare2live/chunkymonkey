#!/usr/bin/env python3
"""DuckDB rank-matrix proxy benchmark for feature association.

This does not replace ``build_feature_association_duck``. It materializes a
run-local rank matrix so large feature searches can be benchmarked against the
current exact pairwise association path before any default behavior changes.
"""
from __future__ import annotations

import argparse
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
    config_json TEXT,
    stage_timings_json TEXT,
    built_at TEXT
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


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _progress(message: str) -> None:
    print(f"[rank_matrix] {utc_now_iso()} {message}", flush=True)


def _safe_alias(prefix: str, name: str, idx: int) -> str:
    stem = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_").lower() or "col"
    return f"{prefix}_{idx:03d}_{stem[:48]}"


def _build_rank_matrix(
    conn: Any,
    *,
    features: list[str],
    labels: list[str],
) -> tuple[dict[str, str], int]:
    rank_columns = list(dict.fromkeys([*features, *labels]))
    rank_aliases = {
        column: _safe_alias("rank", column, idx)
        for idx, column in enumerate(rank_columns, start=1)
    }
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
    conn.execute("DROP TABLE IF EXISTS __feature_rank_matrix")
    conn.execute(
        f"""
        CREATE TEMP TABLE __feature_rank_matrix AS
        SELECT {", ".join(select_exprs)}
          FROM __feature_assoc_base b
        """
    )
    row = conn.execute("SELECT COUNT(*) FROM __feature_rank_matrix").fetchone()
    return rank_aliases, int(row[0] or 0)


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
) -> dict[str, Any]:
    ensure_tables(conn)
    run_id = run_id or f"feature_rank_matrix_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = time.perf_counter()
    timer = PipelineTimer()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
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
        rank_aliases, rank_matrix_rows = _build_rank_matrix(
            conn,
            features=usable_features,
            labels=labels,
        )
    _progress(f"rank_matrix_build done rows={rank_matrix_rows}")

    exact_rank_ic, exact_duration_s = _load_exact_rank_ic(conn, exact_run_id)
    proxy_rows = []
    deltas: list[float] = []
    _progress("proxy_association start")
    with timer.stage("proxy_association_s"):
        for feature in usable_features:
            for label in labels:
                stat = _compute_proxy_stat(
                    conn,
                    feature_rank_column=rank_aliases[feature],
                    label_rank_column=rank_aliases[label],
                    label=label,
                    total_rows=total_rows,
                    min_daily_count=min_daily_count,
                )
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
             avg_abs_rank_ic_delta, config_json, stage_timings_json, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                json.dumps(stage_timings, ensure_ascii=False, sort_keys=True),
                built_at,
            ],
        )
        record_actual_version(conn, "mart_feature_rank_matrix_proxy_stat")
        record_actual_version(conn, "mart_feature_rank_matrix_benchmark")
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
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
