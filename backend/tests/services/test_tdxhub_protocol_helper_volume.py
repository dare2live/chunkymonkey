"""``tdxhub.protocol.helper.get_volume`` 的零点回归锁。

2026-09-09 从上游 dare2live/tdxhub@e2516d5 的 ``tests/test_protocol_helper_volume.py``
搬入 —— 并入 backend/tdxhub 时只带了 package 不带 tests/, 于是
``backend/tdxhub/protocol/helper.py`` 的 docstring 指向了一个本仓不存在的路径, 而这个
解码器**没有任何本仓测试**直接锁它。

与 ``test_tdxhub_volume_range_guard.py`` 守的不是同一件事, 两者不能互相替代:
  - 那个测 ``_reject_impossible_quantity`` —— **消费边界**上的不变量: 任何源、任何解码
    路径产出物理不可能的量都要响亮失败。它不 import get_volume, 所以解码器回归了它在
    提交时抓不到, 只会在生产 sync 时以整批异常炸出来。
  - 本文件测 ``get_volume`` 本身 —— **解码器正确性**。回归在提交时就红, 且直接指向
    helper.py 而不是指向某一批数据。

2026-09-08: 本仓 ``canonical_nominal_ohlcv_daily`` 里出现 6 行
(2026-08-31 的停牌股) ``vol == 5.877471754111438e-39``。追下来正是本函数对**全零输入**
的返回值 —— 通达信私有浮点里那个"隐含前导 1"项 (``dbl_xmm6``) 在 logpoint=0 时算出
``2 ** -127`` 并被无条件累加, 于是"这根 bar 没有成交"被解成一个物理上不可能却是有限正数
的量, 一路穿过下游 ``float()`` 落库, 再被"非零"过滤条件静默滤掉。

第一个用例在修复前会红 (返回 2**-127 而非 0.0); 其余用例锁住"修零点没有动到
任何非零输入的解码结果"。
"""
import pytest

from tdxhub.protocol.helper import get_volume


def test_zero_field_decodes_to_zero_not_denormal():
    """全零 = 没有成交。修复前这里返回 2**-127。"""
    assert get_volume(0) == 0.0
    assert get_volume(0) is not None


def test_zero_result_is_not_the_historical_artifact():
    """点名那个具体的坏值, 免得将来有人"顺手"把零点改回去。

    **必须精确比较, 不能用 pytest.approx**: approx 的默认绝对容差是 1e-12, 而坏值
    2**-127 ≈ 5.9e-39 远在容差之内 —— ``0.0 == approx(5.877e-39)`` 会成立, 这条断言
    就变成永远通过。用 approx 去区分一个非规格化数, 正好踩了本 bug 自己的坑
    (写这个文件时真的踩了一次, 由本行注释留证)。
    """
    got = get_volume(0)
    assert got == 0.0
    assert got != 5.877471754111438e-39
    assert got != 2.0 ** -127


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0x40000000, 2.0),
        (0x41000000, 8.0),
        (0x4C000000, 33554432.0),
    ],
)
def test_known_nonzero_encodings_unchanged(raw, expected):
    """指数字节主导的几个标定点 —— 修零点不得影响它们。"""
    assert get_volume(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [1, 2, 0xFF, 0x01000000, 0x7FFFFFFF, 0xFFFFFFFF])
def test_nonzero_inputs_stay_finite_and_positive(raw):
    """非零输入一律仍走原路径: 有限、正、且不等于零点的 0.0。

    刻意不钉具体数值 —— 那是运行时测量值, 钉了只会在无关改动时要人改数字;
    这里断言的是"零点分支没有误吞非零输入"这个不变量。
    """
    got = get_volume(raw)
    assert got > 0.0
    assert got == got  # not NaN
    assert got != float("inf")


def test_volume_is_monotonic_in_exponent_byte():
    """指数字节递增 -> 量级递增。锁住解码语义没被改坏(而不是锁某个具体数)。"""
    vals = [get_volume(e << 24) for e in range(0x40, 0x50)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
