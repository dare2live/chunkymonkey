"""Phase η++ — portfolio_sizer 模块单测.

覆盖:
  - wilson lower bound (n=8/8=100% → 67.6%; n=100/100 → 96.4%)
  - Kelly fraction (高赔率 + 高胜率 → 大仓位; 反之 → 0)
  - sizing.rank_and_size (过滤 + 排序 + 仓位)
  - sell_rules.evaluate_sell (3 优先级: 止损/trailing/到期)
"""
from __future__ import annotations

import pytest


# ===================== Wilson =====================
class TestWilson:
    def test_wilson_naive_100pct_corrected(self):
        from services.portfolio_sizer.wilson import wilson_lower
        # 8/8 = 100% (朴素) → Wilson 95% 下界 ≈ 67.6%
        w = wilson_lower(8, 8, 0.95)
        assert 0.65 <= w <= 0.70

    def test_wilson_naive_with_large_n(self):
        from services.portfolio_sizer.wilson import wilson_lower
        # 90/100 = 90% → Wilson 95% 下界 ≈ 82.7%
        w = wilson_lower(90, 100, 0.95)
        assert 0.80 <= w <= 0.85

    def test_wilson_zero_returns_zero(self):
        from services.portfolio_sizer.wilson import wilson_lower
        assert wilson_lower(0, 0, 0.95) == 0.0
        assert wilson_lower(0, 10, 0.95) >= 0  # 0 wins / 10 → 下界很低

    def test_wilson_invalid_raises(self):
        from services.portfolio_sizer.wilson import wilson_lower
        with pytest.raises(ValueError):
            wilson_lower(11, 10, 0.95)

    def test_wilson_from_rate(self):
        from services.portfolio_sizer.wilson import wilson_from_rate
        # 0.875 × 8 = 7 wins, n=8 → 朴素 87.5%, Wilson ~52.9%
        w = wilson_from_rate(0.875, 8, 0.95)
        assert 0.50 <= w <= 0.60

    def test_bayesian(self):
        from services.portfolio_sizer.wilson import bayesian_win_rate
        # 8 wins 0 losses + Beta(2,2) prior = (2+8)/(4+8) = 0.833
        b = bayesian_win_rate(8, 8)
        assert abs(b - 0.833) < 0.01


# ===================== Kelly =====================
class TestKelly:
    def test_kelly_positive_edge(self):
        from services.portfolio_sizer.kelly import kelly_fraction
        # p=0.7, b=2 (赔率) → f* = (0.7*2 - 0.3)/2 = 0.55
        # fractional 0.5 → 0.275, cap 0.25 → 0.25
        f = kelly_fraction(win_rate=0.7, avg_ret=0.20, avg_dd=-0.10,
                          kelly_mul=0.5, max_f=0.25)
        assert 0.20 <= f <= 0.25

    def test_kelly_negative_edge_returns_zero(self):
        from services.portfolio_sizer.kelly import kelly_fraction
        # p=0.3, b=1 (赔率) → f* = (0.3 - 0.7)/1 = -0.4 → 0
        f = kelly_fraction(win_rate=0.3, avg_ret=0.10, avg_dd=-0.10)
        assert f == 0.0

    def test_kelly_no_dd_returns_zero(self):
        from services.portfolio_sizer.kelly import kelly_fraction
        f = kelly_fraction(win_rate=0.8, avg_ret=0.10, avg_dd=0.0)
        assert f == 0.0

    def test_kelly_high_win_high_payoff(self):
        from services.portfolio_sizer.kelly import kelly_fraction
        # p=0.9, b=5 (大赔率) → f* = (0.9*5-0.1)/5 = 0.88
        # fractional 0.25 → 0.22, cap 0.25 → 0.22
        f = kelly_fraction(win_rate=0.9, avg_ret=0.50, avg_dd=-0.10,
                          kelly_mul=0.25, max_f=0.25)
        assert 0.20 <= f <= 0.25


# ===================== Sizing =====================
class TestSizing:
    @pytest.fixture
    def short_profile(self):
        from services.portfolio_sizer.profiles import get_profile
        return get_profile("short")

    def test_rank_and_size_filters_low_win(self, short_profile):
        from services.portfolio_sizer.sizing import rank_and_size
        # 一组 candidates, 部分应被过滤
        # 短期 profile: min_wilson_win=0.60, min_n=5, hp ∈ {5,10,15}
        # Wilson 0.95: 18/20 → 0.699, 8/10 → 0.49, 17/20 → 0.637
        cands = [
            {"stock_code": "A", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 30, "win_rate": 0.90, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证"},  # 通过 Wilson(27/30)~0.74
            {"stock_code": "B", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.40, "avg_ret": 0.05,
             "avg_dd": -0.03, "calmar": 1.5, "signal_close": 50.0,
             "fundamental_stage": "温和验证"},  # win_rate 太低
            {"stock_code": "C", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 60, "n_signals": 20, "win_rate": 0.90, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 30.0},  # hp 不在 short
            {"stock_code": "D", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 2, "win_rate": 1.0, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 80.0,
             "fundamental_stage": "温和验证"},  # n 太少
            # E 没用了 — Phase η+++++ 已砍 exclude_fund_stages (fundamental_stage 历史不可重建,
            # 无法回测验证). 测试反映新设计: 只过滤 hp/n/Wilson, 不过滤 stage.
        ]
        out = rank_and_size(cands, short_profile)
        codes = [r["stock_code"] for r in out]
        assert "A" in codes
        assert "B" not in codes
        assert "C" not in codes
        assert "D" not in codes

    def test_rank_and_size_max_positions(self, short_profile):
        from services.portfolio_sizer.sizing import rank_and_size
        # 8 candidates 全合格, 短期 profile max=5
        cands = [
            {"stock_code": f"S{i}", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.80, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证"}
            for i in range(8)
        ]
        out = rank_and_size(cands, short_profile)
        assert len(out) <= short_profile.max_positions

    def test_rank_and_size_position_sum_under_90pct(self, short_profile):
        from services.portfolio_sizer.sizing import rank_and_size
        cands = [
            {"stock_code": f"S{i}", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.80, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证"}
            for i in range(8)
        ]
        out = rank_and_size(cands, short_profile)
        total = sum(r["position_pct"] for r in out)
        assert total <= 0.91  # ≤ 90% 留 cash, 1% 浮动容差

    def test_rank_and_size_uses_optimal_params_not_default(self, short_profile):
        """Phase ζ 防护: 当 candidate 携带 optimal_stop/target/trailing,
        sizing 必须用这些参数算 stop/target/trailing, 不能 fallback 到 DEFAULT_STRATEGY."""
        from services.portfolio_sizer.sizing import rank_and_size
        cands = [
            # 携带 per-stock 寻优出的非默认参数
            {"stock_code": "A", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 30, "win_rate": 0.90, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证",
             # ζ 寻优出来的非默认值
             "optimal_stop_pct": -0.09,    # vs default -0.06
             "optimal_target_pct": 0.18,   # vs default +0.10
             "optimal_trailing_pct": 0.045,# vs default 0.025
             },
        ]
        out = rank_and_size(cands, short_profile)
        assert len(out) == 1
        r = out[0]
        # stop_price 应基于 optimal_stop_pct -0.09, 不是 -0.06
        assert r["stop_price"] == pytest.approx(r["buy_price"] * 0.91, rel=0.01)
        # sell_target 应基于 +0.18, 不是 +0.10
        assert r["sell_target"] == pytest.approx(r["buy_price"] * 1.18, rel=0.01)
        # trailing 应 = 0.045, 不是 0.025
        assert r["trailing_pct"] == pytest.approx(0.045)

    def test_rank_and_size_fallback_to_default_when_no_optimal(self, short_profile):
        """Phase ζ 防护: candidate 无 optimal_* 字段时, fallback 到 DEFAULT_STRATEGY (不崩)."""
        from services.portfolio_sizer.sizing import rank_and_size
        from services.backtest.strategy_defaults import DEFAULT_STRATEGY
        cands = [
            {"stock_code": "B", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 30, "win_rate": 0.90, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 50.0,
             "fundamental_stage": "温和验证",
             # 无 optimal_* 字段
             },
        ]
        out = rank_and_size(cands, short_profile)
        assert len(out) == 1
        r = out[0]
        # 应回退到 DEFAULT_STRATEGY
        assert r["stop_price"] == pytest.approx(
            r["buy_price"] * (1 + DEFAULT_STRATEGY.stop_pct), rel=0.01)
        assert r["sell_target"] == pytest.approx(
            r["buy_price"] * (1 + DEFAULT_STRATEGY.target_pct), rel=0.01)
        assert r["trailing_pct"] == pytest.approx(DEFAULT_STRATEGY.trailing_pct)

    def test_rank_and_size_dedup_same_stock(self, short_profile):
        """Phase η+++: 同股不同 hp/variant 只保留最高分一条 (portfolio 视角去重)."""
        from services.portfolio_sizer.sizing import rank_and_size
        # 同股 A: 2 个 hp (5/10/15 都在 short.hp), 1 个不同 variant
        cands = [
            # 股 A, hp=5  低分
            {"stock_code": "A", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 5, "n_signals": 30, "win_rate": 0.85, "avg_ret": 0.05,
             "avg_dd": -0.05, "calmar": 1.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证"},
            # 股 A, hp=10 高分
            {"stock_code": "A", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 30, "win_rate": 0.95, "avg_ret": 0.20,
             "avg_dd": -0.05, "calmar": 4.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证"},
            # 股 A, hp=10 turtle variant
            {"stock_code": "A", "formula_id": "turtle", "formula_variant": "turtle_v1",
             "holding_days": 10, "n_signals": 30, "win_rate": 0.90, "avg_ret": 0.15,
             "avg_dd": -0.05, "calmar": 3.0, "signal_close": 100.0,
             "fundamental_stage": "温和验证"},
            # 股 B (对照)
            {"stock_code": "B", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 30, "win_rate": 0.85, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 50.0,
             "fundamental_stage": "温和验证"},
        ]
        out = rank_and_size(cands, short_profile)
        codes = [r["stock_code"] for r in out]
        # 股 A 只出现一次 (portfolio 视角同股去重)
        assert codes.count("A") == 1
        # 股 A 保留的是最高分一条 (hp=10 macd_v1, win=0.95 calmar=4.0)
        a_row = next(r for r in out if r["stock_code"] == "A")
        assert a_row["holding_days"] == 10
        assert a_row["formula_variant"] == "macd_v1"

    def test_rank_and_size_prioritizes_pit_tier_over_higher_score_fallback(self, short_profile):
        """PIT 安全候选应优先于更高分的 cross-stage fallback."""
        from services.portfolio_sizer.sizing import rank_and_size

        cands = [
            # PIT-safe 候选，分数略低
            {"stock_code": "PIT", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.84, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 1.6, "signal_close": 100.0,
             "match_tier": "stage_pit"},
            # cross-stage fallback，原始 score 更高
            {"stock_code": "XSTAGE", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.95, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.4, "signal_close": 100.0,
             "match_tier": "cross_stage_fallback"},
        ]

        out = rank_and_size(cands, short_profile)
        assert [r["stock_code"] for r in out] == ["PIT", "XSTAGE"]
        assert out[0]["match_tier"] == "stage_pit"
        assert out[0]["score"] < out[1]["score"]

    def test_summarize_profile_attrition_counts_dropoff_and_pit_priority(self, short_profile):
        from services.portfolio_sizer.attrition import summarize_profile_attrition

        cands = [
            # PIT-safe 候选，score 低一点但应优先保留
            {"stock_code": "PIT", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.84, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 1.6, "signal_close": 100.0,
             "match_tier": "stage_pit"},
            # 更高分 fallback
            {"stock_code": "XSTAGE", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.95, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.4, "signal_close": 100.0,
             "match_tier": "cross_stage_fallback"},
            # 直接在样本数门槛上被拦掉
            {"stock_code": "LOWN", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 2, "win_rate": 1.0, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "match_tier": "cross_stage_fallback"},
        ]

        summary = summarize_profile_attrition(cands, short_profile, max_examples=2)
        assert summary["input_rows"] == 3
        assert summary["stage_reached"] == {
            "hp": 3,
            "n_signals": 2,
            "avg_ret": 2,
            "fund_stage": 2,
            "wilson": 2,
            "kelly": 2,
        }
        assert summary["fail_reasons"] == {"n_signals": 1}
        assert summary["fail_reasons_by_match_tier"] == {
            "cross_stage_fallback": {"n_signals": 1},
        }
        assert summary["fail_holding_days_by_match_tier"] == {
            "cross_stage_fallback": {10: 1},
        }
        assert summary["after_filter_rows"] == 2
        assert summary["selected_rows"] == 2
        assert summary["selected_match_tiers"] == {
            "stage_pit": 1,
            "cross_stage_fallback": 1,
        }
        assert [row["stock_code"] for row in summary["selected_examples"]] == ["PIT", "XSTAGE"]
        assert summary["selected_examples"][0]["score"] < summary["selected_examples"][1]["score"]

    def test_summarize_profile_attrition_breaks_fail_reasons_by_match_tier(self, short_profile):
        from services.portfolio_sizer.attrition import summarize_profile_attrition

        cands = [
            # exact PIT 但样本数太少
            {"stock_code": "EXACT_FAIL", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 2, "win_rate": 1.0, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "match_tier": "stage_pit"},
            # cross-stage 通过
            {"stock_code": "FALLBACK_OK", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.85, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "match_tier": "cross_stage_fallback"},
        ]

        summary = summarize_profile_attrition(cands, short_profile, max_examples=2)
        assert summary["fail_reasons_by_match_tier"] == {
            "stage_pit": {"n_signals": 1},
        }
        assert summary["fail_holding_days_by_match_tier"] == {
            "stage_pit": {10: 1},
        }
        assert summary["selected_match_tiers"] == {
            "cross_stage_fallback": 1,
        }
        assert [row["stock_code"] for row in summary["selected_examples"]] == ["FALLBACK_OK"]

    def test_summarize_profile_attrition_records_off_anchor_hp_fail_days(self, short_profile):
        from services.portfolio_sizer.attrition import summarize_profile_attrition

        cands = [
            # exact PIT 候选，但 holding_days 落在 short profile 的 off-anchor 区间
            {"stock_code": "EXACT_OFF_ANCHOR", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 60, "n_signals": 20, "win_rate": 0.85, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "match_tier": "stage_pit"},
            {"stock_code": "FALLBACK_OK", "formula_id": "macd", "formula_variant": "macd_v1",
             "holding_days": 10, "n_signals": 20, "win_rate": 0.85, "avg_ret": 0.10,
             "avg_dd": -0.05, "calmar": 2.0, "signal_close": 100.0,
             "match_tier": "cross_stage_fallback"},
        ]

        summary = summarize_profile_attrition(cands, short_profile, max_examples=2)
        assert summary["fail_reasons_by_match_tier"] == {
            "stage_pit": {"hp": 1},
        }
        assert summary["fail_holding_days_by_match_tier"] == {
            "stage_pit": {60: 1},
        }
        assert summary["selected_match_tiers"] == {
            "cross_stage_fallback": 1,
        }


# ===================== Sell Rules =====================
class TestSellRules:
    def test_stop_loss_priority(self):
        from services.portfolio_sizer.sell_rules import PositionState, evaluate_sell
        p = PositionState(
            stock_code="A", buy_date="2026-05-01", buy_price=100.0,
            holding_days_elapsed=5, holding_days_target=20,
            sell_target_price=110.0, stop_price=95.0, trailing_pct=0.02,
        )
        # 今日低点 = 94 < 95 (stop) → 触发止损
        r = evaluate_sell(p, today_high=98, today_low=94, today_close=96)
        assert r["action"] == "sell"
        assert r["reason"] == "stop_hit"

    def test_trailing_arm_on_target_hit(self):
        from services.portfolio_sizer.sell_rules import PositionState, evaluate_sell
        p = PositionState(
            stock_code="A", buy_date="2026-05-01", buy_price=100.0,
            holding_days_elapsed=5, holding_days_target=20,
            sell_target_price=110.0, stop_price=95.0, trailing_pct=0.02,
        )
        # 今日 high = 112 ≥ 110 → arm trailing
        r = evaluate_sell(p, today_high=112, today_low=109, today_close=111)
        assert p.trailing_armed is True
        # close = 111, high = 112, 回撤 (111-112)/112 = -0.89%, 未超 2%
        assert r["action"] == "hold"

    def test_trailing_stop_triggered(self):
        from services.portfolio_sizer.sell_rules import PositionState, evaluate_sell
        p = PositionState(
            stock_code="A", buy_date="2026-05-01", buy_price=100.0,
            holding_days_elapsed=5, holding_days_target=20,
            sell_target_price=110.0, stop_price=95.0, trailing_pct=0.02,
            high_since_buy=120.0, trailing_armed=True,
        )
        # high_since_buy=120, today_close=117 → 回撤 -2.5% > 2% → trailing 触发
        r = evaluate_sell(p, today_high=118, today_low=116, today_close=117)
        assert r["action"] == "sell"
        assert r["reason"] == "trailing_stop"

    def test_hp_expired(self):
        from services.portfolio_sizer.sell_rules import PositionState, evaluate_sell
        p = PositionState(
            stock_code="A", buy_date="2026-05-01", buy_price=100.0,
            holding_days_elapsed=20, holding_days_target=20,
            sell_target_price=110.0, stop_price=95.0, trailing_pct=0.02,
        )
        # 持仓到期, 无止损/止盈 → 到期清仓
        r = evaluate_sell(p, today_high=105, today_low=103, today_close=104)
        assert r["action"] == "sell"
        assert r["reason"] == "hp_expired"

    def test_hold_no_trigger(self):
        from services.portfolio_sizer.sell_rules import PositionState, evaluate_sell
        p = PositionState(
            stock_code="A", buy_date="2026-05-01", buy_price=100.0,
            holding_days_elapsed=5, holding_days_target=20,
            sell_target_price=110.0, stop_price=95.0, trailing_pct=0.02,
        )
        # 一切正常
        r = evaluate_sell(p, today_high=105, today_low=103, today_close=104)
        assert r["action"] == "hold"


class TestAddPosition:
    def test_can_add_when_pullback(self):
        from services.portfolio_sizer.sell_rules import can_add_position
        r = can_add_position(
            first_buy_price=100.0, first_buy_date_idx=0, today_idx=5,
            today_price=95.0,  # 回踩
            current_position_pct=0.05, profile_stock_cap=0.20,
        )
        assert r["can_add"] is True
        # 加仓 5% × 0.5 = 2.5%, 总 7.5%
        assert abs(r["add_position_pct"] - 0.025) < 1e-6

    def test_cannot_add_too_soon(self):
        from services.portfolio_sizer.sell_rules import can_add_position
        r = can_add_position(
            first_buy_price=100.0, first_buy_date_idx=0, today_idx=2,  # 距首次 < 3d
            today_price=95.0,
            current_position_pct=0.05, profile_stock_cap=0.20,
        )
        assert r["can_add"] is False
        assert r["reason"] == "too_soon"

    def test_cannot_add_no_pullback(self):
        from services.portfolio_sizer.sell_rules import can_add_position
        r = can_add_position(
            first_buy_price=100.0, first_buy_date_idx=0, today_idx=5,
            today_price=105.0,  # 涨了不加
            current_position_pct=0.05, profile_stock_cap=0.20,
        )
        assert r["can_add"] is False
        assert r["reason"] == "no_pullback"

    def test_cannot_add_at_cap(self):
        from services.portfolio_sizer.sell_rules import can_add_position
        r = can_add_position(
            first_buy_price=100.0, first_buy_date_idx=0, today_idx=5,
            today_price=95.0,
            current_position_pct=0.20, profile_stock_cap=0.20,  # 已到 cap
        )
        assert r["can_add"] is False
        assert r["reason"] == "cap_reached"


# ===================== Profiles =====================
class TestProfiles:
    def test_profile_config_loader(self):
        from services.portfolio_sizer.config import load_portfolio_sizer_profile_specs

        specs = load_portfolio_sizer_profile_specs()
        assert list(specs.keys()) == ["short", "mid", "long"]
        assert specs["short"].holding_days == (5, 10, 15)
        assert specs["mid"].min_n_signals == 8
        assert specs["long"].min_wilson_win == 0.70

    def test_three_profiles(self):
        from services.portfolio_sizer.profiles import PROFILES, list_profiles
        assert set(PROFILES.keys()) == {"short", "mid", "long"}
        out = list_profiles()
        assert len(out) == 3
        ids = [p["profile_id"] for p in out]
        assert ids == ["short", "mid", "long"]

    def test_short_profile_specifics(self):
        from services.portfolio_sizer.profiles import get_profile
        p = get_profile("short")
        assert p.max_positions == 5
        assert p.stock_cap_pct == 0.20
        assert p.kelly_fraction == 0.5
        assert p.holding_days == (5, 10, 15)
        assert p.min_wilson_win == 0.60
        assert p.min_n_signals == 5

    def test_long_profile_strictest(self):
        from services.portfolio_sizer.profiles import get_profile
        p = get_profile("long")
        assert p.max_positions == 15
        assert p.kelly_fraction == 0.25
        assert p.min_wilson_win == 0.70
        # Phase η+++++ 砍 exclude_fund_stages (fundamental_stage 历史不可重建)
        # 用 evidence 框架追溯
        assert p.exclude_fund_stages == ()
        assert "exclude_fund_stages" in p.evidence
        assert p.evidence["exclude_fund_stages"].kind == "unverified"

    def test_unknown_profile_raises(self):
        from services.portfolio_sizer.profiles import get_profile
        with pytest.raises(ValueError):
            get_profile("crazy")
