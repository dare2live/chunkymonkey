"""Phase γ D1 — fundamental_stage 分类器单测。

覆盖 audit 实证的 7 个 stage_reason 模板 + 边界 (空/未匹配/混合关键词)。
"""
from __future__ import annotations

import pytest

from services.picture.fundamental_stage import (
    FUNDAMENTAL_STAGES,
    classify_fundamental_stage,
    stage_confidence,
)


class TestClassifyFundamentalStage:
    """6 状态分类 - 基于 D1 audit 的 7 个真实模板。"""

    def test_steady_continuation_maps_to_mild_validation(self):
        # 稳健型基本面续航与趋势健康较好 (audit n=227)
        result = classify_fundamental_stage("稳健型基本面续航与趋势健康较好")
        assert result == "温和验证"

    def test_growth_extending_maps_to_mild_validation(self):
        # 成长型增速延续尚可，阶段仍具跟踪价值 (audit n=98)
        result = classify_fundamental_stage("成长型增速延续尚可，阶段仍具跟踪价值")
        assert result == "温和验证"

    def test_cyclic_recovery_maps_to_recovery(self):
        # 周期/事件型处于修复展开阶段 (audit n=560)
        result = classify_fundamental_stage("周期/事件型处于修复展开阶段")
        assert result == "周期复苏"

    def test_overheat_maps_to_fully_developed(self):
        # 稳健型短期存在过热迹象 (audit n=91)
        result = classify_fundamental_stage("稳健型短期存在过热迹象")
        assert result == "已充分演绎"

    def test_realization_pressure_maps_to_fully_developed(self):
        # 周期/事件型兑现或不确定性压力偏大 (audit n=1254, 最常见)
        result = classify_fundamental_stage("周期/事件型兑现或不确定性压力偏大")
        assert result == "已充分演绎"

    def test_growth_slowdown_maps_to_failure(self):
        # 成长型已出现放缓或价格透支信号 (audit n=13, 强 sell 信号)
        result = classify_fundamental_stage("成长型已出现放缓或价格透支信号")
        assert result == "失效破坏"

    def test_neutral_maps_to_neutral(self):
        # 阶段结构中性 (audit n=1112)
        result = classify_fundamental_stage("阶段结构中性")
        assert result == "中性"

    def test_none_returns_underdeveloped(self):
        assert classify_fundamental_stage(None) == "未充分演绎"

    def test_empty_string_returns_underdeveloped(self):
        assert classify_fundamental_stage("") == "未充分演绎"

    def test_unrecognized_returns_underdeveloped(self):
        assert classify_fundamental_stage("完全不在模板里的文本") == "未充分演绎"


class TestPriorityOrdering:
    """规则顺序 - 强 sell 信号优先于模糊关键词。"""

    def test_failure_beats_overheat_when_both_present(self):
        # 同时含"透支" + "过热" → 失效破坏胜出 (因失效在规则表更前)
        result = classify_fundamental_stage("xxx 过热 yyy 透支 zzz")
        assert result == "失效破坏"

    def test_overheat_beats_neutral(self):
        result = classify_fundamental_stage("某种过热, 总体中性")
        assert result == "已充分演绎"


class TestStageConfidence:
    def test_none_gives_zero(self):
        assert stage_confidence(None, None) == 0.0

    def test_strong_template_gives_high(self):
        # audit 实证的整句模板
        c = stage_confidence("稳健型基本面续航与趋势健康较好", None)
        assert 0.79 <= c <= 0.81  # base 0.8

    def test_strong_template_with_high_score_gets_bonus(self):
        c = stage_confidence("稳健型基本面续航与趋势健康较好", 50.0)
        assert 0.89 <= c <= 0.91  # base 0.8 + 0.1 bonus

    def test_weak_keyword_gives_mid(self):
        # 只命中关键词,不是整句模板
        c = stage_confidence("自由文本含 兑现 字样", None)
        assert 0.39 <= c <= 0.41  # base 0.4

    def test_confidence_never_exceeds_one(self):
        c = stage_confidence("成长型增速延续尚可", 100.0)
        assert c <= 1.0


class TestEnum:
    def test_six_states_canonical(self):
        # 公示集合, UI 侧依赖
        assert FUNDAMENTAL_STAGES == {
            "未充分演绎", "温和验证", "已充分演绎",
            "失效破坏", "周期复苏", "中性",
        }
