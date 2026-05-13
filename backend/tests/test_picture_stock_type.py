"""Phase γ D2 — stock_type 分类器单测。

5 状态优先级 + 边界 + 无规则命中。
"""
from __future__ import annotations

import pytest

from services.picture.stock_type import PRIMARY_TYPES, classify_stock_type


class TestClassifyStockType:
    def test_no_rule_hits_returns_dash(self):
        out = classify_stock_type({})
        assert out["primary_type"] == "—"
        assert out["secondary_types"] == []
        assert out["reason_codes"] == []

    def test_event_driven_when_2plus_events(self):
        out = classify_stock_type({"event_count_30d": 3})
        assert out["primary_type"] == "事件驱动"
        assert "event_count_30d:3" in out["reason_codes"][0]

    def test_event_driven_priority_over_others(self):
        # 同时命中事件驱动 + 业绩驱动 → 事件驱动胜出 (规则 1 优先级最高)
        out = classify_stock_type({
            "event_count_30d": 2,
            "stock_archetype": "成长兑现型",
            "latest_profit_yoy": 0.50,
            "valuation_pe_pctile": 0.30,
        })
        assert out["primary_type"] == "事件驱动"
        assert "业绩驱动" in out["secondary_types"]

    def test_performance_driven_full_match(self):
        out = classify_stock_type({
            "stock_archetype": "成长兑现型",
            "latest_profit_yoy": 0.45,
            "valuation_pe_pctile": 0.50,
        })
        assert out["primary_type"] == "业绩驱动"

    def test_performance_driven_blocked_by_high_pe_pctile(self):
        # pe_pctile > 0.60 时拒绝
        out = classify_stock_type({
            "stock_archetype": "成长兑现型",
            "latest_profit_yoy": 0.45,
            "valuation_pe_pctile": 0.75,
        })
        # 落到 "—" 因为 PE 太贵
        assert out["primary_type"] == "—"

    def test_performance_driven_missing_pe_pctile_passes(self):
        # pe_pctile=None 视为不阻挡 (lenient)
        out = classify_stock_type({
            "stock_archetype": "成长兑现型",
            "latest_profit_yoy": 0.45,
            "valuation_pe_pctile": None,
        })
        assert out["primary_type"] == "业绩驱动"

    def test_value_repair(self):
        out = classify_stock_type({
            "stock_archetype": "高质量稳健型",
            "valuation_pe_pctile": 0.15,
        })
        assert out["primary_type"] == "价值修复"

    def test_value_repair_requires_high_quality_archetype(self):
        # 周期型 + 低 PE 不算价值修复
        out = classify_stock_type({
            "stock_archetype": "周期/事件驱动型",
            "valuation_pe_pctile": 0.10,
        })
        assert out["primary_type"] != "价值修复"

    def test_cyclical_recovery_via_fundamental_stage(self):
        out = classify_stock_type({"fundamental_stage": "周期复苏"})
        assert out["primary_type"] == "周期复苏"

    def test_cyclical_recovery_via_archetype_and_growth(self):
        out = classify_stock_type({
            "stock_archetype": "周期/事件驱动型",
            "latest_revenue_yoy": 0.15,
            "return_3m": 0.20,
        })
        assert out["primary_type"] == "周期复苏"

    def test_cyclical_recovery_needs_positive_growth(self):
        out = classify_stock_type({
            "stock_archetype": "周期/事件驱动型",
            "latest_revenue_yoy": -0.05,
            "return_3m": 0.20,
        })
        assert out["primary_type"] != "周期复苏"

    def test_technical_breakout(self):
        out = classify_stock_type({
            "formula_hits_last_5d": 2,
            "vol_ratio": 1.5,
        })
        assert out["primary_type"] == "技术突破"

    def test_technical_breakout_needs_volume_confirmation(self):
        out = classify_stock_type({
            "formula_hits_last_5d": 2,
            "vol_ratio": 1.1,    # < 1.3 → 不算技术突破
        })
        assert out["primary_type"] != "技术突破"

    def test_multiple_secondaries(self):
        # 业绩 + 周期 + 技术 三命中
        out = classify_stock_type({
            "stock_archetype": "成长兑现型",  # 业绩驱动
            "latest_profit_yoy": 0.40,
            "valuation_pe_pctile": 0.40,
            "fundamental_stage": "周期复苏",  # 周期复苏
            "formula_hits_last_5d": 1,        # 技术突破
            "vol_ratio": 1.5,
        })
        assert out["primary_type"] == "业绩驱动"
        # 不强求顺序, 只验集合
        assert set(out["secondary_types"]) == {"周期复苏", "技术突破"}


class TestEnum:
    def test_primary_types_canonical(self):
        assert "事件驱动" in PRIMARY_TYPES
        assert "业绩驱动" in PRIMARY_TYPES
        assert "价值修复" in PRIMARY_TYPES
        assert "周期复苏" in PRIMARY_TYPES
        assert "技术突破" in PRIMARY_TYPES
        assert "—" in PRIMARY_TYPES
