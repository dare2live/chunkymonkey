"""Cap A moneyflow decision-assist — Tier3 product consumer (read-only).

Consumes ``mart_sector_pulse_daily`` + ``fact_stock_moneyflow(_dc)_daily``.
Never writes behavior labels into Tier0/Tier2. Moneyflow = vendor imbalance
proxy (AGENTS.md), not conserved cash.

Authority: analysis/product_decision_assist_backlog_20260721.md (Cap A) +
analysis/FOUNDATION_EXECUTION_PLAN.md §2.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services import market_pulse as mp
from services import stock_flow_streak as sfs

_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / "moneyflow_assist.yaml"

BEHAVIOR_LATENT = "latent"
BEHAVIOR_CHASE = "chase"
BEHAVIOR_DISTRIBUTE = "distribute"
BEHAVIOR_UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def load_cfg() -> dict[str, Any]:
    with open(_CFG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("moneyflow_assist.yaml must be a mapping")
    return cfg


def behavior_from_regime(
    regime: str | None,
    *,
    window_return_pct: float | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map Tier2 flow_regime → Tier3 product behavior (versioned, unknown-allowed)."""
    c = cfg or load_cfg()
    mapping: dict[str, str] = dict(c.get("regime_to_behavior") or {})
    labels_zh: dict[str, str] = dict(c.get("behavior_labels_zh") or {})
    key = (regime or "").strip() or None
    raw = mapping.get(key) if key else None
    label = raw if raw in {
        BEHAVIOR_LATENT, BEHAVIOR_CHASE, BEHAVIOR_DISTRIBUTE, BEHAVIOR_UNKNOWN,
    } else BEHAVIOR_UNKNOWN

    # Price-response honesty guards (product layer; does not mutate pulse).
    # Incomplete / unknown window return → never force chase/distribute.
    if label == BEHAVIOR_CHASE:
        floor = float(c.get("chase_min_window_return_pct", 0.0))
        if window_return_pct is None or window_return_pct <= floor:
            label = BEHAVIOR_UNKNOWN
    elif label == BEHAVIOR_DISTRIBUTE:
        ceil = float(c.get("distribute_max_window_return_pct", 1.0))
        if window_return_pct is None or window_return_pct > ceil:
            label = BEHAVIOR_UNKNOWN

    return {
        "behavior": label,
        "behavior_zh": labels_zh.get(label, "未形成标签"),
        "flow_regime": key,
        "version": c.get("behavior_version", "moneyflow_behavior_v0"),
        "window_return_pct": window_return_pct,
    }


def _horizons(cfg: dict[str, Any]) -> list[int]:
    hs = [int(h) for h in (cfg.get("horizons") or [1, 3, 5, 10, 20, 30, 60])]
    return sorted({h for h in hs if h > 0})


def _implied_sector_mv(cum_net_20: float | None, cum_ratio_20d: float | None) -> float | None:
    """Back out as-of sector_mv from published 20d cum_ratio (same-day denominator).

    cum_ratio_20d = cum_net_20 / sector_mv * 100 → sector_mv = cum_net_20 / (ratio/100).
    Missing either side → unknown (never invent).
    """
    if cum_net_20 is None or cum_ratio_20d is None:
        return None
    if cum_ratio_20d == 0:
        return None
    mv = cum_net_20 / (cum_ratio_20d / 100.0)
    return mv if mv > 0 else None


def _horizon_metrics(
    nets: list[float | None],
    pcts: list[float | None],
    *,
    horizon: int,
    sector_mv: float | None,
) -> dict[str, Any]:
    """nets/pcts are ascending by trade_date; take last ``horizon`` observations."""
    if horizon <= 0:
        return {
            "horizon": horizon,
            "status": "unknown",
            "coverage_days": 0,
            "cum_net": None,
            "relative_ratio_pct": None,
            "window_return_pct": None,
        }
    slice_n = nets[-horizon:] if len(nets) >= horizon else nets[:]
    slice_p = pcts[-horizon:] if len(pcts) >= horizon else pcts[:]
    present = [v for v in slice_n if v is not None]
    coverage = len(present)
    full = coverage == horizon and len(slice_n) == horizon
    cum_net = float(sum(present)) if present and full else None
    # Window return = sum of daily pct_change when every day present (unknown else).
    pct_present = [v for v in slice_p if v is not None]
    window_ret = float(sum(pct_present)) if full and len(pct_present) == horizon else None
    ratio = None
    if full and cum_net is not None and sector_mv is not None and sector_mv > 0:
        ratio = cum_net / sector_mv * 100.0
    return {
        "horizon": horizon,
        "status": "known" if full else "unknown",
        "coverage_days": coverage,
        "required_days": horizon,
        "cum_net": cum_net,
        "relative_ratio_pct": ratio,
        "window_return_pct": window_ret,
    }


def build_sector_board(
    conn,
    *,
    chain: str,
    horizon: int,
    level: str = "L1",
    limit: int = 20,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sector moneyflow assist board for one chain × one horizon.

    Reuses pulse ``flow_regime`` as evidence; product behavior is mapped, not
    fused into the mart. Relative ratio uses implied sector_mv from published
    cum_ratio_20d when available; horizon window must be full or status=unknown.
    """
    c = cfg or load_cfg()
    hs = _horizons(c)
    if horizon not in hs:
        raise ValueError(f"horizon must be one of {hs}; got {horizon}")
    if chain not in (c.get("chains") or [mp.CHAIN_DC_INDUSTRY]):
        raise ValueError(f"unsupported chain {chain}")
    limit = max(1, min(int(limit), 500))

    max_h = max(hs)
    lvl_filter, params = "", [chain]
    if chain == mp.CHAIN_SW:
        lvl_filter = "AND level = ?"
        params.append(level)

    # Latest date + history window per sector (enough for max horizon + 20d mv).
    lookback = max(max_h, 20)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT chain, sector_code, sector_name, level, content_type, trade_date,
                   pct_change, net_amount, flow_z, flow_streak, cum_ratio_20d, flow_regime,
                   ROW_NUMBER() OVER (
                       PARTITION BY sector_code ORDER BY trade_date DESC
                   ) AS rn
            FROM {mp.SECTOR_TABLE}
            WHERE chain = ? {lvl_filter}
        )
        SELECT chain, sector_code, sector_name, level, content_type, trade_date,
               pct_change, net_amount, flow_z, flow_streak, cum_ratio_20d, flow_regime
        FROM ranked
        WHERE rn <= ?
        ORDER BY sector_code, trade_date ASC
        """,
        [*params, lookback],
    ).fetchall()
    cols = [
        "chain", "sector_code", "sector_name", "level", "content_type", "trade_date",
        "pct_change", "net_amount", "flow_z", "flow_streak", "cum_ratio_20d", "flow_regime",
    ]
    by_code: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rec = dict(zip(cols, r))
        by_code.setdefault(str(rec["sector_code"]), []).append(rec)

    items: list[dict[str, Any]] = []
    as_of: str | None = None
    for code, hist in by_code.items():
        if not hist:
            continue
        latest = hist[-1]
        as_of = max(as_of or "", str(latest["trade_date"]))
        nets = [h["net_amount"] for h in hist]
        pcts = [h["pct_change"] for h in hist]
        # 20d cum_net for implied mv (full window only).
        m20 = _horizon_metrics(nets, pcts, horizon=20, sector_mv=None)
        sector_mv = _implied_sector_mv(m20["cum_net"], latest.get("cum_ratio_20d"))
        # If 20d ratio published but our window incomplete, leave mv unknown.
        if latest.get("cum_ratio_20d") is not None and m20["status"] != "known":
            sector_mv = None
        metrics = _horizon_metrics(nets, pcts, horizon=horizon, sector_mv=sector_mv)
        # Prefer published cum_ratio_20d when horizon==20 and known.
        if horizon == 20 and latest.get("cum_ratio_20d") is not None and metrics["status"] == "known":
            metrics["relative_ratio_pct"] = float(latest["cum_ratio_20d"])
        # Incomplete horizon → behavior unknown (never promote a label on thin history).
        if metrics.get("status") != "known":
            beh = behavior_from_regime(None, window_return_pct=None, cfg=c)
            beh["flow_regime"] = latest.get("flow_regime")
        else:
            beh = behavior_from_regime(
                latest.get("flow_regime"),
                window_return_pct=metrics.get("window_return_pct"),
                cfg=c,
            )
        conclusion = _conclusion_sentence(
            sector_name=str(latest.get("sector_name") or code),
            horizon=horizon,
            metrics=metrics,
            behavior=beh,
            cfg=c,
        )
        items.append({
            "sector_code": code,
            "sector_name": latest.get("sector_name"),
            "level": latest.get("level"),
            "content_type": latest.get("content_type"),
            "trade_date": latest.get("trade_date"),
            "flow_regime": latest.get("flow_regime"),
            "flow_z": latest.get("flow_z"),
            "flow_streak": latest.get("flow_streak"),
            "cum_ratio_20d": latest.get("cum_ratio_20d"),
            "horizon": metrics,
            "behavior": beh,
            "conclusion": conclusion,
        })

    # Rank by relative ratio when known; unknown sink; then |cum_net|.
    def _sort_key(it: dict[str, Any]) -> tuple:
        h = it["horizon"]
        ratio = h.get("relative_ratio_pct")
        cum = h.get("cum_net")
        return (
            0 if h.get("status") == "known" and ratio is not None else 1,
            -(ratio if ratio is not None else 0.0),
            0 if cum is not None else 1,
            -(cum if cum is not None else 0.0),
        )

    items.sort(key=_sort_key)
    labeled = [it for it in items if it["behavior"]["behavior"] != BEHAVIOR_UNKNOWN]
    # Prefer showing labeled rows first, still capped by limit.
    board = (labeled + [it for it in items if it not in labeled])[:limit]

    return {
        "status": "ok",
        "surface": "moneyflow_decision_assist",
        "behavior_version": c.get("behavior_version"),
        "disclaimer": c.get("disclaimer"),
        "chain": chain,
        "level": level if chain == mp.CHAIN_SW else None,
        "as_of": as_of,
        "horizon": horizon,
        "horizons": hs,
        "count": len(board),
        "rows": board,
    }


def _conclusion_sentence(
    *,
    sector_name: str,
    horizon: int,
    metrics: dict[str, Any],
    behavior: dict[str, Any],
    cfg: dict[str, Any],
) -> str | None:
    if metrics.get("status") != "known":
        return f"{sector_name}：近{horizon}日资金窗未满或分母未知 — 不形成结论。"
    label = behavior.get("behavior")
    if label == BEHAVIOR_UNKNOWN:
        return None
    zh = behavior.get("behavior_zh") or "未形成标签"
    ratio = metrics.get("relative_ratio_pct")
    ret = metrics.get("window_return_pct")
    ratio_s = f"{ratio:.2f}%" if isinstance(ratio, (int, float)) else "未知"
    ret_s = f"{ret:.2f}%" if isinstance(ret, (int, float)) else "未知"
    return (
        f"{sector_name}：近{horizon}日代理净流入/市值 {ratio_s}，"
        f"窗口涨跌 {ret_s}，呈{zh}（{cfg.get('disclaimer')}）"
    )


def build_stock_moneyflow(
    conn,
    *,
    stock_code: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-stock multi-horizon moneyflow + sector read-through behavior.

    DC plane primary (eastmoney stock net_amount); tushare plane separately
    labeled when present. Never blends the two into one number.
    """
    c = cfg or load_cfg()
    hs = _horizons(c)
    code = "".join(ch for ch in stock_code if ch.isdigit())[:6]
    max_h = max(hs)

    dc_rows = []
    try:
        dc_rows = conn.execute(
            """
            SELECT trade_date, net_amount, net_amount_rate, pct_change, available_at
            FROM fact_stock_moneyflow_dc_daily
            WHERE stock_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [code, max_h + 5],
        ).fetchall()
    except Exception:  # noqa: BLE001 — table absent → unknown plane
        dc_rows = []

    mf_rows = []
    try:
        mf_rows = conn.execute(
            """
            SELECT trade_date, net_mf_amount, available_at
            FROM fact_stock_moneyflow_daily
            WHERE stock_code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [code, max_h + 5],
        ).fetchall()
    except Exception:  # noqa: BLE001
        mf_rows = []

    circ_mv = None
    circ_asof = None
    try:
        r = conn.execute(
            """
            SELECT trade_date, circ_mv FROM dim_stock_segment_daily
            WHERE stock_code = ? AND circ_mv IS NOT NULL AND circ_mv > 0
            ORDER BY trade_date DESC LIMIT 1
            """,
            [code],
        ).fetchone()
        if r:
            circ_asof, circ_mv_wan = r[0], r[1]
            circ_mv = float(circ_mv_wan) * 10000.0  # 万元 → 元
    except Exception:  # noqa: BLE001
        pass

    def _plane(rows: list, net_idx: int, rate_idx: int | None, pct_idx: int | None) -> dict[str, Any]:
        if not rows:
            return {
                "status": "unknown",
                "as_of": None,
                "horizons": [
                    {
                        "horizon": h,
                        "status": "unknown",
                        "coverage_days": 0,
                        "required_days": h,
                        "cum_net": None,
                        "relative_ratio_pct": None,
                        "window_return_pct": None,
                    }
                    for h in hs
                ],
            }
        # rows DESC → reverse to ASC for horizon helpers
        asc = list(reversed(rows))
        nets = [r[net_idx] for r in asc]
        # Vendor amounts are 万元; convert to 元 for ratio vs circ_mv (元).
        nets_yuan = [None if v is None else float(v) * 10000.0 for v in nets]
        pcts = [r[pct_idx] if pct_idx is not None else None for r in asc]
        as_of = str(asc[-1][0])
        horizons_out = []
        for h in hs:
            m = _horizon_metrics(nets_yuan, pcts, horizon=h, sector_mv=circ_mv)
            # Prefer vendor net_amount_rate for 1d when present (already relative).
            if h == 1 and rate_idx is not None and asc and asc[-1][rate_idx] is not None:
                if m["status"] == "known":
                    m["relative_ratio_pct"] = float(asc[-1][rate_idx])
                    m["ratio_source"] = "vendor_net_amount_rate"
            horizons_out.append(m)
        return {"status": "ok", "as_of": as_of, "horizons": horizons_out}

    dc_plane = _plane(dc_rows, 1, 2, 3)
    ts_plane = _plane(mf_rows, 1, None, None)

    # Sector read-through: DC industry of this stock → latest pulse regime.
    sector_ctx = _sector_readthrough(conn, code, cfg=c)

    primary_beh = sector_ctx.get("behavior") if sector_ctx else {
        "behavior": BEHAVIOR_UNKNOWN,
        "behavior_zh": "未形成标签",
        "flow_regime": None,
        "version": c.get("behavior_version"),
        "window_return_pct": None,
    }

    # Stock conclusion prefers DC plane + sector behavior.
    dc_h20 = next((x for x in dc_plane.get("horizons", []) if x["horizon"] == 20), None)
    conclusion = None
    if sector_ctx and sector_ctx.get("conclusion"):
        conclusion = sector_ctx["conclusion"]
    elif dc_h20 and dc_h20.get("status") == "known" and primary_beh.get("behavior") != BEHAVIOR_UNKNOWN:
        conclusion = (
            f"本股东财主力近20日代理净流入/流通市值 "
            f"{dc_h20.get('relative_ratio_pct') if dc_h20.get('relative_ratio_pct') is not None else '未知'}%，"
            f"所属板块呈{primary_beh.get('behavior_zh')}（{c.get('disclaimer')}）"
        )

    # CX-3: expose signed stock-level flow streak for facet chip (serve brick; no UI invent).
    streak_block = sfs.compute_stock_flow_streak(conn, code)

    return {
        "status": "ok",
        "surface": "moneyflow_decision_assist_stock",
        "behavior_version": c.get("behavior_version"),
        "disclaimer": c.get("disclaimer"),
        "stock_code": code,
        "circ_mv": {
            "value": circ_mv,
            "as_of": circ_asof,
            "unit": "yuan",
            "note": "dim_stock_segment_daily.circ_mv ×10000; missing → ratios unknown",
        },
        "planes": {
            "moneyflow_dc": {
                "label": "东财主力（stock-day）",
                "vendor": "moneyflow_dc",
                **dc_plane,
            },
            "moneyflow_tushare": {
                "label": "tushare 全单（stock-day）",
                "vendor": "moneyflow",
                **ts_plane,
            },
        },
        "sector_context": sector_ctx,
        "behavior": primary_beh,
        "conclusion": conclusion,
        "flow_streak": streak_block.get("flow_streak"),
        "flow_streak_direction": streak_block.get("direction"),
        "flow_streak_as_of": streak_block.get("as_of"),
        "horizons": hs,
        "gaps": _stock_gaps(dc_plane, circ_mv, sector_ctx),
    }


def _sector_readthrough(conn, stock_code: str, *, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Attach DC industry pulse regime for this stock (read-only; no cross-chain)."""
    try:
        ind = conn.execute(
            """
            SELECT tdx_l3, tdx_l3_name, tdx_l1_name FROM dim_stock_dc_industry
            WHERE stock_code = ? LIMIT 1
            """,
            [stock_code],
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not ind or not ind[0]:
        return None
    sector_code = str(ind[0])
    try:
        row = conn.execute(
            f"""
            SELECT sector_code, sector_name, trade_date, pct_change, net_amount,
                   cum_ratio_20d, flow_regime, flow_z, flow_streak
            FROM {mp.SECTOR_TABLE}
            WHERE chain = ? AND sector_code = ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            [mp.CHAIN_DC_INDUSTRY, sector_code],
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return {
            "chain": mp.CHAIN_DC_INDUSTRY,
            "sector_code": sector_code,
            "sector_name": ind[1] or ind[2],
            "status": "unknown",
            "note": "industry mapped but no pulse row",
        }
    cols = [
        "sector_code", "sector_name", "trade_date", "pct_change", "net_amount",
        "cum_ratio_20d", "flow_regime", "flow_z", "flow_streak",
    ]
    rec = dict(zip(cols, row))
    # Single-day pct_change is not a multi-day window return — only use it when
    # present; never invent a window sum here. Missing pct → unknown behavior.
    day_ret = rec.get("pct_change")
    if day_ret is None or rec.get("cum_ratio_20d") is None:
        beh = behavior_from_regime(None, window_return_pct=None, cfg=cfg)
        beh["flow_regime"] = rec.get("flow_regime")
    else:
        beh = behavior_from_regime(rec.get("flow_regime"), window_return_pct=float(day_ret), cfg=cfg)
    metrics = {
        "status": "known" if rec.get("cum_ratio_20d") is not None else "unknown",
        "relative_ratio_pct": rec.get("cum_ratio_20d"),
        "window_return_pct": day_ret,
        "cum_net": rec.get("net_amount"),
    }
    return {
        "chain": mp.CHAIN_DC_INDUSTRY,
        "sector_code": rec["sector_code"],
        "sector_name": rec.get("sector_name") or ind[1],
        "trade_date": rec.get("trade_date"),
        "cum_ratio_20d": rec.get("cum_ratio_20d"),
        "flow_regime": rec.get("flow_regime"),
        "behavior": beh,
        "conclusion": _conclusion_sentence(
            sector_name=str(rec.get("sector_name") or sector_code),
            horizon=20,
            metrics={**metrics, "status": metrics["status"]},
            behavior=beh,
            cfg=cfg,
        ),
        "status": "ok",
    }


def _stock_gaps(dc_plane: dict, circ_mv: float | None, sector_ctx: dict | None) -> list[str]:
    gaps: list[str] = []
    if dc_plane.get("status") != "ok":
        gaps.append("fact_stock_moneyflow_dc_daily_absent_or_empty")
    if circ_mv is None:
        gaps.append("circ_mv_unknown_ratios_fail_closed")
    if not sector_ctx or sector_ctx.get("status") not in ("ok",):
        gaps.append("sector_pulse_readthrough_unknown")
    return gaps
