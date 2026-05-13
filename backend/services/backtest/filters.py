"""Phase ζ — 回测代码过滤 (单一职责).

⚠ 排除指数代码 (非个股, K 线复权异常).
⚠ 改这里 = 改全局过滤列表, 不在脚本里硬编码.
"""
from __future__ import annotations

# 已知指数代码 (不是个股, 回测数据复权异常)
KNOWN_INDEX_CODES: frozenset = frozenset({
    # 上交所指数
    "000016",  # 上证 50
    "000300",  # 沪深 300
    "000688",  # 科创 50
    "000852",  # 中证 1000
    "000905",  # 中证 500
    # 深交所指数
    "399001",  # 深证成指
    "399005",  # 中小 100
    "399006",  # 创业板指
    "399300",  # 沪深 300 (深口径)
    "399905",  # 中证 500 (深口径)
    "399012",  # 创业板综合
})


def is_index_code(stock_code: str) -> bool:
    """判定是否为指数代码 (应排除回测)."""
    if not stock_code:
        return False
    return stock_code in KNOWN_INDEX_CODES


# Net return 极值 cap — 复权异常 (000300 + 207747887%) 导致均值污染
NET_RET_MAX = 5.0      # 单笔最大 +500%
NET_RET_MIN = -1.0     # 单笔最低 -100% (理论极限 = 退市清零)


def cap_net_ret(net_ret: float) -> float:
    """单笔交易净收益 winsorize (防止复权 spike 污染聚合 metrics)."""
    if net_ret > NET_RET_MAX:
        return NET_RET_MAX
    if net_ret < NET_RET_MIN:
        return NET_RET_MIN
    return net_ret
