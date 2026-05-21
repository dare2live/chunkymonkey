"""Shareholder-plan feature-family evaluation read model for Workbench."""
from __future__ import annotations

from typing import Any


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


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


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


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _latest_run_id(conn: Any, table_name: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    cols = _columns(conn, table_name)
    if "run_id" not in cols:
        return None
    order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else None
    if order_col:
        return _scalar(conn, f"SELECT run_id FROM {table_name} ORDER BY {order_col} DESC LIMIT 1")
    return _scalar(conn, f"SELECT run_id FROM {table_name} LIMIT 1")


def build_shareholder_plan_family_eval_view(conn: Any, *, limit: int = 12) -> dict[str, Any]:
    table = "mart_shareholder_plan_feature_family_eval"
    empty = {
        "run_id": None,
        "summary": {},
        "family_summary": [],
        "top_effects": [],
        "paired_advantages": [],
    }
    if not _table_exists(conn, table):
        return empty
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return empty
    row = conn.execute(
        """
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT source_family) AS source_family_count,
               COUNT(DISTINCT feature_name) AS feature_count,
               COUNT(DISTINCT label_name) AS label_count,
               MAX(total_rows) AS panel_rows,
               MAX(built_at) AS built_at
          FROM mart_shareholder_plan_feature_family_eval
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    summary = {
        "row_count": int(row["row_count"] or 0) if row else 0,
        "source_family_count": int(row["source_family_count"] or 0) if row else 0,
        "feature_count": int(row["feature_count"] or 0) if row else 0,
        "label_count": int(row["label_count"] or 0) if row else 0,
        "panel_rows": int(row["panel_rows"] or 0) if row else 0,
        "built_at": row["built_at"] if row else None,
    }
    family_rows = conn.execute(
        """
        SELECT source_family,
               label_name,
               COUNT(*) AS feature_count,
               MAX(total_rows) AS panel_rows,
               AVG(nondefault_pct) AS avg_nondefault_pct,
               MAX(ABS(COALESCE(rank_ic, 0))) AS max_abs_rank_ic,
               MAX(ABS(COALESCE(active_inactive_label_spread, 0))) AS max_abs_spread,
               AVG(CASE WHEN active_inactive_label_spread > 0 THEN 1.0 ELSE 0.0 END) AS positive_spread_share
          FROM mart_shareholder_plan_feature_family_eval
         WHERE run_id = ?
         GROUP BY source_family, label_name
         ORDER BY label_name, source_family
        """,
        (run_id,),
    ).fetchall()
    family_summary = [
        {
            "source_family": item["source_family"],
            "label_name": item["label_name"],
            "feature_count": int(item["feature_count"] or 0),
            "panel_rows": int(item["panel_rows"] or 0),
            "avg_nondefault_pct": item["avg_nondefault_pct"],
            "max_abs_rank_ic": item["max_abs_rank_ic"],
            "max_abs_spread": item["max_abs_spread"],
            "positive_spread_share": item["positive_spread_share"],
        }
        for item in family_rows
    ]
    top_rows = conn.execute(
        """
        SELECT source_family, source_table, feature_name, feature_purpose, label_name,
               window_days, valid_rows, nondefault_pct, event_rows,
               distinct_event_stocks, ic, rank_ic, daily_rank_ic_count,
               positive_rank_ic_share, label_mean_when_active,
               label_mean_when_inactive, active_inactive_label_spread, built_at
          FROM mart_shareholder_plan_feature_family_eval
         WHERE run_id = ?
         ORDER BY ABS(COALESCE(active_inactive_label_spread, 0)) DESC,
                  ABS(COALESCE(rank_ic, 0)) DESC,
                  source_family,
                  feature_name
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    top_effects = [
        {
            "source_family": item["source_family"],
            "source_table": item["source_table"],
            "feature_name": item["feature_name"],
            "feature_purpose": item["feature_purpose"],
            "label_name": item["label_name"],
            "window_days": item["window_days"],
            "valid_rows": int(item["valid_rows"] or 0),
            "nondefault_pct": item["nondefault_pct"],
            "event_rows": int(item["event_rows"] or 0),
            "distinct_event_stocks": int(item["distinct_event_stocks"] or 0),
            "ic": item["ic"],
            "rank_ic": item["rank_ic"],
            "daily_rank_ic_count": int(item["daily_rank_ic_count"] or 0),
            "positive_rank_ic_share": item["positive_rank_ic_share"],
            "label_mean_when_active": item["label_mean_when_active"],
            "label_mean_when_inactive": item["label_mean_when_inactive"],
            "active_inactive_label_spread": item["active_inactive_label_spread"],
            "built_at": item["built_at"],
        }
        for item in top_rows
    ]
    paired_rows = conn.execute(
        """
        WITH paired AS (
            SELECT feature_name,
                   label_name,
                   MAX(CASE WHEN source_family = 'latest_state' THEN rank_ic END) AS latest_rank_ic,
                   MAX(CASE WHEN source_family = 'initial_event' THEN rank_ic END) AS initial_rank_ic,
                   MAX(CASE WHEN source_family = 'latest_state' THEN active_inactive_label_spread END) AS latest_spread,
                   MAX(CASE WHEN source_family = 'initial_event' THEN active_inactive_label_spread END) AS initial_spread,
                   MAX(CASE WHEN source_family = 'latest_state' THEN nondefault_pct END) AS latest_nondefault_pct,
                   MAX(CASE WHEN source_family = 'initial_event' THEN nondefault_pct END) AS initial_nondefault_pct
              FROM mart_shareholder_plan_feature_family_eval
             WHERE run_id = ?
               AND feature_name != 'shareholder_plan_completed_count_180d'
             GROUP BY feature_name, label_name
        )
        SELECT feature_name,
               label_name,
               latest_rank_ic,
               initial_rank_ic,
               latest_spread,
               initial_spread,
               ABS(COALESCE(initial_spread, 0)) - ABS(COALESCE(latest_spread, 0)) AS abs_spread_advantage,
               latest_nondefault_pct,
               initial_nondefault_pct
          FROM paired
         WHERE latest_spread IS NOT NULL
           AND initial_spread IS NOT NULL
         ORDER BY abs_spread_advantage DESC, feature_name, label_name
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    paired_advantages = [
        {
            "feature_name": item["feature_name"],
            "label_name": item["label_name"],
            "latest_rank_ic": item["latest_rank_ic"],
            "initial_rank_ic": item["initial_rank_ic"],
            "latest_spread": item["latest_spread"],
            "initial_spread": item["initial_spread"],
            "abs_spread_advantage": item["abs_spread_advantage"],
            "latest_nondefault_pct": item["latest_nondefault_pct"],
            "initial_nondefault_pct": item["initial_nondefault_pct"],
        }
        for item in paired_rows
    ]
    return {
        "run_id": run_id,
        "summary": summary,
        "family_summary": family_summary,
        "top_effects": top_effects,
        "paired_advantages": paired_advantages,
    }
