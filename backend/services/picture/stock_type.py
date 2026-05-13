"""Phase γ D2 — stock_type (primary_type) 分类器。

5 状态 (audit-aware 重写, 用真实 archetype + stage 字段):
  - 事件驱动 (event_count_30d ≥ 2)
  - 业绩驱动 (archetype="成长兑现型" + profit_yoy ≥ 30% + PE_pctile ≤ 0.60)
  - 价值修复 (PE_pctile ≤ 0.20 + archetype="高质量稳健型")
  - 周期复苏 (fundamental_stage="周期复苏" OR archetype="周期/事件驱动型" + revenue_yoy > 0)
  - 技术突破 (任一公式 last 5 天 hit + vol_ratio > 1.3)

输出:
  - primary_type: 第一个命中规则
  - secondary_types: 其它命中规则列表
  - reason_codes: list[str] 描述触发原因

设计:
  - 纯函数, 输入完整字典, 不读 DB (DB I/O 在 build_picture_daily.py)
  - 规则有序, 优先级高的先匹配
"""
from __future__ import annotations

from typing import Any


PRIMARY_TYPES = (
    "事件驱动", "业绩驱动", "价值修复", "周期复苏", "技术突破", "—",
)


def classify_stock_type(features: dict[str, Any]) -> dict[str, Any]:
    """根据特征字典派生 stock_type。

    Args:
        features: 必含/可缺字段:
            - event_count_30d (int)         事件数 30 日内
            - stock_archetype (str)         "成长兑现型"/"高质量稳健型"/"周期/事件驱动型"
            - fundamental_stage (str)       见 fundamental_stage.py
            - latest_profit_yoy (float)     利润同比 (1.0 = 100%)
            - latest_revenue_yoy (float)    营收同比
            - valuation_pe_pctile (float)   PE 历史分位 0-1
            - return_3m (float)             3 月涨幅
            - vol_ratio (float)             量比 (20 日 / 120 日 均量)
            - formula_hits_last_5d (int)    最近 5 日公式触发次数

    Returns:
        {"primary_type": str, "secondary_types": list[str], "reason_codes": list[str]}
    """
    types_hit: list[tuple[str, str]] = []  # (type, reason_code)

    # 规则 1: 事件驱动 (最高优先)
    event_n = features.get("event_count_30d") or 0
    if event_n >= 2:
        types_hit.append(("事件驱动", f"event_count_30d:{event_n}"))

    # 规则 2: 业绩驱动
    arch = features.get("stock_archetype") or ""
    profit_yoy = features.get("latest_profit_yoy") or 0.0
    pe_pctile = features.get("valuation_pe_pctile")
    if (arch == "成长兑现型"
        and profit_yoy >= 0.30
        and (pe_pctile is None or pe_pctile <= 0.60)):
        types_hit.append((
            "业绩驱动",
            f"成长兑现型+profit_yoy:{profit_yoy:.2f}+pe_pctile:{pe_pctile or 'NA'}"
        ))

    # 规则 3: 价值修复
    if arch == "高质量稳健型" and pe_pctile is not None and pe_pctile <= 0.20:
        types_hit.append((
            "价值修复",
            f"高质量稳健型+pe_pctile:{pe_pctile:.2f}"
        ))

    # 规则 4: 周期复苏
    fund_stage = features.get("fundamental_stage") or ""
    rev_yoy = features.get("latest_revenue_yoy") or 0.0
    ret_3m = features.get("return_3m") or 0.0
    if (fund_stage == "周期复苏"
        or (arch == "周期/事件驱动型" and rev_yoy > 0 and ret_3m > 0)):
        types_hit.append((
            "周期复苏",
            f"fund_stage:{fund_stage}/arch:{arch}/rev_yoy:{rev_yoy:.2f}/ret_3m:{ret_3m:.2f}"
        ))

    # 规则 5: 技术突破
    n_hits = features.get("formula_hits_last_5d") or 0
    vol_ratio = features.get("vol_ratio") or 0.0
    if n_hits >= 1 and vol_ratio > 1.3:
        types_hit.append((
            "技术突破",
            f"formula_hits_5d:{n_hits}+vol_ratio:{vol_ratio:.2f}"
        ))

    if not types_hit:
        return {"primary_type": "—", "secondary_types": [], "reason_codes": []}

    primary = types_hit[0][0]
    secondary = [t for t, _ in types_hit[1:]]
    reasons = [r for _, r in types_hit]
    return {
        "primary_type": primary,
        "secondary_types": secondary,
        "reason_codes": reasons,
    }
