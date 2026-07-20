#!/usr/bin/env python3
"""Run the offline CI pytest surface (single source of truth = ci_pytest_surface.yaml).

2026-07-20 Fable5 CI-tax fix. Binding findings closed by this script:
  1. `scripts/safe_commit.sh` ran zero pytest locally -> stale/broken test
     assertions only exploded on public CI, long after a local "reviewed"
     commit claimed success.
  2. `.github/workflows/ci.yml` hand-maintained its own pytest path list,
     which had silently drifted from the real committed test surface (e.g.
     `tests/services/test_main_rally_b1.py` was never listed there).

This script is the ONLY place that turns `backend/config/ci_pytest_surface.yaml`
into an actual pytest invocation. Both `.github/workflows/ci.yml` and
`scripts/safe_commit.sh` (L2/L3 `ci_pytest` gate) call this script — never a
hand-copied path list — so the two surfaces cannot drift apart again.

Usage:
  PYTHONPATH=backend python backend/scripts/run_ci_pytest.py [extra pytest args...]

Env overrides (safe_commit staged-snapshot mode):
  CHUNKYMONKEY_REPO          — repo root for pytest cwd (default: parents of this file)
  CI_PYTEST_SURFACE          — path to ci_pytest_surface.yaml (default: <repo>/backend/config/...)

With no extra args, runs with `-p no:cacheprovider --tb=short -q` (CI default).
Callers that want a faster/quieter local run (e.g. safe_commit) pass their own
`--tb=line` etc.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_REPO = Path(__file__).resolve().parents[2]
REPO = Path(os.environ.get("CHUNKYMONKEY_REPO", str(_DEFAULT_REPO))).resolve()
BACKEND = REPO / "backend"
SURFACE_PATH = Path(
    os.environ.get(
        "CI_PYTEST_SURFACE",
        str(REPO / "backend" / "config" / "ci_pytest_surface.yaml"),
    )
).resolve()

DEFAULT_PYTEST_ARGS = ("-p", "no:cacheprovider", "--tb=short", "-q")


class SurfaceError(RuntimeError):
    """ci_pytest_surface.yaml is missing, malformed, or fails an invariant."""


def load_surface(path: Path = SURFACE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise SurfaceError(f"missing ci_pytest_surface policy: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SurfaceError(f"unreadable ci_pytest_surface policy: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise SurfaceError(f"invalid ci_pytest_surface policy (version != 1): {path}")

    paths = raw.get("paths")
    if not isinstance(paths, list) or not paths or not all(
        isinstance(p, str) and p for p in paths
    ):
        raise SurfaceError("ci_pytest_surface.yaml `paths` must be a non-empty list of strings")
    if len(set(paths)) != len(paths):
        raise SurfaceError("ci_pytest_surface.yaml `paths` has duplicate entries")

    optional = raw.get("ci_test_optional")
    if not isinstance(optional, list):
        raise SurfaceError("ci_pytest_surface.yaml `ci_test_optional` must be a list")
    optional_paths: list[str] = []
    for entry in optional:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not entry.get("path")
            or not isinstance(entry.get("reason"), str)
            or not entry.get("reason")
        ):
            raise SurfaceError(f"ci_test_optional entry missing str path/reason: {entry!r}")
        optional_paths.append(entry["path"])
    if len(set(optional_paths)) != len(optional_paths):
        raise SurfaceError("ci_pytest_surface.yaml `ci_test_optional` has duplicate paths")

    overlap = set(paths) & set(optional_paths)
    if overlap:
        raise SurfaceError(f"path(s) in both `paths` and `ci_test_optional`: {sorted(overlap)}")

    return raw


def build_pytest_cmd(surface: dict[str, Any], extra_args: list[str]) -> list[str]:
    args = extra_args if extra_args else list(DEFAULT_PYTEST_ARGS)
    return [sys.executable, "-m", "pytest", *surface["paths"], *args]


def main(argv: list[str]) -> int:
    try:
        surface = load_surface(SURFACE_PATH)
    except SurfaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    cmd = build_pytest_cmd(surface, argv)
    proc = subprocess.run(cmd, cwd=BACKEND)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
