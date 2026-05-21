"""Temporal synergy research read model for Workbench."""
from __future__ import annotations

from typing import Any

from services.workbench_temporal_synergy_discovery_read import build_temporal_synergy_discovery_view
from services.workbench_temporal_synergy_pairs_read import build_temporal_synergy_pair_view
from services.workbench_temporal_synergy_policy_read import build_temporal_synergy_policy_view
from services.workbench_temporal_synergy_quality_read import build_temporal_synergy_quality_view


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


def _empty_temporal_synergy() -> dict[str, Any]:
    return {
        "run_id": None,
        "quality": None,
        "label_summary": [],
        "top_relevance": [],
        "top_synergies": [],
        "selected_interactions": [],
        "optuna_studies": [],
        "policy_candidates": [],
        "policy_gates": [],
        "policy_mtm_gates": [],
        "redundancy_clusters": [],
        "conditional_synergies": [],
    }


def build_temporal_synergy_research(
    conn: Any,
    *,
    relevance_limit: int = 15,
    synergy_limit: int = 15,
) -> dict[str, Any]:
    quality_table = "mart_temporal_research_panel_quality"
    if not _table_exists(conn, quality_table):
        return _empty_temporal_synergy()
    run_id = _latest_run_id(conn, quality_table)
    if not run_id:
        return _empty_temporal_synergy()

    quality_view = build_temporal_synergy_quality_view(conn, run_id=run_id, relevance_limit=relevance_limit)
    pair_view = build_temporal_synergy_pair_view(conn, run_id=run_id, synergy_limit=synergy_limit)
    policy_view = build_temporal_synergy_policy_view(conn, run_id=run_id)
    discovery_view = build_temporal_synergy_discovery_view(conn, run_id=run_id, synergy_limit=synergy_limit)

    return {
        "run_id": run_id,
        "quality": quality_view["quality"],
        "label_summary": quality_view["label_summary"],
        "top_relevance": quality_view["top_relevance"],
        "top_synergies": pair_view["top_synergies"],
        "selected_interactions": pair_view["selected_interactions"],
        "optuna_studies": policy_view["optuna_studies"],
        "policy_candidates": policy_view["policy_candidates"],
        "policy_gates": policy_view["policy_gates"],
        "policy_mtm_gates": policy_view["policy_mtm_gates"],
        "policy_mtm_strategy_sweeps": policy_view["policy_mtm_strategy_sweeps"],
        "redundancy_clusters": discovery_view["redundancy_clusters"],
        "conditional_synergies": discovery_view["conditional_synergies"],
    }
