"""
constants.py — 全局共享常量

所有业务常量的唯一真相源，禁止在其他模块硬编码同类值。
"""

# ──────────────────────────────────────────────
# 路径分类阈值 — stock_stage_engine + scoring 共用
# ──────────────────────────────────────────────
PATH_THRESHOLDS = {
    "mild_gain_max": 10.0,     # 未充分演绎上限 (%)
    "warm_gain_max": 30.0,     # 温和验证上限 (%)
    "exhausted_min": 30.0,     # 已充分演绎下限 (%)
    "broken_drawdown": 15.0,   # 失效破坏回撤阈值 (%)
}

# ETF 分类体系 (ETF_INDUSTRY_MAP 等) 已删 2026-06-29 (批3d: ETF 子系统整体退役, 唯一消费方
#   etf_engine/etf_mining_engine/etf-*.js frontend widget 均已物删, 0 外部消费方)

# CHANGE_MAP (东财 hold_change→事件类型映射) 已删 2026-06-28 (残留清理批1: event_engine+holdings serving 退役,
#   0 外部消费方; holders_aif10 用自己的 _parse_change)。

# ──────────────────────────────────────────────
# 评分体系常量 — scoring.py 共用，禁止在其他模块硬编码同类值
# ──────────────────────────────────────────────

# 复合评分权重（四维加权平均）
COMPOSITE_WEIGHTS = {
    "discovery": 0.35,
    "quality": 0.30,
    "stage": 0.20,
    "forecast": 0.15,
}

# 复合评分封顶规则
CEILING_RULES = {
    "stage_floor": 40,             # 阶段分低于此值 → D池风险
    "stage_floor_cap": 69.0,       # 阶段分低时的封顶值
    "quality_floor": 45,           # 质量分低于此值 → 封顶（非周期类）
    "quality_floor_cap": 64.0,     # 质量分低时的封顶值
    "crowding_severe": 8,          # 严重拥挤阈值
    "crowding_severe_cap": 69.0,   # 严重拥挤时封顶值
    "crowding_moderate": 6,        # 中度拥挤阈值
    "crowding_moderate_stage": 60, # 中度拥挤配合阶段分阈值
    "crowding_moderate_cap": 74.0, # 中度拥挤时封顶值
    "quality_exempt_archetypes": ("周期/事件驱动型",),  # 质量封顶豁免的股票原型
}

# 池子门限
POOL_THRESHOLDS = {
    "a_composite": 75,      # A池复合分门限
    "a_stage": 50,           # A池阶段分门限
    "a_quality": 55,         # A池质量分门限
    "a_discovery": 50,       # A池发现分门限
    "b_composite": 60,       # B池复合分门限
    "d_composite": 45,       # D池复合分门限
    "d_stage": 40,           # D池阶段分门限（同 ceiling stage_floor）
    "ext_attn_promote": 70,  # 外部关注分晋B池阈值
    "ext_attn_confirm": 72,  # 外部确认阈值
}

# 事件类型得分
EVENT_TYPE_SCORES = {
    "new_entry": 100,
    "increase": 70,
    "unchanged": 30,
    "decrease": 10,
    "exit": 0,
}

# Setup 显著性门限
SETUP_LEVEL_THRESHOLDS = {
    "level3": {"min_samples": 5, "min_edge_raw": 2.5},
    "level2": {"min_samples": 8, "min_edge_raw": 2.0},
    "level1": {"min_samples": 12, "min_edge_raw": 1.5},
}

# 外部关注度权重
ATTENTION_WEIGHTS = {
    "composite": 0.42,
    "focus": 0.30,
    "participation": 0.28,
    "survey": 0.18,
}

# 外部关注度 Boost 参数
ATTENTION_BOOST_PARAMS = {
    "baseline": 55.0,          # Boost 起算基准
    "multiplier": 0.18,        # 每超过基准1分的加成系数
    "survey_high_count": 2,    # 调研高活跃阈值（30日）
    "survey_high_pts": 0.8,    # 高活跃加成
    "survey_low_pts": 0.4,     # 低活跃加成
    "max_boost": 8.0,          # 最大 Boost 上限
}

# 拥挤惩罚上限
CROWDING_PENALTY_CAP = 10.0
