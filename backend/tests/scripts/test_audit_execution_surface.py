from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_execution_surface.py"
SPEC = importlib.util.spec_from_file_location("audit_execution_surface", SCRIPT_PATH)
audit_execution_surface = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_execution_surface
SPEC.loader.exec_module(audit_execution_surface)


def _write_plist(path: Path, *, label: str, args: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": args,
                "RunAtLoad": False,
            }
        )
    )


def _write_base_repo(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "backend/config").mkdir(parents=True)
    (root / ".moth").mkdir(parents=True)
    (root / "configs/cron").mkdir(parents=True)
    (root / "configs/launchd").mkdir(parents=True)
    (root / "scripts/daily_update.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (root / "scripts/cm_resume.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (root / "backend/config/test_tool_registry.yaml").write_text(
        """
version: 1
updated_at: "2026-06-05"
tools:
  - id: execution_surface_audit_tests
    paths:
      - scripts/daily_update.sh
    owner_module: startup_tooling
    scope: contract
    runner: PYTHONPATH=backend python -m pytest -q backend/tests/scripts/test_audit_execution_surface.py
    status: active
    evidence_level: trusted_with_scope
    truth_source: filesystem
    risk_reason: validates execution surface
    replacement: none
    last_verified: "2026-06-05"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / ".moth/profile.yaml").write_text(
        """
kind: profile
name: chunkymonkey
evidence_paths:
  daily_update: scripts/daily_update.sh
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "configs/cron/crontab.txt").write_text(
        "0 17 * * * cd /tmp/repo && bash scripts/daily_update.sh >> /tmp/out.log 2>&1\n",
        encoding="utf-8",
    )
    _write_plist(
        root / "configs/launchd/com.chunkymonkey.daily-update.plist",
        label="com.chunkymonkey.daily-update",
        args=["/bin/bash", str(root / "scripts/daily_update.sh")],
    )


def test_execution_surface_passes_valid_static_refs(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    (tmp_path / "scripts/session_snapshot.sh").write_text(
        "echo 'run scripts/cm_resume.sh。'\necho \\`scripts/daily_update.sh\\`\n",
        encoding="utf-8",
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "PASS"
    assert report["findings"] == []


def test_execution_surface_fails_missing_launchd_entrypoint(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    _write_plist(
        tmp_path / "configs/launchd/com.chunkymonkey.daily-update.plist",
        label="com.chunkymonkey.daily-update",
        args=["/bin/bash", str(tmp_path / "scripts/missing.sh")],
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "FAIL"
    assert any(f["check"] == "missing_local_entrypoint" and "scripts/missing.sh" in f["message"] for f in report["findings"])


def test_execution_surface_scans_chunkyctl_wrapper_refs(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    (tmp_path / "scripts/chunkyctl").write_text(
        "PYTHONPATH=backend python backend/scripts/missing_command.py\n",
        encoding="utf-8",
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "FAIL"
    assert any(
        f["check"] == "missing_local_entrypoint" and "backend/scripts/missing_command.py" in f["message"]
        for f in report["findings"]
    )


def test_execution_surface_fails_retired_launchd_label(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    _write_plist(
        tmp_path / "configs/launchd/com.chunkymonkey.phase5-monitor.plist",
        label="com.chunkymonkey.phase5-monitor",
        args=["/bin/bash", str(tmp_path / "scripts/daily_update.sh")],
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "FAIL"
    assert any(f["check"] == "retired_launchd_label" for f in report["findings"])


def test_execution_surface_fails_active_registry_missing_path(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    (tmp_path / "backend/config/test_tool_registry.yaml").write_text(
        """
version: 1
updated_at: "2026-06-05"
tools:
  - id: bad_tool
    paths:
      - scripts/missing.sh
    owner_module: startup_tooling
    scope: contract
    runner: missing
    status: active
    evidence_level: trusted_with_scope
    truth_source: filesystem
    risk_reason: catches missing registry path
    replacement: none
    last_verified: "2026-06-05"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "FAIL"
    assert any(f["check"] == "active_registry_missing_path" for f in report["findings"])


def test_execution_surface_fails_moth_missing_evidence_path(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    (tmp_path / ".moth/profile.yaml").write_text(
        """
kind: profile
name: chunkymonkey
evidence_paths:
  missing: scripts/missing.sh
""".strip()
        + "\n",
        encoding="utf-8",
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "FAIL"
    assert any(f["check"] == "moth_missing_evidence_path" for f in report["findings"])


def test_execution_surface_fails_live_retired_launchd_agent(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    launch_agents = tmp_path / "LaunchAgents"
    _write_plist(
        launch_agents / "com.chunkymonkey.gcp-cost-tracker.plist",
        label="com.chunkymonkey.gcp-cost-tracker",
        args=["/bin/bash", str(tmp_path / "scripts/daily_update.sh")],
    )

    report = audit_execution_surface.build_report(
        repo=tmp_path,
        include_live_launchd=True,
        launch_agents_dir=launch_agents,
    )

    assert report["verdict"] == "FAIL"
    assert any(f["check"] == "live_retired_launchd_agent" for f in report["findings"])


def test_execution_surface_fails_retired_execution_tokens(tmp_path: Path) -> None:
    _write_base_repo(tmp_path)
    (tmp_path / "scripts/session_status.sh").write_text(
        "echo 'VM 状态'\necho /tmp/phase5_retrain_mac.log\n",
        encoding="utf-8",
    )

    report = audit_execution_surface.build_report(repo=tmp_path)

    assert report["verdict"] == "FAIL"
    checks = {(f["check"], f["message"]) for f in report["findings"]}
    assert any(check == "retired_execution_token" and "provider_bound_vm_status" in message for check, message in checks)
    assert any(check == "retired_execution_token" and "phase5_chain_state" in message for check, message in checks)
