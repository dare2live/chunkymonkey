"""agent-boot 单测 — 汇总纯函数 + fail-closed 红例 (agent-OS WP3).

Runner 全部注入 stub; 不跑真 git/moth/codegraph, 不写任何状态。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import agent_boot  # noqa: E402


GIT_CLEAN = (
    "# branch.oid abc123\n"
    "# branch.head main\n"
    "# branch.upstream origin/main\n"
    "# branch.ab +0 -0\n"
)
GIT_DIRTY = (
    GIT_CLEAN.replace("+0 -0", "+2 -1")
    + "1 .M N... 100644 100644 100644 aaa bbb scripts/chunkyctl\n"
    + "? backend/scripts/agent_boot.py\n"
)
MOTH_WARN = json.dumps({
    "status": "WARN",
    "warnings": ["complexity hotspots: 80 findings"],
    "issues": [],
    "assertions": {"packs": [{"name": "chunkymonkey-claims", "pass": 30, "fail": 0, "error": 0}]},
    "dirty_worktree": [],
})


def _runner(table):
    def run(cmd):
        key = cmd[0]
        entry = table[key]
        return {"cmd": cmd, "returncode": entry.get("rc", 0),
                "stdout": entry.get("out", ""), "stderr": entry.get("err", "")}
    return run


def _write_board(root: pathlib.Path, **overrides) -> None:
    board = {
        "generated_at": "2026-07-20T09:00:00Z",
        "track": {"name": "agent-os-redesign", "a_to_h": "suspended_at_d8b69090"},
        "cutovers": {
            "b_pit_mart": {"cutover_allowed": False},
            "tier12_consumer": {"cutover_allowed": False},
        },
        "phase_e": {"overall_status": "measured_reject_no_gain"},
        "bans": ["Optuna / E gate loosen / StrategyRelease / margin thaw"],
        "next_knives_frozen": ["or stop"],
        "goal_hand_excerpt": "## 当前 objective",
    }
    board.update(overrides)
    path = root / "data" / "board" / "agent_context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board), encoding="utf-8")


def test_git_summary_parses_branch_and_dirty_counts():
    clean = agent_boot.git_summary(_runner({"git": {"out": GIT_CLEAN}}))
    assert clean["status"] == "ok"
    assert clean["branch"] == "main"
    assert clean["upstream"] == "origin/main"
    assert (clean["changed_count"], clean["untracked_count"]) == (0, 0)

    dirty = agent_boot.git_summary(_runner({"git": {"out": GIT_DIRTY}}))
    assert dirty["status"] == "warn"
    assert (dirty["ahead"], dirty["behind"]) == (2, 1)
    assert dirty["changed_count"] == 1
    assert dirty["untracked_count"] == 1
    assert "scripts/chunkyctl" in dirty["dirty_head"]


def test_git_summary_fails_closed_on_nonzero_exit():
    section = agent_boot.git_summary(_runner({"git": {"rc": 128, "err": "not a git repo"}}))
    assert section["status"] == "error"
    assert "not a git repo" in section["error"]


def test_moth_summary_compresses_snapshot():
    section = agent_boot.moth_summary(_runner({"moth": {"out": MOTH_WARN}}))
    assert section["status"] == "warn"
    assert section["moth_status"] == "WARN"
    assert section["assertion_packs"] == [
        {"name": "chunkymonkey-claims", "pass": 30, "fail": 0, "error": 0}
    ]
    assert section["warnings"] == ["complexity hotspots: 80 findings"]


def test_moth_fail_is_reported_warn_not_tool_error(tmp_path):
    """moth status=FAIL is a snapshot fact; boot must not render ERROR: None."""
    payload = json.loads(MOTH_WARN)
    payload["status"] = "FAIL"
    payload["issues"] = ["assertion error: example"]
    section = agent_boot.moth_summary(_runner({"moth": {"out": json.dumps(payload)}}))
    assert section["status"] == "warn"
    assert section["moth_status"] == "FAIL"
    _write_board(tmp_path)
    data = agent_boot.collect(tmp_path, run=_runner({
        "git": {"out": GIT_CLEAN},
        "moth": {"out": json.dumps(payload)},
        "codegraph": {"out": "Index is up to date"},
    }))
    assert data["overall"] == "warn"  # FAIL fact → warn, not tool error
    text = agent_boot.render_text(data)
    assert "status=FAIL" in text
    assert "ERROR: None" not in text
    assert "assertion error: example" in text


@pytest.mark.parametrize(
    "result",
    [
        {"out": "not json"},
        {"out": "[1, 2]"},
        {"out": json.dumps({"warnings": []})},          # missing status
        {"out": json.dumps({"status": "GREENISH"})},    # invalid status token
    ],
)
def test_moth_summary_fails_closed_on_malformed_snapshot(result):
    section = agent_boot.moth_summary(_runner({"moth": result}))
    assert section["status"] == "error"
    assert "invalid" in section["error"]


def test_moth_summary_missing_binary_is_unavailable_not_ok():
    section = agent_boot.moth_summary(_runner({"moth": {"rc": 127}}))
    assert section["status"] == "unavailable"


def test_codegraph_summary_distinguishes_fresh_stale_error():
    fresh = agent_boot.codegraph_summary(
        _runner({"codegraph": {"out": "✓ Index is up to date"}}))
    assert fresh["status"] == "ok" and fresh["index_fresh"] is True

    stale = agent_boot.codegraph_summary(
        _runner({"codegraph": {"out": "pending changes: added=1"}}))
    assert stale["status"] == "warn" and stale["index_fresh"] is False

    broken = agent_boot.codegraph_summary(
        _runner({"codegraph": {"rc": 1, "err": "index corrupt"}}))
    assert broken["status"] == "error"


def test_board_summary_reads_generated_context(tmp_path):
    _write_board(tmp_path)
    section = agent_boot.board_summary(tmp_path)
    assert section["status"] == "ok"
    assert section["track"]["a_to_h"] == "suspended_at_d8b69090"
    assert section["cutover_allowed"] == {"b_pit_mart": False, "tier12_consumer": False}
    assert section["phase_e_overall"] == "measured_reject_no_gain"


def test_board_summary_missing_or_invalid_is_error_with_regen_hint(tmp_path):
    missing = agent_boot.board_summary(tmp_path)
    assert missing["status"] == "error"
    assert "build_agent_board" in missing["fix"]

    path = tmp_path / "data" / "board" / "agent_context.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    invalid = agent_boot.board_summary(tmp_path)
    assert invalid["status"] == "error"


def test_collect_overall_and_exit_semantics(tmp_path):
    _write_board(tmp_path)
    table = {
        "git": {"out": GIT_CLEAN},
        "moth": {"out": MOTH_WARN.replace("WARN", "PASS")},
        "codegraph": {"out": "Index is up to date"},
    }
    ok = agent_boot.collect(tmp_path, run=_runner(table))
    assert ok["overall"] == "ok"
    assert ok["enforcement"] == "projection_only_not_truth"

    warn = agent_boot.collect(
        tmp_path, run=_runner({**table, "moth": {"out": MOTH_WARN}}))
    assert warn["overall"] == "warn"

    error = agent_boot.collect(
        tmp_path, run=_runner({**table, "moth": {"out": "not json"}}))
    assert error["overall"] == "error"


def test_render_text_is_one_page_with_all_sections(tmp_path):
    _write_board(tmp_path)
    data = agent_boot.collect(tmp_path, run=_runner({
        "git": {"out": GIT_DIRTY},
        "moth": {"out": MOTH_WARN},
        "codegraph": {"out": "Index is up to date"},
    }))
    text = agent_boot.render_text(data)
    for heading in (
        "## git",
        "## moth",
        "## codegraph",
        "## board",
        "## delivery (§15 knife-merge)",
        "## read next",
    ):
        assert heading in text
    assert "suspended_at_d8b69090" in text
    assert "goal.md" in text
    assert "one Rule10" in text
    assert "pre-knife" in text
    assert len(text.splitlines()) < 70  # one-page contract (+ §15 reminder)


def test_render_text_surfaces_board_error_and_fix(tmp_path):
    data = agent_boot.collect(tmp_path, run=_runner({
        "git": {"out": GIT_CLEAN},
        "moth": {"rc": 127},
        "codegraph": {"rc": 127},
    }))
    assert data["overall"] == "error"  # board missing
    text = agent_boot.render_text(data)
    assert "ERROR" in text
    assert "build_agent_board" in text
