"""PIT-strict stock-level market context aggregation for Market Perception P7."""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from .regime_engine import _fetchall, _table_exists, _to_date

logger = logging.getLogger("cm-api.market-perception.stock-context")


def compute_stock_context_for_date(conn, snapshot_date: str | date | datetime, limit: int = 100) -> dict[str, Any]:
    day = _to_date(snapshot_date)
    frame = compute_stock_context_for_range(conn, day, day, limit=limit)
    return {
        "snapshot_date": day.isoformat(),
        "rows": int(len(frame)),
        "stocks": frame.to_dict("records"),
    }


def compute_stock_context_for_range(
    conn,
    start: str | date,
    end: str | date,
    *,
    limit: int = 100,
) -> pd.DataFrame:
    start_day = _to_date(start)
    end_day = _to_date(end)
    if start_day > end_day:
        raise ValueError(f"start {start_day} > end {end_day}")
    days = _trading_days(conn, start_day, end_day)
    if any(d >= date.today() for d in days):
        raise ValueError(f"range {start_day} -> {end_day} includes today/future; PIT requires snapshot_date < today")
    if not days:
        return pd.DataFrame()
    if not _table_exists(conn, "mart_market_perception_under_reaction_daily"):
        raise ValueError("mart_market_perception_under_reaction_daily is required for StockContextEngine MVP")

    seeds = _load_under_reaction(conn, start_day, end_day, max(1, int(limit)))
    if seeds.empty:
        return pd.DataFrame()
    frame = seeds
    for loader, keys in [
        (_load_market_regime, ["snapshot_date"]),
        (_load_emotion, ["snapshot_date"]),
        (_load_style, ["snapshot_date"]),
        (_load_leader_follow, ["snapshot_date", "stock_code"]),
    ]:
        add = loader(conn, start_day, end_day)
        if not add.empty:
            frame = frame.merge(add, on=keys, how="left")
    frame = _score(frame)
    return frame.sort_values(["snapshot_date", "context_score"], ascending=[True, False]).reset_index(drop=True)


def _load_under_reaction(conn, start_day: date, end_day: date, limit: int) -> pd.DataFrame:
    rows = _fetchall(
        conn,
        """
        WITH ranked AS (
            SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
                   stock_code, under_reaction_score, fund_anomaly_score,
                   theme_name, theme_score, lifecycle_stage,
                   ROW_NUMBER() OVER (PARTITION BY snapshot_date ORDER BY under_reaction_score DESC) AS rn
              FROM mart_market_perception_under_reaction_daily
              JOIN dim_trading_calendar cal
                ON CAST(cal.trade_date AS DATE) = snapshot_date
               AND cal.is_trading = 1
             WHERE snapshot_date BETWEEN ? AND ?
        )
        SELECT snapshot_date, stock_code, under_reaction_score, fund_anomaly_score,
               theme_name, theme_score, lifecycle_stage
          FROM ranked
         WHERE rn <= ?
        """,
        [start_day.isoformat(), end_day.isoformat(), limit],
    )
    return pd.DataFrame(rows)


def _load_market_regime(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "mart_market_perception_daily"):
        return pd.DataFrame()
    rows = _fetchall(
        conn,
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
               regime_score AS market_regime_score
          FROM mart_market_perception_daily
         WHERE snapshot_date BETWEEN ? AND ?
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_emotion(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "mart_market_perception_emotion_daily"):
        return pd.DataFrame()
    rows = _fetchall(
        conn,
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
               emotion_score, emotion_state
          FROM mart_market_perception_emotion_daily
         WHERE snapshot_date BETWEEN ? AND ?
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_style(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "mart_market_perception_style_daily"):
        return pd.DataFrame()
    rows = _fetchall(
        conn,
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
               style_rotation_score, style_bias, crowding_risk_score, overheat_reversal_risk
          FROM mart_market_perception_style_daily
         WHERE snapshot_date BETWEEN ? AND ?
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_leader_follow(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "mart_market_perception_leader_follower_daily"):
        return pd.DataFrame()
    rows = _fetchall(
        conn,
        """
        WITH ranked AS (
            SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
                   follower_stock_code AS stock_code,
                   leader_stock_code,
                   diffusion_score AS leader_follow_score,
                   diffusion_score AS chain_diffusion_score,
                   ROW_NUMBER() OVER (
                       PARTITION BY snapshot_date, follower_stock_code
                       ORDER BY diffusion_score DESC
                   ) AS rn
              FROM mart_market_perception_leader_follower_daily
             WHERE snapshot_date BETWEEN ? AND ?
        )
        SELECT snapshot_date, stock_code, leader_stock_code, leader_follow_score, chain_diffusion_score
          FROM ranked
         WHERE rn = 1
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _score(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    existing_cols = set(out.columns)
    for col in ["emotion_state", "leader_stock_code", "style_bias"]:
        if col not in existing_cols:
            out[col] = None
    numeric_cols = [
        "under_reaction_score", "fund_anomaly_score", "theme_score",
        "market_regime_score", "emotion_score", "leader_follow_score",
        "chain_diffusion_score", "style_rotation_score", "crowding_risk_score",
        "overheat_reversal_risk",
    ]
    for col in numeric_cols:
        if col not in existing_cols:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    required = [
        "under_reaction_score", "theme_score", "market_regime_score", "emotion_score",
        "leader_follow_score", "style_rotation_score", "crowding_risk_score",
    ]
    missing_lists = []
    completeness = []
    for _, row in out.iterrows():
        missing = [col for col in required if pd.isna(row[col])]
        missing_lists.append(json.dumps(missing, ensure_ascii=False))
        completeness.append(round((len(required) - len(missing)) / len(required), 6))
    out["missing_context_fields"] = missing_lists
    out["data_completeness_score"] = completeness
    filled = out[numeric_cols].fillna(0.0)
    raw = (
        filled["under_reaction_score"] * 0.30
        + filled["theme_score"] * 0.15
        + filled["market_regime_score"] * 0.10
        + filled["emotion_score"] * 0.10
        + filled["leader_follow_score"] * 0.15
        + filled["style_rotation_score"] * 0.10
        - filled["crowding_risk_score"].clip(lower=0.0) * 0.07
        - filled["overheat_reversal_risk"].clip(lower=0.0) * 0.03
    )
    out["context_score"] = raw.clip(-0.9, 0.9).round(6)
    out["context_state"] = out["context_score"].apply(_state)
    out["source_engines"] = out.apply(_source_engines, axis=1)
    out["pit_cutoff_date"] = out["snapshot_date"]
    for _, row in out.iterrows():
        _guard_row(row)
    return out[
        [
            "snapshot_date", "stock_code", "context_score", "context_state",
            "market_regime_score", "emotion_score", "emotion_state",
            "theme_name", "theme_score", "lifecycle_stage",
            "under_reaction_score", "fund_anomaly_score",
            "leader_follow_score", "leader_stock_code", "chain_diffusion_score",
            "style_rotation_score", "style_bias", "crowding_risk_score",
            "overheat_reversal_risk", "data_completeness_score",
            "missing_context_fields", "pit_cutoff_date", "source_engines",
        ]
    ]


def _state(score: float) -> str:
    if score >= 0.35:
        return "context_supportive"
    if score <= -0.25:
        return "context_hostile"
    return "context_mixed"


def _source_engines(row: pd.Series) -> str:
    engines = []
    mapping = [
        ("UnderReactionAlpha", "under_reaction_score"),
        ("ThemeLifecycleEngine", "theme_score"),
        ("MarketRegimeEngine", "market_regime_score"),
        ("MarketEmotionCycle", "emotion_score"),
        ("LeaderFollowerEngine", "leader_follow_score"),
        ("StyleRotationEngine", "style_rotation_score"),
        ("CrowdingRiskEngine", "crowding_risk_score"),
    ]
    for engine, col in mapping:
        value = row.get(col)
        if pd.notna(value):
            engines.append({"engine": engine, "score": float(value), "weight": 1.0})
    return json.dumps(engines, ensure_ascii=False)


def _trading_days(conn, start_day: date, end_day: date) -> list[date]:
    rows = _fetchall(
        conn,
        """
        SELECT CAST(trade_date AS VARCHAR) AS trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) BETWEEN ? AND ?
         ORDER BY CAST(trade_date AS DATE)
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return [_to_date(row["trade_date"]) for row in rows]


def _guard_row(row: pd.Series) -> None:
    score = float(row["context_score"])
    if not math.isfinite(score) or abs(score) > 0.95:
        raise ValueError(f"context_score guard failed for {row['snapshot_date']} {row['stock_code']}: {score}")
