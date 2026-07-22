"""CX-3 stock-level continuous net-inflow/outflow streak universe (Tier3 serve).

Reads ``fact_stock_moneyflow_dc_daily`` only. Same-sign streak semantics mirror
sector ``flow_streak``: positive net = inflow day, negative = outflow, zero/NULL
breaks the streak. Fail-closed when moneyflow_dc as_of lags SLA — empty rows,
no narrative. Never writes Tier0/Tier2.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services import calendar
from services.universe import sql_where_active_a_share

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "stock_flow_streak.yaml"

STATUS_OK = "ok"
STATUS_STALE = "stale"
DIR_INFLOW = "inflow"
DIR_OUTFLOW = "outflow"


@lru_cache(maxsize=1)
def load_cfg() -> dict[str, Any]:
    with open(_CFG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("stock_flow_streak.yaml must be a mapping")
    return cfg


def _calendar_days_between(d1: str, d2: str) -> int | None:
    from datetime import date

    try:
        a = date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        b = date(int(d2[:4]), int(d2[4:6]), int(d2[6:8]))
    except (ValueError, TypeError):
        return None
    return abs((b - a).days)


def _freshness_gate(conn, *, as_of: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    if not as_of:
        return {"status": STATUS_STALE, "reason": "moneyflow_dc_as_of_missing"}
    try:
        expected = calendar.latest_completed_trade_date(conn)
    except Exception:  # noqa: BLE001
        expected = None
    if expected:
        expected_compact = expected.replace("-", "")
        max_lag = int(cfg.get("sla_max_lag_calendar_days", 1))
        lag = _calendar_days_between(str(as_of), expected_compact)
        if lag is not None and lag > max_lag:
            return {
                "status": STATUS_STALE,
                "reason": (
                    f"as_of_lag_{lag}_calendar_days_gt_sla_{max_lag} "
                    f"as_of={as_of} expected={expected_compact}"
                ),
            }
    return {"status": "ready", "reason": None}


def compute_stock_flow_streak(
    conn,
    stock_code: str,
    *,
    lookback_calendar_days: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Signed streak ending at the stock's latest moneyflow_dc day.

    Returns ``{flow_streak, as_of, direction, status}`` where flow_streak is
    signed (+N inflow / -N outflow / 0 broken) matching sector pulse convention.
    """
    c = cfg or load_cfg()
    code = "".join(ch for ch in stock_code if ch.isdigit())[:6]
    lookback = int(lookback_calendar_days or c.get("lookback_calendar_days", 120))
    if lookback < 1 or lookback > 400:
        raise ValueError("lookback_calendar_days out of bounds")
    row = conn.execute(
        f"""
        WITH latest AS (
          SELECT MAX(trade_date) AS d
          FROM fact_stock_moneyflow_dc_daily
          WHERE stock_code = ?
        ),
        base AS (
          SELECT m.trade_date, m.net_amount,
                 CASE WHEN m.net_amount > 0 THEN 1
                      WHEN m.net_amount < 0 THEN -1
                      ELSE 0 END AS sgn
          FROM fact_stock_moneyflow_dc_daily m, latest
          WHERE m.stock_code = ?
            AND latest.d IS NOT NULL
            AND m.trade_date >= CAST(
                  strftime(strptime(latest.d, '%Y%m%d') - INTERVAL {lookback} DAY, '%Y%m%d')
                  AS VARCHAR)
        ),
        marked AS (
          SELECT trade_date, net_amount, sgn,
                 LAG(sgn) OVER (ORDER BY trade_date) AS prev_sgn
          FROM base
        ),
        grp AS (
          SELECT *, SUM(CASE WHEN sgn = 0 OR prev_sgn IS NULL OR sgn != prev_sgn
                             THEN 1 ELSE 0 END)
                     OVER (ORDER BY trade_date) AS g
          FROM marked
        ),
        streak AS (
          SELECT sgn, COUNT(*) AS streak, MAX(trade_date) AS as_of
          FROM grp
          WHERE sgn != 0
          GROUP BY sgn, g
        )
        SELECT sgn, streak, as_of
        FROM streak
        WHERE as_of = (SELECT d FROM latest)
        ORDER BY streak DESC
        LIMIT 1
        """,
        [code, code],
    ).fetchone()
    if not row:
        as_of_row = conn.execute(
            "SELECT MAX(trade_date) FROM fact_stock_moneyflow_dc_daily WHERE stock_code = ?",
            [code],
        ).fetchone()
        return {
            "stock_code": code,
            "flow_streak": 0,
            "direction": None,
            "as_of": as_of_row[0] if as_of_row else None,
            "status": "unknown" if not as_of_row or not as_of_row[0] else "ok",
        }
    sgn, streak, as_of = int(row[0]), int(row[1]), str(row[2])
    signed = streak if sgn > 0 else -streak
    return {
        "stock_code": code,
        "flow_streak": signed,
        "direction": DIR_INFLOW if sgn > 0 else DIR_OUTFLOW,
        "as_of": as_of,
        "status": "ok",
    }


def build_stock_flow_streak_universe(
    conn,
    *,
    direction: str = DIR_INFLOW,
    min_streak: int = 5,
    limit: int = 50,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Universe of HS-A stocks whose latest DC moneyflow streak meets the bar."""
    c = cfg or load_cfg()
    direction = (direction or DIR_INFLOW).strip().lower()
    if direction not in (DIR_INFLOW, DIR_OUTFLOW):
        raise ValueError(f"direction must be {DIR_INFLOW}|{DIR_OUTFLOW}")
    min_streak = max(1, int(min_streak))
    limit = max(1, min(int(limit), int(c.get("max_limit", 200))))
    lookback = int(c.get("lookback_calendar_days", 120))
    if lookback < 1 or lookback > 400:
        raise ValueError("lookback_calendar_days out of bounds")
    sgn = 1 if direction == DIR_INFLOW else -1
    hsa = sql_where_active_a_share("stock_code")

    as_of_row = conn.execute(
        "SELECT MAX(trade_date) FROM fact_stock_moneyflow_dc_daily"
    ).fetchone()
    as_of = str(as_of_row[0]) if as_of_row and as_of_row[0] else None

    base = {
        "status": None,
        "reason": None,
        "surface": "stock_flow_streak_universe",
        "surface_version": c.get("surface_version"),
        "disclaimer": c.get("disclaimer"),
        "vendor": c.get("vendor"),
        "direction": direction,
        "min_streak": min_streak,
        "as_of": as_of,
        "count": 0,
        "rows": [],
    }
    fresh = _freshness_gate(conn, as_of=as_of, cfg=c)
    if fresh["status"] != "ready":
        return {**base, "status": STATUS_STALE, "reason": fresh["reason"]}

    rows = conn.execute(
        f"""
        WITH latest AS (
          SELECT MAX(trade_date) AS d FROM fact_stock_moneyflow_dc_daily
        ),
        base AS (
          SELECT m.stock_code, m.trade_date, m.net_amount,
                 CASE WHEN m.net_amount > 0 THEN 1
                      WHEN m.net_amount < 0 THEN -1
                      ELSE 0 END AS sgn
          FROM fact_stock_moneyflow_dc_daily m, latest
          WHERE latest.d IS NOT NULL
            AND m.trade_date >= CAST(
                  strftime(strptime(latest.d, '%Y%m%d') - INTERVAL {lookback} DAY, '%Y%m%d')
                  AS VARCHAR)
            AND {hsa}
        ),
        marked AS (
          SELECT stock_code, trade_date, net_amount, sgn,
                 LAG(sgn) OVER (PARTITION BY stock_code ORDER BY trade_date) AS prev_sgn
          FROM base
        ),
        grp AS (
          SELECT *, SUM(CASE WHEN sgn = 0 OR prev_sgn IS NULL OR sgn != prev_sgn
                             THEN 1 ELSE 0 END)
                     OVER (PARTITION BY stock_code ORDER BY trade_date) AS g
          FROM marked
        ),
        streak AS (
          SELECT stock_code, sgn, COUNT(*) AS streak, MAX(trade_date) AS as_of,
                 SUM(net_amount) AS cum_net
          FROM grp
          WHERE sgn != 0
          GROUP BY stock_code, sgn, g
        )
        SELECT s.stock_code, s.streak, s.as_of, s.cum_net
        FROM streak s, latest
        WHERE s.as_of = latest.d
          AND s.sgn = ?
          AND s.streak >= ?
        ORDER BY s.streak DESC, ABS(s.cum_net) DESC, s.stock_code
        LIMIT ?
        """,
        [sgn, min_streak, limit],
    ).fetchall()

    codes = [str(r[0]) for r in rows]
    names = _bulk_names(conn, codes)
    out_rows = []
    for stock_code, streak, row_as_of, cum_net in rows:
        signed = int(streak) if sgn > 0 else -int(streak)
        out_rows.append({
            "stock_code": str(stock_code),
            "stock_name": names.get(str(stock_code)),
            "flow_streak": signed,
            "direction": direction,
            "streak_days": int(streak),
            "as_of": str(row_as_of),
            # Vendor unit = 万元 (publication); expose as-is, never invent yuan.
            "cum_net_vendor": None if cum_net is None else float(cum_net),
            "cum_net_unit": "wan_yuan",
            "why": (
                f"{names.get(str(stock_code)) or stock_code}："
                f"东财主力代理连续{streak}日净"
                f"{'流入' if direction == DIR_INFLOW else '流出'}"
                f"（截至 {row_as_of}），{c.get('disclaimer')}"
            ),
        })

    return {
        **base,
        "status": STATUS_OK,
        "reason": None if out_rows else "no_stock_matches_streak",
        "count": len(out_rows),
        "rows": out_rows,
    }


def _bulk_names(conn, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    out: dict[str, str] = {}
    try:
        placeholders = ",".join(["?"] * len(codes))
        for code, name in conn.execute(
            f"""
            SELECT stock_code, name FROM dim_active_a_stock
            WHERE stock_code IN ({placeholders})
            """,
            codes,
        ).fetchall():
            if name:
                out[str(code)] = str(name)
    except Exception:  # noqa: BLE001 — name lookup best-effort
        pass
    return out


__all__ = [
    "DIR_INFLOW",
    "DIR_OUTFLOW",
    "STATUS_OK",
    "STATUS_STALE",
    "build_stock_flow_streak_universe",
    "compute_stock_flow_streak",
    "load_cfg",
]
