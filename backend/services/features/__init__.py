"""Phase 4 #2 alpha feature engineering (Codex round 19 verdict #1 priority).

新增 alpha factors 候选 (按 Codex 推荐 ROI 排序):
1. time_of_month: 月初/月末效应 (无 join, 仅日期算)
2. market_cap_decile: 大小盘 SMB factor (TODO)
3. industry_beta: stock vs industry residual (TODO)
4. capital_flow: 北向 5d 净买入 / 融资余额变化 (TODO)
5. institution_visit: 调研事件 7d/30d count (TODO)
6. sector_momentum: 28 行业 30d return rank (复用 fact_sector_momentum_daily)
"""
from services.features.time_of_month import build_time_of_month_features, feature_names as tom_feature_names
from services.features.market_cap_decile import build_market_cap_features, feature_names as mc_feature_names
from services.features.industry_beta import build_industry_beta_features, feature_names as ib_feature_names

__all__ = [
    "build_time_of_month_features",
    "tom_feature_names",
    "build_market_cap_features",
    "mc_feature_names",
    "build_industry_beta_features",
    "ib_feature_names",
]
