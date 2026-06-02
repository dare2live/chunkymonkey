from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "services" / "moth_snapshot.py"
SPEC = importlib.util.spec_from_file_location("moth_snapshot", SCRIPT_PATH)
moth_snapshot = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = moth_snapshot
SPEC.loader.exec_module(moth_snapshot)


def test_build_snapshot_command_targets_moth_cli(monkeypatch) -> None:
    monkeypatch.delenv("CHUNKYMONKEY_MOTH_COMMAND", raising=False)
    monkeypatch.setattr(moth_snapshot.shutil, "which", lambda _name: None)
    command = moth_snapshot.build_snapshot_command(Path("/repo"), "chunkymonkey")

    assert command[:3] == [sys.executable, "-m", "moth.cli"]
    assert command[3:] == ["snapshot", "--repo", "/repo", "--profile", "chunkymonkey", "--format", "json"]


def test_build_snapshot_command_prefers_path_moth(monkeypatch) -> None:
    monkeypatch.delenv("CHUNKYMONKEY_MOTH_COMMAND", raising=False)
    monkeypatch.setattr(moth_snapshot.shutil, "which", lambda _name: "/usr/local/bin/moth")
    command = moth_snapshot.build_snapshot_command(Path("/repo"), "chunkymonkey")

    assert command[:1] == ["/usr/local/bin/moth"]
    assert command[1:] == ["snapshot", "--repo", "/repo", "--profile", "chunkymonkey", "--format", "json"]


def test_build_tooling_gate_report_maps_snapshot_fields() -> None:
    report = moth_snapshot.build_tooling_gate_report(
        {
            "schema_version": 1,
            "generated_at": "2026-06-02T00:00:00Z",
            "status": "WARN",
            "dirty_worktree": ["backend/app.py"],
            "codegraph": {"pending": {"sync_required": True, "added": 3}},
            "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 2}},
            "issues": ["codegraph pending"],
            "warnings": ["complexity new high findings: 2"],
        }
    )

    assert report["verdict"] == "WARN"
    assert report["git_status"]["clean"] is False
    assert report["git_status"]["total"] == 1
    assert report["codegraph"]["pending"]["added"] == 3
    assert report["complexity"]["diff"]["new_high_count"] == 2
    assert report["moth"]["warnings"] == ["complexity new high findings: 2"]
