"""Northbound holding alpha for Scheme 7."""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

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

log = logging.getLogger("northbound_alpha")

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "institution_alpha.yaml"


@lru_cache(maxsize=1)
def _max_staleness_days(config_path: str | None = None) -> int:
    """Read the fact_hsgt_daily staleness threshold (calendar days) from yaml.

    Rules-in-yaml (CLAUDE.md §3): the threshold is config-owned, not hardcoded.
    """
    path = Path(config_path) if config_path else _CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    value = (raw.get("northbound") or {}).get("max_staleness_days")
    if value is None:
        raise ValueError(f"{path.name}: northbound.max_staleness_days is required")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{path.name}: northbound.max_staleness_days must be a positive number")
    return int(value)


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

    def __init__(self, conn=None, max_staleness_days: int | None = None):
        self.conn = conn or open_smart_conn(read_only=True)
        self._own_conn = conn is None
        # yaml-back threshold; explicit override allowed for tests.
        self.max_staleness_days = (
            int(max_staleness_days) if max_staleness_days is not None else _max_staleness_days()
        )

    def close(self) -> None:
        if self._own_conn:
            self.conn.close()

    def _unknown_features(self, universe: list[str] | None) -> pd.DataFrame:
        """source 缺失/过期时的 unknown frame: score 列 = NaN, 不是 0.0.

        2026-06-11 Fable-5 复查修正: 旧版返回 empty_features (全 0.0). 但 0.0 在
        compose_signal_date_scores 里被 normalize 成 0.0 (notna), _prepare_class_frame
        当作合格类计入 eligible_norm_cols, 稀释 composite_score + 虚增 n_classes_eligible
        (§4 "unknown 当 0 偷偷参与" 反模式). 把 score 列设 NaN 后, normalize valid=False
        → norm NaN → 该类被 compose 完全排除 (不参与均值, 不计 eligible).
        """
        frame = empty_features(universe, self.FEATURE_COLUMNS)
        frame["northbound_score"] = np.nan
        return frame

    def get_features(self, signal_date, universe: list[str] | None = None) -> pd.DataFrame:
        signal = normalize_signal_date(signal_date)
        if not table_exists(self.conn, "fact_hsgt_daily"):
            return self._unknown_features(universe)
        if self._is_stale(signal, universe):
            # Deprecated source past freshness budget: emit unknown (score=NaN) so the
            # class is EXCLUDED from the composite, never a silent 2-year-old 0.0 vote.
            return self._unknown_features(universe)
        features = self._features(signal, universe)
        features = complete_universe(features, universe, self.FEATURE_COLUMNS[:-1])
        features["northbound_score"] = self._score(features)
        return complete_universe(features, universe, self.FEATURE_COLUMNS)

    def _latest_snapshot_date(self, signal: str, universe: list[str] | None) -> date | None:
        """Most recent snapshot strictly before signal_date, or None if absent."""
        dt = date_expr("snapshot_date")
        clause, params = universe_clause(universe)
        sql = f"""
            SELECT MAX({dt}) AS latest_snapshot
              FROM fact_hsgt_daily
             WHERE {dt} < CAST(? AS DATE)
               {clause}
        """
        try:
            df = fetch_df(self.conn, sql, [signal, *params])
        except Exception:
            return None
        if df.empty:
            return None
        latest = df.iloc[0]["latest_snapshot"]
        if latest is None or pd.isna(latest):
            return None
        return pd.to_datetime(latest).date()

    def _is_stale(self, signal: str, universe: list[str] | None) -> bool:
        """True when the freshest available snapshot is older than the budget."""
        latest = self._latest_snapshot_date(signal, universe)
        if latest is None:
            # No usable snapshot before signal_date — downstream yields all-zero
            # features anyway; treat as unknown without a staleness warning.
            return True
        signal_dt = pd.to_datetime(signal).date()
        staleness_days = (signal_dt - latest).days
        if staleness_days > self.max_staleness_days:
            log.warning(
                "fact_hsgt_daily stale: signal_date=%s latest_snapshot=%s "
                "staleness=%sd > max=%sd -> northbound factor set to unknown (0.0)",
                signal,
                latest.isoformat(),
                staleness_days,
                self.max_staleness_days,
            )
            return True
        return False

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
