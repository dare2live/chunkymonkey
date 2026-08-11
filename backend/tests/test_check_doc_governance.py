"""Focused behavior tests for the live-document governance gate."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.check_doc_governance import BESTCHOICE_ALLOWLIST, main, run  # noqa: E402


AUTHORITY_DOCS = (
    "README.md",
    "MASTER_TOPLEVEL_DESIGN.md",
    "engineering_governance.md",
    "strategy_validation_contract.md",
)


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_authority_docs(root: pathlib.Path) -> None:
    for name in AUTHORITY_DOCS:
        _write(root / "docs" / name, f"# {name}\n")


def test_warning_is_not_reported_as_pass_or_success(tmp_path, capsys):
    _seed_authority_docs(tmp_path)
    _write(tmp_path / "goal.md", "see `analysis/old.md`\n")
    _write(tmp_path / "analysis" / "old.md", "> 状态: retired；evidence-only\n")

    fails, warns = run(tmp_path)
    assert fails == []
    assert warns

    assert main(["--root", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "verdict=WARN" in output
    assert "verdict=PASS" not in output


def test_live_doc_reference_to_retired_chunkyctl_command_fails(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(tmp_path / "AGENTS.md", "Run `scripts/chunkyctl docs --format markdown`.\n")
    _write(
        tmp_path / "backend" / "scripts" / "chunkyctl.py",
        '_RETIRED = ("docs", "jobs")\n',
    )

    fails, warns = run(tmp_path)

    assert warns == []
    assert any("退役 CLI" in finding and "chunkyctl docs" in finding for finding in fails)


def test_historical_analysis_content_is_not_scanned_as_live_policy(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(tmp_path / "AGENTS.md", "Current policy.\n")
    _write(
        tmp_path / "analysis" / "old_plan.md",
        "# Old plan\n\n> 状态：evidence-only；本文不拥有当前规则。\n\nRun `scripts/chunkyctl docs`.\n",
    )
    _write(tmp_path / "backend" / "scripts" / "chunkyctl.py", '_RETIRED = ("docs",)\n')

    assert run(tmp_path) == ([], [])


def test_non_ledger_analysis_markdown_requires_evidence_only_header(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(tmp_path / "analysis" / "undecided.md", "# Still looks like a live plan\n")

    fails, warns = run(tmp_path)

    assert warns == []
    assert any("evidence-only" in finding and "analysis/undecided.md" in finding for finding in fails)


def test_analysis_evidence_header_rejects_live_self_owner_and_pending_main_session(tmp_path):
    _seed_authority_docs(tmp_path)
    cases = {
        "live.md": "> 状态：live\n",
        "self_owner.md": "> 状态：evidence-only；owner: self\n",
        "pending.md": "> 状态：evidence-only；待主会话决定是否转正\n",
    }
    for name, header in cases.items():
        _write(tmp_path / "analysis" / name, f"# Evidence\n\n{header}")

    fails, warns = run(tmp_path)

    assert warns == []
    assert all(any(name in finding for finding in fails) for name in cases)


def test_project_state_ledger_is_the_only_analysis_markdown_header_exception(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(
        tmp_path / "analysis" / "project_state_ledger.md",
        "# Ledger\n\n> 状态：historical evidence index，query-only\n",
    )

    assert run(tmp_path) == ([], [])


def test_generated_feature_map_cannot_list_retired_cli_as_current(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(
        tmp_path / "FEATURE_MAP.md",
        "# Feature Map\n\n### chunkyctl 子命令\n\n| 命令 | 说明 |\n|---|---|\n| `docs` | retired |\n",
    )
    _write(tmp_path / "backend" / "scripts" / "chunkyctl.py", '_RETIRED = ("docs",)\n')

    fails, warns = run(tmp_path)

    assert warns == []
    assert any("FEATURE_MAP.md" in finding and "chunkyctl docs" in finding for finding in fails)


def test_common_command_style_script_names_must_exist(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(
        tmp_path / "AGENTS.md",
        "Run check_missing.py, update_missing.py, and optimize_missing.py.\n",
    )

    fails, warns = run(tmp_path)

    assert fails == []
    assert {finding.split("命令 ", 1)[1].split(" ", 1)[0] for finding in warns} == {
        "check_missing.py",
        "update_missing.py",
        "optimize_missing.py",
    }


def test_existing_full_path_is_not_a_dangling_command_name(tmp_path):
    """C7 只管「悬空的命令名」；真实存在的全路径归 check_doc_drift。

    2026-08-11 实测反例：MASTER §5.4 引用 backend/services/pipeline/run_outcome.py，
    前缀 run_ 命中命令名启发式，而它是 service 模块不在 scripts/ 下 → 假 WARN。
    """
    _seed_authority_docs(tmp_path)
    _write(tmp_path / "backend" / "services" / "pipeline" / "run_outcome.py", "x = 1\n")
    _write(
        tmp_path / "docs" / "MASTER_TOPLEVEL_DESIGN.md",
        "单一计算点 = `backend/services/pipeline/run_outcome.py`。\n",
    )

    fails, warns = run(tmp_path)

    assert fails == []
    assert not [w for w in warns if "run_outcome.py" in w]


def test_full_path_that_does_not_exist_still_warns(tmp_path):
    """豁免只对**存在**的全路径生效，不能被写全路径就绕过。"""
    _seed_authority_docs(tmp_path)
    _write(
        tmp_path / "docs" / "MASTER_TOPLEVEL_DESIGN.md",
        "见 `backend/services/pipeline/run_ghost.py`。\n",
    )

    fails, warns = run(tmp_path)

    assert fails == []
    assert [w for w in warns if "run_ghost.py" in w]


def test_parallel_live_doc_is_rejected(tmp_path):
    _seed_authority_docs(tmp_path)
    _write(tmp_path / "docs" / "NEW_PLAN.md", "# parallel owner\n")

    fails, _ = run(tmp_path)

    assert any("authority allowlist" in finding and "NEW_PLAN.md" in finding for finding in fails)


def test_bestchoice_second_control_plane_is_rejected(tmp_path):
    _seed_authority_docs(tmp_path)
    for rel in BESTCHOICE_ALLOWLIST:
        _write(tmp_path / "bestchoice" / rel, "fixture\n")
    _write(tmp_path / "bestchoice" / "goal.md", "# second controller\n")

    fails, warns = run(tmp_path)

    assert warns == []
    assert any(
        "C9" in finding and "goal.md" in finding and "第二 control plane" in finding
        for finding in fails
    )
