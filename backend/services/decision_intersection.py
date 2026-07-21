"""Cap 4D 交集最强股 — Tier3 product consumer (read-only).

Intersection = stocks that are simultaneous members of a currently "strong"
(inflow behavior known + chase/latent) DC 行业 sector *and* a currently
"strong" DC 概念 sector, on the same as-of trade_date. Reuses
``moneyflow_assist.build_sector_board`` for sector-level honesty (incomplete
horizon → unknown; never fused into Tier0/Tier2) and ``fact_dc_member_daily``
(观察日 PIT membership, 沪深A-filtered) for constituent lookup.

Input honesty (plan §3.5): membership + strength must share a serve as-of; a
stale or mismatched as-of degrades the whole surface to ``status=stale`` with
empty rows — never a fake freshness claim (mirrors ``/pulse/strongest``).

Authority: analysis/decision_4d_intersection_strongest_20260721.md
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services import calendar
from services import market_pulse as mp
from services import market_pulse_serve_read as pulse_serve
from services import moneyflow_assist as mfa

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "decision_intersection.yaml"

STATUS_OK = "ok"
STATUS_STALE = "stale"

# Cap fan-out on sector member lookups even if config is misconfigured.
_MEMBER_LOOKUP_HARD_CAP = 200


@lru_cache(maxsize=1)
def load_cfg() -> dict[str, Any]:
    with open(_CFG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("decision_intersection.yaml must be a mapping")
    return cfg


def _calendar_days_between(d1: str, d2: str) -> int | None:
    """|calendar days| between two YYYYMMDD strings; None if unparsable."""
    from datetime import date

    try:
        a = date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        b = date(int(d2[:4]), int(d2[4:6]), int(d2[6:8]))
    except (ValueError, TypeError):
        return None
    return abs((b - a).days)


def _freshness_gate(
    conn,
    *,
    industry_as_of: str | None,
    concept_as_of: str | None,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Fail-closed as-of honesty: chains must agree and not lag the calendar."""
    if not industry_as_of or not concept_as_of:
        return {"status": STATUS_STALE, "reason": "sector_board_as_of_missing"}
    if industry_as_of != concept_as_of:
        return {
            "status": STATUS_STALE,
            "reason": (
                f"chain_as_of_mismatch dc_industry={industry_as_of} "
                f"dc_concept={concept_as_of}"
            ),
        }
    try:
        expected = calendar.latest_completed_trade_date(conn)
    except Exception:  # noqa: BLE001 — calendar unreachable → don't block on it
        expected = None
    if expected:
        expected_compact = expected.replace("-", "")
        max_lag = int(cfg.get("sla_max_lag_calendar_days", 1))
        lag = _calendar_days_between(industry_as_of, expected_compact)
        if lag is not None and lag > max_lag:
            return {
                "status": STATUS_STALE,
                "reason": (
                    f"as_of_lag_{lag}_calendar_days_gt_sla_{max_lag} "
                    f"as_of={industry_as_of} expected={expected_compact}"
                ),
            }
    return {"status": "ready", "reason": None}


def _strong_sectors(board: dict[str, Any], strong_set: set[str], cap: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in board.get("rows") or []:
        if len(out) >= cap:
            break
        h = row.get("horizon") or {}
        b = row.get("behavior") or {}
        if h.get("status") == "known" and b.get("behavior") in strong_set:
            out[str(row["sector_code"])] = row
    return out


def _why_sentence(
    *,
    stock_name: str | None,
    stock_code: str,
    industry_names: list[str],
    concept_names: list[str],
    horizon: int,
    disclaimer: str,
) -> str:
    label = stock_name or stock_code
    ind_s = "、".join(n for n in industry_names if n) or "未知行业"
    con_s = "、".join(n for n in concept_names if n) or "未知概念"
    return (
        f"{label}：同属强势行业「{ind_s}」与强势概念「{con_s}」交集"
        f"（近{horizon}日代理净流入/市值均呈抢筹或潜伏迹象），{disclaimer}"
    )


def _valid_horizons() -> list[int]:
    return mfa._horizons(mfa.load_cfg())


def _compute_intersection(conn, *, horizon: int, cfg: dict[str, Any]) -> dict[str, Any]:
    """Unsliced (unlimited rows) intersection computation.

    Shared by the board endpoint (which slices to ``limit``) and the
    per-stock lookup (which must not miss a hit just because it ranks below
    a display-only limit).
    """
    mcfg = mfa.load_cfg()
    strong_set = set(cfg.get("strong_behaviors") or ["chase", "latent"])
    cap = min(int(cfg.get("max_strong_sectors_per_chain", 60)), _MEMBER_LOOKUP_HARD_CAP)
    disclaimer = str(cfg.get("disclaimer") or "")

    industry_board = mfa.build_sector_board(
        conn, chain=mp.CHAIN_DC_INDUSTRY, horizon=horizon, limit=500, cfg=mcfg,
    )
    concept_board = mfa.build_sector_board(
        conn, chain=mp.CHAIN_DC_CONCEPT, horizon=horizon, limit=500, cfg=mcfg,
    )

    base = {
        "status": None,
        "reason": None,
        "surface": "decision_intersection_strongest",
        "surface_version": cfg.get("surface_version"),
        "disclaimer": disclaimer,
        "as_of": {
            "dc_industry": industry_board.get("as_of"),
            "dc_concept": concept_board.get("as_of"),
        },
        "horizon": horizon,
        "horizons": _valid_horizons(),
        "rows": [],
        "strong_sector_counts": {"dc_industry": 0, "dc_concept": 0},
    }

    fresh = _freshness_gate(
        conn,
        industry_as_of=industry_board.get("as_of"),
        concept_as_of=concept_board.get("as_of"),
        cfg=cfg,
    )
    if fresh["status"] != "ready":
        return {**base, "status": STATUS_STALE, "reason": fresh["reason"]}

    industry_strong = _strong_sectors(industry_board, strong_set, cap)
    concept_strong = _strong_sectors(concept_board, strong_set, cap)
    base["strong_sector_counts"] = {
        "dc_industry": len(industry_strong),
        "dc_concept": len(concept_strong),
    }
    if not industry_strong or not concept_strong:
        return {**base, "status": STATUS_OK, "reason": "no_strong_sector_intersection_this_window"}

    mem_sql = pulse_serve.dc_member_mem_sql()
    stock_hits: dict[str, dict[str, Any]] = {}

    def _collect(strong: dict[str, dict[str, Any]], key: str) -> None:
        for sector_code, row in strong.items():
            snap = pulse_serve.dc_member_snap(conn, sector_code, str(row["trade_date"]))
            if snap is None:
                continue
            for ts_code, name in conn.execute(mem_sql, [sector_code, snap]).fetchall():
                hit = stock_hits.setdefault(ts_code, {"name": name, "industry": [], "concept": []})
                if not hit.get("name"):
                    hit["name"] = name
                hit[key].append(sector_code)

    _collect(industry_strong, "industry")
    _collect(concept_strong, "concept")

    rows: list[dict[str, Any]] = []
    for ts_code, hit in stock_hits.items():
        if not hit["industry"] or not hit["concept"]:
            continue
        stock_code = ts_code.split(".")[0]
        ind_rows = [industry_strong[code] for code in hit["industry"]]
        con_rows = [concept_strong[code] for code in hit["concept"]]
        rows.append({
            "stock_code": stock_code,
            "ts_code": ts_code,
            "stock_name": hit.get("name"),
            "industry_sectors": [
                {
                    "sector_code": r["sector_code"],
                    "sector_name": r.get("sector_name"),
                    "behavior": r["behavior"]["behavior"],
                    "behavior_zh": r["behavior"]["behavior_zh"],
                }
                for r in ind_rows
            ],
            "concept_sectors": [
                {
                    "sector_code": r["sector_code"],
                    "sector_name": r.get("sector_name"),
                    "behavior": r["behavior"]["behavior"],
                    "behavior_zh": r["behavior"]["behavior_zh"],
                }
                for r in con_rows
            ],
            "why": _why_sentence(
                stock_name=hit.get("name"),
                stock_code=stock_code,
                industry_names=[r.get("sector_name") for r in ind_rows],
                concept_names=[r.get("sector_name") for r in con_rows],
                horizon=horizon,
                disclaimer=disclaimer,
            ),
        })

    rows.sort(key=lambda r: (-(len(r["industry_sectors"]) + len(r["concept_sectors"])), r["stock_code"]))
    return {
        **base,
        "status": STATUS_OK,
        "reason": None if rows else "no_stock_at_intersection_this_window",
        "rows": rows,
    }


def build_intersection_strongest(
    conn,
    *,
    horizon: int = 20,
    limit: int = 20,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """交集最强股 decision list (board): DC 行业∩概念 strong-sector membership overlap.

    Output is a ranked decision list + per-row "why" sentence — not a raw
    rank dump (plan §3.5).
    """
    c = cfg or load_cfg()
    hs = _valid_horizons()
    if horizon not in hs:
        raise ValueError(f"horizon must be one of {hs}; got {horizon}")
    limit = max(1, min(int(limit), int(c.get("max_limit", 200))))
    out = _compute_intersection(conn, horizon=horizon, cfg=c)
    rows = out["rows"][:limit]
    return {**out, "count": len(rows), "rows": rows}


def build_intersection_for_stock(
    conn,
    *,
    stock_code: str,
    horizon: int = 20,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-stock view of the intersection board (dossier 交集 tab)."""
    c = cfg or load_cfg()
    hs = _valid_horizons()
    if horizon not in hs:
        raise ValueError(f"horizon must be one of {hs}; got {horizon}")
    full = _compute_intersection(conn, horizon=horizon, cfg=c)
    detail = next((r for r in full["rows"] if r["stock_code"] == stock_code), None)
    return {
        "status": full["status"],
        "surface": "decision_intersection_strongest_stock",
        "surface_version": full.get("surface_version"),
        "disclaimer": full.get("disclaimer"),
        "stock_code": stock_code,
        "horizon": horizon,
        "horizons": full["horizons"],
        "as_of": full["as_of"],
        "reason": full.get("reason") if detail is None else None,
        "in_intersection": detail is not None,
        "detail": detail,
    }


__all__ = [
    "load_cfg",
    "build_intersection_strongest",
    "build_intersection_for_stock",
]
