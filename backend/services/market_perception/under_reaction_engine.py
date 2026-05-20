"""PIT-strict fund anomaly / under-reaction context for Market Perception P4."""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .regime_engine import _attach_market_if_available, _fetchall, _table_exists, _to_date

logger = logging.getLogger("cm-api.market-perception.under-reaction")


def compute_under_reaction_for_date(conn, snapshot_date: str | date | datetime, top_n: int = 100) -> dict[str, Any]:
    day = _to_date(snapshot_date)
    frame = compute_under_reaction_for_range(conn, day, day, top_n=top_n)
    return {
        "snapshot_date": day.isoformat(),
        "rows": int(len(frame)),
        "candidates": frame.to_dict("records"),
    }


def compute_under_reaction_for_range(
    conn,
    start: str | date,
    end: str | date,
    *,
    top_n: int = 100,
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
    if not _table_exists(conn, "fact_capital_flow_pit_daily"):
        raise ValueError("fact_capital_flow_pit_daily is required for UnderReactionEngine")

    _attach_market_if_available(conn)
    price = _load_price_reaction(conn, start_day, end_day)
    flow = _load_capital_flow(conn, start_day, end_day)
    if price.empty or flow.empty:
        raise ValueError(f"under-reaction inputs missing for {start_day} -> {end_day}")
    theme = _load_theme_context(conn, start_day, end_day)
    frame = flow.merge(price, on=["snapshot_date", "stock_code"], how="inner")
    if not theme.empty:
        frame = frame.merge(theme, on=["snapshot_date", "stock_code"], how="left")
    else:
        frame["theme_name"] = None
        frame["theme_score"] = 0.0
        frame["lifecycle_stage"] = None
    frame = _score(frame)
    frame["source_engines"] = frame.apply(
        lambda row: json.dumps(
            [{"engine": "UnderReactionEngine", "score": float(row["under_reaction_score"]), "weight": 1.0}],
            ensure_ascii=False,
        ),
        axis=1,
    )
    frame["pit_cutoff_date"] = frame["snapshot_date"]
    for _, row in frame.iterrows():
        _guard_row(row)
    return (
        frame.sort_values(["snapshot_date", "under_reaction_score"], ascending=[True, False])
        .groupby("snapshot_date", group_keys=False)
        .head(max(1, int(top_n)))
        .reset_index(drop=True)
    )


def _load_price_reaction(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if _table_exists(conn, "fact_stock_kline_daily"):
        return _load_fact_stock_price_reaction(conn, start_day, end_day)
    return _load_market_price_reaction(conn, start_day, end_day)


def _load_fact_stock_price_reaction(conn, start_day: date, end_day: date) -> pd.DataFrame:
    query_start = start_day - timedelta(days=45)
    rows = _fetchall(
        conn,
        """
        WITH px AS (
            SELECT stock_code, CAST(trade_date AS VARCHAR) AS snapshot_date,
                   close, amount,
                   LAG(close, 5) OVER (PARTITION BY stock_code ORDER BY CAST(trade_date AS DATE)) AS close_5d,
                   LAG(close, 20) OVER (PARTITION BY stock_code ORDER BY CAST(trade_date AS DATE)) AS close_20d,
                   AVG(amount) OVER (PARTITION BY stock_code ORDER BY CAST(trade_date AS DATE) ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS amount_5d,
                   AVG(amount) OVER (PARTITION BY stock_code ORDER BY CAST(trade_date AS DATE) ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amount_20d
              FROM fact_stock_kline_daily
             WHERE CAST(trade_date AS DATE) BETWEEN ? AND ?
        )
        SELECT stock_code, snapshot_date,
               CASE WHEN close_5d > 0 THEN close / close_5d - 1.0 ELSE NULL END AS ret_5d,
               CASE WHEN close_20d > 0 THEN close / close_20d - 1.0 ELSE NULL END AS ret_20d,
               CASE WHEN amount_20d > 0 THEN amount_5d / amount_20d ELSE NULL END AS amount_ratio_5_20
          FROM px
         WHERE CAST(snapshot_date AS DATE) BETWEEN ? AND ?
           AND close_5d IS NOT NULL AND close_20d IS NOT NULL
        """,
        [query_start.isoformat(), end_day.isoformat(), start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_market_price_reaction(conn, start_day: date, end_day: date) -> pd.DataFrame:
    query_start = start_day - timedelta(days=45)
    rows = _fetchall(
        conn,
        """
        WITH px AS (
            SELECT code AS stock_code, CAST(date AS VARCHAR) AS snapshot_date,
                   close, amount,
                   LAG(close, 5) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) AS close_5d,
                   LAG(close, 20) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) AS close_20d,
                   AVG(amount) OVER (PARTITION BY code ORDER BY CAST(date AS DATE) ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS amount_5d,
                   AVG(amount) OVER (PARTITION BY code ORDER BY CAST(date AS DATE) ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amount_20d
              FROM market.v_price_kline_qfq
             WHERE freq = 'daily' AND adjust = 'qfq'
               AND CAST(date AS DATE) BETWEEN ? AND ?
               AND regexp_matches(code, '^(00|30|60|68)[0-9]{4}$')
               AND COALESCE(source_name, '') NOT LIKE 'tdxhub_index%'
        )
        SELECT stock_code, snapshot_date,
               CASE WHEN close_5d > 0 THEN close / close_5d - 1.0 ELSE NULL END AS ret_5d,
               CASE WHEN close_20d > 0 THEN close / close_20d - 1.0 ELSE NULL END AS ret_20d,
               CASE WHEN amount_20d > 0 THEN amount_5d / amount_20d ELSE NULL END AS amount_ratio_5_20
          FROM px
         WHERE CAST(snapshot_date AS DATE) BETWEEN ? AND ?
           AND close_5d IS NOT NULL AND close_20d IS NOT NULL
        """,
        [query_start.isoformat(), end_day.isoformat(), start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_capital_flow(conn, start_day: date, end_day: date) -> pd.DataFrame:
    rows = _fetchall(
        conn,
        """
        SELECT trade_date AS snapshot_date, stock_code,
               lhb_count_30d, lhb_inst_buy_30d, lhb_net_buy_pct_30d,
               exec_net_signal, holder_count_change_q_pct
          FROM fact_capital_flow_pit_daily
         WHERE CAST(trade_date AS DATE) BETWEEN ? AND ?
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_theme_context(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not (_table_exists(conn, "mart_market_perception_theme_daily") and _table_exists(conn, "mart_stock_industry_pit")):
        return pd.DataFrame()
    rows = _fetchall(
        conn,
        """
        SELECT ip.stock_code, CAST(t.snapshot_date AS VARCHAR) AS snapshot_date,
               t.theme_name, t.theme_score, t.lifecycle_stage
          FROM mart_market_perception_theme_daily t
          JOIN mart_stock_industry_pit ip
            ON ip.tdx_l1_name = t.theme_name
           AND ip.confidence_level = 'observed_snapshot'
           AND t.snapshot_date BETWEEN CAST(ip.effective_from AS DATE) AND CAST(ip.effective_to AS DATE)
         WHERE t.snapshot_date BETWEEN ? AND ?
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _score(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["lhb_count_30d"] = out["lhb_count_30d"].fillna(0).astype(int)
    out["lhb_inst_buy_30d"] = out["lhb_inst_buy_30d"].fillna(0).astype(int)
    for col in ["lhb_net_buy_pct_30d", "exec_net_signal", "holder_count_change_q_pct", "amount_ratio_5_20", "ret_5d", "ret_20d", "theme_score"]:
        out[col] = pd.to_numeric(out.get(col), errors="coerce").fillna(0.0)
    grouped = out.groupby("snapshot_date", group_keys=False)
    out["flow_rank"] = grouped["lhb_net_buy_pct_30d"].rank(pct=True, method="average")
    out["inst_rank"] = grouped["lhb_inst_buy_30d"].rank(pct=True, method="average")
    out["exec_rank"] = grouped["exec_net_signal"].rank(pct=True, method="average")
    out["holder_rank"] = grouped["holder_count_change_q_pct"].rank(pct=True, ascending=False, method="average")
    out["amount_rank"] = grouped["amount_ratio_5_20"].rank(pct=True, method="average")
    out["ret5_rank"] = grouped["ret_5d"].rank(pct=True, method="average")
    out["ret20_rank"] = grouped["ret_20d"].rank(pct=True, method="average")
    out["capital_flow_score"] = (
        out["flow_rank"] * 0.35
        + out["inst_rank"] * 0.25
        + out["exec_rank"] * 0.20
        + out["holder_rank"] * 0.20
    )
    out["amount_expansion_score"] = out["amount_rank"]
    out["price_reaction_score"] = out["ret5_rank"] * 0.55 + out["ret20_rank"] * 0.45
    overheat = (
        (out["ret_5d"].clip(lower=0) / 0.20).clip(upper=1) * 0.55
        + (out["ret_20d"].clip(lower=0) / 0.40).clip(upper=1) * 0.45
    )
    out["crowding_penalty"] = overheat
    theme_boost = ((out["theme_score"].clip(lower=-0.9, upper=0.9) + 0.9) / 1.8).fillna(0.5)
    out["fund_anomaly_score"] = (
        out["capital_flow_score"] * 0.65
        + out["amount_expansion_score"] * 0.25
        + theme_boost * 0.10
    )
    score01 = (out["fund_anomaly_score"] * 0.70 + (1.0 - out["price_reaction_score"]) * 0.30) - out["crowding_penalty"] * 0.35
    out["under_reaction_score"] = (score01 * 2.0 - 1.0).clip(-0.9, 0.9).round(6)
    for col in ["fund_anomaly_score", "price_reaction_score", "capital_flow_score", "amount_expansion_score", "crowding_penalty"]:
        out[col] = out[col].round(6)
    return out[
        [
            "snapshot_date", "stock_code", "under_reaction_score", "fund_anomaly_score",
            "price_reaction_score", "capital_flow_score", "amount_expansion_score",
            "crowding_penalty", "ret_5d", "ret_20d", "amount_ratio_5_20",
            "lhb_count_30d", "lhb_inst_buy_30d", "lhb_net_buy_pct_30d", "exec_net_signal",
            "holder_count_change_q_pct", "theme_name", "theme_score", "lifecycle_stage",
        ]
    ]


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
    score = float(row["under_reaction_score"])
    if not math.isfinite(score) or abs(score) > 0.95:
        raise ValueError(f"under_reaction_score guard failed for {row['snapshot_date']} {row['stock_code']}: {score}")
