"""Phase γ D2 — 估值字段派生。

来源 (D1 audit 已定):
  - valuation_pe         <- dim_stock_archetype_latest.pe_ttm
  - valuation_pe_pctile  <- raw_aif10_valuation_quantile (index_type='1') 的 P30/P50/P70
  - valuation_upside_pct <- (peer_pe_median × eps_ttm - close) / close  (clamped 0-80)

为何不直接读 DB?
  - 这层是纯计算: 给定原料数字, 算分位 + upside
  - DB I/O 在 build_picture_daily.py 一次性 bulk fetch + 调本层 per-stock
"""
from __future__ import annotations

from typing import Any


def compute_pe_percentile(
    pe_ttm: float | None,
    p30: float | None,
    p50: float | None,
    p70: float | None,
) -> float | None:
    """给定 PE 当前值 + 历史 P30 / P50 / P70 阈值, 返回当前所处分位。

    简化逻辑 (audit 显示 quantile 表只有这 3 个阈值):
      - PE ≤ P30 → 0.15  (低估)
      - P30 < PE ≤ P50 → 0.40
      - P50 < PE ≤ P70 → 0.60
      - PE > P70 → 0.85  (高估)

    任意阈值缺失 → 返回 None (调用方处理)。
    """
    if pe_ttm is None or pe_ttm <= 0:
        return None
    if p30 is None or p50 is None or p70 is None:
        return None
    if pe_ttm <= p30:
        return 0.15
    if pe_ttm <= p50:
        return 0.40
    if pe_ttm <= p70:
        return 0.60
    return 0.85


def compute_upside_pct(
    close: float | None,
    peer_pe_median: float | None,
    eps_ttm: float | None,
    cap_pct: float = 80.0,
) -> float | None:
    """计算 valuation_upside_pct = (peer 估值 target - close) / close × 100。

    Args:
        close: 最新收盘价
        peer_pe_median: 同行业 PE 中位数 (raw_aif10_peer_valuation.industry_pe_median)
        eps_ttm: 每股收益 TTM
        cap_pct: 抑制噪声, 上限默认 80%

    Returns:
        % 数 (clamp 到 [0, cap_pct]); 任意输入缺失 → None
    """
    if close is None or close <= 0:
        return None
    if peer_pe_median is None or peer_pe_median <= 0:
        return None
    if eps_ttm is None or eps_ttm <= 0:
        return None
    target = peer_pe_median * eps_ttm
    upside = (target - close) / close * 100.0
    # 负向 (高估) 不在 v3 UI 显示, 用 0 替代; 正向 cap
    return max(0.0, min(cap_pct, upside))


def derive_valuation(
    *,
    pe_ttm: float | None,
    pe_p30: float | None = None,
    pe_p50: float | None = None,
    pe_p70: float | None = None,
    close: float | None = None,
    peer_pe_median: float | None = None,
    eps_ttm: float | None = None,
) -> dict[str, float | None]:
    """聚合 3 字段返回 (即 mart_stock_picture_daily 用的字段)。"""
    return {
        "valuation_pe": pe_ttm if (pe_ttm is not None and pe_ttm > 0) else None,
        "valuation_pe_pctile": compute_pe_percentile(pe_ttm, pe_p30, pe_p50, pe_p70),
        "valuation_upside_pct": compute_upside_pct(close, peer_pe_median, eps_ttm),
    }
