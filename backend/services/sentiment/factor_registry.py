"""Phase η++++ — 因子中央注册表.

**唯一的 factor 真相源**. 添加 / 修改 / 删除因子, 只改这一处文件.
下游 (ETL / Optuna / daily 推荐 / UI) 都通过此 registry 查询.

设计目标:
  - 添加新因子: 只需在此文件加一个 FactorSpec, 不动其他地方
  - 删除某因子: 移除条目即可, 下游自动失效
  - 改 profile 启用集合: 改 configs.PROFILE_POLICY (因子注册仍不动)
  - 改桶阈值: 改 configs.SURVEY_BIN (因子注册仍不动)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from services.sentiment.configs import (
    IC_GATE, PROFILE_POLICY,
)


FactorKind = Literal["sentiment", "context", "fundamental"]
ICDirection = Literal["positive", "negative", "neutral"]


@dataclass(frozen=True)
class ICMeasurement:
    """单一 horizon 的 IC 实测结果 (来自 validate_sentiment_ic.py 报告)."""
    horizon_days: int
    ic_mean: float
    ic_pos_pct: float
    n_days: int
    proof_date: str  # 何时跑的


@dataclass(frozen=True)
class FactorSpec:
    """单一因子的全描述.

    Fields:
        factor_id: 内部唯一 ID (snake_case, 必须与产生该列的 mart 表列名一致)
        display_name: 中文短名 (UI 用)
        kind: 分类 (sentiment / context / fundamental)
        source_table: 因子值来源表 (None 表示派生)
        source_column: 来源列名 (None 表示派生)
        bin_column: 离散化后的桶列名 (None 表示连续因子, 不分桶)
        bin_count: 桶数量 (用于自动生成 bucket label, None 表示连续)
        ic_proof: IC 实测列表 (多 horizon)
        profile_eligible: 该因子允许使用的 profile 列表 (实测决定)
        bin_score_multiplier: {bin_label: multiplier} — 给 sizing.score 加权
                              空 dict = 不参与 score 加权 (仅作过滤/分桶)
        notes: 额外说明
    """
    factor_id: str
    display_name: str
    kind: FactorKind
    source_table: str | None
    source_column: str | None
    bin_column: str | None
    bin_count: int | None
    ic_proof: tuple[ICMeasurement, ...]
    profile_eligible: tuple[str, ...]
    bin_score_multiplier: tuple[tuple[str, float], ...] = ()  # tuple-of-tuples for immutability
    notes: str = ""

    def get_multiplier(self, bin_label: str | None) -> float:
        """查桶标签 → score 乘子. 未匹配 → 1.0."""
        if not bin_label or not self.bin_score_multiplier:
            return 1.0
        for label, mult in self.bin_score_multiplier:
            if label == bin_label:
                return mult
        return 1.0

    def best_horizon(self) -> ICMeasurement | None:
        """选 IC 最大的 horizon (绝对值)."""
        if not self.ic_proof:
            return None
        return max(self.ic_proof, key=lambda m: abs(m.ic_mean))

    def ic_direction(self) -> ICDirection:
        """因子方向 (正向/负向/中性, 基于最佳 horizon)."""
        best = self.best_horizon()
        if not best:
            return "neutral"
        if best.ic_mean >= IC_GATE.strong_threshold:
            return "positive"
        if best.ic_mean <= -IC_GATE.strong_threshold:
            return "negative"
        return "neutral"


# ─────────────────────────────────────────────────────────────────────
# 注册表本体 (在此添加 / 删除因子)
# ─────────────────────────────────────────────────────────────────────

FACTOR_REGISTRY: dict[str, FactorSpec] = {

    # ── sentiment: 调研热度 (60d 滚动机构调研次数) ───────────────────
    # 注: profile_eligible 在 FactorSpec 中是"展示语义" (该因子被哪些 profile 使用),
    # 真实 eligibility 由 is_factor_eligible() 通过 PROFILE_POLICY 反向查询决定.
    "survey_count_60d": FactorSpec(
        factor_id="survey_count_60d",
        display_name="调研热度·60日",
        kind="sentiment",
        source_table="mart_stock_survey_features",
        source_column="survey_count_60d",
        bin_column="survey_bin",
        bin_count=4,
        ic_proof=(
            ICMeasurement(5,  -0.0012, 0.555, 137, "2026-05-12"),
            ICMeasurement(10, +0.0277, 0.598, 132, "2026-05-12"),
            ICMeasurement(20, +0.0428, 0.607, 122, "2026-05-12"),
            ICMeasurement(60, +0.0860, 0.720,  82, "2026-05-12"),
        ),
        profile_eligible=("long",),
        # 桶 → score 乘子: 实测 60d IC 在 "狂" 桶最强 (pos_pct 72%), 给 1.25× 加权;
        # 改这里 = 直接调整因子在 portfolio rank 中的影响力, 不动业务代码.
        bin_score_multiplier=(
            ("冷", 1.00),
            ("温", 1.05),
            ("热", 1.15),
            ("狂", 1.25),
        ),
        notes="实测: 5d 无效, 60d IC=0.086 强正向. 仅用于长期 profile.",
    ),

    # ── sentiment: 龙虎榜 ─ 显式注册以便 UI/审计追踪, 但 profile_eligible=空 ──
    # 此项目里**故意保留**这个条目, 用于:
    #   1) 文档清楚说明 "已验证 + 决定不用" 而非 "忘了接"
    #   2) 未来若假设错误可重新启用
    "lhb_score": FactorSpec(
        factor_id="lhb_score",
        display_name="龙虎榜分数",
        kind="sentiment",
        source_table="fact_lhb_event",
        source_column=None,  # 派生 (seats × 10 + net_buy/1e8)
        bin_column=None,
        bin_count=None,
        ic_proof=(
            ICMeasurement(5,  -0.0135, 0.484, 704, "2026-05-12"),
            ICMeasurement(10, -0.0272, 0.443, 699, "2026-05-12"),
            ICMeasurement(20, -0.0383, 0.434, 689, "2026-05-12"),
            ICMeasurement(60, -0.0324, 0.416, 649, "2026-05-12"),
        ),
        profile_eligible=(),  # 显式空: 反向 IC, 不进任何 profile
        notes="实测全 horizon 负 IC + pos_pct < 50%, 利好出尽效应. 数据保留供 UI 展示, 不进模型.",
    ),
}


# ─────────────────────────────────────────────────────────────────────
# 查询 API (下游唯一接口)
# ─────────────────────────────────────────────────────────────────────

def list_factor_ids() -> list[str]:
    return list(FACTOR_REGISTRY.keys())


def get_factor(factor_id: str) -> FactorSpec:
    if factor_id not in FACTOR_REGISTRY:
        raise KeyError(f"unknown factor_id: {factor_id}")
    return FACTOR_REGISTRY[factor_id]


def get_eligible_factors(profile_id: str) -> list[FactorSpec]:
    """某 profile 启用的全部因子. 由 configs.PROFILE_POLICY 决定."""
    eligible_ids = PROFILE_POLICY.get_eligible(profile_id)
    return [FACTOR_REGISTRY[fid] for fid in eligible_ids if fid in FACTOR_REGISTRY]


def get_bucket_dims(profile_id: str) -> list[str]:
    """该 profile 对应的额外桶维度 (用于 Optuna / signal_context 扩展).

    返回 bin_column 列表 (跳过连续因子).
    """
    return [
        f.bin_column for f in get_eligible_factors(profile_id)
        if f.bin_column is not None
    ]


def is_factor_eligible(factor_id: str, profile_id: str) -> bool:
    """权威 eligibility 检查 — 通过 PROFILE_POLICY 反查.

    FactorSpec.profile_eligible 是 "展示用" 元数据 (注册时声明),
    真实策略以 configs.PROFILE_POLICY 为准 (改 policy 即生效, 不动 registry).
    """
    if factor_id not in FACTOR_REGISTRY:
        return False
    return factor_id in PROFILE_POLICY.get_eligible(profile_id)


def factor_summary() -> list[dict]:
    """供 UI / 调试用的全因子摘要."""
    out = []
    for f in FACTOR_REGISTRY.values():
        best = f.best_horizon()
        out.append({
            "factor_id": f.factor_id,
            "display_name": f.display_name,
            "kind": f.kind,
            "direction": f.ic_direction(),
            "best_ic": best.ic_mean if best else None,
            "best_horizon": best.horizon_days if best else None,
            "best_pos_pct": best.ic_pos_pct if best else None,
            "profile_eligible": list(f.profile_eligible),
            "bin_column": f.bin_column,
            "notes": f.notes,
        })
    return out
