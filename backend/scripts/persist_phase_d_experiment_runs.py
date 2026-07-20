#!/usr/bin/env python3
"""Persist immutable Phase D ExperimentRun artifacts (idempotent regenerate).

Writes JSON + manifest under data/lineage/phase_d_experiment_runs/:

1. ``b0_bound`` — offline B0-bound loop with the **real measured purged
   walk-forward plan** (from Phase E b0 artifact).
2. ``measured_offline`` — runtime-owned measured path (not a strategy package)
   producing ExperimentVerdict with claimable=false and numeric fills.

Fail-closed: missing snapshot, missing/invalid b0 walk_forward plan, or a
non-purged plan aborts instead of silently falling back to stub hooks.
Never loosens gates, never emits StrategyRelease; claimable stays false.

Usage:
  PYTHONPATH=backend python backend/scripts/persist_phase_d_experiment_runs.py
  PYTHONPATH=backend python backend/scripts/persist_phase_d_experiment_runs.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_sources.disclosure_dataset_snapshot import (  # noqa: E402
    DISCLOSURE_SNAPSHOT_RELPATH,
    default_snapshot_path,
)
from services.research_runtime import (  # noqa: E402
    ResearchObservation,
    ResearchRuntimeError,
    dataset_snapshot_from_disclosure,
    default_fold_embargo_hooks,
    fold_embargo_from_walk_forward_plan,
    run_offline_b0_bound_loop,
    run_offline_measured_loop,
)

REPO = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "data/lineage/phase_d_experiment_runs"
OUT_DIR = REPO / OUT_DIR_REL
MANIFEST_NAME = "manifest.json"
B0_ARTIFACT_REL = "data/lineage/phase_e_experiment_verdicts/b0.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def load_measured_walk_forward_plan(
    b0_artifact_path: Path,
) -> dict[str, Any]:
    """Read the measured WF plan from the persisted Phase E b0 artifact."""

    if not b0_artifact_path.is_file():
        raise SystemExit(f"missing Phase E b0 artifact at {b0_artifact_path}")
    payload = json.loads(b0_artifact_path.read_text(encoding="utf-8"))
    plan = (
        (payload.get("verdict_full") or {}).get("details") or {}
    ).get("walk_forward")
    if not isinstance(plan, dict):
        raise SystemExit(
            f"Phase E b0 artifact has no measured walk_forward plan: "
            f"{b0_artifact_path}"
        )
    return plan


def _manifest_current(
    manifest_path: Path,
    snapshot_hash: str,
    b0_artifact_hash: str,
    *,
    repo: Path,
) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("snapshot_hash") != snapshot_hash:
        return False
    if payload.get("b0_artifact_hash") != b0_artifact_hash:
        return False
    runs = payload.get("runs") or {}
    for key in ("b0_bound", "measured_offline"):
        rel = runs.get(key)
        if not rel or not (repo / rel).is_file():
            return False
    return True


def _runtime_owned_trade_obs(
    snap_lower: str,
    snap_upper: str,
) -> tuple[ResearchObservation, ...]:
    """Deterministic synthetic trade legs inside the disclosure freeze window."""

    return (
        ResearchObservation(
            entity_id="600000.SH",
            event_date=snap_lower,
            available_at=snap_lower,
            payload={
                "entry_px": 10.0,
                "exit_px": 10.2,
                "entry_date": snap_lower,
                "fold_role": "one_touch_holdout",
            },
        ),
        ResearchObservation(
            entity_id="600001.SH",
            event_date=snap_upper,
            available_at=snap_upper,
            payload={
                "entry_px": 20.0,
                "exit_px": 19.6,
                "entry_date": snap_upper,
                "fold_role": "purged_eval",
            },
        ),
    )


def persist(*, force: bool = False, repo: Path = REPO) -> dict[str, Any]:
    out_dir = repo / OUT_DIR_REL
    snap_path = default_snapshot_path()
    if not snap_path.is_file():
        raise SystemExit(f"missing frozen snapshot at {snap_path}")
    snapshot_hash = _sha256_file(snap_path)
    b0_artifact_path = REPO / B0_ARTIFACT_REL
    plan = load_measured_walk_forward_plan(b0_artifact_path)
    b0_artifact_hash = _sha256_file(b0_artifact_path)

    manifest_path = out_dir / MANIFEST_NAME
    if not force and _manifest_current(
        manifest_path, snapshot_hash, b0_artifact_hash, repo=repo
    ):
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            f"[persist] already current snapshot_hash={snapshot_hash[:12]}… "
            f"dir={OUT_DIR_REL}",
            file=sys.stderr,
        )
        return existing

    # Validate plan shape before the loop so a bad artifact fails loudly here.
    hooks = fold_embargo_from_walk_forward_plan(plan)
    if hooks.protocol != "purged_walk_forward" or hooks.n_folds < 1:
        raise ResearchRuntimeError(
            f"measured plan is not a usable purged walk-forward plan: "
            f"protocol={hooks.protocol!r} n_folds={hooks.n_folds}"
        )

    run, verdict = run_offline_b0_bound_loop(walk_forward_plan=plan)
    if verdict.claimable:
        raise ResearchRuntimeError(
            "Phase D persisted verdict must not be claimable"
        )

    disclosure = json.loads(snap_path.read_text(encoding="utf-8"))
    runtime_snap = dataset_snapshot_from_disclosure(disclosure)
    measured_hooks = default_fold_embargo_hooks(
        n_folds=hooks.n_folds,
        embargo_days=hooks.embargo_days,
        label_horizon_days=hooks.label_horizon_days,
        holdout_start=hooks.holdout_start,
    )
    # Prefer the real measured fold ids when binding runtime-owned measure.
    measured_hooks = replace(
        measured_hooks,
        protocol=hooks.protocol,
        fold_ids=hooks.fold_ids,
        notes=hooks.notes + ("persisted_with_runtime_owned_measured_path",),
    )
    m_run, m_verdict = run_offline_measured_loop(
        runtime_snap,
        _runtime_owned_trade_obs(
            runtime_snap.available_at_lower,
            runtime_snap.available_at_upper,
        ),
        decision_date=runtime_snap.available_at_upper,
        fold_embargo=measured_hooks,
    )
    if m_verdict.claimable:
        raise ResearchRuntimeError(
            "Phase D runtime-owned measured verdict must not be claimable"
        )
    measure = (m_run.artifact_manifest or {}).get("measure") or {}
    if measure.get("status") != "measured" or measure.get("paper_fills") != "measured":
        raise ResearchRuntimeError(
            "Phase D runtime-owned path did not produce measured fills"
        )

    run_payload = {
        "schema_version": 1,
        "kind": "phase_d_experiment_run",
        "snapshot_relpath": DISCLOSURE_SNAPSHOT_RELPATH,
        "snapshot_hash": snapshot_hash,
        "b0_artifact_relpath": B0_ARTIFACT_REL,
        "b0_artifact_hash": b0_artifact_hash,
        "fold_embargo_bound": hooks.as_dict(),
        "run": run.as_dict(),
        "verdict": verdict.as_dict(),
        "strategy_release": False,
        "optuna": False,
    }
    rel = f"{OUT_DIR_REL}/b0_bound.json"
    _write_json(repo / rel, run_payload)

    measured_payload = {
        "schema_version": 1,
        "kind": "phase_d_experiment_run",
        "path": "runtime_owned_measured_offline",
        "snapshot_relpath": DISCLOSURE_SNAPSHOT_RELPATH,
        "snapshot_hash": snapshot_hash,
        "run": m_run.as_dict(),
        "verdict": m_verdict.as_dict(),
        "strategy_release": False,
        "optuna": False,
    }
    measured_rel = f"{OUT_DIR_REL}/measured_offline.json"
    _write_json(repo / measured_rel, measured_payload)

    manifest = {
        "schema_version": 1,
        "kind": "phase_d_experiment_run_manifest",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_relpath": DISCLOSURE_SNAPSHOT_RELPATH,
        "snapshot_hash": snapshot_hash,
        "b0_artifact_relpath": B0_ARTIFACT_REL,
        "b0_artifact_hash": b0_artifact_hash,
        "runs": {"b0_bound": rel, "measured_offline": measured_rel},
        "summary": {
            "experiment_id": run.experiment_id,
            "strategy_package": run.strategy_package,
            "block": run.block,
            "snapshot_id": run.snapshot_id,
            "universe_id": run.universe_id,
            "verdict": verdict.verdict,
            "reason": verdict.reason,
            "claimable": verdict.claimable,
            "strategy_release": False,
            "fold_protocol": hooks.protocol,
            "n_folds": hooks.n_folds,
            "fold_ids": list(hooks.fold_ids),
            "embargo_days": hooks.embargo_days,
            "label_horizon_days": hooks.label_horizon_days,
            "holdout_start": hooks.holdout_start,
            "measured_offline": {
                "experiment_id": m_run.experiment_id,
                "strategy_package": m_run.strategy_package,
                "verdict": m_verdict.verdict,
                "reason": m_verdict.reason,
                "claimable": m_verdict.claimable,
                "measure_status": measure.get("status"),
                "paper_fills": measure.get("paper_fills"),
                "total_return": measure.get("total_return"),
                "n_trades_completed": measure.get("n_trades_completed"),
            },
        },
        "consumers": ["backend/scripts/build_agent_board.py"],
    }
    _write_json(manifest_path, manifest)
    print(
        f"[persist] wrote {OUT_DIR_REL}/ "
        f"snapshot_hash={snapshot_hash[:12]}… "
        f"protocol={hooks.protocol} folds={hooks.n_folds} "
        f"claimable={verdict.claimable} "
        f"measured_offline_trades={measure.get('n_trades_completed')}",
        file=sys.stderr,
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even when manifest matches snapshot/b0 artifact hashes",
    )
    args = ap.parse_args()
    manifest = persist(force=args.force)
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
