"""Stage filter for bc_absorbed daily picks (Phase 2.5).

Per goal.md MASTER_SYNTHESIS Phase 2.5: Wyckoff Stage {1.5, 2, 3} positive IC.

V4 ablation evidence (commit 2175c9fa):
- Stage 1.5 IC +0.081 IR +0.45 pos% 66% (best alpha)
- Stage 2 IC -0.001 (neutral)
- Stage 3 IC +0.010 (weak positive)
- Stage 1 IC -0.013 (neg)
- Stage 4 IC -0.021 (bad)

Apply to BC daily picks: keep only signal_dates where stock in Stage {1.5, 2, 3}.

Usage:
    from services.bc_absorbed.stage_filter import filter_by_stage, get_positive_stages
    picks = filter_by_stage(picks_df, conn, positive_stages=get_positive_stages())
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

# rule-compliance: ok evidence=V4 per-stage ablation Stage 1.5/2/3 positive IC (commit 2175c9fa)
DEFAULT_POSITIVE_STAGES = ("1.5", "2", "3")


def get_positive_stages() -> tuple[str, ...]:
    """Stages with positive V4 OOS IC per ablation."""
    return DEFAULT_POSITIVE_STAGES


def filter_by_stage(
    picks: pd.DataFrame,
    conn,
    *,
    positive_stages: Iterable[str] = DEFAULT_POSITIVE_STAGES,
    signal_date_col: str = "signal_date",
    stock_code_col: str = "stock_code",
) -> pd.DataFrame:
    """Filter picks DataFrame to only rows where (stock_code, signal_date) in positive stage.

    Args:
        picks: DataFrame with at least signal_date_col + stock_code_col
        conn: DuckDB read connection
        positive_stages: tuple of stage values to keep (default Stage 1.5/2/3)
        signal_date_col: column name in picks DataFrame for signal date
        stock_code_col: column name in picks DataFrame for stock code

    Returns:
        Filtered DataFrame (same columns, subset of rows)
    """
    if picks.empty:
        return picks

    stages_csv = ",".join(f"'{s}'" for s in positive_stages)
    # Query fact_stock_technical_stage for positive stage stock-date pairs
    stage_filter = conn.execute(f"""
        SELECT stock_code, date AS signal_date
          FROM fact_stock_technical_stage
         WHERE stage IN ({stages_csv})
           AND date >= ?
           AND date <= ?
    """, [str(picks[signal_date_col].min()), str(picks[signal_date_col].max())]).fetchdf()
    stage_filter[signal_date_col] = pd.to_datetime(stage_filter[signal_date_col])
    if signal_date_col in picks.columns:
        picks = picks.copy()
        picks[signal_date_col] = pd.to_datetime(picks[signal_date_col])

    # Inner merge keeps only picks where (stock_code, signal_date) in positive stage
    filtered = picks.merge(
        stage_filter[[stock_code_col, signal_date_col]],
        on=[stock_code_col, signal_date_col],
        how="inner",
    )
    return filtered


def summary_per_stage(conn, *, period_start: str = "2024-01-02", period_end: str = "2026-04-13") -> dict:
    """Quick stage distribution over period."""
    rows = conn.execute("""
        SELECT stage, COUNT(*) AS n
          FROM fact_stock_technical_stage
         WHERE date >= ? AND date <= ? AND stage IS NOT NULL
         GROUP BY stage
         ORDER BY stage
    """, [period_start, period_end]).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}
