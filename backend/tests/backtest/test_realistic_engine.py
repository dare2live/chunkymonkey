"""单测: backtest/realistic_engine.py — 验证每条出场路径都正确触发."""
from __future__ import annotations

import pytest

from services.backtest.realistic_engine import Bar, simulate_trade


def _mk_bars(prices: list[tuple]) -> list[Bar]:
    """[(date, O, H, L, C, vol, amt), ...] → Bar list."""
    return [Bar(date=p[0], open=p[1], high=p[2], low=p[3], close=p[4],
                volume=p[5], amount=p[6]) for p in prices]


def _flat(start_date: str, n: int, price: float, daily_chg: float = 0.0):
    """生成 n 个 K 线, 价格从 price 起按 daily_chg 增长."""
    from datetime import date as _date, timedelta
    bars = []
    d = _date.fromisoformat(start_date)
    p = price
    for i in range(n):
        bars.append((d.isoformat(), p, p * 1.005, p * 0.995, p,
                     1_000_000, 1_000_000 * p * 100))
        p *= (1 + daily_chg)
        d += timedelta(days=1)
    return bars


class TestSimulateTrade:
    def test_stop_loss_triggered(self):
        """T+1 买入, T+2 跌穿止损."""
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),  # signal day
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),  # T+1 buy day
            # T+2 跌穿 stop_price = 10 × 0.95 = 9.5
            ("2026-05-03", 10, 10, 9.0, 9.2, 1e6, 9e8),
            ("2026-05-04", 9, 9.5, 8.5, 9.3, 1e6, 9e8),
        ])
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.05, target_pct=0.15, trailing_pct=0.02, hp_target=10,
        )
        assert t is not None
        assert t.exit_reason == "stop_loss"
        # 触发价 9.5, 滑点 -0.3% → 9.4715
        assert t.sell_price == pytest.approx(9.5 * (1 - 0.003), abs=0.01)
        assert t.net_ret < 0

    def test_target_hit_then_trailing(self):
        """T+1 买, T+5 达标 arm trailing, T+6 close 回撤 ≥2% → trailing."""
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),  # T+1 buy ~10
            ("2026-05-03", 10.0, 10.5, 10.0, 10.4, 1e6, 1e9),
            ("2026-05-04", 10.4, 11.0, 10.3, 10.8, 1e6, 1e9),
            ("2026-05-05", 10.8, 11.5, 10.7, 11.4, 1e6, 1e9),  # 接近 target 11.5
            ("2026-05-06", 11.4, 12.0, 11.3, 12.0, 1e6, 1e9),  # high≥target 11.5, arm
            # T+7 大跌 close, 较 high_since=12, dd=(11.7-12)/12=-2.5% → trail
            ("2026-05-07", 11.9, 12.0, 11.5, 11.7, 1e6, 1e9),
        ])
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.10, target_pct=0.15, trailing_pct=0.02, hp_target=20,
        )
        assert t is not None
        assert t.exit_reason == "trailing_stop"
        assert t.sell_price == pytest.approx(11.7, rel=0.01)

    def test_hp_expired(self):
        """正常持有到期 hp_target."""
        # 平价行情, 5 日后到期卖
        bars = _mk_bars(_flat("2026-05-01", n=15, price=10.0, daily_chg=0.0))
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.05, target_pct=0.15, trailing_pct=0.02, hp_target=5,
        )
        assert t is not None
        assert t.exit_reason == "hp_expired"
        assert t.holding_days == 5

    def test_one_word_blocked(self):
        """T+1 一字涨停 + T+2 同样涨停 → 无法开仓."""
        from services.trading_config.filters import LimitBoardConfig
        # 用 prev_close=10, 主板 ±10%, 涨停 = 11
        bars = _mk_bars([
            ("2026-05-01", 9.5, 10.0, 9.4, 10.0, 1e6, 1e9),  # signal close 10
            # T+1 一字 11
            ("2026-05-02", 11.0, 11.0, 11.0, 11.0, 1e6, 1.1e9),
            # T+2 一字 12.1
            ("2026-05-03", 12.1, 12.1, 12.1, 12.1, 1e6, 1.2e9),
            ("2026-05-04", 12.0, 12.5, 11.8, 12.3, 1e6, 1.2e9),
        ])
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.05, target_pct=0.15, trailing_pct=0.02, hp_target=10,
        )
        assert t is not None
        assert t.exit_reason == "one_word_blocked"
        assert t.buy_price == 0.0
        assert t.net_ret == 0.0

    def test_net_ret_accounts_for_costs(self):
        """毛收益 +5%, 双边 ~25 bps → 净收益 ~4.75%."""
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),    # buy ~10
            ("2026-05-03", 10, 10.5, 10, 10.5, 1e6, 1e9),   # +5% close (hp_target=1)
        ])
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.10, target_pct=0.30, trailing_pct=0.05, hp_target=1,
        )
        assert t is not None
        # gross = (10.5 - 10) / 10 = 0.05
        # net = 0.05 - 0.00254 ≈ 0.0475
        assert t.gross_ret == pytest.approx(0.05, abs=0.001)
        assert t.net_ret == pytest.approx(0.0475, abs=0.001)

    def test_max_drawdown_is_intraday_low(self):
        """max_drawdown = (intraday low - buy) / buy, 不是 close."""
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),    # buy ~10
            # T+2 intraday 探底 9.6 (跌 4%) 但 close 反弹 9.8
            ("2026-05-03", 10, 10, 9.6, 9.8, 1e6, 9.8e8),
            ("2026-05-04", 9.8, 10.5, 9.7, 10.5, 1e6, 1e9),  # hp_target
        ])
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.10, target_pct=0.30, trailing_pct=0.05, hp_target=2,
        )
        assert t is not None
        # max_dd should reflect intraday low 9.6 not close 9.8
        assert t.max_drawdown == pytest.approx((9.6 - 10) / 10, abs=0.01)

    def test_stop_takes_priority_over_target_same_day(self):
        """同一日同时触发 stop_low 和 target_high → 优先 stop."""
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),    # buy ~10
            # T+2 大幅震荡: low=9.4 (穿 stop 9.5) high=11.6 (穿 target 11.5)
            ("2026-05-03", 10, 11.6, 9.4, 11.5, 1e6, 1e9),
        ])
        t = simulate_trade(
            stock_code="000001", signal_date="2026-05-01", bars=bars,
            stop_pct=-0.05, target_pct=0.15, trailing_pct=0.02, hp_target=10,
        )
        assert t is not None
        # 我们的实现优先 stop, 这是保守策略 (假设 T 日内先到低点)
        assert t.exit_reason == "stop_loss"


class TestBugRegressionGuards:
    def test_avg_dd_is_max_drawdown_not_loss_mean(self):
        """Bug #1 防护: TradeResult.max_drawdown 必须是 intraday max_drawdown,
        不是 'negative final return mean' (旧 avg_dd 定义错的)."""
        # 一笔交易 net_ret = +3%, 但中间一度回撤 -4% → max_drawdown = -4%
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),  # buy
            # T+2 intraday 探底 9.6
            ("2026-05-03", 10, 10, 9.6, 9.7, 1e6, 9.7e8),
            # T+3 反弹收正
            ("2026-05-04", 9.7, 10.5, 9.7, 10.3, 1e6, 1e9),
        ])
        t = simulate_trade("000001", "2026-05-01", bars,
                           stop_pct=-0.10, target_pct=0.30, trailing_pct=0.05, hp_target=2)
        assert t is not None
        assert t.net_ret > 0     # 最终赚钱
        assert t.max_drawdown < -0.03  # 但中间深度回撤 < -3%

    def test_buy_price_consistency_with_config(self):
        """Bug #3 防护: 改 trading_config.buy_pricing.mode → simulate 自动同步."""
        from services.trading_config.execution_model import ExecutionModel
        from services.trading_config.buy_pricing import BuyPricingConfig
        from services.trading_config.sell_pricing import SellPricingConfig
        from services.trading_config.slippage import TradingCostConfig
        from services.trading_config.filters import LimitBoardConfig
        from services.trading_config.horizon import HorizonUnit

        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 9.5, 10.2, 9.5, 10, 1e6, 1e9),
            ("2026-05-03", 10, 10.5, 10, 10.5, 1e6, 1e9),
        ])
        # 用 open 模式, T+1 open = 9.5 → buy=9.5
        # default-free 后 cost 必须显式; 本测试只断言 buy_price, 用零成本隔离变量
        zero_cost = TradingCostConfig(
            buy_commission_bps=0.0, buy_transfer_bps=0.0, buy_impact_bps=0.0,
            sell_commission_bps=0.0, sell_transfer_bps=0.0,
            sell_stamp_duty_bps=0.0, sell_impact_bps=0.0,
        )
        em_open = ExecutionModel(
            version="test", buy_pricing=BuyPricingConfig(mode="open"),
            sell_pricing=SellPricingConfig(), cost=zero_cost,
            limit_board=LimitBoardConfig(), horizon_unit=HorizonUnit.TRADING_DAYS,
        )
        t = simulate_trade("000001", "2026-05-01", bars,
                           stop_pct=-0.10, target_pct=0.30, trailing_pct=0.05, hp_target=1,
                           execution=em_open)
        assert t is not None
        assert t.buy_price == pytest.approx(9.5, abs=0.01)

    def test_costs_create_real_winrate_gap(self):
        """Bug #5 防护: 无成本的 win 在扣成本后变 loss."""
        # 毛收益 +0.1%, 双边 ~25 bps → 净收益 < 0
        bars = _mk_bars([
            ("2026-05-01", 10, 10.5, 9.5, 10, 1e6, 1e9),
            ("2026-05-02", 10, 10.2, 9.8, 10, 1e6, 1e9),    # buy
            ("2026-05-03", 10, 10.02, 10, 10.01, 1e6, 1e9), # +0.1% close
        ])
        t = simulate_trade("000001", "2026-05-01", bars,
                           stop_pct=-0.05, target_pct=0.30, trailing_pct=0.05, hp_target=1)
        assert t is not None
        assert t.gross_ret > 0       # 毛 win
        assert t.net_ret < 0          # 净 loss
