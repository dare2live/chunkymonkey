"""Shared form/stage production read for dossier F + screener 5B.

Both surfaces flip together (decision_5b): never cut over one without the other.

Hybrid honesty (accepted payload is a subset of fact axes today):
- Base brick = ``fact_stock_form_daily`` (full axes incl. purity/vol/sub).
- When ``resolve_tier12_production_read`` → ``ACCEPTED_CUTOVER`` for the row's
  trade_date: overlay ``form_name`` / ``axis_pos`` / ``axis_trend`` /
  ``is_breakout_event`` from accepted stock_states.
- Axes absent from accepted payload stay on the fact brick as **typed residual**
  (``field_sources`` / ``hybrid_residual_fields``) — not invented, not claimed
  pure-accepted. Enrich-into-accept deferred until a versioned contract +
  re-accept knife is scheduled (Occam 2026-07-22).
"""
from __future__ import annotations

from typing import Any

from services.tier12_consumer_cutover import (
    resolve_tier12_production_read,
    stock_states_from_accepted_payload,
)

# Test hooks (mirror market_pulse_serve_read).
_TIER12_ARTIFACT_ROOT = None
_TIER12_CUTOVER_CONFIG = None
_TIER12_CONFIG_PATH = None

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

_OVERLAY_FIELDS = ("form_name", "axis_pos", "axis_trend", "is_breakout_event")
# Present on fact brick; not in accepted StockStateDaily payload today.
_FACT_RESIDUAL_FIELDS = (
    "form_sub",
    "weekly_name",
    "monthly_name",
    "axis_purity",
    "axis_vol",
    "axis_volregime",
    "axis_pos_memb",
    "axis_trend_memb",
    "axis_purity_memb",
    "axis_vol_memb",
    "base_days",
)


def _code6(code: Any) -> str:
    s = str(code or "").strip()
    if not s:
        return ""
    return s.split(".")[0][:6]


def _accepted_states_for_day(day: str) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return (production_read_status, code6→accepted fields)."""
    d = "".join(ch for ch in str(day) if ch.isdigit())[:8]
    if len(d) != 8:
        return "legacy_scaffold", {}
    read = resolve_tier12_production_read(
        d,
        config=_TIER12_CUTOVER_CONFIG,
        artifact_root=_TIER12_ARTIFACT_ROOT,
        config_path=_TIER12_CONFIG_PATH,
    )
    if read.uses_legacy or read.accepted_payload is None:
        return str(read.status), {}
    return str(read.status), stock_states_from_accepted_payload(read.accepted_payload)


def load_form_row(conn, code: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Single-stock form row (dossier F) via shared production-read boundary."""
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
    status, by_code = _accepted_states_for_day(str(out.get("trade_date") or ""))
    accepted = by_code.get(_code6(code))
    field_sources: dict[str, str] = {
        field: "fact_stock_form_daily" for field in _FORM_COLS if field != "trade_date"
    }
    if accepted:
        for field in _OVERLAY_FIELDS:
            if accepted.get(field) is not None:
                out[field] = accepted[field]
                field_sources[field] = "accepted_partition"
        out["source"] = "accepted_partition+fact_stock_form_daily"
        out["production_read_status"] = status
        out["hybrid_residual_fields"] = list(_FACT_RESIDUAL_FIELDS)
        out["field_sources"] = field_sources
        out["resolver_note"] = (
            "typed hybrid: ACCEPTED_CUTOVER overlay on "
            + "/".join(_OVERLAY_FIELDS)
            + "; residual axes stay fact brick "
            f"({','.join(_FACT_RESIDUAL_FIELDS[:4])}…); "
            "not pure accepted-only"
        )
    else:
        out["source"] = "fact_stock_form_daily"
        out["production_read_status"] = status
        out["hybrid_residual_fields"] = list(_FORM_COLS[1:])
        out["field_sources"] = field_sources
        out["resolver_note"] = (
            "production-read boundary checked; legacy/fact brick in use "
            f"(status={status}); no accepted overlay"
        )
    return out


def overlay_form_rows(
    rows: list[dict[str, Any]],
    *,
    as_of: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bulk overlay for screener rows sharing one as-of snapshot day."""
    meta = {
        "production_read_status": "legacy_scaffold",
        "overlay_applied": False,
        "overlay_fields": list(_OVERLAY_FIELDS),
        "hybrid_residual_fields": list(_FACT_RESIDUAL_FIELDS),
        "read_mode": "fact_only",
    }
    if not rows or not as_of:
        return rows, meta
    status, by_code = _accepted_states_for_day(as_of)
    meta["production_read_status"] = status
    if not by_code:
        return rows, meta
    out: list[dict[str, Any]] = []
    applied = 0
    for row in rows:
        rec = dict(row)
        accepted = by_code.get(_code6(rec.get("stock_code")))
        if accepted:
            for field in _OVERLAY_FIELDS:
                if accepted.get(field) is not None:
                    rec[field] = accepted[field]
            rec["source"] = "accepted_partition+fact_stock_form_daily"
            applied += 1
        else:
            rec.setdefault("source", "fact_stock_form_daily")
        out.append(rec)
    meta["overlay_applied"] = applied > 0
    meta["overlay_row_count"] = applied
    meta["read_mode"] = "hybrid_accepted_plus_fact_residual" if applied else "fact_only"
    return out, meta


__all__ = [
    "load_form_row",
    "overlay_form_rows",
]
