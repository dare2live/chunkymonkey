"""institution_profile 状态机单测 — 成本/收益正确性证伪门 (promote 纪律)。

覆盖: 新进→增持(加权平均成本)→减持(部分了结)→退出(清仓) 全链数值 + seeded/多轮 episode/无价跳过。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.institution_profile import run_episode_state_machine


def _row(holder="H", stock="600000", period="20240331", status="新进", is_exit=False,
         shares=None, chg=None, htype="QFII", notice="20240430", c1=10.0, c2=10.0, c3=10.0):
    return (holder, stock, period, status, is_exit, shares, chg, htype, notice, c1, c2, c3)


def test_full_lifecycle_weighted_cost_and_realized():
    """新进1000股@10 → 增持1000股@20 (成本加权→15) → 减持500股@30 (realized+7500) → 退出@40 (剩1500股×25)。"""
    rows = [
        _row(period="20240331", status="新进", shares=1000, c1=10.0, c2=10.0, c3=10.0),
        _row(period="20240630", status="增持", shares=2000, chg=1000, c1=20.0, c2=20.0, c3=20.0),
        _row(period="20240930", status="减持", shares=1500, chg=-500, c1=30.0, c2=30.0, c3=30.0),
        _row(period="20241231", status="退出", is_exit=True, shares=1500, chg=-1500, c1=40.0, c2=40.0, c3=40.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 1, "closed": 1, "seeded": 0, "no_price_skip": 0}
    ep = eps[0]
    assert ep["status"] == "closed"
    assert ep["cost_c1"] == pytest.approx(15.0)          # (1000×10 + 1000×20) / 2000
    assert ep["peak_shares"] == 2000
    # realized = 减持 500×(30−15)=7500 + 退出 1500×(40−15)=37500 = 45000
    assert ep["realized_c1"] == pytest.approx(45000.0)
    # 收益口径: realized/(cost×peak) = 45000/(15×2000) = 150%
    assert ep["realized_c1"] / (ep["cost_c1"] * ep["peak_shares"]) == pytest.approx(1.5)


def test_seeded_from_change_flagged():
    """首见即增持 (前期第11名进前十) → 按新进开仓且 seeded=True (画像排除)。"""
    rows = [_row(status="增持", shares=800, chg=300, c1=12.0)]
    eps, stats = run_episode_state_machine(rows)
    assert stats["seeded"] == 1
    assert eps[0]["seeded"] is True and eps[0]["status"] == "holding"
    assert eps[0]["cost_c1"] == 12.0 and eps[0]["shares"] == 800


def test_multi_round_episodes_same_holder_stock():
    """退出后再新进 = 独立新 episode (易方达×茅台 3 轮实证形态)。"""
    rows = [
        _row(period="20230331", status="新进", shares=100, c1=10.0),
        _row(period="20230630", status="退出", is_exit=True, shares=100, chg=-100, c1=12.0),
        _row(period="20240331", status="新进", shares=200, c1=8.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats == {"opened": 2, "closed": 1, "seeded": 0, "no_price_skip": 0}
    closed = [e for e in eps if e["status"] == "closed"][0]
    holding = [e for e in eps if e["status"] == "holding"][0]
    assert closed["realized_c1"] == pytest.approx(100 * 2.0)
    assert holding["cost_c1"] == 8.0 and holding["shares"] == 200


def test_no_price_skips_event_not_episode():
    """窗口无K线 (c1=None) 的事件跳过, 不影响已开 episode。"""
    rows = [
        _row(period="20240331", status="新进", shares=100, c1=10.0),
        _row(period="20240630", status="增持", shares=200, chg=100, c1=None),  # 停牌窗口
        _row(period="20240930", status="退出", is_exit=True, shares=100, chg=-100, c1=20.0),
    ]
    eps, stats = run_episode_state_machine(rows)
    assert stats["no_price_skip"] == 1
    ep = eps[0]
    # 增持事件被跳过 → shares 仍 100, 成本仍 10, 退出 realized=100×10
    assert ep["shares"] == 100 and ep["cost_c1"] == 10.0
    assert ep["realized_c1"] == pytest.approx(1000.0)


def test_exit_without_open_is_noop():
    """无开仓的孤儿退出行 (数据起点截断) → 不产生 episode。"""
    eps, stats = run_episode_state_machine([_row(status="退出", is_exit=True, shares=100, chg=-100)])
    assert eps == [] and stats["closed"] == 0
