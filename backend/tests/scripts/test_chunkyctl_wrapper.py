from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER = REPO_ROOT / "scripts" / "chunkyctl"
FEATURE_MAP_BUILDER = REPO_ROOT / "backend" / "scripts" / "build_feature_map.py"
RETIRED_COMMANDS = ("worktree", "docs", "preflight", "audit", "data-status", "jobs")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(WRAPPER, repo / "scripts" / "chunkyctl")
    _write(
        repo / "backend" / "scripts" / "chunkyctl.py",
        "from __future__ import annotations\n"
        "import json\n"
        "import sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
    )
    return repo


def _run_wrapper(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/chunkyctl", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


@pytest.mark.parametrize("command", RETIRED_COMMANDS)
def test_retired_command_is_rejected_before_backend(command: str, tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)

    result = _run_wrapper(repo, command, "probe")

    assert result.returncode != 0
    assert "retired" in result.stdout.lower()


def test_help_does_not_advertise_retired_commands(tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)

    result = _run_wrapper(repo, "--help")

    assert result.returncode == 0
    for command in RETIRED_COMMANDS:
        assert f"scripts/chunkyctl {command}" not in result.stdout


def test_feature_map_does_not_publish_retired_commands() -> None:
    spec = importlib.util.spec_from_file_location("build_feature_map", FEATURE_MAP_BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    commands = {name for name, _help in module.scan_chunkyctl(REPO_ROOT)}

    assert {"doctor", "map", "pipeline", "lineage"} <= commands
    assert commands.isdisjoint(RETIRED_COMMANDS)
