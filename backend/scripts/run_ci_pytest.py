#!/usr/bin/env python3
"""Run the offline CI pytest surface (single source of truth = ci_pytest_surface.yaml).

2026-07-20 Fable5 CI-tax fix. Binding findings closed by this script:
  1. `scripts/safe_commit.sh` ran zero pytest locally -> stale/broken test
     assertions only exploded on public CI, long after a local "reviewed"
     commit claimed success.
  2. `.github/workflows/ci.yml` hand-maintained its own pytest path list,
     which had silently drifted from the real committed test surface (e.g.
     `tests/services/test_main_rally_b1.py` was never listed there).

2026-07-21 gate redesign #1 (Occam): split the offline surface into
`blocking_paths` (L2/L3 safe_commit + CI) and `nightly_paths` (async).
`--tier blocking|nightly|all` selects which list runs. Default = blocking.
See git log --grep gate_redesign_occams.

This script is the ONLY place that turns `backend/config/ci_pytest_surface.yaml`
into an actual pytest invocation. Both `.github/workflows/ci.yml` and
`scripts/safe_commit.sh` (L2/L3 `ci_pytest` gate) call this script — never a
hand-copied path list — so the two surfaces cannot drift apart again.

Usage:
  PYTHONPATH=backend python backend/scripts/run_ci_pytest.py [--tier blocking|nightly|all] [extra pytest args...]

Env overrides (safe_commit staged-snapshot mode):
  CHUNKYMONKEY_REPO          — repo root for pytest cwd (default: parents of this file)
  CI_PYTEST_SURFACE          — path to ci_pytest_surface.yaml (default: <repo>/backend/config/...)

With no extra pytest args, runs with `-p no:cacheprovider --tb=short -q` (CI default).
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
VALID_TIERS = frozenset({"blocking", "nightly", "all"})
DEFAULT_TIER = "blocking"


class SurfaceError(RuntimeError):
    """ci_pytest_surface.yaml is missing, malformed, or fails an invariant."""


def _require_path_list(raw: dict[str, Any], key: str, *, allow_empty: bool) -> list[str]:
    value = raw.get(key)
    if value is None:
        if allow_empty:
            return []
        raise SurfaceError(f"ci_pytest_surface.yaml missing `{key}`")
    if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
        raise SurfaceError(f"ci_pytest_surface.yaml `{key}` must be a list of non-empty strings")
    if not allow_empty and not value:
        raise SurfaceError(f"ci_pytest_surface.yaml `{key}` must be a non-empty list of strings")
    if len(set(value)) != len(value):
        raise SurfaceError(f"ci_pytest_surface.yaml `{key}` has duplicate entries")
    return value


def load_surface(path: Path = SURFACE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise SurfaceError(f"missing ci_pytest_surface policy: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SurfaceError(f"unreadable ci_pytest_surface policy: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise SurfaceError(f"invalid ci_pytest_surface policy (version != 1): {path}")

    if "paths" in raw and "blocking_paths" not in raw:
        raise SurfaceError(
            "ci_pytest_surface.yaml retired bare `paths`; use `blocking_paths` "
            "(+ optional `nightly_paths`). See gate_redesign_occams_20260721.md"
        )

    blocking = _require_path_list(raw, "blocking_paths", allow_empty=False)
    nightly = _require_path_list(raw, "nightly_paths", allow_empty=True)

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

    blocking_set = set(blocking)
    nightly_set = set(nightly)
    optional_set = set(optional_paths)
    overlap_bn = blocking_set & nightly_set
    if overlap_bn:
        raise SurfaceError(
            f"path(s) in both `blocking_paths` and `nightly_paths`: {sorted(overlap_bn)}"
        )
    overlap_bo = blocking_set & optional_set
    if overlap_bo:
        raise SurfaceError(
            f"path(s) in both `blocking_paths` and `ci_test_optional`: {sorted(overlap_bo)}"
        )
    overlap_no = nightly_set & optional_set
    if overlap_no:
        raise SurfaceError(
            f"path(s) in both `nightly_paths` and `ci_test_optional`: {sorted(overlap_no)}"
        )

    # Compat alias: older callers/tests that still read surface["paths"]
    # see the blocking commit/CI surface only.
    raw["blocking_paths"] = blocking
    raw["nightly_paths"] = nightly
    raw["paths"] = list(blocking)
    return raw


def resolve_tier_paths(surface: dict[str, Any], tier: str) -> list[str]:
    if tier not in VALID_TIERS:
        raise SurfaceError(f"unknown tier {tier!r}; expected one of {sorted(VALID_TIERS)}")
    blocking = list(surface["blocking_paths"])
    nightly = list(surface.get("nightly_paths") or [])
    if tier == "blocking":
        return blocking
    if tier == "nightly":
        return nightly
    return [*blocking, *nightly]


def build_pytest_cmd(
    surface: dict[str, Any],
    extra_args: list[str],
    *,
    tier: str = DEFAULT_TIER,
) -> list[str]:
    args = extra_args if extra_args else list(DEFAULT_PYTEST_ARGS)
    paths = resolve_tier_paths(surface, tier)
    if not paths:
        raise SurfaceError(f"tier {tier!r} resolved to an empty path list")
    return [sys.executable, "-m", "pytest", *paths, *args]


def parse_argv(argv: list[str]) -> tuple[str, list[str]]:
    """Extract `--tier X` from argv; remaining args pass through to pytest."""
    tier = DEFAULT_TIER
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--tier":
            if i + 1 >= len(argv):
                raise SurfaceError("--tier requires blocking|nightly|all")
            tier = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--tier="):
            tier = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    if tier not in VALID_TIERS:
        raise SurfaceError(f"unknown tier {tier!r}; expected one of {sorted(VALID_TIERS)}")
    return tier, rest


def main(argv: list[str]) -> int:
    try:
        tier, pytest_args = parse_argv(argv)
        surface = load_surface(SURFACE_PATH)
        paths = resolve_tier_paths(surface, tier)
        if not paths:
            print(f"[ci-pytest] tier={tier} paths=0 (nothing scheduled)", flush=True)
            return 0
        cmd = build_pytest_cmd(surface, pytest_args, tier=tier)
    except SurfaceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[ci-pytest] tier={tier} paths={len(paths)}", flush=True)
    proc = subprocess.run(cmd, cwd=BACKEND)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
