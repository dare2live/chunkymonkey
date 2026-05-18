"""Northbound holding alpha for Scheme 7."""

from __future__ import annotations

import numpy as np
import pandas as pd

from services.strategies.institution_follow._common import (
    complete_universe,
    date_expr,
    empty_features,
    fetch_df,
    normalize_signal_date,
    open_smart_conn,
    table_exists,
    universe_clause,
)


class NorthboundAlpha:
    """PIT northbound features from fact_hsgt_daily when available."""

    FEATURE_COLUMNS = [
        "nb_holding_pct",
        "nb_holding_chg_30d",
        "northbound_hold_market_value",
        "northbound_hold_pct_float",
        "northbound_hold_value_delta_30d",
        "northbound_hold_pct_delta_30d",
        "northbound_score",
    ]

    def __init__(self, conn=None):
        self.conn = conn or open_smart_conn(read_only=True)
        self._own_conn = conn is None

    def close(self) -> None:
        if self._own_conn:
            self.conn.close()

    def get_features(self, signal_date, universe: list[str] | None = None) -> pd.DataFrame:
        signal = normalize_signal_date(signal_date)
        if not table_exists(self.conn, "fact_hsgt_daily"):
            return empty_features(universe, self.FEATURE_COLUMNS)
        features = self._features(signal, universe)
        features = complete_universe(features, universe, self.FEATURE_COLUMNS[:-1])
        features["northbound_score"] = self._score(features)
        return complete_universe(features, universe, self.FEATURE_COLUMNS)

    def _features(self, signal: str, universe: list[str] | None) -> pd.DataFrame:
        dt = date_expr("snapshot_date")
        clause, params = universe_clause(universe)
        sql = f"""
            WITH latest_key AS (
                SELECT stock_code, MAX({dt}) AS snapshot_dt
                  FROM fact_hsgt_daily
                 WHERE {dt} < CAST(? AS DATE)
                   {clause}
                 GROUP BY stock_code
            ),
            latest AS (
                SELECT h.stock_code,
                       COALESCE(h.hold_market_value, 0) AS hold_market_value,
                       COALESCE(h.hold_pct_of_float, 0) AS hold_pct_of_float
                  FROM fact_hsgt_daily h
                  JOIN latest_key lk
                    ON lk.stock_code = h.stock_code
                   AND lk.snapshot_dt = {date_expr("h.snapshot_date")}
            ),
            prev_key AS (
                SELECT stock_code, MAX({dt}) AS snapshot_dt
                  FROM fact_hsgt_daily
                 WHERE {dt} < CAST(? AS DATE) - INTERVAL '30 days'
                   {clause}
                 GROUP BY stock_code
            ),
            prev AS (
                SELECT h.stock_code,
                       COALESCE(h.hold_market_value, 0) AS hold_market_value,
                       COALESCE(h.hold_pct_of_float, 0) AS hold_pct_of_float
                  FROM fact_hsgt_daily h
                  JOIN prev_key pk
                    ON pk.stock_code = h.stock_code
                   AND pk.snapshot_dt = {date_expr("h.snapshot_date")}
            )
            SELECT l.stock_code,
                   l.hold_market_value AS northbound_hold_market_value,
                   l.hold_pct_of_float AS northbound_hold_pct_float,
                   l.hold_market_value - COALESCE(p.hold_market_value, l.hold_market_value)
                       AS northbound_hold_value_delta_30d,
                   l.hold_pct_of_float - COALESCE(p.hold_pct_of_float, l.hold_pct_of_float)
                       AS northbound_hold_pct_delta_30d,
                   l.hold_pct_of_float AS nb_holding_pct,
                   l.hold_pct_of_float - COALESCE(p.hold_pct_of_float, l.hold_pct_of_float)
                       AS nb_holding_chg_30d
              FROM latest l
              LEFT JOIN prev p ON p.stock_code = l.stock_code
        """
        try:
            return fetch_df(self.conn, sql, [signal, *params, signal, *params])
        except Exception:
            return pd.DataFrame(columns=self.FEATURE_COLUMNS[:-1])

    @staticmethod
    def _score(df: pd.DataFrame) -> pd.Series:
        value_delta = pd.to_numeric(df["northbound_hold_value_delta_30d"], errors="coerce").fillna(0.0)
        pct_delta = pd.to_numeric(df["northbound_hold_pct_delta_30d"], errors="coerce").fillna(0.0)
        pct = pd.to_numeric(df["northbound_hold_pct_float"], errors="coerce").fillna(0.0)
        return (
            np.sign(value_delta) * np.log1p(np.abs(value_delta) / 100_000_000.0)
            + 0.5 * pct_delta
            + 0.05 * pct
        )
