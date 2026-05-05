#!/usr/bin/env python3
"""Benchmark model-pipeline performance and write a manifest row.

The script has two modes:
  - --dry-run: print and record the planned commands without executing them.
  - execute: run a small or full benchmark plan and record per-step timings.

CI should only assert the plan/contract shape. Real duration thresholds are
tracked in mart_pipeline_run_manifest.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent


def benchmark_plan(profile: str, *, model_id: str | None = None) -> list[dict[str, Any]]:
    py = sys.executable
    if profile == "small":
        train_prefix = "benchmark_small"
        return [
            {
                "name": "train_small",
                "kind": "train",
                "command": [
                    py,
                    str(SCRIPT_DIR / "train_multidim_model.py"),
                    "--start",
                    "2025-01-01",
                    "--end",
                    "2025-06-30",
                    "--feature-group",
                    "base",
                    "--trials",
                    "1",
                    "--objective-num-round",
                    "20",
                    "--num-round",
                    "20",
                    "--model-id-prefix",
                    train_prefix,
                ],
                "model_prefix": train_prefix,
            },
            {
                "name": "walkforward_small",
                "kind": "walkforward",
                "command": [
                    py,
                    str(SCRIPT_DIR / "run_multidim_walkforward.py"),
                    "--model-id",
                    model_id or "{model_id}",
                    "--start",
                    "2025-01-01",
                    "--end",
                    "2025-09-30",
                    "--feature-group",
                    "base",
                    "--max-folds",
                    "1",
                    "--walkforward-num-round",
                    "20",
                ],
            },
        ]
    if profile == "full":
        train_prefix = "benchmark_full"
        return [
            {
                "name": "train_full_base_dense",
                "kind": "train",
                "command": [
                    py,
                    str(SCRIPT_DIR / "train_multidim_model.py"),
                    "--start",
                    "2023-01-01",
                    "--end",
                    "2026-04-30",
                    "--feature-group",
                    "base_dense_v2",
                    "--trials",
                    "2",
                    "--objective-num-round",
                    "80",
                    "--num-round",
                    "80",
                    "--model-id-prefix",
                    train_prefix,
                ],
                "model_prefix": train_prefix,
            },
            {
                "name": "walkforward_full_2fold",
                "kind": "walkforward",
                "command": [
                    py,
                    str(SCRIPT_DIR / "run_multidim_walkforward.py"),
                    "--model-id",
                    model_id or "{model_id}",
                    "--start",
                    "2023-01-01",
                    "--end",
                    "2026-04-30",
                    "--feature-group",
                    "base_dense_v2",
                    "--max-folds",
                    "2",
                    "--save-predictions",
                    "--prediction-mode",
                    "topk",
                    "--prediction-top-k",
                    "100",
                    "--walkforward-num-round",
                    "80",
                ],
            },
            {
                "name": "holding_topk_grid",
                "kind": "holding_topk",
                "command": [
                    py,
                    str(SCRIPT_DIR / "evaluate_holding_topk.py"),
                    "--model-id",
                    model_id or "{model_id}",
                    "--feature-table",
                    "fact_feature_panel",
                    "--horizons",
                    "5,10,20,60",
                    "--top-sizes",
                    "20,50,100,200,500",
                ],
            },
        ]
    raise ValueError(f"unknown profile: {profile}")


def _latest_model_id_with_prefix(prefix: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT model_id
              FROM mart_multidim_model
             WHERE model_id LIKE ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (f"{prefix}_%",),
        ).fetchone()
        return str(row["model_id"]) if row else None


def _resolve_command(command: list[str], *, model_id: str | None) -> list[str]:
    return [model_id if part == "{model_id}" and model_id else part for part in command]


def _run_step(step: dict[str, Any], *, model_id: str | None, timeout: int) -> tuple[dict[str, Any], str | None]:
    command = _resolve_command(step["command"], model_id=model_id)
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    duration_s = time.perf_counter() - started
    next_model_id = model_id
    if step.get("kind") == "train" and step.get("model_prefix"):
        next_model_id = _latest_model_id_with_prefix(step["model_prefix"]) or model_id
    return (
        {
            "name": step["name"],
            "kind": step.get("kind"),
            "status": "success" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "duration_s": round(duration_s, 3),
            "command": " ".join(command),
            "output_tail": (result.stdout or "")[-2000:],
        },
        next_model_id,
    )


def run_benchmark(
    *,
    profile: str,
    dry_run: bool = False,
    model_id: str | None = None,
    timeout: int = 1800,
) -> dict[str, Any]:
    run_id = f"benchmark_model_pipeline_{profile}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    started = time.perf_counter()
    plan = benchmark_plan(profile, model_id=model_id)
    steps: list[dict[str, Any]] = []
    current_model_id = model_id

    if dry_run:
        steps = [
            {
                "name": step["name"],
                "kind": step.get("kind"),
                "status": "planned",
                "command": " ".join(_resolve_command(step["command"], model_id=current_model_id or "{model_id}")),
            }
            for step in plan
        ]
        status = "planned"
    else:
        status = "success"
        for step in plan:
            result, current_model_id = _run_step(step, model_id=current_model_id, timeout=timeout)
            steps.append(result)
            if result["status"] != "success":
                status = "failed"
                break

    duration_s = time.perf_counter() - started
    with get_conn() as conn:
        record_pipeline_run(
            conn,
            run_id=run_id,
            pipeline_name="benchmark_model_pipeline",
            status=status,
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=duration_s,
            commit_sha=git_commit_sha(REPO),
            input_tables=["fact_feature_panel", "mart_multidim_model"],
            output_tables=[
                "mart_multidim_model",
                "mart_model_walkforward_fold",
                "mart_model_walkforward_prediction",
                "mart_model_holding_topk_eval",
            ],
            model_id=current_model_id,
            feature_group=profile,
            gate_result=status,
            blockers=[step["name"] for step in steps if step.get("status") == "failed"],
            perf_summary={"profile": profile, "dry_run": dry_run, "steps": steps},
        )
    return {
        "run_id": run_id,
        "profile": profile,
        "status": status,
        "model_id": current_model_id,
        "duration_s": round(duration_s, 3),
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["small", "full"], default="small")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    result = run_benchmark(
        profile=args.profile,
        dry_run=args.dry_run,
        model_id=args.model_id,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"success", "planned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
