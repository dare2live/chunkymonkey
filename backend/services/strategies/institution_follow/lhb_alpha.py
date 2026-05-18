"""LHB institution net-buy alpha for Scheme 7.

The source table has no separate announce_date column. LHB rows are daily public
events, so this module treats fact_lhb_event.trade_date as the event availability
date and always filters it with trade_date <= signal_date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.strategies.institution_follow._common import (
    complete_universe,
    date_expr,
    empty_features,
    ensure_market_attached,
    fetch_df,
    normalize_signal_date,
    open_smart_conn,
    table_exists,
    universe_clause,
)


class LHBAlpha:
    """Point-in-time LHB institutional net-buy features."""

    FEATURE_COLUMNS = [
        "lhb_event_count_30d",
        "lhb_inst_net_buy_count_30d",
        "lhb_inst_net_buy_amount_30d",
        "lhb_net_buy_pct_sum_30d",
        "lhb_recency_days",
        "lhb_excess_return_5d",
        "lhb_excess_return_10d",
        "lhb_excess_return_20d",
        "lhb_score",
    ]

    def __init__(self, conn=None, price_table: str = "market.v_price_kline_qfq"):
        self.conn = conn or open_smart_conn(read_only=True)
        self._own_conn = conn is None
        self.price_table = price_table
        if "." in price_table:
            ensure_market_attached(self.conn)

    def close(self) -> None:
        if self._own_conn:
            self.conn.close()

    def get_features(self, signal_date, universe: list[str] | None = None) -> pd.DataFrame:
        """Return one feature row per stock_code using only events <= signal_date."""
        signal = normalize_signal_date(signal_date)
        if not table_exists(self.conn, "fact_lhb_event"):
            return empty_features(universe, self.FEATURE_COLUMNS)

        recent = self._recent_event_features(signal, universe)
        drift = self._historical_drift_features(signal, universe)
        features = recent.merge(drift, on="stock_code", how="outer")
        features = complete_universe(features, universe, self.FEATURE_COLUMNS[:-1])
        features["lhb_score"] = self._score(features)
        return complete_universe(features, universe, self.FEATURE_COLUMNS)

    def _recent_event_features(self, signal: str, universe: list[str] | None) -> pd.DataFrame:
        dt = date_expr("trade_date")
        clause, params = universe_clause(universe)
        sql = f"""
            WITH events AS (
                SELECT stock_code,
                       {dt} AS announce_dt,
                       COALESCE(is_inst_net_buy, 0) AS is_inst_net_buy,
                       COALESCE(net_buy, 0) AS net_buy,
                       COALESCE(net_buy_pct, 0) AS net_buy_pct
                  FROM fact_lhb_event
                 WHERE {dt} <= CAST(? AS DATE)
                   AND {dt} > CAST(? AS DATE) - INTERVAL '30 days'
                   {clause}
            )
            SELECT stock_code,
                   COUNT(*) AS lhb_event_count_30d,
                   SUM(CASE WHEN is_inst_net_buy = 1 THEN 1 ELSE 0 END) AS lhb_inst_net_buy_count_30d,
                   SUM(CASE WHEN is_inst_net_buy = 1 THEN net_buy ELSE 0 END) AS lhb_inst_net_buy_amount_30d,
                   SUM(CASE WHEN is_inst_net_buy = 1 THEN net_buy_pct ELSE 0 END) AS lhb_net_buy_pct_sum_30d,
                   date_diff('day', MAX(announce_dt), CAST(? AS DATE)) AS lhb_recency_days
              FROM events
             GROUP BY stock_code
        """
        return fetch_df(self.conn, sql, [signal, signal, *params, signal])

    def _historical_drift_features(self, signal: str, universe: list[str] | None) -> pd.DataFrame:
        """Compute realized post-event excess drift for fully observed past LHB events."""
        dt = date_expr("e.trade_date")
        clause, params = universe_clause(universe, "e.stock_code")
        price = self.price_table
        sql = f"""
            WITH calendar AS (
                SELECT date,
                       close AS bench_close,
                       ROW_NUMBER() OVER (ORDER BY date) AS rn
                  FROM {price}
                 WHERE code = '000300'
                   AND adjust = 'qfq'
                   AND freq = 'daily'
                   AND date <= ?
            ),
            events AS (
                SELECT e.stock_code,
                       {dt} AS event_date
                  FROM fact_lhb_event e
                 WHERE {dt} <= CAST(? AS DATE)
                   AND {dt} > CAST(? AS DATE) - INTERVAL '420 days'
                   AND COALESCE(e.is_inst_net_buy, 0) = 1
                   {clause}
            ),
            event_idx AS (
                SELECT e.stock_code, e.event_date, c.rn AS event_rn, c.bench_close AS bench_0
                  FROM events e
                  JOIN calendar c ON c.date = CAST(e.event_date AS VARCHAR)
            ),
            returns AS (
                SELECT e.stock_code,
                       CASE
                           WHEN p0.close > 0 AND p5.close > 0 AND c5.bench_close > 0 AND e.bench_0 > 0
                           THEN (p5.close / p0.close - 1) - (c5.bench_close / e.bench_0 - 1)
                       END AS ex5,
                       CASE
                           WHEN p0.close > 0 AND p10.close > 0 AND c10.bench_close > 0 AND e.bench_0 > 0
                           THEN (p10.close / p0.close - 1) - (c10.bench_close / e.bench_0 - 1)
                       END AS ex10,
                       CASE
                           WHEN p0.close > 0 AND p20.close > 0 AND c20.bench_close > 0 AND e.bench_0 > 0
                           THEN (p20.close / p0.close - 1) - (c20.bench_close / e.bench_0 - 1)
                       END AS ex20
                  FROM event_idx e
                  JOIN {price} p0
                    ON p0.code = e.stock_code
                   AND p0.date = CAST(e.event_date AS VARCHAR)
                   AND p0.adjust = 'qfq'
                   AND p0.freq = 'daily'
                  LEFT JOIN calendar c5 ON c5.rn = e.event_rn + 5
                  LEFT JOIN calendar c10 ON c10.rn = e.event_rn + 10
                  LEFT JOIN calendar c20 ON c20.rn = e.event_rn + 20
                  LEFT JOIN {price} p5
                    ON p5.code = e.stock_code AND p5.date = c5.date
                   AND p5.adjust = 'qfq' AND p5.freq = 'daily'
                  LEFT JOIN {price} p10
                    ON p10.code = e.stock_code AND p10.date = c10.date
                   AND p10.adjust = 'qfq' AND p10.freq = 'daily'
                  LEFT JOIN {price} p20
                    ON p20.code = e.stock_code AND p20.date = c20.date
                   AND p20.adjust = 'qfq' AND p20.freq = 'daily'
            )
            SELECT stock_code,
                   AVG(ex5) AS lhb_excess_return_5d,
                   AVG(ex10) AS lhb_excess_return_10d,
                   AVG(ex20) AS lhb_excess_return_20d
              FROM returns
             GROUP BY stock_code
        """
        try:
            return fetch_df(self.conn, sql, [signal, signal, signal, *params])
        except Exception:
            return pd.DataFrame(columns=[
                "stock_code",
                "lhb_excess_return_5d",
                "lhb_excess_return_10d",
                "lhb_excess_return_20d",
            ])

    @staticmethod
    def _score(df: pd.DataFrame) -> pd.Series:
        amount = np.sign(df["lhb_inst_net_buy_amount_30d"]) * np.log1p(
            np.abs(df["lhb_inst_net_buy_amount_30d"]) / 10_000_000.0
        )
        recency = np.where(df["lhb_event_count_30d"] > 0, 1.0 / (1.0 + df["lhb_recency_days"] / 10.0), 0.0)
        drift = df[[
            "lhb_excess_return_5d",
            "lhb_excess_return_10d",
            "lhb_excess_return_20d",
        ]].mean(axis=1).clip(-0.2, 0.2)
        return (
            0.7 * df["lhb_inst_net_buy_count_30d"]
            + 0.4 * amount
            + 0.2 * df["lhb_net_buy_pct_sum_30d"].clip(-20, 20)
            + 1.0 * recency
            + 5.0 * drift
        )
