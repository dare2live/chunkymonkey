from __future__ import annotations

import json
import importlib.util
import sys
from argparse import Namespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "chunkyctl.py"
SPEC = importlib.util.spec_from_file_location("chunkyctl", SCRIPT_PATH)
chunkyctl = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = chunkyctl
SPEC.loader.exec_module(chunkyctl)


def test_preflight_reports_dirty_pending_and_required_gates(tmp_path: Path) -> None:
    report = chunkyctl.build_preflight_report(
        repo=tmp_path,
        task="拆 updater status glue",
        scopes=["backend/routers/updater.py", "backend/tests/test_updater_status.py"],
        tooling_gate={
            "git_status": {"clean": False},
            "codegraph": {"pending": {"sync_required": True, "added": 1}},
        },
    )

    assert report["verdict"] == "FAIL"
    assert {risk["risk"] for risk in report["risks"]} == {"dirty_worktree", "codegraph_pending"}
    gate_names = {gate["gate"] for gate in report["required_gates"]}
    assert "codegraph_context" in gate_names
    assert "test_tool_validity" in gate_names
    assert "complexity" in gate_names
    assert report["truth_sources"][0] == "K-line is trading truth"
    test_tool_gate = next(gate for gate in report["required_gates"] if gate["gate"] == "test_tool_validity")
    assert "--scope backend/routers/updater.py --scope backend/tests/test_updater_status.py" in test_tool_gate["command"]


def test_preflight_accepts_positional_task_and_scope_args() -> None:
    task, scopes = chunkyctl._resolve_preflight_task_and_scopes(
        Namespace(
            task=None,
            task_arg="clean dirty worktree",
            scope=[],
            scope_arg=["goal.md", "docs/engineering_governance.md"],
        )
    )

    assert task == "clean dirty worktree"
    assert scopes == ["goal.md", "docs/engineering_governance.md"]


def test_preflight_keeps_flag_task_compatible() -> None:
    task, scopes = chunkyctl._resolve_preflight_task_and_scopes(
        Namespace(
            task="flag task",
            task_arg=None,
            scope=["backend/scripts/chunkyctl.py"],
            scope_arg=["backend/tests/scripts/test_chunkyctl.py"],
        )
    )

    assert task == "flag task"
    assert scopes == ["backend/scripts/chunkyctl.py", "backend/tests/scripts/test_chunkyctl.py"]


def test_preflight_marks_strategy_or_cloud_tasks_as_blocked(tmp_path: Path) -> None:
    report = chunkyctl.build_preflight_report(
        repo=tmp_path,
        task="run GCP Optuna backtest",
        scopes=[],
        tooling_gate={
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False, "added": 0}},
        },
    )

    assert report["verdict"] == "FAIL"
    assert report["risks"] == [
        {
            "severity": "FAIL",
            "risk": "strategy_or_cloud_gate",
            "detail": "require explicit preflight gates before expensive or strategy work",
        }
    ]


def test_preflight_does_not_match_ui_inside_build(tmp_path: Path) -> None:
    report = chunkyctl.build_preflight_report(
        repo=tmp_path,
        task="build architecture inventory parser scanner",
        scopes=[],
        tooling_gate={
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False, "added": 0}},
        },
    )

    assert report["verdict"] == "PASS"
    assert report["risks"] == []


def test_preflight_matches_ui_as_own_token(tmp_path: Path) -> None:
    report = chunkyctl.build_preflight_report(
        repo=tmp_path,
        task="frontend ui contract review",
        scopes=[],
        tooling_gate={
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False, "added": 0}},
        },
    )

    assert report["verdict"] == "WARN"
    assert report["risks"] == [
        {
            "severity": "WARN",
            "risk": "frontend_contract",
            "detail": "backend contract and Browser verification required",
        }
    ]


def test_run_preflight_uses_moth_snapshot(monkeypatch, tmp_path: Path, capsys) -> None:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-06-02T00:00:00Z",
        "status": "WARN",
        "profile": {"name": "chunkymonkey"},
        "dirty_worktree": ["backend/app.py"],
        "codegraph": {"pending": {"sync_required": True, "added": 1}},
        "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}},
        "issues": [],
        "warnings": [],
    }

    def fake_moth_snapshot(repo, profile):
        assert profile == "chunkymonkey"
        return {
            "command": ["/usr/local/bin/moth", "snapshot", "--repo", str(repo), "--profile", profile, "--format", "json"],
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
            "payload": payload,
            "verdict": "WARN",
        }

    monkeypatch.setattr(chunkyctl, "run_moth_snapshot", fake_moth_snapshot)

    args = Namespace(
        repo=str(tmp_path),
        task="inspect dirty worktree",
        task_arg=None,
        scope=["backend/app.py"],
        scope_arg=[],
    )
    rc = chunkyctl.run_preflight(args)
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["tooling_gate_command"]["cmd"][0] == "/usr/local/bin/moth"
    assert report["tooling_gate"]["codegraph"]["pending"]["sync_required"] is True
    assert report["risks"] == [
        {
            "severity": "FAIL",
            "risk": "dirty_worktree",
            "detail": "classify/stage by scope; never git add .",
        },
        {
            "severity": "FAIL",
            "risk": "codegraph_pending",
            "detail": "sync and disclose remaining untracked Added pending",
        },
    ]


def test_audit_plan_prefers_scoped_commands() -> None:
    report = chunkyctl.build_audit_plan(
        scopes=["backend/scripts/chunkyctl.py", "backend/tests/scripts/test_chunkyctl.py"]
    )

    commands = [item["cmd"] for item in report["commands"]]
    assert report["verdict"] == "PASS"
    assert commands[0][:3] == [sys.executable, "backend/scripts/audit_test_tool_health.py", "--scope"]
    assert chunkyctl.build_moth_snapshot_command(chunkyctl.REPO, "chunkymonkey") in commands
    assert [sys.executable, "-m", "py_compile", "backend/scripts/chunkyctl.py", "backend/tests/scripts/test_chunkyctl.py"] in commands
    assert ["codegraph", "sync", "."] in commands
    assert [sys.executable, "-m", "pytest", "-q", "backend/tests/scripts/test_chunkyctl.py"] in commands


def test_audit_plan_does_not_py_compile_backend_config_files() -> None:
    report = chunkyctl.build_audit_plan(
        scopes=[
            "backend/scripts/chunkyctl.py",
            "backend/config/test_tool_registry.yaml",
            "pytest.ini",
        ]
    )

    commands = [item["cmd"] for item in report["commands"]]
    assert [sys.executable, "-m", "py_compile", "backend/scripts/chunkyctl.py"] in commands
    assert all("backend/config/test_tool_registry.yaml" not in command for command in commands[1:])


def test_audit_plan_without_scope_warns() -> None:
    report = chunkyctl.build_audit_plan(scopes=[])

    assert report["verdict"] == "WARN"
    assert report["commands"] == []


def test_prompt_printer_helpers_are_removed() -> None:
    assert not hasattr(chunkyctl, "SESSION_PROMPT")
    assert not hasattr(chunkyctl, "build_prompt_payload")
    assert not hasattr(chunkyctl, "run_prompt")


def test_storage_payload_summary_keeps_only_top_risk_findings() -> None:
    report = chunkyctl._storage_payload_summary(
        {
            "verdict": "FAIL",
            "summary": {"fail": 2, "warn": 1, "pass": 4},
            "findings": [
                {"severity": "PASS", "table": "ok", "column": "payload_json", "max_value_bytes": 0},
                {"severity": "WARN", "table": "warn", "column": "payload_json", "max_value_bytes": 9},
                {"severity": "FAIL", "table": "small_fail", "column": "audit_json", "max_value_bytes": 10},
                {"severity": "FAIL", "table": "large_fail", "column": "signals_json", "max_value_bytes": 100},
            ],
        },
        max_findings=2,
    )

    assert report is not None
    assert report["verdict"] == "FAIL"
    assert [item["table"] for item in report["top_findings"]] == ["large_fail", "small_fail"]


def test_next_actions_include_storage_payload_failures() -> None:
    actions = chunkyctl._next_actions(
        {
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False}},
            "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}},
        },
        {"unknown_count": 0},
        {"verdict": "FAIL"},
    )

    assert actions[-1] == {
        "priority": "P0",
        "action": "Review storage payload audit findings for recursive JSON or oversized opaque DB payloads before claiming cleanup complete",
    }


def test_worktree_report_classifies_dirty_entries_by_review_bucket(tmp_path: Path) -> None:
    report = chunkyctl.build_worktree_report(
        repo=tmp_path,
        git_status_text="\n".join(
            [
                " M goal.md",
                "?? backend/scripts/chunkyctl.py",
                " D PLAN_V3.md",
                "?? analysis/plan_v3_20260514_archived.md",
                " M backend/routers/updater.py",
                "?? backend/routers/updater_status.py",
                " M backend/services/universe.py",
                "?? backend/config/tdx_data_need_coverage.yaml",
                " M backend/scripts/audit_data_completeness.py",
                " M backend/scripts/build_price_kline_tdxhub.py",
                " M backend/services/data_quality.py",
                " M backend/tests/test_universe.py",
                "?? backend/scripts/audit_n_plus_one_results.json",
                " M docs/data_product_contract.md",
                " M pytest.ini",
                " M CLAUDE.md",
                " M gcp/README_GCP_BATCH.md",
                " M gcp/cost_tracker.sh",
                " M backend/scripts/run_phase4_full_chain.sh",
                " M configs/data_governance.yaml",
                "?? scratch.tmp",
            ]
        ),
    )

    assert report["verdict"] == "FAIL"
    assert report["summary"]["total"] == 21
    assert report["summary"]["unknown_count"] == 1
    assert report["summary"]["codegraph_candidate_untracked_count"] == 2
    assert report["summary"]["codegraph_candidate_untracked_bucket_counts"] == {
        "startup_tooling": 1,
        "updater_split": 1,
    }
    assert report["summary"]["bucket_counts"] == {
        "controller_state": 2,
        "startup_tooling": 3,
        "docs_archive_moves": 2,
        "updater_split": 2,
        "universe_governance": 1,
        "data_source_lineage_profiles": 2,
        "audit_gate_scripts": 1,
        "pipeline_build_scripts": 2,
        "backend_services_api": 1,
        "tests": 1,
        "generated_evidence": 1,
        "config_project": 2,
        "unknown": 1,
    }
    assert report["summary"]["selected_bucket_counts"] == report["summary"]["bucket_counts"]
    updater_bucket = next(item for item in report["buckets"] if item["bucket"] == "updater_split")
    assert updater_bucket["priority"] == "P1"
    assert {entry["normalized_path"] for entry in updater_bucket["entries"]} == {
        "backend/routers/updater.py",
        "backend/routers/updater_status.py",
    }


def test_worktree_report_can_filter_to_one_bucket(tmp_path: Path) -> None:
    report = chunkyctl.build_worktree_report(
        repo=tmp_path,
        git_status_text=" M goal.md\n?? backend/routers/updater_status.py\n?? scratch.tmp\n",
        bucket="updater_split",
    )

    assert report["summary"]["total"] == 3
    assert report["summary"]["unknown_count"] == 1
    assert report["summary"]["bucket_counts"] == {
        "controller_state": 1,
        "updater_split": 1,
        "unknown": 1,
    }
    assert report["summary"]["selected_bucket_counts"] == {"updater_split": 1}
    assert [item["bucket"] for item in report["buckets"]] == ["updater_split"]
    assert report["buckets"][0]["entries"][0]["normalized_path"] == "backend/routers/updater_status.py"


def test_worktree_markdown_summary_lists_bucket_commands(tmp_path: Path) -> None:
    report = chunkyctl.build_worktree_report(
        repo=tmp_path,
        git_status_text=" M goal.md\n?? backend/routers/updater_status.py\n?? scratch.tmp\n",
    )

    markdown = chunkyctl.render_worktree_markdown(report)

    assert "| Verdict | FAIL |" in markdown
    assert "| Unknown entries | 1 |" in markdown
    assert "`scripts/chunkyctl worktree --bucket updater_split --format markdown`" in markdown
    assert "## Entries:" not in markdown


def test_worktree_markdown_bucket_filter_lists_entries(tmp_path: Path) -> None:
    report = chunkyctl.build_worktree_report(
        repo=tmp_path,
        git_status_text=" M goal.md\n?? backend/routers/updater_status.py\n?? scratch.tmp\n",
        bucket="updater_split",
    )

    markdown = chunkyctl.render_worktree_markdown(report)

    assert "## Entries: updater_split" in markdown
    assert "- `untracked` `backend/routers/updater_status.py`" in markdown
    assert "Recommended action: Review as updater modularization slice" in markdown


def test_doctor_worktree_summary_reconciles_codegraph_pending(tmp_path: Path) -> None:
    worktree_report = chunkyctl.build_worktree_report(
        repo=tmp_path,
        git_status_text="\n".join(
            [
                "?? backend/routers/updater_status.py",
                "?? docs/chunkyctl_session_quickstart.md",
                " M goal.md",
            ]
        ),
    )
    summary = chunkyctl.build_doctor_worktree_summary(
        worktree_report,
        codegraph={"pending": {"added": 1, "total": 1, "sync_required": True}},
    )

    assert summary["total"] == 3
    assert summary["unknown_count"] == 0
    assert summary["codegraph_reconciliation"] == {
        "pending_added": 1,
        "untracked_indexable_files": 1,
        "matches": True,
        "interpretation": "CodeGraph Added pending matches untracked .py/.js/.jsx files",
    }
    actions = chunkyctl._next_actions(
        {"git_status": {"clean": False}, "codegraph": {"pending": {"added": 1, "sync_required": True}}, "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}}},
        summary,
    )
    assert actions[:2] == [
        {
            "priority": "P0",
            "action": "Review dirty worktree one bucket at a time with scripts/chunkyctl worktree --bucket <name>; do not bulk stage",
        },
        {
            "priority": "P0",
            "action": "CodeGraph Added pending matches untracked indexable files; review/stage by worktree bucket, not by forcing sync",
        },
    ]


def test_doctor_includes_data_health_snapshot_and_red_action(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_moth_snapshot(repo, profile):
        payload = {
            "schema_version": 1,
            "generated_at": "2026-06-02T00:00:00Z",
            "status": "PASS",
            "profile": {"name": profile},
            "dirty_worktree": [],
            "codegraph": {"pending": {"sync_required": False, "added": 0}},
            "complexity": {
                "baseline": {"status": "loaded"},
                "diff": {"status": "compared", "new_high_count": 0},
            },
            "issues": [],
            "warnings": [],
        }
        return {
            "command": [sys.executable, "-m", "moth.cli", "snapshot", "--repo", str(repo), "--profile", profile, "--format", "json"],
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
            "payload": payload,
            "verdict": "PASS",
        }

    def fake_run_command(cmd, cwd):
        cmd_text = " ".join(str(part) for part in cmd)
        if "audit_test_tool_health.py" in cmd_text:
            return {"cmd": cmd, "returncode": 0, "stdout": json.dumps({"verdict": "PASS"}), "stderr": ""}
        if "check_universe_filter.py" in cmd_text:
            return {"cmd": cmd, "returncode": 0, "stdout": "", "stderr": ""}
        if "audit_storage_payloads.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps({"verdict": "PASS", "summary": {"pass": 1, "warn": 0, "fail": 0}, "findings": []}),
                "stderr": "",
            }
        if "data_health_snapshot.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 1,
                "stdout": json.dumps(
                    {
                        "schema_version": 1,
                        "command": "data_health_snapshot",
                        "run_started_at": "2026-06-01T13:57:00Z",
                        "snapshot_at": "2026-06-01T13:57:00",
                        "dry_run": True,
                        "keep_history": 30,
                        "summary": {"total": 3, "green": 1, "yellow": 1, "red": 1},
                        "verdict": "FAIL",
                        "red_tables": [{"table_name": "red_table", "severity": "red"}],
                        "yellow_tables": [{"table_name": "yellow_table", "severity": "yellow"}],
                        "blockers": ["red_table"],
                    }
                ),
                "stderr": "",
            }
        if "audit_stage_opt_candidate_supply.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "verdict": "PASS",
                        "raw_signal_rows": 1381657,
                        "raw_trigger_rows": 7,
                        "raw_state_history_rows": 3,
                        "filtered_signal_rows": 733083,
                        "unique_keys": 120273,
                        "ready_keys": 57986,
                        "ready_coverage_pct": 48.21,
                        "blocked_reason_counts": {"below_min_signals": 62287},
                        "codes_without_bars": 0,
                        "next_action_recommendation": {
                            "priority": "P1",
                            "focus": "upstream_candidate_supply",
                            "reason": "below_min_signals dominates current blocked keys",
                            "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                            "weakest_formula_ids": ["macd_golden_cross", "reversal_1m_deep"],
                            "weakest_stage_bins": ["1", "1.5"],
                            "top_blocked_reason": "below_min_signals",
                        },
                    }
                ),
                "stderr": "",
            }
        if "audit_tdx_data_need_coverage.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "need_count": 27,
                        "blocked_need_count": 1,
                        "registered_source_names": ["aif10", "akshare"],
                        "blocked_needs": [
                            {
                                "need_id": "need_027",
                                "need_name": "主力/超大/大/中/小单资金流向",
                                "evidence_status": "unknown",
                                "production_eligibility": "blocked",
                                "failure_queue_snapshot": {
                                    "status_counts": {"open": 2, "resolved": 1},
                                },
                                "source_registration": {
                                    "preferred_source_capabilities": ["individual_fund_flow", "individual_fund_flow_rank"],
                                    "fallback_source_capabilities": ["other_capability"],
                                },
                            }
                        ],
                    }
                ),
                "stderr": "",
            }
        if cmd == ["git", "status", "--short"]:
            return {"cmd": cmd, "returncode": 0, "stdout": "", "stderr": ""}
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(chunkyctl, "run_moth_snapshot", fake_moth_snapshot)
    monkeypatch.setattr(chunkyctl, "_run_command", fake_run_command)

    args = Namespace(
        repo=str(tmp_path),
        complexity_target="backend",
        max_findings=80,
        baseline=None,
        fail_on_dirty_worktree=False,
        skip_storage_payload=False,
        skip_stage_opt=False,
        storage_max_findings=20,
    )
    rc = chunkyctl.run_doctor(args)
    report = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert report["data_health"]["report"]["summary"] == {"total": 3, "green": 1, "yellow": 1, "red": 1}
    assert report["data_health"]["report"]["verdict"] == "FAIL"
    assert report["stage_opt"]["report"]["summary"]["raw_trigger_rows"] == 7
    assert report["stage_opt"]["report"]["summary"]["raw_state_history_rows"] == 3
    assert report["stage_opt"]["report"]["blocked_reason_counts"] == {"below_min_signals": 62287}
    assert report["stage_opt"]["report"]["top_blocked_reason_counts"] == [
        {"reason": "below_min_signals", "count": 62287}
    ]
    assert report["stage_opt"]["report"]["next_action_recommendation"]["focus"] == "upstream_candidate_supply"
    assert report["need_coverage"]["report"]["summary"]["blocked_need_count"] == 1
    assert report["verdict"] == "FAIL"
    assert any("data health red tables" in action["action"] for action in report["next_actions"])
    assert any("upstream_candidate_supply" in action["action"] for action in report["next_actions"])
    assert any("need_027 blocked/unknown" in action["action"] for action in report["next_actions"])
    assert any("failure_queue open=2 resolved=1" in action["action"] for action in report["next_actions"])


def test_doctor_prioritizes_blocking_yellow_health_items(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_moth_snapshot(repo, profile):
        payload = {
            "schema_version": 1,
            "generated_at": "2026-06-02T00:00:00Z",
            "status": "PASS",
            "profile": {"name": profile},
            "dirty_worktree": [],
            "codegraph": {"pending": {"sync_required": False, "added": 0}},
            "complexity": {
                "baseline": {"status": "loaded"},
                "diff": {"status": "compared", "new_high_count": 0},
            },
            "issues": [],
            "warnings": [],
        }
        return {
            "command": [sys.executable, "-m", "moth.cli", "snapshot", "--repo", str(repo), "--profile", profile, "--format", "json"],
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
            "payload": payload,
            "verdict": "PASS",
        }

    def fake_run_command(cmd, cwd):
        cmd_text = " ".join(str(part) for part in cmd)
        if "audit_test_tool_health.py" in cmd_text:
            return {"cmd": cmd, "returncode": 0, "stdout": json.dumps({"verdict": "PASS"}), "stderr": ""}
        if "check_universe_filter.py" in cmd_text:
            return {"cmd": cmd, "returncode": 0, "stdout": "", "stderr": ""}
        if "audit_storage_payloads.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps({"verdict": "PASS", "summary": {"pass": 1, "warn": 0, "fail": 0}, "findings": []}),
                "stderr": "",
            }
        if "data_health_snapshot.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "schema_version": 1,
                        "command": "data_health_snapshot",
                        "run_started_at": "2026-06-01T13:57:00Z",
                        "snapshot_at": "2026-06-01T13:57:00",
                        "dry_run": True,
                        "keep_history": 30,
                        "summary": {"total": 2, "green": 1, "yellow": 1, "red": 0, "blocking_yellow": 1},
                        "verdict": "WARN",
                        "red_tables": [],
                        "yellow_tables": [{"table_name": "blocking_yellow_table", "severity": "yellow"}],
                        "blocking_yellow_tables": [
                            {
                                "table_name": "blocking_yellow_table",
                                "severity": "yellow",
                                "quality_gate_level": "blocking",
                                "issue_summary": "writer is 223.0h old (SLA 168h)",
                            }
                        ],
                        "blockers": [],
                    }
                ),
                "stderr": "",
            }
        if "audit_stage_opt_candidate_supply.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "verdict": "PASS",
                        "raw_signal_rows": 1381657,
                        "raw_trigger_rows": 7,
                        "raw_state_history_rows": 3,
                        "filtered_signal_rows": 733083,
                        "unique_keys": 120273,
                        "ready_keys": 57986,
                        "ready_coverage_pct": 48.21,
                        "blocked_reason_counts": {"below_min_signals": 62287},
                        "codes_without_bars": 0,
                        "next_action_recommendation": {
                            "priority": "P1",
                            "focus": "upstream_candidate_supply",
                            "reason": "below_min_signals dominates current blocked keys",
                            "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                            "weakest_formula_ids": ["macd_golden_cross", "reversal_1m_deep"],
                            "weakest_stage_bins": ["1", "1.5"],
                            "top_blocked_reason": "below_min_signals",
                        },
                    }
                ),
                "stderr": "",
            }
        if "audit_tdx_data_need_coverage.py" in cmd_text:
            return {
                "cmd": cmd,
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "need_gap_summary": {
                            "need_count": 27,
                            "blocked_need_count": 1,
                            "registered_source_names": ["aif10", "akshare"],
                            "blocked_needs": [
                                {
                                    "need_id": "need_027",
                                    "need_name": "主力/超大/大/中/小单资金流向",
                                    "evidence_status": "unknown",
                                    "production_eligibility": "blocked",
                                    "failure_queue_snapshot": {
                                        "status_counts": {"open": 2, "resolved": 1},
                                    },
                                    "source_registration": {
                                        "preferred_source_capabilities": ["individual_fund_flow", "individual_fund_flow_rank"],
                                        "fallback_source_capabilities": ["other_capability"],
                                    },
                                }
                            ],
                        },
                    }
                ),
                "stderr": "",
            }
        if cmd == ["git", "status", "--short"]:
            return {"cmd": cmd, "returncode": 0, "stdout": "", "stderr": ""}
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(chunkyctl, "run_moth_snapshot", fake_moth_snapshot)
    monkeypatch.setattr(chunkyctl, "_run_command", fake_run_command)

    args = Namespace(
        repo=str(tmp_path),
        complexity_target="backend",
        max_findings=80,
        baseline=None,
        fail_on_dirty_worktree=False,
        skip_storage_payload=False,
        skip_stage_opt=False,
        storage_max_findings=20,
    )
    rc = chunkyctl.run_doctor(args)
    report = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert report["data_health"]["report"]["summary"]["blocking_yellow"] == 1
    assert report["data_health"]["report"]["verdict"] == "WARN"
    assert report["verdict"] == "WARN"
    assert any("quality_gate_level=blocking first" in action["action"] for action in report["next_actions"])
    assert any("need_027 blocked/unknown" in action["action"] for action in report["next_actions"])
    assert any("failure_queue open=2 resolved=1" in action["action"] for action in report["next_actions"])


def test_next_actions_include_stage_opt_recommendation() -> None:
    actions = chunkyctl._next_actions(
        {
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False}},
            "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}},
        },
        {"unknown_count": 0},
        {"verdict": "PASS"},
        {"summary": {"total": 342, "green": 342, "yellow": 0, "red": 0}},
        {
            "next_action_recommendation": {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "weakest_formula_ids": ["macd_golden_cross", "reversal_1m_deep"],
                "weakest_stage_bins": ["1", "1.5"],
                "top_blocked_reason": "below_min_signals",
            }
        },
    )

    assert actions[-1] == {
        "priority": "P1",
        "action": (
            "Stage-opt candidate supply [upstream_candidate_supply]: below_min_signals dominates current blocked keys → "
            "expand upstream formula coverage or signal density before tuning profile knobs "
            "(weakest formulas: macd_golden_cross, reversal_1m_deep; weakest stages: 1, 1.5)"
        ),
    }


def test_stage_opt_summary_preserves_min_signals_sensitivity() -> None:
    live_formula_ids = [
        "activity_breakout",
        "dynamic_ma_iterative_cross",
        "gs_pullback_confirm",
        "gs_raw_buy",
        "ma_base_breakout",
        "macd_golden_cross",
        "monthly_uptrend_daily_pullback_buy",
        "multi_tf_rsi_alignment",
        "reversal_1m_deep",
        "reversal_1m_mild",
        "reversal_1w",
        "turtle_breakout_20",
        "turtle_breakout_55",
        "volume_base_breakout",
        "weekly_breakout_daily_confirm",
        "weekly_dragon_daily_pullback",
        "weekly_higher_low_daily_break",
        "weekly_macd_daily_macd_bull",
    ]
    summary = chunkyctl._stage_opt_summary(
        {
            "raw_signal_rows": 10,
            "raw_trigger_rows": 7,
            "raw_state_history_rows": 3,
            "filtered_signal_rows": 8,
            "unique_keys": 4,
            "ready_keys": 2,
            "ready_coverage_pct": 50.0,
            "blocked_reason_counts": {"below_min_signals": 6},
            "codes_without_bars": 0,
            "live_formula_registry": {
                "formula_count": 18,
                "formula_ids": live_formula_ids,
            },
            "research_formula_registry": {
                "formula_count": 5,
                "formula_ids": [
                    "gs_raw_buy",
                    "gs_pullback_confirm",
                    "ma_base_breakout",
                    "activity_breakout",
                    "volume_base_breakout",
                ],
            },
            "next_action_recommendation": {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "weakest_formula_ids": ["macd_golden_cross"],
                "weakest_stage_bins": ["1"],
                "top_blocked_reason": "below_min_signals",
            },
            "min_signals_sensitivity": [
                {
                    "min_signals": 4,
                    "ready_keys": 3,
                    "ready_coverage_pct": 75.0,
                    "delta_ready_keys": 1,
                    "delta_ready_coverage_pct": 25.0,
                    "below_min_signals": 5,
                    "delta_below_min_signals": -1,
                    "next_action_recommendation": {
                        "priority": "P1",
                        "focus": "upstream_candidate_supply",
                        "reason": "below_min_signals dominates current blocked keys",
                        "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                        "top_blocked_reason": "below_min_signals",
                    },
                }
            ],
        }
    )

    assert summary["min_signals_sensitivity"][0]["min_signals"] == 4
    assert summary["min_signals_sensitivity"][0]["ready_coverage_pct"] == 75.0
    assert summary["blocked_reason_counts"] == {"below_min_signals": 6}
    assert summary["top_blocked_reason_counts"] == [
        {"reason": "below_min_signals", "count": 6}
    ]
    assert summary["summary"]["raw_trigger_rows"] == 7
    assert summary["summary"]["raw_state_history_rows"] == 3
    assert summary["live_formula_registry"]["formula_count"] == 18
    assert "macd_golden_cross" in summary["live_formula_registry"]["formula_ids"]
    assert "gs_raw_buy" in summary["live_formula_registry"]["formula_ids"]
    assert "weekly_macd_daily_macd_bull" in summary["live_formula_registry"]["formula_ids"]
    assert summary["research_formula_registry"]["formula_count"] == 5
    assert "gs_raw_buy" in summary["research_formula_registry"]["formula_ids"]


def test_next_actions_include_stage_opt_live_registry_boundary() -> None:
    live_formula_ids = [
        "activity_breakout",
        "dynamic_ma_iterative_cross",
        "gs_pullback_confirm",
        "gs_raw_buy",
        "ma_base_breakout",
        "macd_golden_cross",
        "monthly_uptrend_daily_pullback_buy",
        "multi_tf_rsi_alignment",
        "reversal_1m_deep",
        "reversal_1m_mild",
        "reversal_1w",
        "turtle_breakout_20",
        "turtle_breakout_55",
        "volume_base_breakout",
        "weekly_breakout_daily_confirm",
        "weekly_dragon_daily_pullback",
        "weekly_higher_low_daily_break",
        "weekly_macd_daily_macd_bull",
    ]
    actions = chunkyctl._next_actions(
        {
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False}},
            "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}},
        },
        {"unknown_count": 0},
        {"verdict": "PASS"},
        {"summary": {"total": 342, "green": 342, "yellow": 0, "red": 0}},
        {
            "next_action_recommendation": {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "weakest_formula_ids": ["reversal_1m_deep"],
                "weakest_stage_bins": ["1.5"],
                "top_blocked_reason": "below_min_signals",
            },
            "live_formula_registry": {
                "formula_count": 18,
                "formula_ids": live_formula_ids,
            },
            "research_formula_registry": {
                "formula_count": 5,
                "formula_ids": [
                    "gs_raw_buy",
                    "gs_pullback_confirm",
                    "ma_base_breakout",
                    "activity_breakout",
                    "volume_base_breakout",
                ],
            },
        },
    )

    assert actions[-1] == {
        "priority": "P1",
        "action": (
            "Stage-opt candidate supply [upstream_candidate_supply]: below_min_signals dominates current blocked keys → "
            "expand upstream formula coverage or signal density before tuning profile knobs "
            f"(weakest formulas: reversal_1m_deep; weakest stages: 1.5; live registry formulas: 18; "
            f"live registry ids: {', '.join(live_formula_ids[:10])}; "
            "research challengers: 5; research challenger ids: "
            "gs_raw_buy, gs_pullback_confirm, ma_base_breakout, activity_breakout, volume_base_breakout)"
        ),
    }


def test_next_actions_include_stage_opt_structural_notes() -> None:
    actions = chunkyctl._next_actions(
        {
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False}},
            "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}},
        },
        {"unknown_count": 0},
        {"verdict": "PASS"},
        {"summary": {"total": 342, "green": 342, "yellow": 0, "red": 0}},
        {
            "next_action_recommendation": {
                "priority": "P1",
                "focus": "upstream_candidate_supply",
                "reason": "below_min_signals dominates current blocked keys",
                "recommended_lever": "expand upstream formula coverage or signal density before tuning profile knobs",
                "weakest_formula_ids": ["macd_golden_cross", "reversal_1m_deep"],
                "weakest_stage_bins": ["1", "1.5"],
                "top_blocked_reason": "below_min_signals",
                "structural_notes": [
                    "macd_golden_cross is capped by fact_technical_trigger PRIMARY KEY (stock_code, date, formula_id); extra MACD state rows need schema evolution, not a state-only formula tweak"
                ],
            }
        },
    )

    assert any(
        action["priority"] == "P1"
        and action["action"] == (
            "Stage-opt candidate supply [upstream_candidate_supply]: below_min_signals dominates current blocked keys → "
            "expand upstream formula coverage or signal density before tuning profile knobs "
            "(weakest formulas: macd_golden_cross, reversal_1m_deep; weakest stages: 1, 1.5; structural notes: "
            "macd_golden_cross is capped by fact_technical_trigger PRIMARY KEY (stock_code, date, formula_id); "
            "extra MACD state rows need schema evolution, not a state-only formula tweak)"
        )
        for action in actions
    )


def test_format_action_detail_suffix_handles_empty_and_joined_details() -> None:
    assert chunkyctl._format_action_detail_suffix([]) == ""
    assert chunkyctl._format_action_detail_suffix(["one", "two"]) == " (one; two)"


def test_next_actions_include_need_coverage_recommendation() -> None:
    actions = chunkyctl._next_actions(
        {
            "git_status": {"clean": True},
            "codegraph": {"pending": {"sync_required": False}},
            "complexity": {"baseline": {"status": "loaded"}, "diff": {"new_high_count": 0}},
        },
        {"unknown_count": 0},
        {"verdict": "PASS"},
        {"summary": {"total": 342, "green": 342, "yellow": 0, "red": 0}},
        None,
        {
                "blocked_needs": [
                    {
                        "need_id": "need_027",
                        "need_name": "主力/超大/大/中/小单资金流向",
                        "failure_queue_snapshot": {"status_counts": {"open": 2, "resolved": 1}},
                        "source_registration": {
                            "fallback_source_family": "aif10",
                            "fallback_source_supports_individual_fund_flow": False,
                            "preferred_source_capabilities": ["individual_fund_flow", "individual_fund_flow_rank"],
                            "fallback_source_capabilities": [],
                        },
                    }
            ]
        },
    )

    assert actions[-1] == {
        "priority": "P1",
        "action": (
            "Need coverage blocked-gap triage: review blocked needs and source evidence before treating them as production-ready "
            "(blocked needs: need_027; names: 主力/超大/大/中/小单资金流向) "
            "[need_027 blocked/unknown; failure_queue open=2 resolved=1]; aif10 exact individual_fund_flow unavailable"
        ),
    }


def test_docs_cleanup_report_combines_docs_graph_and_worktree_slice(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "goal.md").write_text("see docs/README.md\n", encoding="utf-8")
    (tmp_path / "SESSION_HANDOFF.md").write_text("handoff\n", encoding="utf-8")
    (tmp_path / "analysis/workflow_checkpoint.md").write_text("checkpoint\n", encoding="utf-8")
    (tmp_path / "docs/README.md").write_text("`README.md` `PROJECT_CONSTITUTION.md`\n", encoding="utf-8")
    (tmp_path / "docs/PROJECT_CONSTITUTION.md").write_text("constitution\n", encoding="utf-8")

    report = chunkyctl.build_docs_cleanup_report(
        repo=tmp_path,
        git_status_text="\n".join(
            [
                " M goal.md",
                " M docs/README.md",
                " D docs/old_plan.md",
                "?? analysis/docs_archive_20260531/old_plan.md",
                "?? backend/scripts/audit_docs_graph.py",
                "?? backend/tests/scripts/test_audit_docs_graph.py",
            ]
        ),
    )

    assert report["verdict"] == "WARN"
    assert report["docs_graph"]["verdict"] == "PASS"
    assert report["docs_graph"]["docs_count"] == 2
    assert report["worktree_slice"]["docs_bucket_counts"] == {"project_docs": 3}
    assert report["worktree_slice"]["support_bucket_counts"] == {
        "controller_state": 1,
        "audit_gate_scripts": 2,
    }
    assert report["worktree_slice"]["dirty_docs_entries"] == 3
    assert report["worktree_slice"]["dirty_support_entries"] == 3

    markdown = chunkyctl.render_docs_cleanup_markdown(report)

    assert "| Verdict | WARN |" in markdown
    assert "| docs_graph_verdict | PASS |" in markdown
    assert "| project_docs | 3 |" in markdown
    assert "| audit_gate_scripts | 2 |" in markdown


def test_docs_cleanup_report_fails_when_docs_graph_fails(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "goal.md").write_text("see docs/missing.md\n", encoding="utf-8")
    (tmp_path / "SESSION_HANDOFF.md").write_text("handoff\n", encoding="utf-8")
    (tmp_path / "analysis/workflow_checkpoint.md").write_text("checkpoint\n", encoding="utf-8")
    (tmp_path / "docs/README.md").write_text("`README.md`\n", encoding="utf-8")

    report = chunkyctl.build_docs_cleanup_report(repo=tmp_path, git_status_text="")

    assert report["verdict"] == "FAIL"
    assert report["docs_graph"]["unresolved_live_refs"] == 1
