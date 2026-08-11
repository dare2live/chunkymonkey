"""ledger 退役后唯一的历史检索入口 (goal.md P3.2/P3.3)。

它替代的是一份被**删掉**的文件，所以「查不到 = 没有替代品」。两个面都必须能查到：

* 逐刀细节 → commit message (`git log --grep`)
* 时期叙事 → annotated tag (`git tag -n99`)

**测试必须自带仓库，不许断言宿主仓库的历史** —— 首版就是这么写的，本地全绿而 public CI
四条全红：CI 用 `actions/checkout` 浅克隆，既没有 history 也没有 tag，于是 `git log` /
`git tag` 全返回空。断言「环境里恰好有什么」不是测行为，是测环境。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import history_cli as hc


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """一个自足的小仓库：两条 commit + 一个带叙事正文的 annotated tag。"""
    root = tmp_path / "repo"
    root.mkdir()
    env = ["-c", "user.email=t@t", "-c", "user.name=t"]
    _run(["git", "init", "-q", "-b", "main"], root)
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    _run(["git", "add", "-A"], root)
    _run(["git", *env, "commit", "-q", "-m",
          "fix(gov): cutover 生效性检查\n\nEvidence: 窗口末端已过期。\nResidual: 待 owner 裁决。"], root)
    (root / "b.txt").write_text("2\n", encoding="utf-8")
    _run(["git", "add", "-A"], root)
    _run(["git", *env, "commit", "-q", "-m", "chore: 无关改动"], root)
    _run(["git", *env, "tag", "-a", "era/test-reset", "-m",
          "2026-06-28 — 地基 reset\n\n- 大规模 reset，收缩为数据平台；\n- 结论：reset 是必要清障，不是最终架构。"], root)
    return root


# ── 两个面都必须查得到 ──────────────────────────────────────────────────
def test_grep_finds_per_knife_detail_in_commit_messages(repo: Path) -> None:
    rows = hc.search(grep=["cutover"], repo=repo)
    assert [r["subject"] for r in rows] == ["fix(gov): cutover 生效性检查"]
    assert rows[0]["hash"] and rows[0]["date"]
    assert not rows[0]["body"], "未要 --full 时不该带正文"


def test_full_returns_commit_body(repo: Path) -> None:
    rows = hc.search(grep=["cutover"], full=True, repo=repo)
    assert "Residual" in rows[0]["body"], "--full 必须给出 message 正文"


def test_eras_carry_the_narrative_not_just_a_title(repo: Path) -> None:
    """时期叙事必须**在 tag message 里**，不能只是一个指向文件的链接。"""
    rows = hc.eras(repo)
    assert [r["tag"] for r in rows] == ["era/test-reset"]
    assert "必要清障" in rows[0]["text"], "tag 里只剩标题，叙事正文没迁过来"


def test_narrative_only_in_tag_is_still_findable(repo: Path) -> None:
    """回归锁：只存在于时期叙事里的词，commit 面查不到，tag 面必须查到。"""
    assert hc.search(grep=["地基 reset"], repo=repo) == []
    hits = [e for e in hc.eras(repo) if "地基 reset" in e["tag"] + e["text"]]
    assert hits, "只活在 tag 里的叙事必须能被检索到，否则 ledger 没有替代品"


def test_time_window_narrows_without_keyword(repo: Path) -> None:
    assert hc.search(since="1970-01-01", repo=repo), "时间窗查询必须可用"


# ── 命令面 ──────────────────────────────────────────────────────────────
def test_main_requires_a_query() -> None:
    """不给条件就列全历史 = 又变回「整读账本」，正是要消灭的用法。"""
    with pytest.raises(SystemExit):
        hc.main([])


def test_main_paths_exit_zero_even_with_no_hits() -> None:
    """检索命令不是门：查不到是事实，不是失败。"""
    assert hc.main(["--eras"]) == 0
    assert hc.main(["--grep", "zzz-不存在的词-zzz", "--limit", "1"]) == 0
