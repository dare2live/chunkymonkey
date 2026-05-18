"""Main-money capital-flow alpha for Scheme 7."""

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
    table_columns,
    table_exists,
    universe_clause,
)


class CapitalFlowAlpha:
    """PIT capital-flow features with raw main-flow fields when available."""

    FEATURE_COLUMNS = [
        "capital_main_net_amount_5d",
        "capital_main_net_amount_10d",
        "capital_main_net_pct_5d",
        "capital_main_net_pct_10d",
        "capital_inst_net_amount_5d",
        "capital_inst_net_amount_10d",
        "capital_retail_net_amount_5d",
        "capital_retail_net_amount_10d",
        "capital_inst_retail_divergence_5d",
        "capital_inst_retail_divergence_10d",
        "capital_flow_score",
    ]

    def __init__(self, conn=None):
        self.conn = conn or open_smart_conn(read_only=True)
        self._own_conn = conn is None

    def close(self) -> None:
        if self._own_conn:
            self.conn.close()

    def get_features(self, signal_date, universe: list[str] | None = None) -> pd.DataFrame:
        signal = normalize_signal_date(signal_date)
        if table_exists(self.conn, "raw_fund_flow_daily") and self._raw_flow_supported():
            features = self._raw_fund_flow_features(signal, universe)
        elif table_exists(self.conn, "fact_capital_flow_pit_daily"):
            features = self._pit_proxy_features(signal, universe)
        else:
            return empty_features(universe, self.FEATURE_COLUMNS)

        features = complete_universe(features, universe, self.FEATURE_COLUMNS[:-1])
        features["capital_flow_score"] = self._score(features)
        return complete_universe(features, universe, self.FEATURE_COLUMNS)

    def _raw_flow_supported(self) -> bool:
        cols = table_columns(self.conn, "raw_fund_flow_daily")
        return {
            "main_net_amount",
            "main_net_pct",
            "super_large_net_amount",
            "large_net_amount",
            "small_net_amount",
        }.issubset(cols)

    def _raw_fund_flow_features(self, signal: str, universe: list[str] | None) -> pd.DataFrame:
        dt = date_expr("trade_date")
        clause, params = universe_clause(universe)
        sql = f"""
            WITH flow AS (
                SELECT stock_code,
                       {dt} AS trade_dt,
                       COALESCE(main_net_amount, 0) AS main_net_amount,
                       COALESCE(main_net_pct, 0) AS main_net_pct,
                       COALESCE(super_large_net_amount, 0) + COALESCE(large_net_amount, 0) AS inst_net_amount,
                       COALESCE(small_net_amount, 0) AS retail_net_amount
                  FROM raw_fund_flow_daily
                 WHERE {dt} <= CAST(? AS DATE)
                   AND {dt} > CAST(? AS DATE) - INTERVAL '10 days'
                   {clause}
            )
            SELECT stock_code,
                   SUM(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN main_net_amount ELSE 0 END) AS capital_main_net_amount_5d,
                   SUM(main_net_amount) AS capital_main_net_amount_10d,
                   AVG(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN main_net_pct END) AS capital_main_net_pct_5d,
                   AVG(main_net_pct) AS capital_main_net_pct_10d,
                   SUM(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN inst_net_amount ELSE 0 END) AS capital_inst_net_amount_5d,
                   SUM(inst_net_amount) AS capital_inst_net_amount_10d,
                   SUM(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN retail_net_amount ELSE 0 END) AS capital_retail_net_amount_5d,
                   SUM(retail_net_amount) AS capital_retail_net_amount_10d
              FROM flow
             GROUP BY stock_code
        """
        df = fetch_df(
            self.conn,
            sql,
            [signal, signal, *params, signal, signal, signal, signal],
        )
        return self._add_divergence(df)

    def _pit_proxy_features(self, signal: str, universe: list[str] | None) -> pd.DataFrame:
        dt = date_expr("trade_date")
        clause, params = universe_clause(universe)
        sql = f"""
            WITH flow AS (
                SELECT stock_code,
                       {dt} AS trade_dt,
                       COALESCE(exec_net_signal, 0) AS main_proxy,
                       COALESCE(exec_buy_pct_60d, 0) - COALESCE(exec_sell_pct_60d, 0) AS inst_proxy,
                       COALESCE(lhb_net_buy_pct_30d, 0) AS pct_proxy
                  FROM fact_capital_flow_pit_daily
                 WHERE {dt} <= CAST(? AS DATE)
                   AND {dt} > CAST(? AS DATE) - INTERVAL '10 days'
                   {clause}
            )
            SELECT stock_code,
                   SUM(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN main_proxy ELSE 0 END) AS capital_main_net_amount_5d,
                   SUM(main_proxy) AS capital_main_net_amount_10d,
                   AVG(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN pct_proxy END) AS capital_main_net_pct_5d,
                   AVG(pct_proxy) AS capital_main_net_pct_10d,
                   SUM(CASE WHEN trade_dt > CAST(? AS DATE) - INTERVAL '5 days'
                            THEN inst_proxy ELSE 0 END) AS capital_inst_net_amount_5d,
                   SUM(inst_proxy) AS capital_inst_net_amount_10d,
                   0.0 AS capital_retail_net_amount_5d,
                   0.0 AS capital_retail_net_amount_10d
              FROM flow
             GROUP BY stock_code
        """
        df = fetch_df(
            self.conn,
            sql,
            [signal, signal, *params, signal, signal, signal],
        )
        return self._add_divergence(df)

    @staticmethod
    def _add_divergence(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if out.empty:
            out["capital_inst_retail_divergence_5d"] = pd.Series(dtype="float64")
            out["capital_inst_retail_divergence_10d"] = pd.Series(dtype="float64")
            return out
        for col in [
            "capital_inst_net_amount_5d",
            "capital_inst_net_amount_10d",
            "capital_retail_net_amount_5d",
            "capital_retail_net_amount_10d",
        ]:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        out["capital_inst_retail_divergence_5d"] = (
            out["capital_inst_net_amount_5d"] / (out["capital_retail_net_amount_5d"].abs() + 1.0)
        )
        out["capital_inst_retail_divergence_10d"] = (
            out["capital_inst_net_amount_10d"] / (out["capital_retail_net_amount_10d"].abs() + 1.0)
        )
        return out

    @staticmethod
    def _score(df: pd.DataFrame) -> pd.Series:
        main5 = df["capital_main_net_amount_5d"]
        main10 = df["capital_main_net_amount_10d"]
        scaled_main = (
            np.sign(main5) * np.log1p(np.abs(main5) / 10_000_000.0)
            + 0.5 * np.sign(main10) * np.log1p(np.abs(main10) / 20_000_000.0)
        )
        divergence = 0.7 * df["capital_inst_retail_divergence_5d"].clip(-5, 5)
        pct = 0.1 * df["capital_main_net_pct_5d"].clip(-20, 20)
        return scaled_main + divergence + pct
