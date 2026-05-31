"""P3 Final Holdout Acceptance Gate.

analysis/plan_v3_20260514_archived.md v3.2 P3 (硬验收):
- 输入: P2 冻结代码 / 特征 / 模型 / 权重 / seed
- 数据: 最近 6 个 OOS 月 stitched final holdout (在 P3 之前不可读)
- 4 个硬验收 (用户终极目标):
  - ann_ret ≥ 30%
  - max_dd ≥ -20%
  - 超额 vs HS300 > 0
  - 月胜率 ≥ 55%
- 任一失败 → 停止包装, 回到 alpha 根因, 不调目标 (analysis/plan_v3_20260514_archived.md §4 +30% 目标自检)

输出: paper trading 候选 + 风险暴露 + 不可成交原因 + 交易回放.

PIT 严格 (Rule 7 + Rule 9.1):
- final holdout 只读一次 (P3 验收时)
- P0/P1/P2 阶段绝对禁止读 (governance.enforce_pre_optimize 已加 check)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("portfolio.final_holdout")


# 用户终极目标 (analysis/plan_v3_20260514_archived.md §0.1):
# 短期资产最大幅度增值不缩水, 年化≥30% + max_dd≥-20% + 超额 vs HS300>0
ANN_RET_TARGET = 0.30
MAX_DD_TARGET = -0.20
MONTHLY_WIN_RATE_TARGET = 0.55

# Codex round 17 Q8.7 FIX: ann_ret sanity cap (governance v1, 防 corrupt label 再 PASS)
# A 股策略 hard cap: 真实期望最高 50% / 年, 超过几乎必 leakage (反例: lgbm_v3 ann=21843%)
# evidence: backtest commit 9c01eae0 (governance v1 修复) — 之前 ann_ret=21843% 是 unit bug
ANN_RET_SANITY_CAP = 0.50


@dataclass(frozen=True)
class FinalHoldoutMetrics:
    """Final holdout 上算出的 KPI."""
    ann_ret: float
    max_dd: float
    excess_vs_hs300: float       # excess return = strategy - HS300
    monthly_win_rate: float
    hs300_ann_ret: float | None = None
    n_oos_months: int = 0
    final_period_start: Optional[str] = None
    final_period_end: Optional[str] = None
    # 容量 / 集中度 / 换手 / 成本 (可选, 进 P2 composite 但 P3 acceptance 不强制)
    avg_concentration: Optional[float] = None
    turnover: Optional[float] = None
    tx_cost_pct: Optional[float] = None
    # Reproducibility
    model_version: Optional[str] = None
    feature_version: Optional[str] = None
    label_version: Optional[str] = None
    seed: Optional[int] = None


@dataclass(frozen=True)
class AcceptanceResult:
    """P3 Acceptance gate 结果."""
    passed: bool
    failures: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def check_final_acceptance(metrics: FinalHoldoutMetrics) -> AcceptanceResult:
    """检 PLAN_V3 P3 4 个硬验收.

    Returns:
        AcceptanceResult(passed=True iff 全 4 项通过).

    用户原则 (CLAUDE Rule 9):
    - 不接受"接近" / "差不多" — 数字 ≥ 阈值才算 PASS
    - 任何 FAIL → 停止包装, 回 alpha 根因, 不调目标
    """
    failures: list[str] = []
    detail = {
        "ann_ret_check":      f"{metrics.ann_ret:.4f} vs target ≥ {ANN_RET_TARGET}",
        "max_dd_check":       f"{metrics.max_dd:.4f} vs target ≥ {MAX_DD_TARGET}",
        "excess_check":       f"{metrics.excess_vs_hs300:.4f} vs target > 0",
        "monthly_win_check":  f"{metrics.monthly_win_rate:.4f} vs target ≥ {MONTHLY_WIN_RATE_TARGET}",
    }

    if metrics.ann_ret < ANN_RET_TARGET:
        failures.append(
            f"ann_ret={metrics.ann_ret:.4f} < target {ANN_RET_TARGET} "
            f"(差 {(ANN_RET_TARGET - metrics.ann_ret):.4f})"
        )
    if metrics.max_dd < MAX_DD_TARGET:
        failures.append(
            f"max_dd={metrics.max_dd:.4f} < target {MAX_DD_TARGET} "
            f"(差 {(MAX_DD_TARGET - metrics.max_dd):.4f})"
        )
    if metrics.excess_vs_hs300 <= 0:
        failures.append(
            f"excess_vs_hs300={metrics.excess_vs_hs300:.4f} ≤ 0 (未跑赢 HS300)"
        )
    if metrics.monthly_win_rate < MONTHLY_WIN_RATE_TARGET:
        failures.append(
            f"monthly_win_rate={metrics.monthly_win_rate:.4f} < target {MONTHLY_WIN_RATE_TARGET}"
        )

    # Codex round 17 Q8.7 FIX: ann_ret sanity cap (governance v1 leakage 警报)
    # 反例: corrupt label 时代 lgbm_v3_honest_20d P3 ann_ret=21843% (volume unit bug)
    if metrics.ann_ret > ANN_RET_SANITY_CAP:
        failures.append(
            f"ann_ret={metrics.ann_ret:.4f} > sanity cap {ANN_RET_SANITY_CAP} "
            f"(governance v1 leakage 警报, 反例 lgbm_v3 21843% volume unit bug)"
        )

    return AcceptanceResult(
        passed=len(failures) == 0,
        failures=failures,
        detail=detail,
    )


def format_acceptance_report(metrics: FinalHoldoutMetrics, result: AcceptanceResult) -> str:
    """Human-readable acceptance report (markdown table)."""
    lines = [
        f"# P3 Final Holdout Acceptance Report",
        f"",
        f"## Reproducibility",
        f"- model_version: {metrics.model_version}",
        f"- feature_version: {metrics.feature_version}",
        f"- label_version: {metrics.label_version}",
        f"- seed: {metrics.seed}",
        f"- final_period: {metrics.final_period_start} → {metrics.final_period_end}",
        f"- n_oos_months: {metrics.n_oos_months}",
        f"",
        f"## 4 硬验收 (PLAN_V3 §99 P3)",
        f"",
        f"| metric | value | target | status |",
        f"|---|---:|---:|:---:|",
        f"| ann_ret           | {metrics.ann_ret:.2%} | ≥ {ANN_RET_TARGET:.0%} | "
        f"{'✓' if metrics.ann_ret >= ANN_RET_TARGET else '✗'} |",
        f"| max_dd            | {metrics.max_dd:.2%} | ≥ {MAX_DD_TARGET:.0%} | "
        f"{'✓' if metrics.max_dd >= MAX_DD_TARGET else '✗'} |",
        f"| excess vs HS300   | {metrics.excess_vs_hs300:+.2%} | > 0 | "
        f"{'✓' if metrics.excess_vs_hs300 > 0 else '✗'} |",
        f"| monthly_win_rate  | {metrics.monthly_win_rate:.2%} | ≥ {MONTHLY_WIN_RATE_TARGET:.0%} | "
        f"{'✓' if metrics.monthly_win_rate >= MONTHLY_WIN_RATE_TARGET else '✗'} |",
        f"",
        f"## Verdict",
        f"",
    ]
    if result.passed:
        lines.append("**PASS** ✓ — 4 硬验收全过, 可启动 paper trading")
    else:
        lines.append("**FAIL** ✗ — 任一硬验收失败, 停止包装, 回 alpha 根因 (CLAUDE Rule 9)")
        lines.append("")
        lines.append("### Failures")
        for f in result.failures:
            lines.append(f"- {f}")
    return "\n".join(lines)
