"""Shared SQL fragments for executable qfq pricing."""
from __future__ import annotations


def qfq_vwap_expr(
    *,
    amount: str,
    volume: str,
    close: str,
    factor: str | None = None,
) -> str:
    """Return a guarded qfq VWAP expression.

    TDX volume can be share- or hand-based depending on source path. For qfq
    rows, hand-based VWAP must also be scaled by the row's qfq factor.
    """

    raw_vwap = f"(CAST({amount} AS DOUBLE) / NULLIF(CAST({volume} AS DOUBLE), 0))"
    hand_vwap = f"({raw_vwap} / 100.0)"
    qfq_factor = f"COALESCE(NULLIF(CAST({factor} AS DOUBLE), 0), 1.0)" if factor else "1.0"
    factor_vwap = f"({hand_vwap} * {qfq_factor})"
    factor_needed = f"ABS({qfq_factor} - 1.0) > 1e-9"
    close_value = f"CAST({close} AS DOUBLE)"
    return f"""
        CASE
          WHEN {amount} IS NOT NULL AND {volume} IS NOT NULL
           AND CAST({amount} AS DOUBLE) > 0 AND CAST({volume} AS DOUBLE) > 0
           AND {close_value} > 0
           AND {raw_vwap} / {close_value} BETWEEN 0.5 AND 1.5
          THEN {raw_vwap}
          WHEN {amount} IS NOT NULL AND {volume} IS NOT NULL
           AND CAST({amount} AS DOUBLE) > 0 AND CAST({volume} AS DOUBLE) > 0
           AND {close_value} > 0
           AND {factor_needed}
           AND {factor_vwap} / {close_value} BETWEEN 0.5 AND 1.5
          THEN {factor_vwap}
          WHEN {amount} IS NOT NULL AND {volume} IS NOT NULL
           AND CAST({amount} AS DOUBLE) > 0 AND CAST({volume} AS DOUBLE) > 0
           AND {close_value} > 0
           AND {hand_vwap} / {close_value} BETWEEN 0.5 AND 1.5
          THEN {hand_vwap}
          ELSE NULL
        END
    """


def qfq_vwap_method_expr(
    *,
    amount: str,
    volume: str,
    close: str,
    factor: str | None = None,
    raw_method: str = "signal_day_vwap_qfq",
    factor_method: str = "signal_day_vwap_qfq_volume_hand_factor_adjusted",
    hand_method: str = "signal_day_vwap_qfq_volume_hand_adjusted",
) -> str:
    raw_vwap = f"(CAST({amount} AS DOUBLE) / NULLIF(CAST({volume} AS DOUBLE), 0))"
    hand_vwap = f"({raw_vwap} / 100.0)"
    qfq_factor = f"COALESCE(NULLIF(CAST({factor} AS DOUBLE), 0), 1.0)" if factor else "1.0"
    factor_vwap = f"({hand_vwap} * {qfq_factor})"
    factor_needed = f"ABS({qfq_factor} - 1.0) > 1e-9"
    close_value = f"CAST({close} AS DOUBLE)"
    return f"""
        CASE
          WHEN {amount} IS NOT NULL AND {volume} IS NOT NULL
           AND CAST({amount} AS DOUBLE) > 0 AND CAST({volume} AS DOUBLE) > 0
           AND {close_value} > 0
           AND {raw_vwap} / {close_value} BETWEEN 0.5 AND 1.5
          THEN '{raw_method}'
          WHEN {amount} IS NOT NULL AND {volume} IS NOT NULL
           AND CAST({amount} AS DOUBLE) > 0 AND CAST({volume} AS DOUBLE) > 0
           AND {close_value} > 0
           AND {factor_needed}
           AND {factor_vwap} / {close_value} BETWEEN 0.5 AND 1.5
          THEN '{factor_method}'
          WHEN {amount} IS NOT NULL AND {volume} IS NOT NULL
           AND CAST({amount} AS DOUBLE) > 0 AND CAST({volume} AS DOUBLE) > 0
           AND {close_value} > 0
           AND {hand_vwap} / {close_value} BETWEEN 0.5 AND 1.5
          THEN '{hand_method}'
          ELSE NULL
        END
    """
