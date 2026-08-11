"""CX-3 daily briefing — Tier3 narrative consumer of Cap A / Cap D (+ Cap B).

Aggregates already-published conclusions/why/observations into one narrative.
Never invents freshness: any Cap A/D input untrusted/stale → status=stale,
narrative=None, sections=[] (fail-closed). Does not write Tier0/Tier2.

Authority: goal.md「下一步」执行 backlog §2 (CX-3 DONE)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services import calendar
from services import decision_intersection as di
from services import moneyflow_assist as mfa
from services import stock_screener as screener

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "daily_briefing.yaml"

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_UNAVAILABLE = "unavailable"

TRUSTED = "trusted"
UNTRUSTED = "untrusted"


@lru_cache(maxsize=1)
def load_cfg() -> dict[str, Any]:
    with open(_CFG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("daily_briefing.yaml must be a mapping")
    return cfg


def _calendar_days_between(d1: str, d2: str) -> int | None:
    from datetime import date

    try:
        a = date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        b = date(int(d2[:4]), int(d2[4:6]), int(d2[6:8]))
    except (ValueError, TypeError):
        return None
    return abs((b - a).days)


def _as_of_trust(conn, as_of: str | None, *, max_lag: int) -> dict[str, Any]:
    if not as_of:
        return {"trust": UNTRUSTED, "reason": "as_of_missing"}
    try:
        expected = calendar.latest_completed_trade_date(conn)
    except Exception:  # noqa: BLE001
        expected = None
    if expected:
        expected_compact = expected.replace("-", "")
        lag = _calendar_days_between(str(as_of), expected_compact)
        if lag is not None and lag > max_lag:
            return {
                "trust": UNTRUSTED,
                "reason": (
                    f"as_of_lag_{lag}_calendar_days_gt_sla_{max_lag} "
                    f"as_of={as_of} expected={expected_compact}"
                ),
            }
    return {"trust": TRUSTED, "reason": None}


def build_daily_briefing(
    conn,
    *,
    horizon: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate Cap A board + Cap D intersection (+ Cap B sample) into a briefing."""
    c = cfg or load_cfg()
    mcfg = mfa.load_cfg()
    hs = mfa._horizons(mcfg)
    h = int(horizon if horizon is not None else c.get("default_horizon", 20))
    if h not in hs:
        raise ValueError(f"horizon must be one of {hs}")

    max_lag = int(c.get("sla_max_lag_calendar_days", 1))
    chain = str(c.get("board_chain") or "dc_industry")
    level = str(c.get("board_level") or "L1")
    sample_n = int(c.get("board_sample_per_behavior", 3))
    inter_n = int(c.get("intersection_sample", 5))
    brk_n = int(c.get("screener_breakout_sample", 3))

    base: dict[str, Any] = {
        "status": STATUS_OK,
        "surface": "daily_briefing",
        "surface_version": c.get("surface_version"),
        "disclaimer": c.get("disclaimer"),
        "horizon": h,
        "as_of": None,
        "inputs": {
            "moneyflow": {"trust": UNTRUSTED, "reason": None, "as_of": None},
            "intersection": {"trust": UNTRUSTED, "reason": None, "as_of": None},
            "screener": {"trust": UNTRUSTED, "reason": None, "as_of": None},
        },
        "sections": [],
        "narrative": None,
        "reason": None,
        "tier0_write": False,
    }

    try:
        board = mfa.build_sector_board(
            conn, chain=chain, horizon=h, level=level, limit=200, cfg=mcfg,
        )
    except Exception as e:  # noqa: BLE001
        return {
            **base,
            "status": STATUS_UNAVAILABLE,
            "reason": f"moneyflow_unavailable:{type(e).__name__}",
        }

    board_as_of = board.get("as_of")
    mf_trust = _as_of_trust(conn, board_as_of, max_lag=max_lag)
    base["inputs"]["moneyflow"] = {
        "trust": mf_trust["trust"],
        "reason": mf_trust["reason"],
        "as_of": board_as_of,
        "count": board.get("count"),
        "chain": chain,
    }

    try:
        inter = di.build_intersection_strongest(
            conn, horizon=h, limit=max(inter_n, 20), cfg=di.load_cfg(),
        )
    except Exception as e:  # noqa: BLE001
        return {
            **base,
            "status": STATUS_UNAVAILABLE,
            "reason": f"intersection_unavailable:{type(e).__name__}",
        }

    inter_status = inter.get("status")
    inter_as_of = (inter.get("as_of") or {}).get("dc_industry")
    if inter_status in (di.STATUS_STALE, "stale"):
        inter_trust = {
            "trust": UNTRUSTED,
            "reason": inter.get("reason") or "intersection_stale",
        }
    else:
        inter_trust = _as_of_trust(conn, inter_as_of, max_lag=max_lag)
        if inter_trust["trust"] == TRUSTED and inter_status not in ("ok", STATUS_OK):
            inter_trust = {
                "trust": UNTRUSTED,
                "reason": inter.get("reason") or f"intersection_status_{inter_status}",
            }
    base["inputs"]["intersection"] = {
        "trust": inter_trust["trust"],
        "reason": inter_trust["reason"],
        "as_of": inter.get("as_of"),
        "count": inter.get("count"),
        "status": inter_status,
    }

    screener_payload = None
    try:
        screener_payload = screener.build_form_stage_screen(
            conn, is_breakout_event=True, limit=brk_n,
        )
        sc_as_of = screener_payload.get("as_of")
        sc_trust = _as_of_trust(conn, sc_as_of, max_lag=max_lag)
        if screener_payload.get("status") not in ("ok", STATUS_OK, None):
            # empty ok still trusted if as_of fresh
            if screener_payload.get("status") == "stale":
                sc_trust = {
                    "trust": UNTRUSTED,
                    "reason": screener_payload.get("reason") or "screener_stale",
                }
        base["inputs"]["screener"] = {
            "trust": sc_trust["trust"],
            "reason": sc_trust["reason"],
            "as_of": sc_as_of,
            "count": screener_payload.get("count"),
        }
    except Exception:  # noqa: BLE001 — Cap B optional but trust marked untrusted
        base["inputs"]["screener"] = {
            "trust": UNTRUSTED,
            "reason": "screener_unavailable",
            "as_of": None,
        }

    # Fail-closed: Cap A or Cap D untrusted → no narrative.
    bad: list[str] = []
    if base["inputs"]["moneyflow"]["trust"] != TRUSTED:
        bad.append(f"moneyflow:{base['inputs']['moneyflow']['reason']}")
    if base["inputs"]["intersection"]["trust"] != TRUSTED:
        bad.append(f"intersection:{base['inputs']['intersection']['reason']}")
    if base["inputs"]["screener"]["trust"] != TRUSTED:
        # Cap B is required by CX-3 acceptance tests for full trust set.
        bad.append(f"screener:{base['inputs']['screener']['reason']}")

    if bad:
        return {
            **base,
            "status": STATUS_STALE,
            "reason": ";".join(bad),
            "as_of": board_as_of,
            "narrative": None,
            "sections": [],
        }

    base["as_of"] = str(board_as_of)

    # Build sections from trusted bricks.
    mf_items: list[dict[str, Any]] = []
    for row in (board.get("rows") or [])[: max(sample_n * 3, 9)]:
        beh = ((row.get("behavior") or {}).get("behavior")) or "unknown"
        if beh == "unknown":
            continue
        text = row.get("conclusion")
        if text:
            mf_items.append(
                {
                    "kind": "moneyflow_conclusion",
                    "sector_code": row.get("sector_code"),
                    "sector_name": row.get("sector_name"),
                    "behavior": beh,
                    "text": str(text),
                }
            )
        if len(mf_items) >= sample_n * 3:
            break

    inter_items: list[dict[str, Any]] = []
    for row in (inter.get("rows") or [])[:inter_n]:
        if row.get("why"):
            inter_items.append(
                {
                    "kind": "intersection_why",
                    "stock_code": row.get("stock_code"),
                    "stock_name": row.get("stock_name"),
                    "text": str(row["why"]),
                }
            )

    sc_items: list[dict[str, Any]] = []
    if screener_payload:
        for row in (screener_payload.get("rows") or [])[:brk_n]:
            if row.get("why"):
                sc_items.append(
                    {
                        "kind": "screener_why",
                        "stock_code": row.get("stock_code"),
                        "stock_name": row.get("stock_name"),
                        "text": str(row["why"]),
                    }
                )

    sections = [
        {
            "id": "moneyflow",
            "title": "资金行为（Cap A）",
            "count": len(mf_items),
            "items": mf_items,
        },
        {
            "id": "intersection",
            "title": "三链交集（Cap D）",
            "count": len(inter_items),
            "items": inter_items,
        },
        {
            "id": "screener",
            "title": "形态突破（Cap B）",
            "count": len(sc_items),
            "items": sc_items,
        },
    ]
    base["sections"] = sections

    # Compact narrative: lead with counts + first conclusions.
    parts: list[str] = []
    latent_n = sum(1 for i in mf_items if i.get("behavior") == "latent")
    chase_n = sum(1 for i in mf_items if i.get("behavior") == "chase")
    dist_n = sum(1 for i in mf_items if i.get("behavior") == "distribute")
    parts.append(
        f"截至 {board_as_of}：资金板样本潜伏{latent_n}/抢筹{chase_n}/出货{dist_n}；"
        f"交集{int(inter.get('count') or 0)}只；突破样本{len(sc_items)}。"
    )
    for item in (mf_items[:2] + inter_items[:2] + sc_items[:1]):
        parts.append(str(item["text"]))
    base["narrative"] = " ".join(parts) if parts else None
    if not base["narrative"]:
        base["reason"] = "empty_narrative_inputs"
    return base


__all__ = ["STATUS_OK", "STATUS_STALE", "STATUS_UNAVAILABLE", "load_cfg", "build_daily_briefing"]
