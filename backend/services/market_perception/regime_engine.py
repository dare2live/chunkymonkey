"""PIT-strict daily market regime features for Market Perception P1."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("cm-api.market-perception.regime")

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
HS300_CODE = "000300"  # rule-compliance: ok evidence=PROJECT_INDEX §2.1 HS300 index code
TRADING_DAYS_FOR_RET = 60
TRADING_DAYS_FOR_VOL = 20
TRADING_DAYS_FOR_BREADTH_P75 = 90
TRADING_DAYS_LOOKBACK = 130


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
    rows = [compute_regime_for_date(conn, d) for d in days]
    return pd.DataFrame(rows)


def _load_inputs(conn, day: date) -> RegimeInputs:
    hs300 = _load_hs300_history(conn, day)
    if len(hs300) < TRADING_DAYS_FOR_RET + 1:
        raise ValueError(f"HS300 history before {day} has only {len(hs300)} rows")

    hs300 = hs300.sort_values("date").reset_index(drop=True)
    close_now = float(hs300.iloc[-1]["close"])
    close_60 = float(hs300.iloc[-(TRADING_DAYS_FOR_RET + 1)]["close"])
    ret_60d = close_now / close_60 - 1.0
    log_ret = (hs300["close"].astype(float) / hs300["close"].astype(float).shift(1)).map(math.log)
    vol_20d = float(log_ret.tail(TRADING_DAYS_FOR_VOL).std(ddof=1) * math.sqrt(252))

    breadth = _load_breadth_history(conn, day)
    if breadth.empty:
        raise ValueError(f"breadth history before {day} is empty")
    today = breadth[breadth["date"] == day.isoformat()]
    if today.empty:
        raise ValueError(f"breadth row missing for {day}")
    breadth_ratio = float(today.iloc[-1]["breadth_ratio"])
    p75_window = breadth.tail(TRADING_DAYS_FOR_BREADTH_P75)
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


def _load_hs300_history(conn, day: date) -> pd.DataFrame:
    if _table_exists(conn, "mart_index_daily"):
        cols = _columns(conn, "mart_index_daily")
        date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
        close_col = _first_existing(cols, ["close", "close_price"])
        code_col = _first_existing(cols, ["index_code", "code", "symbol", "ts_code"], required=False)
        where = f"CAST({date_col} AS DATE) <= ?"
        params: list[Any] = [day.isoformat()]
        if code_col:
            where += f" AND ({code_col} = ? OR lower({code_col}) = 'hs300')"
            params.append(HS300_CODE)
        rows = _fetchall(
            conn,
            f"""
            SELECT CAST({date_col} AS VARCHAR) AS date, {close_col} AS close
              FROM mart_index_daily
             WHERE {where}
             ORDER BY CAST({date_col} AS DATE) DESC
             LIMIT {TRADING_DAYS_LOOKBACK}
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
             LIMIT {TRADING_DAYS_LOOKBACK}
            """,
            [HS300_CODE, day.isoformat()],
        )
    return pd.DataFrame(rows, columns=["date", "close"])


def _load_breadth_history(conn, day: date) -> pd.DataFrame:
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
             LIMIT {TRADING_DAYS_LOOKBACK}
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
             LIMIT {TRADING_DAYS_LOOKBACK + 1}
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


def _score_regime(inputs: RegimeInputs) -> float:
    trend_score = _clip(inputs.hs300_ret_60d / 0.25, -1.0, 1.0)
    vol_score = _clip((0.25 - inputs.hs300_vol_20d) / 0.25, -1.0, 1.0)
    if inputs.breadth_ratio >= inputs.breadth_p75_90d:
        denom = max(1.0 - inputs.breadth_p75_90d, 1e-6)
        breadth_score = _clip((inputs.breadth_ratio - inputs.breadth_p75_90d) / denom, 0.0, 1.0)
    else:
        denom = max(inputs.breadth_p75_90d - 0.30, 1e-6)
        breadth_score = -_clip((inputs.breadth_p75_90d - inputs.breadth_ratio) / denom, 0.0, 1.0)
    return round(_clip(0.50 * trend_score + 0.25 * vol_score + 0.25 * breadth_score, -1.0, 1.0), 6)


def _breadth_state(breadth_ratio: float, breadth_p75_90d: float) -> str:
    if breadth_ratio < 0.30:
        return "杀跌"
    if breadth_ratio >= breadth_p75_90d:
        return "健康扩散"
    return "分化"


def _volatility_state(vol: float) -> str:
    if vol < 0.15:
        return "low"
    if vol < 0.25:
        return "normal"
    if vol < 0.40:
        return "high"
    return "extreme"


def _sentiment_phase(inputs: RegimeInputs) -> str:
    if inputs.hs300_ret_60d > 0.12 and inputs.breadth_ratio >= inputs.breadth_p75_90d:
        return "spread"
    if inputs.hs300_ret_60d > 0.18 and inputs.hs300_vol_20d > 0.30:
        return "climax"
    if inputs.hs300_ret_60d < -0.08 or inputs.breadth_ratio < 0.30:
        return "fade"
    return "init"


def _guard_regime_payload(payload: dict[str, Any]) -> None:
    score = payload["regime_score"]
    if score > 0.95 or score < -0.95:
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
