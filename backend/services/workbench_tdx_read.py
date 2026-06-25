"""TDX/F10 read models for Workbench data-source health."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.workbench_tdx_f10_read import build_f10_source_date_audit_view  # tdx_f10_source_dq 视图已退役 (2026-06-25)


REPO = Path(__file__).resolve().parent.parent.parent
MARKET_DB = REPO / "data" / "market.duckdb"


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


def _attach_market_readonly(conn: Any) -> bool:
    if _relation_exists(conn, "market.sqlite_master"):
        return True
    if not MARKET_DB.exists():
        return False
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
        return True
    except Exception:
        return False


def _relation_columns(conn: Any, relation: str) -> set[str]:
    try:
        rows = conn.execute(f"DESCRIBE {relation}").fetchall()
    except Exception:
        return set()
    return {str(row["column_name"]) for row in rows}


def _empty_tdx_server_health_view() -> dict[str, Any]:
    return {
        "summary": {
            "server_count": 0,
            "healthy_count": 0,
            "failure_server_count": 0,
            "timeout_server_count": 0,
            "total_successes": 0,
            "total_failures": 0,
            "total_timeouts": 0,
            "capabilities": [],
            "latest_updated_at": None,
        },
        "servers": [],
        "top_servers": [],
        "failing_servers": [],
        "updated_at": None,
    }


def _tdx_server_health_relation(conn: Any) -> str | None:
    if _relation_exists(conn, "mart_tdx_server_health"):
        return "mart_tdx_server_health"
    if _relation_exists(conn, "market.mart_tdx_server_health"):
        return "market.mart_tdx_server_health"
    if _attach_market_readonly(conn) and _relation_exists(conn, "market.mart_tdx_server_health"):
        return "market.mart_tdx_server_health"
    return None


def build_tdx_server_health_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    relation = _tdx_server_health_relation(conn)
    if not relation:
        return _empty_tdx_server_health_view()
    cols = _relation_columns(conn, relation)
    required = {
        "server_host",
        "server_port",
        "capability",
        "success_count",
        "failure_count",
        "timeout_count",
        "health_score",
        "updated_at",
    }
    missing = sorted(required - cols)
    if missing:
        view = _empty_tdx_server_health_view()
        view["schema_issues"] = [{"kind": "missing_columns", "columns": missing}]
        return view

    query_rows = conn.execute(
        f"""
        SELECT server_host, server_port, capability,
               success_count, failure_count, timeout_count,
               last_success_at, last_failure_at, last_error_type,
               avg_success_elapsed_s, last_attempt_elapsed_s,
               health_score, source_run_id, updated_at
          FROM {relation}
         ORDER BY capability, health_score DESC, server_host, server_port
        """,
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for row in query_rows:
        rows.append(
            {
                "server_host": row["server_host"],
                "server_port": int(row["server_port"] or 0),
                "capability": row["capability"],
                "success_count": int(row["success_count"] or 0),
                "failure_count": int(row["failure_count"] or 0),
                "timeout_count": int(row["timeout_count"] or 0),
                "last_success_at": row["last_success_at"],
                "last_failure_at": row["last_failure_at"],
                "last_error_type": row["last_error_type"],
                "avg_success_elapsed_s": row["avg_success_elapsed_s"],
                "last_attempt_elapsed_s": row["last_attempt_elapsed_s"],
                "health_score": row["health_score"],
                "source_run_id": row["source_run_id"],
                "updated_at": row["updated_at"],
            }
        )
    if not rows:
        return _empty_tdx_server_health_view()

    latest_updated_at = max(str(row.get("updated_at") or "") for row in rows) or None
    summary = {
        "server_count": len(rows),
        "healthy_count": sum(1 for row in rows if row["success_count"] > 0),
        "failure_server_count": sum(1 for row in rows if row["failure_count"] > 0),
        "timeout_server_count": sum(1 for row in rows if row["timeout_count"] > 0),
        "total_successes": sum(row["success_count"] for row in rows),
        "total_failures": sum(row["failure_count"] for row in rows),
        "total_timeouts": sum(row["timeout_count"] for row in rows),
        "capabilities": sorted({str(row["capability"]) for row in rows if row.get("capability")}),
        "latest_updated_at": latest_updated_at,
    }
    top_servers = [row for row in rows if row["success_count"] > 0]
    top_servers.sort(key=lambda row: (float(row["health_score"] or 0), row["success_count"]), reverse=True)
    failing_servers = [row for row in rows if row["failure_count"] > 0 or row["timeout_count"] > 0]
    failing_servers.sort(
        key=lambda row: (
            row["timeout_count"],
            row["failure_count"],
            str(row.get("last_failure_at") or row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return {
        "summary": summary,
        "servers": rows[: int(limit)],
        "top_servers": top_servers[: int(limit)],
        "failing_servers": failing_servers[: min(int(limit), 12)],
        "updated_at": latest_updated_at,
    }
