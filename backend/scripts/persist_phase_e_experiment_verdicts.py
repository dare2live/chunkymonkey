#!/usr/bin/env python3
"""Persist Phase E B0–B4 ExperimentVerdict artifacts (idempotent regenerate).

Writes JSON + manifest under data/lineage/phase_e_experiment_verdicts/.
Uses the frozen disclosure DatasetSnapshot and live measured paper runs.
Does not loosen gates, promote StrategyRelease, or write experiment_store rows.

Usage:
  PYTHONPATH=backend python backend/scripts/persist_phase_e_experiment_verdicts.py
  PYTHONPATH=backend python backend/scripts/persist_phase_e_experiment_verdicts.py --force
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
from services.institution_follow_b0 import (  # noqa: E402
    load_frozen_disclosure_snapshot,
    run_b0_scaffold,
)
from services.institution_follow_b1 import run_b1_scaffold  # noqa: E402
from services.institution_follow_b1_measure import open_stock_state_conn  # noqa: E402
from services.institution_follow_b2 import run_b2_scaffold  # noqa: E402
from services.institution_follow_b4 import run_b4_scaffold  # noqa: E402
from services.institution_follow_b4_measure import open_holders_conn  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "data/lineage/phase_e_experiment_verdicts"
OUT_DIR = REPO / OUT_DIR_REL
MANIFEST_NAME = "manifest.json"
BLOCKS = ("b0", "b1", "b2", "b4")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _metrics_summary(details: dict[str, Any]) -> dict[str, Any]:
    """Compact metrics for the ladder table (full details remain in block JSON)."""

    m = details.get("metrics")
    h = details.get("holdout_metrics")
    edge = details.get("accept_edge_gates")
    lift = details.get("holdout_lift_stability") or details.get(
        "holdout_lift_vs_b0"
    )
    out: dict[str, Any] = {
        "metrics": m if isinstance(m, dict) else m,
        "holdout_metrics": h if isinstance(h, dict) else h,
        "accept_edge_gates": edge if isinstance(edge, dict) else edge,
        "holdout_lift_stability": lift if isinstance(lift, dict) else lift,
    }
    cov = (
        details.get("bare_k_coverage")
        or details.get("state_coverage")
        or details.get("market_context_coverage")
        or details.get("disclosure_event_coverage")
        or details.get("coverage")
    )
    if cov is not None:
        out["coverage"] = cov
    return out


def _block_payload(
    *,
    block: str,
    run: Any,
    verdict: Any,
    snapshot_hash: str,
    snapshot_id: str,
    phase_e_ablation: str,
    snapshot_scope: str,
) -> dict[str, Any]:
    v = verdict.as_dict()
    return {
        "schema_version": 1,
        "kind": "phase_e_experiment_verdict",
        "block": block,
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "snapshot_relpath": DISCLOSURE_SNAPSHOT_RELPATH,
        "snapshot_scope": snapshot_scope,
        "phase_e_ablation": phase_e_ablation,
        "experiment_id": getattr(run, "experiment_id", None),
        "surface_status": getattr(run, "surface_status", None),
        "verdict": v.get("verdict"),
        "reason": v.get("reason"),
        "blocked": v.get("blocked"),
        "claimable": v.get("claimable"),
        "strategy_release": False,
        "metrics_summary": _metrics_summary(dict(v.get("details") or {})),
        "verdict_full": v,
        "run": run.as_dict(),
        "notes": list(getattr(run, "notes", ()) or ()),
    }


def _manifest_current(manifest_path: Path, snapshot_hash: str) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("snapshot_hash") != snapshot_hash:
        return False
    blocks = payload.get("blocks") or {}
    for name in BLOCKS:
        rel = blocks.get(name)
        if not rel or not (REPO / rel).is_file():
            return False
    return True


def persist(*, force: bool = False) -> dict[str, Any]:
    snap_path = default_snapshot_path()
    if not snap_path.is_file():
        raise SystemExit(f"missing frozen snapshot at {snap_path}")
    snapshot_hash = _sha256_file(snap_path)
    snapshot = load_frozen_disclosure_snapshot(snap_path)
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    phase_e_ablation = str(snapshot.get("phase_e_ablation") or "")
    snapshot_scope = str(snapshot.get("scope") or "")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / MANIFEST_NAME
    if not force and _manifest_current(manifest_path, snapshot_hash):
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(
            f"[persist] already current snapshot_hash={snapshot_hash[:12]}… "
            f"dir={OUT_DIR_REL}",
            file=sys.stderr,
        )
        return existing

    # Shared B0 context — one paper measure, reused by B1/B2/B4.
    b0_run, b0_verdict = run_b0_scaffold(snapshot=snapshot)
    state_conn = open_stock_state_conn()
    holders_conn = open_holders_conn()
    try:
        b1_run, b1_verdict = run_b1_scaffold(
            snapshot=snapshot, b0_run=b0_run, state_conn=state_conn
        )
        b2_run, b2_verdict = run_b2_scaffold(snapshot=snapshot, b0_run=b0_run)
        b4_run, b4_verdict = run_b4_scaffold(
            snapshot=snapshot, b0_run=b0_run, holders_conn=holders_conn
        )
    finally:
        state_conn.close()
        holders_conn.close()

    pairs = {
        "b0": (b0_run, b0_verdict),
        "b1": (b1_run, b1_verdict),
        "b2": (b2_run, b2_verdict),
        "b4": (b4_run, b4_verdict),
    }
    block_rels: dict[str, str] = {}
    ladder: list[dict[str, Any]] = []
    for name, (run, verdict) in pairs.items():
        payload = _block_payload(
            block=name,
            run=run,
            verdict=verdict,
            snapshot_hash=snapshot_hash,
            snapshot_id=snapshot_id,
            phase_e_ablation=phase_e_ablation,
            snapshot_scope=snapshot_scope,
        )
        rel = f"{OUT_DIR_REL}/{name}.json"
        _write_json(REPO / rel, payload)
        block_rels[name] = rel
        ladder.append(
            {
                "block": name,
                "verdict": payload["verdict"],
                "reason": payload["reason"],
                "claimable": payload["claimable"],
                "strategy_release": False,
                "metrics_summary": payload["metrics_summary"],
            }
        )

    any_claimable = any(row["claimable"] for row in ladder)
    manifest = {
        "schema_version": 1,
        "kind": "phase_e_experiment_verdict_manifest",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "snapshot_relpath": DISCLOSURE_SNAPSHOT_RELPATH,
        "snapshot_scope": snapshot_scope,
        "phase_e_ablation": phase_e_ablation,
        "window": {
            "label": "bounded_40d_nominal_k",
            "start": "20260522",
            "end": "20260717",
        },
        "protocol": {
            "folds": 3,
            "embargo": 1,
            "holdout_days": 2,
            "execution": "t+1_paper_nominal",
            "accept_edge_gates": True,
            "holdout_lift_vs_b0": True,
        },
        "overall": {
            "status": "measured_reject_no_gain",
            "any_claimable": any_claimable,
            "strategy_release": False,
            "note": (
                "First-class failed/no-gain experiment on bounded window; "
                "do not loosen gates; next=longer-window stability OR stop "
                "until new data."
            ),
        },
        "ladder": ladder,
        "blocks": block_rels,
        "form_qfq_frontier": {
            "requested": "20260717",
            "status": "still_blocked",
            "note": (
                "raw_tushare_daily/adj_factor and qfq/form max remain "
                "20260716; nominal accepted K exists for 20260717."
            ),
        },
    }
    _write_json(manifest_path, manifest)
    print(
        f"[persist] wrote {OUT_DIR_REL}/ "
        f"snapshot_hash={snapshot_hash[:12]}… "
        f"claimable={any_claimable}",
        file=sys.stderr,
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even when manifest matches snapshot_hash",
    )
    args = ap.parse_args()
    manifest = persist(force=args.force)
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
