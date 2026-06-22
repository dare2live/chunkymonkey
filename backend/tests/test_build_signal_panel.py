"""build_signal_panel (b分表 事件信号面) 单测 — 数据模块 v2 §8.0。

重点: macd_golden_cross **PIT 无前视** (EMA 递推只用 ≤i; 截断重算 cross[i] 不变) — 这是事件信号
入 L2 的红线 (前视=leakage 死)。
"""
from scripts.build_signal_panel import _ema, macd_golden_cross


def test_ema_constant_series():
    assert _ema([1, 1, 1, 1], 3) == [1.0, 1.0, 1.0, 1.0]      # 常数 → 常数
    assert _ema([1, 2, 3], 1) == [1.0, 2.0, 3.0]               # period=1 → k=1 → e=v


def test_macd_golden_cross_length():
    closes = [10 + i * 0.1 for i in range(120)]
    gc = macd_golden_cross(closes, 12, 26, 9)
    assert len(gc) == 120
    assert gc[0] is False                                      # 首 bar 无前值不触发


def test_macd_golden_cross_pit_no_lookahead():
    """红线: 第 i bar 金叉只用 ≤i (截断到 i 重算结果必须一致, 否则=前视 leakage)。"""
    closes = [10 + i * 0.1 + (i % 7) * 0.3 for i in range(120)]
    gc = macd_golden_cross(closes, 12, 26, 9)
    for i in (30, 60, 90, 119):
        gc_trunc = macd_golden_cross(closes[: i + 1], 12, 26, 9)
        assert gc_trunc[i] == gc[i], f"bar {i} PIT 违例: 截断重算变化 = 前视 leakage"


def test_macd_golden_cross_detects_turn():
    """先跌后涨应出现金叉 (DIF 上穿 DEA)。"""
    closes = [20 - i * 0.2 for i in range(40)] + [12 + i * 0.5 for i in range(40)]
    gc = macd_golden_cross(closes, 12, 26, 9)
    assert any(gc), "先跌后涨应至少一次金叉"
