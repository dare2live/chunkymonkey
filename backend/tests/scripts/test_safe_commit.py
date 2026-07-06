from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SAFE_COMMIT = REPO_ROOT / "scripts" / "safe_commit.sh"


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
    _write(repo / "backend" / "scripts" / "check_project_index_sync.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_rule_compliance.py", "raise SystemExit(0)\n")
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
    _write(repo / "README.md", "seed\n")

    assert _run(["git", "init"], repo).returncode == 0
    assert _run(["git", "config", "user.email", "test@example.com"], repo).returncode == 0
    assert _run(["git", "config", "user.name", "Test User"], repo).returncode == 0
    assert _run(["git", "add", "."], repo).returncode == 0
    assert _run(["git", "commit", "-m", "initial"], repo).returncode == 0

    _write(repo / "backend" / "sample.py", "print('sample')\n")
    assert _run(["git", "add", "backend/sample.py"], repo).returncode == 0
    return repo


def _safe_commit(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SAFE_COMMIT_DRY_RUN": "1"}
    return _run(["bash", "scripts/safe_commit.sh", message], repo, env=env)


def _safe_commit_no_push(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SAFE_COMMIT_NO_PUSH": "1"}
    return _run(["bash", "scripts/safe_commit.sh", message], repo, env=env)


def test_rule10_blocks_empty_skip_reason_for_staged_python(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\ncodex-review: skipped reason=   ")

    # 2026-06-12 决议: Rule 10 非阻塞化 — 空 skip reason 也只信息提示 (skip_reason=0)
    assert "Rule 10 (informational)" in result.stdout
    assert "skip_reason=0" in result.stdout


def test_rule10_blocks_request_changes_review_verdict(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: REQUEST_CHANGES")

    # 非阻塞化后 safe_commit 不再因 verdict 退出 6, 仅信息行 (历史条款见 CLAUDE.md §11)
    assert "Rule 10 (informational)" in result.stdout


def test_rule10_blocks_request_changes_even_with_skip_reason(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(
        repo,
        "test audit\nCodex-Reviewed: REQUEST_CHANGES\ncodex-review: skipped reason=docs-only rename",
    )

    # 非阻塞化 (2026-06-12 决议): verdict 不再触发退出 6
    assert "Rule 10 (informational)" in result.stdout


def test_rule10_accepts_approved_review_for_staged_python(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE_WITH_NOTES")

    assert result.returncode == 0
    assert "Rule 10 (informational)" in result.stdout  # 2026-06-12 决议: 信息行替代 'Rule 10 OK'
    assert "Codex-Reviewed=1" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" in result.stdout


def test_rule10_accepts_meaningful_skip_reason_for_staged_python(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit(repo, "test audit\ncodex-review: skipped reason=docs-only rename")

    assert result.returncode == 0
    assert "Rule 10 (informational)" in result.stdout
    assert "skip_reason=1" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" in result.stdout


def test_safe_commit_no_push_commits_without_remote_push(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 0
    assert "SAFE_COMMIT_NO_PUSH=1: skipping git push." in result.stdout
    assert "DONE: commit + no-push + codegraph sync 完成" in result.stdout
    log = _run(["git", "log", "-1", "--format=%B"], repo)
    assert "Codex-Reviewed: APPROVE" in log.stdout
