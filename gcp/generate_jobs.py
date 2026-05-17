#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def label_for_horizon(horizon: str) -> str:
    return f"fwd_cost_after_{horizon}"


def powerset_feature_sets(groups: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    full_mask = (1 << len(groups)) - 1
    ordered_masks = [full_mask] + [mask for mask in range(0, 1 << len(groups)) if mask != full_mask]
    for mask in ordered_masks:
        include = [g for i, g in enumerate(groups) if mask & (1 << i)]
        drop = [g for g in groups if g not in include]
        name_bits = "".join("1" if g in include else "0" for g in groups)
        out.append({"name": f"fs_{name_bits}", "include_groups": include, "drop_groups": drop})
    return out


def filtered_axis(values: list[Any], include_values: list[Any] | None) -> list[Any]:
    if not include_values:
        return values
    return [v for v in values if v in include_values]


def filtered_walk_forward(values: list[dict[str, Any]], include_values: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not include_values:
        return values
    include_keys = {(int(v["min_train_months"]), int(v["forward_months"])) for v in include_values}
    return [v for v in values if (int(v["min_train_months"]), int(v["forward_months"])) in include_keys]


def stable_id(parts: list[Any], length: int = 12) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def gs_join(*parts: str) -> str:
    first = parts[0].rstrip("/")
    rest = [p.strip("/") for p in parts[1:]]
    return "/".join([first, *rest])


def build_batch_json(cfg: dict[str, Any], job_id: str, job_config_uri: str) -> dict[str, Any]:
    project = cfg["project"]
    batch = cfg["batch"]
    runnable = {
        "container": {
            "imageUri": project["docker_image"],
        },
        "environment": {
            "variables": {
                "JOB_CONFIG_URI": job_config_uri,
            }
        },
    }
    job = {
        "taskGroups": [
            {
                "taskSpec": {
                    "runnables": [runnable],
                    "computeResource": {
                        "cpuMilli": int(batch["cpu_milli"]),
                        "memoryMib": int(batch["memory_mib"]),
                    },
                    "maxRetryCount": int(batch["max_retry_count"]),
                    "maxRunDuration": str(batch["max_run_duration"]),
                },
                "taskCount": 1,
                "parallelism": 1,
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": batch["machine_type"],
                        "provisioningModel": batch["provisioning_model"],
                        "bootDisk": {
                            "sizeGb": int(batch["boot_disk_gb"]),
                        },
                    }
                }
            ],
            "serviceAccount": {
                "email": project["service_account"],
            },
        },
        "logsPolicy": {
            "destination": batch["logs_policy"],
        },
    }
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GCP Batch experiment jobs.")
    parser.add_argument("--config", default="gcp/experiment_config.yaml")
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--shuffle", action="store_true")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = load_yaml(cfg_path)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_id = args.batch_id or f"batch_{now}"
    out_dir = Path(args.out_dir or f"gcp/jobs/{batch_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    axes = cfg["axes"]
    selection = cfg.get("selection", {})
    feature_axis = powerset_feature_sets(axes["feature_ablation"]["groups"])
    models = filtered_axis(axes["models"], selection.get("include_models"))
    horizons = filtered_axis(axes["horizons"], selection.get("include_horizons"))
    sizers = filtered_axis(axes["sizers"], selection.get("include_sizers"))
    universes = filtered_axis(axes["universes"], selection.get("include_universes"))
    seeds = filtered_axis(axes["seeds"], selection.get("include_seeds"))
    walk_forward = filtered_walk_forward(axes["walk_forward"], selection.get("include_walk_forward"))

    combos = list(itertools.product(feature_axis, models, horizons, sizers, universes, seeds, walk_forward))
    if args.shuffle or selection.get("shuffle"):
        random.Random(42).shuffle(combos)

    max_jobs = args.max_jobs if args.max_jobs is not None else int(selection.get("max_jobs", len(combos)))
    hard_max = int(cfg["batch"].get("max_submit_jobs", max_jobs))
    if max_jobs > hard_max:
        raise SystemExit(f"Refusing to generate {max_jobs} jobs because batch.max_submit_jobs is {hard_max}")

    if args.offset < 0:
        raise SystemExit("--offset must be >= 0")
    combos = combos[args.offset: args.offset + max_jobs]
    project = cfg["project"]
    data = cfg["data"]
    exp = cfg["experiment"]
    output = cfg["output"]

    manifest_path = out_dir / "manifest.jsonl"
    submit_path = out_dir / "submit_jobs.txt"
    with manifest_path.open("w", encoding="utf-8") as manifest, submit_path.open("w", encoding="utf-8") as submit:
        for idx, (feature_set, model, horizon, sizer, universe, seed, wf) in enumerate(combos, start=1):
            exp_hash = stable_id([feature_set, model, horizon, sizer, universe, seed, wf])
            experiment_id = f"exp_{idx:06d}_{exp_hash}"
            run_id = f"gcp_{batch_id}_{experiment_id}_{model}_{horizon}_s{seed}"
            snapshot_prefix = data["gcs_snapshot_prefix"].rstrip("/")
            result_uri = gs_join(output["gcs_results_prefix"], batch_id, experiment_id)
            job_config_uri = gs_join(f"gs://{project['bucket']}", project["prefix"], "jobs", batch_id, f"{experiment_id}.json")
            job_id = f"cm-{batch_id.lower().replace('_', '-')}-{idx:06d}"
            job_id = job_id[:63].rstrip("-")

            job_config = {
                "batch_id": batch_id,
                "experiment_id": experiment_id,
                "run_id": run_id,
                "runner": exp["runner"],
                "model": model,
                "horizon": horizon,
                "label": label_for_horizon(horizon),
                "sizer": sizer,
                "universe": universe,
                "seed": int(seed),
                "walk_forward": {
                    "min_train_months": int(wf["min_train_months"]),
                    "forward_months": int(wf["forward_months"]),
                },
                "feature_set": feature_set,
                "feature_groups": cfg["feature_groups"],
                "feature_panel": exp["feature_panel"],
                "feature_version": exp["feature_version"],
                "label_version": exp["label_version"],
                "start_date": exp["start_date"],
                "end_date": exp["end_date"],
                "n_trials": int(exp["n_trials"]),
                "full": bool(exp["full"]),
                "top_k": int(exp["top_k"]),
                "objective": exp["objective"],
                "pit": cfg["pit"],
                "data": {
                    "snapshot_id": data["snapshot_id"],
                    "smartmoney_uri": gs_join(snapshot_prefix, data["smartmoney_file"]),
                    "alpha158_uri": gs_join(snapshot_prefix, data["alpha158_file"]),
                    "market_uri": gs_join(snapshot_prefix, data["market_file"]),
                },
                "output": {
                    "result_uri": result_uri,
                    "trials_table": output["trials_table"],
                },
            }

            local_job_config = out_dir / f"{experiment_id}.json"
            local_batch_json = out_dir / f"{job_id}.batch.json"
            local_job_config.write_text(json.dumps(job_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            local_batch_json.write_text(json.dumps(build_batch_json(cfg, job_id, job_config_uri), indent=2) + "\n", encoding="utf-8")
            manifest.write(json.dumps({"job_id": job_id, "experiment_id": experiment_id, "run_id": run_id, "job_config_uri": job_config_uri, "result_uri": result_uri}, ensure_ascii=False) + "\n")
            submit.write(f"{job_id} {local_batch_json}\n")

    print(f"Generated {len(combos)} jobs in {out_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Submit list: {submit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
