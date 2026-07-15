#!/usr/bin/env python3
"""Manual job entrypoint: persist failures and never fail silently.

The frontend/manual API runs a registered command through this wrapper. A failed
command leaves a flag for ``chunkyctl doctor`` and the next session; a later
successful run clears it.

Usage:
  <repo>/.venv/bin/python <repo>/scripts/manual_job_wrapper.py <job_name> <command> [args...]

Artifacts:
  /tmp/chunkymonkey_<job>.log
  /tmp/chunkymonkey_ALERT_<job>.flag
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%F %T")


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

    if rc != 0:
        tail = "".join(log.read_text(errors="replace").splitlines(keepends=True)[-5:])
        flag.write_text(
            f"[{start} -> {end}] FAIL rc={rc} job={job}\n"
            f"cmd: {' '.join(cmd)}\n--- last log lines ---\n{tail}"
        )
        with log.open("a") as fh:
            fh.write(f"[{start} -> {end}] FAIL rc={rc} job={job} (alert flag: {flag})\n")
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'display notification "{job} failed rc={rc} — see {log}" '
                f'with title "ChunkyMonkey job FAIL"',
            ],
            capture_output=True,
        )
    else:
        with log.open("a") as fh:
            fh.write(f"[{start} -> {end}] OK job={job}\n")
        flag.unlink(missing_ok=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
