"""
行数缺口严重程度判定（CV 分层）。

稳定域（CV < 0.25）行数掉一半几乎必然是缺陷；
事件类域（涨停/停牌/大宗，CV 0.3~0.45）行数天然高方差，掉一半是正常低活跃日。
不分层的话，全历史扫描扫出的 84 个可疑日里真信号会被大量正常波动淹没。
"""


def dip_signal_level(day_rows: int, neighbor_median: float, series_cv: float) -> str:
    """
    判定某日行数相对邻近中位数的缺口信号强度。

    Args:
        day_rows: 该日行数（整数，可为 0）
        neighbor_median: 邻近日中位行数（浮点，可为 0 或负数）
        series_cv: 时间序列变异系数（浮点，0.0 ~ 1.0）

    Returns:
        "none": 无信号（不是缺口或正常波动）
        "high": 强信号（稳定域行数掉一半）
        "low": 弱信号（事件类域行数掉一半）
    """
    # 规则 1: 邻近中位数为 0 或负数 → 无法判定，返回 "none"
    if neighbor_median <= 0:
        return "none"

    # 规则 2: 计算相对比例
    ratio = day_rows / neighbor_median

    # 规则 3: ratio >= 0.5 → 不是显著缺口，返回 "none"
    if ratio >= 0.5:
        return "none"

    # 规则 4: ratio < 0.5，再看变异系数
    if series_cv < 0.25:
        # 规则 4a: 稳定域 → 强信号
        return "high"
    else:
        # 规则 4b: 事件类域 → 弱信号
        return "low"
