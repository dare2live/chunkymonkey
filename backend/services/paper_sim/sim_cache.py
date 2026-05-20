"""Exact paper_sim cache helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def compute_config_hash(
    yaml_path: str | Path,
    model_id: str,
    start_date: str,
    end_date: str,
    panel_version: str,
) -> str:
    """Return the exact paper_sim config hash as md5 hex."""
    path = Path(yaml_path)
    yaml_content = path.read_bytes()
    h = hashlib.md5()
    for part in (
        yaml_content,
        str(model_id).encode("utf-8"),
        str(start_date).encode("utf-8"),
        str(end_date).encode("utf-8"),
        str(panel_version).encode("utf-8"),
    ):
        h.update(part)
        h.update(b"\0")
    return h.hexdigest()


def check_cache(conn: Any, config_hash: str) -> dict[str, Any] | None:
    """Return the cached KPI row for an exact config hash, or None."""
    if not config_hash:
        raise ValueError("config_hash is required")
    if not _table_exists(conn, "mart_paper_sim_kpi"):
        return None
    cols = _columns(conn, "mart_paper_sim_kpi")
    if "sim_config_hash" not in cols:
        return None
    order_sql = " ORDER BY built_at DESC NULLS LAST" if "built_at" in cols else ""
    sql = (
        "SELECT * FROM mart_paper_sim_kpi "
        "WHERE sim_config_hash = ?"
        f"{order_sql} LIMIT 1"
    )
    cur = conn.execute(sql, [config_hash])
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_dict(row, _cursor_columns(cur))


def register_cache(
    conn: Any,
    sim_run_id: str,
    config_hash: str,
    parent_sim_run_id: str | None,
    param_diff_json: str | dict[str, Any] | None,
) -> None:
    """Attach cache metadata to an existing KPI row."""
    if not sim_run_id:
        raise ValueError("sim_run_id is required")
    if not config_hash:
        raise ValueError("config_hash is required")
    param_diff_text = _normalize_json_text(param_diff_json)
    conn.execute(
        """
        UPDATE mart_paper_sim_kpi
           SET sim_config_hash = ?,
               parent_sim_run_id = ?,
               param_diff_json = ?
         WHERE sim_run_id = ?
        """,
        [config_hash, parent_sim_run_id, param_diff_text, sim_run_id],
    )
    _commit(conn)


def _normalize_json_text(value: str | dict[str, Any] | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        [table_name],
    ).fetchone()
    return row is not None


def _columns(conn: Any, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    except Exception:
        return set()
    return {str(row[0]) for row in rows}


def _cursor_columns(cur: Any) -> list[str]:
    desc = getattr(cur, "description", None) or []
    return [str(item[0]) for item in desc]


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {columns[i]: row[i] for i in range(len(columns))}


def _commit(conn: Any) -> None:
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()
