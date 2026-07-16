from __future__ import annotations

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


def _make_repo_with_staged_python(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SAFE_COMMIT, repo / "scripts" / "safe_commit.sh")
    (repo / "backend" / "scripts").mkdir(parents=True)
    shutil.copy2(CODEX_REVIEW_GATE, repo / "backend" / "scripts" / "check_codex_review.py")
    _write(repo / "backend" / "scripts" / "check_project_index_sync.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_rule_compliance.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "build_feature_map.py", "raise SystemExit(0)\n")
    _write(repo / "FEATURE_MAP.md", "generated fixture\n")
    # 2026-07-02 沙箱跟上 safe_commit 新门 (3.8 sandbox/3.9 serve/3.95 calendar/3.97 dead-references;
    #   pre-existing 6失败根因=沙箱缺这些脚本 → python 非0 → exit 5)
    _write(repo / "backend" / "scripts" / "check_sandbox_isolation.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_serve_read_layer.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_calendar_usage.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_dead_references.py", "print('[dead-references] PASS')\nraise SystemExit(0)\n")
    # 2026-07-04 沙箱跟上 Step 3.98 (grain-uniqueness 门, 本 session R1 新 wire): 沙箱无真实
    #   sync_registry/数据库, 未 stub 会真跑该脚本报错退出非0 (与上条同款坑, 非本次逻辑改动引入)。
    _write(repo / "backend" / "scripts" / "check_grain_uniqueness.py", "print('grain-uniqueness: stub PASS')\nraise SystemExit(0)\n")
    # 2026-07-06 沙箱跟上 Step 3.99 (continuity-integrity 门, 全面数据审计根因根治新 wire):
    #   同款坑, 沙箱无真实 sync_registry/数据库不 stub 会真跑报错退出非0。
    _write(repo / "backend" / "scripts" / "check_continuity_integrity.py",
           "print('continuity-integrity: overall=PASS pass=0 warn=0 fail=0 skipped=0 db_unreachable=0 (latest_expected=stub)')\nraise SystemExit(0)\n")
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


def test_rule10_blocks_empty_skip_reason_for_staged_python(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\ncodex-review: skipped reason=   ")

    assert result.returncode == 6
    assert "Rule 10 requires" in result.stdout


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


def test_rule10_blocks_meaningful_skip_reason_for_staged_python(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\ncodex-review: skipped reason=docs-only rename")

    assert result.returncode == 6
    assert "skip reasons do not satisfy the gate" in result.stdout


def test_doc_governance_failure_blocks_commit(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _write(
        repo / "backend" / "scripts" / "check_doc_governance.py",
        "print('doc-governance verdict=WARN fails=0 warns=1')\nraise SystemExit(1)\n",
    )
    assert _run(["git", "add", "backend/scripts/check_doc_governance.py"], repo).returncode == 0

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 5
    assert "doc-governance 门红" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" not in result.stdout


def test_stale_staged_feature_map_blocks_even_when_worktree_is_dirty(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _stage_feature_map_fixture(repo, source="v2\n", rendered="map:v1\n")

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 2
    assert "staged FEATURE_MAP.md" in result.stdout


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
