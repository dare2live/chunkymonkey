"""Unit tests for the CI pytest surface runner (2026-07-20 Fable5 CI-tax fix).

Only exercises `load_surface`/`build_pytest_cmd` against fixture policies —
does NOT spawn a real pytest subprocess (that would recursively run the whole
suite from inside itself).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import run_ci_pytest as runner


def _write_policy(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "ci_pytest_surface.yaml"
    path.write_text(yaml.dump(doc), encoding="utf-8")
    return path


def test_live_surface_loads_and_validates() -> None:
    surface = runner.load_surface()
    assert surface["version"] == 1
    assert "tests/test_utils.py" in surface["paths"]
    for entry in surface["ci_test_optional"]:
        assert entry["path"] and entry["reason"]


def test_live_surface_no_overlap() -> None:
    surface = runner.load_surface()
    paths = set(surface["paths"])
    optional_paths = {e["path"] for e in surface["ci_test_optional"]}
    assert not (paths & optional_paths)


def test_build_pytest_cmd_default_args() -> None:
    surface = {"paths": ["tests/test_utils.py", "tests/test_db.py"]}
    cmd = runner.build_pytest_cmd(surface, [])
    assert cmd[1:3] == ["-m", "pytest"]
    assert "tests/test_utils.py" in cmd
    assert "tests/test_db.py" in cmd
    assert "--tb=short" in cmd


def test_build_pytest_cmd_extra_args_override_default() -> None:
    surface = {"paths": ["tests/test_utils.py"]}
    cmd = runner.build_pytest_cmd(surface, ["--tb=line", "-q", "-x"])
    assert "--tb=short" not in cmd
    assert "--tb=line" in cmd
    assert "-x" in cmd


def test_missing_policy_raises(tmp_path: Path) -> None:
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(tmp_path / "nope.yaml")


def test_empty_paths_rejected(tmp_path: Path) -> None:
    path = _write_policy(tmp_path, {"version": 1, "paths": [], "ci_test_optional": []})
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)


def test_duplicate_paths_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "paths": ["tests/test_utils.py", "tests/test_utils.py"],
            "ci_test_optional": [],
        },
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)


def test_optional_entry_missing_reason_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "paths": ["tests/test_utils.py"],
            "ci_test_optional": [{"path": "tests/test_db.py"}],
        },
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)


def test_path_in_both_lists_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "paths": ["tests/test_utils.py"],
            "ci_test_optional": [{"path": "tests/test_utils.py", "reason": "x"}],
        },
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)
