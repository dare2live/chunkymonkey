"""Single-stock form read (dossier F) — one truth plane: ``fact_stock_form_daily``.

09-18 tier12 整层退役 (cut_tier12_retire): accepted partition 那份第二真相源
与围绕它的切换判定一并删除；这里只剩一条 SELECT。
"""
from __future__ import annotations

from typing import Any

_FORM_COLS = [
    "trade_date",
    "form_name",
    "form_sub",
    "weekly_name",
    "monthly_name",
    "is_breakout_event",
    "axis_pos",
    "axis_trend",
    "axis_purity",
    "axis_vol",
    "axis_volregime",
    "axis_pos_memb",
    "axis_trend_memb",
    "axis_purity_memb",
    "axis_vol_memb",
    "base_days",
]


def load_form_row(conn, code: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Single-stock form row (dossier F): direct read off ``fact_stock_form_daily``."""
    params: list[Any] = [code]
    date_clause = ""
    if as_of:
        date_clause = "AND trade_date <= ?"
        params.append(as_of)
    row = conn.execute(
        f"""
        SELECT trade_date, form_name, form_sub, weekly_name, monthly_name,
               is_breakout_event, axis_pos, axis_trend, axis_purity, axis_vol,
               axis_volregime, axis_pos_memb, axis_trend_memb, axis_purity_memb,
               axis_vol_memb, base_days
        FROM fact_stock_form_daily
        WHERE stock_code = ? {date_clause}
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    out = dict(zip(_FORM_COLS, row))
    out["source"] = "fact_stock_form_daily"
    return out


__all__ = [
    "load_form_row",
]
