"""
sw_industry_names.py — 申万宏源行业代码 → 中文名字典（静态常量）

数据来源：申万宏源官方 ──
  - L1 名称：akshare.sw_index_first_info()（31 个活跃一级行业）
  - L2/L3 名称：暂留 None，由 services.sw_industry_names_build 后台脚本
    通过 stock_industry_clf_hist_sw 反推 + 渐进式补全（详见 Phase 2）

编码结构：申万分类内部编码（与 801xxx 申万指数代码不同体系）
  6 位 industry_code = L1(2) + L2(2) + L3(2)
  例：480301 → L1=48 银行 / L2=4803 股份行II / L3=480301 ...

L1 列表（31 个活跃 + 7 个历史/已合并）：
  31 个映射来自 sw_index_first_info 当前发布表，
  7 个历史 code（21/25/26/31/32/44/47）暂未映射，命中时返回 None。
"""

from __future__ import annotations

from typing import Optional

# ─────────────────────────────────────────────────────────────────────
# L1 (前 2 位) → 名称  ── 31 个当前活跃一级行业
# 来源：akshare.sw_index_first_info()（2026-04 抓取）
# 反推方式：每个 L1 指数取首位成分股 → 查 stock_industry_clf_hist_sw
#   industry_code 前 2 位 → 该指数名称
# ─────────────────────────────────────────────────────────────────────
SW_L1_NAMES: dict[str, str] = {
    "11": "农林牧渔",
    "22": "基础化工",
    "23": "钢铁",
    "24": "有色金属",
    "27": "电子",
    "28": "汽车",
    "33": "家用电器",
    "34": "食品饮料",
    "35": "纺织服饰",
    "36": "轻工制造",
    "37": "医药生物",
    "41": "公用事业",
    "42": "交通运输",
    "43": "房地产",
    "45": "商贸零售",
    "46": "社会服务",
    "48": "银行",
    "49": "非银金融",
    "51": "综合",
    "61": "建筑材料",
    "62": "建筑装饰",
    "63": "电力设备",
    "64": "机械设备",
    "65": "国防军工",
    "71": "计算机",
    "72": "传媒",
    "73": "通信",
    "74": "煤炭",
    "75": "石油石化",
    "76": "环保",
    "77": "美容护理",
}

# ─────────────────────────────────────────────────────────────────────
# L2 (前 4 位) → 名称  ── 162 个，留待后续脚本补全
# ─────────────────────────────────────────────────────────────────────
SW_L2_NAMES: dict[str, str] = {}

# ─────────────────────────────────────────────────────────────────────
# L3 (6 位) → 名称  ── 396 个，留待后续脚本补全
# ─────────────────────────────────────────────────────────────────────
SW_L3_NAMES: dict[str, str] = {}


def get_sw_l1_name(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return SW_L1_NAMES.get(code)


def get_sw_l2_name(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return SW_L2_NAMES.get(code)


def get_sw_l3_name(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return SW_L3_NAMES.get(code)


def get_sw_industry_name(code: Optional[str]) -> Optional[str]:
    """按 code 长度自动选择 L1/L2/L3 字典查询。"""
    if not code:
        return None
    n = len(code)
    if n == 2:
        return SW_L1_NAMES.get(code)
    if n == 4:
        return SW_L2_NAMES.get(code)
    if n == 6:
        return SW_L3_NAMES.get(code)
    return None
