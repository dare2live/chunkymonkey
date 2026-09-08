"""tdxhub 解码量的值域校验 — 物理不可能的成交量不许落库。

2026-09-08: canonical_nominal_ohlcv_daily 出现 6 行 (2026-08-31, 通达信首批供货)
vol = 5.877471754111438e-39 = 2**-127。追到 sibling repo tdxhub 的
protocol/helper.py:get_volume() —— 通达信私有浮点的反汇编逐字转写, 对**全零字段**
(= 这根 bar 没有成交) 返回 2**-127 而不是 0.0, 隐含前导 1 那一项在 logpoint=0 时
没被抑制。上游已修 (tdxhub e2516d5)。

本文件锁的是**独立于上游修复**的那道防线: 任何供货商、任何未来解码路径产出物理不可能
的量, 都必须响亮失败, 而不是落库后被下游的 "非零" 过滤条件静默滤掉 —— 后者与
"停牌整行缺失" 完全同形, 事后无法区分。
"""
from __future__ import annotations

from datetime import date

import pytest

from services.data_sources.sources.tdxhub import (
    TdxhubDailyBatchError,
    _reject_impossible_quantity,
)

DAY = date(2026, 8, 31)


def _check(vol: float) -> float:
    return _reject_impossible_quantity(vol=vol, ts_code="000635.SZ", target=DAY)


@pytest.mark.parametrize("vol", [39299.0, 145343.0, 1.0, 1e9])
def test_real_volumes_pass(vol: float) -> None:
    assert _check(vol) == vol


def test_legitimate_zero_passes() -> None:
    """0 手是合法的: 停牌日确实没有成交。

    这一条是本文件最容易写错的地方 —— 把"零"和"伪影"一起拒掉, 就等于强迫适配器
    对停牌日抛异常, 那会让整批同步失败。零是真值, (0,1) 手才是不可能。
    """
    assert _check(0.0) == 0.0


def test_the_historical_artifact_is_rejected() -> None:
    """点名那个具体的坏值。用精确常量而非 approx: 2**-127 落在 approx 默认
    abs=1e-12 之内, 用 approx 断言等于白写(写 tdxhub 那边的测试时真踩过一次)。"""
    with pytest.raises(TdxhubDailyBatchError) as caught:
        _check(5.877471754111438e-39)
    assert "below_one_lot" in str(caught.value)
    assert "000635.SZ" in str(caught.value)
    assert "2026-08-31" in str(caught.value)


@pytest.mark.parametrize("vol", [0.5, 0.999, 1e-9, 2.0 ** -127])
def test_sub_lot_quantities_are_rejected(vol: float) -> None:
    """A 股最小成交 1 手。(0,1) 区间物理上不可能, 无论它是哪种伪影。"""
    with pytest.raises(TdxhubDailyBatchError):
        _check(vol)


def test_negative_and_nan_are_rejected() -> None:
    with pytest.raises(TdxhubDailyBatchError) as neg:
        _check(-1.0)
    assert "negative" in str(neg.value)
    with pytest.raises(TdxhubDailyBatchError) as nan:
        _check(float("nan"))
    assert "nan" in str(nan.value)


def test_error_names_the_row_not_just_the_value() -> None:
    """红了要说得清拦的是哪一行 —— 本仓门失效的典型形态是"红了却说不清拦你什么"。"""
    with pytest.raises(TdxhubDailyBatchError) as caught:
        _reject_impossible_quantity(vol=0.5, ts_code="601398.SH", target=date(2019, 1, 2))
    msg = str(caught.value)
    assert "601398.SH" in msg and "2019-01-02" in msg and "0.5" in msg
