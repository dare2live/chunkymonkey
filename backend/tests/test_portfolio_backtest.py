import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.portfolio_backtest import (  # noqa: E402
    PositionConstraint,
    SlippageModel,
    _resolve_execution_price,
    run_portfolio_backtest,
)


def test_run_portfolio_backtest_accepts_records_and_round_trips_position():
    prices = {
        ("000001", "2026-01-02"): 10.0,
        ("000001", "2026-01-03"): 12.0,
    }

    result = run_portfolio_backtest(
        [
            {"date": "2026-01-02", "code": "000001", "weight": 1.0},
            {"date": "2026-01-03", "code": "000001", "weight": 0.0},
        ],
        price_fn=lambda code, date: prices.get((code, date)),
        initial_capital=1_000.0,
        slippage=SlippageModel(fixed_bps=0),
        constraint=PositionConstraint(
            max_position_pct=1.0,
            max_n_holdings=10,
            cash_reserve_pct=0.0,
            min_position_pct=0.0,
        ),
    )

    assert result.metrics["final_capital"] == 1_200.0
    assert result.metrics["total_return"] == 0.2
    assert result.metrics["n_trades"] == 2
    assert [trade["side"] for trade in result.trades] == ["buy", "sell"]
    assert result.equity_curve[-1]["position_count"] == 0


def test_run_portfolio_backtest_reports_empty_signals():
    result = run_portfolio_backtest([], price_fn=lambda _code, _date: None)

    assert result.metrics == {"error": "empty signals"}


def test_default_execution_price_prefers_vwap_with_open_fallback():
    assert _resolve_execution_price({"open": 9.8, "close": 10.0, "amount": 1000.0, "volume": 100.0}) == 10.0
    assert _resolve_execution_price({"open": 9.8, "close": 10.0, "amount": None, "volume": None}) == 9.8
