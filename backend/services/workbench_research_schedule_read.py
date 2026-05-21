"""Research schedule read model for the Workbench research surface."""
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


def _latest_run_id(conn: Any, table_name: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    cols = _columns(conn, table_name)
    if "run_id" not in cols:
        return None
    order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else None
    if order_col:
        return conn.execute(f"SELECT run_id FROM {table_name} ORDER BY {order_col} DESC LIMIT 1").fetchone()[0]
    return conn.execute(f"SELECT run_id FROM {table_name} LIMIT 1").fetchone()[0]


def _status_counts(conn: Any, table_name: str, *, run_id: str | None = None) -> dict[str, int]:
    if not _table_exists(conn, table_name) or "status" not in _columns(conn, table_name):
        return {}
    params: tuple[Any, ...] = ()
    where = ""
    if run_id and "run_id" in _columns(conn, table_name):
        where = "WHERE run_id = ?"
        params = (run_id,)
    rows = conn.execute(
        f"""
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
          FROM {table_name}
          {where}
         GROUP BY COALESCE(status, 'unknown')
        """,
        params,
    ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}


def _select_expr(cols: set[str], column: str, *, alias: str | None = None, default: str = "NULL") -> str:
    out = alias or column
    if column in cols:
        return f"{column} AS {out}"
    return f"{default} AS {out}"


def build_research_schedule_view(conn: Any, *, task_limit: int = 20) -> dict[str, Any]:
    schedule_run_id = _latest_run_id(conn, "mart_research_schedule_plan")
    tasks = []
    if schedule_run_id and _table_exists(conn, "mart_research_schedule_plan"):
        cols = _columns(conn, "mart_research_schedule_plan")
        rows = conn.execute(
            f"""
            SELECT {_select_expr(cols, "task_id")},
                   {_select_expr(cols, "task_type")},
                   {_select_expr(cols, "priority", default="999999")},
                   {_select_expr(cols, "status")},
                   {_select_expr(cols, "enabled", default="TRUE")},
                   {_select_expr(cols, "evidence_table")},
                   {_select_expr(cols, "evidence_run_id")},
                   {_select_expr(cols, "evidence_found", default="FALSE")},
                   {_select_expr(cols, "evidence_status")},
                   {_select_expr(cols, "reason")},
                   {_select_expr(cols, "command_text")}
              FROM mart_research_schedule_plan
             WHERE run_id = ?
             ORDER BY priority, task_id
             LIMIT ?
            """,
            (schedule_run_id, int(task_limit)),
        ).fetchall()
        tasks = [
            {
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "priority": row["priority"],
                "status": row["status"],
                "enabled": bool(row["enabled"]),
                "evidence_table": row["evidence_table"],
                "evidence_run_id": row["evidence_run_id"],
                "evidence_found": bool(row["evidence_found"]),
                "evidence_status": row["evidence_status"],
                "reason": row["reason"],
                "command_text": row["command_text"],
            }
            for row in rows
        ]
    return {
        "run_id": schedule_run_id,
        "status_counts": _status_counts(conn, "mart_research_schedule_plan", run_id=schedule_run_id),
        "tasks": tasks,
    }
