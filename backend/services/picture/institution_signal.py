"""Phase γ D2 — 机构信号聚合。

针对单只股, 输入: 所有当前持仓该股票的机构列表 (来自 fact_top10_holder_period 当前期 + mart_institution_profile 画像)
输出: institution_signal = {score 0-100, n_insts, top: [{name, holding_pct, win_rate_60d}]}

评分逻辑 (Occam, 3 项加权):
  - 0.5 × 近期增持评分 (季度环比 share 增加的机构 / 总机构, 0-100)
  - 0.3 × 跟踪机构持仓数评分 (我们跟踪 240+ 机构, 该股有多少在持仓, 上限 30 → 100)
  - 0.2 × 跟踪机构平均胜率 (win_rate_60d 平均, 0-100)
"""
from __future__ import annotations

from typing import Any


def compute_recent_increase_score(holders: list[dict]) -> float:
    """近期增持机构占比 → 0-100。"""
    if not holders:
        return 0.0
    n_inc = sum(1 for h in holders if (h.get("share_change_qoq") or 0) > 0)
    return float(n_inc) / float(len(holders)) * 100.0


def compute_tracked_count_score(holders: list[dict], cap: int = 30) -> float:
    """跟踪机构的持仓覆盖度 0-100; 超 30 家时饱和。"""
    n_tracked = sum(1 for h in holders if h.get("is_tracked"))
    return min(100.0, float(n_tracked) / float(cap) * 100.0)


def compute_avg_win_rate_score(holders: list[dict]) -> float:
    """跟踪机构平均 60 日胜率 (后端 0-100 直接当分数)。"""
    rates = [h.get("inst_win_rate_60d") for h in holders if h.get("inst_win_rate_60d") is not None]
    if not rates:
        return 0.0
    return float(sum(rates) / len(rates))


def compute_institution_score(holders: list[dict]) -> float:
    """加权汇总: 0.5 × 增持 + 0.3 × 跟踪覆盖 + 0.2 × 胜率均值。"""
    s1 = compute_recent_increase_score(holders)
    s2 = compute_tracked_count_score(holders)
    s3 = compute_avg_win_rate_score(holders)
    return round(0.5 * s1 + 0.3 * s2 + 0.2 * s3, 2)


def top_institutions(holders: list[dict], n: int = 3) -> list[dict]:
    """返回持仓占比最高的 N 家机构 (用于 v3 UI 卡片 hover 展示)。"""
    # 按 share_pct desc 排, 取前 n
    sorted_h = sorted(
        holders,
        key=lambda h: (h.get("share_pct") or 0.0),
        reverse=True,
    )
    out = []
    for h in sorted_h[:n]:
        out.append({
            "institution_id": h.get("institution_id"),
            "name": h.get("institution_name") or h.get("name") or "未知",
            "share_pct": float(h.get("share_pct") or 0.0),
            "win_rate_60d": h.get("inst_win_rate_60d"),
        })
    return out


def aggregate_institution_signal(holders: list[dict]) -> dict[str, Any]:
    """单股聚合接口, 输出 mart_stock_picture_daily 用的 institution_* 字段。

    Args:
        holders: list of dict, 每个 dict 至少含:
            - institution_id, institution_name (or name)
            - share_pct (float)
            - share_change_qoq (float, signed, 增持为正)
            - is_tracked (bool)
            - inst_win_rate_60d (float 0-100)

    Returns:
        {
          "institution_score": float 0-100,
          "institution_n_insts": int,
          "institution_top": list[{name, share_pct, win_rate_60d}]
        }
    """
    return {
        "institution_score": compute_institution_score(holders),
        "institution_n_insts": len(holders),
        "institution_top": top_institutions(holders),
    }
