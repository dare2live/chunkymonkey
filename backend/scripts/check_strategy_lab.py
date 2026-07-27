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
    StrategyLabError,
    StrategyLabPolicy,
    build_ingress_plan,
    load_policy,
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


def build_status() -> dict[str, object]:
    policy = load_policy()
    live_inputs = _live_input_status(policy)
    control_plane_ready = (
        policy.status == "framework_only"
        and policy.execution_mode == "manual_only"
    )
    return {
        "framework_installed": control_plane_ready,
        "framework_ready": control_plane_ready and bool(live_inputs["ready"]),
        "status": policy.status,
        "execution_mode": policy.execution_mode,
        "formal_rx_authorized": bool(policy.formal_rx_authorization),
        "optuna_authorized": bool(policy.phase_n_authorization),
        "modal_authorized": bool(policy.remote_compute_authorization),
        "claimable": False,
        "live_inputs": live_inputs,
        "notes": [
            "accepted DatasetSnapshot -> read-only ResearchInputBundle",
            "development bundle rejects sealed holdout and Tier3 labels",
            "formal RX requires snapshot/evidence/owner authorization",
            "formal RX validators, Optuna runner, and Modal adapter are not implemented",
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
        print(
            f"{prefix} strategy_lab framework_only manual_only "
            f"formal_rx={status['formal_rx_authorized']} "
            f"optuna={status['optuna_authorized']} "
            f"modal={status['modal_authorized']}"
        )
    return 0 if status["framework_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
