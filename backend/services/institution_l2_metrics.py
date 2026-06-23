"""SQL builders for institution L2 expertise metrics.

These replace direct runtime dependencies on historical compatibility views
while preserving the current PIT cohort semantics.
"""
from __future__ import annotations


COHORT_SCHEME = "institution_L2_pit_20240930"


def institution_l2_score_cte(name: str = "institution_l2_score") -> str:
    """Return a CTE for the historical institution L2 score contract."""
    return f"""
    {name} AS (
      WITH train_best AS (
        SELECT cohort_key, entry_lag, max_hold_days, stop_loss, take_profit,
               sharpe AS train_sharpe, avg_pnl AS train_pnl,
               win_rate AS train_wr, n_filled AS train_n,
               ROW_NUMBER() OVER (PARTITION BY cohort_key ORDER BY sharpe DESC) AS rn
          FROM fact_institution_follow_backtest
         WHERE cohort_scheme = '{COHORT_SCHEME}'
           AND split = 'train'
      ),
      paired AS (
        SELECT tb.cohort_key,
               SUBSTR(tb.cohort_key, 1, INSTR(tb.cohort_key, '|') - 1) AS institution_id,
               SUBSTR(tb.cohort_key, INSTR(tb.cohort_key, '|') + 1) AS l2_name,
               tb.entry_lag, tb.max_hold_days, tb.stop_loss, tb.take_profit,
               tb.train_sharpe, tb.train_n, tb.train_pnl, tb.train_wr,
               ho.sharpe AS ho_sharpe, ho.avg_pnl AS ho_pnl,
               ho.win_rate AS ho_wr, ho.n_filled AS ho_n
          FROM train_best tb
          LEFT JOIN fact_institution_follow_backtest ho
            ON tb.cohort_key = ho.cohort_key
           AND ho.split = 'holdout'
           AND ho.cohort_scheme = '{COHORT_SCHEME}'
           AND tb.entry_lag = ho.entry_lag
           AND tb.max_hold_days = ho.max_hold_days
           AND ((tb.stop_loss IS NULL AND ho.stop_loss IS NULL) OR tb.stop_loss = ho.stop_loss)
           AND ((tb.take_profit IS NULL AND ho.take_profit IS NULL) OR tb.take_profit = ho.take_profit)
         WHERE tb.rn = 1
      )
      SELECT institution_id, l2_name, cohort_key,
             entry_lag, max_hold_days, stop_loss, take_profit,
             train_n, ho_n,
             ROUND(CAST(train_sharpe AS DOUBLE), 3) AS train_sharpe,
             ROUND(CAST(train_wr AS DOUBLE), 3) AS train_win_rate,
             ROUND(CAST(ho_sharpe AS DOUBLE), 3) AS ho_sharpe,
             ROUND(CAST(ho_wr AS DOUBLE), 3) AS ho_win_rate,
             ROUND(CAST(ho_pnl AS DOUBLE), 4) AS ho_avg_pnl,
             ROUND(CAST(ho_sharpe AS DOUBLE) / NULLIF(train_sharpe, 0), 2) AS stability_ratio,
             CAST(
               ROUND(
                 100.0
                 * CASE
                     WHEN ho_sharpe IS NULL OR ho_sharpe <= 0 THEN 0
                     WHEN ho_sharpe >= 2.0 THEN 1.0
                     ELSE ho_sharpe / 2.0
                   END
                 * CASE
                     WHEN ho_sharpe IS NULL OR train_sharpe IS NULL OR train_sharpe <= 0 THEN 0
                     WHEN ho_sharpe / train_sharpe >= 1.0 THEN 1.0
                     WHEN ho_sharpe / train_sharpe <= 0 THEN 0
                     ELSE ho_sharpe / train_sharpe
                   END
                 * CASE
                     WHEN ho_n IS NULL OR ho_n <= 0 THEN 0
                     WHEN ho_n >= 30 THEN 1.0
                     ELSE CAST(ho_n AS DOUBLE) / 30.0
                   END,
                 1
               ) AS FLOAT
             ) AS stable_score,
             CASE
               WHEN ho_sharpe IS NULL OR ho_sharpe < 0 THEN 'overfit'
               WHEN ho_sharpe >= 1.0 AND ho_sharpe >= 0.7 * train_sharpe AND ho_n >= 15 THEN 'stable'
               WHEN ho_sharpe >= 0.5 THEN 'weak_positive'
               ELSE 'neutral'
             END AS verdict
        FROM paired
    )
    """


def l2_profile_ctes(score_name: str = "institution_l2_score", profile_name: str = "l2_profile") -> str:
    """Return CTEs for the historical L2 profile contract."""
    return f"""
    {institution_l2_score_cte(score_name)},
    stocks_in_l2 AS (
      SELECT tdx_l2_name AS l2_name,
             COUNT(DISTINCT stock_code) AS n_stocks
        FROM dim_stock_dc_industry
       WHERE tdx_l2_name IS NOT NULL
         AND tdx_l2_name != ''
       GROUP BY tdx_l2_name
    ),
    l2_scores AS (
      SELECT l2_name,
             COUNT(*) AS n_insts_with_score,
             SUM(CASE WHEN verdict = 'stable' THEN 1 ELSE 0 END) AS n_stable,
             SUM(CASE WHEN verdict = 'weak_positive' THEN 1 ELSE 0 END) AS n_weak_positive,
             SUM(CASE WHEN verdict = 'overfit' THEN 1 ELSE 0 END) AS n_overfit,
             ROUND(MAX(stable_score), 1) AS top_score,
             ROUND(AVG(stable_score), 1) AS avg_score,
             ROUND(AVG(CASE WHEN verdict = 'stable' THEN stable_score ELSE NULL END), 1) AS avg_stable_score
        FROM {score_name}
       GROUP BY l2_name
    ),
    {profile_name} AS (
      SELECT s.l2_name,
             s.n_stocks,
             COALESCE(ls.n_insts_with_score, 0) AS n_insts_with_score,
             COALESCE(ls.n_stable, 0) AS n_stable,
             COALESCE(ls.n_weak_positive, 0) AS n_weak_positive,
             COALESCE(ls.n_overfit, 0) AS n_overfit,
             ls.top_score,
             ls.avg_score,
             ls.avg_stable_score
        FROM stocks_in_l2 s
        LEFT JOIN l2_scores ls ON s.l2_name = ls.l2_name
    )
    """
