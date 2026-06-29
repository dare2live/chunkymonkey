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

# ──────────────────────────────────────────────
# ETF 分类体系 — etf_engine + etf_mining_engine + frontend 共用
# ──────────────────────────────────────────────

# 非行业类 ETF（不参与行业轮动排序）
ETF_NON_INDUSTRY_CATS: frozenset[str] = frozenset({
    "跨境", "商品", "债券", "货币", "宽基",
})

# 行业关键词 → 行业名映射（顺序无关，按首匹配）
ETF_INDUSTRY_MAP: list[tuple[list[str], str]] = [
    (["医疗", "医药", "生物科技", "医健", "生科", "中药", "医械", "创新药", "健康", "医疗器械", "疫苗"], "医疗健康"),
    (["半导体", "芯片", "集成电路", "科创芯", "晶圆"], "半导体"),
    (["新能源", "光伏", "风电", "储能", "氢能", "电池", "锂电", "碳中和", "绿色电力", "清洁能源"], "新能源"),
    (["消费", "白酒", "食品", "饮料", "家电", "零售", "酒", "家居", "纺织"], "消费"),
    (["银行", "券商", "保险", "金融", "证券", "理财", "非银"], "金融"),
    (["军工", "航空", "航天", "国防", "船舶"], "军工"),
    (["地产", "房产", "建筑", "建材", "基建"], "地产建筑"),
    (["农业", "农林", "畜牧", "化工", "煤炭", "钢铁", "矿业", "稀土", "有色金属", "资源"], "周期资源"),
    (["游戏", "传媒", "文化", "互联网", "数字", "云计算", "大数据", "人工智能", "AI", "信息", "软件", "计算机", "通信", "5G", "物联网"], "数字科技"),
    (["交通", "港口", "铁路", "物流", "高速", "公路", "航运"], "交通物流"),
    (["电力", "电气", "电网", "公用事业", "水务", "燃气", "环保"], "电力公用"),
    (["汽车", "智能车", "新能车", "车联网", "无人驾驶"], "汽车"),
    (["科技", "高端制造", "机器人", "工业母机", "先进制造", "装备"], "高端制造"),
    (["红利", "央企", "国企", "价值", "高股息"], "红利策略"),
]

# 所有行业名称（有序，供前端排序用）
ETF_INDUSTRY_NAMES: list[str] = [name for _, name in ETF_INDUSTRY_MAP]

# 分类排序权重（数字越小越靠前）
ETF_CATEGORY_SORT_ORDER: dict[str, int] = {
    "宽基": 0,
    **{name: idx + 1 for idx, name in enumerate(ETF_INDUSTRY_NAMES)},
    "行业·其他": len(ETF_INDUSTRY_NAMES) + 1,
    "跨境": len(ETF_INDUSTRY_NAMES) + 2,
    "商品": len(ETF_INDUSTRY_NAMES) + 3,
    "债券": len(ETF_INDUSTRY_NAMES) + 4,
    "货币": len(ETF_INDUSTRY_NAMES) + 5,
}

# 未命中行业关键词的 ETF 分类
ETF_FALLBACK_INDUSTRY = "行业·其他"

# 跨境关键词
ETF_CROSS_BORDER_KW: list[str] = [
    "纳指", "标普", "纳斯达克", "恒生", "港股", "中概", "海外", "亚太",
    "德国", "日经", "美国", "h股", "巴西", "印度", "越南", "法国", "欧洲", "日本",
]

# 商品关键词
ETF_COMMODITY_KW: list[str] = ["黄金", "白银", "豆粕", "有色", "能化", "原油", "商品"]

# 债券关键词
ETF_BOND_KW: list[str] = ["国债", "信用债", "可转债", "城投", "公司债", "债券", "短融"]

# 货币关键词
ETF_MONEY_KW: list[str] = ["货币", "货a", "货b", "现金", "快线", "添益", "日利", "日日盈", "保证金"]

# 宽基关键词
ETF_BROAD_KW: list[str] = [
    "沪深300", "中证500", "中证1000", "上证50", "科创50", "科创100",
    "创业板", "深证100", "中证800", "中证2000", "A50", "msci", "MSCI",
]

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
