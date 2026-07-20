"""Every tracked test file must be classified: run by CI, or explicitly excused.

2026-07-20 Fable5 CI-tax fix, binding finding #2: `.github/workflows/ci.yml`'s
hand-maintained pytest list had silently drifted from the real committed test
surface (e.g. `tests/services/test_main_rally_b1.py` was never listed there,
so a real regression there would have gone green on every push).

This test closes that failure mode mechanically: a new `backend/tests/**/
test_*.py` file that is staged/committed without being added to either
`backend/config/ci_pytest_surface.yaml`'s `paths` (run by CI + safe_commit) or
its `ci_test_optional` (excused, with a reason) turns this test red on the
very next commit that touches it. There is no third, silent, unclassified
state anymore.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import run_ci_pytest as runner

REPO = Path(__file__).resolve().parents[3]


def _tracked_test_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "backend/tests"],
        capture_output=True, text=True, check=True, cwd=REPO,
    )
    out = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.endswith(".py"):
            continue
        name = Path(line).name
        if not name.startswith("test_"):
            continue
        # backend/tests/foo/test_bar.py -> tests/foo/test_bar.py
        out.append("tests/" + line.split("backend/tests/", 1)[1])
    return out


def test_every_tracked_test_file_is_classified() -> None:
    surface = runner.load_surface()
    run_paths = set(surface["paths"])
    optional_paths = {e["path"] for e in surface["ci_test_optional"]}
    classified = run_paths | optional_paths

    tracked = _tracked_test_files()
    unclassified = sorted(set(tracked) - classified)
    assert not unclassified, (
        "New/renamed test file(s) not registered in "
        "backend/config/ci_pytest_surface.yaml `paths` or `ci_test_optional`: "
        f"{unclassified}. Add them to `paths` (if offline-CI-safe) or to "
        "`ci_test_optional` with a `reason` (if not)."
    )


def test_classified_entries_point_at_real_tracked_files() -> None:
    """Catch the opposite drift: stale entries for renamed/deleted test files."""
    surface = runner.load_surface()
    tracked = set(_tracked_test_files())
    run_paths = set(surface["paths"])
    optional_paths = {e["path"] for e in surface["ci_test_optional"]}

    stale_run = sorted(run_paths - tracked)
    stale_optional = sorted(optional_paths - tracked)
    assert not stale_run, f"`paths` references non-existent test file(s): {stale_run}"
    assert not stale_optional, (
        f"`ci_test_optional` references non-existent test file(s): {stale_optional}"
    )


def test_red_case_new_untracked_test_file_would_fail() -> None:
    """Representative violation: a brand-new test file must fail this gate
    until it is explicitly classified — proves the guard is not vacuous."""
    surface = runner.load_surface()
    classified = set(surface["paths"]) | {e["path"] for e in surface["ci_test_optional"]}
    assert "tests/services/test_totally_new_unclassified_example.py" not in classified
