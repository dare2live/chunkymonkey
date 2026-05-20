"""PIT-strict daily market regime features for Market Perception P1."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

logger = logging.getLogger("cm-api.market-perception.regime")

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
CONFIG_PATH = REPO_ROOT / "backend" / "config" / "market_perception.yaml"


@dataclass(frozen=True)
class RegimeInputs:
    snapshot_date: date
    hs300_ret_60d: float
    hs300_vol_20d: float
    breadth_ratio: float
    breadth_p75_90d: float
    limit_up_count: int
    lhb_event_count: int
    n_obs_days: int


@dataclass(frozen=True)
class RegimeConfig:
    hs300_code: str
    ret_days: int
    vol_days: int
    breadth_p75_days: int
    query_days: int
    trend_scale: float
    vol_reference: float
    breadth_floor: float
    trend_weight: float
    volatility_weight: float
    breadth_weight: float
    low_vol_max: float
    normal_vol_max: float
    high_vol_max: float
    spread_ret_min: float
    climax_ret_min: float
    climax_vol_min: float
    fade_ret_max: float
    regime_score_abs_max: float


def compute_regime_for_date(conn, snapshot_date: str | date | datetime) -> dict[str, Any]:
    """Compute one daily market context snapshot using only data available by T close."""
    day = _to_date(snapshot_date)
    _validate_snapshot_date(conn, day)
    _attach_market_if_available(conn)

    inputs = _load_inputs(conn, day)
    regime_score = _score_regime(inputs)
    payload = {
        "snapshot_date": day.isoformat(),
        "regime_score": regime_score,
        "breadth_state": _breadth_state(inputs.breadth_ratio, inputs.breadth_p75_90d),
        "volatility_state": _volatility_state(inputs.hs300_vol_20d),
        "sentiment_phase": _sentiment_phase(inputs),
        "hs300_ret_60d": inputs.hs300_ret_60d,
        "hs300_vol_20d": inputs.hs300_vol_20d,
        "breadth_ratio": inputs.breadth_ratio,
        "breadth_p75_90d": inputs.breadth_p75_90d,
        "limit_up_count": inputs.limit_up_count,
        "lhb_event_count": inputs.lhb_event_count,
        "n_obs_days": inputs.n_obs_days,
        "source_engines": json.dumps(
            [
                {"engine": "MarketRegimeEngine", "score": regime_score, "weight": 1.0},
            ],
            ensure_ascii=False,
        ),
        "pit_cutoff_date": day.isoformat(),
    }
    _guard_regime_payload(payload)
    return payload


def compute_regime_for_range(conn, start: str | date, end: str | date) -> pd.DataFrame:
    """Compute market regime snapshots for trading days in [start, end]."""
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
    inputs_by_day = _load_inputs_for_range(conn, start_day, end_day, days)
    rows = []
    for day in days:
        inputs = inputs_by_day.get(day)
        if inputs is None:
            raise ValueError(f"market regime inputs missing for {day}")
        rows.append(_payload_from_inputs(inputs))
    return pd.DataFrame(rows)


def _payload_from_inputs(inputs: RegimeInputs) -> dict[str, Any]:
    regime_score = _score_regime(inputs)
    day = inputs.snapshot_date
    payload = {
        "snapshot_date": day.isoformat(),
        "regime_score": regime_score,
        "breadth_state": _breadth_state(inputs.breadth_ratio, inputs.breadth_p75_90d),
        "volatility_state": _volatility_state(inputs.hs300_vol_20d),
        "sentiment_phase": _sentiment_phase(inputs),
        "hs300_ret_60d": inputs.hs300_ret_60d,
        "hs300_vol_20d": inputs.hs300_vol_20d,
        "breadth_ratio": inputs.breadth_ratio,
        "breadth_p75_90d": inputs.breadth_p75_90d,
        "limit_up_count": inputs.limit_up_count,
        "lhb_event_count": inputs.lhb_event_count,
        "n_obs_days": inputs.n_obs_days,
        "source_engines": json.dumps(
            [
                {"engine": "MarketRegimeEngine", "score": regime_score, "weight": 1.0},
            ],
            ensure_ascii=False,
        ),
        "pit_cutoff_date": day.isoformat(),
    }
    _guard_regime_payload(payload)
    return payload


def _load_inputs(conn, day: date) -> RegimeInputs:
    cfg = get_regime_config()
    hs300 = _load_hs300_history(conn, day)
    if len(hs300) < cfg.ret_days + 1:
        raise ValueError(f"HS300 history before {day} has only {len(hs300)} rows")

    hs300 = hs300.sort_values("date").reset_index(drop=True)
    close_now = float(hs300.iloc[-1]["close"])
    close_prev = float(hs300.iloc[-(cfg.ret_days + 1)]["close"])
    ret_60d = close_now / close_prev - 1.0
    log_ret = (hs300["close"].astype(float) / hs300["close"].astype(float).shift(1)).map(math.log)
    vol_20d = float(log_ret.tail(cfg.vol_days).std(ddof=1) * math.sqrt(252))

    breadth = _load_breadth_history(conn, day)
    if breadth.empty:
        raise ValueError(f"breadth history before {day} is empty")
    today = breadth[breadth["date"] == day.isoformat()]
    if today.empty:
        raise ValueError(f"breadth row missing for {day}")
    breadth_ratio = float(today.iloc[-1]["breadth_ratio"])
    p75_window = breadth.tail(cfg.breadth_p75_days)
    breadth_p75 = float(p75_window["breadth_ratio"].quantile(0.75))
    limit_up_count = int(today.iloc[-1]["limit_up_count"])
    lhb_count = _load_lhb_count(conn, day)
    return RegimeInputs(
        snapshot_date=day,
        hs300_ret_60d=ret_60d,
        hs300_vol_20d=vol_20d,
        breadth_ratio=breadth_ratio,
        breadth_p75_90d=breadth_p75,
        limit_up_count=limit_up_count,
        lhb_event_count=lhb_count,
        n_obs_days=min(len(hs300), len(breadth)),
    )


def _load_inputs_for_range(
    conn,
    start_day: date,
    end_day: date,
    target_days: list[date],
) -> dict[date, RegimeInputs]:
    cfg = get_regime_config()
    extended_start = _extended_start_day(conn, start_day)
    hs300 = _load_hs300_range(conn, extended_start, end_day)
    if hs300.empty:
        raise ValueError(f"HS300 history before {end_day} is empty")
    hs300 = hs300.sort_values("date").reset_index(drop=True)
    hs300["day"] = pd.to_datetime(hs300["date"]).dt.date
    hs300["close"] = hs300["close"].astype(float)
    hs300["hs300_ret_60d"] = hs300["close"] / hs300["close"].shift(cfg.ret_days) - 1.0
    log_ret = (hs300["close"] / hs300["close"].shift(1)).map(math.log)
    hs300["hs300_vol_20d"] = log_ret.rolling(cfg.vol_days).std(ddof=1) * math.sqrt(252)
    hs300["hs300_obs_days"] = hs300["close"].rolling(cfg.query_days, min_periods=1).count().astype(int)
    hs300_features = hs300.set_index("day")

    breadth = _load_breadth_range(conn, extended_start, end_day)
    if breadth.empty:
        raise ValueError(f"breadth history before {end_day} is empty")
    breadth = breadth.sort_values("date").reset_index(drop=True)
    breadth["day"] = pd.to_datetime(breadth["date"]).dt.date
    breadth["breadth_ratio"] = breadth["breadth_ratio"].astype(float)
    breadth["breadth_p75_90d"] = breadth["breadth_ratio"].rolling(cfg.breadth_p75_days, min_periods=1).quantile(0.75)
    breadth["breadth_obs_days"] = breadth["breadth_ratio"].rolling(cfg.query_days, min_periods=1).count().astype(int)
    breadth_features = breadth.set_index("day")

    lhb_counts = _load_lhb_counts_range(conn, start_day, end_day)
    out: dict[date, RegimeInputs] = {}
    for day in target_days:
        if day not in hs300_features.index:
            raise ValueError(f"HS300 row missing for {day}")
        if day not in breadth_features.index:
            raise ValueError(f"breadth row missing for {day}")
        hs_row = hs300_features.loc[day]
        br_row = breadth_features.loc[day]
        required = {
            "hs300_ret_60d": hs_row["hs300_ret_60d"],
            "hs300_vol_20d": hs_row["hs300_vol_20d"],
            "breadth_ratio": br_row["breadth_ratio"],
            "breadth_p75_90d": br_row["breadth_p75_90d"],
        }
        missing = [name for name, value in required.items() if pd.isna(value)]
        if missing:
            raise ValueError(f"market regime inputs for {day} missing required values: {missing}")
        out[day] = RegimeInputs(
            snapshot_date=day,
            hs300_ret_60d=float(hs_row["hs300_ret_60d"]),
            hs300_vol_20d=float(hs_row["hs300_vol_20d"]),
            breadth_ratio=float(br_row["breadth_ratio"]),
            breadth_p75_90d=float(br_row["breadth_p75_90d"]),
            limit_up_count=int(br_row["limit_up_count"]),
            lhb_event_count=int(lhb_counts.get(day, 0)),
            n_obs_days=min(int(hs_row["hs300_obs_days"]), int(br_row["breadth_obs_days"])),
        )
    return out


def _extended_start_day(conn, start_day: date) -> date:
    cfg = get_regime_config()
    rows = _fetchall(
        conn,
        f"""
        SELECT CAST(trade_date AS VARCHAR) AS trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) <= ?
         ORDER BY CAST(trade_date AS DATE) DESC
         LIMIT {cfg.query_days + 1}
        """,
        [start_day.isoformat()],
    )
    if not rows:
        raise ValueError(f"no trading days found on or before {start_day}")
    return _to_date(rows[-1]["trade_date"])


def _load_hs300_history(conn, day: date) -> pd.DataFrame:
    cfg = get_regime_config()
    if _table_exists(conn, "mart_index_daily"):
        cols = _columns(conn, "mart_index_daily")
        date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
        close_col = _first_existing(cols, ["close", "close_price"])
        code_col = _first_existing(cols, ["index_code", "code", "symbol", "ts_code"], required=False)
        where = f"CAST({date_col} AS DATE) <= ?"
        params: list[Any] = [day.isoformat()]
        if code_col:
            where += f" AND ({code_col} = ? OR lower({code_col}) = 'hs300')"
            params.append(cfg.hs300_code)
        rows = _fetchall(
            conn,
            f"""
            SELECT CAST({date_col} AS VARCHAR) AS date, {close_col} AS close
              FROM mart_index_daily
             WHERE {where}
             ORDER BY CAST({date_col} AS DATE) DESC
             LIMIT {cfg.query_days}
            """,
            params,
        )
    else:
        rows = _fetchall(
            conn,
            f"""
            SELECT CAST(date AS VARCHAR) AS date, close
              FROM market.v_price_kline_qfq
             WHERE code = ? AND freq = 'daily' AND adjust = 'qfq'
               AND CAST(date AS DATE) <= ?
             ORDER BY CAST(date AS DATE) DESC
             LIMIT {cfg.query_days}
            """,
            [cfg.hs300_code, day.isoformat()],
        )
    return pd.DataFrame(rows, columns=["date", "close"])


def _load_hs300_range(conn, start_day: date, end_day: date) -> pd.DataFrame:
    cfg = get_regime_config()
    if _table_exists(conn, "mart_index_daily"):
        cols = _columns(conn, "mart_index_daily")
        date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
        close_col = _first_existing(cols, ["close", "close_price"])
        code_col = _first_existing(cols, ["index_code", "code", "symbol", "ts_code"], required=False)
        where = f"CAST({date_col} AS DATE) BETWEEN ? AND ?"
        params: list[Any] = [start_day.isoformat(), end_day.isoformat()]
        if code_col:
            where += f" AND ({code_col} = ? OR lower({code_col}) = 'hs300')"
            params.append(cfg.hs300_code)
        rows = _fetchall(
            conn,
            f"""
            SELECT CAST({date_col} AS VARCHAR) AS date, {close_col} AS close
              FROM mart_index_daily
             WHERE {where}
             ORDER BY CAST({date_col} AS DATE)
            """,
            params,
        )
    else:
        rows = _fetchall(
            conn,
            """
            SELECT CAST(date AS VARCHAR) AS date, close
              FROM market.v_price_kline_qfq
             WHERE code = ? AND freq = 'daily' AND adjust = 'qfq'
               AND CAST(date AS DATE) BETWEEN ? AND ?
             ORDER BY CAST(date AS DATE)
            """,
            [cfg.hs300_code, start_day.isoformat(), end_day.isoformat()],
        )
    return pd.DataFrame(rows, columns=["date", "close"])


def _load_breadth_history(conn, day: date) -> pd.DataFrame:
    cfg = get_regime_config()
    if _table_exists(conn, "fact_stock_kline_daily"):
        cols = _columns(conn, "fact_stock_kline_daily")
        date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
        pct_col = _first_existing(cols, ["pct_change", "pct_chg", "change_pct", "return_1d"])
        rows = _fetchall(
            conn,
            f"""
            SELECT *
              FROM (
                    SELECT CAST({date_col} AS VARCHAR) AS date,
                           AVG(CASE WHEN {pct_col} > 0 THEN 1.0 ELSE 0.0 END) AS breadth_ratio,
                           SUM(CASE WHEN {pct_col} >= 9.5 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count
                      FROM fact_stock_kline_daily
                     WHERE CAST({date_col} AS DATE) <= ?
                     GROUP BY 1
              )
             ORDER BY CAST(date AS DATE) DESC
             LIMIT {cfg.query_days}
            """,
            [day.isoformat()],
        )
        frame = pd.DataFrame(rows, columns=["date", "breadth_ratio", "limit_up_count"])
        return frame.sort_values("date").reset_index(drop=True)

    rows = _fetchall(
        conn,
        f"""
        WITH trade_days AS (
            SELECT CAST(trade_date AS VARCHAR) AS trade_date
              FROM dim_trading_calendar
             WHERE is_trading = 1 AND CAST(trade_date AS DATE) <= ?
             ORDER BY CAST(trade_date AS DATE) DESC
             LIMIT {cfg.query_days + 1}
        ),
        px AS (
            SELECT code, CAST(date AS VARCHAR) AS date, close,
                   LAG(close) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) AS prev_close
              FROM market.v_price_kline_qfq
             WHERE freq = 'daily' AND adjust = 'qfq'
               AND date IN (SELECT trade_date FROM trade_days)
               AND regexp_matches(code, '^[0-9]{{6}}$')
        )
        SELECT date,
               AVG(CASE WHEN close > prev_close THEN 1.0 ELSE 0.0 END) AS breadth_ratio,
               SUM(CASE WHEN prev_close > 0 AND (close / prev_close - 1.0) >= 0.095 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count
          FROM px
         WHERE prev_close IS NOT NULL
         GROUP BY 1
         ORDER BY CAST(date AS DATE)
        """,
        [day.isoformat()],
    )
    return pd.DataFrame(rows, columns=["date", "breadth_ratio", "limit_up_count"])


def _load_breadth_range(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if _table_exists(conn, "fact_stock_kline_daily"):
        cols = _columns(conn, "fact_stock_kline_daily")
        date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
        pct_col = _first_existing(cols, ["pct_change", "pct_chg", "change_pct", "return_1d"])
        rows = _fetchall(
            conn,
            f"""
            SELECT CAST({date_col} AS VARCHAR) AS date,
                   AVG(CASE WHEN {pct_col} > 0 THEN 1.0 ELSE 0.0 END) AS breadth_ratio,
                   SUM(CASE WHEN {pct_col} >= 9.5 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count
             FROM fact_stock_kline_daily
             WHERE CAST({date_col} AS DATE) BETWEEN ? AND ?
             GROUP BY 1
             ORDER BY CAST(date AS DATE)
            """,
            [start_day.isoformat(), end_day.isoformat()],
        )
        return pd.DataFrame(rows, columns=["date", "breadth_ratio", "limit_up_count"])

    rows = _fetchall(
        conn,
        """
        WITH trade_days AS (
            SELECT CAST(trade_date AS VARCHAR) AS trade_date
              FROM dim_trading_calendar
             WHERE is_trading = 1
               AND CAST(trade_date AS DATE) BETWEEN ? AND ?
        ),
        px AS (
            SELECT code, CAST(date AS VARCHAR) AS date, close,
                   LAG(close) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) AS prev_close
              FROM market.v_price_kline_qfq
             WHERE freq = 'daily' AND adjust = 'qfq'
               AND date IN (SELECT trade_date FROM trade_days)
               AND regexp_matches(code, '^[0-9]{6}$')
        )
        SELECT date,
               AVG(CASE WHEN close > prev_close THEN 1.0 ELSE 0.0 END) AS breadth_ratio,
               SUM(CASE WHEN prev_close > 0 AND (close / prev_close - 1.0) >= 0.095 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count
          FROM px
         WHERE prev_close IS NOT NULL
         GROUP BY 1
         ORDER BY CAST(date AS DATE)
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows, columns=["date", "breadth_ratio", "limit_up_count"])


def _load_lhb_count(conn, day: date) -> int:
    if not _table_exists(conn, "fact_lhb_event"):
        return 0
    cols = _columns(conn, "fact_lhb_event")
    built_filter = ""
    params: list[Any] = [day.isoformat()]
    if "built_at" in cols:
        built_filter = " AND (built_at IS NULL OR TRY_CAST(built_at AS TIMESTAMP) <= TRY_CAST(? AS TIMESTAMP))"
        params.append(f"{day.isoformat()} 23:59:59")
    row = _fetchone(
        conn,
        f"""
        SELECT COUNT(*) AS n
          FROM fact_lhb_event
         WHERE CAST(trade_date AS DATE) = ?
               {built_filter}
        """,
        params,
    )
    return int(row["n"] if row else 0)


def _load_lhb_counts_range(conn, start_day: date, end_day: date) -> dict[date, int]:
    if not _table_exists(conn, "fact_lhb_event"):
        return {}
    cols = _columns(conn, "fact_lhb_event")
    built_filter = ""
    params: list[Any] = [start_day.isoformat(), end_day.isoformat()]
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
        params,
    )
    return {_to_date(row["trade_date"]): int(row["n"]) for row in rows}


def _score_regime(inputs: RegimeInputs) -> float:
    cfg = get_regime_config()
    trend_score = _clip(inputs.hs300_ret_60d / cfg.trend_scale, -1.0, 1.0)
    vol_score = _clip((cfg.vol_reference - inputs.hs300_vol_20d) / cfg.vol_reference, -1.0, 1.0)
    if inputs.breadth_ratio >= inputs.breadth_p75_90d:
        denom = max(1.0 - inputs.breadth_p75_90d, 1e-6)
        breadth_score = _clip((inputs.breadth_ratio - inputs.breadth_p75_90d) / denom, 0.0, 1.0)
    else:
        denom = max(inputs.breadth_p75_90d - cfg.breadth_floor, 1e-6)
        breadth_score = -_clip((inputs.breadth_p75_90d - inputs.breadth_ratio) / denom, 0.0, 1.0)
    score = (
        cfg.trend_weight * trend_score
        + cfg.volatility_weight * vol_score
        + cfg.breadth_weight * breadth_score
    )
    return round(_clip(score, -1.0, 1.0), 6)


def _breadth_state(breadth_ratio: float, breadth_p75_90d: float) -> str:
    cfg = get_regime_config()
    if breadth_ratio < cfg.breadth_floor:
        return "杀跌"
    if breadth_ratio >= breadth_p75_90d:
        return "健康扩散"
    return "分化"


def _volatility_state(vol: float) -> str:
    cfg = get_regime_config()
    if vol < cfg.low_vol_max:
        return "low"
    if vol < cfg.normal_vol_max:
        return "normal"
    if vol < cfg.high_vol_max:
        return "high"
    return "extreme"


def _sentiment_phase(inputs: RegimeInputs) -> str:
    cfg = get_regime_config()
    if inputs.hs300_ret_60d > cfg.spread_ret_min and inputs.breadth_ratio >= inputs.breadth_p75_90d:
        return "spread"
    if inputs.hs300_ret_60d > cfg.climax_ret_min and inputs.hs300_vol_20d > cfg.climax_vol_min:
        return "climax"
    if inputs.hs300_ret_60d < cfg.fade_ret_max or inputs.breadth_ratio < cfg.breadth_floor:
        return "fade"
    return "init"


def _guard_regime_payload(payload: dict[str, Any]) -> None:
    cfg = get_regime_config()
    score = payload["regime_score"]
    if abs(score) > cfg.regime_score_abs_max:
        raise ValueError(f"regime_score leakage guard triggered: {score}")


def _validate_snapshot_date(conn, day: date) -> None:
    today = date.today()
    if day >= today:
        raise ValueError(f"snapshot_date={day} must be earlier than today={today}")
    row = _fetchone(
        conn,
        """
        SELECT is_trading
          FROM dim_trading_calendar
         WHERE CAST(trade_date AS DATE) = ?
         LIMIT 1
        """,
        [day.isoformat()],
    )
    if not row or int(row["is_trading"]) != 1:
        raise ValueError(f"snapshot_date={day} is not a trading day in dim_trading_calendar")


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
    return [_to_date(r["trade_date"]) for r in rows]


def _attach_market_if_available(conn) -> None:
    if _table_exists(conn, "mart_index_daily") and _table_exists(conn, "fact_stock_kline_daily"):
        return
    if not MARKET_DB.exists():
        return
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{_sql_path(MARKET_DB)}' AS market (READ_ONLY)")
    except Exception as exc:
        logger.warning("market.duckdb attach failed: %s", exc)


def _table_exists(conn, table: str) -> bool:
    row = _fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = ?",
        [table],
    )
    return bool(row and int(row["n"]) > 0)


def _columns(conn, table: str) -> set[str]:
    return {r["column_name"] for r in _fetchall(conn, f"DESCRIBE {table}")}


def _first_existing(cols: set[str], names: list[str], required: bool = True) -> str | None:
    for name in names:
        if name in cols:
            return name
    if required:
        raise ValueError(f"required columns missing; expected one of {names}, got {sorted(cols)}")
    return None


def _fetchone(conn, sql: str, params: list[Any] | None = None):
    cur = conn.execute(sql, params or [])
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description] if getattr(cur, "description", None) else []
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(zip(cols, row))


def _fetchall(conn, sql: str, params: list[Any] | None = None):
    cur = conn.execute(sql, params or [])
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if getattr(cur, "description", None) else []
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            out.append(dict(zip(cols, row)))
    return out


def _to_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _clip(value: float, lo: float, hi: float) -> float:
    if math.isnan(value):
        raise ValueError("regime input produced NaN")
    return max(lo, min(hi, float(value)))


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


@lru_cache(maxsize=1)
def get_regime_config() -> RegimeConfig:
    """Load P1 MarketRegimeEngine config from yaml."""
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    section = raw.get("regime_engine") or {}

    def value(path: list[str]) -> Any:
        node: Any = section
        for key in path:
            node = node[key]
        if isinstance(node, dict) and "value" in node:
            return node["value"]
        return node

    return RegimeConfig(
        hs300_code=str(value(["hs300_code"])),
        ret_days=int(value(["lookback", "ret_days"])),
        vol_days=int(value(["lookback", "vol_days"])),
        breadth_p75_days=int(value(["lookback", "breadth_p75_days"])),
        query_days=int(value(["lookback", "query_days"])),
        trend_scale=float(value(["score", "trend_scale"])),
        vol_reference=float(value(["score", "vol_reference"])),
        breadth_floor=float(value(["score", "breadth_floor"])),
        trend_weight=float(value(["score", "weights", "trend"])),
        volatility_weight=float(value(["score", "weights", "volatility"])),
        breadth_weight=float(value(["score", "weights", "breadth"])),
        low_vol_max=float(value(["volatility_buckets", "low_max"])),
        normal_vol_max=float(value(["volatility_buckets", "normal_max"])),
        high_vol_max=float(value(["volatility_buckets", "high_max"])),
        spread_ret_min=float(value(["sentiment", "spread_ret_min"])),
        climax_ret_min=float(value(["sentiment", "climax_ret_min"])),
        climax_vol_min=float(value(["sentiment", "climax_vol_min"])),
        fade_ret_max=float(value(["sentiment", "fade_ret_max"])),
        regime_score_abs_max=float(value(["guards", "regime_score_abs_max"])),
    )
