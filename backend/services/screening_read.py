"""Shared read-side helpers for screening and dual-confirm marts.

Keep screening routes and stock-trend enrichment on the same read model
so identical business facts are not reassembled differently in each place.
"""

from typing import Optional

_SCREENING_FORMULA_FIELDS = {
    "f1": "f1_hit",
    "f3": "f3_hit",
    "f5": "f5_hit",
}


def load_screening_snapshot_map(conn) -> dict[str, dict]:
    try:
        rows = conn.execute(
            """
            SELECT stock_code, screen_date, f1_hit, f3_hit, f5_hit, hit_count, float_market_cap
            FROM mart_stock_screening
            """
        ).fetchall()
    except Exception:
        return {}

    return {
        row["stock_code"]: {
            "screen_date": row["screen_date"],
            "f1_hit": bool(row["f1_hit"]),
            "f3_hit": bool(row["f3_hit"]),
            "f5_hit": bool(row["f5_hit"]),
            "hit_count": row["hit_count"] or 0,
            "float_market_cap": row["float_market_cap"],
        }
        for row in rows
    }


def load_dual_confirm_snapshot_map(conn) -> dict[str, dict]:
    try:
        rows = conn.execute(
            """
            SELECT stock_code,
                   COUNT(*) AS dual_confirm_count,
                   MAX(report_date) AS dual_confirm_latest_report_date
            FROM mart_dual_confirm
            WHERE dual_confirm = 1
            GROUP BY stock_code
            """
        ).fetchall()
    except Exception:
        return {}

    return {
        row["stock_code"]: {
            "dual_confirm_count": row["dual_confirm_count"] or 0,
            "dual_confirm_latest_report_date": row["dual_confirm_latest_report_date"],
        }
        for row in rows
    }


def list_dual_confirm_rows(conn, *, hits_only: bool = True, limit: int = 500) -> list[dict]:
    where = "WHERE dual_confirm = 1" if hits_only else ""
    rows = conn.execute(
        f"SELECT * FROM mart_dual_confirm {where} ORDER BY report_date DESC LIMIT ?",
        (max(int(limit or 0), 1),),
    ).fetchall()
    return [dict(row) for row in rows]


def _build_screening_where_clause(formula: Optional[str], hits_only: bool) -> str:
    conditions = []
    formula_field = _SCREENING_FORMULA_FIELDS.get(str(formula or "").strip().lower())
    if formula_field:
        conditions.append(f"{formula_field} = 1")
    if hits_only:
        conditions.append("hit_count > 0")
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def list_screening_results(conn, *, formula: Optional[str] = None, hits_only: bool = False, limit: int = 500, offset: int = 0) -> tuple[list[dict], int]:
    where = _build_screening_where_clause(formula, hits_only)
    rows = conn.execute(
        f"SELECT * FROM mart_stock_screening {where} ORDER BY hit_count DESC, stock_code LIMIT ? OFFSET ?",
        (max(int(limit or 0), 1), max(int(offset or 0), 0)),
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) FROM mart_stock_screening {where}"
    ).fetchone()[0]
    return [dict(row) for row in rows], int(total or 0)


def get_screening_detail(conn, stock_code: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM mart_stock_screening WHERE stock_code = ?",
        (stock_code,),
    ).fetchone()
    return dict(row) if row else None


def get_screening_summary(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM mart_stock_screening").fetchone()[0]
    f1 = conn.execute("SELECT COUNT(*) FROM mart_stock_screening WHERE f1_hit = 1").fetchone()[0]
    f3 = conn.execute("SELECT COUNT(*) FROM mart_stock_screening WHERE f3_hit = 1").fetchone()[0]
    f5 = conn.execute("SELECT COUNT(*) FROM mart_stock_screening WHERE f5_hit = 1").fetchone()[0]
    any_hit = conn.execute("SELECT COUNT(*) FROM mart_stock_screening WHERE hit_count > 0").fetchone()[0]

    screen_date = None
    row = conn.execute("SELECT screen_date FROM mart_stock_screening LIMIT 1").fetchone()
    if row:
        screen_date = row["screen_date"]

    turtle_count = 0
    turtle_breakout_count = 0
    turtle_pre_breakout_count = 0
    turtle_exit_count = 0
    turtle_snapshot_date = None
    try:
        turtle_count = conn.execute("SELECT COUNT(*) FROM dim_stock_turtle_latest").fetchone()[0]
        state_rows = conn.execute(
            "SELECT turtle_setup_state, COUNT(*) AS n FROM dim_stock_turtle_latest GROUP BY turtle_setup_state"
        ).fetchall()
        for r in state_rows:
            state = (r["turtle_setup_state"] or "").strip()
            n = int(r["n"] or 0)
            if "突破触发" in state:
                turtle_breakout_count += n
            elif "待突破" in state:
                turtle_pre_breakout_count += n
            elif "退出触发" in state:
                turtle_exit_count += n
        row = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM dim_stock_turtle_latest"
        ).fetchone()
        if row:
            turtle_snapshot_date = row["d"]
    except Exception:
        pass

    return {
        "screen_date": screen_date,
        "total_stocks": int(total or 0),
        "f1_hits": int(f1 or 0),
        "f3_hits": int(f3 or 0),
        "f5_hits": int(f5 or 0),
        "any_hit": int(any_hit or 0),
        "turtle_feature_count": int(turtle_count or 0),
        "turtle_breakout_count": int(turtle_breakout_count or 0),
        "turtle_pre_breakout_count": int(turtle_pre_breakout_count or 0),
        "turtle_exit_count": int(turtle_exit_count or 0),
        "turtle_snapshot_date": turtle_snapshot_date,
    }