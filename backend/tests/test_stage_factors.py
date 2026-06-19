"""主升浪 stage 因子 PIT 正确性单测 (2026-06-19, A0 地基止血 #1).

证: feature_momentum / feature_moneyflow_trend / feature_asof_quality 全 PIT (feat[i] 只用 <=i),
warmup 不足返 None, 财务 as-of 严守 ann_date<=决策日 (公告前不可见)。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.formula_engine.features import (  # noqa: E402
    feature_asof_quality,
    feature_momentum,
    feature_moneyflow_trend,
)


def test_momentum_pit_and_warmup():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    out = feature_momentum(closes, window=2)
    assert out[0] is None and out[1] is None        # warmup 不足
    assert abs(out[2] - (12.0 / 10.0 - 1.0)) < 1e-9  # close[2]/close[0]-1, 全 <=i (PIT)
    assert abs(out[4] - (14.0 / 12.0 - 1.0)) < 1e-9


def test_momentum_none_zero_guard():
    closes = [None, 0.0, 12.0, 13.0]
    out = feature_momentum(closes, window=2)
    assert out[2] is None   # close[0]=None
    assert out[3] is None   # close[1]=0


def test_moneyflow_trend_pit_and_flow_guard():
    net = [1.0, 2.0, -1.0, 3.0]
    flow = [10.0, 10.0, 10.0, 10.0]
    out = feature_moneyflow_trend(net, flow, window=2)
    assert out[0] is None                            # warmup
    assert abs(out[1] - (3.0 / 20.0)) < 1e-9         # (1+2)/(10+10), 全 <=i
    assert abs(out[2] - (1.0 / 20.0)) < 1e-9         # (2-1)/(10+10)
    # flow 总和 <=0 -> None
    out2 = feature_moneyflow_trend([1.0, 1.0], [0.0, 0.0], window=2)
    assert out2[1] is None


def test_asof_quality_pit_announcement_boundary():
    dates = ["2024-03-01", "2024-04-30", "2024-05-01"]
    # 报告 ann_date=2024-04-30 (披露日), end_date=2024-03-31 (Q1)
    reports = [("2024-04-30", "2024-03-31", 0.15)]
    out = feature_asof_quality(dates, reports)
    assert out[0] is None              # 2024-03-01 公告前 = 不可见 (PIT 核心)
    assert abs(out[1] - 0.15) < 1e-9   # 2024-04-30 = 公告日, 可见
    assert abs(out[2] - 0.15) < 1e-9   # 2024-05-01 之后持续可见


def test_asof_quality_revision_override():
    dates = ["2024-09-01"]
    # 同 end_date(Q1) 后到的 ann_date 覆盖 (财报修订)
    reports = [("2024-04-30", "2024-03-31", 0.15), ("2024-08-20", "2024-03-31", 0.18)]
    out = feature_asof_quality(dates, reports)
    assert abs(out[0] - 0.18) < 1e-9   # 取已披露最新修订值
