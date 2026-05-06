#!/usr/bin/env python3
"""Build per-stock holding-horizon and feature-effect profiles."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
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
    path_obs_count INTEGER,
    horizon_score DOUBLE,
    rank_in_stock INTEGER,
    is_best BOOLEAN,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, stock_code, label_name)
);
ALTER TABLE mart_stock_horizon_profile ADD COLUMN IF NOT EXISTS compounded_return DOUBLE;
ALTER TABLE mart_stock_horizon_profile ADD COLUMN IF NOT EXISTS max_drawdown DOUBLE;
ALTER TABLE mart_stock_horizon_profile ADD COLUMN IF NOT EXISTS path_obs_count INTEGER;
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

CREATE TABLE IF NOT EXISTS mart_stock_horizon_selection (
    run_id TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    baseline_label TEXT NOT NULL,
    baseline_horizon_days INTEGER,
    selected_label TEXT NOT NULL,
    selected_horizon_days INTEGER,
    selected_horizon_confidence DOUBLE,
    selected_horizon_score DOUBLE,
    baseline_horizon_score DOUBLE,
    score_advantage DOUBLE,
    avg_return_advantage DOUBLE,
    selected_max_drawdown DOUBLE,
    baseline_max_drawdown DOUBLE,
    selected_obs_count INTEGER,
    baseline_obs_count INTEGER,
    gate_status TEXT NOT NULL,
    fallback_reason TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_stock_horizon_selection_horizon
    ON mart_stock_horizon_selection(run_id, selected_horizon_days, gate_status);
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


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
    baseline_label: str = "forward_ret_60d",
    min_score_advantage: float = 0.0,
    min_avg_return_advantage: float = 0.0,
    min_selection_confidence: float = 0.55,
    max_candidate_drawdown: float = 0.25,
    skip_feature_effects: bool = False,
) -> dict[str, Any]:
    ensure_tables(conn)
    conn.execute("DELETE FROM mart_stock_horizon_profile WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_stock_horizon_feature_effect WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_stock_horizon_selection WHERE run_id = ?", (run_id,))
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    stage_timings: dict[str, float] = {}
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    print(f"[stock_horizon] {utc_now_iso()} start run_id={run_id} table={feature_table}", flush=True)
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

    stage_started = time.perf_counter()
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
    stage_timings["build_base_s"] = round(time.perf_counter() - stage_started, 3)
    print(
        f"[stock_horizon] {utc_now_iso()} base done "
        f"features={len(feature_cols)} labels={len(labels)} elapsed={stage_timings['build_base_s']:.3f}s",
        flush=True,
    )

    stage_started = time.perf_counter()
    profile_selects = []
    for label in labels:
        label_q = _quote_ident(label)
        horizon = holding_period_from_label(label)
        horizon_step = max(int(horizon or 1), 1)
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
                   d.max_drawdown,
                   d.path_obs_count
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
                           COUNT(*) AS path_obs_count,
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
                                                           WHEN sampled_return > -0.999999 THEN LN(1.0 + sampled_return)
                                                           ELSE LN(0.000001)
                                                       END
                                                   ) OVER (
                                                       PARTITION BY stock_code
                                                       ORDER BY date
                                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                                                   )) AS equity
                                              FROM (
                                                    SELECT stock_code,
                                                           date,
                                                           {label_q} AS sampled_return
                                                      FROM (
                                                            SELECT stock_code,
                                                                   date,
                                                                   {label_q},
                                                                   ROW_NUMBER() OVER (
                                                                       PARTITION BY stock_code
                                                                       ORDER BY date
                                                                   ) AS rn
                                                              FROM stock_horizon_base
                                                             WHERE {label_q} IS NOT NULL
                                                           ) ordered_labels
                                                     WHERE ((rn - 1) % {horizon_step}) = 0
                                                   ) sampled_labels
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
            path_obs_count,
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
               path_obs_count,
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
    stage_timings["profile_metrics_s"] = round(time.perf_counter() - stage_started, 3)
    print(
        f"[stock_horizon] {utc_now_iso()} profile metrics done "
        f"elapsed={stage_timings['profile_metrics_s']:.3f}s",
        flush=True,
    )

    stage_started = time.perf_counter()
    if not skip_feature_effects:
        feature_aliases = [(feature, f"f_{idx:04d}") for idx, feature in enumerate(feature_cols)]
        aggregate_selects = []
        for label in labels:
            label_q = _quote_ident(label)
            label_finite = f"{label_q} IS NOT NULL AND ISFINITE(CAST({label_q} AS DOUBLE))"
            horizon = holding_period_from_label(label)
            aggregate_columns = []
            for feature, alias in feature_aliases:
                feature_q = _quote_ident(feature)
                feature_finite = f"{feature_q} IS NOT NULL AND ISFINITE(CAST({feature_q} AS DOUBLE))"
                valid_pair = f"{label_finite} AND {feature_finite}"
                aggregate_columns.extend(
                    [
                        f"SUM(CASE WHEN {valid_pair} THEN 1 ELSE 0 END) AS {alias}_obs",
                        f"""
                        CORR(
                            CASE WHEN {valid_pair} THEN CAST({feature_q} AS DOUBLE) END,
                            CASE WHEN {valid_pair} THEN CAST({label_q} AS DOUBLE) END
                        ) AS {alias}_corr
                        """,
                    ]
                )
            aggregate_selects.append(
                f"""
                SELECT stock_code,
                       {_quote_literal(label)} AS label_name,
                       {int(horizon or 0)} AS horizon_days,
                       {', '.join(aggregate_columns)}
                  FROM stock_horizon_base
                 WHERE {label_finite}
                 GROUP BY stock_code
                """
            )
        if aggregate_selects:
            effect_rows = []
            for feature, alias in feature_aliases:
                effect_rows.append(
                    f"""
                    SELECT stock_code,
                           label_name,
                           horizon_days,
                           {_quote_literal(feature)} AS feature_name,
                           {alias}_obs AS obs_count,
                           {alias}_corr AS corr
                      FROM stock_horizon_effect_agg
                     WHERE {alias}_obs >= {int(min_observations)}
                       AND {alias}_corr IS NOT NULL
                       AND ISFINITE({alias}_corr)
                    """
                )
            rank_filter = ""
            if top_features_per_stock and top_features_per_stock > 0:
                rank_filter = f"WHERE abs_corr_rank <= {int(top_features_per_stock)}"
            conn.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE stock_horizon_effect_agg AS
                {' UNION ALL '.join(aggregate_selects)}
                """
            )
            conn.execute(
                f"""
                CREATE OR REPLACE TEMP TABLE stock_horizon_effect_raw AS
                WITH raw AS ({' UNION ALL '.join(effect_rows)}),
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
    stage_timings["feature_effects_s"] = round(time.perf_counter() - stage_started, 3)
    print(
        f"[stock_horizon] {utc_now_iso()} feature effects done skipped={skip_feature_effects} "
        f"elapsed={stage_timings['feature_effects_s']:.3f}s",
        flush=True,
    )

    if baseline_label not in labels:
        raise RuntimeError(f"baseline_label must be included in labels: {baseline_label}")

    stage_started = time.perf_counter()
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE stock_horizon_candidates AS
        WITH baseline AS (
            SELECT *
              FROM mart_stock_horizon_profile
             WHERE run_id = ?
               AND label_name = ?
        ),
        ranked AS (
            SELECT p.*,
                   b.horizon_days AS baseline_horizon_days,
                   b.horizon_score AS baseline_horizon_score,
                   b.avg_return AS baseline_avg_return,
                   b.max_drawdown AS baseline_max_drawdown,
                   b.obs_count AS baseline_obs_count,
                   p.horizon_score - b.horizon_score AS score_advantage,
                   p.avg_return - b.avg_return AS avg_return_advantage,
                   0.50
                   + LEAST(0.25, GREATEST(-0.25, (p.horizon_score - b.horizon_score) * 2.0))
                   + LEAST(0.20, GREATEST(-0.20, (p.avg_return - b.avg_return) * 5.0))
                       AS selection_confidence
              FROM mart_stock_horizon_profile p
              JOIN baseline b
                ON b.run_id = p.run_id
               AND b.stock_code = p.stock_code
             WHERE p.run_id = ?
        ),
        eligible AS (
            SELECT *,
                   CASE
                     WHEN label_name = ? THEN 'baseline'
                     WHEN obs_count < ? THEN 'candidate_low_observation'
                     WHEN score_advantage < ? THEN 'candidate_score_advantage_below_threshold'
                     WHEN avg_return_advantage < ? THEN 'candidate_return_advantage_below_threshold'
                     WHEN max_drawdown IS NOT NULL AND max_drawdown < -? THEN 'candidate_drawdown_blocked'
                     WHEN selection_confidence < ? THEN 'candidate_confidence_low'
                     ELSE 'candidate_pass'
                   END AS candidate_status
              FROM ranked
        ),
        picked AS (
            SELECT *
              FROM eligible
             WHERE candidate_status IN ('baseline', 'candidate_pass')
             QUALIFY ROW_NUMBER() OVER (
                 PARTITION BY stock_code
                 ORDER BY
                   CASE WHEN candidate_status = 'candidate_pass' THEN 0 ELSE 1 END,
                   horizon_score DESC NULLS LAST,
                   horizon_days ASC
             ) = 1
        )
        SELECT b.stock_code,
               b.label_name AS baseline_label,
               b.horizon_days AS baseline_horizon_days,
               COALESCE(p.label_name, b.label_name) AS selected_label,
               COALESCE(p.horizon_days, b.horizon_days) AS selected_horizon_days,
               CASE
                 WHEN p.label_name IS NULL OR p.label_name = b.label_name THEN 1.0
                 ELSE LEAST(0.95, GREATEST(0.0, p.selection_confidence))
               END AS selected_horizon_confidence,
               COALESCE(p.horizon_score, b.horizon_score) AS selected_horizon_score,
               b.horizon_score AS baseline_horizon_score,
               COALESCE(p.score_advantage, 0.0) AS score_advantage,
               COALESCE(p.avg_return_advantage, 0.0) AS avg_return_advantage,
               COALESCE(p.max_drawdown, b.max_drawdown) AS selected_max_drawdown,
               b.max_drawdown AS baseline_max_drawdown,
               COALESCE(p.obs_count, b.obs_count) AS selected_obs_count,
               b.obs_count AS baseline_obs_count,
               CASE
                 WHEN p.label_name IS NULL OR p.label_name = b.label_name THEN 'baseline'
                 ELSE 'selected'
               END AS gate_status,
               CASE
                 WHEN p.label_name IS NULL THEN 'missing_baseline_candidate'
                 WHEN p.label_name = b.label_name THEN 'baseline_best_or_no_candidate_passed'
                 ELSE NULL
               END AS fallback_reason
          FROM mart_stock_horizon_profile b
          LEFT JOIN picked p
            ON p.run_id = b.run_id
           AND p.stock_code = b.stock_code
         WHERE b.run_id = ?
           AND b.label_name = ?
        """,
        (
            run_id,
            baseline_label,
            run_id,
            baseline_label,
            int(min_observations),
            float(min_score_advantage),
            float(min_avg_return_advantage),
            float(max_candidate_drawdown),
            float(min_selection_confidence),
            run_id,
            baseline_label,
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_stock_horizon_selection (
            run_id, stock_code, baseline_label, baseline_horizon_days,
            selected_label, selected_horizon_days, selected_horizon_confidence,
            selected_horizon_score, baseline_horizon_score, score_advantage,
            avg_return_advantage, selected_max_drawdown, baseline_max_drawdown,
            selected_obs_count, baseline_obs_count, gate_status, fallback_reason,
            built_at
        )
        SELECT ? AS run_id,
               stock_code,
               baseline_label,
               baseline_horizon_days,
               selected_label,
               selected_horizon_days,
               selected_horizon_confidence,
               selected_horizon_score,
               baseline_horizon_score,
               score_advantage,
               avg_return_advantage,
               selected_max_drawdown,
               baseline_max_drawdown,
               selected_obs_count,
               baseline_obs_count,
               gate_status,
               fallback_reason,
               ? AS built_at
          FROM stock_horizon_candidates
        """,
        (run_id, built_at),
    )
    stage_timings["selection_s"] = round(time.perf_counter() - stage_started, 3)
    print(
        f"[stock_horizon] {utc_now_iso()} selection done elapsed={stage_timings['selection_s']:.3f}s",
        flush=True,
    )

    stage_started = time.perf_counter()
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
    selection_count = int(conn.execute(
        "SELECT COUNT(*) FROM mart_stock_horizon_selection WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0])
    selected_non_baseline_count = int(conn.execute(
        """
        SELECT COUNT(*) FROM mart_stock_horizon_selection
         WHERE run_id = ? AND selected_label <> baseline_label
        """,
        (run_id,),
    ).fetchone()[0])
    stage_timings["count_outputs_s"] = round(time.perf_counter() - stage_started, 3)
    record_actual_version(conn, "mart_stock_horizon_profile")
    record_actual_version(conn, "mart_stock_horizon_feature_effect")
    record_actual_version(conn, "mart_stock_horizon_selection")
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
        output_tables=[
            "mart_stock_horizon_profile",
            "mart_stock_horizon_feature_effect",
            "mart_stock_horizon_selection",
        ],
        feature_group="stock_horizon_profile",
        label_name=",".join(labels),
        perf_summary={
            "feature_table": feature_table,
            "feature_set_id": feature_set_id,
            "labels": labels,
            "features": feature_cols,
            "min_observations": int(min_observations),
            "top_features_per_stock": int(top_features_per_stock),
            "skip_feature_effects": bool(skip_feature_effects),
            "feature_effect_builder": "skipped" if skip_feature_effects else "batched_label_aggregate",
            "profile_count": profile_count,
            "best_count": best_count,
            "effect_count": effect_count,
            "selection_count": selection_count,
            "selected_non_baseline_count": selected_non_baseline_count,
            "duration_s": duration_s,
            "stage_timings": stage_timings,
        },
    )
    conn.commit()
    print(
        f"[stock_horizon] {utc_now_iso()} done run_id={run_id} "
        f"profile={profile_count} effects={effect_count} selections={selection_count} "
        f"elapsed={duration_s:.3f}s",
        flush=True,
    )
    return {
        "run_id": run_id,
        "feature_table": feature_table,
        "feature_set_id": feature_set_id,
        "labels": labels,
        "feature_count": len(feature_cols),
        "profile_count": profile_count,
        "best_count": best_count,
        "effect_count": effect_count,
        "selection_count": selection_count,
        "selected_non_baseline_count": selected_non_baseline_count,
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
    parser.add_argument("--baseline-label", default="forward_ret_60d")
    parser.add_argument("--min-score-advantage", type=float, default=0.0)
    parser.add_argument("--min-avg-return-advantage", type=float, default=0.0)
    parser.add_argument("--min-selection-confidence", type=float, default=0.55)
    parser.add_argument("--max-candidate-drawdown", type=float, default=0.25)
    parser.add_argument(
        "--skip-feature-effects",
        action="store_true",
        help="Only build horizon metrics/60d selection; skip expensive per-stock feature correlations.",
    )
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
            baseline_label=args.baseline_label,
            min_score_advantage=args.min_score_advantage,
            min_avg_return_advantage=args.min_avg_return_advantage,
            min_selection_confidence=args.min_selection_confidence,
            max_candidate_drawdown=args.max_candidate_drawdown,
            skip_feature_effects=args.skip_feature_effects,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
