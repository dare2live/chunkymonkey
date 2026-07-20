#!/usr/bin/env python3
"""Persist immutable Phase D ExperimentRun artifacts (idempotent regenerate).

Writes JSON + manifest under data/lineage/phase_d_experiment_runs/. Runs the
shared research runtime offline B0-bound loop with the **real measured purged
walk-forward plan** (read from the persisted Phase E b0 artifact — accepted
evidence, not a stub) bound into prereg fold/embargo hooks.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_sources.disclosure_dataset_snapshot import (  # noqa: E402
    DISCLOSURE_SNAPSHOT_RELPATH,
    default_snapshot_path,
)
from services.research_runtime import (  # noqa: E402
    ResearchRuntimeError,
    fold_embargo_from_walk_forward_plan,
    run_offline_b0_bound_loop,
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
    rel = (payload.get("runs") or {}).get("b0_bound")
    return bool(rel) and (repo / rel).is_file()


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

    manifest = {
        "schema_version": 1,
        "kind": "phase_d_experiment_run_manifest",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_relpath": DISCLOSURE_SNAPSHOT_RELPATH,
        "snapshot_hash": snapshot_hash,
        "b0_artifact_relpath": B0_ARTIFACT_REL,
        "b0_artifact_hash": b0_artifact_hash,
        "runs": {"b0_bound": rel},
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
        },
        "consumers": ["backend/scripts/build_agent_board.py"],
    }
    _write_json(manifest_path, manifest)
    print(
        f"[persist] wrote {OUT_DIR_REL}/ "
        f"snapshot_hash={snapshot_hash[:12]}… "
        f"protocol={hooks.protocol} folds={hooks.n_folds} "
        f"claimable={verdict.claimable}",
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
