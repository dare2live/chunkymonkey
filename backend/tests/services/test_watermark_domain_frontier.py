"""_last_watermark_date 按域聚合单测 (2026-09-02 换源读空事故根治).

背景: 读侧原实现 `WHERE data_domain = ? AND source_name = ?`，第二参数传当前 registry
source。域换源后水位表里的行仍挂在旧源名下，新源读回 None，三个批处理分支
`start_d = start or wm or spec["data_start"]` 就回落到 data_start，触发全史重拉
(daily 实测 1858 交易日 ≈ 310 小时)。水位语义是"该域推进到哪天"——与谁供货无关
(MERGE 幂等、按域消费)，供货者证据留在写入行的 source_name 里，不参与读取身份。
修法: 读侧去掉 source 谓词，改按域 MAX 聚合。

本门锁定 (使用 tmp duckdb fixture, 不碰宿主库):
1. 换源场景: 旧源行 non-null + 新源行 null → 仍能读到旧源行的日期 (核心回归锁)。
2. 反向自证: 旧的按 source 过滤实现在同一 fixture 上必须返回 None —— 证明本测试
   确实能红，不是巧合绿。
3. 多源取最大 (非仅"换源单向前进"这一种形状)。
4. 空表返回 None。
5. 日期格式归一 (带横杠 → 8 位无横杠)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema


def _insert_watermark(conn, domain: str, source: str, last_data_date: str | None) -> None:
    # source_tier 是 PK 一部分 + NOT NULL, 用 sync_runner 里同一个 tushare tier 常量,
    # 每行不同 source_name 已足够构成"多行"场景, tier 本身不是本门要测的维度。
    conn.execute(
        """
        INSERT INTO mart_data_source_watermark
            (data_domain, source_name, source_tier, last_data_date)
        VALUES (?, ?, ?, ?)
        """,
        [domain, source, sr.SOURCE_TIER_TUSHARE, last_data_date],
    )


def _legacy_source_filtered_read(domain: str, source: str, conn) -> str | None:
    """旧实现的忠实复刻 (按 source_name 过滤) —— 只用于反向自证本门能红。"""
    row = conn.execute(
        "SELECT last_data_date FROM mart_data_source_watermark "
        "WHERE data_domain = ? AND source_name = ?",
        [f"sync:{domain}", source],
    ).fetchone()
    return str(row[0]).replace("-", "") if row and row[0] else None


def test_switched_source_still_reads_old_source_row():
    """核心回归锁: 域换源后, 旧源行仍是唯一有日期的行, 新读法必须仍能拿到它,
    不能因为当前 source 变了就读空。"""
    conn = duck_mem()
    ensure_source_watermark_schema(conn)
    _insert_watermark(conn, "sync:x", "tushare", "20260828")
    _insert_watermark(conn, "sync:x", "miaoxiang", None)

    assert sr._last_watermark_date("x", conn) == "20260828"
    conn.close()


def test_source_filtered_legacy_implementation_regresses_to_none():
    """反向自证: 同一份 fixture 喂给旧的 (按 source 过滤) 实现必须返回 None ——
    证明这条门确实检测得到换源读空这个 bug, 不是碰巧全绿。"""
    conn = duck_mem()
    ensure_source_watermark_schema(conn)
    _insert_watermark(conn, "sync:x", "tushare", "20260828")
    _insert_watermark(conn, "sync:x", "miaoxiang", None)

    assert _legacy_source_filtered_read("x", "miaoxiang", conn) is None
    conn.close()


def test_multiple_sources_take_the_max_date():
    """同域多行不同日期 (不只是"新行是 null"这一种形状) 必须取较大者。"""
    conn = duck_mem()
    ensure_source_watermark_schema(conn)
    _insert_watermark(conn, "sync:y", "old_source", "20240101")
    _insert_watermark(conn, "sync:y", "new_source", "20260828")

    assert sr._last_watermark_date("y", conn) == "20260828"
    conn.close()


def test_empty_table_returns_none():
    conn = duck_mem()
    ensure_source_watermark_schema(conn)

    assert sr._last_watermark_date("nonexistent", conn) is None
    conn.close()


def test_dashed_date_is_normalized_without_dashes():
    conn = duck_mem()
    ensure_source_watermark_schema(conn)
    _insert_watermark(conn, "sync:z", "tushare", "2026-08-28")

    assert sr._last_watermark_date("z", conn) == "20260828"
    conn.close()
