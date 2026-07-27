"""Load accepted canonical nominal OHLCV bars for institution_follow paper."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.data_sources.nominal_ohlcv_schema import CANONICAL_TABLE

_BAR_KEYS = (
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "vol",
    "amount",
)


def _norm_day(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def load_nominal_bars_by_day(
    conn,
    trading_days: Sequence[str],
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Load canonical nominal bars.

    When ``snapshot`` is provided (strategy B0–B4 live paths), bind live
    ``accepted_partition`` row_count/content_hash to the frozen generation
    before any bar I/O.
    """
    days = sorted({_norm_day(d) for d in trading_days if len(_norm_day(d)) == 8})
    if not days:
        return {}
    if snapshot is not None:
        from services.snapshot_nominal_bind import (
            load_snapshot_bound_nominal_bars_by_day,
        )

        bound = load_snapshot_bound_nominal_bars_by_day(
            snapshot, conn, days=days
        )
        return {
            day: [
                {
                    key: (
                        str(row[key]) if key == "ts_code" else row[key]
                    )
                    for key in _BAR_KEYS
                }
                for row in rows
            ]
            for day, rows in bound.items()
        }
    placeholders = ", ".join(["?"] * len(days))
    sql = f"""
        SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS d,
               ts_code, open, high, low, close, pre_close, pct_chg, vol, amount
          FROM {CANONICAL_TABLE}
         WHERE replace(CAST(trade_date AS VARCHAR), '-', '') IN ({placeholders})
         ORDER BY 1, ts_code
    """
    out: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for row in conn.execute(sql, days).fetchall():
        if hasattr(row, "keys"):
            d = _norm_day(row["d"])
            item = {k: (str(row[k]) if k == "ts_code" else row[k]) for k in _BAR_KEYS}
        else:
            d = _norm_day(row[0])
            item = {
                k: (str(row[i + 1]) if k == "ts_code" else row[i + 1])
                for i, k in enumerate(_BAR_KEYS)
            }
        out.setdefault(d, []).append(item)
    return out


__all__ = ["load_nominal_bars_by_day"]
