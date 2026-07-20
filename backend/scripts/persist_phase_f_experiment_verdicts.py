#!/usr/bin/env python3
"""Persist Phase F F0+F1+F2 artifacts (main_rally snapshot + B0/B1 verdicts).

Writes:
  data/lineage/main_rally_dataset_snapshot/snapshot.json  (F0, if missing or --freeze)
  data/lineage/phase_f_experiment_verdicts/{b0.json,b1.json,manifest.json}

B1 = B0 + Tier1 stock-state FeatureBlock (same snapshot/folds/costs/paper as
B0). B1 claimable=true requires accept edge gates AND a strict holdout
return lift vs B0 (REQUIRE_HOLDOUT_LIFT_VS_B0) — never a fake improve.

Does not loosen gates, promote StrategyRelease, rebuild GT, or flip cutover.

Usage:
  PYTHONPATH=backend python backend/scripts/persist_phase_f_experiment_verdicts.py
  PYTHONPATH=backend python backend/scripts/persist_phase_f_experiment_verdicts.py --force
  PYTHONPATH=backend python backend/scripts/persist_phase_f_experiment_verdicts.py --freeze
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

from services.main_rally_b0 import run_b0_scaffold  # noqa: E402
from services.main_rally_b1 import run_b1_scaffold  # noqa: E402
from services.main_rally_dataset_snapshot import (  # noqa: E402
    MAIN_RALLY_SNAPSHOT_RELPATH,
    default_snapshot_path,
    freeze_main_rally_dataset_snapshot,
    load_frozen_main_rally_snapshot,
)

REPO = Path(__file__).resolve().parents[2]
OUT_DIR_REL = "data/lineage/phase_f_experiment_verdicts"
OUT_DIR = REPO / OUT_DIR_REL
MANIFEST_NAME = "manifest.json"
BLOCKS = ("b0", "b1")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _metrics_summary(details: dict[str, Any]) -> dict[str, Any]:
    m = details.get("metrics")
    h = details.get("holdout_metrics")
    edge = details.get("accept_edge_gates")
    out: dict[str, Any] = {
        "metrics": m if isinstance(m, dict) else m,
        "holdout_metrics": h if isinstance(h, dict) else h,
        "accept_edge_gates": edge if isinstance(edge, dict) else edge,
    }
    cov = (
        details.get("setup_coverage")
        or details.get("bare_k_coverage")
        or details.get("stock_state_coverage")
    )
    if cov is not None:
        out["coverage"] = cov
    stability = details.get("holdout_lift_stability")
    if stability is not None:
        out["holdout_lift_stability"] = stability
    delta = details.get("delta_b1_minus_b0")
    if delta is not None:
        out["delta_b1_minus_b0"] = delta
    return out


def _block_payload(
    *,
    block: str,
    run: Any,
    verdict: Any,
    snapshot_hash: str,
    snapshot_id: str,
    phase_f_ablation: str,
    snapshot_scope: str,
) -> dict[str, Any]:
    v = verdict.as_dict()
    return {
        "schema_version": 1,
        "kind": "phase_f_experiment_verdict",
        "block": block,
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "snapshot_relpath": MAIN_RALLY_SNAPSHOT_RELPATH,
        "snapshot_scope": snapshot_scope,
        "phase_f_ablation": phase_f_ablation,
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


def persist(*, force: bool = False, freeze: bool = False) -> dict[str, Any]:
    snap_path = default_snapshot_path()
    if freeze or not snap_path.is_file():
        print(f"[persist] freezing F0 snapshot → {MAIN_RALLY_SNAPSHOT_RELPATH}", file=sys.stderr)
        freeze_main_rally_dataset_snapshot(path=snap_path)

    if not snap_path.is_file():
        raise SystemExit(f"missing frozen snapshot at {snap_path}")
    snapshot_hash = _sha256_file(snap_path)
    snapshot = load_frozen_main_rally_snapshot(snap_path)
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    phase_f_ablation = str(snapshot.get("phase_f_ablation") or "")
    snapshot_scope = str(snapshot.get("scope") or "")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / MANIFEST_NAME
    if (
        not force
        and manifest_path.is_file()
        and all((OUT_DIR / f"{b}.json").is_file() for b in BLOCKS)
    ):
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("snapshot_hash") == snapshot_hash:
            print(
                f"[persist] already current snapshot_hash={snapshot_hash[:12]}… "
                f"dir={OUT_DIR_REL}",
                file=sys.stderr,
            )
            return existing

    print("[persist] running F1 main_rally B0 measured scaffold…", file=sys.stderr)
    b0_run, b0_verdict = run_b0_scaffold(snapshot=snapshot)
    print("[persist] running F2 main_rally B1 stock-state measured scaffold…", file=sys.stderr)
    b1_run, b1_verdict = run_b1_scaffold(snapshot=snapshot, b0_run=b0_run)

    pairs = {"b0": (b0_run, b0_verdict), "b1": (b1_run, b1_verdict)}
    block_rels: dict[str, str] = {}
    ladder: list[dict[str, Any]] = []
    for name, (run, verdict) in pairs.items():
        payload = _block_payload(
            block=name,
            run=run,
            verdict=verdict,
            snapshot_hash=snapshot_hash,
            snapshot_id=snapshot_id,
            phase_f_ablation=phase_f_ablation,
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
    b0_details = dict((b0_verdict.as_dict() or {}).get("details") or {})
    wf = dict(b0_details.get("walk_forward") or {})
    trading_days = [
        str(d).replace("-", "")[:8]
        for d in (wf.get("trading_days") or [])
        if str(d).replace("-", "")[:8].isdigit()
    ]
    if not trading_days:
        cov = dict(
            b0_details.get("setup_coverage")
            or b0_details.get("bare_k_coverage")
            or {}
        )
        trading_days = [
            str(d).replace("-", "")[:8]
            for d in (cov.get("accepted_nominal_partitions") or [])
            if str(d).replace("-", "")[:8].isdigit()
        ]
    trading_days = sorted(set(trading_days))
    window_start = trading_days[0] if trading_days else None
    window_end = trading_days[-1] if trading_days else None
    n_days = len(trading_days)
    overall_status = (
        "measured_accept_claimable"
        if any_claimable
        else "measured_reject_or_inconclusive_setup_entry"
    )
    manifest = {
        "schema_version": 1,
        "kind": "phase_f_experiment_verdict_manifest",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "snapshot_relpath": MAIN_RALLY_SNAPSHOT_RELPATH,
        "snapshot_scope": snapshot_scope,
        "phase_f_ablation": phase_f_ablation,
        "strategy_package": "main_rally_v1",
        "slices_complete": ["F0", "F1", "F2"],
        "window": {
            "label": f"bounded_{n_days}d_nominal_k_setup_entry",
            "start": window_start,
            "end": window_end,
            "trading_day_count": n_days,
            "full_episode_not_attempted": True,
        },
        "protocol": {
            "name": str(wf.get("protocol") or "unknown"),
            "folds": len(wf.get("folds") or []),
            "embargo": 1,
            "holdout_days": 2,
            "execution": "t+1_paper_nominal",
            "accept_edge_gates": True,
            "claimable_protocol": bool(wf.get("claimable_protocol")),
            "signal": "rally_setup_pivot_confirmed_base_days",
            "b1_feature_block": getattr(
                b1_run.feature_block, "block_id", None
            ),
            "require_holdout_lift_vs_b0": True,
        },
        "overall": {
            "status": overall_status,
            "any_claimable": any_claimable,
            "strategy_release": False,
            "cutover_unchanged": True,
            "note": (
                "F0+F1+F2 setup-entry short-horizon ablation (B0 bare setup, "
                "B1 + Tier1 stock state) on accepted nominal window; "
                "reject/inconclusive claimable=false is an honest deliverable. "
                "B1 accept requires accept edge gates AND strict holdout lift "
                "vs B0 (REQUIRE_HOLDOUT_LIFT_VS_B0). Full-episode deferred. "
                "No Optuna / StrategyRelease."
            ),
        },
        "ladder": ladder,
        "blocks": block_rels,
    }
    _write_json(manifest_path, manifest)
    print(
        f"[persist] wrote {OUT_DIR_REL}/ "
        f"snapshot_hash={snapshot_hash[:12]}… "
        f"verdict={ladder[0]['verdict']} claimable={any_claimable}",
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
    ap.add_argument(
        "--freeze",
        action="store_true",
        help="Re-freeze F0 DatasetSnapshot from live accepted/GT hashes",
    )
    args = ap.parse_args()
    manifest = persist(force=args.force, freeze=args.freeze)
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
