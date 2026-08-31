"""check_staged_blob_size.py 的机械锁 (2026-08-31 大 blob 入库硬门)。

**必须自带 fixture, 不许依赖当前仓库的真实 git 状态** — 项目在这条上栽过多次
(feedback-test-must-carry-its-own-fixture: 本地信息完整而 CI 浅克隆, 本地绿 CI 红)。
每个用例在 ``tmp_path`` 里建一个全新、自足的 git 仓库 (``git init`` + 配 user.email/
user.name), 阈值/白名单也各自写临时 policy yaml, 从不读 backend/config/repo_blob_policy.yaml
真文件或本仓库的真实 staged 状态。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_staged_blob_size as gate


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "t@t"], root)
    _run(["git", "config", "user.name", "t"], root)
    return root


def _write_policy(
    tmp_path: Path,
    *,
    fail_bytes: int = 1000,
    warn_bytes: int = 500,
    whitelist: list[tuple[str, str]] | None = None,
) -> Path:
    lines = ["version: 1", f"fail_bytes: {fail_bytes}", f"warn_bytes: {warn_bytes}"]
    if whitelist:
        lines.append("whitelist:")
        for path, reason in whitelist:
            lines.append(f"  - path: {path}")
            lines.append(f"    reason: {reason!r}")
    else:
        lines.append("whitelist: []")
    policy_path = tmp_path / "repo_blob_policy.yaml"
    policy_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return policy_path


def _stage_file(repo: Path, rel: str, size_bytes: int) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    _run(["git", "add", rel], repo)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path)


# ── 1. staged 小文件 → PASS ─────────────────────────────────────────────
def test_small_staged_file_passes(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "small.txt", 100)
    policy_path = _write_policy(tmp_path)
    policy = gate.load_policy(policy_path)
    fails, warns = gate.run(repo, policy)
    assert fails == []
    assert warns == []


# ── 2. staged 超阈值文件 → FAIL, 退出码非 0 ─────────────────────────────
def test_oversize_staged_file_fails_with_nonzero_exit(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "big.bin", 2000)
    policy_path = _write_policy(tmp_path)
    rc = gate.main(["--repo", str(repo), "--policy", str(policy_path)])
    assert rc != 0

    policy = gate.load_policy(policy_path)
    fails, warns = gate.run(repo, policy)
    assert len(fails) == 1
    assert "big.bin" in fails[0]
    assert "2,000 bytes" in fails[0]
    assert warns == []


# ── 3. 超阈值但在白名单 → PASS ──────────────────────────────────────────
def test_oversize_but_whitelisted_passes(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "big_but_ok.json", 2000)
    policy_path = _write_policy(
        tmp_path,
        whitelist=[("big_but_ok.json", "已知证据链依赖, 测试豁免")],
    )
    rc = gate.main(["--repo", str(repo), "--policy", str(policy_path)])
    assert rc == 0

    policy = gate.load_policy(policy_path)
    fails, warns = gate.run(repo, policy)
    assert fails == []
    assert warns == []


# ── 4. 只超 warn 阈值 → PASS 但报 warn, 退出码 0 ────────────────────────
def test_warn_only_zone_passes_but_reports_warn(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "medium.bin", 700)  # 500 < 700 <= 1000
    policy_path = _write_policy(tmp_path)
    rc = gate.main(["--repo", str(repo), "--policy", str(policy_path)])
    assert rc == 0

    policy = gate.load_policy(policy_path)
    fails, warns = gate.run(repo, policy)
    assert fails == []
    assert len(warns) == 1
    assert "medium.bin" in warns[0]
    assert "700 bytes" in warns[0]


# ── 5. 只查 A/M 不查 D: staged 删除的大文件 → 不该 FAIL ─────────────────
def test_staged_deletion_of_large_file_is_not_flagged(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "was_big.bin", 5000)
    _run(["git", "commit", "-q", "-m", "add large file"], repo)
    (repo / "was_big.bin").unlink()
    _run(["git", "add", "was_big.bin"], repo)  # stage 删除 (D)

    staged = gate.staged_added_modified(repo)
    assert "was_big.bin" not in staged

    policy_path = _write_policy(tmp_path)
    policy = gate.load_policy(policy_path)
    fails, warns = gate.run(repo, policy)
    assert fails == []
    assert warns == []


# ── 6. 白名单自清: 配了一个不存在的路径 → 报 stale 并计入 warn ──────────
def test_stale_whitelist_entry_is_reported_as_warn(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "small.txt", 10)
    policy_path = _write_policy(
        tmp_path,
        whitelist=[("data/lineage/does_not_exist.json", "早已删除但登记还在")],
    )
    policy = gate.load_policy(policy_path)
    fails, warns = gate.run(repo, policy)
    assert fails == []
    assert len(warns) == 1
    assert "stale" in warns[0]
    assert "data/lineage/does_not_exist.json" in warns[0]

    rc = gate.main(["--repo", str(repo), "--policy", str(policy_path)])
    assert rc == 0  # warn 不阻断


# ── 7. 阈值确实从 yaml 读取, 不是硬编码 ─────────────────────────────────
def test_threshold_is_read_from_yaml_not_hardcoded(repo: Path, tmp_path: Path) -> None:
    _stage_file(repo, "mid.bin", 300)

    strict_policy = _write_policy(tmp_path, fail_bytes=200, warn_bytes=100)
    fails, _ = gate.run(repo, gate.load_policy(strict_policy))
    assert len(fails) == 1, "300 bytes > fail_bytes=200 应该 FAIL"

    loose_policy = _write_policy(tmp_path, fail_bytes=10_000, warn_bytes=5_000)
    fails2, warns2 = gate.run(repo, gate.load_policy(loose_policy))
    assert fails2 == [] and warns2 == [], "同一个 300 bytes 文件, 换宽松阈值就该全绿 —— 证明阈值来自 yaml"


# ── policy 校验: fail-closed on 坏配置 ──────────────────────────────────
def test_policy_missing_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(gate.PolicyError):
        gate.load_policy(tmp_path / "nope.yaml")


def test_policy_warn_must_be_strictly_less_than_fail(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nfail_bytes: 100\nwarn_bytes: 100\nwhitelist: []\n", encoding="utf-8")
    with pytest.raises(gate.PolicyError):
        gate.load_policy(bad)


def test_policy_whitelist_entry_requires_reason(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\nfail_bytes: 100\nwarn_bytes: 50\nwhitelist:\n  - path: a.json\n",
        encoding="utf-8",
    )
    with pytest.raises(gate.PolicyError):
        gate.load_policy(bad)


def test_no_staged_files_is_clean(repo: Path, tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path)
    rc = gate.main(["--repo", str(repo), "--policy", str(policy_path)])
    assert rc == 0
