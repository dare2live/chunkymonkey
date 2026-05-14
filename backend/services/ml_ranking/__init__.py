"""P0b ML ranking — LightGBM pointwise + walk-forward expanding_monthly.

PLAN_V3 v3.2 P0b: ML ranking 主导, 替换 V3 两路合并. 月度 walk-forward, OOS
RankIC ≥ 0.03, cost-after ann > +3.78%.

接入:
    from services.ml_ranking import (
        train_lightgbm_walkforward,
        compute_rank_ic,
    )
"""
from services.ml_ranking.rank_ic import compute_rank_ic, compute_cross_section_ic
from services.ml_ranking.lightgbm_walkforward import (
    LightGBMWalkForwardConfig,
    train_lightgbm_walkforward,
)

__all__ = [
    "compute_rank_ic",
    "compute_cross_section_ic",
    "LightGBMWalkForwardConfig",
    "train_lightgbm_walkforward",
]
