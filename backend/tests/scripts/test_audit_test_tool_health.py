from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_test_tool_health.py"
SPEC = importlib.util.spec_from_file_location("audit_test_tool_health", SCRIPT_PATH)
audit_test_tool_health = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_test_tool_health
SPEC.loader.exec_module(audit_test_tool_health)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_pytest_ini(root: Path, *, include_slow_gcp: bool = True) -> Path:
    excludes = "not realdb and not perf and not network"
    if include_slow_gcp:
        excludes += " and not gcp and not slow"
    return _write(
        root / "pytest.ini",
        f"""
[pytest]
addopts = --strict-markers -m "{excludes}"
testpaths = backend/tests
markers =
    realdb: opt-in
    perf: opt-in
    network: opt-in
    gcp: opt-in
    slow: opt-in
""",
    )


def _write_registry(root: Path, body: str) -> Path:
    return _write(
        root / "backend/config/test_tool_registry.yaml",
        f"""
version: 1
updated_at: "2026-05-27"
policy:
  default_testpaths:
    - backend/tests
  default_excluded_markers:
    - realdb
    - perf
    - network
    - gcp
    - slow
  coverage_required_prefixes:
    - backend/tests/scripts
  selected_registry_owner: "off"
  unregistered_selected_sample_limit: 20
tools:
{body}
""",
    )


def _patch_repo(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(audit_test_tool_health, "REPO", root)
    monkeypatch.setattr(audit_test_tool_health, "PYTEST_INI", root / "pytest.ini")


def test_audit_passes_for_registered_scope(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    test_path = _write(root / "backend/tests/scripts/test_tool.py", "def test_ok():\n    assert True\n")
    registry = _write_registry(
        root,
        """
  - id: tool_tests
    paths:
      - backend/tests/scripts
    owner_module: test_governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: pytest.ini and registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=[str(test_path)])

    assert report["verdict"] == "PASS"
    assert report["summary"]["selected_files"] == 1


def test_shell_test_scope_is_audited_when_registered(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    shell_path = _write(root / "tests/scripts/test_workflow.sh", "#!/usr/bin/env bash\ntrue\n")
    registry = _write_registry(
        root,
        """
  - id: workflow_shell_tests
    paths:
      - tests/scripts
    owner_module: workflow
    scope: integration
    runner: bash tests/scripts/test_workflow.sh
    status: active
    evidence_level: trusted_with_scope
    truth_source: shell workflow fixture
    risk_reason: root shell tests need explicit runner
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=[str(shell_path)])

    assert report["verdict"] == "PASS"
    assert report["summary"]["selected_files"] == 1


def test_pytest_config_missing_opt_in_excludes_fails(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root, include_slow_gcp=False)
    _write(root / "backend/tests/scripts/test_tool.py", "def test_ok():\n    assert True\n")
    registry = _write_registry(
        root,
        """
  - id: tool_tests
    paths:
      - backend/tests/scripts
    owner_module: test_governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: pytest.ini and registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry)

    assert report["verdict"] == "FAIL"
    messages = [finding["message"] for finding in report["findings"]]
    assert any("gcp" in message for message in messages)
    assert any("slow" in message for message in messages)


def test_dim_active_fixture_without_allowed_context_fails(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    test_path = _write(
        root / "backend/tests/scripts/test_universe_bad.py",
        "def test_bad():\n    assert 'dim_active_a_stock'\n",
    )
    registry = _write_registry(
        root,
        """
  - id: bad_universe_fixture
    paths:
      - backend/tests/scripts/test_universe_bad.py
    owner_module: universe
    scope: unit
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: active universe fixture
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=[str(test_path)])

    assert report["verdict"] == "FAIL"
    assert any(finding["check"] == "universe_fixture_drift" for finding in report["findings"])
    assert report["controller_feedback"]["next_task_slices"]


def test_missing_registry_path_returns_controller_feedback(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    registry = _write_registry(
        root,
        """
  - id: missing_path
    paths:
      - backend/tests/scripts/missing_test.py
    owner_module: governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=["missing_path"])

    assert report["verdict"] == "FAIL"
    assert report["controller_feedback"]["registry_updates"][0]["path"].endswith("missing_test.py")


def test_explicit_missing_scope_fails(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    registry = _write_registry(
        root,
        """
  - id: tool_tests
    paths:
      - backend/tests/scripts
    owner_module: test_governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: pytest.ini and registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(
        config_path=registry,
        scopes=["__definitely_missing_scope_for_audit__"],
    )

    assert report["verdict"] == "FAIL"
    assert report["summary"]["selected_files"] == 0
    assert any(finding["check"] == "empty_scope_selection" for finding in report["findings"])


def test_selected_registry_owner_warns_for_unregistered_selected_tests(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    test_path = _write(root / "backend/tests/test_unregistered_tool.py", "def test_ok():\n    assert True\n")
    _write(root / "backend/tests/scripts/test_registered_tool.py", "def test_ok():\n    assert True\n")
    registry = _write_registry(
        root,
        """
  - id: script_tests
    paths:
      - backend/tests/scripts
    owner_module: governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    text = registry.read_text(encoding="utf-8")
    registry.write_text(text.replace('selected_registry_owner: "off"', 'selected_registry_owner: "warn"'), encoding="utf-8")
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=[str(test_path)])

    assert report["verdict"] == "WARN"
    assert report["summary"]["unregistered_selected_files"] == 1
    assert report["summary"]["registry_coverage_pct"] == 0.0
    assert report["controller_feedback"]["registry_updates"]


def test_unregistered_selected_tests_are_grouped_into_task_slices(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    _write(root / "backend/tests/test_analytics.py", "def test_ok():\n    assert True\n")
    _write(root / "backend/tests/services/paper_sim/test_sim_cache.py", "def test_ok():\n    assert True\n")
    _write(root / "backend/tests/scripts/test_registered_tool.py", "def test_ok():\n    assert True\n")
    registry = _write_registry(
        root,
        """
  - id: script_tests
    paths:
      - backend/tests/scripts
    owner_module: governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    text = registry.read_text(encoding="utf-8")
    registry.write_text(text.replace('selected_registry_owner: "off"', 'selected_registry_owner: "warn"'), encoding="utf-8")
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry)

    slices = report["summary"]["unregistered_selected_slices"]
    assert report["verdict"] == "WARN"
    assert {task_slice["path"] for task_slice in slices} == {
        "backend/tests/<root-files>",
        "backend/tests/services/paper_sim",
    }
    assert any(item["path"] == "backend/tests/services/paper_sim" for item in report["controller_feedback"]["next_task_slices"])


def test_default_gate_non_current_registry_state_fails(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    test_path = _write(root / "backend/tests/test_quarantined.py", "def test_ok():\n    assert True\n")
    registry = _write_registry(
        root,
        """
  - id: quarantined_default_test
    paths:
      - backend/tests/test_quarantined.py
    owner_module: sample
    scope: unit
    runner: pytest
    status: quarantined
    evidence_level: quarantined
    truth_source: registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=[str(test_path)])

    assert report["verdict"] == "FAIL"
    assert any(finding["check"] == "registered_tool_state" for finding in report["findings"])
    assert report["controller_feedback"]["gate_updates"]


def test_marker_registry_drift_fails(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _write_pytest_ini(root)
    test_path = _write(
        root / "backend/tests/scripts/test_realdb_marker.py",
        "import pytest\n\npytestmark = pytest.mark.realdb\n\ndef test_ok():\n    assert True\n",
    )
    registry = _write_registry(
        root,
        """
  - id: script_tests
    paths:
      - backend/tests/scripts/test_realdb_marker.py
    owner_module: governance
    scope: contract
    runner: pytest
    status: active
    evidence_level: trusted_current
    truth_source: registry
    risk_reason: sample
    replacement: unknown
    last_verified: "2026-05-27"
""",
    )
    _patch_repo(monkeypatch, root)

    report = audit_test_tool_health.audit_test_tool_health(config_path=registry, scopes=[str(test_path)])

    assert report["verdict"] == "FAIL"
    assert any(finding["check"] == "marker_registry_drift" for finding in report["findings"])
