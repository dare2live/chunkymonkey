"""Phase 4 alpha feature engineering (Codex round 19 verdict #1 priority).

实际实施 (Codex round 19-22, 2026-05-17):
1. time_of_month: 月初/月末效应 (7 features, 无 join)
2. market_cap_decile: SMB factor (6 features)
3. industry_beta: rolling 60d residual (4 features)
4. capital_flow: PIT wrap fact_capital_flow_pit_daily LHB+高管+股东户数 (15 features)
5. sector_momentum: PIT industry + sector_momentum_daily — 实测 0% coverage (Pattern D 警告)
6. institution_survey: mart_stock_survey_features wrap (7 features)
7. forecast_upside: 一致预期 EPS × target_PE 上升空间 (纯函数, 等 PIT 累积)

Audit (2026-05-17 AUDIT_2026_05_17.md):
- 真有用: mcap_decile (corr 0.074), lhb_count_30d (0.055)
- CONST/dead: sector_momentum 9 + holder_count_change_q_pct (见 V5_FEATURE_PLAN.md)
- a158 top features 仍是 alpha 主力 (0.10-0.11)
"""
from services.features.time_of_month import build_time_of_month_features, feature_names as tom_feature_names
from services.features.market_cap_decile import build_market_cap_features, feature_names as mc_feature_names
from services.features.industry_beta import build_industry_beta_features, feature_names as ib_feature_names
from services.features.capital_flow import build_capital_flow_features, feature_names as cf_feature_names
from services.features.sector_momentum import build_sector_momentum_features, feature_names as sm_feature_names
from services.features.institution_survey import build_institution_survey_features, feature_names as is_feature_names
from services.features.forecast_upside import build_forecast_upside_features, feature_names as fu_feature_names

__all__ = [
    "build_time_of_month_features",
    "tom_feature_names",
    "build_market_cap_features",
    "mc_feature_names",
    "build_industry_beta_features",
    "ib_feature_names",
    "build_capital_flow_features",
    "cf_feature_names",
    "build_sector_momentum_features",
    "sm_feature_names",
    "build_institution_survey_features",
    "is_feature_names",
    "build_forecast_upside_features",
    "fu_feature_names",
]
