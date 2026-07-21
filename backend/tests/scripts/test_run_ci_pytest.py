"""Unit tests for the CI pytest surface runner (2026-07-20 Fable5 CI-tax fix;
2026-07-21 blocking/nightly tiers).

Only exercises `load_surface`/`build_pytest_cmd`/`resolve_tier_paths` against
fixture policies — does NOT spawn a real pytest subprocess (that would
recursively run the whole suite from inside itself).
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


def _minimal_tiered(**overrides: object) -> dict:
    doc: dict = {
        "version": 1,
        "blocking_paths": ["tests/test_utils.py"],
        "nightly_paths": ["tests/services/test_main_rally_b0.py"],
        "ci_test_optional": [
            {"path": "tests/test_db.py", "reason": "fixture optional"},
        ],
    }
    doc.update(overrides)
    return doc


def test_live_surface_loads_and_validates() -> None:
    surface = runner.load_surface()
    assert surface["version"] == 1
    assert "tests/test_utils.py" in surface["blocking_paths"]
    assert surface["paths"] == surface["blocking_paths"]  # compat alias
    for entry in surface["ci_test_optional"]:
        assert entry["path"] and entry["reason"]


def test_live_surface_no_overlap() -> None:
    surface = runner.load_surface()
    blocking = set(surface["blocking_paths"])
    nightly = set(surface["nightly_paths"])
    optional_paths = {e["path"] for e in surface["ci_test_optional"]}
    assert not (blocking & nightly)
    assert not (blocking & optional_paths)
    assert not (nightly & optional_paths)


def test_live_blocking_promotes_tier12_and_demotes_strategy() -> None:
    """Gate redesign #1: PIT publish contracts block; strategy-paused → nightly."""
    surface = runner.load_surface()
    blocking = set(surface["blocking_paths"])
    nightly = set(surface["nightly_paths"])
    for path in (
        "tests/services/test_tier12_publish_contract.py",
        "tests/services/test_tier12_publish_accept.py",
        "tests/services/test_tier12_publish_writer.py",
        "tests/services/test_tier12_publish_scope.py",
        "tests/services/test_tier12_project_universe.py",
    ):
        assert path in blocking, f"tier12 contract must be blocking: {path}"
        assert path not in nightly
    for path in (
        "tests/services/test_main_rally_b0.py",
        "tests/services/test_main_rally_b1.py",
        "tests/services/test_main_rally_b2.py",
        "tests/services/test_institution_follow_b0.py",
        "tests/services/test_institution_follow_b1.py",
        "tests/services/test_institution_follow_b2.py",
        "tests/services/test_institution_follow_b4.py",
    ):
        assert path in nightly, f"strategy-paused must be nightly: {path}"
        assert path not in blocking


def test_resolve_tier_paths_blocking_nightly_all() -> None:
    surface = _minimal_tiered()
    assert runner.resolve_tier_paths(surface, "blocking") == ["tests/test_utils.py"]
    assert runner.resolve_tier_paths(surface, "nightly") == [
        "tests/services/test_main_rally_b0.py"
    ]
    assert runner.resolve_tier_paths(surface, "all") == [
        "tests/test_utils.py",
        "tests/services/test_main_rally_b0.py",
    ]


def test_resolve_tier_paths_rejects_unknown() -> None:
    with pytest.raises(runner.SurfaceError, match="unknown tier"):
        runner.resolve_tier_paths(_minimal_tiered(), "live")


def test_build_pytest_cmd_default_args() -> None:
    surface = _minimal_tiered()
    cmd = runner.build_pytest_cmd(surface, [], tier="blocking")
    assert cmd[1:3] == ["-m", "pytest"]
    assert "tests/test_utils.py" in cmd
    assert "tests/services/test_main_rally_b0.py" not in cmd
    assert "--tb=short" in cmd


def test_build_pytest_cmd_nightly_tier() -> None:
    surface = _minimal_tiered()
    cmd = runner.build_pytest_cmd(surface, ["--tb=line"], tier="nightly")
    assert "tests/services/test_main_rally_b0.py" in cmd
    assert "tests/test_utils.py" not in cmd


def test_build_pytest_cmd_extra_args_override_default() -> None:
    surface = _minimal_tiered()
    cmd = runner.build_pytest_cmd(surface, ["--tb=line", "-q", "-x"], tier="blocking")
    assert "--tb=short" not in cmd
    assert "--tb=line" in cmd
    assert "-x" in cmd


def test_parse_argv_extracts_tier() -> None:
    tier, rest = runner.parse_argv(["--tier", "nightly", "--tb=line", "-q"])
    assert tier == "nightly"
    assert rest == ["--tb=line", "-q"]
    tier, rest = runner.parse_argv(["--tb=short"])
    assert tier == "blocking"
    assert rest == ["--tb=short"]


def test_missing_policy_raises(tmp_path: Path) -> None:
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(tmp_path / "nope.yaml")


def test_empty_blocking_paths_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {"version": 1, "blocking_paths": [], "nightly_paths": [], "ci_test_optional": []},
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)


def test_legacy_paths_key_rejected(tmp_path: Path) -> None:
    """`paths` alone is retired — must use blocking_paths (+ optional nightly)."""
    path = _write_policy(
        tmp_path,
        {"version": 1, "paths": ["tests/test_utils.py"], "ci_test_optional": []},
    )
    with pytest.raises(runner.SurfaceError, match="blocking_paths"):
        runner.load_surface(path)


def test_duplicate_blocking_paths_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "blocking_paths": ["tests/test_utils.py", "tests/test_utils.py"],
            "nightly_paths": [],
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
            "blocking_paths": ["tests/test_utils.py"],
            "nightly_paths": [],
            "ci_test_optional": [{"path": "tests/test_db.py"}],
        },
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)


def test_path_in_blocking_and_optional_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "blocking_paths": ["tests/test_utils.py"],
            "nightly_paths": [],
            "ci_test_optional": [{"path": "tests/test_utils.py", "reason": "x"}],
        },
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)


def test_path_in_blocking_and_nightly_rejected(tmp_path: Path) -> None:
    path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "blocking_paths": ["tests/test_utils.py"],
            "nightly_paths": ["tests/test_utils.py"],
            "ci_test_optional": [],
        },
    )
    with pytest.raises(runner.SurfaceError):
        runner.load_surface(path)
