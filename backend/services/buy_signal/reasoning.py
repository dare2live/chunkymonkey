"""Phase η+++++ — 买点理由文本生成 (单一职责).

输入: FactorScores + 各原始字段
输出: 人类可读的中文理由字符串 (供 UI 展示)
"""
from __future__ import annotations

from typing import Optional

from services.buy_signal.factor_aggregator import FactorScores


def generate_reasoning(
    factors: FactorScores,
    *,
    formula_variant: str,
    today_technical_stage: Optional[str] = None,
    fundamental_stage: Optional[str] = None,
    survey_bin: Optional[str] = None,
    sharpe: Optional[float] = None,
    win_rate: Optional[float] = None,
    is_best_bucket: bool = False,
    n_traded: Optional[int] = None,
    stock_archetype: Optional[str] = None,
    primary_type: Optional[str] = None,
) -> str:
    """生成 ≤120 字符的买点理由 (按贡献从大到小)."""
    reasons: list[str] = []

    if factors.trigger >= 1.0:
        reasons.append(f"{_formula_short_name(formula_variant)}触发")
    else:
        return "今日无触发"

    if factors.historical_alpha >= 0.6 and sharpe is not None and win_rate is not None:
        reasons.append(f"Sharpe {sharpe:+.2f}/胜率{win_rate*100:.0f}%")
    elif factors.historical_alpha >= 0.3:
        reasons.append("中等 alpha")

    if factors.bucket_match >= 0.7:
        reasons.append("5维桶吻合")
    elif factors.bucket_match >= 0.4:
        reasons.append("桶吻合(小样本)")

    if factors.stage_fitness >= 0.85 and today_technical_stage:
        reasons.append(f"阶段{today_technical_stage}匹配(数据驱动)")
    elif factors.stage_fitness <= 0.0 and today_technical_stage == "4":
        reasons.append("⚠阶段4顶部")

    if factors.fundamental_stage <= 0.0 and fundamental_stage in ("失效破坏", "已充分演绎"):
        reasons.append(f"⚠基本面{fundamental_stage}")
    elif factors.fundamental_stage >= 0.9 and fundamental_stage in ("周期复苏", "温和验证"):
        reasons.append(f"基本面{fundamental_stage}")

    if factors.stock_archetype >= 0.85 and stock_archetype:
        reasons.append(f"原型{stock_archetype}最匹配")
    elif factors.stock_archetype <= 0.6 and stock_archetype:
        reasons.append(f"⚠原型{stock_archetype}弱匹配")

    if factors.primary_type >= 0.85 and primary_type and primary_type != "—":
        reasons.append(f"{primary_type}型")

    if factors.sentiment >= 1.0 and survey_bin == "狂":
        reasons.append("调研狂热")
    elif factors.sentiment >= 0.7 and survey_bin == "热":
        reasons.append("调研活跃")

    text = " · ".join(reasons)
    return text[:160]


def _formula_short_name(variant: str) -> str:
    """公式 variant → UI 短名."""
    return {
        "macd_golden_cross_above_zero": "MACD金叉(高位)",
        "macd_golden_cross_below_zero": "MACD金叉(底部)",
        "turtle_breakout_20": "Turtle20突破",
        "turtle_breakout_55": "Turtle55突破",
        "dynamic_ma_iterative_cross": "动均线多重金叉",
    }.get(variant, variant)
