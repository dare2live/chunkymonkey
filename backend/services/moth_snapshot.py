from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Any


def _moth_base_command() -> list[str]:
    configured = os.environ.get("CHUNKYMONKEY_MOTH_COMMAND")
    if configured:
        return shlex.split(configured)
    moth_executable = shutil.which("moth")
    if moth_executable:
        return [moth_executable]
    return [sys.executable, "-m", "moth.cli"]


def build_snapshot_command(repo: Path, profile: str) -> list[str]:
    return [
        *_moth_base_command(),
        "snapshot",
        "--repo",
        str(repo),
        "--profile",
        profile,
        "--format",
        "json",
    ]


def run_snapshot(repo: Path, profile: str) -> dict[str, Any]:
    command = build_snapshot_command(repo, profile)
    completed = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    payload: dict[str, Any] | None = None
    if completed.stdout.strip():
        try:
            parsed = json.loads(completed.stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "payload": payload,
        "verdict": (payload or {}).get("status") if payload else "FAIL",
    }


def build_tooling_gate_report(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = snapshot or {}
    dirty_worktree = snapshot.get("dirty_worktree") or []
    codegraph = snapshot.get("codegraph") or {}
    complexity = snapshot.get("complexity") or {}
    verdict = str(snapshot.get("status") or "FAIL")
    issues = list(snapshot.get("issues") or [])
    warnings = list(snapshot.get("warnings") or [])
    return {
        "verdict": verdict,
        "git_status": {
            "clean": not dirty_worktree,
            "total": len(dirty_worktree),
            "counts": {"dirty": len(dirty_worktree)} if dirty_worktree else {},
            "entries": [{"path": path} for path in dirty_worktree],
        },
        "codegraph": codegraph,
        "complexity": complexity,
        "moth": {
            "schema_version": snapshot.get("schema_version"),
            "generated_at": snapshot.get("generated_at"),
            "status": snapshot.get("status"),
            "profile": snapshot.get("profile"),
            "issues": issues,
            "warnings": warnings,
        },
    }
