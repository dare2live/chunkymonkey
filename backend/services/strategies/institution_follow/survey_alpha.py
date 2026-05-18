"""Institution-survey heat alpha for Scheme 7."""

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


class SurveyAlpha:
    """PIT survey heat features using disclosure/notice date when present."""

    FEATURE_COLUMNS = [
        "inst_survey_count_30d",
        "inst_survey_quality_30d",
        "survey_count_30d",
        "survey_inst_count_30d",
        "survey_count_prev_30d",
        "survey_inst_count_prev_30d",
        "survey_heat_rising",
        "survey_expectation_upgrade_flag",
        "survey_score",
    ]

    def __init__(self, conn=None):
        self.conn = conn or open_smart_conn(read_only=True)
        self._own_conn = conn is None

    def close(self) -> None:
        if self._own_conn:
            self.conn.close()

    def get_features(self, signal_date, universe: list[str] | None = None) -> pd.DataFrame:
        signal = normalize_signal_date(signal_date)
        if not table_exists(self.conn, "raw_institution_surveys"):
            return empty_features(universe, self.FEATURE_COLUMNS)
        features = self._survey_features(signal, universe)
        features = complete_universe(features, universe, self.FEATURE_COLUMNS[:-1])
        features["survey_score"] = self._score(features)
        return complete_universe(features, universe, self.FEATURE_COLUMNS)

    def _survey_features(self, signal: str, universe: list[str] | None) -> pd.DataFrame:
        survey_dt = date_expr("survey_date")
        notice_dt = date_expr("notice_date")
        available_dt = f"COALESCE({notice_dt}, {survey_dt})"
        clause, params = universe_clause(universe)
        sql = f"""
            WITH survey AS (
                SELECT stock_code,
                       {survey_dt} AS survey_dt,
                       {available_dt} AS available_dt,
                       COALESCE(inst_count, 0) AS inst_count
                  FROM raw_institution_surveys
                 WHERE {survey_dt} < CAST(? AS DATE)
                   AND {available_dt} < CAST(? AS DATE)
                   AND {available_dt} > CAST(? AS DATE) - INTERVAL '60 days'
                   {clause}
            )
            SELECT stock_code,
                   SUM(CASE WHEN available_dt > CAST(? AS DATE) - INTERVAL '30 days'
                            THEN 1 ELSE 0 END) AS survey_count_30d,
                   SUM(CASE WHEN available_dt > CAST(? AS DATE) - INTERVAL '30 days'
                            THEN inst_count ELSE 0 END) AS survey_inst_count_30d,
                   AVG(CASE WHEN available_dt > CAST(? AS DATE) - INTERVAL '30 days'
                            THEN inst_count END) AS inst_survey_quality_30d,
                   SUM(CASE WHEN available_dt < CAST(? AS DATE) - INTERVAL '30 days'
                            THEN 1 ELSE 0 END) AS survey_count_prev_30d,
                   SUM(CASE WHEN available_dt < CAST(? AS DATE) - INTERVAL '30 days'
                            THEN inst_count ELSE 0 END) AS survey_inst_count_prev_30d,
                   0 AS survey_expectation_upgrade_flag
              FROM survey
             GROUP BY stock_code
        """
        df = fetch_df(
            self.conn,
            sql,
            [signal, signal, signal, *params, signal, signal, signal, signal, signal],
        )
        if df.empty:
            df["survey_heat_rising"] = pd.Series(dtype="float64")
            df["inst_survey_count_30d"] = pd.Series(dtype="float64")
            return df
        df["inst_survey_count_30d"] = pd.to_numeric(df["survey_inst_count_30d"], errors="coerce").fillna(0.0)
        df["survey_heat_rising"] = (
            (pd.to_numeric(df["survey_count_30d"], errors="coerce").fillna(0) > pd.to_numeric(df["survey_count_prev_30d"], errors="coerce").fillna(0))
            | (pd.to_numeric(df["survey_inst_count_30d"], errors="coerce").fillna(0) > pd.to_numeric(df["survey_inst_count_prev_30d"], errors="coerce").fillna(0))
        ).astype(float)
        return df

    @staticmethod
    def _score(df: pd.DataFrame) -> pd.Series:
        inst = pd.to_numeric(df["survey_inst_count_30d"], errors="coerce").fillna(0.0)
        count = pd.to_numeric(df["survey_count_30d"], errors="coerce").fillna(0.0)
        rising = pd.to_numeric(df["survey_heat_rising"], errors="coerce").fillna(0.0)
        upgrade = pd.to_numeric(df["survey_expectation_upgrade_flag"], errors="coerce").fillna(0.0)
        return np.log1p(inst) + 0.5 * np.log1p(count) + 0.75 * rising + 1.0 * upgrade
