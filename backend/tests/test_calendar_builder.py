"""calendar_builder 单测 (R1 件1, 2026-07-03; R2 换源 2026-09-01)。

锁: (1) accepted canonical (最新 generation) → dim_trading_calendar 增量正确性
(watermark 语义, 只延伸不回填前史); (2) DATE/整数 canonical → ISO VARCHAR dim 转换 +
SSE/is_open=1 过滤; (3) accepted 指针缺失 / 该 generation 无内容 fail loud;
(4) 多代际隔离 —— 只认 accepted_partition 最新 accepted_at 指向的 generation_id,
不受同表里其它 (通常更早) generation 的行污染。

日历 horizon 门 (前瞻余量 red-green) 2026-07-06 迁至
backend/tests/scripts/test_check_continuity_integrity.py (孤儿 data_quality.py 整体退役,
horizon 逻辑真正接进 check_continuity_integrity.py 的 calendar_horizon 检测类型)。

R2 (2026-09-01): legacy raw_tushare_trade_cal 中间层退役 —— 全仓零生产 writer, 只有本文件
过去在写它, 是断链点本身。数据源改为 accepted canonical_sse_trading_calendar_generation,
经 accepted_partition (dataset_id + 最新 accepted_at) 选取权威 generation_id 指针, 不用
max(generation_id) 字符串排序 (那是命名巧合非指针语义)。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services import calendar_builder as cb
from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.calendar_schema import CANONICAL_TABLE, DATASET_ID

_CANONICAL_DDL = (
    f"CREATE TABLE {CANONICAL_TABLE} "
    "(generation_id VARCHAR, exchange VARCHAR, cal_date DATE, is_open INTEGER)"
)
_ACCEPTED_DDL = (
    f"CREATE TABLE {ACCEPTED_TABLE} "
    "(dataset_id VARCHAR, partition_value VARCHAR, batch_id VARCHAR, "
    "accepted_at TIMESTAMP)"
)
_DIM_DDL = ("CREATE TABLE dim_trading_calendar "
            "(trade_date VARCHAR NOT NULL, is_trading BIGINT, PRIMARY KEY(trade_date))")

_GEN = "trade_cal:SSE:19901219_20261231:20260831T154506Z"
_BASE_ACCEPTED_AT = datetime(2026, 8, 31, 23, 45, 7, tzinfo=timezone.utc)


def _conn(*, with_dim: bool = True):
    c = duck_mem()
    c.execute(_CANONICAL_DDL)
    c.execute(_ACCEPTED_DDL)
    if with_dim:
        c.execute(_DIM_DDL)
    return c


def _accept(c, generation_id: str, *, accepted_at: datetime) -> None:
    """注册一个 accepted 指针 (accepted_partition 行)。"""
    c.execute(
        f"INSERT INTO {ACCEPTED_TABLE} VALUES (?, ?, ?, ?)",
        [DATASET_ID, generation_id, generation_id, accepted_at],
    )


def _canonical_rows(c, generation_id: str, rows: list[tuple[str, str, int]]) -> None:
    """rows: [(exchange, 'YYYY-MM-DD', is_open), ...]"""
    c.executemany(
        f"INSERT INTO {CANONICAL_TABLE} VALUES (?, ?, ?, ?)",
        [(generation_id, exch, date.fromisoformat(d), is_open) for exch, d, is_open in rows],
    )


def test_bootstrap_full_copy_iso_and_filters():
    """dim 空 → 全量拷贝最新 accepted generation 的 SSE is_open=1 行;
    DATE→ISO VARCHAR; SZSE 行 / is_open=0 行不入。"""
    c = _conn()
    try:
        _accept(c, _GEN, accepted_at=_BASE_ACCEPTED_AT)
        _canonical_rows(c, _GEN, [
            ("SSE", "2026-07-02", 1), ("SSE", "2026-07-03", 1),
            ("SSE", "2026-07-04", 0),               # 非交易日不入 (只存交易日语义)
            ("SZSE", "2026-07-02", 1),              # 非 SSE 不入 (统一上交所口径)
            ("SSE", "2026-07-06", 1),
        ])
        out = cb.build_latest(conn=c)
        assert out["inserted"] == 3 and out["watermark_before"] is None
        assert out["generation_id"] == _GEN
        rows = c.execute(
            "SELECT trade_date, is_trading FROM dim_trading_calendar ORDER BY trade_date").fetchall()
        assert [r[0] for r in rows] == ["2026-07-02", "2026-07-03", "2026-07-06"]  # ISO 格式
        assert all(isinstance(r[0], str) for r in rows), "dim.trade_date 必须是 VARCHAR, 不是 DATE"
        assert all(r[1] == 1 for r in rows)
        assert out["dim_max"] == "2026-07-06" and out["raw_max_trading"] == "2026-07-06"
    finally:
        c.close()


def test_incremental_repairs_holes_without_prehistory_backfill():
    """从既有 dim 起点幂等补洞并延伸；更早 accepted 前史仍不回填。"""
    c = _conn()
    try:
        c.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)",
                      [("2026-07-02",), ("2026-07-03",)])
        _accept(c, _GEN, accepted_at=_BASE_ACCEPTED_AT)
        _canonical_rows(c, _GEN, [
            ("SSE", "1990-12-19", 1),               # 前史: accepted 有但 dim 契约起点后 → 不回填
            ("SSE", "2026-07-02", 1), ("SSE", "2026-07-03", 1),
            ("SSE", "2026-07-04", 1),               # 中段漏传: 应由自修复补上
            ("SSE", "2026-07-06", 1), ("SSE", "2026-07-07", 1),   # 新日: 延伸
        ])
        out = cb.build_latest(conn=c)
        assert out["inserted"] == 3 and out["watermark_before"] == "2026-07-03"
        assert out["dim_max"] == "2026-07-07" and out["dim_rows"] == 5
        assert c.execute(
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE trade_date < '2026-07-02'"
        ).fetchone()[0] == 0, "watermark 语义: 前史不回填 (回填走人工 rebuild)"
        out2 = cb.build_latest(conn=c)   # 幂等重跑
        assert out2["inserted"] == 0 and out2["dim_rows"] == 5
    finally:
        c.close()


def test_missing_accepted_pointer_fails_loud():
    """accepted_partition 无匹配 dataset_id 的行 (从未 accept 过任何 generation) → raise,
    拒绝静默 no-op 假装刷新。"""
    c = duck_mem()
    c.execute(_CANONICAL_DDL)
    c.execute(_ACCEPTED_DDL)   # 建空表 (与生产同 schema), 避免触发真实 ATTACH
    c.execute(_DIM_DDL)
    try:
        with pytest.raises(RuntimeError, match="无.*accepted 指针"):
            cb.build_latest(conn=c)
        # 有行但 dataset_id 不匹配同样算无指针
        c.execute(
            f"INSERT INTO {ACCEPTED_TABLE} VALUES (?, ?, ?, ?)",
            ["some.other.dataset", "x", "x", _BASE_ACCEPTED_AT],
        )
        with pytest.raises(RuntimeError, match="无.*accepted 指针"):
            cb.build_latest(conn=c)
    finally:
        c.close()


def test_generation_with_no_sse_trading_rows_fails_loud():
    """accepted 指针存在但指向的 generation 无 SSE is_open=1 行 (只有 closed 行 / 只有非 SSE
    交易所行) → raise, 而不是把 dim 静默冻结在旧状态。"""
    c = _conn()
    try:
        _accept(c, _GEN, accepted_at=_BASE_ACCEPTED_AT)
        _canonical_rows(c, _GEN, [("SZSE", "2026-07-02", 1)])   # 只有非 SSE 行
        with pytest.raises(RuntimeError, match="无 SSE is_open=1 行"):
            cb.build_latest(conn=c)
        c.execute(f"DELETE FROM {CANONICAL_TABLE}")
        _canonical_rows(c, _GEN, [("SSE", "2026-07-04", 0)])    # 只有非交易日行
        with pytest.raises(RuntimeError, match="无 SSE is_open=1 行"):
            cb.build_latest(conn=c)
    finally:
        c.close()


def test_builder_removes_dim_day_when_raw_revises_open_to_closed():
    """自修复必须双向收敛；只 ON CONFLICT insert 无法修正 open→closed。"""
    c = _conn()
    try:
        c.execute("INSERT INTO dim_trading_calendar VALUES ('2026-07-15', 1)")
        _accept(c, _GEN, accepted_at=_BASE_ACCEPTED_AT)
        _canonical_rows(c, _GEN, [
            ("SSE", "2026-07-14", 1),
            ("SSE", "2026-07-15", 0),
            ("SSE", "2026-07-16", 1),
        ])
        out = cb.build_latest(conn=c)
        assert out["deleted"] == 1
        assert c.execute(
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE trade_date = '2026-07-15'"
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE trade_date = '2026-07-16'"
        ).fetchone()[0] == 1
    finally:
        c.close()


def test_multi_generation_isolation_only_latest_accepted_counts():
    """canonical 是 append-only 多代际表: 库里有一个已废弃的老代际 (含一个新代际里已被撤销的
    交易日) 和一个新代际 (accepted_at 更晚)。dim 必须只反映最新 accepted 代际, 不受老代际
    行污染 —— 这是本次改造最容易错的地方 (曾经的候选实现是 max(generation_id) 字符串排序或
    整表不分代 SELECT, 两者都会让老代际的行泄漏进 dim)。"""
    c = _conn()
    try:
        old_gen = "trade_cal:SSE:19901219_20261231:20260719T131257Z"
        new_gen = _GEN
        # 老代际: 误把 2026-07-20 标成交易日 (后续被订正)
        _canonical_rows(c, old_gen, [
            ("SSE", "2026-07-17", 1),
            ("SSE", "2026-07-20", 1),   # 只在老代际存在的"幽灵"交易日
        ])
        # 新代际: 订正后 2026-07-20 不是交易日, 且延伸到 07-21
        _canonical_rows(c, new_gen, [
            ("SSE", "2026-07-17", 1),
            ("SSE", "2026-07-20", 0),
            ("SSE", "2026-07-21", 1),
        ])
        # accepted_partition: 老代际先被 accept, 新代际后被 accept (accepted_at 更晚)
        _accept(c, old_gen, accepted_at=_BASE_ACCEPTED_AT - timedelta(days=43))
        _accept(c, new_gen, accepted_at=_BASE_ACCEPTED_AT)

        out = cb.build_latest(conn=c)
        assert out["generation_id"] == new_gen, "必须选中 accepted_at 最新的指针, 不是随便一个"
        rows = {r[0] for r in c.execute(
            "SELECT trade_date FROM dim_trading_calendar").fetchall()}
        assert rows == {"2026-07-17", "2026-07-21"}
        assert "2026-07-20" not in rows, (
            "幽灵交易日只存在于老代际; 若代际选取退化为 max(generation_id) 字符串排序或"
            "整表不分代, 这行会错误地泄漏进 dim"
        )
    finally:
        c.close()


def test_multi_generation_isolation_survives_rerun_after_new_generation_accepted():
    """dim 已从老代际建好后, 新代际被 accept 且订正了一天 → 重跑必须收敛到新代际, 不能因为
    '已经有该日期的 dim 行' 就跳过订正 (open→closed 收敛必须跨代际生效)。"""
    c = _conn()
    try:
        old_gen = "trade_cal:SSE:19901219_20261231:20260719T131257Z"
        new_gen = _GEN
        _canonical_rows(c, old_gen, [("SSE", "2026-07-20", 1)])
        _accept(c, old_gen, accepted_at=_BASE_ACCEPTED_AT - timedelta(days=43))
        out1 = cb.build_latest(conn=c)
        assert out1["generation_id"] == old_gen
        assert c.execute(
            "SELECT is_trading FROM dim_trading_calendar WHERE trade_date = '2026-07-20'"
        ).fetchone()[0] == 1

        # 新代际发布, 订正 07-20 为非交易日 (同时带一个真实交易日, 避免这份最小化 fixture
        # 意外撞上"generation 无 SSE is_open=1 行"的 fail-loud 门 —— 真实 generation 总有
        # 大量交易日, 这里只是最小化复现订正语义)
        _canonical_rows(c, new_gen, [("SSE", "2026-07-20", 0), ("SSE", "2026-07-21", 1)])
        _accept(c, new_gen, accepted_at=_BASE_ACCEPTED_AT)
        out2 = cb.build_latest(conn=c)
        assert out2["generation_id"] == new_gen
        assert c.execute(
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE trade_date = '2026-07-20'"
        ).fetchone()[0] == 0, "新代际订正必须跨代际收敛, 不能因 dim 已有该日就跳过"
    finally:
        c.close()
