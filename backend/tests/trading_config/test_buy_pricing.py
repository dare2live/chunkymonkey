"""单测: buy_pricing.py"""
from __future__ import annotations

import pytest


class TestBuyPricing:
    def test_vwap_default(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="vwap")
        # amount=1,000,000 元 / volume=1000 手 / 100 = 价格 10
        p = compute_buy_price(signal_close=9.5, next_amount=1_000_000.0,
                              next_volume=1000.0, config=cfg)
        assert p == pytest.approx(10.0)

    def test_vwap_with_slippage(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="vwap", slippage_pct=0.005)
        p = compute_buy_price(signal_close=9.5, next_amount=1_000_000.0,
                              next_volume=1000.0, config=cfg)
        assert p == pytest.approx(10.05)

    def test_signal_close_plus_pct(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="signal_close_plus_pct", signal_close_pct=0.005)
        p = compute_buy_price(signal_close=100.0, config=cfg)
        assert p == pytest.approx(100.5)

    def test_open_mode(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="open", slippage_pct=0.0)
        p = compute_buy_price(signal_close=9.0, next_open=9.5, config=cfg)
        assert p == pytest.approx(9.5)

    def test_returns_none_on_missing_data(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="vwap")
        # 没传 volume → None
        p = compute_buy_price(signal_close=9.0, config=cfg)
        assert p is None

    def test_vwap_zero_volume_returns_none(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="vwap")
        p = compute_buy_price(signal_close=9.0, next_amount=1000.0, next_volume=0.0, config=cfg)
        assert p is None

    def test_unknown_mode_raises(self):
        from services.trading_config.buy_pricing import BuyPricingConfig, compute_buy_price
        cfg = BuyPricingConfig(mode="invalid_mode")  # type: ignore
        with pytest.raises(ValueError):
            compute_buy_price(signal_close=9.0, config=cfg)
