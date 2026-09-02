#!/usr/bin/env python3
"""Read-only status check for the strategy-lab control plane."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from services.main_rally_dataset_snapshot import dataset_snapshot_from_main_rally
from services.research_runtime import dataset_snapshot_from_disclosure
from services.strategy_lab import (
    ComputeRequest,
    StrategyLabError,
    StrategyLabPolicy,
    assess_compute,
    build_ingress_plan,
    load_policy,
)
from services.strategy_spec import (
    StrategySpecError,
    disclosure_freeze_coverage,
    load_all_strategy_packages,
)


REPO = Path(__file__).resolve().parents[2]


def _live_input_status(
    policy: StrategyLabPolicy,
    *,
    repo: Path = REPO,
) -> dict[str, object]:
    holdout_raw = yaml.safe_load(
        (repo / "backend" / "config" / "holdout_policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    holdout_start = str(holdout_raw["holdout_start"])
    train_end = (
        datetime.strptime(holdout_start, "%Y%m%d")
        - timedelta(days=policy.validation_calendar_days)
    ).strftime("%Y%m%d")
    cases = (
        (
            "main_rally",
            repo / "data" / "lineage" / "main_rally_dataset_snapshot" / "snapshot.json",
            dataset_snapshot_from_main_rally,
        ),
        (
            "disclosure",
            repo / "data" / "lineage" / "disclosure_dataset_snapshot.json",
            dataset_snapshot_from_disclosure,
        ),
    )
    results: dict[str, object] = {}
    for name, path, adapter in cases:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = adapter(payload)
            bundle = build_ingress_plan(
                snapshot,
                train_end=train_end,
                holdout_start=holdout_start,
            )
            results[name] = {
                "status": "READY",
                "reason": "",
                "snapshot_id": bundle.snapshot_id,
            }
        except (OSError, ValueError, KeyError, StrategyLabError) as exc:
            results[name] = {"status": "BLOCKED", "reason": str(exc)}
    return {
        "holdout_start": holdout_start,
        "train_end": train_end,
        "snapshots": results,
        "ready": all(
            item.get("status") == "READY"
            for item in results.values()
            if isinstance(item, dict)
        ),
    }


def _formal_rx_compute(
    policy: StrategyLabPolicy,
    live_inputs: dict[str, object],
    *,
    repo: Path = REPO,
) -> dict[str, object]:
    if not live_inputs.get("ready"):
        return {
            "allowed": False,
            "claimable": False,
            "reasons": ["live_inputs_not_ready"],
        }
    path = repo / "data" / "lineage" / "disclosure_dataset_snapshot.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot = dataset_snapshot_from_disclosure(payload)
        bundle = build_ingress_plan(
            snapshot,
            train_end=str(live_inputs["train_end"]),
            holdout_start=str(live_inputs["holdout_start"]),
        )
        admission = assess_compute(
            bundle,
            ComputeRequest(stage="formal_rx", executor="local"),
        )
    except (OSError, ValueError, KeyError, StrategyLabError) as exc:
        return {
            "allowed": False,
            "claimable": False,
            "reasons": [str(exc)],
        }
    _ = policy
    return admission.as_dict()


def _strategy_packages() -> dict[str, object]:
    try:
        specs = load_all_strategy_packages()
    except StrategySpecError as exc:
        return {
            "loaded": False,
            "claimable": False,
            "reason": str(exc),
        }
    return {
        "loaded": True,
        "claimable": False,
        "packages": sorted({spec.package_id for spec in specs}),
        "spec_ids": [spec.spec_id for spec in specs],
    }


def _disclosure_coverage(*, repo: Path) -> dict[str, object]:
    path = repo / "data" / "lineage" / "disclosure_dataset_snapshot.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "denominator": "disclosure_freeze_partitions",
            "status": "BLOCKED",
            "reason": str(exc),
        }
    coverage = disclosure_freeze_coverage(payload)
    coverage["status"] = "READY"
    return coverage


def _ablation_verdicts(*, repo: Path) -> dict[str, object]:
    manifests = {
        "phase_e": repo / "data" / "lineage" / "phase_e_experiment_verdicts" / "manifest.json",
        "phase_f": repo / "data" / "lineage" / "phase_f_experiment_verdicts" / "manifest.json",
    }
    return {
        "role": "ablation_only",
        "claimable": False,
        "not_strategy_spec": True,
        "manifests": {
            name: {
                "present": path.is_file(),
                "path": str(path.relative_to(repo)),
            }
            for name, path in manifests.items()
        },
    }


def build_status() -> dict[str, object]:
    policy = load_policy()
    live_inputs = _live_input_status(policy)
    control_plane_ready = (
        policy.status == "framework_only"
        and policy.execution_mode == "manual_only"
    )
    formal_rx_compute = _formal_rx_compute(policy, live_inputs)
    return {
        "framework_installed": control_plane_ready,
        "framework_ready": control_plane_ready and bool(live_inputs["ready"]),
        "status": policy.status,
        "execution_mode": policy.execution_mode,
        "formal_rx_compute": formal_rx_compute,
        "claimable": False,
        "live_inputs": live_inputs,
        "strategy_packages": _strategy_packages(),
        "disclosure_coverage": _disclosure_coverage(repo=REPO),
        "ablation_verdicts": _ablation_verdicts(repo=REPO),
        "formula_challenge": {
            "status": "synthetic_smoke_ready",
            "one_name_replay": "offline_day_membership_ready",
            "live_pointer_bind": "one_name_ready",
            "live_replay": "not_implemented",
            "b5_ablation": "not_implemented",
            "purged_wf": "not_implemented",
            "holdout": "not_implemented",
            "experiment_verdict": "not_implemented",
            "absorb": "not_implemented",
            "claimable": False,
        },
        "follow_spec_paper": {
            "status": "snapshot_events_ready",
            "ablation_json": "not_this_spec",
            "claimable": False,
        },
        "rally_setup_paper": {
            "status": "ready",
            "full_episode": "not_implemented",
            "claimable": False,
        },
        "notes": [
            "accepted DatasetSnapshot -> read-only ResearchInputBundle",
            "development bundle rejects sealed holdout and Tier3 labels",
            "formal RX validators: holdout policy + opaque seal; claimable stays false",
            "loaded StrategySpec is not claimable and is not StrategyRelease",
            "disclosure coverage denominator excludes freeze nominal_ohlcv",
            "E/F verdict JSON is ablation-only, not institution_follow StrategySpec",
            "follow spec paper reads stk_holdertrade events; E/F JSON is not that spec",
            "rally setup paper is next-open plus named horizon, not full-episode and not Release",
            "formula challenge is synthetic smoke + one-name live pointer; universe B5/absorb not implemented",
            "Optuna runner and Modal adapter are not implemented",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--framework", action="store_true", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    status = build_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    else:
        prefix = "PASS" if status["framework_ready"] else "BLOCKED"
        print(f"{prefix} strategy_lab framework_only manual_only")
    return 0 if status["framework_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
