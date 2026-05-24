"""PIT-strict leader/follower context for Market Perception P5.

This MVP uses only observed PIT industry membership plus close/amount history.
It does not use future leader labels or post-snapshot returns.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .regime_engine import _attach_market_if_available, _fetchall, _table_exists, _to_date

logger = logging.getLogger("cm-api.market-perception.leader-follower")


def compute_leader_follower_for_date(conn, snapshot_date: str | date | datetime, top_n: int = 5) -> dict[str, Any]:
    day = _to_date(snapshot_date)
    frame = compute_leader_follower_for_range(conn, day, day, top_n=top_n)
    return {
        "snapshot_date": day.isoformat(),
        "rows": int(len(frame)),
        "edges": frame.to_dict("records"),
    }


def compute_leader_follower_for_range(
    conn,
    start: str | date,
    end: str | date,
    *,
    top_n: int = 5,
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
    if not _table_exists(conn, "mart_stock_industry_pit"):
        raise ValueError("mart_stock_industry_pit is required for LeaderFollowerEngine")

    _attach_market_if_available(conn)
    features = _load_stock_theme_features(conn, start_day, end_day)
    if features.empty:
        raise ValueError(f"leader/follower inputs missing for {start_day} -> {end_day}")
    theme = _load_theme_context(conn, start_day, end_day)
    if not theme.empty:
        features = features.merge(theme, on=["snapshot_date", "theme_name"], how="left")
    else:
        features["theme_score"] = 0.0
        features["lifecycle_stage"] = None

    scored = _score_stock_roles(features)
    edges = _build_edges(scored, top_n=max(1, int(top_n)))
    if edges.empty:
        return edges
    edges["source_engines"] = edges.apply(
        lambda row: json.dumps(
            [{"engine": "LeaderFollowerEngine", "score": float(row["diffusion_score"]), "weight": 1.0}],
            ensure_ascii=False,
        ),
        axis=1,
    )
    edges["pit_cutoff_date"] = edges["snapshot_date"]
    for _, row in edges.iterrows():
        _guard_row(row)
    return edges.reset_index(drop=True)


def _load_stock_theme_features(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if _table_exists(conn, "fact_stock_kline_daily"):
        price_sql = """
            SELECT stock_code, CAST(trade_date AS VARCHAR) AS snapshot_date, close, amount
              FROM fact_stock_kline_daily
             WHERE CAST(trade_date AS DATE) BETWEEN ? AND ?
        """
    else:
        price_sql = """
            SELECT code AS stock_code, CAST(date AS VARCHAR) AS snapshot_date, close, amount
              FROM market.v_price_kline_qfq
             WHERE freq = 'daily' AND adjust = 'qfq'
               AND CAST(date AS DATE) BETWEEN ? AND ?
               AND regexp_matches(code, '^(00|30|60|68)[0-9]{4}$')
               AND COALESCE(source_name, '') NOT LIKE 'tdxhub_index%'
        """
    query_start = start_day - timedelta(days=70)
    rows = _fetchall(
        conn,
        f"""
        WITH px0 AS ({price_sql}),
        px AS (
            SELECT stock_code, snapshot_date, close, amount,
                   LAG(close, 1) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE)) AS close_1d,
                   LAG(close, 3) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE)) AS close_3d,
                   LAG(close, 5) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE)) AS close_5d,
                   LAG(close, 20) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE)) AS close_20d,
                   AVG(amount) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE) ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS amount_5d,
                   AVG(amount) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE) ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amount_20d
              FROM px0
        )
        SELECT px.stock_code,
               px.snapshot_date,
               ip.tdx_l1_name AS theme_name,
               CASE WHEN px.close_1d > 0 THEN px.close / px.close_1d - 1.0 ELSE NULL END AS ret_1d,
               CASE WHEN px.close_3d > 0 THEN px.close / px.close_3d - 1.0 ELSE NULL END AS ret_3d,
               CASE WHEN px.close_5d > 0 THEN px.close / px.close_5d - 1.0 ELSE NULL END AS ret_5d,
               CASE WHEN px.close_20d > 0 THEN px.close / px.close_20d - 1.0 ELSE NULL END AS ret_20d,
               CASE WHEN px.amount_20d > 0 THEN px.amount_5d / px.amount_20d ELSE NULL END AS amount_ratio_5_20,
               ip.confidence_level AS pit_member_confidence
          FROM px
          JOIN dim_trading_calendar cal
            ON CAST(cal.trade_date AS DATE) = CAST(px.snapshot_date AS DATE)
           AND cal.is_trading = 1
          JOIN mart_stock_industry_pit ip
            ON ip.stock_code = px.stock_code
           AND ip.confidence_level = 'observed_snapshot'
           AND CAST(px.snapshot_date AS DATE) BETWEEN CAST(ip.effective_from AS DATE) AND CAST(ip.effective_to AS DATE)
         WHERE CAST(px.snapshot_date AS DATE) BETWEEN ? AND ?
           AND px.close_5d IS NOT NULL
           AND px.close_20d IS NOT NULL
        """,
        [query_start.isoformat(), end_day.isoformat(), start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_theme_context(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "mart_market_perception_theme_daily"):
        return pd.DataFrame()
    rows = _fetchall(
        conn,
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
               theme_name, theme_score, lifecycle_stage
          FROM mart_market_perception_theme_daily
         WHERE snapshot_date BETWEEN ? AND ?
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _score_stock_roles(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in ["ret_1d", "ret_3d", "ret_5d", "ret_20d", "amount_ratio_5_20", "theme_score"]:
        out[col] = pd.to_numeric(out.get(col), errors="coerce").fillna(0.0)
    grouped = out.groupby(["snapshot_date", "theme_name"], group_keys=False)
    out["ret1_rank"] = grouped["ret_1d"].rank(pct=True, method="average")
    out["ret3_rank"] = grouped["ret_3d"].rank(pct=True, method="average")
    out["ret5_rank"] = grouped["ret_5d"].rank(pct=True, method="average")
    out["amount_rank"] = grouped["amount_ratio_5_20"].rank(pct=True, method="average")
    out["leader_strength_score"] = (out["ret5_rank"] * 0.70 + out["amount_rank"] * 0.30).round(6)
    out["follower_lag_score"] = (
        (1.0 - out["ret5_rank"]) * 0.35
        + out["ret1_rank"] * 0.25
        + out["ret3_rank"] * 0.20
        + out["amount_rank"] * 0.20
    ).round(6)
    return out


def _build_edges(frame: pd.DataFrame, *, top_n: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (snapshot_date, theme_name), group in frame.groupby(["snapshot_date", "theme_name"], sort=True):
        if len(group) < 3:
            continue
        leader = group.sort_values(["leader_strength_score", "ret_5d"], ascending=[False, False]).iloc[0]
        followers = group[group["stock_code"] != leader["stock_code"]].copy()
        followers = followers[followers["ret_5d"] <= leader["ret_5d"]]
        followers = followers.sort_values(["follower_lag_score", "ret_1d"], ascending=[False, False]).head(top_n)
        theme_boost = (float(leader.get("theme_score", 0.0)) + 0.9) / 1.8
        theme_boost = max(0.0, min(1.0, theme_boost))
        for _, follower in followers.iterrows():
            diffusion_score = (
                float(leader["leader_strength_score"]) * 0.35
                + float(follower["follower_lag_score"]) * 0.45
                + theme_boost * 0.20
            )
            rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "theme_name": theme_name,
                    "leader_stock_code": leader["stock_code"],
                    "follower_stock_code": follower["stock_code"],
                    "relation_type": "同板块",
                    "lag_days": 1,
                    "leader_strength_score": round(float(leader["leader_strength_score"]), 6),
                    "follower_lag_score": round(float(follower["follower_lag_score"]), 6),
                    "diffusion_score": round(float(diffusion_score) * 2.0 - 1.0, 6),
                    "leader_ret_5d": float(leader["ret_5d"]),
                    "leader_ret_20d": float(leader["ret_20d"]),
                    "follower_ret_1d": float(follower["ret_1d"]),
                    "follower_ret_3d": float(follower["ret_3d"]),
                    "follower_ret_5d": float(follower["ret_5d"]),
                    "follower_ret_20d": float(follower["ret_20d"]),
                    "follower_amount_ratio_5_20": float(follower["amount_ratio_5_20"]),
                    "theme_score": float(follower.get("theme_score", 0.0)),
                    "lifecycle_stage": follower.get("lifecycle_stage"),
                    "pit_member_confidence": follower.get("pit_member_confidence"),
                }
            )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values(["snapshot_date", "diffusion_score"], ascending=[True, False])


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
    score = float(row["diffusion_score"])
    if not math.isfinite(score) or abs(score) > 0.95:
        raise ValueError(
            f"leader/follower diffusion_score guard failed for {row['snapshot_date']} "
            f"{row['leader_stock_code']}->{row['follower_stock_code']}: {score}"
        )
