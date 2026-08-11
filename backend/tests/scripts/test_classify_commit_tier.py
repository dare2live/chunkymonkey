"""TDD for commit-tier classifier (WP1). Fail-closed; no agent self-downgrade."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import classify_commit_tier as clas


REPO = Path(__file__).resolve().parents[3]
POLICY = REPO / "backend" / "config" / "commit_tiers.yaml"


def test_live_policy_validates() -> None:
    policy = clas.load_policy(POLICY)
    clas.validate_policy(policy)
    assert policy["tier_gates"]["L3"] == "all"
    assert "rule10" in policy["tier_gates"]["L2"]
    assert "doc_governance" in policy["tier_gates"]["L1"]


def test_l1_docs_only() -> None:
    result = clas.classify(
        ["goal.md", "docs/README.md", "analysis/project_state_ledger.md"],
        scan_content=False,
    )
    assert result["tier"] == "L1"
    assert "rule10" not in result["gates"]
    assert "doc_governance" in result["gates"]
    assert "grain_uniqueness" not in result["gates"]
    assert "continuity" not in result["gates"]


def test_l2_tests_and_routers() -> None:
    result = clas.classify(
        ["backend/tests/test_utils.py", "backend/routers/ops.py", "backend/main.py"],
        scan_content=False,
    )
    assert result["tier"] == "L2"
    assert "rule10" in result["gates"]
    assert "ci_pytest" in result["gates"]
    assert "grain_uniqueness" not in result["gates"]
    assert "continuity" not in result["gates"]
    assert "population_contract" in result["gates"]


def test_l3_services_and_config() -> None:
    result = clas.classify(
        ["backend/services/calendar.py", "backend/config/sync_registry.yaml"],
        scan_content=False,
    )
    assert result["tier"] == "L3"
    assert result["gates"] == list(clas.ALL_GATES_ORDERED)


def test_mixed_docs_and_service_is_l3() -> None:
    result = clas.classify(
        ["goal.md", "backend/services/research_runtime.py"],
        scan_content=False,
    )
    assert result["tier"] == "L3"


def test_pit_file_in_docs_commit_escalates() -> None:
    """Bad case: PIT/writer surface mixed into a docs-looking set → L3."""
    result = clas.classify(
        ["docs/README.md", "backend/services/data_sources/nominal_ohlcv_acceptance.py"],
        scan_content=False,
    )
    assert result["tier"] == "L3"


def test_unknown_path_fail_closed_l3() -> None:
    result = clas.classify(["weird/orphan.bin"], scan_content=False)
    assert result["tier"] == "L3"


def test_retired_board_artifact_is_no_longer_special_cased() -> None:
    """BOARD.md / agent_context.json 于 2026-08-11 P2.3 退役 (board 改现查、零文件)。

    政策里不该再为它们留 L1 特例 —— 留着就是给一个已不存在的产物开后门。
    """
    policy = clas.load_policy(POLICY)
    assert "BOARD.md" not in policy["l1_files"]
    assert "agent_board" not in policy["tier_gates"]["L1"]
    assert "agent_board" not in policy["tier_gates"]["L2"]
    assert "agent_board" not in clas.ALL_GATES_ORDERED

def test_feature_map_alone_is_l3() -> None:
    """FEATURE_MAP is generated; hand edits must not take the L1 light path."""
    result = clas.classify(["FEATURE_MAP.md"], scan_content=False)
    assert result["tier"] == "L3"


def test_deletion_forces_l3() -> None:
    policy = clas.load_policy(POLICY)
    clas.validate_policy(policy)
    result = clas.classify_paths([("D", "docs/README.md")], policy, scan_content=False)
    assert result["tier"] == "L3"
    assert any(r.startswith("status_D:") for r in result["reasons"])


def test_rename_forces_l3() -> None:
    policy = clas.load_policy(POLICY)
    result = clas.classify_paths(
        [("R", "goal.md")], policy, scan_content=False,
    )
    assert result["tier"] == "L3"


def test_missing_policy_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    result = clas.classify(["goal.md"], policy_path=missing, scan_content=False)
    assert result["tier"] == "L3"
    assert any(r.startswith("policy_error:") for r in result["reasons"])
    assert result["gates"] == list(clas.ALL_GATES_ORDERED)


def test_invalid_policy_l3_missing_all_fail_closed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.dump({
            "version": 1,
            "l1_prefixes": ["docs/"],
            "l1_files": [],
            "l2_prefixes": [],
            "l2_files": [],
            "content_scan_exempt_prefixes": [],
            "tier_gates": {
                "L1": ["doc_drift", "doc_governance"],
                "L2": ["rule10"],
                "L3": ["doc_drift"],  # must be 'all'
            },
        }),
        encoding="utf-8",
    )
    result = clas.classify(["goal.md"], policy_path=bad, scan_content=False)
    assert result["tier"] == "L3"
    assert "policy_error:" in result["reasons"][0]


def test_cli_json_shape() -> None:
    import os
    import subprocess

    env = {**os.environ, "PYTHONPATH": str(REPO / "backend")}
    py = REPO / ".venv" / "bin" / "python3"
    if not py.exists():
        py = Path(os.environ.get("PYTHON", "python3"))
    r = subprocess.run(
        [
            str(py),
            str(REPO / "backend" / "scripts" / "classify_commit_tier.py"),
            "--paths", "goal.md", "--no-content-scan",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["tier"] == "L1"
    assert "gates" in payload and "reasons" in payload


def test_l1_gates_subset_of_known() -> None:
    policy = clas.load_policy(POLICY)
    for name in policy["tier_gates"]["L1"]:
        assert name in clas.KNOWN_GATES
    for name in policy["tier_gates"]["L2"]:
        assert name in clas.KNOWN_GATES


def test_no_downgrade_env_knob() -> None:
    """Classifier source must not honor any FORCE_TIER / DOWNGRADE env."""
    src = (REPO / "backend" / "scripts" / "classify_commit_tier.py").read_text(encoding="utf-8")
    assert "FORCE_TIER" not in src
    assert "DOWNGRADE" not in src
    assert "os.environ" not in src
