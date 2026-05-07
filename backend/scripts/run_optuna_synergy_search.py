#!/usr/bin/env python3
"""Optuna proxy search over temporal feature relevance and pair synergy."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import optuna

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.model_feature_schema import holding_period_from_label  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)


DDL = """
CREATE TABLE IF NOT EXISTS mart_optuna_synergy_trial (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    objective_value DOUBLE,
    selected_count INTEGER,
    selected_interaction_count INTEGER,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    params_json TEXT,
    metrics_json TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, trial_number)
);
CREATE INDEX IF NOT EXISTS idx_optuna_synergy_trial_run
    ON mart_optuna_synergy_trial(run_id);

CREATE TABLE IF NOT EXISTS mart_optuna_synergy_study_summary (
    run_id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    best_trial_number INTEGER,
    objective_score DOUBLE,
    trials INTEGER,
    study_total_trials INTEGER,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    config_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_candidate (
    run_id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    objective_score DOUBLE,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    gate_status TEXT NOT NULL,
    notes_json TEXT,
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


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def default_optuna_storage_url() -> str:
    storage_dir = Path(__file__).resolve().parent.parent.parent / "data" / "optuna"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{storage_dir / 'synergy_studies.sqlite3'}"


def _finite_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _progress(message: str) -> None:
    print(f"[optuna_synergy] {utc_now_iso()} {message}", flush=True)


def _trial_value(trial: Any, default: float = float("-inf")) -> float:
    value = getattr(trial, "value", None)
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _store_trial_outcome(trial: optuna.Trial, outcome: dict[str, Any]) -> None:
    trial.set_user_attr("synergy_selected_features", outcome["selected_features"])
    trial.set_user_attr("synergy_selected_interactions", outcome["selected_interactions"])
    trial.set_user_attr("synergy_metrics", outcome["metrics"])


def latest_temporal_synergy_run_id(conn: Any) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_temporal_research_panel_quality
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def _load_relevance(conn: Any, source_run_id: str, label_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT feature_name,
               coverage_pct,
               rank_ic,
               directional_spread,
               stability_score,
               daily_count
          FROM mart_feature_temporal_relevance
         WHERE run_id = ?
           AND label_name = ?
         ORDER BY ABS(COALESCE(rank_ic, 0)) DESC,
                  ABS(COALESCE(directional_spread, 0)) DESC,
                  feature_name
        """,
        (source_run_id, label_name),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no temporal relevance rows for run={source_run_id} label={label_name}")
    return [dict(row) for row in rows]


def _load_pairs(conn: Any, source_run_id: str, label_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT feature_a,
               feature_b,
               joint_uplift,
               interaction_score,
               joint_obs_count,
               feature_corr
          FROM mart_feature_pair_synergy
         WHERE run_id = ?
           AND label_name = ?
           AND joint_uplift IS NOT NULL
         ORDER BY interaction_score DESC NULLS LAST,
                  joint_uplift DESC NULLS LAST,
                  feature_a,
                  feature_b
        """,
        (source_run_id, label_name),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["interaction_type"] = "pair"
        out.append(item)
    return out


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: Any, table_name: str) -> set[str]:
    return {
        str(row["column_name"])
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


def _stddev(values: list[float]) -> float:
    clean = [value for value in values if math.isfinite(value)]
    if len(clean) <= 1:
        return 0.0
    avg = sum(clean) / len(clean)
    return math.sqrt(sum((value - avg) ** 2 for value in clean) / (len(clean) - 1))


def _max_drawdown(returns: list[float]) -> tuple[float, float]:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity *= 1.0 + _finite_float(value)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1.0)
    return equity - 1.0, max_dd


def _dailyize_holding_period_return(value: float, holding_period_days: int | None) -> float:
    horizon = max(int(holding_period_days or 1), 1)
    clean = _finite_float(value)
    if horizon <= 1:
        return clean
    if clean <= -1.0:
        return -1.0
    return math.pow(max(1.0 + clean, 1e-9), 1.0 / horizon) - 1.0


def _daily_turnover(current: set[str], previous: set[str] | None) -> float:
    if not current:
        return 0.0
    if previous is None:
        return 1.0
    overlap = len(current & previous)
    return max(0.0, 1.0 - overlap / max(len(current), 1))


def _load_feature_directions(
    conn: Any,
    *,
    source_run_id: str,
    label_name: str,
    features: list[str],
) -> dict[str, int]:
    if not features:
        return {}
    rows = conn.execute(
        """
        SELECT feature_name, rank_ic
          FROM mart_feature_temporal_relevance
         WHERE run_id = ?
           AND label_name = ?
           AND feature_name IN ({})
        """.format(", ".join(["?"] * len(features))),
        (source_run_id, label_name, *features),
    ).fetchall()
    by_feature = {str(row["feature_name"]): _finite_float(row["rank_ic"]) for row in rows}
    return {feature: (-1 if by_feature.get(feature, 0.0) < 0 else 1) for feature in features}


def _risk_rank_cache_key(
    *,
    source_run_id: str,
    label_name: str,
    date_column: str,
    features: list[str],
    feature_directions: dict[str, int],
) -> str:
    payload = {
        "rank_mode": "daily_percent_rank_directional_v1",
        "source_run_id": source_run_id,
        "label_name": label_name,
        "date_column": date_column,
        "features": [
            {"feature_name": feature, "direction": int(feature_directions.get(feature, 1))}
            for feature in sorted(features)
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _empty_risk_rank_cache() -> dict[str, Any]:
    return {
        "tables": {},
        "stats": {
            "enabled": True,
            "hits": 0,
            "misses": 0,
            "entry_count": 0,
            "build_s": 0.0,
            "lookup_s": 0.0,
        },
    }


def _risk_rank_cache_stats(cache: dict[str, Any] | None) -> dict[str, Any]:
    if not cache:
        return {"enabled": False}
    stats = dict(cache.get("stats") or {})
    stats["entry_count"] = len(cache.get("tables") or {})
    stats["preferred_feature_count"] = len(cache.get("preferred_features") or [])
    for key in ("build_s", "lookup_s"):
        stats[key] = round(float(stats.get(key) or 0.0), 3)
    for key in ("hits", "misses", "entry_count", "preferred_feature_count"):
        stats[key] = int(stats.get(key) or 0)
    stats["enabled"] = bool(stats.get("enabled", True))
    return stats


def _risk_feature_rank_table(
    conn: Any,
    *,
    panel_table: str,
    source_run_id: str,
    label_name: str,
    date_column: str,
    usable_features: list[str],
    feature_directions: dict[str, int],
    rank_cache: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    stats = rank_cache.get("stats") if rank_cache else None
    tables = rank_cache.get("tables") if rank_cache else None
    cache_key = _risk_rank_cache_key(
        source_run_id=source_run_id,
        label_name=label_name,
        date_column=date_column,
        features=usable_features,
        feature_directions=feature_directions,
    )
    lookup_t0 = time.perf_counter()
    if isinstance(tables, dict) and cache_key in tables:
        if isinstance(stats, dict):
            stats["hits"] = int(stats.get("hits") or 0) + 1
            stats["lookup_s"] = float(stats.get("lookup_s") or 0.0) + time.perf_counter() - lookup_t0
        entry = dict(tables[cache_key])
        return str(entry["table_name"]), {
            "status": "hit",
            "cache_key": cache_key,
            "table_name": entry["table_name"],
            "feature_count": len(usable_features),
        }

    if isinstance(stats, dict):
        stats["misses"] = int(stats.get("misses") or 0) + 1
        stats["lookup_s"] = float(stats.get("lookup_s") or 0.0) + time.perf_counter() - lookup_t0
    table_name = f"optuna_risk_feature_rank_{cache_key[:20]}" if tables is not None else "optuna_risk_feature_rank"
    rank_exprs = []
    for feature in usable_features:
        direction = feature_directions.get(feature, 1)
        order = "ASC" if direction >= 0 else "DESC"
        alias = f"__risk_rank_{feature}"
        rank_exprs.append(
            "PERCENT_RANK() OVER "
            f"(PARTITION BY {_quote_ident(date_column)} "
            f"ORDER BY {_quote_ident(feature)} {order} NULLS FIRST) AS {_quote_ident(alias)}"
        )
    build_t0 = time.perf_counter()
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {_quote_relation(table_name)} AS
        SELECT stock_code,
               CAST({_quote_ident(date_column)} AS VARCHAR) AS date,
               {_quote_ident(label_name)} AS label_value,
               {", ".join(rank_exprs)}
          FROM {_quote_relation(panel_table)}
         WHERE run_id = ?
           AND {_quote_ident(label_name)} IS NOT NULL
        """,
        (source_run_id,),
    )
    build_s = time.perf_counter() - build_t0
    if isinstance(tables, dict):
        tables[cache_key] = {"table_name": table_name, "feature_count": len(usable_features)}
        if isinstance(stats, dict):
            stats["entry_count"] = len(tables)
            stats["build_s"] = float(stats.get("build_s") or 0.0) + build_s
    return table_name, {
        "status": "miss",
        "cache_key": cache_key,
        "table_name": table_name,
        "feature_count": len(usable_features),
        "build_s": round(build_s, 3),
    }


def _load_feature_clusters(conn: Any, source_run_id: str) -> dict[str, str]:
    table_name = "mart_feature_cluster_redundancy"
    if not _table_exists(conn, table_name):
        return {}
    latest = conn.execute(
        """
        SELECT run_id
          FROM mart_feature_cluster_redundancy
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """,
        (source_run_id,),
    ).fetchone()
    if not latest:
        return {}
    rows = conn.execute(
        """
        SELECT feature_name, cluster_id
          FROM mart_feature_cluster_redundancy
         WHERE run_id = ?
        """,
        (latest["run_id"],),
    ).fetchall()
    return {str(row["feature_name"]): str(row["cluster_id"]) for row in rows}


def _load_conditionals(conn: Any, source_run_id: str, label_name: str) -> list[dict[str, Any]]:
    table_name = "mart_feature_conditional_synergy"
    if not _table_exists(conn, table_name):
        return []
    rows = conn.execute(
        """
        SELECT condition_feature AS feature_a,
               response_feature AS feature_b,
               incremental_uplift AS joint_uplift,
               interaction_score,
               conditional_response_obs_count AS joint_obs_count,
               feature_corr
          FROM mart_feature_conditional_synergy
         WHERE run_id = ?
           AND label_name = ?
           AND selected = TRUE
           AND incremental_uplift IS NOT NULL
         ORDER BY interaction_score DESC NULLS LAST,
                  incremental_uplift DESC NULLS LAST,
                  condition_feature,
                  response_feature
        """,
        (source_run_id, label_name),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["interaction_type"] = "conditional"
        out.append(item)
    return out


def _risk_evaluate_selection(
    conn: Any,
    *,
    source_run_id: str,
    label_name: str,
    selected_features: list[str],
    selected_interactions: list[dict[str, Any]],
    top_quantile: float,
    folds: int,
    transaction_cost_bps: float,
    conditional_threshold: float,
    proxy_objective: float,
    proxy_weight: float,
    rank_ic_weight: float,
    return_weight: float,
    drawdown_penalty_weight: float,
    turnover_penalty_weight: float,
    rank_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    panel_table = "mart_temporal_research_panel"
    if not _table_exists(conn, panel_table):
        return {
            "available": False,
            "reason": "missing_mart_temporal_research_panel",
            "risk_objective": proxy_objective,
        }
    panel_cols = _table_columns(conn, panel_table)
    date_column = "date" if "date" in panel_cols else "signal_date" if "signal_date" in panel_cols else None
    required = {"run_id", "stock_code", label_name}
    missing_required = sorted(required - panel_cols)
    if not date_column:
        missing_required.append("date_or_signal_date")
    usable_features = [feature for feature in selected_features if feature in panel_cols]
    missing_features = sorted(set(selected_features) - set(usable_features))
    if missing_required or not usable_features:
        return {
            "available": False,
            "reason": "missing_required_columns_or_features",
            "missing_required": missing_required,
            "missing_features": missing_features,
            "risk_objective": proxy_objective,
        }

    rank_features = list(usable_features)
    if rank_cache:
        preferred_features = [
            str(feature)
            for feature in rank_cache.get("preferred_features", [])
            if str(feature) in panel_cols
        ]
        if preferred_features and set(usable_features).issubset(preferred_features):
            rank_features = preferred_features

    feature_directions = _load_feature_directions(
        conn,
        source_run_id=source_run_id,
        label_name=label_name,
        features=rank_features,
    )
    feature_rank_table, feature_rank_cache = _risk_feature_rank_table(
        conn,
        panel_table=panel_table,
        source_run_id=source_run_id,
        label_name=label_name,
        date_column=date_column,
        usable_features=rank_features,
        feature_directions=feature_directions,
        rank_cache=rank_cache,
    )
    score_terms = [f"{_quote_ident(f'__risk_rank_{feature}')}" for feature in usable_features]
    interaction_terms = []
    for interaction in selected_interactions:
        a = str(interaction.get("feature_a") or "")
        b = str(interaction.get("feature_b") or "")
        interaction_type = str(interaction.get("interaction_type") or "pair").lower()
        if a in usable_features and b in usable_features:
            rank_a = _quote_ident(f"__risk_rank_{a}")
            rank_b = _quote_ident(f"__risk_rank_{b}")
            if interaction_type == "conditional":
                threshold_sql = max(min(float(conditional_threshold), 0.999), 0.001)
                interaction_terms.append(
                    f"CASE WHEN {rank_a} >= {threshold_sql} THEN {rank_b} ELSE 0.5 END"
                )
            else:
                interaction_terms.append(f"{rank_a} * {rank_b}")
    all_terms = score_terms + interaction_terms
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE optuna_risk_scored AS
        SELECT stock_code,
               date,
               label_value,
               ({' + '.join(all_terms)}) / {float(len(all_terms))} AS policy_score
          FROM {_quote_relation(feature_rank_table)}
        """
    )
    fold_count = max(int(folds), 1)
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE optuna_risk_dates AS
        SELECT date,
               NTILE({fold_count}) OVER (ORDER BY CAST(date AS DATE)) AS fold_id
          FROM (SELECT DISTINCT date FROM optuna_risk_scored)
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE optuna_risk_ranked AS
        SELECT s.stock_code,
               s.date,
               d.fold_id,
               s.label_value,
               s.policy_score,
               PERCENT_RANK() OVER (PARTITION BY s.date ORDER BY s.label_value NULLS FIRST) AS label_rank,
               PERCENT_RANK() OVER (PARTITION BY s.date ORDER BY s.policy_score NULLS FIRST) AS score_rank
          FROM optuna_risk_scored s
          JOIN optuna_risk_dates d USING (date)
         WHERE s.policy_score IS NOT NULL
           AND s.label_value IS NOT NULL
        """
    )
    threshold = 1.0 - max(min(float(top_quantile), 1.0), 0.001)
    daily_rows = conn.execute(
        """
        SELECT fold_id,
               date,
               COUNT(*) AS obs_count,
               COUNT(DISTINCT stock_code) AS stock_count,
               CORR(score_rank, label_rank) AS rank_ic,
               AVG(label_value) AS universe_avg_return,
               AVG(CASE WHEN score_rank >= ? THEN label_value ELSE NULL END) AS top_avg_return,
               SUM(CASE WHEN score_rank >= ? THEN 1 ELSE 0 END) AS top_obs_count,
               AVG(CASE WHEN score_rank >= ? THEN CASE WHEN label_value > 0 THEN 1.0 ELSE 0.0 END ELSE NULL END) AS top_hit_rate
          FROM optuna_risk_ranked
         GROUP BY fold_id, date
         ORDER BY fold_id, CAST(date AS DATE)
        """,
        (threshold, threshold, threshold),
    ).fetchall()
    holding_rows = conn.execute(
        """
        SELECT fold_id, date, stock_code
          FROM optuna_risk_ranked
         WHERE score_rank >= ?
         ORDER BY fold_id, CAST(date AS DATE), stock_code
        """,
        (threshold,),
    ).fetchall()
    holdings_by_fold_date: dict[tuple[int, str], set[str]] = {}
    for row in holding_rows:
        key = (int(row["fold_id"]), str(row["date"]))
        holdings_by_fold_date.setdefault(key, set()).add(str(row["stock_code"]))

    by_fold: dict[int, list[dict[str, Any]]] = {}
    for row in daily_rows:
        by_fold.setdefault(int(row["fold_id"]), []).append(dict(row))

    transaction_cost_rate = max(float(transaction_cost_bps), 0.0) / 10000.0
    candidate_horizon_days = holding_period_from_label(label_name)
    fold_metrics = []
    for fold_id in sorted(by_fold):
        rows = by_fold[fold_id]
        rank_ics = [_finite_float(row["rank_ic"]) for row in rows if row["rank_ic"] is not None]
        top_returns = [_finite_float(row["top_avg_return"]) for row in rows if row["top_avg_return"] is not None]
        universe_returns = [_finite_float(row["universe_avg_return"]) for row in rows if row["universe_avg_return"] is not None]
        turnovers = []
        adjusted_top_returns = []
        adjusted_top_excess = []
        previous_holdings: set[str] | None = None
        for row in rows:
            current_holdings = holdings_by_fold_date.get((fold_id, str(row["date"])), set())
            turnover = _daily_turnover(current_holdings, previous_holdings)
            previous_holdings = current_holdings
            turnovers.append(turnover)
            if row["top_avg_return"] is not None:
                adjusted_top = _finite_float(row["top_avg_return"]) - turnover * transaction_cost_rate
                adjusted_top_returns.append(adjusted_top)
                if row["universe_avg_return"] is not None:
                    adjusted_top_excess.append(adjusted_top - _finite_float(row["universe_avg_return"]))
        top_excess = [
            _finite_float(row["top_avg_return"]) - _finite_float(row["universe_avg_return"])
            for row in rows
            if row["top_avg_return"] is not None and row["universe_avg_return"] is not None
        ]
        drawdown_source_returns = adjusted_top_returns or top_returns
        drawdown_returns = [
            _dailyize_holding_period_return(value, candidate_horizon_days)
            for value in drawdown_source_returns
        ]
        compounded_return, max_drawdown = _max_drawdown(drawdown_returns)
        fold_metrics.append(
            {
                "fold_id": fold_id,
                "start_date": rows[0]["date"],
                "end_date": rows[-1]["date"],
                "date_count": len(rows),
                "obs_count": int(sum(int(row["obs_count"] or 0) for row in rows)),
                "stock_count": int(max(int(row["stock_count"] or 0) for row in rows)),
                "rank_ic": sum(rank_ics) / len(rank_ics) if rank_ics else 0.0,
                "top_quantile": float(top_quantile),
                "top_obs_count": int(sum(int(row["top_obs_count"] or 0) for row in rows)),
                "top_avg_return": sum(top_returns) / len(top_returns) if top_returns else 0.0,
                "universe_avg_return": sum(universe_returns) / len(universe_returns) if universe_returns else 0.0,
                "top_excess_return": sum(top_excess) / len(top_excess) if top_excess else 0.0,
                "avg_cost_adjusted_top_excess_return": sum(adjusted_top_excess) / len(adjusted_top_excess)
                if adjusted_top_excess
                else 0.0,
                "avg_turnover": sum(turnovers) / len(turnovers) if turnovers else 0.0,
                "compounded_top_return": compounded_return,
                "max_drawdown": max_drawdown,
            }
        )

    rank_ics = [metric["rank_ic"] for metric in fold_metrics]
    top_excess = [metric["top_excess_return"] for metric in fold_metrics]
    cost_adjusted = [metric["avg_cost_adjusted_top_excess_return"] for metric in fold_metrics]
    turnovers = [metric["avg_turnover"] for metric in fold_metrics]
    drawdowns = [metric["max_drawdown"] for metric in fold_metrics]
    avg_rank_ic = sum(rank_ics) / len(rank_ics) if rank_ics else 0.0
    avg_top_excess = sum(top_excess) / len(top_excess) if top_excess else 0.0
    avg_cost_adjusted = sum(cost_adjusted) / len(cost_adjusted) if cost_adjusted else 0.0
    avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0.0
    worst_drawdown = min(drawdowns) if drawdowns else 0.0
    risk_objective = (
        proxy_weight * proxy_objective
        + rank_ic_weight * avg_rank_ic
        + return_weight * avg_cost_adjusted
        - drawdown_penalty_weight * abs(worst_drawdown)
        - turnover_penalty_weight * avg_turnover
    )
    return {
        "available": True,
        "risk_objective": risk_objective,
        "proxy_objective": proxy_objective,
        "avg_rank_ic": avg_rank_ic,
        "std_rank_ic": _stddev(rank_ics),
        "avg_top_excess_return": avg_top_excess,
        "avg_cost_adjusted_top_excess_return": avg_cost_adjusted,
        "avg_turnover": avg_turnover,
        "worst_max_drawdown": worst_drawdown,
        "fold_count": len(fold_metrics),
        "drawdown_return_mode": "cost_adjusted_holding_period_return_dailyized",
        "missing_features": missing_features,
        "rank_feature_count": len(rank_features),
        "feature_rank_cache": feature_rank_cache,
        "fold_metrics": fold_metrics,
        "weights": {
            "proxy_weight": proxy_weight,
            "rank_ic_weight": rank_ic_weight,
            "return_weight": return_weight,
            "drawdown_penalty_weight": drawdown_penalty_weight,
            "turnover_penalty_weight": turnover_penalty_weight,
        },
    }


def _feature_score(row: dict[str, Any], params: dict[str, float]) -> float:
    coverage = _finite_float(row.get("coverage_pct")) / 100.0
    rank_ic = abs(_finite_float(row.get("rank_ic")))
    spread = max(_finite_float(row.get("directional_spread")), 0.0)
    stability = max(_finite_float(row.get("stability_score")), 0.0)
    daily_count = math.log1p(max(_finite_float(row.get("daily_count")), 0.0))
    return (
        params["rank_ic_weight"] * rank_ic
        + params["spread_weight"] * spread
        + params["stability_weight"] * stability
        + params["coverage_weight"] * coverage
        + params["daily_count_weight"] * daily_count
    )


def _pair_score(row: dict[str, Any], params: dict[str, float]) -> float:
    uplift = max(_finite_float(row.get("joint_uplift")), 0.0)
    interaction = max(_finite_float(row.get("interaction_score")), 0.0)
    obs = math.log1p(max(_finite_float(row.get("joint_obs_count")), 0.0))
    corr_penalty = abs(_finite_float(row.get("feature_corr")))
    return (
        params["interaction_weight"] * interaction
        + params["uplift_weight"] * uplift
        + params["joint_obs_weight"] * obs
        - params["corr_penalty_weight"] * corr_penalty
    )


def _evaluate_policy(
    *,
    relevance: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    params: dict[str, float],
    max_features: int,
    max_interactions: int,
    min_coverage_pct: float,
    feature_clusters: dict[str, str] | None = None,
    feature_allowlist: set[str] | None = None,
) -> dict[str, Any]:
    eligible_features = [
        row
        for row in relevance
        if _finite_float(row.get("coverage_pct")) >= min_coverage_pct
        and (feature_allowlist is None or str(row["feature_name"]) in feature_allowlist)
    ]
    ranked_features = sorted(
        eligible_features,
        key=lambda row: (-_feature_score(row, params), str(row["feature_name"])),
    )
    feature_clusters = feature_clusters or {}
    selected_feature_rows = []
    deferred_cluster_duplicates = []
    used_clusters: set[str] = set()
    for row in ranked_features:
        feature = str(row["feature_name"])
        cluster_id = feature_clusters.get(feature, feature)
        if cluster_id not in used_clusters:
            selected_feature_rows.append(row)
            used_clusters.add(cluster_id)
        else:
            deferred_cluster_duplicates.append(row)
        if len(selected_feature_rows) >= max(int(max_features), 1):
            break
    if len(selected_feature_rows) < max(int(max_features), 1):
        selected_feature_rows.extend(
            deferred_cluster_duplicates[: max(int(max_features), 1) - len(selected_feature_rows)]
        )
    selected_features = [str(row["feature_name"]) for row in selected_feature_rows]
    selected_set = set(selected_features)
    eligible_pairs = [
        row
        for row in pairs
        if str(row["feature_a"]) in selected_set
        and str(row["feature_b"]) in selected_set
        and _finite_float(row.get("joint_uplift")) > 0
    ]
    ranked_pairs = sorted(
        eligible_pairs,
        key=lambda row: (-_pair_score(row, params), str(row["feature_a"]), str(row["feature_b"])),
    )
    selected_pair_rows = ranked_pairs[: max(int(max_interactions), 0)]
    selected_interactions = [
        {
            "interaction_type": row.get("interaction_type") or "pair",
            "feature_a": row["feature_a"],
            "feature_b": row["feature_b"],
        }
        for row in selected_pair_rows
    ]
    feature_component = sum(_feature_score(row, params) for row in selected_feature_rows)
    interaction_component = sum(_pair_score(row, params) for row in selected_pair_rows)
    redundancy_penalty = sum(abs(_finite_float(row.get("feature_corr"))) for row in selected_pair_rows) * params["redundancy_weight"]
    count_penalty = max(len(selected_features) - 1, 0) * params["feature_count_penalty"]
    selected_cluster_ids = [feature_clusters.get(feature, feature) for feature in selected_features]
    cluster_duplicate_count = max(len(selected_cluster_ids) - len(set(selected_cluster_ids)), 0)
    cluster_duplicate_penalty = cluster_duplicate_count * params["cluster_duplicate_penalty_weight"]
    objective = feature_component + interaction_component - redundancy_penalty - count_penalty - cluster_duplicate_penalty
    return {
        "objective": objective,
        "selected_features": selected_features,
        "selected_interactions": selected_interactions,
        "metrics": {
            "feature_component": feature_component,
            "interaction_component": interaction_component,
            "redundancy_penalty": redundancy_penalty,
            "feature_count_penalty": count_penalty,
            "cluster_duplicate_count": cluster_duplicate_count,
            "cluster_duplicate_penalty": cluster_duplicate_penalty,
            "eligible_feature_count": len(eligible_features),
            "eligible_pair_count": len(eligible_pairs),
        },
    }


def _suggest_params(trial: optuna.Trial) -> dict[str, float]:
    return {
        "rank_ic_weight": trial.suggest_float("rank_ic_weight", 0.5, 3.0),
        "spread_weight": trial.suggest_float("spread_weight", 0.0, 2.0),
        "stability_weight": trial.suggest_float("stability_weight", 0.0, 2.0),
        "coverage_weight": trial.suggest_float("coverage_weight", 0.0, 0.3),
        "daily_count_weight": trial.suggest_float("daily_count_weight", 0.0, 0.05),
        "interaction_weight": trial.suggest_float("interaction_weight", 0.0, 2.0),
        "uplift_weight": trial.suggest_float("uplift_weight", 0.0, 10.0),
        "joint_obs_weight": trial.suggest_float("joint_obs_weight", 0.0, 0.05),
        "corr_penalty_weight": trial.suggest_float("corr_penalty_weight", 0.0, 1.0),
        "redundancy_weight": trial.suggest_float("redundancy_weight", 0.0, 0.5),
        "cluster_duplicate_penalty_weight": trial.suggest_float("cluster_duplicate_penalty_weight", 0.0, 0.5),
        "feature_count_penalty": trial.suggest_float("feature_count_penalty", 0.0, 0.05),
    }


def _deterministic_params() -> dict[str, float]:
    return {
        "rank_ic_weight": 1.0,
        "spread_weight": 0.5,
        "stability_weight": 0.5,
        "coverage_weight": 0.05,
        "daily_count_weight": 0.01,
        "interaction_weight": 1.0,
        "uplift_weight": 2.0,
        "joint_obs_weight": 0.01,
        "corr_penalty_weight": 0.2,
        "redundancy_weight": 0.1,
        "cluster_duplicate_penalty_weight": 0.1,
        "feature_count_penalty": 0.01,
    }


def run_optuna_synergy_search(
    conn: Any,
    *,
    source_run_id: str | None = None,
    label_name: str = "forward_ret_20d",
    run_id: str | None = None,
    trials: int = 20,
    min_features: int = 3,
    max_features: int = 12,
    max_interactions: int = 8,
    min_coverage_pct: float = 80.0,
    seed: int = 42,
    storage_url: str | None = None,
    study_name: str | None = None,
    feature_subset_pool_size: int = 0,
    risk_aware: bool = False,
    risk_eval_top_trials: int = 20,
    risk_top_quantile: float = 0.20,
    risk_folds: int = 4,
    risk_transaction_cost_bps: float = 20.0,
    risk_conditional_threshold: float = 0.80,
    risk_proxy_weight: float = 0.05,
    risk_rank_ic_weight: float = 10.0,
    risk_return_weight: float = 100.0,
    risk_drawdown_penalty_weight: float = 3.0,
    risk_turnover_penalty_weight: float = 1.0,
) -> dict[str, Any]:
    ensure_tables(conn)
    source_run_id = source_run_id or latest_temporal_synergy_run_id(conn)
    if not source_run_id:
        raise RuntimeError("source_run_id is required and no temporal synergy run exists")
    run_id = run_id or f"optuna_synergy_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    study_name = study_name or run_id
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    stage_timings: dict[str, float] = {}
    _progress(
        f"start run_id={run_id} source_run_id={source_run_id} "
        f"label={label_name} trials={trials}"
    )
    load_t0 = time.perf_counter()
    relevance = _load_relevance(conn, source_run_id, label_name)
    pair_rows = _load_pairs(conn, source_run_id, label_name)
    conditional_rows = _load_conditionals(conn, source_run_id, label_name)
    pairs = pair_rows + conditional_rows
    feature_clusters = _load_feature_clusters(conn, source_run_id)
    stage_timings["load_inputs_s"] = round(time.perf_counter() - load_t0, 3)
    _progress(
        f"loaded relevance={len(relevance)} pair_rows={len(pair_rows)} "
        f"conditional_rows={len(conditional_rows)} clusters={len(feature_clusters)}"
    )
    conn.execute("DELETE FROM mart_optuna_synergy_trial WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_optuna_synergy_study_summary WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_synergy_policy_candidate WHERE run_id = ?", (run_id,))

    max_features = max(min(int(max_features), len(relevance)), 1)
    min_features = max(min(int(min_features), max_features), 1)
    max_interactions = max(int(max_interactions), 0)
    evaluations: dict[int, dict[str, Any]] = {}
    subset_pool_rows = [
        row
        for row in relevance
        if _finite_float(row.get("coverage_pct")) >= min_coverage_pct
    ][: max(int(feature_subset_pool_size), 0)]

    def evaluate(
        params: dict[str, float],
        *,
        n_features: int,
        n_interactions: int,
        feature_allowlist: set[str] | None = None,
    ) -> dict[str, Any]:
        return _evaluate_policy(
            relevance=relevance,
            pairs=pairs,
            params=params,
            max_features=n_features,
            max_interactions=n_interactions,
            min_coverage_pct=min_coverage_pct,
            feature_clusters=feature_clusters,
            feature_allowlist=feature_allowlist,
        )

    if trials > 0:
        _progress("optuna optimize start")
        optimize_t0 = time.perf_counter()
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=seed),
            storage=storage_url,
            load_if_exists=bool(storage_url),
        )

        def objective(trial: optuna.Trial) -> float:
            params = _suggest_params(trial)
            n_features = trial.suggest_int("max_features", min_features, max_features)
            n_interactions = trial.suggest_int("max_interactions", 0, max_interactions)
            feature_allowlist = None
            if subset_pool_rows:
                chosen_rows = [
                    row
                    for idx, row in enumerate(subset_pool_rows)
                    if trial.suggest_categorical(f"use_feature_{idx:02d}", [False, True])
                ]
                if len(chosen_rows) < n_features:
                    chosen_names = {str(row["feature_name"]) for row in chosen_rows}
                    fill_rows = [
                        row
                        for row in sorted(
                            subset_pool_rows,
                            key=lambda item: (-_feature_score(item, params), str(item["feature_name"])),
                        )
                        if str(row["feature_name"]) not in chosen_names
                    ]
                    chosen_rows.extend(fill_rows[: n_features - len(chosen_rows)])
                feature_allowlist = {str(row["feature_name"]) for row in chosen_rows}
            outcome = evaluate(
                params,
                n_features=n_features,
                n_interactions=n_interactions,
                feature_allowlist=feature_allowlist,
            )
            if feature_allowlist is not None:
                outcome["metrics"]["feature_subset_pool_size"] = len(subset_pool_rows)
                outcome["metrics"]["feature_allowlist_count"] = len(feature_allowlist)
            evaluations[trial.number] = {"params": params, **outcome}
            _store_trial_outcome(trial, outcome)
            return float(outcome["objective"])

        study.optimize(objective, n_trials=int(trials))
        stage_timings["optuna_optimize_s"] = round(time.perf_counter() - optimize_t0, 3)
        _progress(f"optuna optimize done trials={len(study.trials)}")
        completed = [
            trial
            for trial in study.trials
            if trial.state == optuna.trial.TrialState.COMPLETE and trial.number in evaluations
        ]
        if not completed:
            raise RuntimeError("Optuna produced no completed synergy trials")
        best_trial = max(completed, key=_trial_value)
        best = evaluations[best_trial.number]
        study_total_trials = len(study.trials)
    else:
        _progress("deterministic baseline start")
        baseline_t0 = time.perf_counter()
        params = _deterministic_params()
        best = evaluate(params, n_features=max_features, n_interactions=max_interactions)
        best["params"] = params
        best_trial = type("FrozenBaseline", (), {"number": 0, "value": best["objective"], "params": params})()
        completed = [best_trial]
        evaluations[0] = best
        study_total_trials = 1
        stage_timings["deterministic_baseline_s"] = round(time.perf_counter() - baseline_t0, 3)
        _progress("deterministic baseline done")

    risk_evaluated_trials = 0
    risk_rank_cache: dict[str, Any] | None = None
    if risk_aware:
        risk_t0 = time.perf_counter()
        risk_rank_cache = _empty_risk_rank_cache()
        candidates_for_risk = sorted(completed, key=_trial_value, reverse=True)[
            : max(int(risk_eval_top_trials), 1)
        ]
        preferred_rank_features = sorted(
            {
                str(feature)
                for trial in candidates_for_risk
                for feature in evaluations[int(trial.number)]["selected_features"]
            }
        )
        risk_rank_cache["preferred_features"] = preferred_rank_features
        _progress(
            f"risk rerank start candidates={len(candidates_for_risk)} "
            f"top_quantile={risk_top_quantile} folds={risk_folds} "
            f"preferred_rank_features={len(preferred_rank_features)}"
        )
        risk_ranked: list[tuple[float, Any, dict[str, Any]]] = []
        for idx, trial in enumerate(candidates_for_risk, start=1):
            outcome = evaluations[int(trial.number)]
            proxy_objective = _finite_float(getattr(trial, "value", outcome["objective"]))
            candidate_t0 = time.perf_counter()
            _progress(
                f"risk rerank candidate_start {idx}/{len(candidates_for_risk)} "
                f"trial={int(trial.number)} proxy_objective={proxy_objective:.6f}"
            )
            risk_metrics = _risk_evaluate_selection(
                conn,
                source_run_id=source_run_id,
                label_name=label_name,
                selected_features=outcome["selected_features"],
                selected_interactions=outcome["selected_interactions"],
                top_quantile=risk_top_quantile,
                folds=risk_folds,
                transaction_cost_bps=risk_transaction_cost_bps,
                conditional_threshold=risk_conditional_threshold,
                proxy_objective=proxy_objective,
                proxy_weight=risk_proxy_weight,
                rank_ic_weight=risk_rank_ic_weight,
                return_weight=risk_return_weight,
                drawdown_penalty_weight=risk_drawdown_penalty_weight,
                turnover_penalty_weight=risk_turnover_penalty_weight,
                rank_cache=risk_rank_cache,
            )
            outcome["metrics"]["risk_evaluation"] = risk_metrics
            risk_score = _finite_float(risk_metrics.get("risk_objective"), proxy_objective)
            risk_ranked.append((risk_score, trial, outcome))
            risk_evaluated_trials += 1
            _progress(
                f"risk rerank candidate_done {idx}/{len(candidates_for_risk)} "
                f"trial={int(trial.number)} risk_objective={risk_score:.6f} "
                f"worst_drawdown={_finite_float(risk_metrics.get('worst_max_drawdown')):.6f} "
                f"cost_adjusted_excess={_finite_float(risk_metrics.get('avg_cost_adjusted_top_excess_return')):.6f} "
                f"elapsed={time.perf_counter() - candidate_t0:.3f}s"
            )
        best_risk_score, best_trial, best = max(risk_ranked, key=lambda item: item[0])
        best["metrics"]["proxy_objective"] = _finite_float(
            getattr(best_trial, "value", best["objective"])
        )
        best["objective"] = best_risk_score
        _progress(
            f"risk rerank done evaluated={risk_evaluated_trials} "
            f"best_trial={int(best_trial.number)} risk_objective={best_risk_score:.6f} "
            f"rank_cache={_risk_rank_cache_stats(risk_rank_cache)}"
        )
        stage_timings["risk_rerank_s"] = round(time.perf_counter() - risk_t0, 3)

    trial_rows = []
    for trial in completed:
        outcome = evaluations[int(trial.number)]
        params = dict(getattr(trial, "params", {}) or outcome["params"])
        if "max_features" not in params:
            params["max_features"] = len(outcome["selected_features"])
        if "max_interactions" not in params:
            params["max_interactions"] = len(outcome["selected_interactions"])
        trial_rows.append(
            (
                run_id,
                source_run_id,
                label_name,
                int(trial.number),
                _finite_float(getattr(trial, "value", outcome["objective"])),
                len(outcome["selected_features"]),
                len(outcome["selected_interactions"]),
                _safe_json(outcome["selected_features"]),
                _safe_json(outcome["selected_interactions"]),
                _safe_json(params),
                _safe_json(outcome["metrics"]),
                built_at,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_optuna_synergy_trial (
            run_id, source_run_id, label_name, trial_number, objective_value,
            selected_count, selected_interaction_count, selected_features_json,
            selected_interactions_json, params_json, metrics_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        trial_rows,
    )
    selected_features_json = _safe_json(best["selected_features"])
    selected_interactions_json = _safe_json(best["selected_interactions"])
    objective_score = _finite_float(best.get("objective"))
    config = {
        "source_run_id": source_run_id,
        "label_name": label_name,
        "trials_requested": int(trials),
        "min_features": min_features,
        "max_features": max_features,
        "max_interactions": max_interactions,
        "min_coverage_pct": min_coverage_pct,
        "seed": seed,
        "study_name": study_name,
        "storage_url": storage_url,
        "best_params": best["params"],
        "best_metrics": best["metrics"],
        "feature_subset_pool_size": int(feature_subset_pool_size),
        "risk_aware": bool(risk_aware),
        "risk_eval_top_trials": int(risk_eval_top_trials),
        "risk_evaluated_trials": int(risk_evaluated_trials),
        "risk_top_quantile": float(risk_top_quantile),
        "risk_folds": int(risk_folds),
        "risk_transaction_cost_bps": float(risk_transaction_cost_bps),
        "risk_conditional_threshold": float(risk_conditional_threshold),
        "risk_feature_rank_cache": _risk_rank_cache_stats(risk_rank_cache),
        "feature_cluster_count": len(set(feature_clusters.values())) if feature_clusters else 0,
        "pair_interaction_rows": len(pair_rows),
        "conditional_interaction_rows": len(conditional_rows),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_optuna_synergy_study_summary (
            run_id, source_run_id, label_name, best_trial_number,
            objective_score, trials, study_total_trials, selected_features_json,
            selected_interactions_json, config_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_run_id,
            label_name,
            int(best_trial.number),
            objective_score,
            int(trials),
            study_total_trials,
            selected_features_json,
            selected_interactions_json,
            _safe_json(config),
            built_at,
        ),
    )
    notes = {
        "research_only": True,
        "promotion_gate_required": True,
        "reason": "proxy synergy search; requires walk-forward model validation before production use",
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_candidate (
            run_id, source_run_id, label_name, objective_score,
            selected_features_json, selected_interactions_json,
            gate_status, notes_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_run_id,
            label_name,
            objective_score,
            selected_features_json,
            selected_interactions_json,
            "research_only",
            _safe_json(notes),
            built_at,
        ),
    )
    for table in (
        "mart_optuna_synergy_trial",
        "mart_optuna_synergy_study_summary",
        "mart_synergy_policy_candidate",
    ):
        record_actual_version(conn, table)
    duration_s = time.perf_counter() - t0
    stage_timings["total_s"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="run_optuna_synergy_search",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            "mart_feature_temporal_relevance",
            "mart_feature_pair_synergy",
        ],
        output_tables=[
            "mart_optuna_synergy_trial",
            "mart_optuna_synergy_study_summary",
            "mart_synergy_policy_candidate",
        ],
        label_name=label_name,
        feature_group="temporal_synergy_research",
        perf_summary={
            "source_run_id": source_run_id,
            "label_name": label_name,
            "trials": int(trials),
            "study_total_trials": study_total_trials,
            "selected_count": len(best["selected_features"]),
            "selected_interaction_count": len(best["selected_interactions"]),
            "objective_score": objective_score,
            "risk_aware": bool(risk_aware),
            "risk_evaluated_trials": int(risk_evaluated_trials),
            "risk_feature_rank_cache": _risk_rank_cache_stats(risk_rank_cache),
            "best_trial_number": int(best_trial.number),
            "feature_cluster_count": len(set(feature_clusters.values())) if feature_clusters else 0,
            "pair_interaction_rows": len(pair_rows),
            "conditional_interaction_rows": len(conditional_rows),
            "storage_url": storage_url,
            "study_name": study_name,
            "feature_subset_pool_size": int(feature_subset_pool_size),
            "stage_timings": stage_timings,
            "duration_s": duration_s,
        },
    )
    conn.commit()
    _progress(
        f"done run_id={run_id} objective={objective_score} "
        f"selected_features={len(best['selected_features'])} "
        f"selected_interactions={len(best['selected_interactions'])} duration_s={duration_s:.3f}"
    )
    return {
        "run_id": run_id,
        "source_run_id": source_run_id,
        "label_name": label_name,
        "trials": int(trials),
        "study_total_trials": study_total_trials,
        "best_trial_number": int(best_trial.number),
        "objective_score": objective_score,
        "selected_count": len(best["selected_features"]),
        "selected_interaction_count": len(best["selected_interactions"]),
        "selected_features": best["selected_features"],
        "selected_interactions": best["selected_interactions"],
        "risk_aware": bool(risk_aware),
        "risk_evaluated_trials": int(risk_evaluated_trials),
        "risk_feature_rank_cache": _risk_rank_cache_stats(risk_rank_cache),
        "duration_s": duration_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", default=None)
    parser.add_argument("--label", default="forward_ret_20d")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--min-features", type=int, default=3)
    parser.add_argument("--max-features", type=int, default=12)
    parser.add_argument("--max-interactions", type=int, default=8)
    parser.add_argument("--min-coverage-pct", type=float, default=80.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--storage", default=None)
    parser.add_argument("--study-name", default=None)
    parser.add_argument("--no-persistent-study", action="store_true")
    parser.add_argument("--feature-subset-pool-size", type=int, default=0)
    parser.add_argument("--risk-aware", action="store_true")
    parser.add_argument("--risk-eval-top-trials", type=int, default=20)
    parser.add_argument("--risk-top-quantile", type=float, default=0.20)
    parser.add_argument("--risk-folds", type=int, default=4)
    parser.add_argument("--risk-transaction-cost-bps", type=float, default=20.0)
    parser.add_argument("--risk-conditional-threshold", type=float, default=0.80)
    parser.add_argument("--risk-proxy-weight", type=float, default=0.05)
    parser.add_argument("--risk-rank-ic-weight", type=float, default=10.0)
    parser.add_argument("--risk-return-weight", type=float, default=100.0)
    parser.add_argument("--risk-drawdown-penalty-weight", type=float, default=3.0)
    parser.add_argument("--risk-turnover-penalty-weight", type=float, default=1.0)
    args = parser.parse_args()
    storage_url = None if args.no_persistent_study else (args.storage or default_optuna_storage_url())
    with get_conn() as conn:
        result = run_optuna_synergy_search(
            conn,
            source_run_id=args.source_run_id,
            label_name=args.label,
            run_id=args.run_id,
            trials=args.trials,
            min_features=args.min_features,
            max_features=args.max_features,
            max_interactions=args.max_interactions,
            min_coverage_pct=args.min_coverage_pct,
            seed=args.seed,
            storage_url=storage_url,
            study_name=args.study_name,
            feature_subset_pool_size=args.feature_subset_pool_size,
            risk_aware=args.risk_aware,
            risk_eval_top_trials=args.risk_eval_top_trials,
            risk_top_quantile=args.risk_top_quantile,
            risk_folds=args.risk_folds,
            risk_transaction_cost_bps=args.risk_transaction_cost_bps,
            risk_conditional_threshold=args.risk_conditional_threshold,
            risk_proxy_weight=args.risk_proxy_weight,
            risk_rank_ic_weight=args.risk_rank_ic_weight,
            risk_return_weight=args.risk_return_weight,
            risk_drawdown_penalty_weight=args.risk_drawdown_penalty_weight,
            risk_turnover_penalty_weight=args.risk_turnover_penalty_weight,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
