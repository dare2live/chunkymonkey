"""Cap 5B 形态/阶段选股面 — Tier3 product consumer (read-only).

Consumes the exact same Tier1 brick as the stock dossier 「形态·阶段」 tab
(``fact_stock_form_daily``): same table, same axis vocabulary, same
observation-day semantics. This module adds *no* new Tier1 concept — it is a
filter/select surface over an already-published brick (plan §3.6: "形态/阶段
as strategy surface consuming same Tier1 bricks as F"). No Optuna, no
StrategyRelease, no ranking/scoring model — output is a plain filtered
decision list with a per-row observation sentence, never a score.

Freshness (mirrors ``/pulse/strongest`` + Cap 4D honesty): the global as-of
(``MAX(trade_date)``) must not lag the trading-calendar's latest completed
trade date beyond the configured SLA — stale → ``status=stale`` + empty rows,
never a silently outdated screen.

Authority: 本文件 (产品能力边界, Cap B) +
goal.md「下一步」执行 backlog §2.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services import calendar
from services.form_production_read import overlay_form_rows
from services.universe import sql_where_active_a_share

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "stock_screener.yaml"

STATUS_OK = "ok"
STATUS_STALE = "stale"

# Bricks reused verbatim from fact_stock_form_daily (same columns as dossier
# stock_dossier.py::_load_form) — keep the vocabulary singular, not re-derived.
_AXIS_FACETS = ("axis_pos", "axis_trend", "axis_purity", "axis_vol")


@lru_cache(maxsize=1)
def load_cfg() -> dict[str, Any]:
    with open(_CFG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("stock_screener.yaml must be a mapping")
    return cfg


def _calendar_days_between(d1: str, d2: str) -> int | None:
    """|calendar days| between two YYYYMMDD strings; None if unparsable."""
    try:
        a = date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        b = date(int(d2[:4]), int(d2[4:6]), int(d2[6:8]))
    except (ValueError, TypeError):
        return None
    return abs((b - a).days)


def _axis_zh(cfg: dict[str, Any], facet: str, raw: Any) -> str | None:
    if raw is None:
        return None
    mapping = dict(cfg.get(f"{facet}_zh") or {})
    key = str(raw).strip().lower()
    return mapping.get(key) or (str(raw) if str(raw).strip() else None)


def _global_as_of(conn) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) FROM fact_stock_form_daily").fetchone()
    return str(row[0]) if row and row[0] else None


def _freshness_gate(conn, *, as_of: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed as-of honesty — same SLA style as Cap 4D intersection."""
    if not as_of:
        return {"status": STATUS_STALE, "reason": "form_daily_as_of_missing"}
    try:
        expected = calendar.latest_completed_trade_date(conn)
    except Exception:  # noqa: BLE001 — calendar unreachable → don't block on it
        expected = None
    if expected:
        expected_compact = expected.replace("-", "")
        max_lag = int(cfg.get("sla_max_lag_calendar_days", 1))
        lag = _calendar_days_between(as_of, expected_compact)
        if lag is not None and lag > max_lag:
            return {
                "status": STATUS_STALE,
                "reason": (
                    f"as_of_lag_{lag}_calendar_days_gt_sla_{max_lag} "
                    f"as_of={as_of} expected={expected_compact}"
                ),
            }
    return {"status": "ready", "reason": None}


def build_options(conn, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Live facet menu (form_name/form_sub + 4 axes) at the current as-of.

    Never a hardcoded enum — options come from what is actually published
    today, so the filter UI cannot offer a value with zero real matches.
    """
    c = cfg or load_cfg()
    as_of = _global_as_of(conn)
    base = {
        "status": None,
        "reason": None,
        "surface": "stock_screener_options",
        "surface_version": c.get("surface_version"),
        "disclaimer": c.get("disclaimer"),
        "as_of": as_of,
        "facets": {},
    }
    fresh = _freshness_gate(conn, as_of=as_of, cfg=c)
    if fresh["status"] != "ready":
        return {**base, "status": STATUS_STALE, "reason": fresh["reason"]}

    cap = int(c.get("max_options_per_facet", 30))
    hsa = sql_where_active_a_share("stock_code")
    facets: dict[str, list[dict[str, Any]]] = {}
    for col in ("form_name", "form_sub", *_AXIS_FACETS):
        rows = conn.execute(
            f"""
            SELECT {col} AS value, count(*) AS n
            FROM fact_stock_form_daily
            WHERE trade_date = ? AND {col} IS NOT NULL AND {hsa}
            GROUP BY 1
            ORDER BY n DESC
            LIMIT {cap}
            """,
            [as_of],
        ).fetchall()
        facets[col] = [
            {
                "value": str(v),
                "label": _axis_zh(c, col, v) if col in _AXIS_FACETS else str(v),
                "count": int(n),
            }
            for v, n in rows
        ]
    return {**base, "status": STATUS_OK, "facets": facets}


def _why_sentence(row: dict[str, Any], cfg: dict[str, Any]) -> str:
    label = row.get("stock_name") or row.get("stock_code")
    parts = [
        p
        for p in (
            _axis_zh(cfg, "axis_pos", row.get("axis_pos")),
            _axis_zh(cfg, "axis_trend", row.get("axis_trend")),
            _axis_zh(cfg, "axis_purity", row.get("axis_purity")),
            _axis_zh(cfg, "axis_vol", row.get("axis_vol")),
        )
        if p
    ]
    chunks: list[str] = []
    if row.get("form_name"):
        sub = f"（{row['form_sub']}）" if row.get("form_sub") else ""
        chunks.append(f"形态为{row['form_name']}{sub}")
    if parts:
        chunks.append(f"阶段偏{' · '.join(parts)}")
    body = "，".join(chunks) if chunks else "形态/阶段未知"
    return f"{label}：{body}（截至 {row.get('trade_date')}），{cfg.get('disclaimer')}"


def build_form_stage_screen(
    conn,
    *,
    form_names: list[str] | None = None,
    axis_pos: str | None = None,
    axis_trend: str | None = None,
    axis_purity: str | None = None,
    axis_vol: str | None = None,
    is_breakout_event: bool | None = None,
    limit: int = 50,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Filtered decision list over the current fact_stock_form_daily snapshot.

    Output is a plain filtered list + per-row observation sentence — never a
    score/rank (no Optuna, no StrategyRelease; plan §3.6 gate).
    """
    c = cfg or load_cfg()
    limit = max(1, min(int(limit), int(c.get("max_limit", 200))))
    as_of = _global_as_of(conn)
    filters_applied = {
        "form_names": list(form_names) if form_names else None,
        "axis_pos": axis_pos,
        "axis_trend": axis_trend,
        "axis_purity": axis_purity,
        "axis_vol": axis_vol,
        "is_breakout_event": is_breakout_event,
    }
    base = {
        "status": None,
        "reason": None,
        "surface": "stock_screener_form_stage",
        "surface_version": c.get("surface_version"),
        "disclaimer": c.get("disclaimer"),
        "as_of": as_of,
        "filters_applied": filters_applied,
        "count": 0,
        "rows": [],
    }
    fresh = _freshness_gate(conn, as_of=as_of, cfg=c)
    if fresh["status"] != "ready":
        return {**base, "status": STATUS_STALE, "reason": fresh["reason"]}

    hsa = sql_where_active_a_share("stock_code")
    clauses = [hsa, "trade_date = ?"]
    params: list[Any] = [as_of]
    if form_names:
        placeholders = ",".join(["?"] * len(form_names))
        clauses.append(f"form_name IN ({placeholders})")
        params.extend(form_names)
    for facet, val in (
        ("axis_pos", axis_pos),
        ("axis_trend", axis_trend),
        ("axis_purity", axis_purity),
        ("axis_vol", axis_vol),
    ):
        if val:
            clauses.append(f"{facet} = ?")
            params.append(val)
    if is_breakout_event is not None:
        clauses.append("is_breakout_event = ?")
        params.append(bool(is_breakout_event))

    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT stock_code, trade_date, form_name, form_sub, weekly_name,
               monthly_name, is_breakout_event, axis_pos, axis_trend,
               axis_purity, axis_vol, base_days
        FROM fact_stock_form_daily
        WHERE {where_sql}
        ORDER BY stock_code
        LIMIT {limit + 1}
        """,
        params,
    ).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    cols = [
        "stock_code", "trade_date", "form_name", "form_sub", "weekly_name",
        "monthly_name", "is_breakout_event", "axis_pos", "axis_trend",
        "axis_purity", "axis_vol", "base_days",
    ]
    codes = [str(r[0]) for r in rows]
    names = _bulk_stock_names(conn, codes)

    out_rows: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(zip(cols, r))
        out_rows.append(rec)

    # Same production-read boundary as dossier F (decision_5b: flip together).
    out_rows, prod_meta = overlay_form_rows(out_rows, as_of=as_of)

    for rec in out_rows:
        rec["stock_name"] = names.get(rec["stock_code"])
        rec["axis_pos_zh"] = _axis_zh(c, "axis_pos", rec.get("axis_pos"))
        rec["axis_trend_zh"] = _axis_zh(c, "axis_trend", rec.get("axis_trend"))
        rec["axis_purity_zh"] = _axis_zh(c, "axis_purity", rec.get("axis_purity"))
        rec["axis_vol_zh"] = _axis_zh(c, "axis_vol", rec.get("axis_vol"))
        rec["why"] = _why_sentence(rec, c)

    return {
        **base,
        "status": STATUS_OK,
        "reason": "no_stock_matches_filters" if not out_rows else None,
        "count": len(out_rows),
        "truncated": truncated,
        "production_read": prod_meta,
        "rows": out_rows,
    }


def _bulk_stock_names(conn, codes: list[str]) -> dict[str, str]:
    """Best-effort name lookup via dim_active_a_stock (identity SSOT).

    Missing → unknown; never invent; never block the screen. Holders fact
    plane is retired — do not fall back to it for names.
    """
    if not codes:
        return {}
    try:
        from services.security_master import active_stock_name_map

        return {
            str(c): str(n)
            for c, n in active_stock_name_map(codes, conn=conn).items()
            if n
        }
    except Exception:  # noqa: BLE001 — name lookup is best-effort, fail-open to unknown
        return {}


__all__ = [
    "load_cfg",
    "build_options",
    "build_form_stage_screen",
]
