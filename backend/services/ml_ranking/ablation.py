"""P1 feature group ablation — add/drop ablation framework.

PLAN_V3 v3.2 P1 (3 数据决定的决策点):
- alpha158 全量 vs top-N
- 机构路径 A/B 是否保留
- 公式特征是否保留
- label horizon 5/10/20
- 行业/市值中性 vs raw
- 事件衰减窗口 1/3/5/10/20

工作流:
1. 定义 feature groups (e.g. {"alpha158": [...], "risk_factors": [...], "events": [...]})
2. 跑 baseline (全特征) walk_forward → OOS RankIC
3. 每个 group drop 一次 → 跑 walk_forward → OOS RankIC, 比较 baseline
4. 每个 group 单独 add (仅含本组 + 基础) → 同上
5. 排名 + 入库 (mart_p1_ablation_result)

Acceptance (PLAN_V3 P1 Go metric):
- validation stitched OOS RankIC ≥ 0.04 (比 P0b 0.03 提升)
- ann 高于 P0
- mdd 不劣化

PIT 保证: 复用 train_lightgbm_walkforward (Rule 7 splits) + feature_columns 决定 X.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from services.ml_ranking.lightgbm_walkforward import (
    LightGBMWalkForwardConfig,
    WalkForwardResult,
    train_lightgbm_walkforward,
)

log = logging.getLogger("ml_ranking.ablation")


@dataclass(frozen=True)
class FeatureGroup:
    """Feature group 定义."""
    name: str
    columns: tuple[str, ...]
    description: str = ""


# 默认 feature groups (与 mart_p0a_feature_label_panel 对齐)
DEFAULT_GROUPS: tuple[FeatureGroup, ...] = (
    FeatureGroup(
        name="alpha158",
        columns=tuple(
            f"a158_{stem}" for stem in (
                "kmid", "klen", "kmid2", "kup", "kup2", "klow", "klow2", "ksft", "ksft2"
            )
        ) + tuple(
            f"a158_{stat}{n}"
            for n in (5, 10, 20, 30, 60)
            for stat in ("roc", "ma", "std", "max", "min", "rsv", "qtl", "cntp", "sump", "vma", "vstd")
        ),
        description="alpha158 完整 K 线衍生量价特征 (65 列)",
    ),
    FeatureGroup(
        name="risk_factors",
        columns=("vol_30d", "vol_60d", "vol_120d", "sharpe_60d", "mom_30d", "mom_120d"),
        description="风险因子 (从 fact_risk_factors)",
    ),
    FeatureGroup(
        name="financial_pit",
        columns=("pe_ttm", "pb", "ps_ttm", "roe_q"),
        description="财务 PIT 估值/质量 (从 fact_financial_pit_daily)",
    ),
    FeatureGroup(
        name="events",
        columns=("event_lhb_7d", "event_lhb_30d", "event_inst_7d", "event_inst_30d"),
        description="事件 dummy (LHB / 机构事件 7d/30d 内有无)",
    ),
)


@dataclass
class AblationResult:
    """单 ablation experiment 结果."""
    experiment_name: str
    feature_groups_used: list[str]
    n_features: int
    walk_forward_result: WalkForwardResult

    @property
    def mean_rank_ic(self) -> float:
        return self.walk_forward_result.overall_rank_ic.mean_rank_ic

    @property
    def ic_ir(self) -> float:
        return self.walk_forward_result.overall_rank_ic.ic_ir

    @property
    def n_windows(self) -> int:
        return self.walk_forward_result.n_windows


@dataclass
class AblationSuite:
    """完整 ablation 套件结果."""
    baseline: AblationResult
    drop_one: list[AblationResult] = field(default_factory=list)  # drop 单 group
    add_one: list[AblationResult] = field(default_factory=list)   # 只用单 group

    def summary(self) -> dict:
        """Return tabular summary {experiment_name: {rank_ic, ic_ir, delta_vs_baseline}}."""
        bl_ic = self.baseline.mean_rank_ic
        out = {
            self.baseline.experiment_name: {
                "rank_ic": bl_ic,
                "ic_ir": self.baseline.ic_ir,
                "n_features": self.baseline.n_features,
                "n_windows": self.baseline.n_windows,
                "delta_vs_baseline": 0.0,
            }
        }
        for r in self.drop_one + self.add_one:
            out[r.experiment_name] = {
                "rank_ic": r.mean_rank_ic,
                "ic_ir": r.ic_ir,
                "n_features": r.n_features,
                "n_windows": r.n_windows,
                "delta_vs_baseline": r.mean_rank_ic - bl_ic,
            }
        return out


def _columns_to_use(
    all_available: set[str],
    groups: Iterable[FeatureGroup],
) -> list[str]:
    """从 groups 收集 columns, 只保留实际可用 (panel 含的) 列."""
    used: list[str] = []
    for g in groups:
        for c in g.columns:
            if c in all_available:
                used.append(c)
    return sorted(set(used))


def run_ablation_suite(
    rows: list[dict],
    groups: Iterable[FeatureGroup] = DEFAULT_GROUPS,
    base_cfg: LightGBMWalkForwardConfig | None = None,
) -> AblationSuite:
    """跑 baseline + drop-one + add-one ablation suite.

    Args:
        rows: from mart_p0a_feature_label_panel.
        groups: feature groups; default DEFAULT_GROUPS.
        base_cfg: LightGBM config; default LightGBMWalkForwardConfig().

    Returns:
        AblationSuite with baseline + drop_one + add_one results.
    """
    base_cfg = base_cfg or LightGBMWalkForwardConfig()
    groups_list = list(groups)
    if not rows:
        raise ValueError("Empty rows; nothing to ablate")

    # Determine available feature columns
    available = set(rows[0].keys())

    # ─── 1. Baseline: 全 groups ───
    baseline_cols = _columns_to_use(available, groups_list)
    bl_cfg = LightGBMWalkForwardConfig(
        label_field=base_cfg.label_field,
        min_train_months=base_cfg.min_train_months,
        forward_months=base_cfg.forward_months,
        n_estimators=base_cfg.n_estimators,
        learning_rate=base_cfg.learning_rate,
        num_leaves=base_cfg.num_leaves,
        feature_fraction=base_cfg.feature_fraction,
        bagging_fraction=base_cfg.bagging_fraction,
        bagging_freq=base_cfg.bagging_freq,
        min_child_samples=base_cfg.min_child_samples,
        random_state=base_cfg.random_state,
        feature_columns=baseline_cols,
    )
    log.info(f"Ablation: baseline n_features={len(baseline_cols)}")
    bl_result = train_lightgbm_walkforward(rows, bl_cfg)
    baseline = AblationResult(
        experiment_name="baseline_all_groups",
        feature_groups_used=[g.name for g in groups_list],
        n_features=len(baseline_cols),
        walk_forward_result=bl_result,
    )

    suite = AblationSuite(baseline=baseline)

    # ─── 2. Drop-one ablation ───
    for drop_g in groups_list:
        remaining = [g for g in groups_list if g.name != drop_g.name]
        cols = _columns_to_use(available, remaining)
        if not cols:
            continue
        cfg = LightGBMWalkForwardConfig(
            label_field=base_cfg.label_field,
            min_train_months=base_cfg.min_train_months,
            forward_months=base_cfg.forward_months,
            n_estimators=base_cfg.n_estimators,
            learning_rate=base_cfg.learning_rate,
            num_leaves=base_cfg.num_leaves,
            feature_columns=cols,
        )
        log.info(f"Ablation drop {drop_g.name}: n_features={len(cols)}")
        result = train_lightgbm_walkforward(rows, cfg)
        suite.drop_one.append(AblationResult(
            experiment_name=f"drop_{drop_g.name}",
            feature_groups_used=[g.name for g in remaining],
            n_features=len(cols),
            walk_forward_result=result,
        ))

    # ─── 3. Add-one ablation (只含单组) ───
    for add_g in groups_list:
        cols = _columns_to_use(available, [add_g])
        if not cols:
            continue
        cfg = LightGBMWalkForwardConfig(
            label_field=base_cfg.label_field,
            min_train_months=base_cfg.min_train_months,
            forward_months=base_cfg.forward_months,
            n_estimators=base_cfg.n_estimators,
            learning_rate=base_cfg.learning_rate,
            num_leaves=base_cfg.num_leaves,
            feature_columns=cols,
        )
        log.info(f"Ablation add only {add_g.name}: n_features={len(cols)}")
        result = train_lightgbm_walkforward(rows, cfg)
        suite.add_one.append(AblationResult(
            experiment_name=f"only_{add_g.name}",
            feature_groups_used=[add_g.name],
            n_features=len(cols),
            walk_forward_result=result,
        ))

    return suite
