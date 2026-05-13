"""Kelly Criterion 派生仓位.

Kelly 公式 (二元离散赢/输模型):
  f* = (p × b - q) / b
    p = 胜率
    q = 1 - p (败率)
    b = 赔率 (win_amount / loss_amount = avg_ret / |avg_dd|)

Fractional Kelly (业界标准, 完整 Kelly 太激进):
  实际仓位 = f* × kelly_fraction
    kelly_fraction = 0.25 (保守) / 0.5 (中等) / 1.0 (满 Kelly, 不推荐)
"""
from __future__ import annotations


def kelly_fraction(win_rate: float, avg_ret: float, avg_dd: float,
                   kelly_mul: float = 0.5, max_f: float = 0.25) -> float:
    """计算单次下注的 Kelly 仓位 (fractional).

    Args:
        win_rate: 胜率 (建议传 Wilson 下界, 不是朴素)
        avg_ret: 平均收益 (正数, 比如 0.10)
        avg_dd: 平均最大回撤 (负数, 比如 -0.05)
        kelly_mul: fractional Kelly 系数 (0.25-0.5 常见)
        max_f: 单股最大仓位上限 (默认 25%)

    Returns:
        仓位 ∈ [0, max_f]; 输入异常或 f* < 0 时返回 0
    """
    if win_rate is None or avg_ret is None or avg_dd is None:
        return 0.0
    if win_rate <= 0 or avg_ret <= 0:
        return 0.0
    dd_abs = abs(avg_dd)
    if dd_abs < 1e-6:
        return 0.0  # 无回撤 = 数据异常 (可能只 1-2 个样本)
    b = avg_ret / dd_abs  # 赔率
    q = 1.0 - win_rate
    f_star = (win_rate * b - q) / b
    if f_star <= 0:
        return 0.0
    final = f_star * kelly_mul
    return min(max_f, max(0.0, final))


def equal_weight(n_stocks: int) -> float:
    """简单等权 fallback (Kelly 不适用时)."""
    if n_stocks <= 0:
        return 0.0
    return min(0.20, 0.90 / n_stocks)  # 90% 资金平分, 留 10% 现金, 单股 cap 20%
