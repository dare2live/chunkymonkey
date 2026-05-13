"""Phase γ — fundamental_stage 分类器。

把 dim_stock_stage_latest.stage_reason (7 个固定模板) 映射为 6 状态:

| stage_reason 模板 (audit 实证 7 个)          | 派生 fundamental_stage |
|--------------------------------------------|----------------------|
| 稳健型基本面续航与趋势健康较好                  | 温和验证               |
| 成长型增速延续尚可，阶段仍具跟踪价值              | 温和验证               |
| 周期/事件型处于修复展开阶段                     | 周期复苏               |
| 稳健型短期存在过热迹象                         | 已充分演绎              |
| 周期/事件型兑现或不确定性压力偏大                 | 已充分演绎              |
| 成长型已出现放缓或价格透支信号                   | 失效破坏               |
| 阶段结构中性                                  | 中性                  |
| (其它/未匹配 — 占 0)                          | 未充分演绎              |

注: path_state 是死字段 (全 3,355 行只有"未充分演绎"一个值, 不可用),
所以我们用 stage_reason 文本模板派生, 而不是直接 copy path_state。

输入: 单行 stage_reason (TEXT)
输出: 6 状态字符串之一 + 派生 confidence (0-1)

纯函数, 容易测试, 容易换路由。
"""
from __future__ import annotations


# 6 状态枚举 (UI 渲染 / 过滤 都依赖这个集合)
FUNDAMENTAL_STAGES = {
    "未充分演绎",   # 信息不足 / 默认占位
    "温和验证",     # 持续向好,但未过热
    "已充分演绎",   # 过热 / 兑现, 该考虑减仓
    "失效破坏",     # 增长破位 / 价格透支
    "周期复苏",     # 周期股低位修复
    "中性",         # 模板无明显方向
}

# (stage_reason 子串 → fundamental_stage) 优先级有序, 第一个匹配胜出
_TEMPLATE_RULES: tuple[tuple[str, str], ...] = (
    # 失效优先识别 (最强 sell 信号)
    ("成长型已出现放缓或价格透支信号", "失效破坏"),
    ("放缓",   "失效破坏"),
    ("透支",   "失效破坏"),
    # 过热 / 兑现 = 已充分演绎
    ("稳健型短期存在过热迹象",     "已充分演绎"),
    ("过热",                    "已充分演绎"),
    ("兑现或不确定性压力偏大",     "已充分演绎"),
    ("兑现",                    "已充分演绎"),
    # 周期复苏
    ("周期/事件型处于修复展开阶段", "周期复苏"),
    ("修复展开",                "周期复苏"),
    ("修复",                    "周期复苏"),
    # 温和验证 = 健康发展
    ("稳健型基本面续航与趋势健康较好", "温和验证"),
    ("续航",                       "温和验证"),
    ("成长型增速延续尚可",            "温和验证"),
    ("延续尚可",                    "温和验证"),
    # 中性兜底
    ("阶段结构中性",  "中性"),
    ("中性",         "中性"),
)


def classify_fundamental_stage(stage_reason: str | None) -> str:
    """返回 6 状态之一。

    None / 空字符串 / 未匹配 → 未充分演绎 (信息不足)。
    """
    if not stage_reason:
        return "未充分演绎"
    text = str(stage_reason)
    for substr, stage in _TEMPLATE_RULES:
        if substr in text:
            return stage
    return "未充分演绎"


def stage_confidence(stage_reason: str | None, stage_score_v1: float | None) -> float:
    """派生置信度 0-1。

    规则 (Occam):
      - 无 reason → 0
      - 命中明确模板 → 0.8
      - 命中弱关键词 → 0.4
      - 有 stage_score_v1 ≥ 40 时 +0.1
    """
    if not stage_reason:
        return 0.0
    text = str(stage_reason)
    # 明确模板 (整句, 见 _TEMPLATE_RULES 前 4 个)
    strong_templates = (
        "成长型已出现放缓或价格透支信号",
        "稳健型短期存在过热迹象",
        "兑现或不确定性压力偏大",
        "周期/事件型处于修复展开阶段",
        "稳健型基本面续航与趋势健康较好",
        "成长型增速延续尚可",
        "阶段结构中性",
    )
    base = 0.8 if any(t in text for t in strong_templates) else 0.4
    # 加分: stage_score_v1 高 表示 reason 更可信
    if stage_score_v1 is not None and stage_score_v1 >= 40:
        base = min(1.0, base + 0.1)
    return base
