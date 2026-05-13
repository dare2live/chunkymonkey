"""单测: sell_pricing.py"""
from __future__ import annotations

import pytest


class TestStopLoss:
    def test_trigger_price_mode(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        cfg = SellPricingConfig(stop_loss_mode="trigger_price")
        p = compute_sell_price("stop_loss", stop_price=95.0, config=cfg)
        assert p == pytest.approx(95.0)

    def test_trigger_with_slippage(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        cfg = SellPricingConfig(stop_loss_mode="trigger_with_slippage",
                                stop_loss_slippage_pct=-0.005)
        # 触发价 100, 滑点 -0.5% → 实际 99.5
        p = compute_sell_price("stop_loss", stop_price=100.0, config=cfg)
        assert p == pytest.approx(99.5)

    def test_next_open(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        cfg = SellPricingConfig(stop_loss_mode="next_open")
        p = compute_sell_price("stop_loss", stop_price=95.0, next_open=92.0, config=cfg)
        assert p == pytest.approx(92.0)


class TestTargetHit:
    def test_target_price_mode(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        cfg = SellPricingConfig(target_hit_mode="target_price")
        p = compute_sell_price("target_hit", target_price=110.0, today_high=115.0, config=cfg)
        assert p == pytest.approx(110.0)

    def test_target_high_mode(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        cfg = SellPricingConfig(target_hit_mode="high")
        p = compute_sell_price("target_hit", target_price=110.0, today_high=115.0, config=cfg)
        assert p == pytest.approx(115.0)


class TestHpExpired:
    def test_close_mode(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        cfg = SellPricingConfig(hp_expiry_mode="close")
        p = compute_sell_price("hp_expired", today_close=105.0, config=cfg)
        assert p == pytest.approx(105.0)


class TestOneWordLimit:
    def test_returns_none(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        p = compute_sell_price("one_word_limit", today_close=105.0, config=SellPricingConfig())
        assert p is None


class TestUnknownReason:
    def test_raises(self):
        from services.trading_config.sell_pricing import SellPricingConfig, compute_sell_price
        with pytest.raises(ValueError):
            compute_sell_price("invalid_reason", config=SellPricingConfig())  # type: ignore
