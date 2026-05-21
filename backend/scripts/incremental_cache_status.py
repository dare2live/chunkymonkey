#!/usr/bin/env python3
"""Read-only criteria #10 cache and incremental-management status.

This is the implementation behind ``cm cache``. It deliberately avoids writes:
the command must stay usable while another process owns DuckDB's single-writer
lock.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "scripts"))

from services.analytics import duck_connection  # noqa: E402


def table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        [table],
    ).fetchone()
    return row is not None


def columns(conn: Any, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}


def fmt_pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "N/A"
    return f"{numerator / denominator * 100:.1f}%"


def fmt_json(value: Any) -> str:
    if value is None:
        return "null"
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def print_paper_sim_cache(conn: Any) -> None:
    print("=== paper_sim cache state (criteria #10 incremental mgmt) ===")
    print()
    if not table_exists(conn, "mart_paper_sim_kpi"):
        print("  mart_paper_sim_kpi: MISSING")
        return

    cols = columns(conn, "mart_paper_sim_kpi")
    has_hash_col = "sim_config_hash" in cols
    has_parent_col = "parent_sim_run_id" in cols
    has_config_col = "config_snapshot" in cols
    total_runs = conn.execute("SELECT COUNT(*) FROM mart_paper_sim_kpi").fetchone()[0] or 0
    if has_hash_col:
        hash_count, distinct_hashes = conn.execute(
            "SELECT COUNT(sim_config_hash), COUNT(DISTINCT sim_config_hash) FROM mart_paper_sim_kpi"
        ).fetchone()
    else:
        hash_count, distinct_hashes = 0, 0

    print(f"  total_runs           = {total_runs}")
    print(f"  sim_config_hash col  = {'yes' if has_hash_col else 'no'}")
    print(f"  has_sim_config_hash  = {hash_count} ({fmt_pct(hash_count, total_runs)})")
    print(f"  distinct_hashes      = {distinct_hashes}")
    print()

    if has_hash_col:
        rows = conn.execute(
            """
            SELECT sim_config_hash, COUNT(*) AS n, MIN(built_at) AS first_run, MAX(built_at) AS last_run
              FROM mart_paper_sim_kpi
             WHERE sim_config_hash IS NOT NULL
             GROUP BY sim_config_hash
            HAVING COUNT(*) >= 2
             ORDER BY n DESC
             LIMIT 5
            """
        ).fetchall()
    else:
        rows = []
    if rows:
        print("  cache-hit candidates (>=2 same hash):")
        for row in rows:
            print(f"    hash={str(row[0])[:12]}... n={row[1]} first={row[2]} last={row[3]}")
    else:
        print("  cache-hit candidates: 0")
    print()

    if has_parent_col:
        parent_linked = conn.execute(
            "SELECT COUNT(*) FROM mart_paper_sim_kpi WHERE parent_sim_run_id IS NOT NULL"
        ).fetchone()[0]
    else:
        parent_linked = 0
    print(f"  parent_sim_run_id col = {'yes' if has_parent_col else 'no'}")
    print(f"  parent-linked runs    = {parent_linked} (param-evolution trace)")
    print()

    if has_config_col:
        rows = conn.execute(
            """
            SELECT JSON_EXTRACT_STRING(config_snapshot, '$.model_id') AS mid, COUNT(*) AS n
              FROM mart_paper_sim_kpi
             WHERE config_snapshot IS NOT NULL
             GROUP BY mid
            HAVING COUNT(*) >= 2
             ORDER BY n DESC
             LIMIT 5
            """
        ).fetchall()
    else:
        rows = []
    if rows:
        print("  model_id implicit predictions reuse (Layer 2):")
        for row in rows:
            print(f"    model_id={(row[0] or '')[:50]}... n={row[1]} runs")
    else:
        print("  model_id implicit predictions reuse (Layer 2): no evidence")
    print()


def print_l3_panel_incremental(conn: Any) -> None:
    print("=== L3 feature panel incremental state ===")
    print()
    if table_exists(conn, "fact_feature_panel"):
        row = conn.execute(
            "SELECT COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks, MIN(date), MAX(date) FROM fact_feature_panel"
        ).fetchone()
        print(f"  fact_feature_panel    = {row[0]} rows / {row[1]} stocks / {row[2]} -> {row[3]}")
    else:
        print("  fact_feature_panel    = MISSING")

    if table_exists(conn, "mart_feature_panel_validation"):
        row = conn.execute(
            """
            SELECT run_mode, status, validated_at, rows, duplicate_keys, close_coverage,
                   source_lineage_coverage, source_watermark_hash, blockers_json
              FROM mart_feature_panel_validation
             ORDER BY validated_at DESC
             LIMIT 1
            """
        ).fetchone()
        if row:
            print(f"  latest_validation     = mode={row[0]} status={row[1]} at={row[2]}")
            print(f"  validation_rows       = {row[3]} dup={row[4]} close_cov={row[5]}")
            print(f"  source_lineage_cov    = {row[6]} source_hash={str(row[7] or '')[:16]}")
            blockers = fmt_json(row[8])
            print(f"  validation_blockers   = {blockers}")
    else:
        print("  latest_validation     = MISSING")

    if table_exists(conn, "mart_pipeline_run_manifest"):
        row = conn.execute(
            """
            SELECT run_id, status, started_at, duration_s, gate_result, blockers_json, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name = 'build_feature_panel_duck'
             ORDER BY started_at DESC NULLS LAST
             LIMIT 1
            """
        ).fetchone()
        if row:
            print(f"  latest_manifest       = {row[0]} status={row[1]} started={row[2]} duration={row[3]}")
            print(f"  latest_gate           = {row[4]} blockers={fmt_json(row[5])}")
        else:
            print("  latest_manifest       = no build_feature_panel_duck manifest rows yet")
    else:
        print("  latest_manifest       = mart_pipeline_run_manifest missing")

    try:
        import build_feature_panel_duck

        plan = build_feature_panel_duck.plan_incremental_window(conn)
        print("  readonly_plan         =")
        print(f"    mode={plan.get('mode')} noop={plan.get('noop')} reason={plan.get('reason')}")
        print(
            "    "
            f"source_max={plan.get('source_max_date')} existing_max={plan.get('existing_max_date')} "
            f"write_start={plan.get('write_start_date')} read_start={plan.get('read_start_date')}"
        )
        changed = ",".join(plan.get("changed_source_domains") or [])
        print(f"    changed_domains={changed or '(none)'} source_hash={str(plan.get('source_watermark_hash') or '')[:16]}")
    except Exception as exc:
        print(f"  readonly_plan         = unavailable: {exc}")
    print()


def _latest_warm_start_checkpoint() -> dict[str, Any] | None:
    optuna_dir = REPO_ROOT / "data" / "reports" / "optuna"
    if not optuna_dir.exists():
        return None
    candidates = sorted(optuna_dir.glob("*.best.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        params = payload.get("best_params")
        if not isinstance(params, dict) or not params:
            continue
        return {
            "path": path,
            "model_id": path.name.removesuffix(".best.json"),
            "study_name": payload.get("study_name"),
            "best_trial_number": payload.get("best_trial_number"),
            "best_value": payload.get("best_value"),
            "updated_at": payload.get("updated_at"),
            "param_count": len(params),
        }
    return None


def print_l4_retrain_warm_start() -> None:
    print("=== L4 retrain warm-start state ===")
    print()
    checkpoint = _latest_warm_start_checkpoint()
    if checkpoint is None:
        print("  latest_checkpoint     = MISSING")
        print("  status                = SPEC only (no local *.best.json)")
        print()
        return
    rel_path = checkpoint["path"].relative_to(REPO_ROOT)
    print(f"  latest_checkpoint     = {rel_path}")
    print(f"  model_id              = {checkpoint['model_id']}")
    print(f"  study_name            = {checkpoint.get('study_name') or '(unknown)'}")
    print(f"  best_trial            = {checkpoint.get('best_trial_number')}")
    print(f"  best_value            = {checkpoint.get('best_value')}")
    print(f"  updated_at            = {checkpoint.get('updated_at')}")
    print(f"  param_count           = {checkpoint.get('param_count')}")
    print("  retrain_cli           = --warm-start-checkpoint <checkpoint> (queues best_params as pending trial)")
    print("  status                = DEPLOYED opt-in; not auto-promote")
    print()


def print_layer_status(conn: Any) -> None:
    has_panel_code = (REPO_ROOT / "backend" / "scripts" / "build_feature_panel_duck.py").exists()
    has_panel_tests = (REPO_ROOT / "backend" / "tests" / "pipeline" / "test_feature_panel_incremental.py").exists()
    has_validation = table_exists(conn, "mart_feature_panel_validation")
    has_manifest = table_exists(conn, "mart_pipeline_run_manifest")
    print("=== layer status ===")
    print("  L1 paper_sim sim_config_hash    DEPLOYED code; current DB coverage shown above")
    print("  L2 predictions reuse implicit   IMPLICIT via model_id")
    print(
        "  L3 panel incremental rebuild    "
        f"{'DEPLOYED' if has_panel_code and has_panel_tests else 'PARTIAL'} "
        f"(cli --mode incremental; validation_table={has_validation}; manifest_table={has_manifest})"
    )
    warm_start = _latest_warm_start_checkpoint()
    print(
        "  L4 retrain warm-start           "
        f"{'DEPLOYED opt-in' if warm_start else 'SPEC'} "
        "(checkpoint best_params -> Optuna enqueue_trial; not auto-promote)"
    )


def main() -> int:
    with duck_connection(writable=False) as conn:
        print_paper_sim_cache(conn)
        print_l3_panel_incremental(conn)
        print_l4_retrain_warm_start()
        print_layer_status(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
