"""Paper simulation read models for Workbench."""
from __future__ import annotations

from typing import Any
import json


def _relation_exists(conn: Any, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None and _relation_exists(conn, table_name)


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(_row_value(row, "column_name", 0))
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _json_or_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_workbench_paper_sim_kpi_timeseries(
    conn: Any,
    *,
    limit: int = 50,
    variant: str | None = None,
) -> dict[str, Any]:
    """Return paper_sim KPI history for dashboard/timeseries consumption."""
    endpoint = "/api/workbench/paper-sim/kpi-timeseries"
    if not _table_exists(conn, "mart_paper_sim_kpi"):
        return {
            "ok": True,
            "data": [],
            "meta": {
                "endpoint": endpoint,
                "source_mode": "missing_table",
                "recompute_on_read": False,
                "latest_materialized_at": None,
                "limit": limit,
                "variant": variant,
            },
        }

    safe_limit = max(1, min(int(limit), 500))
    cols = _columns(conn, "mart_paper_sim_kpi")
    wanted = [
        "sim_run_id",
        "variant",
        "period_start",
        "period_end",
        "n_days",
        "annual_return",
        "max_dd",
        "sharpe",
        "monthly_win_rate",
        "annual_turnover",
        "avg_holding_days",
        "user_criteria_pass",
        "anti_churn_pass",
        "robustness_pass",
        "all_kpi_pass",
        "sim_config_hash",
        "parent_sim_run_id",
        "param_diff_json",
        "lineage_url",
        "built_at",
    ]
    selected = [col for col in wanted if col in cols]
    if not selected:
        return {"ok": True, "data": [], "meta": {"endpoint": endpoint, "source_mode": "empty_schema"}}

    where = ""
    params: list[Any] = []
    if variant and "variant" in cols:
        where = "WHERE variant = ?"
        params.append(variant)
    order = "built_at DESC NULLS LAST, sim_run_id DESC" if "built_at" in cols else "sim_run_id DESC"
    rows = conn.execute(
        f"""
        SELECT {", ".join(selected)}
          FROM mart_paper_sim_kpi
          {where}
         ORDER BY {order}
         LIMIT ?
        """,
        [*params, safe_limit],
    ).fetchall()

    data: list[dict[str, Any]] = []
    for row in rows:
        item = {col: _row_value(row, col, idx) for idx, col in enumerate(selected)}
        data.append(
            {
                "sim_run_id": item.get("sim_run_id"),
                "variant": item.get("variant"),
                "period_start": str(item.get("period_start")) if item.get("period_start") is not None else None,
                "period_end": str(item.get("period_end")) if item.get("period_end") is not None else None,
                "n_days": item.get("n_days"),
                "annual_return": _float_or_none(item.get("annual_return")),
                "max_dd": _float_or_none(item.get("max_dd")),
                "sharpe": _float_or_none(item.get("sharpe")),
                "monthly_win_rate": _float_or_none(item.get("monthly_win_rate")),
                "annual_turnover": _float_or_none(item.get("annual_turnover")),
                "avg_holding_days": _float_or_none(item.get("avg_holding_days")),
                "user_criteria_pass": item.get("user_criteria_pass"),
                "anti_churn_pass": item.get("anti_churn_pass"),
                "robustness_pass": item.get("robustness_pass"),
                "all_kpi_pass": item.get("all_kpi_pass"),
                "sim_config_hash": item.get("sim_config_hash"),
                "parent_sim_run_id": item.get("parent_sim_run_id"),
                "param_diff": _json_or_none(item.get("param_diff_json")),
                "lineage_url": item.get("lineage_url"),
                "built_at": str(item.get("built_at")) if item.get("built_at") is not None else None,
            }
        )

    latest = data[0] if data else None
    return {
        "ok": True,
        "data": data,
        "latest": latest,
        "meta": {
            "endpoint": endpoint,
            "source_mode": "materialized_snapshot",
            "recompute_on_read": False,
            "refresh_semantics": "paper_sim_run_or_backfill",
            "latest_materialized_at": latest.get("built_at") if latest else None,
            "limit": safe_limit,
            "variant": variant,
            "row_count": len(data),
        },
    }
