"""on_demand 短窗域的有界缺口自愈。

2026-08-21 实证代价: daily/stock_st 结构上只补 eligible_end 那一天, 不回头看;
而这两个域又拒绝无参数 --drain(授权短窗)。链停跑几天后再跑一次 daily_update,
跑完两个域各自仍缺 20260817-20260820 四个交易日, 连续性门 fail=25 ——
洞只能靠人看门再手工 --start/--end 补。
"""
from __future__ import annotations

import duckdb
import pytest

from services.pipeline import acquire


TRADING = [
    "20260806", "20260807", "20260810", "20260811", "20260812",
    "20260813", "20260814", "20260817", "20260818", "20260819", "20260820",
]
DATASET = "tier0.market_data.nominal_ohlcv_daily"


@pytest.fixture()
def conn(monkeypatch):
    c = duckdb.connect(":memory:")
    c.execute("create table accepted_partition (dataset_id VARCHAR, partition_value VARCHAR)")
    monkeypatch.setattr(
        acquire, "_accepted_partition_exists",
        lambda cn, ds, pv: cn.execute(
            "select 1 from accepted_partition where dataset_id=? and partition_value=? limit 1",
            [ds, pv],
        ).fetchone() is not None,
    )

    return c


def _days(start, end):
    """假交易日历 —— 注入用, 不碰任何数据库。"""
    return [d for d in TRADING if start <= d <= end]


def _accept(conn, days):
    for d in days:
        conn.execute("insert into accepted_partition values (?, ?)", [DATASET, d])


def test_finds_the_days_that_were_never_accepted(conn):
    """漏跑的那几天必须被找出来 —— 这正是链跑完仍缺 4 天的那个场景。"""
    _accept(conn, [d for d in TRADING if d not in ("20260817", "20260818", "20260819", "20260820")])

    holes = acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=10,
        trading_days_fn=_days,
    )

    assert holes == ["20260817", "20260818", "20260819", "20260820"], holes


def test_no_holes_when_everything_is_accepted(conn):
    _accept(conn, TRADING)

    holes = acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=10,
        trading_days_fn=_days,
    )

    assert holes == [], holes


def test_window_bounds_the_heal_so_history_still_needs_an_explicit_backfill(conn):
    """窗口之外的洞**不**自愈 —— 原设计 log-not-fill / explicit backfill knife 不被推翻。

    只覆盖"漏跑几天"这一运维场景; 更早的洞仍由连续性门报出、走显式回填。
    """
    # 只接受最后 3 天, 更早的全缺
    _accept(conn, TRADING[-3:])

    narrow = acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=3,
        trading_days_fn=_days,
    )
    wide = acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=len(TRADING),
        trading_days_fn=_days,
    )

    assert narrow == [], narrow                      # 窗口内全已接受
    assert len(wide) == len(TRADING) - 3, wide       # 放宽窗口才看得见历史洞
    assert "20260806" in wide


def test_zero_window_disables_healing(conn):
    """窗口为 0 = 关闭自愈, 行为退回原来的'只补最新日'。"""
    _accept(conn, [])

    assert acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=0,
        trading_days_fn=_days,
    ) == []


def test_known_empty_days_are_not_healed_forever(conn):
    """源端实测真空的日子不参与自愈, 否则每天重拉一次永不收敛。

    drain_domain 早就为同一件事写过警告(cyq_perf 20260615 仅 1 股):
    墓碑日排出 expected, 既不当缺口也不制造永久 partial 告警。自愈路径必须守同一条线。
    """
    _accept(conn, [d for d in TRADING if d not in ("20260818", "20260819")])

    without = acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=10,
        trading_days_fn=_days,
    )
    with_tomb = acquire._recent_unaccepted_days(
        conn, DATASET, eligible_end="20260820", window=10,
        known_empty={"20260818"}, trading_days_fn=_days,
    )

    assert without == ["20260818", "20260819"], without
    assert with_tomb == ["20260819"], with_tomb
