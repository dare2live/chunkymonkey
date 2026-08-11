"""FEATURE_MAP 漂移判定体 (`_body`) 的边界。

2026-08-11 实测反例：全量 codegraph 索引与增量索引的 calls 边数不同
(12,619 vs 12,434)，top-N 排名尾部因此翻转 —— worktree 说「无漂移」而
safe_commit 的 fresh 快照说「漂移」，连续两刀假红。计数行早就被排除在判定之外，
排名表与它同源同因，也必须排除；但**真正的源变化仍须被抓到**，两个方向都锁。
"""
from __future__ import annotations

from scripts.build_feature_map import _body


def _doc(*, hot_rows: str, writer_row: str) -> str:
    return "\n".join(
        [
            "# FEATURE_MAP",
            "> Snapshot: 2026-08-11 09:01",
            "",
            "## 3. 产表 writer",
            "",
            f"| {writer_row} |",
            "",
            "## 4. 依赖热点 (codegraph 派生)",
            "",
            "> Codegraph: 节点 10,734 | calls 边 12,434 | imports 边 3,582 (波动)",
            "",
            "### 被 import 最多的模块 (top 15)",
            "",
            "| 模块 | import 处数 |",
            "|---|---|",
            f"| {hot_rows} |",
            "",
            "### LOC top 10 (God module 候选)",
            "",
            "| 文件 | 行数 |",
            "|---|---|",
            "| backend/services/x.py | 100 |",
            "",
            "## 5. 概览",
            "",
            "- 产表 33",
        ]
    )


def test_codegraph_ranking_rows_do_not_count_as_drift() -> None:
    a = _doc(hot_rows="services.a | 7", writer_row="fact_x | writer_a.py")
    b = _doc(hot_rows="frontend/src/Card.tsx | 8", writer_row="fact_x | writer_a.py")
    assert a != b
    assert _body(a) == _body(b), "索引口径差异不该被判成地图漂移"


def test_real_source_change_still_counts_as_drift() -> None:
    a = _doc(hot_rows="services.a | 7", writer_row="fact_x | writer_a.py")
    b = _doc(hot_rows="services.a | 7", writer_row="fact_x | writer_b.py")
    assert _body(a) != _body(b), "writer 归属变了必须仍然红"


def test_sections_after_the_volatile_block_survive() -> None:
    body = _body(_doc(hot_rows="services.a | 7", writer_row="fact_x | writer_a.py"))
    assert "## 5. 概览" in body
    assert "### LOC top 10 (God module 候选)" in body
    assert "backend/services/x.py | 100" in body
    assert "被 import 最多的模块" not in body
    assert "> Codegraph:" not in body
    assert "> Snapshot:" not in body
