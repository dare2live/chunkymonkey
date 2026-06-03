"""Tests for services/formula_engine/ — FormulaBase 接口 + 各公式实现。"""
from __future__ import annotations

import numpy as np
import pytest

from services.formula_engine.base import (
    FormulaBase,
    FormulaMetadata,
    FormulaSignal,
    REGISTRY,
    cross_down,
    cross_up,
    ema,
    sma,
)


# ============================================================
# base.py 辅助函数
# ============================================================


class TestEmaSma:
    def test_ema_first_value_equals_input(self):
        v = np.array([100.0, 101, 102, 103, 104])
        result = ema(v, 5)
        assert result[0] == pytest.approx(100.0)
        # alpha = 2/(5+1) = 1/3, ema[1] = 1/3 * 101 + 2/3 * 100 = 100.333
        assert result[1] == pytest.approx(100.333, abs=0.01)

    def test_ema_handles_empty(self):
        result = ema(np.array([]), 5)
        assert len(result) == 0

    def test_sma_basic(self):
        v = np.array([1.0, 2, 3, 4, 5])
        result = sma(v, 3)
        # 前 2 个 nan,第 3 个 = (1+2+3)/3 = 2
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_sma_short_array(self):
        v = np.array([1.0, 2])
        result = sma(v, 5)
        # 长度 < period, 全 nan
        assert np.all(np.isnan(result))


class TestCrossDetection:
    def test_cross_up_clear(self):
        a = np.array([1, 2, 3, 4, 5])
        b = np.array([3, 3, 3, 3, 3])
        # a[1]=2 < b[1]=3, a[2]=3 == b[2]=3 (不严格上穿), a[3]=4 > b[3]=3 (确实穿了 from 3 to 4)
        # 严格定义: a[i-1]<b[i-1] AND a[i]>b[i]
        result = cross_up(a, b)
        # i=3: a[2]=3 < b[2]=3 是 False; not crossing
        # 检查 i=2 and 3
        # i=2: a[1]=2<3 True; a[2]=3>3 False -> not cross
        # i=3: a[2]=3<3 False -> not cross
        # i=4: a[3]=4>3 True (上方); a[4]=5>3 still above -> not cross
        # 这个例子其实没有严格 cross. 用另一个例子
        a = np.array([1, 2, 4, 5])
        b = np.array([3, 3, 3, 3])
        # i=2: a[1]=2<3 True, a[2]=4>3 True -> cross!
        result = cross_up(a, b)
        assert result[0] == False
        assert result[1] == False
        assert result[2] == True  # 上穿发生
        assert result[3] == False

    def test_cross_down_clear(self):
        a = np.array([5, 4, 2, 1])
        b = np.array([3, 3, 3, 3])
        # i=2: a[1]=4>3 True, a[2]=2<3 True -> cross down
        result = cross_down(a, b)
        assert result[2] == True

    def test_cross_short_array(self):
        # 长度 < 2 不能 cross
        result = cross_up(np.array([1.0]), np.array([2.0]))
        assert len(result) == 1
        assert result[0] == False


class TestSharedFormulaWindows:
    def test_loader_reads_values(self, tmp_path):
        from services.formula_engine.shared_windows import load_holding_days

        cfg = tmp_path / "formula_shared_windows.yaml"
        cfg.write_text("holding_days: [3, 7, 14]\n", encoding="utf-8")
        assert load_holding_days(cfg) == (3, 7, 14)

    def test_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.shared_windows import load_holding_days

        cfg = tmp_path / "formula_shared_windows.yaml"
        cfg.write_text("other_key: [3, 7, 14]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing holding_days"):
            load_holding_days(cfg)


# ============================================================
# MACD 公式
# ============================================================


class TestMacdGoldenCross:
    @pytest.fixture
    def formula(self):
        # import 触发 register
        from services.formula_engine import macd_golden_cross  # noqa: F401
        return REGISTRY["macd_golden_cross"]

    def test_metadata(self, formula):
        m = formula.metadata
        assert m.formula_id == "macd_golden_cross"
        assert m.tag == "MA"
        assert m.default_horizon_days == 10
        assert m.has_state is True
        assert formula.fast_period == 12
        assert formula.slow_period == 26
        assert formula.signal_period == 9

    def test_config_loader_reads_values(self, tmp_path):
        from services.formula_engine.macd_golden_cross import _load_config

        cfg = tmp_path / "formula_macd_golden_cross.yaml"
        cfg.write_text(
            "fast_period: 8\n"
            "slow_period: 17\n"
            "signal_period: 5\n"
            "cross_window: 4\n"
            "imminent_days: 7\n"
            "imminent_gap_ratio: 0.01\n",
            encoding="utf-8",
        )
        loaded = _load_config(cfg)
        assert loaded["fast_period"] == 8
        assert loaded["slow_period"] == 17
        assert loaded["signal_period"] == 5
        assert loaded["cross_window"] == 4
        assert loaded["imminent_days"] == 7
        assert loaded["imminent_gap_ratio"] == pytest.approx(0.01)

    def test_config_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.macd_golden_cross import _load_config

        cfg = tmp_path / "formula_macd_golden_cross.yaml"
        cfg.write_text(
            "fast_period: 8\n"
            "slow_period: 17\n"
            "signal_period: 5\n"
            "cross_window: 4\n"
            "imminent_days: 7\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing macd rule key imminent_gap_ratio"):
            _load_config(cfg)

    def test_short_kline_no_signals(self, formula):
        # K 线太短 (< slow + signal = 26+9 = 35),不应产生信号
        n = 20
        dates = np.array([f"2024-01-{i+1:02d}" for i in range(n)])
        closes = np.array([100.0 + i for i in range(n)])
        signals = formula.compute_signals(
            "TEST", dates,
            opens=closes, highs=closes, lows=closes, closes=closes,
            volumes=np.ones(n) * 1000, amounts=np.ones(n) * 100000,
        )
        assert signals == []

    def test_oscillating_uptrend_produces_above_zero_cross(self, formula):
        # 震荡上涨 K 线 (类似真实牛市), 必然产生 DIF>=0 金叉
        np.random.seed(42)
        n = 200
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        trend = np.linspace(100, 150, n)
        noise = 5 * np.sin(np.arange(n) / 8) + np.random.randn(n) * 2
        closes = trend + noise

        signals = formula.compute_signals(
            "TEST", dates,
            opens=closes, highs=closes * 1.01, lows=closes * 0.99, closes=closes,
            volumes=np.ones(n) * 1000, amounts=closes * 1000,
        )
        # 震荡上涨应该有多个金叉信号
        assert len(signals) >= 3, f"震荡上涨应产生 >=3 金叉,实际 {len(signals)}"
        first_signal = signals[0]
        assert first_signal.formula_id == "macd_golden_cross"
        assert first_signal.state == "just_crossed"
        assert 0.0 < first_signal.strength <= 1.0
        assert len(first_signal.reason_codes) >= 2
        assert any("dif_above_zero" in rc for rc in first_signal.reason_codes)

    def test_linear_recovery_below_zero_cross_produces_below_variant(self, formula):
        # 平滑反转: 50 跌 + 50 涨, 金叉时若 DIF<0 → variant=below_zero (区分新设计)
        n = 100
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.linspace(100, 80, 50),
            np.linspace(80, 130, 50),
        ])
        signals = formula.compute_signals(
            "TEST", dates,
            opens=closes, highs=closes * 1.01, lows=closes * 0.99, closes=closes,
            volumes=np.ones(n) * 1000, amounts=closes * 1000,
        )
        # 新行为: 金叉都产生信号, variant 区分 above/below_zero
        for s in signals:
            if s.formula_variant == "macd_golden_cross_above_zero":
                assert any("dif_above_zero" in rc for rc in s.reason_codes)
            elif s.formula_variant == "macd_golden_cross_below_zero":
                assert any("dif_below_zero" in rc for rc in s.reason_codes)
            else:
                raise AssertionError(f"未知 variant: {s.formula_variant}")

    def test_flat_kline_no_signals(self, formula):
        # 全平 K 线 (close 不变),DIF 始终为 0,不应有金叉
        n = 80
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.full(n, 100.0)
        signals = formula.compute_signals(
            "TEST", dates,
            opens=closes, highs=closes, lows=closes, closes=closes,
            volumes=np.ones(n) * 1000, amounts=np.ones(n) * 100000,
        )
        assert signals == []

    def test_downtrend_no_above_zero_cross(self, formula):
        # 持续下跌, DIF 始终在 0 以下, 即使有金叉也不应记录 (DIFF<0 过滤)
        n = 80
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.linspace(100, 50, n)
        signals = formula.compute_signals(
            "TEST", dates,
            opens=closes, highs=closes * 1.01, lows=closes * 0.99, closes=closes,
            volumes=np.ones(n) * 1000, amounts=closes * 1000,
        )
        # 持续下跌可能有反弹金叉但 DIF < 0,应被 filter
        for s in signals:
            # 既然产生了,必须 reason_codes 含 dif_above_zero
            assert any("dif_above_zero" in rc for rc in s.reason_codes)

    def test_state_history_emits_holding_and_imminent_without_trigger_rows(self, formula):
        n = 180
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.linspace(120, 78, 90),
            np.linspace(78, 138, 90),
        ])
        state_rows = formula.compute_state_history(
            "TEST", dates,
            opens=closes, highs=closes * 1.01, lows=closes * 0.99, closes=closes,
            volumes=np.ones(n) * 1000, amounts=closes * 1000,
        )
        assert state_rows, "MACD state history should emit active states on a recovery path"
        assert all(row.state in {"holding", "imminent"} for row in state_rows)
        assert all(row.state != "just_crossed" for row in state_rows)
        assert any(row.state == "holding" for row in state_rows)


class TestFormulaRegistry:
    def test_registry_contains_macd(self):
        from services.formula_engine import macd_golden_cross  # noqa: F401
        assert "macd_golden_cross" in REGISTRY

    def test_registry_no_duplicate(self):
        # 重复注册同 ID 应抛错
        from services.formula_engine.base import register_formula
        from services.formula_engine.macd_golden_cross import MacdGoldenCross
        with pytest.raises(ValueError, match="already registered"):
            register_formula(MacdGoldenCross())


class TestTurtleBreakout:
    @pytest.fixture
    def f20(self):
        from services.formula_engine import turtle_breakout  # noqa: F401
        return REGISTRY["turtle_breakout_20"]

    @pytest.fixture
    def f55(self):
        from services.formula_engine import turtle_breakout  # noqa: F401
        return REGISTRY["turtle_breakout_55"]

    def test_metadata_20(self, f20):
        assert f20.metadata.formula_id == "turtle_breakout_20"
        assert f20.metadata.tag == "T2"
        assert f20.metadata.has_variant is True

    def test_metadata_55(self, f55):
        assert f55.metadata.formula_id == "turtle_breakout_55"
        assert f55.metadata.default_horizon_days == 30
        from services.formula_engine import turtle_breakout as turtle_breakout_module
        assert turtle_breakout_module.VOLUME_MULTIPLE_20 == pytest.approx(0.9)
        assert turtle_breakout_module.VOLUME_MULTIPLE_55 == pytest.approx(0.4)

    def test_volume_multiple_loader_reads_config(self, tmp_path):
        from services.formula_engine.turtle_breakout import _load_volume_multiple

        cfg = tmp_path / "formula_turtle_breakout.yaml"
        cfg.write_text("turtle_breakout_55:\n  volume_multiple: 1.05\n", encoding="utf-8")
        assert _load_volume_multiple(cfg, variant="turtle_breakout_55") == pytest.approx(1.05)

    def test_volume_multiple_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.turtle_breakout import _load_volume_multiple

        cfg = tmp_path / "formula_turtle_breakout.yaml"
        cfg.write_text("turtle_breakout_55:\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing turtle_breakout key volume_multiple"):
            _load_volume_multiple(cfg, variant="turtle_breakout_55")

    def test_volume_multiple_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.turtle_breakout import _load_volume_multiple

        cfg = tmp_path / "formula_turtle_breakout.yaml"
        cfg.write_text("turtle_breakout_55:\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing turtle_breakout key volume_multiple"):
            _load_volume_multiple(cfg, variant="turtle_breakout_55")

    def test_short_kline_no_signal(self, f20):
        n = 10
        dates = np.array([f"2024-01-{i+1:02d}" for i in range(n)])
        closes = np.linspace(100, 110, n)
        signals = f20.compute_signals(
            "T", dates, closes, closes * 1.01, closes * 0.99, closes,
            np.ones(n) * 1000, closes * 1000,
        )
        assert signals == []

    def test_breakout_with_volume(self, f20):
        # 30 天平稳 + 突然突破 (close 跳高) + 量能放大
        np.random.seed(7)
        n = 60
        dates = np.array([f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)])
        # 前 30 天: close 在 100 ± 1
        # 后 30 天: close 涨到 110+, volume 跳到 3000
        closes = np.concatenate([
            100 + np.random.randn(30) * 0.5,
            np.linspace(100, 115, 30),
        ])
        volumes = np.concatenate([
            np.ones(30) * 1000,
            np.ones(30) * 3000,
        ])
        signals = f20.compute_signals(
            "T", dates, closes, closes * 1.005, closes * 0.995, closes,
            volumes, closes * volumes,
        )
        # 应产生突破信号 (close 突破 prev 20d max + 量放大)
        assert len(signals) >= 1
        s = signals[0]
        assert s.formula_id == "turtle_breakout_20"
        assert s.state is None
        assert 0.0 < s.strength <= 1.0
        assert any("close_above_20d_high" in rc for rc in s.reason_codes)

    def test_no_volume_no_signal(self, f20):
        # 价格突破但量能不放大,应不触发
        n = 40
        dates = np.array([f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.ones(20) * 100.0,
            np.array([101.0]),
            np.ones(19) * 101.0,
        ])
        # 突破日量能低于门槛, 且后续不再创新高
        volumes = np.concatenate([
            np.ones(20) * 1000,
            np.ones(1) * 800,
            np.ones(19) * 800,
        ])
        signals = f20.compute_signals(
            "T", dates, closes, closes * 1.005, closes * 0.995, closes,
            volumes, closes * volumes,
        )
        # 量不放大,应该 0 信号
        assert len(signals) == 0

    def test_moderate_volume_breakout_now_triggers(self, f55):
        # 介于 0.4x 和 0.5x 的放量突破,用来锁住 55 日博弈不会回弹到 0.5
        n = 60
        dates = np.array([f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.ones(55) * 100.0,
            np.array([111.0, 111.0, 111.0, 111.0, 111.0]),
        ])
        volumes = np.concatenate([
            np.ones(55) * 1000,
            np.array([450.0, 1000.0, 1000.0, 1000.0, 1000.0]),
        ])
        signals = f55.compute_signals(
            "T", dates, closes, closes * 1.005, closes * 0.995, closes,
            volumes, closes * volumes,
        )
        assert len(signals) >= 1
        assert signals[0].formula_id == "turtle_breakout_55"


class TestDynamicMaIterativeCross:
    @pytest.fixture
    def formula(self):
        from services.formula_engine import dynamic_ma_iterative  # noqa: F401
        return REGISTRY["dynamic_ma_iterative_cross"]

    def test_metadata(self, formula):
        assert formula.metadata.formula_id == "dynamic_ma_iterative_cross"
        assert formula.metadata.tag == "DM"
        assert formula.iterations == 0

    def test_config_loader_reads_values(self, tmp_path):
        from services.formula_engine.dynamic_ma_iterative import _load_config

        cfg = tmp_path / "formula_dynamic_ma_iterative.yaml"
        cfg.write_text(
            "iterations: 3\nmultiplier_up: 1.05\nmultiplier_down: 0.95\n",
            encoding="utf-8",
        )
        loaded = _load_config(cfg)
        assert loaded["iterations"] == pytest.approx(3.0)
        assert loaded["multiplier_up"] == pytest.approx(1.05)
        assert loaded["multiplier_down"] == pytest.approx(0.95)

    def test_config_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.dynamic_ma_iterative import _load_config

        cfg = tmp_path / "formula_dynamic_ma_iterative.yaml"
        cfg.write_text("iterations: 3\nmultiplier_up: 1.05\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing dynamic_ma_iterative key multiplier_down"):
            _load_config(cfg)

    def test_short_kline_no_signal(self, formula):
        n = 30  # < 50 warmup
        dates = np.array([f"2024-01-{i+1:02d}" for i in range(n)])
        closes = np.linspace(100, 110, n)
        signals = formula.compute_signals(
            "T", dates, closes, closes, closes, closes,
            np.ones(n) * 1000, closes * 1000,
        )
        assert signals == []

    def test_oscillating_uptrend_produces_signal(self, formula):
        np.random.seed(7)
        n = 200
        dates = np.array([f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)])
        trend = np.linspace(100, 140, n)
        noise = 4 * np.sin(np.arange(n) / 7) + np.random.randn(n) * 1.5
        closes = trend + noise
        opens = closes + np.random.randn(n) * 0.5
        highs = np.maximum(opens, closes) * (1 + np.abs(np.random.randn(n)) * 0.005)
        lows = np.minimum(opens, closes) * (1 - np.abs(np.random.randn(n)) * 0.005)
        volumes = np.ones(n) * 1000 + np.random.randn(n).clip(0) * 200

        signals = formula.compute_signals(
            "T", dates, opens, highs, lows, closes, volumes, closes * volumes,
        )
        # 震荡上涨 200 天必然有至少 1 个 X_36 上穿 X_3 信号
        assert len(signals) >= 1, f"应有信号,实际 {len(signals)}"
        s = signals[0]
        assert s.formula_id == "dynamic_ma_iterative_cross"
        assert 0.0 < s.strength <= 1.0
        assert any("x36_cross_x3" in rc for rc in s.reason_codes)


class TestShortTermReversalConfig:
    def test_config_loader_reads_values(self, tmp_path):
        from services.formula_engine.reversal_short_term import _load_config

        cfg = tmp_path / "formula_reversal_short_term.yaml"
        cfg.write_text(
            "reversal_1m_mild:\n"
            "  lookback_days: 20\n"
            "  pct_change_lo: -0.15\n"
            "  pct_change_hi: -0.01\n"
            "  rel_std_max: 0.08\n"
            "  vol_ratio_lo: 0.6\n"
            "  vol_ratio_hi: 2.0\n"
            "reversal_1m_deep:\n"
            "  lookback_days: 20\n"
            "  pct_change_lo: -0.30\n"
            "  pct_change_hi: -0.04\n"
            "  rel_std_max: 0.10\n"
            "  vol_ratio_lo: 0.6\n"
            "  vol_ratio_hi: 2.0\n"
            "reversal_1w:\n"
            "  lookback_days: 5\n"
            "  pct_change_lo: -0.10\n"
            "  pct_change_hi: -0.01\n"
            "  rel_std_max: 0.07\n"
            "  vol_ratio_lo: 0.6\n"
            "  vol_ratio_hi: 2.0\n",
            encoding="utf-8",
        )
        loaded = _load_config(cfg)
        assert loaded["reversal_1m_mild"]["pct_change_hi"] == pytest.approx(-0.01)
        assert loaded["reversal_1m_mild"]["rel_std_max"] == pytest.approx(0.08)
        assert loaded["reversal_1m_deep"]["pct_change_hi"] == pytest.approx(-0.04)
        assert loaded["reversal_1m_deep"]["rel_std_max"] == pytest.approx(0.10)
        assert loaded["reversal_1w"]["pct_change_hi"] == pytest.approx(-0.01)
        assert loaded["reversal_1w"]["rel_std_max"] == pytest.approx(0.07)
        assert loaded["reversal_1w"]["lookback_days"] == 5

    def test_config_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.reversal_short_term import _load_config

        cfg = tmp_path / "formula_reversal_short_term.yaml"
        cfg.write_text(
            "reversal_1m_mild:\n"
            "  lookback_days: 20\n"
            "  pct_change_lo: -0.15\n"
            "  pct_change_hi: -0.01\n"
            "  rel_std_max: 0.08\n"
            "  vol_ratio_lo: 0.6\n"
            "reversal_1m_deep:\n"
            "  lookback_days: 20\n"
            "  pct_change_lo: -0.30\n"
            "  pct_change_hi: -0.04\n"
            "  rel_std_max: 0.10\n"
            "  vol_ratio_lo: 0.6\n"
            "  vol_ratio_hi: 2.0\n"
            "reversal_1w:\n"
            "  lookback_days: 5\n"
            "  pct_change_lo: -0.10\n"
            "  pct_change_hi: -0.01\n"
            "  rel_std_max: 0.07\n"
            "  vol_ratio_lo: 0.6\n"
            "  vol_ratio_hi: 2.0\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing reversal key vol_ratio_hi"):
            _load_config(cfg)


class TestShortTermReversal:
    @pytest.fixture
    def mild(self):
        from services.formula_engine import reversal_short_term  # noqa: F401
        return REGISTRY["reversal_1m_mild"]

    @pytest.fixture
    def deep(self):
        from services.formula_engine import reversal_short_term  # noqa: F401
        return REGISTRY["reversal_1m_deep"]

    @pytest.fixture
    def w1(self):
        from services.formula_engine import reversal_short_term  # noqa: F401
        return REGISTRY["reversal_1w"]

    def test_metadata_uses_config_thresholds(self, deep):
        assert deep.lookback_days == 20
        assert deep.pct_change_lo == pytest.approx(-0.30)
        assert deep.pct_change_hi == pytest.approx(-0.04)
        assert deep.rel_std_max == pytest.approx(0.10)

    def test_mild_variant_triggers_on_roughly_1pct_drop(self, mild):
        n = 90
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.full(60, 100.0),
            np.linspace(100.0, 99.0, 20),
            np.full(10, 99.0),
        ])
        volumes = np.ones(n) * 1000

        signals = mild.compute_signals(
            "T",
            dates,
            closes,
            closes,
            closes,
            closes,
            volumes,
            closes * volumes,
        )

        assert len(signals) >= 1, "1% 左右温和下跌应落入 reversal_1m_mild"
        assert all(s.formula_id == "reversal_1m_mild" for s in signals)

    def test_deep_variant_triggers_on_roughly_4pct_drop(self, deep):
        n = 90
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.full(60, 100.0),
            np.linspace(100.0, 96.0, 20),
            np.full(10, 96.0),
        ])
        volumes = np.ones(n) * 1000

        signals = deep.compute_signals(
            "T",
            dates,
            closes,
            closes,
            closes,
            closes,
            volumes,
            closes * volumes,
        )

        assert len(signals) >= 1, "4% 左右深跌应落入 reversal_1m_deep"
        assert all(s.formula_id == "reversal_1m_deep" for s in signals)

    def test_deep_variant_triggers_on_roughly_11pct_drop(self, deep):
        n = 90
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.full(60, 100.0),
            np.linspace(100.0, 89.0, 20),
            np.full(10, 89.0),
        ])
        volumes = np.ones(n) * 1000

        signals = deep.compute_signals(
            "T",
            dates,
            closes,
            closes,
            closes,
            closes,
            volumes,
            closes * volumes,
        )

        assert len(signals) >= 1, "11% 左右深跌应落入 reversal_1m_deep"
        assert all(s.formula_id == "reversal_1m_deep" for s in signals)

    def test_deep_variant_triggers_on_roughly_9pct_drop(self, deep):
        n = 90
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.full(60, 100.0),
            np.linspace(100.0, 91.0, 20),
            np.full(10, 91.0),
        ])
        volumes = np.ones(n) * 1000

        signals = deep.compute_signals(
            "T",
            dates,
            closes,
            closes,
            closes,
            closes,
            volumes,
            closes * volumes,
        )

        assert len(signals) >= 1, "9% 左右深跌也应落入 reversal_1m_deep"
        assert all(s.formula_id == "reversal_1m_deep" for s in signals)

    def test_weekly_variant_triggers_on_roughly_2pct_drop(self, w1):
        n = 61
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.full(56, 100.0),
            np.linspace(100.0, 98.0, 5),
        ])
        volumes = np.ones(n) * 1000

        signals = w1.compute_signals(
            "T",
            dates,
            closes,
            closes,
            closes,
            closes,
            volumes,
            closes * volumes,
        )

        assert len(signals) >= 1, "2% 左右回撤应落入 reversal_1w"
        assert all(s.formula_id == "reversal_1w" for s in signals)

    def test_weekly_variant_triggers_on_roughly_1pct_drop(self, w1):
        n = 61
        dates = np.array([f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}" for i in range(n)])
        closes = np.concatenate([
            np.full(56, 100.0),
            np.linspace(100.0, 99.0, 5),
        ])
        volumes = np.ones(n) * 1000

        signals = w1.compute_signals(
            "T",
            dates,
            closes,
            closes,
            closes,
            closes,
            volumes,
            closes * volumes,
        )

        assert len(signals) >= 1, "1% 左右回撤应落入 reversal_1w"
        assert all(s.formula_id == "reversal_1w" for s in signals)


class TestTechnicalStage:
    """Stan Weinstein 4-stage 规则版 v1 测试。"""

    @pytest.fixture
    def classify(self):
        from services.formula_engine.technical_stage import classify_technical_stage
        return classify_technical_stage

    def test_config_loader_reads_values(self, tmp_path):
        from services.formula_engine.technical_stage import _load_rules

        cfg = tmp_path / "technical_stage.yaml"
        cfg.write_text(
            "ma_fast_days: 42\n"
            "ma_mid_days: 126\n"
            "ma_slow_days: 252\n"
            "range_lookback: 280\n"
            "breakout_recent_days: 8\n"
            "drawdown_max_stage2: 0.12\n"
            "drawdown_lookback_days: 55\n"
            "stage1_pos_max: 0.25\n"
            "stage1_slope_max_abs: 0.01\n"
            "stage1_vol_ratio_max: 0.7\n"
            "stage15_vol_ratio_min: 1.4\n"
            "stage15_recent_below_min_count: 3\n"
            "volume_ma_days: 18\n"
            "slope_lookback_days: 15\n"
            "stage3_price_above_ma_mid_multiple: 1.12\n"
            "stage3_slope_min: 0.02\n"
            "stage4_slope_max: -0.03\n"
            "residual_above_mid_stage: \"3\"\n"
            "residual_below_mid_down_stage: \"4\"\n"
            "residual_below_mid_other_stage: \"1\"\n",
            encoding="utf-8",
        )
        loaded = _load_rules(cfg)
        assert loaded["ma_fast_days"] == 42
        assert loaded["ma_mid_days"] == 126
        assert loaded["ma_slow_days"] == 252
        assert loaded["range_lookback"] == 280
        assert loaded["breakout_recent_days"] == 8
        assert loaded["drawdown_max_stage2"] == pytest.approx(0.12)
        assert loaded["drawdown_lookback_days"] == 55
        assert loaded["stage1_pos_max"] == pytest.approx(0.25)
        assert loaded["stage1_slope_max_abs"] == pytest.approx(0.01)
        assert loaded["stage1_vol_ratio_max"] == pytest.approx(0.7)
        assert loaded["stage15_vol_ratio_min"] == pytest.approx(1.4)
        assert loaded["stage15_recent_below_min_count"] == 3
        assert loaded["volume_ma_days"] == 18
        assert loaded["slope_lookback_days"] == 15
        assert loaded["stage3_price_above_ma_mid_multiple"] == pytest.approx(1.12)
        assert loaded["stage3_slope_min"] == pytest.approx(0.02)
        assert loaded["stage4_slope_max"] == pytest.approx(-0.03)
        assert loaded["residual_above_mid_stage"] == "3"
        assert loaded["residual_below_mid_down_stage"] == "4"
        assert loaded["residual_below_mid_other_stage"] == "1"

    def test_config_loader_missing_key_fails(self, tmp_path):
        from services.formula_engine.technical_stage import _load_rules

        cfg = tmp_path / "technical_stage.yaml"
        cfg.write_text(
            "ma_fast_days: 42\n"
            "ma_mid_days: 126\n"
            "ma_slow_days: 252\n"
            "range_lookback: 280\n"
            "breakout_recent_days: 8\n"
            "drawdown_max_stage2: 0.12\n"
            "drawdown_lookback_days: 55\n"
            "stage1_pos_max: 0.25\n"
            "stage1_slope_max_abs: 0.01\n"
            "stage1_vol_ratio_max: 0.7\n"
            "stage15_vol_ratio_min: 1.4\n"
            "stage15_recent_below_min_count: 3\n"
            "volume_ma_days: 18\n"
            "slope_lookback_days: 15\n"
            "stage3_price_above_ma_mid_multiple: 1.12\n"
            "stage3_slope_min: 0.02\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing technical_stage key stage4_slope_max"):
            _load_rules(cfg)

    def test_config_loader_invalid_residual_stage_fails(self, tmp_path):
        from services.formula_engine.technical_stage import _load_rules

        cfg = tmp_path / "technical_stage.yaml"
        cfg.write_text(
            "ma_fast_days: 42\n"
            "ma_mid_days: 126\n"
            "ma_slow_days: 252\n"
            "range_lookback: 280\n"
            "breakout_recent_days: 8\n"
            "drawdown_max_stage2: 0.12\n"
            "drawdown_lookback_days: 55\n"
            "stage1_pos_max: 0.25\n"
            "stage1_slope_max_abs: 0.01\n"
            "stage1_vol_ratio_max: 0.7\n"
            "stage15_vol_ratio_min: 1.4\n"
            "stage15_recent_below_min_count: 3\n"
            "volume_ma_days: 18\n"
            "slope_lookback_days: 15\n"
            "stage3_price_above_ma_mid_multiple: 1.12\n"
            "stage3_slope_min: 0.02\n"
            "stage4_slope_max: -0.03\n"
            "residual_above_mid_stage: \"2\"\n"
            "residual_below_mid_down_stage: \"4\"\n"
            "residual_below_mid_other_stage: \"1\"\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="residual_above_mid_stage"):
            _load_rules(cfg)

    def test_short_kline_returns_unknown(self, classify):
        # < MA_SLOW_DAYS = 250 全部 unknown
        n = 100
        closes = np.linspace(10, 11, n)
        volumes = np.ones(n) * 1000
        out = classify(closes, volumes)
        assert len(out) == n
        assert all(s == "unknown" for s in out)

    def test_strong_uptrend_produces_stage2(self, classify):
        # 持续 1 年震荡上涨, 后期应该 stage 2 (MA10>MA30>MA50, 价>MA30)
        np.random.seed(7)
        n = 350
        trend = np.linspace(10, 15, n)
        noise = 0.3 * np.sin(np.arange(n) / 10) + np.random.randn(n) * 0.1
        closes = trend + noise
        volumes = np.ones(n) * 1000

        out = classify(closes, volumes)
        # 后期 100 天应该多数是 stage 2
        late = out[-100:]
        stage2_count = sum(1 for s in late if s == "2")
        assert stage2_count >= 30, f"后期至少 30 天应判为 stage 2, 实际 {stage2_count}"

    def test_downtrend_produces_stage4(self, classify):
        # 持续下跌
        n = 350
        closes = np.linspace(20, 10, n)
        volumes = np.ones(n) * 1000
        out = classify(closes, volumes)
        # 后期应该多数 stage 4
        late = out[-50:]
        stage4_count = sum(1 for s in late if s == "4")
        assert stage4_count >= 20, f"持续下跌后期至少 20 天 stage 4, 实际 {stage4_count}"

    def test_sufficient_history_residual_is_classified(self, classify):
        from services.formula_engine.technical_stage import MA_SLOW_DAYS

        n = 380
        x = np.arange(n)
        closes = 10.0 + np.sin(x / 9.0) * 0.12
        volumes = np.ones(n) * 1000

        out = classify(closes, volumes)

        assert all(s != "unknown" for s in out[MA_SLOW_DAYS:])
        assert set(out[MA_SLOW_DAYS:]).issubset({"1", "1.5", "2", "3", "4"})

    def test_residual_stage_policy_branches_are_governed(self):
        from services.formula_engine.technical_stage import (
            RESIDUAL_ABOVE_MID_STAGE,
            RESIDUAL_BELOW_MID_DOWN_STAGE,
            RESIDUAL_BELOW_MID_OTHER_STAGE,
            _classify_residual_stage,
        )

        assert RESIDUAL_ABOVE_MID_STAGE == "3"
        assert RESIDUAL_BELOW_MID_DOWN_STAGE == "4"
        assert RESIDUAL_BELOW_MID_OTHER_STAGE == "1"
        assert _classify_residual_stage(10.2, 9.8, 10.0, 0.01) == "3"
        assert _classify_residual_stage(9.8, 10.1, 10.0, -0.03) == "4"
        assert _classify_residual_stage(9.8, 10.1, 10.0, 0.0) == "1"

    def test_output_only_valid_labels(self, classify):
        from services.formula_engine.technical_stage import MA_SLOW_DAYS

        # 任何输入,输出 labels 必须在合法集合内
        np.random.seed(42)
        n = 400
        closes = 10 + np.cumsum(np.random.randn(n) * 0.05)
        volumes = np.abs(np.random.randn(n)) * 1000 + 100
        out = classify(closes, volumes)
        valid = {"1", "1.5", "2", "3", "4", "unknown"}
        bad = [s for s in out if s not in valid]
        assert not bad, f"出现非法 label: {set(bad)}"
        assert all(s != "unknown" for s in out[MA_SLOW_DAYS:])


class TestFormulaSignalSerialization:
    def test_to_db_row(self):
        sig = FormulaSignal(
            stock_code="600519",
            date="2024-01-15",
            formula_id="macd_golden_cross",
            formula_variant="macd_golden_cross",
            strength=0.75,
            state="just_crossed",
            reason_codes=("dif_above_dea:0.5", "dif_above_zero:0.3"),
        )
        row = sig.to_db_row()
        assert row["stock_code"] == "600519"
        assert row["date"] == "2024-01-15"
        assert row["formula_id"] == "macd_golden_cross"
        assert row["strength"] == 0.75
        assert row["state"] == "just_crossed"
        # reason_codes_json 是 JSON 字符串
        import json
        parsed = json.loads(row["reason_codes_json"])
        assert parsed == ["dif_above_dea:0.5", "dif_above_zero:0.3"]
