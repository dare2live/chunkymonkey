#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {uri}")
    rest = uri[5:]
    bucket, _, name = rest.partition("/")
    if not bucket or not name:
        raise ValueError(f"Invalid gs:// URI: {uri}")
    return bucket, name


def download_gs(uri: str, dest: Path) -> None:
    bucket_name, blob_name = parse_gs_uri(uri)
    dest.parent.mkdir(parents=True, exist_ok=True)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.download_to_filename(str(dest))


def upload_gs(src: Path, uri: str) -> None:
    bucket_name, blob_name = parse_gs_uri(uri)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(str(src))


def upload_results(result_dir: Path, result_uri: str) -> None:
    for path in sorted(result_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(result_dir).as_posix()
            upload_gs(path, f"{result_uri.rstrip('/')}/{rel}")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install_app_data_symlink(local_data_dir: Path) -> None:
    app_data = Path("/app/data")
    if app_data.is_symlink() or app_data.exists():
        if app_data.is_symlink() or app_data.is_file():
            app_data.unlink()
        else:
            shutil.rmtree(app_data)
    app_data.symlink_to(local_data_dir, target_is_directory=True)


def run_command(cmd: list[str], cwd: Path, stdout_path: Path, stderr_path: Path) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = "/app/backend"
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=out, stderr=err, text=True)
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download data, run one experiment, upload results.")
    parser.add_argument("--job-config-uri", required=True)
    parser.add_argument("--workdir-root", default="/tmp/chunkymonkey-batch")
    args = parser.parse_args()

    task_index = os.environ.get("BATCH_TASK_INDEX", "0")
    retry = os.environ.get("BATCH_TASK_RETRY_ATTEMPT", "0")
    workdir = Path(args.workdir_root) / f"task_{task_index}_try_{retry}"
    data_dir = workdir / "data"
    result_dir = workdir / "results"
    logs_dir = result_dir / "logs"
    job_config_path = workdir / "job.json"
    result_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    status = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_config_uri": args.job_config_uri,
        "workdir": str(workdir),
        "status": "running",
    }

    try:
        download_gs(args.job_config_uri, job_config_path)
        cfg = json.loads(job_config_path.read_text(encoding="utf-8"))
        result_uri = cfg["output"]["result_uri"]
        status.update({
            "batch_id": cfg.get("batch_id"),
            "experiment_id": cfg.get("experiment_id"),
            "run_id": cfg.get("run_id"),
            "result_uri": result_uri,
        })
        write_json(result_dir / "job.json", cfg)

        downloads = {
            "smartmoney.duckdb": cfg["data"]["smartmoney_uri"],
            "alpha158.duckdb": cfg["data"]["alpha158_uri"],
            "market.duckdb": cfg["data"]["market_uri"],
        }
        for name, uri in downloads.items():
            target = data_dir / name
            print(f"Downloading {uri} to {target}", flush=True)
            download_gs(uri, target)

        install_app_data_symlink(data_dir)

        runner = cfg.get("runner", "generic_rankic")
        if runner == "generic_rankic":
            cmd = [
                sys.executable,
                "/app/gcp/run_rankic_experiment.py",
                "--job-config",
                str(job_config_path),
                "--workdir",
                str(workdir),
            ]
        elif runner == "existing_lightgbm_v4":
            cmd = [
                sys.executable,
                "/app/backend/scripts/run_p0b_lightgbm_optuna_v4.py",
                "--label",
                cfg["label"],
                "--run-id",
                cfg["run_id"],
                "--n-trials",
                str(cfg["n_trials"]),
                "--start-date",
                cfg["start_date"],
                "--end-date",
                cfg["end_date"],
                "--min-train-months",
                str(cfg["walk_forward"]["min_train_months"]),
                "--feature-panel",
                cfg["feature_panel"],
                "--seed",
                str(cfg["seed"]),
            ]
            if cfg.get("full"):
                cmd.append("--full")
        else:
            raise ValueError(f"Unsupported runner: {runner}")

        write_json(result_dir / "command.json", {"cmd": cmd})
        returncode = run_command(cmd, Path("/app"), logs_dir / "stdout.log", logs_dir / "stderr.log")
        status["returncode"] = returncode
        status["status"] = "success" if returncode == 0 else "failed"
        status["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_json(result_dir / "status.json", status)
        upload_results(result_dir, result_uri)
        return returncode
    except Exception as exc:
        status["status"] = "exception"
        status["error"] = str(exc)
        status["traceback"] = traceback.format_exc()
        status["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_json(result_dir / "status.json", status)
        try:
            cfg = json.loads(job_config_path.read_text(encoding="utf-8"))
            upload_results(result_dir, cfg["output"]["result_uri"])
        except Exception as upload_err:
            # rule-compliance: ok evidence=cloud-batch-best-effort-upload (failure 不阻 status report)
            print(f"upload_results failed: {upload_err}", file=sys.stderr)
        print(status["traceback"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
