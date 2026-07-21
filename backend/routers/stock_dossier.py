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
    try:
        yield con
    finally:
        con.close()


def _norm_code(code: str) -> str:
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        return c
    raise HTTPException(status_code=400, detail="stock code must be 6 digits")


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

    periods = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT report_date
            FROM fact_top10_holder_period
            WHERE stock_code = ? AND holder_set = 'free'
            ORDER BY report_date DESC
            LIMIT 8
            """,
            [code],
        ).fetchall()
    ]
    presence: dict[str, int] = {}
    if periods:
        ph = conn.execute(
            """
            SELECT holder_name_norm, report_date
            FROM fact_top10_holder_period
            WHERE stock_code = ? AND holder_set = 'free'
              AND coalesce(is_exit_row, FALSE) = FALSE
              AND report_date IN ({})
            """.format(",".join(["?"] * len(periods))),
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
    for row in rows:
        d = dict(zip(cols, row))
        hn = d.get("holder_name_norm") or d.get("holder_name")
        d["approx_periods_present"] = presence.get(str(hn)) if hn else None
        d["return_pct"] = None
        d["holding_cycle_days"] = None
        out_rows.append(d)

    gaps.extend(
        [
            "holder_return_pct_unknown",
            "holding_cycle_days_unknown",
            "approx_periods_present_is_heuristic_not_episode_engine",
        ]
    )
    prev = periods[1] if len(periods) >= 2 else None
    return {
        "report_date": report_date,
        "prev_report_date": prev,
        "source": source,
        "rows": out_rows,
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
        "basic": basic,
        "form_stage": form,
        "observation": observation,
        "holders": holders,
        "gaps": sorted(set(gaps)),
        "pit_notes": [
            "form trade_date = observation day of fact_stock_form_daily builder",
            "holders available_at/notice_date bound disclosure — not same-day as K",
            "observation.text is product label stock_dossier_obs_v0 — not Tier0",
        ],
    }
