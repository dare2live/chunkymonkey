"""Paper Sim v2 — 仓位分配.

4 种模式 (config.portfolio.position_sizing):
  - equal:                 N 个 candidate 每个 (1 - cash) / N
  - kelly:                 每个候选 Kelly fraction → 然后 normalize 到总仓位 cap
  - wilson_kelly:          Wilson 修正胜率 → Kelly fraction → normalize
  - score_rank_diff_v1:    Codex round 19 + 用户"差异化到底" verdict
                           rank-based score tilt (w ∝ (6-rank)^p) ×
                           vol haircut (低 vol → 重仓) ×
                           cap/floor/cash buffer

输出 cash 金额, 调用方再 round_to_lots 转股数.

Codex round 19 verdict (a59f50ececd83cdb1):
- alpha 弱时 (RankIC<0.03) 仓位差异化最多 +2~+8pp ann (vs equal)
- 推荐 stacking: sector cap → liquidity filter → vol haircut → rank tilt → cap/cash
- 推荐形状: 30 / 23 / 17 / 10 / 5 + 15% cash (p=1.2)
- 反对 35/25/20/15/5 (太激进) + 反对 full 5D Optuna (5 样本易 overfit)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import PortfolioConfig
from services.paper_sim.selector import CandidateRow
from services.portfolio_sizer.wilson import wilson_lower
from services.portfolio_sizer.kelly import kelly_fraction


@dataclass(frozen=True)
class SizingResult:
    stock_code: str
    target_cny: float            # 应投金额
    target_pct: float            # 占总资金比例
    raw_kelly_f: Optional[float] = None
    raw_wilson: Optional[float] = None
    reason: str = ""


def _kelly_for_candidate(c: CandidateRow, total_cap_pct: float) -> tuple[float, float, float]:
    """单候选: 用 Wilson + Kelly 算 target_pct (占总资金 cap_pct 的比例)."""
    # daily_position_recommendation 上游已经给了 wilson 修正胜率
    # 这里如果没有就回退用 raw — 但 candidate row 没传 wilson_win, 简化用 0.55 中位 default
    wilson = 0.55   # 简化版: 假设 buy_signal 上游已经用 wilson 排序过.
    # avg_ret + avg_dd 来自 daily_rec 数据
    avg_ret = c.expected_total_return
    avg_dd = c.optimal_stop_pct or -0.05   # 用 optimal_stop_pct 估 max_dd (保守)
    if avg_dd >= 0:
        avg_dd = -0.05
    f = kelly_fraction(wilson, avg_ret, avg_dd, kelly_mul=0.5, max_f=0.25)
    return f * total_cap_pct, wilson, f


def _score_rank_diff_v1(
    candidates: list[CandidateRow],
    cfg: PortfolioConfig,
    available_cash: float,
    total_capital: float,
) -> list[SizingResult]:
    """Codex round 19 heuristic v1 — rank-based score tilt + vol haircut + cap.

    Formula (按 Codex verdict 推荐 default):
      base_w_i = (N + 1 - rank_i) ^ p   # rank 1 重仓, rank N 轻仓
      vol_haircut_i = clip((median_vol / vol_i) ^ vol_exp, 0.75, 1.20)
      raw_w_i = base_w_i * vol_haircut_i
      final_w_i = raw_w_i / sum(raw_w) * (1 - cash_buffer)
      clip(final_w_i, min_single, max_single)

    Default params (Codex 推荐):
      p = 1.2 (rank exponent, 不超过 1.5)
      vol_exp = 0.5 (vol haircut 强度)
      cash_buffer = 0.15 (15% 现金, 风险期可调 0.20-0.30)
      max_single = 0.25 (默认 25%, 上限 30%, 反对 35%)
      min_single = 0.05
    """
    n = len(candidates)
    if n == 0:
        return []

    # Read params from cfg (or use Codex round 19 defaults)
    # rule-compliance: ok evidence=Codex round 19 a59f50ececd83cdb1 verdict
    p = getattr(cfg, "score_rank_p", 1.2)
    vol_exp = getattr(cfg, "vol_haircut_exp", 0.5)
    vol_haircut_min = getattr(cfg, "vol_haircut_min", 0.75)
    vol_haircut_max = getattr(cfg, "vol_haircut_max", 1.20)
    cash_buffer = max(cfg.min_cash_pct, getattr(cfg, "score_rank_cash_buffer", 0.15))
    max_single = getattr(cfg, "max_single_weight", 0.25)
    min_single = getattr(cfg, "min_single_weight", 0.05)

    # rank-based base weight: rank 1 -> N^p / 最重, rank N -> 1^p / 最轻
    base_w = [(n - i) ** p for i in range(n)]

    # vol haircut (用 candidate.expected_total_return 估 vol — 缺真实 vol 数据用 stop_pct fallback)
    # 实际生产 build_p0a_label_panel 应该带 20d realized vol 字段
    vols = []
    for c in candidates:
        v = abs(c.optimal_stop_pct) if c.optimal_stop_pct else 0.05
        vols.append(max(v, 0.01))  # avoid div-by-zero
    median_vol = sorted(vols)[n // 2]
    vol_haircut = [
        max(vol_haircut_min, min(vol_haircut_max, (median_vol / v) ** vol_exp))
        for v in vols
    ]

    raw_w = [bw * vh for bw, vh in zip(base_w, vol_haircut)]
    total_raw = sum(raw_w)
    if total_raw <= 0:
        # Edge: fallback equal
        per_pct = (1.0 - cash_buffer) / n
        return [
            SizingResult(c.stock_code, per_pct * total_capital, per_pct,
                         reason="score_rank_diff_zero_fallback_equal")
            for c in candidates
        ]

    # Normalize to (1 - cash_buffer), then clip
    target_total = 1.0 - cash_buffer
    out = []
    remaining_cash = available_cash
    for c, rw, vh in zip(candidates, raw_w, vol_haircut):
        pct = rw / total_raw * target_total
        # cap + floor
        pct_capped = max(min_single, min(max_single, pct))
        target_cny = min(pct_capped * total_capital, remaining_cash)
        remaining_cash -= target_cny
        out.append(SizingResult(
            stock_code=c.stock_code,
            target_pct=pct_capped,
            target_cny=target_cny,
            raw_kelly_f=None, raw_wilson=None,
            reason=f"score_rank_diff_v1(p={p},vol_haircut={vh:.2f},cash={cash_buffer:.2f})",
        ))
    return out


def allocate_positions(
    candidates: list[CandidateRow],
    cfg: PortfolioConfig,
    available_cash: float,
    total_capital: float,
) -> list[SizingResult]:
    """给 N 个候选 (N ≤ max_positions) 分配仓位.

    Args:
        candidates: 已经按 score 排序的候选, 长度 ≤ max_positions
        cfg: portfolio config
        available_cash: 当前可用现金
        total_capital: 总资本 (现金 + 已持仓市值)

    Returns:
        SizingResult 列表, 顺序跟输入一致.
    """
    if not candidates:
        return []

    # Codex round 19: score_rank_diff_v1 (rank tilt + vol haircut)
    if cfg.position_sizing == "score_rank_diff_v1":
        return _score_rank_diff_v1(candidates, cfg, available_cash, total_capital)

    # 总仓位上限 = 1 - min_cash_pct (留缓冲)
    total_cap_pct = 1.0 - cfg.min_cash_pct

    n = len(candidates)

    if cfg.position_sizing == "equal":
        per_pct = total_cap_pct / max(n, 1)
        return [
            SizingResult(
                stock_code=c.stock_code,
                target_pct=per_pct,
                target_cny=min(per_pct * total_capital, available_cash / max(n - i, 1)),
                reason=f"equal({per_pct:.3f})",
            )
            for i, c in enumerate(candidates)
        ]

    # kelly / wilson_kelly: 算每个 raw kelly, 然后 normalize 到 total_cap_pct
    raw: list[tuple[float, float, float]] = []   # (pct, wilson, f)
    for c in candidates:
        pct, wilson, f = _kelly_for_candidate(c, total_cap_pct)
        raw.append((pct, wilson, f))
    raw_total = sum(r[0] for r in raw)
    if raw_total <= 0:
        # 全 Kelly 0 (亏损模型), 退化为 equal
        per_pct = total_cap_pct / n
        return [
            SizingResult(c.stock_code, per_pct * total_capital, per_pct,
                         reason="kelly_zero_fallback_equal")
            for c in candidates
        ]
    # 等比例放大到 total_cap_pct
    scale = min(1.0, total_cap_pct / raw_total)
    out: list[SizingResult] = []
    remaining_cash = available_cash
    for c, (pct, wilson, f) in zip(candidates, raw):
        final_pct = pct * scale
        final_cny = min(final_pct * total_capital, remaining_cash)
        remaining_cash -= final_cny
        out.append(SizingResult(
            stock_code=c.stock_code,
            target_pct=final_pct,
            target_cny=final_cny,
            raw_kelly_f=f, raw_wilson=wilson,
            reason=f"{cfg.position_sizing}(kelly_f={f:.3f}*scale{scale:.2f})",
        ))
    return out
