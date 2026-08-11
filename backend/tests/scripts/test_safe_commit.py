from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SAFE_COMMIT = REPO_ROOT / "scripts" / "safe_commit.sh"
CODEX_REVIEW_GATE = REPO_ROOT / "backend" / "scripts" / "check_codex_review.py"


def _run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **kwargs,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _continuity_report(
    *, overall: str = "PASS", statuses: tuple[str, ...] = ("pass",)
) -> dict:
    checks = []
    counts = {
        "pass": 0, "warn": 0, "fail": 0, "observe": 0,
        "skipped": 0, "db_unreachable": 0,
    }
    for status in statuses:
        checks.append({
            "status": status,
            "check": "calendar_gaps",
            "domain": "margin_detail",
            "table": "raw_tushare_margin_detail",
            "detail": "fixture detail",
            "fix_hint": "fixture hint",
        })
        category = (
            "fail" if status.startswith("fail")
            else "warn" if status.startswith("warn")
            else "pass" if status == "pass"
            else "observe" if status.startswith("observe_")
            else "db_unreachable" if status == "db_unreachable"
            else "skipped"
        )
        counts[category] += 1
    return {
        "overall": overall,
        "latest_expected": "20260716",
        "checks": checks,
        "summary": {"counts": counts, "by_check": {"calendar_gaps": counts.copy()}},
    }


def _write_continuity_stub(repo: Path, payload: dict, *, rc: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    _write(
        repo / "backend" / "scripts" / "check_continuity_integrity.py",
        f"print({rendered!r})\nraise SystemExit({rc})\n",
    )


def _make_repo_with_staged_python(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SAFE_COMMIT, repo / "scripts" / "safe_commit.sh")
    (repo / "backend" / "scripts").mkdir(parents=True)
    (repo / "backend" / "config").mkdir(parents=True)
    shutil.copy2(CODEX_REVIEW_GATE, repo / "backend" / "scripts" / "check_codex_review.py")
    # WP1: fixture without classifier → safe_commit fail-closes to L3 full gates
    # (explicit stub keeps classify import path available for Rule 10 L1 checks).
    _write(
        repo / "backend" / "scripts" / "classify_commit_tier.py",
        "import json\n"
        "print(json.dumps({'tier':'L3','gates':["
        "'project_index_sync','feature_map','moth','rule_compliance',"
        "'sandbox_isolation','serve_read_layer','calendar_usage',"
        "'population_contract','lineage_drift','dead_references',"
        "'grain_uniqueness','continuity','no_emoji','config_refs','doc_drift',"
        "'doc_governance','doc_runtime_state','commit_msg','rule10'],"
        "'reasons':['fixture_l3'],'paths':[]}))\n",
    )
    # 2026-08-11: agent_board 门随 BOARD.md 退役(P2.3); no_emoji / doc_runtime_state 新登记。
    # always-on 的 ci-surface-drift 不看 tier, 沙箱必须真有这个测试文件, 否则 Step 3.35
    # 直接 exit 3 —— 这正是本文件长期 25 例全红的根因(全死在门体系之前, 与被测逻辑无关)。
    _write(
        repo / "backend" / "tests" / "scripts" / "test_ci_pytest_surface_drift.py",
        "def test_stub():\n    assert True\n",
    )
    _write(repo / "backend" / "scripts" / "check_no_emoji.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_doc_runtime_state.py",
           "print('[doc-runtime-state] stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_commit_message.py", "raise SystemExit(0)\n")
    # gate_policy 不可用时 safe_commit 走 fail-closed(全阻断) —— 沙箱给最小实现, 让
    # 分组语义(scaffold=warn / system_health=skip)在测试里也真的生效。
    _write(
        repo / "backend" / "scripts" / "gate_policy.py",
        "import sys\n"
        "g = sys.argv[sys.argv.index('--names')+1] if '--names' in sys.argv else ''\n"
        "print({'scaffold':'project_index_sync feature_map moth doc_drift doc_governance "
        "doc_runtime_state commit_msg','system_health':'grain_uniqueness continuity'}.get(g,''))\n",
    )
    _write(repo / "backend" / "config" / "commit_tiers.yaml", "version: 1\n")
    _write(repo / "backend" / "scripts" / "check_project_index_sync.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_rule_compliance.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "build_feature_map.py", "raise SystemExit(0)\n")
    _write(repo / "FEATURE_MAP.md", "generated fixture\n")
    # 2026-07-02 沙箱跟上 safe_commit 新门 (3.8 sandbox/3.9 serve/3.95 calendar/3.97 dead-references;
    #   pre-existing 6失败根因=沙箱缺这些脚本 → python 非0 → exit 5)
    _write(repo / "backend" / "scripts" / "check_sandbox_isolation.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_serve_read_layer.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_calendar_usage.py", "raise SystemExit(0)\n")
    _write(
        repo / "backend" / "scripts" / "check_universe_filter.py",
        "import json\n"
        "print(json.dumps({'verdict':'PASS','formal_dataset_count':1,"
        "'live_readiness':'NOT_EVALUATED'}))\n",
    )
    _write(repo / "backend" / "scripts" / "check_dead_references.py", "print('[dead-references] PASS')\nraise SystemExit(0)\n")
    # 2026-07-04 沙箱跟上 Step 3.98 (grain-uniqueness 门, 本 session R1 新 wire): 沙箱无真实
    #   sync_registry/数据库, 未 stub 会真跑该脚本报错退出非0 (与上条同款坑, 非本次逻辑改动引入)。
    _write(repo / "backend" / "scripts" / "check_grain_uniqueness.py", "print('grain-uniqueness: stub PASS')\nraise SystemExit(0)\n")
    # 2026-07-06 沙箱跟上 Step 3.99 (continuity-integrity 门, 全面数据审计根因根治新 wire):
    #   同款坑, 沙箱无真实 sync_registry/数据库不 stub 会真跑报错退出非0。
    _write_continuity_stub(repo, _continuity_report(), rc=0)
    # Step 3.991-3.994 都是硬闸；空测试仓库必须提供确定性 stub。
    _write(repo / "backend" / "scripts" / "check_config_refs.py",
           "print('[config-refs] PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_doc_drift.py",
           "print('{\"overall\": \"PASS\"}')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_doc_governance.py",
           "print('doc-governance: stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_lineage_drift.py",
           "print('[lineage-drift] stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "README.md", "seed\n")

    assert _run(["git", "init"], repo).returncode == 0
    assert _run(["git", "config", "user.email", "test@example.com"], repo).returncode == 0
    assert _run(["git", "config", "user.name", "Test User"], repo).returncode == 0
    assert _run(["git", "add", "."], repo).returncode == 0
    assert _run(["git", "commit", "-m", "initial"], repo).returncode == 0

    _write(repo / "backend" / "sample.py", "print('sample')\n")
    assert _run(["git", "add", "backend/sample.py"], repo).returncode == 0
    return repo


def _success_codegraph_env(repo: Path, **extra: str) -> dict[str, str]:
    fake_bin = repo.parent / "fake-codegraph-success"
    fake_bin.mkdir(exist_ok=True)
    fake_codegraph = fake_bin / "codegraph"
    _write(
        fake_codegraph,
        """#!/usr/bin/env bash
set -eu
case "${1:-}" in
  init)
    touch "$2/.fake-codegraph-initialized"
    ;;
  sync)
    if [[ -n "${2:-}" && ! -f "$2/.fake-codegraph-initialized" ]]; then
      exit 9
    fi
    ;;
  *) exit 9 ;;
esac
""",
    )
    fake_codegraph.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        **extra,
    }


def _safe_commit(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = _success_codegraph_env(repo, SAFE_COMMIT_DRY_RUN="1")
    return _run(["bash", "scripts/safe_commit.sh", message], repo, env=env)


def _safe_commit_no_push(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = _success_codegraph_env(repo, SAFE_COMMIT_NO_PUSH="1")
    return _run(["bash", "scripts/safe_commit.sh", message], repo, env=env)


def _stage_feature_map_fixture(repo: Path, *, source: str, rendered: str) -> None:
    _write(
        repo / "backend" / "scripts" / "build_feature_map.py",
        """from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
expected = f"map:{(root / 'SOURCE_VALUE.txt').read_text(encoding='utf-8').strip()}\\n"
actual = (root / 'FEATURE_MAP.md').read_text(encoding='utf-8')
raise SystemExit(0 if actual == expected else 1)
""",
    )
    _write(repo / "SOURCE_VALUE.txt", source)
    _write(repo / "FEATURE_MAP.md", rendered)
    _write(repo / "PROJECT_INDEX.md", "feature map fixture registered\n")
    assert _run(
        [
            "git",
            "add",
            "backend/scripts/build_feature_map.py",
            "SOURCE_VALUE.txt",
            "FEATURE_MAP.md",
            "PROJECT_INDEX.md",
        ],
        repo,
    ).returncode == 0


def _stage_lineage_fixture(repo: Path, *, source: str, rendered: str) -> None:
    _write(
        repo / "backend" / "scripts" / "check_lineage_drift.py",
        """from pathlib import Path

root = Path(__file__).resolve().parents[2]
expected = f"lineage:{(root / 'LINEAGE_SOURCE.txt').read_text(encoding='utf-8').strip()}\\n"
actual = (root / 'data' / 'lineage' / 'graph.json').read_text(encoding='utf-8')
raise SystemExit(0 if actual == expected else 1)
""",
    )
    _write(repo / "LINEAGE_SOURCE.txt", source)
    _write(repo / "data" / "lineage" / "graph.json", rendered)
    assert _run(
        [
            "git",
            "add",
            "backend/scripts/check_lineage_drift.py",
            "LINEAGE_SOURCE.txt",
            "data/lineage/graph.json",
        ],
        repo,
    ).returncode == 0


def test_rule10_missing_approve_only_notes_since_20260810(tmp_path: Path) -> None:
    """2026-08-10 裁决: 缺 APPROVE 不再阻断 —— 该门只能匹配提交者自写的字符串,
    验证不了审查是否真发生; 阻断只挡住不愿假称「审过了」的诚实提交者。"""
    repo = _make_repo_with_staged_python(tmp_path)
    result = _safe_commit_no_push(repo, "test audit without any review verdict")
    assert result.returncode == 0
    assert "NOTE: Rule 10" in result.stdout or "NOTE: Rule 10" in result.stderr

def test_rule10_blocks_request_changes_review_verdict(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: REQUEST_CHANGES")

    assert result.returncode == 6
    assert "REQUEST_CHANGES" in result.stdout


def test_rule10_blocks_request_changes_even_with_skip_reason(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(
        repo,
        "test audit\nCodex-Reviewed: REQUEST_CHANGES\ncodex-review: skipped reason=docs-only rename",
    )

    assert result.returncode == 6
    assert "REQUEST_CHANGES" in result.stdout


def test_rule10_accepts_approved_review_for_staged_python(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE_WITH_NOTES")

    assert result.returncode == 0
    assert "Rule 10 PASS" in result.stdout
    assert "canonical staged check_codex_review gate" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" in result.stdout


def test_rule10_still_blocks_explicit_request_changes(tmp_path: Path) -> None:
    """仍阻断的那一半: 没人会「忘记」写下否定裁决, 写了就意味着有未消除的异议。"""
    repo = _make_repo_with_staged_python(tmp_path)
    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: REQUEST_CHANGES")
    assert result.returncode != 0

def test_doc_governance_failure_warns_but_does_not_block(tmp_path: Path) -> None:
    """P1: doc_governance 属 scaffold —— 文档没闭合不该挡住代码修复。"""
    repo = _make_repo_with_staged_python(tmp_path)
    _write(repo / "backend" / "scripts" / "check_doc_governance.py", "raise SystemExit(1)\n")
    assert _run(["git", "add", "backend/scripts/check_doc_governance.py"], repo).returncode == 0
    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert "WARN-ONLY [doc_governance]" in result.stdout
    assert result.returncode == 0

def test_stale_staged_feature_map_warns_but_does_not_block(tmp_path: Path) -> None:
    """P1 起 feature_map 属 scaffold 组 —— 陈旧只提示不阻断。

    受害者是下一个读地图的人, 不是这次 diff; 用它挡住代码修复正是 P1 要消灭的。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    _write(repo / "backend" / "scripts" / "build_feature_map.py", "raise SystemExit(1)\n")
    assert _run(["git", "add", "backend/scripts/build_feature_map.py"], repo).returncode == 0

    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert "WARN-ONLY [feature_map]" in result.stdout
    assert result.returncode == 0, "scaffold 门不得阻断提交"


def test_system_health_gates_are_not_run_at_commit(tmp_path: Path) -> None:
    """P1: continuity / grain 查的是 live 数据, 与本次 diff 无关 —— commit 路径不跑。

    原先这里有 13 个用例断言 continuity 在 commit 路径的各种降级语义; 那套行为已整体
    归位 `daily_update` 的 system_health 自检(见 governance_gates.yaml)。留着它们就是
    在测一个不再存在的执法点。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert result.returncode == 0
    for gate in ("grain_uniqueness", "continuity"):
        assert f"skip {gate}" in result.stdout, f"{gate} 应在 commit 路径被跳过"
    assert "LIVE DATA READINESS" not in result.stdout, "commit 不再产生任何 readiness 声明"

def test_feature_map_gate_ignores_unstaged_worktree_change(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _stage_feature_map_fixture(repo, source="v2\n", rendered="map:v2\n")
    _write(repo / "SOURCE_VALUE.txt", "v3-unstaged\n")

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 0
    assert "staged snapshot fresh" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" in result.stdout


def test_stale_staged_lineage_graph_blocks_commit(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _stage_lineage_fixture(repo, source="v2\n", rendered="lineage:v1\n")

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 5
    assert "staged 血缘图" in result.stdout


def test_lineage_gate_ignores_unstaged_worktree_change(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _stage_lineage_fixture(repo, source="v2\n", rendered="lineage:v2\n")
    _write(repo / "LINEAGE_SOURCE.txt", "v3-unstaged\n")

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 0
    assert "staged snapshot PASS" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" in result.stdout


def test_moth_gate_runs_from_exported_staged_snapshot(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _write(repo / ".moth" / "profile.yaml", "kind: profile\nname: fixture\nrepo_path: .\n")
    assert _run(["git", "add", ".moth/profile.yaml"], repo).returncode == 0

    env = _success_codegraph_env(repo, SAFE_COMMIT_DRY_RUN="1")
    fake_bin = Path(env["PATH"].split(os.pathsep, 1)[0])
    calls = tmp_path / "moth-calls.txt"
    fake_moth = fake_bin / "moth"
    _write(
        fake_moth,
        """#!/usr/bin/env bash
set -eu
[[ "$PWD" == /tmp/chunkymonkey-staged-index.* || "$PWD" == /private/tmp/chunkymonkey-staged-index.* ]]
[[ -f .moth/profile.yaml ]]
[[ "${2:-}" == "--repo" ]]
[[ "${3:-}" == "." ]]
printf '%s\n' "${1:-}" >> "$MOTH_CALLS"
""",
    )
    fake_moth.chmod(0o755)
    env["MOTH_CALLS"] = str(calls)

    result = _run(
        ["bash", "scripts/safe_commit.sh", "test audit\nCodex-Reviewed: APPROVE"],
        repo,
        env=env,
    )

    assert result.returncode == 0
    assert "[moth] staged snapshot PASS" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == ["assert", "coupling"]


def test_safe_commit_no_push_commits_without_remote_push(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 0
    assert "SAFE_COMMIT_NO_PUSH=1: skipping git push." in result.stdout
    assert "DONE: commit + no-push + codegraph sync 完成" in result.stdout
    log = _run(["git", "log", "-1", "--format=%B"], repo)
    assert "Codex-Reviewed: APPROVE" in log.stdout


def test_codegraph_sync_failure_is_reported_after_commit(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codegraph = fake_bin / "codegraph"
    _write(
        fake_codegraph,
        """#!/usr/bin/env bash
if [[ "${1:-}" == "init" ]]; then exit 0; fi
if [[ "${1:-}" == "sync" && -n "${2:-}" ]]; then exit 0; fi
exit 9
""",
    )
    fake_codegraph.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "SAFE_COMMIT_NO_PUSH": "1",
    }

    result = _run(
        ["bash", "scripts/safe_commit.sh", "test audit\nCodex-Reviewed: APPROVE"],
        repo,
        env=env,
    )

    assert result.returncode == 7
    assert "commit 已创建，但 CodeGraph sync 失败" in result.stdout
    assert "Codex-Reviewed: APPROVE" in _run(
        ["git", "log", "-1", "--format=%B"], repo
    ).stdout
