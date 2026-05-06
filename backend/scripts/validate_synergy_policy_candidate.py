#!/usr/bin/env python3
"""Walk-forward validation for research-only synergy policy candidates."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.model_feature_schema import holding_period_from_label  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.pricing_policy import load_pricing_label_policy, record_pricing_label_policy  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DDL = """
CREATE TABLE IF NOT EXISTS mart_synergy_policy_walkforward (
    run_id TEXT NOT NULL,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    fold_id INTEGER NOT NULL,
    start_date TEXT,
    end_date TEXT,
    date_count INTEGER,
    obs_count INTEGER,
    stock_count INTEGER,
    rank_ic DOUBLE,
    top_quantile DOUBLE,
    top_obs_count INTEGER,
    top_avg_return DOUBLE,
    universe_avg_return DOUBLE,
    top_excess_return DOUBLE,
    top_hit_rate DOUBLE,
    avg_turnover DOUBLE,
    avg_cost_adjusted_top_return DOUBLE,
    avg_cost_adjusted_top_excess_return DOUBLE,
    transaction_cost_bps DOUBLE,
    compounded_top_return DOUBLE,
    max_drawdown DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, fold_id)
);
ALTER TABLE mart_synergy_policy_walkforward ADD COLUMN IF NOT EXISTS avg_turnover DOUBLE;
ALTER TABLE mart_synergy_policy_walkforward ADD COLUMN IF NOT EXISTS avg_cost_adjusted_top_return DOUBLE;
ALTER TABLE mart_synergy_policy_walkforward ADD COLUMN IF NOT EXISTS avg_cost_adjusted_top_excess_return DOUBLE;
ALTER TABLE mart_synergy_policy_walkforward ADD COLUMN IF NOT EXISTS transaction_cost_bps DOUBLE;

CREATE TABLE IF NOT EXISTS mart_synergy_policy_gate (
    run_id TEXT PRIMARY KEY,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    baseline_horizon_days INTEGER,
    candidate_horizon_days INTEGER,
    validation_status TEXT NOT NULL,
    promotion_status TEXT NOT NULL,
    production_eligible BOOLEAN NOT NULL,
    fold_count INTEGER,
    avg_rank_ic DOUBLE,
    std_rank_ic DOUBLE,
    min_rank_ic DOUBLE,
    avg_top_excess_return DOUBLE,
    worst_top_excess_return DOUBLE,
    avg_top_hit_rate DOUBLE,
    worst_max_drawdown DOUBLE,
    avg_turnover DOUBLE,
    avg_cost_adjusted_top_excess_return DOUBLE,
    worst_cost_adjusted_top_excess_return DOUBLE,
    transaction_cost_bps DOUBLE,
    blockers_json TEXT,
    thresholds_json TEXT,
    built_at TEXT NOT NULL
);
ALTER TABLE mart_synergy_policy_gate ADD COLUMN IF NOT EXISTS avg_turnover DOUBLE;
ALTER TABLE mart_synergy_policy_gate ADD COLUMN IF NOT EXISTS avg_cost_adjusted_top_excess_return DOUBLE;
ALTER TABLE mart_synergy_policy_gate ADD COLUMN IF NOT EXISTS worst_cost_adjusted_top_excess_return DOUBLE;
ALTER TABLE mart_synergy_policy_gate ADD COLUMN IF NOT EXISTS transaction_cost_bps DOUBLE;

CREATE TABLE IF NOT EXISTS mart_synergy_policy_evidence_bundle (
    run_id TEXT PRIMARY KEY,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    feature_directions_json TEXT,
    fold_metrics_json TEXT,
    gate_json TEXT,
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


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_relation(name: str) -> str:
    return ".".join(_quote_ident(part) for part in name.split("."))


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


def _safe_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _finite(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _latest_candidate_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_synergy_policy_candidate"):
        return None
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_synergy_policy_candidate
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def _load_candidate(conn: Any, candidate_run_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT run_id, source_run_id, label_name,
               selected_features_json, selected_interactions_json, gate_status
          FROM mart_synergy_policy_candidate
         WHERE run_id = ?
         LIMIT 1
        """,
        (candidate_run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"synergy policy candidate not found: {candidate_run_id}")
    return {
        "run_id": row["run_id"],
        "source_run_id": row["source_run_id"],
        "label_name": row["label_name"],
        "selected_features": _safe_json(row["selected_features_json"], []),
        "selected_interactions": _safe_json(row["selected_interactions_json"], []),
        "gate_status": row["gate_status"],
    }


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
    by_feature = {str(row["feature_name"]): _finite(row["rank_ic"]) for row in rows}
    return {feature: (-1 if by_feature.get(feature, 0.0) < 0 else 1) for feature in features}


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
        equity *= 1.0 + _finite(value)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1.0)
    return equity - 1.0, max_dd


def _dailyize_holding_period_return(value: float, holding_period_days: int | None) -> float:
    horizon = max(int(holding_period_days or 1), 1)
    clean = _finite(value)
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


def validate_synergy_policy_candidate(
    conn: Any,
    *,
    candidate_run_id: str | None = None,
    run_id: str | None = None,
    folds: int = 5,
    top_quantile: float = 0.10,
    baseline_horizon_days: int = 60,
    min_fold_count: int = 4,
    min_avg_rank_ic: float = 0.01,
    max_std_rank_ic: float = 0.05,
    min_avg_top_excess_return: float = 0.0,
    min_avg_cost_adjusted_top_excess_return: float = 0.0,
    max_worst_drawdown: float = 0.25,
    min_top_obs_count: int = 50,
    transaction_cost_bps: float | None = None,
    conditional_threshold: float = 0.80,
) -> dict[str, Any]:
    ensure_tables(conn)
    pricing_policy = load_pricing_label_policy()
    if transaction_cost_bps is None:
        transaction_cost_bps = pricing_policy.transaction_cost_bps
    record_pricing_label_policy(conn, pricing_policy)
    candidate_run_id = candidate_run_id or _latest_candidate_run_id(conn)
    if not candidate_run_id:
        raise RuntimeError("candidate_run_id is required and no synergy policy candidate exists")
    candidate = _load_candidate(conn, candidate_run_id)
    source_run_id = str(candidate["source_run_id"])
    label_name = str(candidate["label_name"])
    run_id = run_id or f"synergy_policy_wf_{candidate_run_id}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    started_at = utc_now_iso()

    selected_features = [str(feature) for feature in candidate["selected_features"]]
    selected_interactions = [
        {
            "interaction_type": str(row.get("interaction_type") or "pair"),
            "feature_a": str(row.get("feature_a")),
            "feature_b": str(row.get("feature_b")),
        }
        for row in candidate["selected_interactions"]
        if row.get("feature_a") and row.get("feature_b")
    ]
    panel_table = "mart_temporal_research_panel"
    if not _table_exists(conn, panel_table):
        raise RuntimeError("mart_temporal_research_panel is required for synergy policy validation")
    panel_cols = _table_columns(conn, panel_table)
    date_column = "date" if "date" in panel_cols else "signal_date" if "signal_date" in panel_cols else None
    required = {"run_id", "stock_code", label_name}
    missing_required = sorted(required - panel_cols)
    if not date_column:
        missing_required.append("date_or_signal_date")
    if missing_required:
        raise RuntimeError(f"temporal research panel missing required columns: {missing_required}")
    usable_features = [feature for feature in selected_features if feature in panel_cols]
    missing_features = sorted(set(selected_features) - set(usable_features))
    if not usable_features:
        raise RuntimeError("candidate has no usable selected features in mart_temporal_research_panel")

    feature_directions = _load_feature_directions(
        conn,
        source_run_id=source_run_id,
        label_name=label_name,
        features=usable_features,
    )
    conn.execute("DELETE FROM mart_synergy_policy_walkforward WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_synergy_policy_gate WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_synergy_policy_evidence_bundle WHERE run_id = ?", (run_id,))

    fold_count = max(int(folds), 1)
    candidate_horizon_days = holding_period_from_label(label_name)
    rank_exprs = []
    for feature in usable_features:
        direction = feature_directions.get(feature, 1)
        order = "ASC" if direction >= 0 else "DESC"
        alias = f"__rank_{feature}"
        rank_exprs.append(
            f"PERCENT_RANK() OVER (PARTITION BY date ORDER BY {_quote_ident(feature)} {order} NULLS FIRST) AS {_quote_ident(alias)}"
        )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE synergy_policy_feature_rank AS
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

    score_terms = [f"{_quote_ident(f'__rank_{feature}')}" for feature in usable_features]
    interaction_terms = []
    for interaction in selected_interactions:
        a = interaction["feature_a"]
        b = interaction["feature_b"]
        interaction_type = str(interaction.get("interaction_type") or "pair").lower()
        if a in usable_features and b in usable_features:
            rank_a = _quote_ident(f"__rank_{a}")
            rank_b = _quote_ident(f"__rank_{b}")
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
        CREATE OR REPLACE TEMP TABLE synergy_policy_scored AS
        SELECT stock_code,
               date,
               label_value,
               ({' + '.join(all_terms)}) / {float(len(all_terms))} AS policy_score
          FROM synergy_policy_feature_rank
        """
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE synergy_policy_dates AS
        SELECT date,
               NTILE({fold_count}) OVER (ORDER BY CAST(date AS DATE)) AS fold_id
          FROM (SELECT DISTINCT date FROM synergy_policy_scored)
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE synergy_policy_ranked AS
        SELECT s.stock_code,
               s.date,
               d.fold_id,
               s.label_value,
               s.policy_score,
               PERCENT_RANK() OVER (PARTITION BY s.date ORDER BY s.label_value NULLS FIRST) AS label_rank,
               PERCENT_RANK() OVER (PARTITION BY s.date ORDER BY s.policy_score NULLS FIRST) AS score_rank
          FROM synergy_policy_scored s
          JOIN synergy_policy_dates d USING (date)
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
          FROM synergy_policy_ranked
         GROUP BY fold_id, date
         ORDER BY fold_id, CAST(date AS DATE)
        """,
        (threshold, threshold, threshold),
    ).fetchall()
    by_fold: dict[int, list[dict[str, Any]]] = {}
    for row in daily_rows:
        by_fold.setdefault(int(row["fold_id"]), []).append(dict(row))
    holding_rows = conn.execute(
        """
        SELECT fold_id, date, stock_code
          FROM synergy_policy_ranked
         WHERE score_rank >= ?
         ORDER BY fold_id, CAST(date AS DATE), stock_code
        """,
        (threshold,),
    ).fetchall()
    holdings_by_fold_date: dict[tuple[int, str], set[str]] = {}
    for row in holding_rows:
        key = (int(row["fold_id"]), str(row["date"]))
        holdings_by_fold_date.setdefault(key, set()).add(str(row["stock_code"]))

    fold_rows = []
    fold_metrics = []
    transaction_cost_rate = max(float(transaction_cost_bps), 0.0) / 10000.0
    for fold_id in sorted(by_fold):
        rows = by_fold[fold_id]
        rank_ics = [_finite(row["rank_ic"]) for row in rows if row["rank_ic"] is not None]
        top_returns = [_finite(row["top_avg_return"]) for row in rows if row["top_avg_return"] is not None]
        universe_returns = [_finite(row["universe_avg_return"]) for row in rows if row["universe_avg_return"] is not None]
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
                adjusted_top = _finite(row["top_avg_return"]) - turnover * transaction_cost_rate
                adjusted_top_returns.append(adjusted_top)
                if row["universe_avg_return"] is not None:
                    adjusted_top_excess.append(adjusted_top - _finite(row["universe_avg_return"]))
        top_excess = [
            _finite(row["top_avg_return"]) - _finite(row["universe_avg_return"])
            for row in rows
            if row["top_avg_return"] is not None and row["universe_avg_return"] is not None
        ]
        drawdown_source_returns = adjusted_top_returns or top_returns
        drawdown_returns = [
            _dailyize_holding_period_return(value, candidate_horizon_days)
            for value in drawdown_source_returns
        ]
        compounded_return, max_drawdown = _max_drawdown(drawdown_returns)
        metric = {
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
            "top_hit_rate": sum(_finite(row["top_hit_rate"]) for row in rows if row["top_hit_rate"] is not None)
            / max(sum(1 for row in rows if row["top_hit_rate"] is not None), 1),
            "avg_turnover": sum(turnovers) / len(turnovers) if turnovers else 0.0,
            "avg_cost_adjusted_top_return": sum(adjusted_top_returns) / len(adjusted_top_returns)
            if adjusted_top_returns
            else 0.0,
            "avg_cost_adjusted_top_excess_return": sum(adjusted_top_excess) / len(adjusted_top_excess)
            if adjusted_top_excess
            else 0.0,
            "transaction_cost_bps": float(transaction_cost_bps),
            "compounded_top_return": compounded_return,
            "max_drawdown": max_drawdown,
        }
        fold_metrics.append(metric)
        fold_rows.append(
            (
                run_id,
                candidate_run_id,
                source_run_id,
                label_name,
                metric["fold_id"],
                metric["start_date"],
                metric["end_date"],
                metric["date_count"],
                metric["obs_count"],
                metric["stock_count"],
                metric["rank_ic"],
                metric["top_quantile"],
                metric["top_obs_count"],
                metric["top_avg_return"],
                metric["universe_avg_return"],
                metric["top_excess_return"],
                metric["top_hit_rate"],
                metric["avg_turnover"],
                metric["avg_cost_adjusted_top_return"],
                metric["avg_cost_adjusted_top_excess_return"],
                metric["transaction_cost_bps"],
                metric["compounded_top_return"],
                metric["max_drawdown"],
                built_at,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_walkforward (
            run_id, candidate_run_id, source_run_id, label_name, fold_id,
            start_date, end_date, date_count, obs_count, stock_count,
            rank_ic, top_quantile, top_obs_count, top_avg_return,
            universe_avg_return, top_excess_return, top_hit_rate,
            avg_turnover, avg_cost_adjusted_top_return,
            avg_cost_adjusted_top_excess_return, transaction_cost_bps,
            compounded_top_return, max_drawdown, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        fold_rows,
    )

    rank_ics = [metric["rank_ic"] for metric in fold_metrics]
    top_excess = [metric["top_excess_return"] for metric in fold_metrics]
    cost_adjusted_top_excess = [metric["avg_cost_adjusted_top_excess_return"] for metric in fold_metrics]
    turnovers = [metric["avg_turnover"] for metric in fold_metrics]
    drawdowns = [metric["max_drawdown"] for metric in fold_metrics]
    avg_rank_ic = sum(rank_ics) / len(rank_ics) if rank_ics else 0.0
    std_rank_ic = _stddev(rank_ics)
    avg_top_excess = sum(top_excess) / len(top_excess) if top_excess else 0.0
    avg_cost_adjusted_top_excess = (
        sum(cost_adjusted_top_excess) / len(cost_adjusted_top_excess) if cost_adjusted_top_excess else 0.0
    )
    avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0.0
    blockers = []
    if len(fold_metrics) < int(min_fold_count):
        blockers.append("insufficient_fold_count")
    if avg_rank_ic < float(min_avg_rank_ic):
        blockers.append("low_avg_rank_ic")
    if std_rank_ic > float(max_std_rank_ic):
        blockers.append("high_rank_ic_std")
    if avg_top_excess < float(min_avg_top_excess_return):
        blockers.append("low_top_excess_return")
    if avg_cost_adjusted_top_excess < float(min_avg_cost_adjusted_top_excess_return):
        blockers.append("low_cost_adjusted_top_excess_return")
    if drawdowns and min(drawdowns) < -abs(float(max_worst_drawdown)):
        blockers.append("excessive_topk_drawdown")
    if fold_metrics and min(metric["top_obs_count"] for metric in fold_metrics) < int(min_top_obs_count):
        blockers.append("insufficient_top_obs")
    if missing_features:
        blockers.append("missing_selected_features")
    validation_status = "pass" if not blockers else "blocked"
    production_eligible = bool(validation_status == "pass" and candidate_horizon_days == baseline_horizon_days)
    promotion_status = "production_candidate" if production_eligible else "research_only"
    if validation_status == "pass" and candidate_horizon_days != baseline_horizon_days:
        promotion_status = "research_only"

    thresholds = {
        "baseline_horizon_days": baseline_horizon_days,
        "min_fold_count": min_fold_count,
        "min_avg_rank_ic": min_avg_rank_ic,
        "max_std_rank_ic": max_std_rank_ic,
        "min_avg_top_excess_return": min_avg_top_excess_return,
        "min_avg_cost_adjusted_top_excess_return": min_avg_cost_adjusted_top_excess_return,
        "max_worst_drawdown": max_worst_drawdown,
        "min_top_obs_count": min_top_obs_count,
        "top_quantile": top_quantile,
        "transaction_cost_bps": transaction_cost_bps,
        "conditional_threshold": conditional_threshold,
        "drawdown_return_mode": "cost_adjusted_holding_period_return_dailyized",
    }
    gate = {
        "validation_status": validation_status,
        "promotion_status": promotion_status,
        "production_eligible": production_eligible,
        "blockers": blockers,
        "missing_features": missing_features,
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_gate (
            run_id, candidate_run_id, source_run_id, label_name,
            baseline_horizon_days, candidate_horizon_days, validation_status,
            promotion_status, production_eligible, fold_count, avg_rank_ic,
            std_rank_ic, min_rank_ic, avg_top_excess_return,
            worst_top_excess_return, avg_top_hit_rate, worst_max_drawdown,
            avg_turnover, avg_cost_adjusted_top_excess_return,
            worst_cost_adjusted_top_excess_return, transaction_cost_bps,
            blockers_json, thresholds_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            candidate_run_id,
            source_run_id,
            label_name,
            baseline_horizon_days,
            candidate_horizon_days,
            validation_status,
            promotion_status,
            production_eligible,
            len(fold_metrics),
            avg_rank_ic,
            std_rank_ic,
            min(rank_ics) if rank_ics else 0.0,
            avg_top_excess,
            min(top_excess) if top_excess else 0.0,
            sum(metric["top_hit_rate"] for metric in fold_metrics) / len(fold_metrics) if fold_metrics else 0.0,
            min(drawdowns) if drawdowns else 0.0,
            avg_turnover,
            avg_cost_adjusted_top_excess,
            min(cost_adjusted_top_excess) if cost_adjusted_top_excess else 0.0,
            float(transaction_cost_bps),
            _json(blockers),
            _json(thresholds),
            built_at,
        ),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_evidence_bundle (
            run_id, candidate_run_id, source_run_id, label_name,
            selected_features_json, selected_interactions_json,
            feature_directions_json, fold_metrics_json, gate_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            candidate_run_id,
            source_run_id,
            label_name,
            _json(usable_features),
            _json(selected_interactions),
            _json(feature_directions),
            _json(fold_metrics),
            _json(gate),
            built_at,
        ),
    )
    for table in (
        "mart_pricing_label_policy",
        "mart_synergy_policy_walkforward",
        "mart_synergy_policy_gate",
        "mart_synergy_policy_evidence_bundle",
    ):
        record_actual_version(conn, table)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="validate_synergy_policy_candidate",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            "mart_synergy_policy_candidate",
            "mart_temporal_research_panel",
            "mart_feature_temporal_relevance",
        ],
        output_tables=[
            "mart_synergy_policy_walkforward",
            "mart_synergy_policy_gate",
            "mart_synergy_policy_evidence_bundle",
        ],
        label_name=label_name,
        holding_period=candidate_horizon_days,
        gate_result=validation_status,
        blockers=blockers,
        perf_summary={
            "candidate_run_id": candidate_run_id,
            "source_run_id": source_run_id,
            "fold_count": len(fold_metrics),
            "avg_rank_ic": avg_rank_ic,
            "std_rank_ic": std_rank_ic,
            "avg_top_excess_return": avg_top_excess,
            "avg_cost_adjusted_top_excess_return": avg_cost_adjusted_top_excess,
            "avg_turnover": avg_turnover,
            "transaction_cost_bps": float(transaction_cost_bps),
            "pricing_policy_id": pricing_policy.policy_id,
            "pricing_policy_hash": pricing_policy.policy_hash(),
            "conditional_threshold": float(conditional_threshold),
            "promotion_status": promotion_status,
            "production_eligible": production_eligible,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "candidate_run_id": candidate_run_id,
        "source_run_id": source_run_id,
        "label_name": label_name,
        "fold_count": len(fold_metrics),
        "validation_status": validation_status,
        "promotion_status": promotion_status,
        "production_eligible": production_eligible,
        "avg_rank_ic": avg_rank_ic,
        "std_rank_ic": std_rank_ic,
        "avg_top_excess_return": avg_top_excess,
        "avg_cost_adjusted_top_excess_return": avg_cost_adjusted_top_excess,
        "avg_turnover": avg_turnover,
        "transaction_cost_bps": float(transaction_cost_bps),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-run-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--top-quantile", type=float, default=0.10)
    parser.add_argument("--baseline-horizon-days", type=int, default=60)
    parser.add_argument("--min-fold-count", type=int, default=4)
    parser.add_argument("--min-avg-rank-ic", type=float, default=0.01)
    parser.add_argument("--max-std-rank-ic", type=float, default=0.05)
    parser.add_argument("--min-avg-top-excess-return", type=float, default=0.0)
    parser.add_argument("--min-avg-cost-adjusted-top-excess-return", type=float, default=0.0)
    parser.add_argument("--max-worst-drawdown", type=float, default=0.25)
    parser.add_argument("--min-top-obs-count", type=int, default=50)
    parser.add_argument("--transaction-cost-bps", type=float, default=None)
    parser.add_argument("--conditional-threshold", type=float, default=0.80)
    args = parser.parse_args()
    with get_conn() as conn:
        result = validate_synergy_policy_candidate(
            conn,
            candidate_run_id=args.candidate_run_id,
            run_id=args.run_id,
            folds=args.folds,
            top_quantile=args.top_quantile,
            baseline_horizon_days=args.baseline_horizon_days,
            min_fold_count=args.min_fold_count,
            min_avg_rank_ic=args.min_avg_rank_ic,
            max_std_rank_ic=args.max_std_rank_ic,
            min_avg_top_excess_return=args.min_avg_top_excess_return,
            min_avg_cost_adjusted_top_excess_return=args.min_avg_cost_adjusted_top_excess_return,
            max_worst_drawdown=args.max_worst_drawdown,
            min_top_obs_count=args.min_top_obs_count,
            transaction_cost_bps=args.transaction_cost_bps,
            conditional_threshold=args.conditional_threshold,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
