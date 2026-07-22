"""CX-3 decision-facing sector membership facet serve (Tier3 navigation).

Thin trust wrapper over ``market_pulse_serve_read.list_sector_members``.
Does not invent a second membership reader. Fail-closed for DC when snapshot
as_of lags SLA; SW remains display-snapshot with explicit non-PIT honesty.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services import calendar
from services import market_pulse as mp
from services import market_pulse_serve_read as pulse_serve

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "sector_membership_serve.yaml"

STATUS_OK = "ok"
STATUS_STALE = "stale"


@lru_cache(maxsize=1)
def load_cfg() -> dict[str, Any]:
    with open(_CFG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("sector_membership_serve.yaml must be a mapping")
    return cfg


def _calendar_days_between(d1: str, d2: str) -> int | None:
    from datetime import date

    try:
        a = date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        b = date(int(d2[:4]), int(d2[4:6]), int(d2[6:8]))
    except (ValueError, TypeError):
        return None
    return abs((b - a).days)


def _dc_freshness(conn, *, as_of: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    if not as_of:
        return {"status": STATUS_STALE, "reason": "membership_as_of_missing"}
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


def build_sector_membership(
    conn,
    *,
    chain: str,
    sector_code: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decision facet universe for one sector membership brick."""
    c = cfg or load_cfg()
    allowed = list(c.get("chains") or [mp.CHAIN_DC_INDUSTRY])
    if chain not in allowed:
        raise ValueError(f"chain must be one of {allowed}")

    leaf = pulse_serve.list_sector_members(conn, chain=chain, sector_code=sector_code)
    as_of = leaf.get("as_of")
    members = list(leaf.get("members") or [])
    # Normalize member codes for dossier links (con_code → stock_code digits).
    rows = []
    for m in members:
        con_code = str(m.get("con_code") or "")
        digits = "".join(ch for ch in con_code if ch.isdigit())[:6]
        rows.append({
            "stock_code": digits or con_code,
            "ts_code": con_code,
            "stock_name": m.get("name"),
        })

    base = {
        "status": None,
        "reason": None,
        "surface": "decision_sector_membership",
        "surface_version": c.get("surface_version"),
        "disclaimer": c.get("disclaimer"),
        "chain": chain,
        "sector_code": sector_code,
        "as_of": as_of,
        "membership_pit": False if chain == mp.CHAIN_SW else True,
        "count": 0,
        "rows": [],
    }

    if chain in mp.DC_CHAINS:
        fresh = _dc_freshness(conn, as_of=as_of, cfg=c)
        if fresh["status"] != "ready":
            return {**base, "status": STATUS_STALE, "reason": fresh["reason"]}
    else:
        # SW display snapshot — never claim PIT; still serve rows with honesty.
        base["membership_pit"] = bool(c.get("sw_membership_pit", False))
        base["honesty_note"] = "sw_index_member_all_is_new_snapshot_not_pit"

    return {
        **base,
        "status": STATUS_OK,
        "reason": None if rows else "unknown_or_empty_sector",
        "count": len(rows),
        "rows": rows,
    }


__all__ = [
    "STATUS_OK",
    "STATUS_STALE",
    "build_sector_membership",
    "load_cfg",
]
