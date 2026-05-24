"""PIT-strict style rotation and crowding context for Market Perception P6."""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .regime_engine import _attach_market_if_available, _fetchall, _table_exists, _to_date

logger = logging.getLogger("cm-api.market-perception.style-rotation")


def compute_style_rotation_for_date(conn, snapshot_date: str | date | datetime) -> dict[str, Any]:
    day = _to_date(snapshot_date)
    frame = compute_style_rotation_for_range(conn, day, day)
    if frame.empty:
        return {"snapshot_date": day.isoformat(), "data": None}
    return frame.iloc[0].to_dict()


def compute_style_rotation_for_range(conn, start: str | date, end: str | date) -> pd.DataFrame:
    start_day = _to_date(start)
    end_day = _to_date(end)
    if start_day > end_day:
        raise ValueError(f"start {start_day} > end {end_day}")
    days = _trading_days(conn, start_day, end_day)
    if any(d >= date.today() for d in days):
        raise ValueError(f"range {start_day} -> {end_day} includes today/future; PIT requires snapshot_date < today")
    if not days:
        return pd.DataFrame()

    _attach_market_if_available(conn)
    stocks = _load_stock_style_inputs(conn, start_day, end_day)
    if stocks.empty:
        raise ValueError(f"style rotation inputs missing for {start_day} -> {end_day}")
    # rule-compliance: ok evidence=Phase 3.2 Codex review a7f6f763c431c9c09 — as_of=end_day for built_at filter
    mcap = _load_market_cap_deciles(conn, start_day, end_day, as_of=end_day)
    stocks = _merge_style_deciles(stocks, mcap)
    emotion = _load_emotion_context(conn, start_day, end_day, as_of=end_day)
    rows = []
    for day in days:
        group = stocks[stocks["snapshot_date"] == day.isoformat()].copy()
        if group.empty:
            raise ValueError(f"style rotation inputs missing for trading day {day}")
        row = _score_day(day, group)
        if not emotion.empty:
            match = emotion[emotion["snapshot_date"] == day.isoformat()]
            if not match.empty:
                row["emotion_score"] = _nullable_float(match.iloc[0]["emotion_score"])
                row["emotion_state"] = match.iloc[0]["emotion_state"]
        row["source_engines"] = json.dumps(
            [
                {"engine": "StyleRotation", "score": row["style_rotation_score"], "weight": 0.55},
                {"engine": "CrowdingRisk", "score": row["crowding_risk_score"], "weight": 0.45},
            ],
            ensure_ascii=False,
        )
        row["pit_cutoff_date"] = day.isoformat()
        _guard_row(row)
        rows.append(row)
    return pd.DataFrame(rows)


def _load_stock_style_inputs(conn, start_day: date, end_day: date) -> pd.DataFrame:
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
    query_start = start_day - timedelta(days=45)
    rows = _fetchall(
        conn,
        f"""
        WITH px0 AS ({price_sql}),
        px AS (
            SELECT stock_code, snapshot_date, close, amount,
                   LAG(close, 1) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE)) AS close_1d,
                   LAG(close, 20) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE)) AS close_20d,
                   AVG(amount) OVER (PARTITION BY stock_code ORDER BY CAST(snapshot_date AS DATE) ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amount_20d
              FROM px0
        )
        SELECT px.stock_code,
               px.snapshot_date,
               CASE WHEN px.close_1d > 0 THEN px.close / px.close_1d - 1.0 ELSE NULL END AS ret_1d,
               CASE WHEN px.close_20d > 0 THEN px.close / px.close_20d - 1.0 ELSE NULL END AS ret_20d,
               px.amount,
               px.amount_20d
          FROM px
          JOIN dim_trading_calendar cal
            ON CAST(cal.trade_date AS DATE) = CAST(px.snapshot_date AS DATE)
           AND cal.is_trading = 1
         WHERE CAST(px.snapshot_date AS DATE) BETWEEN ? AND ?
           AND px.close_1d IS NOT NULL
           AND px.close_20d IS NOT NULL
           AND px.amount_20d IS NOT NULL
        """,
        [query_start.isoformat(), end_day.isoformat(), start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_market_cap_deciles(conn, start_day: date, end_day: date, as_of: date | None = None) -> pd.DataFrame:
    if not _table_exists(conn, "fact_market_cap_decile_daily"):
        return pd.DataFrame()
    cutoff = (as_of or end_day).isoformat()
    rows = _fetchall(
        conn,
        """
        SELECT stock_code, CAST(trade_date AS VARCHAR) AS snapshot_date, mcap_decile
          FROM fact_market_cap_decile_daily
         WHERE trade_date BETWEEN ? AND ?
           AND source_max_trade_date <= trade_date
           AND (built_at IS NULL OR TRY_CAST(built_at AS TIMESTAMP) <= TRY_CAST(? AS TIMESTAMP))
        """,
        [start_day.isoformat(), end_day.isoformat(), cutoff],
    )
    return pd.DataFrame(rows)


def _merge_style_deciles(stocks: pd.DataFrame, mcap: pd.DataFrame) -> pd.DataFrame:
    out = stocks.copy()
    if not mcap.empty:
        out = out.merge(mcap, on=["snapshot_date", "stock_code"], how="left")
    else:
        out["mcap_decile"] = pd.NA
    out["mcap_decile"] = pd.to_numeric(out["mcap_decile"], errors="coerce")
    out["liquidity_decile"] = (
        out.groupby("snapshot_date")["amount_20d"]
        .rank(pct=True, method="average")
        .mul(10)
        .clip(lower=1, upper=10)
        .round()
        .astype(int)
    )
    out["style_decile"] = out["mcap_decile"].fillna(out["liquidity_decile"]).astype(int)
    out["style_source"] = out["mcap_decile"].notna().map({True: "market_cap_decile", False: "amount_liquidity_proxy"})
    return out


def _load_emotion_context(conn, start_day: date, end_day: date, as_of: date | None = None) -> pd.DataFrame:
    if not _table_exists(conn, "mart_market_perception_emotion_daily"):
        return pd.DataFrame()
    cutoff = (as_of or end_day).isoformat()
    rows = _fetchall(
        conn,
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date, emotion_score, emotion_state
          FROM mart_market_perception_emotion_daily
         WHERE snapshot_date BETWEEN ? AND ?
           AND (built_at IS NULL OR TRY_CAST(built_at AS TIMESTAMP) <= TRY_CAST(? AS TIMESTAMP))
        """,
        [start_day.isoformat(), end_day.isoformat(), cutoff],
    )
    return pd.DataFrame(rows)


def _score_day(day: date, group: pd.DataFrame) -> dict[str, Any]:
    for col in ["ret_1d", "ret_20d", "amount", "amount_20d"]:
        group[col] = pd.to_numeric(group[col], errors="coerce").fillna(0.0)
    small = group[group["style_decile"] <= 3]
    mid = group[(group["style_decile"] >= 4) & (group["style_decile"] <= 7)]
    large = group[group["style_decile"] >= 8]
    ret_rank = group["ret_20d"].rank(pct=True, method="average")
    trend = group[ret_rank >= 0.8]
    reversal = group[ret_rank <= 0.2]
    small_ret = _mean(small["ret_1d"])
    mid_ret = _mean(mid["ret_1d"])
    large_ret = _mean(large["ret_1d"])
    trend_ret = _mean(trend["ret_1d"])
    reversal_ret = _mean(reversal["ret_1d"])
    size_score = _bounded((small_ret - large_ret) * 10.0)
    trend_score = _bounded((trend_ret - reversal_ret) * 10.0)
    style_score = _bounded(size_score * 0.60 + trend_score * 0.40)
    top_decile_turnover_share = _turnover_share(group[group["style_decile"] >= 9], group)
    hot = group[ret_rank >= 0.9]
    hot_stock_share = float(len(hot) / len(group)) if len(group) else 0.0
    hot_amount_share = _turnover_share(hot, group)
    crowding = max(0.0, min(0.9, hot_amount_share * 0.65 + top_decile_turnover_share * 0.35))
    overheat = max(0.0, min(0.9, crowding * max(0.0, trend_score)))
    sources = sorted(set(str(x) for x in group["style_source"].dropna()))
    return {
        "snapshot_date": day.isoformat(),
        "style_rotation_score": round(style_score, 6),
        "style_bias": _style_bias(size_score, trend_score),
        "size_preference_score": round(size_score, 6),
        "trend_preference_score": round(trend_score, 6),
        "crowding_risk_score": round(crowding, 6),
        "overheat_reversal_risk": round(overheat, 6),
        "small_ret_1d": small_ret,
        "mid_ret_1d": mid_ret,
        "large_ret_1d": large_ret,
        "trend_ret_1d": trend_ret,
        "reversal_ret_1d": reversal_ret,
        "top_decile_turnover_share": round(top_decile_turnover_share, 6),
        "hot_stock_share": round(hot_stock_share, 6),
        "style_source": "+".join(sources) if sources else "unknown",
        "emotion_score": None,
        "emotion_state": None,
    }


def _style_bias(size_score: float, trend_score: float) -> str:
    size = "小盘" if size_score > 0 else "大盘" if size_score < 0 else "均衡"
    trend = "趋势" if trend_score > 0 else "超跌" if trend_score < 0 else "均衡"
    return f"{size}/{trend}"


def _mean(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    return float(values.mean())


def _turnover_share(part: pd.DataFrame, whole: pd.DataFrame) -> float:
    denom = float(whole["amount"].sum())
    if denom <= 0:
        return 0.0
    return float(part["amount"].sum() / denom)


def _bounded(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(-0.9, min(0.9, value))


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


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


def _guard_row(row: dict[str, Any]) -> None:
    for key in ["style_rotation_score", "crowding_risk_score", "overheat_reversal_risk"]:
        score = float(row[key])
        if not math.isfinite(score) or abs(score) > 0.95:
            raise ValueError(f"{key} guard failed for {row['snapshot_date']}: {score}")
