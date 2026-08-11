"""ledger 退役后唯一的历史检索入口 (goal.md P3.2/P3.3)。

它替代的是一份被删掉的文件，所以**查不到 = 没有替代品**。两个面都必须能查到：

* 逐刀细节 → commit message (`git log --grep`)
* 时期叙事 → annotated tag (`git tag -n99`)

2026-08-11 实测踩到过：首版 `--grep` 只搜 commit，查「地基 reset」零命中——那段叙事
只存在于 `era/*` tag 里。噪音会让人无视一道门，查不到则直接让替代方案不成立。

离线可跑：只调本地 git，不联网、不碰 DB。
"""
from __future__ import annotations

from scripts import history_cli as hc


def test_eras_are_annotated_tags_with_narrative_body() -> None:
    """时期叙事必须**在 tag message 里**，不能只是一个指向文件的链接。"""
    rows = hc.eras()
    assert rows, "无 annotated tag = 时期导航丢失"
    era_rows = [r for r in rows if r["tag"].startswith("era/")]
    assert era_rows, "P3.3 迁入的 era/* tag 不见了"
    assert any(len(r["text"]) > 100 for r in era_rows), "tag 里只有标题没有叙事正文"


def test_grep_finds_narrative_that_lives_only_in_a_tag() -> None:
    """回归锁：时期叙事的全文必须在 tag 面搜得到。

    只锁「tag 面有」，不锁「commit 面没有」—— 后者是过度指定：一条讨论该时期的
    commit message 里出现同样的词完全正常（本测试首版就是这么误判红的）。
    """
    hits = [e for e in hc.eras() if "地基 reset" in (e["tag"] + "\n" + e["text"])]
    assert hits, "「地基 reset」这段叙事丢了"
    assert "必要清障" in hits[0]["text"], "tag 里只剩标题，叙事正文没迁过来"


def test_grep_finds_per_knife_detail_in_commit_messages() -> None:
    rows = hc.search(grep=["cutover"], limit=10)
    assert rows, "逐刀检索无命中 —— git log 面失效"
    assert all(r["hash"] and r["date"] and r["subject"] for r in rows)
    assert all(not r["body"] for r in rows), "未要 --full 时不该带正文"


def test_full_returns_commit_body() -> None:
    rows = hc.search(grep=["cutover"], limit=5, full=True)
    assert any(r["body"] for r in rows), "--full 必须给出 message 正文"


def test_main_requires_a_query() -> None:
    """不给条件就列全历史 = 又变成「整读账本」，正是要消灭的用法。"""
    import pytest

    with pytest.raises(SystemExit):
        hc.main([])


def test_main_paths_exit_zero() -> None:
    assert hc.main(["--eras"]) == 0
    assert hc.main(["--grep", "cutover", "--limit", "3"]) == 0
