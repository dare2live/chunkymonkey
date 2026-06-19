"""DAG metadata for updater routes."""


def step_ids_for(steps):
    """Return step ids in route execution order."""

    return [step["id"] for step in steps]


def step_index_for(steps):
    """Return a step-id lookup for one route run."""

    return {step["id"]: step for step in steps}


def step_name_for(step_index, step_id: str) -> str:
    step = step_index.get(step_id) or {}
    return step.get("name", step_id)


def step_specs_for_group(steps, group_id: str):
    return [step for step in steps if step.get("group") == group_id]


def selected_step_specs(steps, selected: set[str]):
    return [
        step
        for step in steps
        if step["id"] in selected
    ]


def selected_dependency_ids(dependencies, selected: set[str]) -> list[str]:
    return [dependency for dependency in dependencies if dependency in selected]


def skipped_step_ids_outside(steps, selected: set[str]) -> list[str]:
    return [
        step["id"]
        for step in steps
        if step["id"] not in selected
    ]


STEPS = [
    {"id": "sync_calendar", "name": "交易日历前置", "group": "data", "order": 0},
    {"id": "sync_raw", "name": "下载十大股东", "group": "data", "order": 1},
    {"id": "match_inst", "name": "匹配跟踪机构", "group": "data", "order": 2},
    {"id": "sync_market_data", "name": "同步行情数据", "group": "data", "order": 3},
    {"id": "sync_financial", "name": "同步财务数据", "group": "data", "order": 4},
    {"id": "gen_events", "name": "生成事件", "group": "calc", "order": 5},
    {"id": "calc_returns", "name": "计算收益", "group": "calc", "order": 6},
    {"id": "sync_industry", "name": "通达信行业", "group": "data", "order": 7},
    {"id": "sync_surveys", "name": "机构调研", "group": "data", "order": 7.5},
    {"id": "sync_qfii", "name": "QFII 季报", "group": "data", "order": 7.6},
    # {"id": "sync_margin", ...} removed Phase psi.5: dead data, see audit.
    {"id": "sync_lhb", "name": "龙虎榜", "group": "data", "order": 7.8},
    {"id": "sync_aif10_valuation_quantile", "name": "妙想估值分位", "group": "data", "order": 7.82},
    {"id": "sync_aif10_peer_valuation", "name": "妙想同行估值", "group": "data", "order": 7.83},
    {"id": "sync_aif10_forecast_consensus", "name": "妙想一致预期", "group": "data", "order": 7.84},
    {"id": "calc_financial_derived", "name": "计算财务指标", "group": "calc", "order": 8},
    {"id": "build_current_rel", "name": "构建当前关系", "group": "mart", "order": 9},
    {"id": "build_profiles", "name": "机构画像", "group": "mart", "order": 10},
    {"id": "build_industry_stat", "name": "行业统计", "group": "mart", "order": 11},
    {"id": "build_trends", "name": "生成股票列表", "group": "mart", "order": 12},
    {"id": "calc_screening", "name": "TDX选股筛选", "group": "mart", "order": 13},
    {"id": "calc_sector_momentum", "name": "板块动量分析", "group": "mart", "order": 14},
    {"id": "build_external_attention", "name": "外部关注快照", "group": "mart", "order": 15},
    {"id": "build_stage_features", "name": "阶段特征构建", "group": "mart", "order": 16},
    {"id": "calc_risk_factors", "name": "风险因子", "group": "mart", "order": 16.5},
    {"id": "calc_prediction_outcomes", "name": "预测 outcome", "group": "mart", "order": 16.6},
    {"id": "build_turtle_features", "name": "海龟执行特征", "group": "mart", "order": 17.5},
    {"id": "calc_inst_scores", "name": "机构评分", "group": "mart", "order": 18},
    {"id": "calc_stock_scores", "name": "股票评分", "group": "mart", "order": 19},
    {"id": "refresh_today_signals", "name": "今日信号快照", "group": "mart", "order": 20},
]

# Hard dependency failure skips the downstream step.
HARD_DEPS = {
    "sync_calendar": [],
    "sync_raw": ["sync_calendar"],
    "match_inst": ["sync_raw"],
    "sync_market_data": ["sync_calendar", "match_inst"],
    "sync_financial": ["sync_calendar"],
    "gen_events": ["match_inst"],
    "calc_returns": ["gen_events"],
    "sync_industry": ["sync_calendar", "match_inst"],
    "sync_surveys": ["sync_calendar"],
    "sync_qfii": ["sync_calendar"],
    "sync_lhb": ["sync_calendar"],
    "sync_aif10_valuation_quantile": ["sync_calendar"],
    "sync_aif10_peer_valuation": ["sync_calendar"],
    "sync_aif10_forecast_consensus": ["sync_calendar"],
    "calc_financial_derived": ["sync_financial"],
    "build_current_rel": ["gen_events"],
    "build_profiles": ["build_current_rel"],
    "build_industry_stat": ["build_current_rel"],
    "build_trends": ["build_current_rel"],
    "calc_screening": ["sync_market_data"],
    "calc_sector_momentum": ["sync_market_data", "sync_industry"],
    "build_external_attention": [],
    "build_stage_features": ["build_trends", "calc_sector_momentum"],
    "build_turtle_features": ["build_stage_features"],
    "calc_inst_scores": ["build_profiles", "build_industry_stat"],
    "calc_stock_scores": ["calc_inst_scores", "build_stage_features"],
    "refresh_today_signals": ["calc_returns"],
}

# Soft dependency failure marks downstream completeness as partial but does not block.
SOFT_DEPS = {
    "calc_returns": ["sync_market_data"],
    "build_current_rel": ["calc_returns", "sync_industry"],
    "build_profiles": ["calc_returns"],
    "build_industry_stat": ["calc_returns", "sync_industry"],
    "build_trends": ["calc_returns", "sync_industry"],
    "calc_screening": ["calc_financial_derived"],
    "calc_sector_momentum": ["build_trends"],
    "build_external_attention": [],
    "build_stage_features": ["calc_financial_derived"],
    "build_turtle_features": [],
    "calc_inst_scores": ["calc_returns"],
    "calc_stock_scores": ["calc_returns", "build_external_attention"],
    "refresh_today_signals": ["sync_industry", "sync_financial", "sync_surveys"],
}

MANUAL_ONLY_STEPS = {"calc_screening", "build_turtle_features"}
