#!/usr/bin/env python3
"""Manual job entrypoint: persist failures and never fail silently.

The frontend/manual API runs a registered command through this wrapper. A failed
command leaves a flag for ``chunkyctl doctor`` and the next session; a later
successful run clears it.

Notification policy is keyed by typed ``run_outcome`` from
``data/reports/daily_*.json`` (plan §C2) — not by ``rc==1`` heuristics.

Usage:
  <repo>/.venv/bin/python <repo>/scripts/manual_job_wrapper.py <job_name> <command> [args...]

Artifacts:
  /tmp/chunkymonkey_<job>.log
  /tmp/chunkymonkey_ALERT_<job>.flag
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%F %T")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _extract_run_date(cmd: list[str], log: Path) -> str | None:
    """Best-effort YYYYMMDD from --date flag or latest daily_update log banner."""
    for i, tok in enumerate(cmd):
        if tok == "--date" and i + 1 < len(cmd) and re.fullmatch(r"\d{8}", cmd[i + 1]):
            return cmd[i + 1]
        if tok.startswith("--date=") and re.fullmatch(r"\d{8}", tok.split("=", 1)[1]):
            return tok.split("=", 1)[1]
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return None
    matches = re.findall(r"ChunkyMonkey daily update (\d{8})", text)
    if matches:
        return matches[-1]
    return datetime.now().strftime("%Y%m%d")


def _load_run_outcome(job: str, cmd: list[str], log: Path) -> dict | None:
    """Read typed run_outcome from daily report JSON (truth object)."""
    if job != "daily_update":
        return None
    run_date = _extract_run_date(cmd, log)
    if not run_date:
        return None
    report = _repo_root() / "data" / "reports" / f"daily_{run_date}.json"
    if not report.exists():
        # Fallback: newest report (writer-block / clock skew edge).
        reports = sorted((_repo_root() / "data" / "reports").glob("daily_*.json"))
        if not reports:
            return None
        report = reports[-1]
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    outcome = data.get("run_outcome")
    if outcome not in {"success", "soft_waiting_clock", "hard_fail"}:
        return None
    return {
        "run_outcome": outcome,
        "run_outcome_label": data.get("run_outcome_label") or outcome,
        "report_path": str(report),
        "exit_code": data.get("run_outcome_exit_code"),
    }


def _fallback_outcome_from_rc(rc: int) -> str:
    """Only when report missing — preserve hard vs soft shell contract."""
    if rc == 0:
        return "success"
    if rc == 1:
        return "soft_waiting_clock"
    return "hard_fail"


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: manual_job_wrapper.py <job_name> <command...>", file=sys.stderr)
        return 2
    job, cmd = sys.argv[1], sys.argv[2:]
    log = Path(f"/tmp/chunkymonkey_{job}.log")
    flag = Path(f"/tmp/chunkymonkey_ALERT_{job}.flag")
    start = _ts()

    with log.open("a") as fh:
        rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    end = _ts()

    meta = _load_run_outcome(job, cmd, log)
    # Trust report only when it matches this process rc (same run). A stale
    # soft report must not swallow a fresh hard rc (or vice versa).
    if (
        meta is not None
        and meta.get("exit_code") is not None
        and int(meta["exit_code"]) == int(rc)
    ):
        outcome = str(meta["run_outcome"])
        label = str(meta.get("run_outcome_label") or outcome)
    else:
        outcome = _fallback_outcome_from_rc(rc)
        label = outcome
        meta = None  # do not cite mismatched report path in logs

    if rc != 0:
        tail = "".join(log.read_text(errors="replace").splitlines(keepends=True)[-5:])
        flag.write_text(
            f"[{start} -> {end}] run_outcome={outcome} rc={rc} job={job}\n"
            f"cmd: {' '.join(cmd)}\n--- last log lines ---\n{tail}"
        )
        with log.open("a") as fh:
            fh.write(
                f"[{start} -> {end}] run_outcome={outcome} rc={rc} job={job} "
                f"(alert flag: {flag})\n"
            )
        # soft_waiting_clock → never "job FAIL" (store owns observation banner).
        # hard_fail → one FAIL notification.
        if outcome == "soft_waiting_clock":
            with log.open("a") as fh:
                fh.write(
                    f"[{end}] soft_waiting_clock: skip FAIL notification "
                    f"(outcome-keyed; observation banner owns macOS"
                    + (f"; report={meta['report_path']}" if meta else "")
                    + ")\n"
                )
        else:
            title = "ChunkyMonkey job FAIL" if outcome == "hard_fail" else "ChunkyMonkey job"
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    f'display notification "{job} {label} rc={rc} — see {log}" '
                    f'with title "{title}"',
                ],
                capture_output=True,
            )
    else:
        with log.open("a") as fh:
            fh.write(f"[{start} -> {end}] OK job={job} run_outcome={outcome}\n")
        flag.unlink(missing_ok=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
