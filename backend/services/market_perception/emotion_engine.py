"""PIT-strict short-term market emotion features for Market Perception P2."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .regime_engine import _attach_market_if_available, _fetchall, _fetchone, _table_exists, _to_date

logger = logging.getLogger("cm-api.market-perception.emotion")

REPO_ROOT = Path(__file__).resolve().parents[3]
UNKNOWN_METRICS = [
    "first_board_count",
    "second_board_count",
    "third_plus_count",
    "promotion_rate_1_to_2",
    "promotion_rate_2_to_3",
    "open_board_rate",
    "next_day_premium",
]


@dataclass(frozen=True)
class EmotionInputs:
    snapshot_date: date
    market_breadth: float
    up_count: int
    down_count: int
    limit_up_count: int
    limit_down_count: int
    turnover_concentration: float | None
    lhb_event_count: int
    n_stocks: int


def compute_emotion_for_date(conn, snapshot_date: str | date | datetime) -> dict[str, Any]:
    day = _to_date(snapshot_date)
    inputs_by_day = _load_inputs_for_range(conn, day, day, [day])
    return _payload_from_inputs(inputs_by_day[day])


def compute_emotion_for_range(conn, start: str | date, end: str | date) -> pd.DataFrame:
    start_day = _to_date(start)
    end_day = _to_date(end)
    if start_day > end_day:
        raise ValueError(f"start {start_day} > end {end_day}")
    days = _trading_days(conn, start_day, end_day)
    if any(d >= date.today() for d in days):
        raise ValueError(f"range {start_day} -> {end_day} includes today/future; PIT requires snapshot_date < today")
    if not days:
        return pd.DataFrame()
    inputs_by_day = _load_inputs_for_range(conn, start_day, end_day, days)
    rows = [_payload_from_inputs(inputs_by_day[d]) for d in days]
    return pd.DataFrame(rows)


def _load_inputs_for_range(
    conn,
    start_day: date,
    end_day: date,
    target_days: list[date],
) -> dict[date, EmotionInputs]:
    _attach_market_if_available(conn)
    stats = _load_market_stats(conn, start_day, end_day)
    if stats.empty:
        raise ValueError(f"emotion market stats are empty for {start_day} -> {end_day}")
    stats["day"] = pd.to_datetime(stats["date"]).dt.date
    stats = stats.set_index("day")
    lhb_counts = _load_lhb_counts(conn, start_day, end_day)
    out: dict[date, EmotionInputs] = {}
    for day in target_days:
        if day not in stats.index:
            raise ValueError(f"emotion market stats missing for {day}")
        row = stats.loc[day]
        breadth = float(row["market_breadth"])
        if not math.isfinite(breadth):
            raise ValueError(f"emotion market breadth invalid for {day}: {breadth}")
        out[day] = EmotionInputs(
            snapshot_date=day,
            market_breadth=breadth,
            up_count=int(row["up_count"]),
            down_count=int(row["down_count"]),
            limit_up_count=int(row["limit_up_count"]),
            limit_down_count=int(row["limit_down_count"]),
            turnover_concentration=_nullable_float(row["turnover_concentration"]),
            lhb_event_count=int(lhb_counts.get(day, 0)),
            n_stocks=int(row["n_stocks"]),
        )
    return out


def _load_market_stats(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if _table_exists(conn, "fact_stock_kline_daily"):
        return _load_fact_stock_stats(conn, start_day, end_day)
    return _load_market_view_stats(conn, start_day, end_day)


def _load_fact_stock_stats(conn, start_day: date, end_day: date) -> pd.DataFrame:
    cols = {r["column_name"] for r in _fetchall(conn, "DESCRIBE fact_stock_kline_daily")}
    date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
    pct_col = _first_existing(cols, ["pct_change", "pct_chg", "change_pct", "return_1d"])
    amount_col = _first_existing(cols, ["amount", "turnover", "turnover_amount"], required=False)
    amount_expr = f"CAST({amount_col} AS DOUBLE)" if amount_col else "NULL::DOUBLE"
    rows = _fetchall(
        conn,
        f"""
        WITH base AS (
            SELECT CAST({date_col} AS VARCHAR) AS date,
                   CAST({pct_col} AS DOUBLE) AS pct_change,
                   {amount_expr} AS amount
              FROM fact_stock_kline_daily
             WHERE CAST({date_col} AS DATE) BETWEEN ? AND ?
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY date ORDER BY amount DESC NULLS LAST) AS amount_rank
              FROM base
        )
        SELECT date,
               COUNT(*)::INTEGER AS n_stocks,
               SUM(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END)::INTEGER AS up_count,
               SUM(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END)::INTEGER AS down_count,
               SUM(CASE WHEN pct_change >= 9.5 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count,
               SUM(CASE WHEN pct_change <= -9.5 THEN 1 ELSE 0 END)::INTEGER AS limit_down_count,
               AVG(CASE WHEN pct_change > 0 THEN 1.0 ELSE 0.0 END) AS market_breadth,
               CASE
                   WHEN SUM(amount) IS NULL OR SUM(amount) <= 0 THEN NULL
                   ELSE SUM(CASE WHEN amount_rank <= 10 THEN amount ELSE 0 END) / SUM(amount)
               END AS turnover_concentration
          FROM ranked
         GROUP BY 1
         ORDER BY CAST(date AS DATE)
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_market_view_stats(conn, start_day: date, end_day: date) -> pd.DataFrame:
    extended_start = _previous_trading_day(conn, start_day)
    rows = _fetchall(
        conn,
        """
        WITH px AS (
            SELECT code, CAST(date AS VARCHAR) AS date, close, amount,
                   LAG(close) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) AS prev_close
              FROM market.v_price_kline_qfq
             WHERE freq = 'daily' AND adjust = 'qfq'
               AND CAST(date AS DATE) BETWEEN ? AND ?
               AND regexp_matches(code, '^(00|30|60|68)[0-9]{4}$')
               AND COALESCE(source_name, '') NOT LIKE 'tdxhub_index%'
        ),
        base AS (
            SELECT date, code, amount,
                   CASE WHEN prev_close > 0 THEN close / prev_close - 1.0 ELSE NULL END AS ret_1d
              FROM px
             WHERE prev_close IS NOT NULL
               AND CAST(date AS DATE) BETWEEN ? AND ?
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY date ORDER BY amount DESC NULLS LAST) AS amount_rank
              FROM base
        )
        SELECT date,
               COUNT(*)::INTEGER AS n_stocks,
               SUM(CASE WHEN ret_1d > 0 THEN 1 ELSE 0 END)::INTEGER AS up_count,
               SUM(CASE WHEN ret_1d < 0 THEN 1 ELSE 0 END)::INTEGER AS down_count,
               SUM(CASE WHEN ret_1d >= 0.095 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count,
               SUM(CASE WHEN ret_1d <= -0.095 THEN 1 ELSE 0 END)::INTEGER AS limit_down_count,
               AVG(CASE WHEN ret_1d > 0 THEN 1.0 ELSE 0.0 END) AS market_breadth,
               CASE
                   WHEN SUM(amount) IS NULL OR SUM(amount) <= 0 THEN NULL
                   ELSE SUM(CASE WHEN amount_rank <= 10 THEN amount ELSE 0 END) / SUM(amount)
               END AS turnover_concentration
          FROM ranked
         GROUP BY 1
         ORDER BY CAST(date AS DATE)
        """,
        [extended_start.isoformat(), end_day.isoformat(), start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _payload_from_inputs(inputs: EmotionInputs) -> dict[str, Any]:
    score = _score_emotion(inputs)
    payload = {
        "snapshot_date": inputs.snapshot_date.isoformat(),
        "emotion_score": score,
        "emotion_state": _emotion_state(score),
        "action_bias": _action_bias(score),
        "cycle_phase": _cycle_phase(inputs, score),
        "market_breadth": inputs.market_breadth,
        "up_count": inputs.up_count,
        "down_count": inputs.down_count,
        "limit_up_count": inputs.limit_up_count,
        "limit_down_count": inputs.limit_down_count,
        "first_board_count": None,
        "second_board_count": None,
        "third_plus_count": None,
        "promotion_rate_1_to_2": None,
        "promotion_rate_2_to_3": None,
        "open_board_rate": None,
        "next_day_premium": None,
        "turnover_concentration": inputs.turnover_concentration,
        "lhb_event_count": inputs.lhb_event_count,
        "n_stocks": inputs.n_stocks,
        "unknown_metrics": json.dumps(UNKNOWN_METRICS, ensure_ascii=False),
        "source_engines": json.dumps(
            [{"engine": "MarketEmotionCycle", "score": score, "weight": 1.0}],
            ensure_ascii=False,
        ),
        "pit_cutoff_date": inputs.snapshot_date.isoformat(),
    }
    if abs(score) > 0.95:
        raise ValueError(f"emotion_score leakage guard triggered: {score}")
    return payload


def _score_emotion(inputs: EmotionInputs) -> float:
    breadth_component = _clip((inputs.market_breadth - 0.5) / 0.5, -1.0, 1.0)
    limit_balance = (inputs.limit_up_count - inputs.limit_down_count) / max(
        inputs.limit_up_count + inputs.limit_down_count,
        20,
    )
    lhb_heat = _clip(inputs.lhb_event_count / max(inputs.n_stocks, 1) * 20.0, 0.0, 1.0)
    return round(_clip(0.60 * breadth_component + 0.30 * limit_balance + 0.10 * lhb_heat, -1.0, 1.0), 6)


def _emotion_state(score: float) -> str:
    if score >= 0.35:
        return "赚钱效应扩张"
    if score <= -0.35:
        return "亏钱效应扩散"
    return "分化震荡"


def _action_bias(score: float) -> str:
    if score >= 0.35:
        return "追强有效"
    if score <= -0.35:
        return "降低仓位"
    return "低吸观察"


def _cycle_phase(inputs: EmotionInputs, score: float) -> str:
    if score >= 0.55 and inputs.limit_up_count >= max(inputs.limit_down_count * 3, 20):
        return "主升扩散"
    if score >= 0.35:
        return "新周期试错"
    if score <= -0.35:
        return "退潮"
    return "分歧"


def _load_lhb_counts(conn, start_day: date, end_day: date) -> dict[date, int]:
    if not _table_exists(conn, "fact_lhb_event"):
        return {}
    cols = {r["column_name"] for r in _fetchall(conn, "DESCRIBE fact_lhb_event")}
    built_filter = ""
    if "built_at" in cols:
        built_filter = (
            " AND (built_at IS NULL OR TRY_CAST(built_at AS TIMESTAMP) <= "
            "CAST(trade_date AS DATE) + INTERVAL 1 DAY - INTERVAL 1 SECOND)"
        )
    rows = _fetchall(
        conn,
        f"""
        SELECT CAST(trade_date AS VARCHAR) AS trade_date, COUNT(*)::INTEGER AS n
          FROM fact_lhb_event
         WHERE CAST(trade_date AS DATE) BETWEEN ? AND ?
               {built_filter}
         GROUP BY 1
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return {_to_date(row["trade_date"]): int(row["n"]) for row in rows}


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


def _previous_trading_day(conn, day: date) -> date:
    row = _fetchone(
        conn,
        """
        SELECT CAST(MAX(trade_date) AS VARCHAR) AS trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) < ?
        """,
        [day.isoformat()],
    )
    if not row or row["trade_date"] is None:
        return day
    return _to_date(row["trade_date"])


def _first_existing(cols: set[str], names: list[str], required: bool = True) -> str | None:
    for name in names:
        if name in cols:
            return name
    if required:
        raise ValueError(f"required columns missing; expected one of {names}, got {sorted(cols)}")
    return None


def _nullable_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _clip(value: float, lo: float, hi: float) -> float:
    if math.isnan(value):
        raise ValueError("emotion input produced NaN")
    return max(lo, min(hi, float(value)))
