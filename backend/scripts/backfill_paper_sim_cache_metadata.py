"""Backfill legacy paper_sim cache/lineage metadata.

Historical KPI rows created before sim_config_hash existed still have a
config_snapshot. This script assigns a namespaced legacy hash from that
snapshot and links rows in built_at order for parameter-impact tracing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn
from services.paper_sim.ddl import apply_schema_migration
from services.paper_sim.sim_cache import register_cache


LEGACY_HASH_NAMESPACE = "legacy_snapshot_v1"


def canonical_json_text(value: str | None) -> str:
    """Return stable JSON text; invalid snapshots are treated as raw text."""
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def legacy_snapshot_hash(
    *,
    config_snapshot: str | None,
    variant: str,
    period_start: str,
    period_end: str,
    n_days: int,
) -> str:
    """Hash the captured runtime config plus run scope for legacy rows."""
    h = hashlib.md5()
    for part in (
        LEGACY_HASH_NAMESPACE,
        canonical_json_text(config_snapshot),
        variant,
        period_start,
        period_end,
        str(n_days),
    ):
        h.update(str(part).encode("utf-8"))
        h.update(b"\0")
    return f"{LEGACY_HASH_NAMESPACE}:{h.hexdigest()}"


def param_diff_json(current_snapshot: str | None, parent_snapshot: str | None) -> str | None:
    """Return a compact top-level portfolio/swap diff against the parent row."""
    if not current_snapshot or not parent_snapshot:
        return None
    try:
        current = json.loads(current_snapshot)
        parent = json.loads(parent_snapshot)
    except json.JSONDecodeError:
        return None

    diff: dict[str, Any] = {}
    for section in ("portfolio", "swap"):
        section_diff: dict[str, Any] = {}
        cur_section = current.get(section) or {}
        parent_section = parent.get(section) or {}
        if not isinstance(cur_section, dict) or not isinstance(parent_section, dict):
            continue
        for key in sorted(set(cur_section) | set(parent_section)):
            if cur_section.get(key) != parent_section.get(key):
                section_diff[key] = [parent_section.get(key), cur_section.get(key)]
        if section_diff:
            diff[section] = section_diff
    if not diff:
        return None
    return json.dumps(diff, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def backfill(*, apply: bool) -> dict[str, Any]:
    conn = get_conn()
    try:
        apply_schema_migration(conn)
        rows = conn.execute(
            """
            SELECT sim_run_id, variant, period_start, period_end, n_days,
                   config_snapshot, built_at
              FROM mart_paper_sim_kpi
             WHERE sim_config_hash IS NULL
             ORDER BY built_at ASC NULLS LAST, sim_run_id ASC
            """
        ).fetchall()
        updates: list[tuple[str, str, str | None, str | None]] = []
        parent_sim_run_id: str | None = None
        parent_snapshot: str | None = None
        for row in rows:
            sim_run_id, variant, period_start, period_end, n_days, snapshot, _built_at = row
            config_hash = legacy_snapshot_hash(
                config_snapshot=snapshot,
                variant=str(variant),
                period_start=str(period_start),
                period_end=str(period_end),
                n_days=int(n_days),
            )
            diff = param_diff_json(snapshot, parent_snapshot)
            updates.append((str(sim_run_id), config_hash, parent_sim_run_id, diff))
            parent_sim_run_id = str(sim_run_id)
            parent_snapshot = snapshot

        if apply:
            for sim_run_id, config_hash, parent, diff in updates:
                register_cache(conn, sim_run_id, config_hash, parent, diff, update_missing_only=True)

        return {
            "mode": "apply" if apply else "dry-run",
            "candidate_rows": len(rows),
            "updated_rows": len(updates) if apply else 0,
            "first": updates[0][0] if updates else None,
            "last": updates[-1][0] if updates else None,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write backfilled metadata")
    args = parser.parse_args()
    print(json.dumps(backfill(apply=args.apply), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
