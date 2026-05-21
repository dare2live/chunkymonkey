"""Data asset health read model for Workbench."""
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


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _latest_data_health_snapshot_at(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_data_health"):
        return None
    return _scalar(conn, "SELECT CAST(MAX(snapshot_at) AS VARCHAR) FROM mart_data_health")


def build_asset_health_snapshot(conn: Any) -> dict[str, Any]:
    empty = {
        "snapshot_at": None,
        "summary": {"total": 0, "green": 0, "yellow": 0, "red": 0, "unknown": 0},
        "by_layer": {},
        "governance_counts": {
            "coverage_policy": {},
            "null_policy": {},
            "model_eligibility": {},
            "quality_gate_level": {},
        },
        "items": [],
        "red_list": [],
        "fallback_active": [],
    }
    if not _table_exists(conn, "dim_data_asset") or not _table_exists(conn, "mart_data_health"):
        return empty
    snap_at = _latest_data_health_snapshot_at(conn)
    if not snap_at:
        return empty
    health_cols = _columns(conn, "mart_data_health")
    dim_cols = _columns(conn, "dim_data_asset")
    tier_dist_expr = "m.source_tier_dist" if "source_tier_dist" in health_cols else "NULL AS source_tier_dist"
    governance_exprs = []
    for column in (
        "asset_grain",
        "asset_cadence",
        "coverage_policy",
        "null_policy",
        "pit_policy",
        "intended_use",
        "model_eligibility",
        "strategy_eligibility",
        "frontend_visibility",
        "quality_gate_level",
    ):
        governance_exprs.append(f"d.{column}" if column in dim_cols else f"NULL AS {column}")
    rows = conn.execute(
        f"""
        SELECT d.table_name, d.layer, d.purpose, d.writer_module,
               d.upstream_source, d.source_tier, d.expected_freshness, d.sla_hours,
               {", ".join(governance_exprs)},
               m.row_count, CAST(m.last_data_date AS VARCHAR) AS last_data_date,
               m.freshness_hours, m.freshness_ok, m.severity, m.issue_summary,
               {tier_dist_expr}
          FROM dim_data_asset d
          LEFT JOIN mart_data_health m
            ON m.table_name = d.table_name
           AND CAST(m.snapshot_at AS VARCHAR) = ?
         ORDER BY d.layer, d.table_name
        """,
        (snap_at,),
    ).fetchall()
    by_layer: dict[str, dict[str, int]] = {}
    severity_total = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    governance_counts: dict[str, dict[str, int]] = {
        "coverage_policy": {},
        "null_policy": {},
        "model_eligibility": {},
        "quality_gate_level": {},
    }
    items = []
    red_list = []
    fallback_active = []
    for row in rows:
        sev = str(row["severity"] or "unknown")
        layer = str(row["layer"] or "unknown")
        severity_total[sev] = severity_total.get(sev, 0) + 1
        layer_counts = by_layer.setdefault(layer, {"green": 0, "yellow": 0, "red": 0, "unknown": 0, "total": 0})
        layer_counts[sev] = layer_counts.get(sev, 0) + 1
        layer_counts["total"] += 1
        for field, counts in governance_counts.items():
            value = str(row[field] or "unknown")
            counts[value] = counts.get(value, 0) + 1
        tier_dist = _safe_json(row["source_tier_dist"]) or None
        if isinstance(tier_dist, dict):
            fallback_rows = sum(int(value) for key, value in tier_dist.items() if str(key).isdigit() and int(key) > 1)
            if fallback_rows > 0:
                fallback_active.append(
                    {
                        "table": row["table_name"],
                        "tier_distribution": tier_dist,
                        "fallback_rows": fallback_rows,
                    }
                )
        item = {
            "table_name": row["table_name"],
            "layer": layer,
            "severity": sev,
            "row_count": row["row_count"],
            "last_data_date": row["last_data_date"],
            "freshness_hours": row["freshness_hours"],
            "sla_hours": row["sla_hours"],
            "expected_freshness": row["expected_freshness"],
            "writer_module": row["writer_module"],
            "upstream_source": row["upstream_source"],
            "source_tier": row["source_tier"],
            "asset_grain": row["asset_grain"],
            "asset_cadence": row["asset_cadence"],
            "coverage_policy": row["coverage_policy"],
            "null_policy": row["null_policy"],
            "pit_policy": row["pit_policy"],
            "intended_use": row["intended_use"],
            "model_eligibility": row["model_eligibility"],
            "strategy_eligibility": row["strategy_eligibility"],
            "frontend_visibility": row["frontend_visibility"],
            "quality_gate_level": row["quality_gate_level"],
            "issue_summary": row["issue_summary"],
            "source_tier_distribution": tier_dist,
        }
        items.append(item)
        if sev == "red":
            red_list.append(item)
    return {
        "snapshot_at": snap_at,
        "summary": {"total": sum(severity_total.values()), **severity_total},
        "by_layer": by_layer,
        "governance_counts": governance_counts,
        "items": items,
        "red_list": red_list,
        "fallback_active": fallback_active,
    }
