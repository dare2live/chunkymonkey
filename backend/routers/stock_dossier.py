"""Stock dossier read API — Cap F decision-assist surface (product consume).

GET /api/v3/stock/{code}/dossier

Cap F scope (沪深A): overview / form·stage / holders(+机构 deep-link) /
holder_number (concentration assist) work, or fail closed with typed reason.
Moneyflow + intersection tabs delegate to Cap A/D APIs (same page). Observation
text is a versioned product label — never Tier0. Episode overlay supplies
this-stock cycle/return when measured; never invent PnL.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect
from services.holdernumber_assist import load_holdernumber_assist
from services.universe import classify_exclusion

router = APIRouter()
SURFACE_STATUS = "stock_dossier_cap_f_usable"
OBS_VERSION = "stock_dossier_obs_v0"
_ASOF_TZ = ZoneInfo("Asia/Shanghai")

_AXIS_POS = {"low": "低位", "mid": "中位", "high": "高位"}
_AXIS_TREND = {
    "up": "上行",
    "down": "下行",
    "side": "横盘",
    "sideways": "横盘",
    "flat": "横盘",
}
# Live fact_stock_form_daily vocabulary (technical_states.yaml / stock_screener.yaml).
# Do not revive unused clean/mixed/light labels — they never appear in production rows.
_AXIS_PURITY = {"trending": "结构干净", "choppy": "结构嘈杂"}
_AXIS_VOL = {"heavy": "放量", "shrink": "缩量", "normal": "常量"}


def get_dossier_conn():
    con = duck_connect(resolver.db_path("smartmoney"), read_only=True)
    # Attach feature_store read-only so holder rows can honestly report whether
    # an institution profile exists (0r.5b — no fake 机构 deep-link at ~54%).
    try:
        fs_path = resolver.db_path("feature_store")
        con.execute(f"ATTACH IF NOT EXISTS '{fs_path}' AS fs (READ_ONLY)")
    except Exception:  # noqa: BLE001 — profile join is best-effort, fail-open to unknown
        pass
    try:
        yield con
    finally:
        con.close()


def _institution_profile_holders(conn, holder_norms: list[str]) -> dict[str, dict[str, Any]]:
    """Map holder_name_norm → profile honesty flags (deep-link gate).

    After 2026-07-23 coverage lift, display rows exist for nearly all holders
    with episodes; thin/passive rows still deep-link but carry ``low_sample`` /
    ``metrics_status``. Only link when a profile row actually exists.
    """
    names = [n for n in {h for h in holder_norms if h}]
    if not names:
        return {}
    try:
        placeholders = ",".join(["?"] * len(names))
        try:
            rows = conn.execute(
                f"""
                SELECT holder, low_sample, n_closed, metrics_status
                FROM fs.mart_inst_profile
                WHERE holder IN ({placeholders})
                """,
                names,
            ).fetchall()
            schema = "metrics_status"
        except Exception:  # noqa: BLE001 — pre-lift schema without metrics_status
            try:
                rows = conn.execute(
                    f"""
                    SELECT holder, low_sample, n_closed
                    FROM fs.mart_inst_profile
                    WHERE holder IN ({placeholders})
                    """,
                    names,
                ).fetchall()
                schema = "rich"
            except Exception:  # noqa: BLE001 — older/test schema without low_sample
                rows = conn.execute(
                    f"SELECT holder FROM fs.mart_inst_profile WHERE holder IN ({placeholders})",
                    names,
                ).fetchall()
                schema = "bare"
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            holder = str(r[0])
            if schema == "metrics_status":
                out[holder] = {
                    "low_sample": bool(r[1]) if r[1] is not None else False,
                    "n_closed": int(r[2]) if r[2] is not None else 0,
                    "metrics_status": str(r[3]) if r[3] is not None else None,
                }
            elif schema == "rich":
                out[holder] = {
                    "low_sample": bool(r[1]) if r[1] is not None else False,
                    "n_closed": int(r[2]) if r[2] is not None else 0,
                    "metrics_status": None,
                }
            else:
                out[holder] = {
                    "low_sample": False,
                    "n_closed": 0,
                    "metrics_status": None,
                }
        return out
    except Exception:  # noqa: BLE001 — feature_store absent → all unknown, fail-open
        return {}


def _load_holder_episodes(conn, code: str, holder_norms: list[str]) -> dict[str, dict[str, Any]]:
    """This-stock investment episodes per holder (2F deepen; product surface).

    Reads ``fs.fact_inst_episode`` (holder × stock state machine over disclosure
    periods). Product dossier legitimately shows "战绩截至今天" for manual choice
    (institution_profile.py §读侧). Returns keyed by holder_name_norm the most
    recent episode on this stock: 建仓期 / 状态 / 加减仓次数 / 收益 (measured only
    for closed episodes; unknown otherwise — never fake PnL for holding legs).

    Holding days honesty: closed → disclosure period-boundary calendar days
    (open→close); holding → open→today (as-of), flagged as disclosure-anchored,
    not true intraperiod entry/exit.
    """
    names = [n for n in {h for h in holder_norms if h}]
    if not names:
        return {}
    try:
        placeholders = ",".join(["?"] * len(names))
        rows = conn.execute(
            f"""
            SELECT holder, open_date, close_date, status, n_adds, n_trims,
                   ret_c1, alpha_c1, seeded, is_passive
            FROM fs.fact_inst_episode
            WHERE stock = ? AND holder IN ({placeholders})
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY holder ORDER BY open_date DESC
            ) = 1
            """,
            [code, *names],
        ).fetchall()
    except Exception:  # noqa: BLE001 — feature_store absent → no episode overlay
        return {}
    out: dict[str, dict[str, Any]] = {}
    cols = [
        "holder", "open_date", "close_date", "status", "n_adds", "n_trims",
        "ret_c1", "alpha_c1", "seeded", "is_passive",
    ]
    as_of = datetime.now(_ASOF_TZ).date()  # rule-compliance: ok evidence=持仓周期是自然日概念(披露开仓日→今天), 非交易日; basis 字段标为 disclosure_open_to_asof_holding
    for row in rows:
        d = dict(zip(cols, row))
        holder = str(d.pop("holder"))
        is_closed = d.get("status") == "closed"
        # Only closed + measured episodes carry a return; never invent PnL.
        d["return_measured"] = bool(is_closed and d.get("ret_c1") is not None)
        if not d["return_measured"]:
            d["ret_c1"] = None
            d["alpha_c1"] = None
        days, basis = _episode_holding_days(
            d.get("open_date"), d.get("close_date"), d.get("status"), as_of=as_of
        )
        d["holding_cycle_days"] = days
        d["holding_cycle_basis"] = basis
        out[holder] = d
    return out


def _parse_ymd(raw: Any) -> date | None:
    s = str(raw or "").replace("-", "")[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _episode_holding_days(
    open_date: Any,
    close_date: Any,
    status: Any,
    *,
    as_of: date | None = None,
) -> tuple[int | None, str | None]:
    """Disclosure-anchored calendar days — not true intraperiod entry/exit."""
    opened = _parse_ymd(open_date)
    if opened is None:
        return None, None
    if str(status or "") == "closed":
        closed = _parse_ymd(close_date)
        if closed is None or closed < opened:
            return None, None
        return (closed - opened).days, "disclosure_open_to_close"
    end = as_of or datetime.now(_ASOF_TZ).date()  # rule-compliance: ok evidence=同上, 未平仓持仓天数按自然日计, 不查交易日历
    if end < opened:
        return None, None
    return (end - opened).days, "disclosure_open_to_asof_holding"


def _norm_code(code: str) -> str:
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        return c
    raise HTTPException(status_code=400, detail="stock code must be 6 digits")


def _require_hs_a(code: str) -> None:
    """沪深A whitelist — same board policy as universe (60/00/30/68)."""
    reason = classify_exclusion(code)
    if reason is not None:
        raise HTTPException(
            status_code=404,
            detail=f"not in 沪深A whitelist: {code} ({reason})",
        )


def _zh(mapping: dict[str, str], raw: Any) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    return mapping.get(key) or (str(raw) if str(raw).strip() else None)


def _compose_observation(form: dict[str, Any] | None) -> dict[str, Any]:
    if not form:
        return {
            "version": OBS_VERSION,
            "text": None,
            "as_of": None,
            "status": "unknown",
        }
    parts = [
        p
        for p in (
            _zh(_AXIS_POS, form.get("axis_pos")),
            _zh(_AXIS_TREND, form.get("axis_trend")),
            _zh(_AXIS_PURITY, form.get("axis_purity")),
            _zh(_AXIS_VOL, form.get("axis_vol")),
        )
        if p
    ]
    form_name = form.get("form_name")
    as_of = form.get("trade_date")
    text = None
    if parts or form_name:
        chunks: list[str] = []
        if parts:
            chunks.append(f"当前阶段偏{' · '.join(parts)}")
        if form_name:
            chunks.append(f"形态为{form_name}")
        text = "，".join(chunks)
        if as_of:
            text = f"{text}（截至 {as_of}）"
        text = f"{text}。"
    return {
        "version": OBS_VERSION,
        "text": text,
        "as_of": as_of,
        "status": "ok" if text else "unknown",
        "axes_zh": parts,
        "form_name": form_name,
    }


def _load_basic(conn, code: str) -> dict[str, Any]:
    name = None
    name_source = None
    try:
        from services.security_master import active_stock_name_map

        names = active_stock_name_map([code], conn=conn)
        if names.get(code):
            name, name_source = str(names[code]), "dim_active_a_stock"
    except Exception:  # noqa: BLE001 — identity dim optional; fail-open unknown name
        name, name_source = None, None
    ind = None
    try:
        irow = conn.execute(
            """
            SELECT tdx_l1, tdx_l1_name, tdx_l2, tdx_l2_name, tdx_l3, tdx_l3_name,
                   CAST(updated_at AS VARCHAR)
            FROM dim_stock_dc_industry
            WHERE stock_code = ?
            LIMIT 1
            """,
            [code],
        ).fetchone()
        if irow:
            ind = {
                "l1_code": irow[0],
                "l1_name": irow[1],
                "l2_code": irow[2],
                "l2_name": irow[3],
                "l3_code": irow[4],
                "l3_name": irow[5],
                "updated_at": irow[6],
                "source": "dim_stock_dc_industry",
            }
    except Exception:  # noqa: BLE001 — dim optional
        ind = None
    return {
        "stock_code": code,
        "stock_name": name,
        "name_source": name_source,
        "industry": ind,
    }


def _load_form(conn, code: str, as_of: str | None) -> dict[str, Any] | None:
    # Shared with Cap 5B screener — must stay lockstep on production-read cutover.
    from services.form_production_read import load_form_row

    return load_form_row(conn, code, as_of)


def _table_exists(conn, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
        [name],
    ).fetchone()
    return r is not None


def _load_holders(conn, code: str) -> dict[str, Any]:
    gaps: list[str] = []
    if not _table_exists(conn, "canonical_top10_float_holders_period"):
        return {
            "report_date": None,
            "source": None,
            "rows": [],
            "prev_report_date": None,
            "gaps": ["holders_empty", "canonical_absent"],
        }
    source = "canonical_top10_float_holders_period"
    latest = conn.execute(
        """
        SELECT max(report_date) FROM canonical_top10_float_holders_period
        WHERE stock_code = ? AND coalesce(is_exit_row, FALSE) = FALSE
        """,
        [code],
    ).fetchone()
    report_date = latest[0] if latest else None
    if not report_date:
        return {
            "report_date": None,
            "source": None,
            "rows": [],
            "prev_report_date": None,
            "gaps": ["holders_empty"],
        }

    rows = conn.execute(
        """
        SELECT holder_rank, holder_name, holder_name_norm, holder_type,
               hold_ratio_float, change_status, hold_change_num,
               CAST(available_at AS VARCHAR), notice_date, shares_approx
        FROM canonical_top10_float_holders_period
        WHERE stock_code = ? AND report_date = ?
          AND coalesce(is_exit_row, FALSE) = FALSE
        ORDER BY holder_rank NULLS LAST, holder_name
        """,
        [code, report_date],
    ).fetchall()
    cols = [
        "holder_rank",
        "holder_name",
        "holder_name_norm",
        "holder_type",
        "hold_ratio_float",
        "change_status",
        "hold_change_num",
        "available_at",
        "notice_date",
        "shares_approx",
    ]

    period_sql = """
        SELECT DISTINCT report_date
        FROM canonical_top10_float_holders_period
        WHERE stock_code = ?
        ORDER BY report_date DESC
        LIMIT 8
    """
    presence_sql = """
        SELECT holder_name_norm, report_date
        FROM canonical_top10_float_holders_period
        WHERE stock_code = ?
          AND coalesce(is_exit_row, FALSE) = FALSE
          AND report_date IN ({})
    """
    periods = [r[0] for r in conn.execute(period_sql, [code]).fetchall()]
    presence: dict[str, int] = {}
    if periods:
        ph = conn.execute(
            presence_sql.format(",".join(["?"] * len(periods))),
            [code, *periods],
        ).fetchall()
        by_holder: dict[str, set[str]] = {}
        for hn, rd in ph:
            if not hn:
                continue
            by_holder.setdefault(str(hn), set()).add(str(rd))
        ordered = sorted(periods, reverse=True)
        for hn, seen in by_holder.items():
            streak = 0
            for rd in ordered:
                if rd in seen:
                    streak += 1
                else:
                    break
            presence[hn] = streak

    out_rows = []
    holder_norms: list[str] = []
    for row in rows:
        d = dict(zip(cols, row))
        hn = d.get("holder_name_norm") or d.get("holder_name")
        if hn:
            holder_norms.append(str(hn))
        d["approx_periods_present"] = presence.get(str(hn)) if hn else None
        d["return_pct"] = None
        d["holding_cycle_days"] = None
        d["holding_cycle_basis"] = None
        out_rows.append(d)

    # 机构档案 honesty: deep-link only when a profile row truly exists.
    profiled = _institution_profile_holders(conn, holder_norms)
    # 2F deepen: this-stock episode overlay (建仓期/状态/加减仓/收益 — measured only).
    episodes = _load_holder_episodes(conn, code, holder_norms)
    n_with_profile = 0
    n_with_episode = 0
    n_episode_only = 0
    n_low_sample = 0
    n_return_measured = 0
    n_cycle_known = 0
    for d in out_rows:
        hn = d.get("holder_name_norm") or d.get("holder_name")
        key = str(hn) if hn else ""
        profile = profiled.get(key) if key else None
        has_profile = profile is not None
        d["has_institution_profile"] = has_profile
        d["institution_profile_low_sample"] = (
            bool(profile.get("low_sample")) if profile else False
        )
        d["institution_metrics_status"] = (
            profile.get("metrics_status") if profile else None
        )
        ep = episodes.get(key) if key else None
        d["episode"] = ep
        if ep:
            n_with_episode += 1
            # Cap F: surface cycle/return from episode when known (typed unknown else).
            d["holding_cycle_days"] = ep.get("holding_cycle_days")
            d["holding_cycle_basis"] = ep.get("holding_cycle_basis")
            if d["holding_cycle_days"] is not None:
                n_cycle_known += 1
            if ep.get("return_measured") and ep.get("alpha_c1") is not None:
                d["return_pct"] = float(ep["alpha_c1"])
                n_return_measured += 1
        if has_profile:
            n_with_profile += 1
            if d["institution_profile_low_sample"]:
                n_low_sample += 1
            d["institution_link_status"] = (
                "profile_low_sample"
                if d["institution_profile_low_sample"]
                else "profile"
            )
        elif ep:
            n_episode_only += 1
            d["institution_link_status"] = "episode_only_no_profile"
        else:
            d["institution_link_status"] = "none"
    n_holders = len(out_rows)
    coverage = round(n_with_profile / n_holders, 4) if n_holders else None

    # Surface gaps = fail-closed / empty only. Per-row unknowns live on the row
    # (return_pct / holding_cycle_* / institution_link_status) — not MVP fog.
    if source == "canonical_top10_float_holders_period":
        # Honesty lineage, not a usability hole (formal-only is the intended path).
        pass
    if n_holders and n_with_profile < n_holders:
        gaps.append("institution_profile_absent_no_deep_link")
    if n_episode_only:
        gaps.append("institution_episode_without_profile_mart_row")
    prev = periods[1] if len(periods) >= 2 else None
    return {
        "report_date": report_date,
        "prev_report_date": prev,
        "source": source,
        "rows": out_rows,
        "institution_profile": {
            "holders_total": n_holders,
            "holders_with_profile": n_with_profile,
            "holders_episode_only": n_episode_only,
            "holders_profile_low_sample": n_low_sample,
            "coverage": coverage,
            "note": (
                "deep-link 机构档案 only when has_institution_profile=true; "
                "episode_only_no_profile = 本股 episode 有、mart 无行（勿假链）；"
                "closed-loop mart_inst_profile ≈ episode coverage; thin/passive "
                "rows carry low_sample + metrics_status — never invent alpha"
            ),
        },
        "episode_overlay": {
            "holders_with_episode": n_with_episode,
            "holders_return_measured": n_return_measured,
            "holders_cycle_known": n_cycle_known,
            "note": (
                "this-stock episode (建仓期/状态/加减仓/持仓日历天); 收益 measured "
                "only for closed episodes (alpha_c1 → return_pct) — holding legs "
                "stay unknown, never faked; disclosure-anchored days, not "
                "intraperiod truth; approx_periods_present remains heuristic streak"
            ),
        },
        "gaps": gaps,
    }


def _tab_usability(
    *,
    basic: dict[str, Any],
    form: dict[str, Any] | None,
    holders: dict[str, Any],
    holder_number: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Per-tab usable|empty|typed — Cap F 100% = no half-dead silent empties."""
    holder_rows = holders.get("rows") or []
    hn_ok = holder_number.get("status") == "ok"
    form_status = "ok" if form else "empty"
    form_reason = None
    if form:
        residuals = form.get("hybrid_residual_fields") or []
        note = str(form.get("resolver_note") or "")
        if residuals or "BLOCKED" in note or "legacy/fact" in note:
            form_reason = "form_read_fact_brick_typed_hybrid"
    return {
        "status": "usable",
        "cap": "F",
        "tabs": {
            "overview": {
                "status": "ok" if observation.get("text") or basic.get("stock_name") else "empty",
                "reason": None
                if (observation.get("text") or basic.get("stock_name"))
                else "overview_bricks_empty",
            },
            "form": {"status": form_status, "reason": form_reason or (
                "form_stage_empty" if form_status == "empty" else None
            )},
            "holders": {
                "status": "ok" if holder_rows else "empty",
                "reason": None if holder_rows else "holders_empty",
            },
            "holder_number": {
                "status": "ok" if hn_ok else "empty",
                "reason": None if hn_ok else (
                    str(holder_number.get("reason") or "holder_number_empty")
                ),
            },
            "moneyflow": {
                "status": "delegated",
                "reason": "cap_a_api",
                "api": "/api/v3/decision/moneyflow/stock/{code}",
            },
            "intersection": {
                "status": "delegated",
                "reason": "cap_d_api",
                "api": "/api/v3/decision/intersection/stock/{code}",
            },
        },
    }


@router.get("/{code}/dossier")
def dossier(
    code: str,
    as_of: str | None = Query(default=None, description="YYYYMMDD form as-of (optional)"),
    conn=Depends(get_dossier_conn),
):
    """Cap F stock archive. Product observations only; fail-closed empties."""
    code = _norm_code(code)
    _require_hs_a(code)
    if as_of is not None:
        as_of = as_of.replace("-", "")
        if len(as_of) != 8 or not as_of.isdigit():
            raise HTTPException(status_code=400, detail="as_of must be YYYYMMDD")

    basic = _load_basic(conn, code)
    form = _load_form(conn, code, as_of)
    holders = _load_holders(conn, code)
    holder_number = load_holdernumber_assist(code, as_of)
    observation = _compose_observation(form)

    gaps = list(holders.get("gaps") or [])
    if form is None:
        gaps.append("form_stage_empty")
    if basic.get("stock_name") is None:
        gaps.append("stock_name_unknown")
    if holder_number.get("status") != "ok":
        gaps.append(str(holder_number.get("reason") or "holder_number_empty"))
    if form is not None:
        note = str(form.get("resolver_note") or "")
        residuals = form.get("hybrid_residual_fields") or []
        if residuals or "BLOCKED" in note or "legacy/fact" in note:
            # Typed honesty — surface still usable on fact brick (not half-dead).
            gaps.append("form_read_fact_brick_typed_hybrid")

    found = bool(basic.get("stock_name") or form or (holders.get("rows")))
    if not found:
        raise HTTPException(status_code=404, detail=f"no dossier bricks for {code}")

    usability = _tab_usability(
        basic=basic,
        form=form,
        holders=holders,
        holder_number=holder_number,
        observation=observation,
    )
    inst = holders.get("institution_profile") or {}
    cov = inst.get("coverage")
    inst_join = (
        "FIXED"
        if cov is not None and float(cov) >= 0.95
        else "HONESTY_GATED"
        if cov is not None
        else "UNKNOWN"
    )

    return {
        "status": "ok",
        "surface": SURFACE_STATUS,
        "usability": usability,
        "stock_code": code,
        "universe": {
            "policy": "active_a_share_trading_universe",
            "whitelist": "沪深A",
            "board_prefixes": ["60", "00", "30", "68"],
        },
        "basic": basic,
        "form_stage": form,
        "observation": observation,
        "holders": holders,
        "holder_number": holder_number,
        "lineage": {
            "status": "attested_usable",
            "audit": "analysis/dossier_100_usable_20260723.md",
            "prior_audit": "analysis/FOUNDATION_EXECUTION_PLAN.md",

            "holders_parse_integrity": "PASS",
            "stock_holder_assoc_readiness": "FIXED",
            "institution_join": inst_join,
            "institution_profile_coverage": inst,
            "holders_watermark_frontier": "canonical_notice_frontier",
            "holder_number_axis": "ann_date_pit_raw_evidence",
            "note": (
                "Cap F usable: HS-A dossier tabs work or fail closed with typed "
                "reason. Institution deep-link only when mart_inst_profile row "
                "exists (closed-loop process); episode supplies this-stock "
                "cycle/return when measured. Form may be fact-brick typed hybrid "
                "while accepted overlay BLOCKED — honesty, not a dead tab. "
                "holder_number = concentration assist via DataAccess (not Optuna). "
                "Moneyflow/intersection delegated to Cap A/D APIs."
            ),
        },
        "gaps": sorted(set(gaps)),
        "pit_notes": [
            "form trade_date = observation day of fact_stock_form_daily builder",
            "holders available_at/notice_date bound disclosure — not same-day as K",
            "holder_number JOIN on ann_date (end_date is report period only)",
            "observation.text is product label stock_dossier_obs_v0 — not Tier0",
            "serve rejects non-沪深A via classify_exclusion (B/BJ/etc.)",
            "holding_cycle_days = disclosure period-boundary calendar days",
        ],
    }
