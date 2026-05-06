#!/usr/bin/env python3
"""Build per-stock holding-horizon and feature-effect profiles."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.model_feature_schema import holding_period_from_label  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402
from scripts.train_multidim_model import load_model_selection_run  # noqa: E402


DEFAULT_LABELS = [
    "forward_ret_5d",
    "forward_ret_10d",
    "forward_ret_20d",
    "forward_ret_60d",
    "forward_ret_90d",
]

DDL = """
CREATE TABLE IF NOT EXISTS mart_stock_horizon_profile (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    feature_set_id TEXT,
    stock_code TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    obs_count INTEGER,
    avg_return DOUBLE,
    median_return DOUBLE,
    win_rate DOUBLE,
    volatility DOUBLE,
    downside_avg DOUBLE,
    compounded_return DOUBLE,
    max_drawdown DOUBLE,
    horizon_score DOUBLE,
    rank_in_stock INTEGER,
    is_best BOOLEAN,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, stock_code, label_name)
);
ALTER TABLE mart_stock_horizon_profile ADD COLUMN IF NOT EXISTS compounded_return DOUBLE;
ALTER TABLE mart_stock_horizon_profile ADD COLUMN IF NOT EXISTS max_drawdown DOUBLE;
CREATE INDEX IF NOT EXISTS idx_stock_horizon_profile_best
    ON mart_stock_horizon_profile(run_id, is_best, horizon_score);

CREATE TABLE IF NOT EXISTS mart_stock_horizon_feature_effect (
    run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER,
    feature_name TEXT NOT NULL,
    obs_count INTEGER,
    corr DOUBLE,
    abs_corr_rank INTEGER,
    effect_direction TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, stock_code, label_name, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_stock_horizon_effect_stock
    ON mart_stock_horizon_feature_effect(run_id, stock_code, label_name, abs_corr_rank);
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


def _table_columns(conn: Any, table: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(f"DESCRIBE {_quote_relation(table)}").fetchall()}


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_features(
    conn: Any,
    *,
    feature_table: str,
    model_selection_run_id: str | None,
    explicit_features: list[str] | None,
) -> list[str]:
    cols = _table_columns(conn, feature_table)
    if explicit_features:
        features = explicit_features
    elif model_selection_run_id:
        selection = load_model_selection_run(conn, model_selection_run_id)
        features = list(selection["selected_features"])
    else:
        raise ValueError("either model_selection_run_id or explicit features is required")
    return [feature for feature in features if feature in cols]


def build_stock_horizon_profile(
    conn: Any,
    *,
    run_id: str,
    feature_table: str = "fact_feature_panel_candidate",
    feature_set_id: str | None = None,
    model_selection_run_id: str | None = None,
    features: list[str] | None = None,
    labels: list[str] | None = None,
    start_date: str = "2025-01-01",
    end_date: str | None = None,
    min_observations: int = 20,
    top_features_per_stock: int = 0,
) -> dict[str, Any]:
    ensure_tables(conn)
    conn.execute("DELETE FROM mart_stock_horizon_profile WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_stock_horizon_feature_effect WHERE run_id = ?", (run_id,))
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    labels = labels or DEFAULT_LABELS
    table_cols = _table_columns(conn, feature_table)
    if "date" not in table_cols:
        raise RuntimeError(f"{feature_table} must include a date column for drawdown comparison")
    labels = [label for label in labels if label in table_cols]
    if not labels:
        raise RuntimeError(f"no requested labels exist in {feature_table}")
    feature_cols = _resolve_features(
        conn,
        feature_table=feature_table,
        model_selection_run_id=model_selection_run_id,
        explicit_features=features,
    )
    if not feature_cols:
        raise RuntimeError("no usable feature columns for stock horizon profile")

    filters = ["date >= ?"]
    params: list[Any] = [start_date]
    if end_date:
        filters.append("date <= ?")
        params.append(end_date)
    if feature_set_id and "feature_set_id" in table_cols:
        filters.append("feature_set_id = ?")
        params.append(feature_set_id)
    where_sql = " AND ".join(filters)
    select_cols = [
        "stock_code",
        "date",
        *[_quote_ident(label) for label in labels],
        *[_quote_ident(feature) for feature in feature_cols],
    ]
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE stock_horizon_base AS
        SELECT {', '.join(select_cols)}
          FROM {_quote_relation(feature_table)}
         WHERE {where_sql}
        """,
        params,
    )

    profile_selects = []
    for label in labels:
        label_q = _quote_ident(label)
        horizon = holding_period_from_label(label)
        profile_selects.append(
            f"""
            SELECT stock_code,
                   '{label}' AS label_name,
                   {int(horizon or 0)} AS horizon_days,
                   s.obs_count,
                   s.avg_return,
                   s.median_return,
                   s.win_rate,
                   s.volatility,
                   s.downside_avg,
                   d.compounded_return,
                   d.max_drawdown
              FROM (
                    SELECT stock_code,
                           COUNT({label_q}) AS obs_count,
                           AVG({label_q}) AS avg_return,
                           MEDIAN({label_q}) AS median_return,
                           AVG(CASE WHEN {label_q} > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                           STDDEV_SAMP({label_q}) AS volatility,
                           AVG(CASE WHEN {label_q} < 0 THEN {label_q} ELSE 0.0 END) AS downside_avg
                      FROM stock_horizon_base
                     WHERE {label_q} IS NOT NULL
                     GROUP BY stock_code
                    HAVING COUNT({label_q}) >= {int(min_observations)}
                   ) s
              LEFT JOIN (
                    SELECT stock_code,
                           MAX(CASE WHEN rn_desc = 1 THEN equity END) - 1.0 AS compounded_return,
                           MIN(equity / NULLIF(peak_equity, 0.0) - 1.0) AS max_drawdown
                      FROM (
                            SELECT *,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY stock_code
                                       ORDER BY date DESC
                                   ) AS rn_desc
                              FROM (
                                    SELECT *,
                                           MAX(equity) OVER (
                                               PARTITION BY stock_code
                                               ORDER BY date
                                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                                           ) AS peak_equity
                                      FROM (
                                            SELECT stock_code,
                                                   date,
                                                   EXP(SUM(
                                                       CASE
                                                           WHEN {label_q} > -0.999999 THEN LN(1.0 + {label_q})
                                                           ELSE LN(0.000001)
                                                       END
                                                   ) OVER (
                                                       PARTITION BY stock_code
                                                       ORDER BY date
                                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                                                   )) AS equity
                                              FROM stock_horizon_base
                                             WHERE {label_q} IS NOT NULL
                                           ) path
                                   ) with_peak
                           ) drawdown_path
                     GROUP BY stock_code
                   ) d USING (stock_code)
            """
        )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE stock_horizon_profile_raw AS
        WITH raw AS ({' UNION ALL '.join(profile_selects)})
        SELECT *,
               COALESCE(avg_return, 0.0)
               + 0.15 * COALESCE(win_rate, 0.0)
               - 0.20 * COALESCE(volatility, 0.0)
               + 0.10 * COALESCE(downside_avg, 0.0)
               + 0.10 * COALESCE(max_drawdown, 0.0) AS horizon_score
          FROM raw
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_stock_horizon_profile (
            run_id,
            feature_table,
            feature_set_id,
            stock_code,
            label_name,
            horizon_days,
            obs_count,
            avg_return,
            median_return,
            win_rate,
            volatility,
            downside_avg,
            compounded_return,
            max_drawdown,
            horizon_score,
            rank_in_stock,
            is_best,
            built_at
        )
        SELECT ? AS run_id,
               ? AS feature_table,
               ? AS feature_set_id,
               stock_code,
               label_name,
               horizon_days,
               obs_count,
               avg_return,
               median_return,
               win_rate,
               volatility,
               downside_avg,
               compounded_return,
               max_drawdown,
               horizon_score,
               ROW_NUMBER() OVER (
                   PARTITION BY stock_code
                   ORDER BY horizon_score DESC NULLS LAST, horizon_days ASC
               ) AS rank_in_stock,
               ROW_NUMBER() OVER (
                   PARTITION BY stock_code
                   ORDER BY horizon_score DESC NULLS LAST, horizon_days ASC
               ) = 1 AS is_best,
               ? AS built_at
          FROM stock_horizon_profile_raw
        """,
        (run_id, feature_table, feature_set_id, built_at),
    )

    effect_selects = []
    for label in labels:
        label_q = _quote_ident(label)
        horizon = holding_period_from_label(label)
        for feature in feature_cols:
            feature_q = _quote_ident(feature)
            effect_selects.append(
                f"""
                SELECT stock_code,
                       '{label}' AS label_name,
                       {int(horizon or 0)} AS horizon_days,
                       '{feature}' AS feature_name,
                       COUNT(*) AS obs_count,
                       CORR(CAST({feature_q} AS DOUBLE), CAST({label_q} AS DOUBLE)) AS corr
                  FROM stock_horizon_base
                 WHERE {label_q} IS NOT NULL
                   AND {feature_q} IS NOT NULL
                 GROUP BY stock_code
                HAVING COUNT(*) >= {int(min_observations)}
                   AND CORR(CAST({feature_q} AS DOUBLE), CAST({label_q} AS DOUBLE)) IS NOT NULL
                   AND ISFINITE(CORR(CAST({feature_q} AS DOUBLE), CAST({label_q} AS DOUBLE)))
                """
            )
    if effect_selects:
        rank_filter = ""
        if top_features_per_stock and top_features_per_stock > 0:
            rank_filter = f"WHERE abs_corr_rank <= {int(top_features_per_stock)}"
        conn.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE stock_horizon_effect_raw AS
            WITH raw AS ({' UNION ALL '.join(effect_selects)}),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, label_name
                           ORDER BY ABS(corr) DESC NULLS LAST, corr DESC NULLS LAST, feature_name
                       ) AS abs_corr_rank
                  FROM raw
            )
            SELECT *
              FROM ranked
             {rank_filter}
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_stock_horizon_feature_effect
            SELECT ? AS run_id,
                   stock_code,
                   label_name,
                   horizon_days,
                   feature_name,
                   obs_count,
                   corr,
                   abs_corr_rank,
                   CASE WHEN corr > 0 THEN 'positive' WHEN corr < 0 THEN 'negative' ELSE 'flat' END AS effect_direction,
                   ? AS built_at
              FROM stock_horizon_effect_raw
            """,
            (run_id, built_at),
        )

    profile_count = int(conn.execute(
        "SELECT COUNT(*) FROM mart_stock_horizon_profile WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0])
    best_count = int(conn.execute(
        "SELECT COUNT(*) FROM mart_stock_horizon_profile WHERE run_id = ? AND is_best",
        (run_id,),
    ).fetchone()[0])
    effect_count = int(conn.execute(
        "SELECT COUNT(*) FROM mart_stock_horizon_feature_effect WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0])
    record_actual_version(conn, "mart_stock_horizon_profile")
    record_actual_version(conn, "mart_stock_horizon_feature_effect")
    duration_s = time.perf_counter() - t0
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_stock_horizon_profile",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[feature_table],
        output_tables=["mart_stock_horizon_profile", "mart_stock_horizon_feature_effect"],
        feature_group="stock_horizon_profile",
        label_name=",".join(labels),
        perf_summary={
            "feature_table": feature_table,
            "feature_set_id": feature_set_id,
            "labels": labels,
            "features": feature_cols,
            "min_observations": int(min_observations),
            "top_features_per_stock": int(top_features_per_stock),
            "profile_count": profile_count,
            "best_count": best_count,
            "effect_count": effect_count,
            "duration_s": duration_s,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "feature_table": feature_table,
        "feature_set_id": feature_set_id,
        "labels": labels,
        "feature_count": len(feature_cols),
        "profile_count": profile_count,
        "best_count": best_count,
        "effect_count": effect_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--feature-table", default="fact_feature_panel_candidate")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--model-selection-run-id", default=None)
    parser.add_argument("--features", default=None)
    parser.add_argument("--labels", default=",".join(DEFAULT_LABELS))
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-observations", type=int, default=20)
    parser.add_argument(
        "--top-features-per-stock",
        type=int,
        default=0,
        help="0 keeps every selected feature effect; positive values keep top-N per stock/horizon",
    )
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_stock_horizon_profile(
            conn,
            run_id=args.run_id,
            feature_table=args.feature_table,
            feature_set_id=args.feature_set_id,
            model_selection_run_id=args.model_selection_run_id,
            features=_parse_csv(args.features),
            labels=_parse_csv(args.labels),
            start_date=args.start_date,
            end_date=args.end_date,
            min_observations=args.min_observations,
            top_features_per_stock=args.top_features_per_stock,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
