"""Feature availability contract read model for Workbench."""
from __future__ import annotations

from typing import Any

from services.feature_registry import load_feature_registry


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


def build_feature_availability_contract_view(conn: Any, *, limit: int = 160) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source = "registry"
    if _table_exists(conn, "mart_feature_availability_contract"):
        source = "mart_feature_availability_contract"
        cols = _columns(conn, "mart_feature_availability_contract")
        selected = [
            "feature_name",
            "feature_group",
            "feature_role",
            "availability_cadence",
            "panel_density",
            "expected_update_frequency",
            "null_policy",
            "coverage_universe",
            "model_input",
            "production_ready",
            "enabled",
            "frontend_visible",
            "pit_release_lag_days",
            "notes",
            "built_at",
        ]
        if set(selected).issubset(cols):
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT feature_name, feature_group, feature_role,
                           availability_cadence, panel_density,
                           expected_update_frequency, null_policy,
                           coverage_universe, model_input, production_ready,
                           enabled, frontend_visible, pit_release_lag_days,
                           notes, built_at
                      FROM mart_feature_availability_contract
                     WHERE frontend_visible = TRUE
                     ORDER BY
                           CASE WHEN model_input THEN 0 ELSE 1 END,
                           CASE WHEN production_ready THEN 0 ELSE 1 END,
                           feature_role, feature_group, feature_name
                     LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            ]
    if not rows:
        registry = load_feature_registry()
        rows = [
            {
                "feature_name": spec.name,
                "feature_group": spec.group,
                "feature_role": spec.feature_role,
                "availability_cadence": spec.availability_cadence,
                "panel_density": spec.panel_density,
                "expected_update_frequency": spec.expected_update_frequency,
                "null_policy": spec.null_policy,
                "coverage_universe": spec.coverage_universe,
                "model_input": spec.model_input,
                "production_ready": spec.production_ready,
                "enabled": spec.enabled,
                "frontend_visible": spec.frontend_visible,
                "pit_release_lag_days": spec.pit_release_lag_days,
                "notes": spec.notes,
                "built_at": None,
            }
            for spec in registry.features.values()
            if spec.frontend_visible
        ][:limit]

    role_counts: dict[str, int] = {}
    cadence_counts: dict[str, int] = {}
    density_counts: dict[str, int] = {}
    null_policy_counts: dict[str, int] = {}
    for row in rows:
        role = str(row.get("feature_role") or "unknown")
        cadence = str(row.get("availability_cadence") or "unknown")
        density = str(row.get("panel_density") or "unknown")
        null_policy = str(row.get("null_policy") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        cadence_counts[cadence] = cadence_counts.get(cadence, 0) + 1
        density_counts[density] = density_counts.get(density, 0) + 1
        null_policy_counts[null_policy] = null_policy_counts.get(null_policy, 0) + 1
    return {
        "source": source,
        "rows": rows,
        "role_counts": role_counts,
        "cadence_counts": cadence_counts,
        "density_counts": density_counts,
        "null_policy_counts": null_policy_counts,
    }
