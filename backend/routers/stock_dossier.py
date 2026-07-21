"""Stock dossier read API — decision-assist surface (product Tier4-ish consume).

GET /api/v3/stock/{code}/dossier

Layers accepted/form/holders only. Observation text is a versioned product label —
never written back as Tier0. Holder PnL / full holding-cycle engine = gaps.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect
from services.universe import classify_exclusion

router = APIRouter()
SURFACE_STATUS = "stock_dossier_mvp_partial"
OBS_VERSION = "stock_dossier_obs_v0"

_AXIS_POS = {"low": "低位", "mid": "中位", "high": "高位"}
_AXIS_TREND = {
    "up": "上行",
    "down": "下行",
    "side": "横盘",
    "sideways": "横盘",
    "flat": "横盘",
}
_AXIS_PURITY = {"clean": "结构干净", "choppy": "结构嘈杂", "mixed": "结构混杂"}
_AXIS_VOL = {"light": "缩量", "normal": "常量", "heavy": "放量"}


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


def _institution_profile_holders(conn, holder_norms: list[str]) -> set[str]:
    """Return the subset of holder_name_norm that have an institution profile.

    Profile coverage is ~54% (holders_stock_dossier_lineage_audit_20260721 §2.1);
    the dossier must only deep-link a holder chip to 机构档案 when a profile row
    actually exists — never claim full 股东↔机构 linkage.
    """
    names = [n for n in {h for h in holder_norms if h} ]
    if not names:
        return set()
    try:
        placeholders = ",".join(["?"] * len(names))
        rows = conn.execute(
            f"SELECT holder FROM fs.mart_inst_profile WHERE holder IN ({placeholders})",
            names,
        ).fetchall()
        return {str(r[0]) for r in rows}
    except Exception:  # noqa: BLE001 — feature_store absent → all unknown, fail-open
        return set()


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
    for row in rows:
        d = dict(zip(cols, row))
        holder = str(d.pop("holder"))
        is_closed = d.get("status") == "closed"
        # Only closed + measured episodes carry a return; never invent PnL.
        d["return_measured"] = bool(is_closed and d.get("ret_c1") is not None)
        if not d["return_measured"]:
            d["ret_c1"] = None
            d["alpha_c1"] = None
        out[holder] = d
    return out


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
    row = conn.execute(
        """
        SELECT stock_name
        FROM fact_top10_holder_period
        WHERE stock_code = ? AND stock_name IS NOT NULL AND length(stock_name) > 0
        ORDER BY report_date DESC
        LIMIT 1
        """,
        [code],
    ).fetchone()
    if row and row[0]:
        name, name_source = str(row[0]), "fact_top10_holder_period"
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
    keys = [
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
    out = dict(zip(keys, row))
    out["source"] = "fact_stock_form_daily"
    out["resolver_note"] = (
        "MVP reads fact_stock_form_daily directly; later prefer accepted "
        "stock_states via resolve_tier12_production_read"
    )
    return out


def _table_exists(conn, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ? LIMIT 1",
        [name],
    ).fetchone()
    return r is not None


def _load_holders(conn, code: str) -> dict[str, Any]:
    gaps: list[str] = []
    use_canonical = _table_exists(conn, "canonical_top10_float_holders_period")
    latest = None
    source = None
    if use_canonical:
        latest = conn.execute(
            """
            SELECT max(report_date) FROM canonical_top10_float_holders_period
            WHERE stock_code = ? AND coalesce(is_exit_row, FALSE) = FALSE
            """,
            [code],
        ).fetchone()
        if latest and latest[0]:
            source = "canonical_top10_float_holders_period"
    if not latest or not latest[0]:
        latest = conn.execute(
            """
            SELECT max(report_date) FROM fact_top10_holder_period
            WHERE stock_code = ? AND holder_set = 'free'
              AND coalesce(is_exit_row, FALSE) = FALSE
            """,
            [code],
        ).fetchone()
        source = "fact_top10_holder_period" if latest and latest[0] else None
    report_date = latest[0] if latest else None
    if not report_date:
        return {
            "report_date": None,
            "source": None,
            "rows": [],
            "prev_report_date": None,
            "gaps": ["holders_empty"],
        }

    if source == "canonical_top10_float_holders_period":
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
    else:
        rows = conn.execute(
            """
            SELECT holder_rank, holder_name, holder_name_norm, holder_type,
                   hold_ratio_float, change_status, hold_change_num,
                   NULL, notice_date, shares_approx
            FROM fact_top10_holder_period
            WHERE stock_code = ? AND report_date = ? AND holder_set = 'free'
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

    # Period streak / prev_report MUST use the same plane as rows.
    # Formal-only sync advances canonical while legacy fact watermark lags —
    # reading fact here silently drops the latest report (fail-closed bug).
    if source == "canonical_top10_float_holders_period":
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
    else:
        period_sql = """
            SELECT DISTINCT report_date
            FROM fact_top10_holder_period
            WHERE stock_code = ? AND holder_set = 'free'
            ORDER BY report_date DESC
            LIMIT 8
        """
        presence_sql = """
            SELECT holder_name_norm, report_date
            FROM fact_top10_holder_period
            WHERE stock_code = ? AND holder_set = 'free'
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
        out_rows.append(d)

    # 机构档案 honesty: deep-link only when a profile row truly exists (~54% coverage).
    profiled = _institution_profile_holders(conn, holder_norms)
    # 2F deepen: this-stock episode overlay (建仓期/状态/加减仓/收益 — measured only).
    episodes = _load_holder_episodes(conn, code, holder_norms)
    n_with_profile = 0
    n_with_episode = 0
    for d in out_rows:
        hn = d.get("holder_name_norm") or d.get("holder_name")
        has_profile = bool(hn) and str(hn) in profiled
        d["has_institution_profile"] = has_profile
        if has_profile:
            n_with_profile += 1
        ep = episodes.get(str(hn)) if hn else None
        d["episode"] = ep
        if ep:
            n_with_episode += 1
    n_holders = len(out_rows)
    coverage = round(n_with_profile / n_holders, 4) if n_holders else None

    gaps.extend(
        [
            "holder_return_pct_unknown",
            "holding_cycle_days_unknown",
            "approx_periods_present_is_heuristic_not_episode_engine",
        ]
    )
    if source == "canonical_top10_float_holders_period":
        gaps.append("legacy_fact_mirror_skipped_formal_only")
    if n_holders and n_with_profile < n_holders:
        gaps.append("institution_profile_partial_no_deep_link_when_absent")
    prev = periods[1] if len(periods) >= 2 else None
    return {
        "report_date": report_date,
        "prev_report_date": prev,
        "source": source,
        "rows": out_rows,
        "institution_profile": {
            "holders_total": n_holders,
            "holders_with_profile": n_with_profile,
            "coverage": coverage,
            "note": (
                "deep-link 机构档案 only when has_institution_profile=true; "
                "population coverage ~54% (audit §2.1) — absent ≠ no institution"
            ),
        },
        "episode_overlay": {
            "holders_with_episode": n_with_episode,
            "note": (
                "this-stock episode (建仓期/状态/加减仓); 收益 measured only for "
                "closed episodes (ret_c1/alpha_c1) — holding legs stay unknown, "
                "never faked; disclosure-anchored days, not intraperiod truth"
            ),
        },
        "gaps": gaps,
    }


@router.get("/{code}/dossier")
def dossier(
    code: str,
    as_of: str | None = Query(default=None, description="YYYYMMDD form as-of (optional)"),
    conn=Depends(get_dossier_conn),
):
    """Layered stock archive MVP. Product observations only; fail-closed empties."""
    code = _norm_code(code)
    _require_hs_a(code)
    if as_of is not None:
        as_of = as_of.replace("-", "")
        if len(as_of) != 8 or not as_of.isdigit():
            raise HTTPException(status_code=400, detail="as_of must be YYYYMMDD")

    basic = _load_basic(conn, code)
    form = _load_form(conn, code, as_of)
    holders = _load_holders(conn, code)
    observation = _compose_observation(form)

    gaps = list(holders.get("gaps") or [])
    if form is None:
        gaps.append("form_stage_empty")
    if basic.get("stock_name") is None:
        gaps.append("stock_name_unknown")
    gaps.append("moneyflow_assist_not_in_mvp")
    gaps.append("accepted_stock_states_resolver_not_wired")

    found = bool(basic.get("stock_name") or form or (holders.get("rows")))
    if not found:
        raise HTTPException(status_code=404, detail=f"no dossier bricks for {code}")

    return {
        "status": "ok",
        "surface": SURFACE_STATUS,
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
        "lineage": {
            "status": "attested_partial",
            "audit": "analysis/holders_stock_dossier_lineage_audit_20260721.md",
            "holders_parse_integrity": "PASS",
            "stock_holder_assoc_readiness": "PARTIAL",
            "institution_join": "PARTIAL",
            "institution_profile_coverage": holders.get("institution_profile"),
            "holders_watermark_frontier": "canonical_notice_frontier",
            "note": (
                "Sample PASS ≠ full PASS. Formal canonical parse integrity PASS; "
                "holders freshness watermark now = formal canonical notice frontier "
                "(0r.5b); institution profile join ~54% (deep-link per-row only when "
                "has_institution_profile=true); holder PnL/cycle engine still unknown."
            ),
        },
        "gaps": sorted(set(gaps)),
        "pit_notes": [
            "form trade_date = observation day of fact_stock_form_daily builder",
            "holders available_at/notice_date bound disclosure — not same-day as K",
            "observation.text is product label stock_dossier_obs_v0 — not Tier0",
            "serve rejects non-沪深A via classify_exclusion (B/BJ/etc.)",
        ],
    }
