from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution_model import build_fixed_holding_trades, build_sell_rule_trades, guarded_vwap


def test_guarded_vwap_lot_volume() -> None:
    price, method = guarded_vwap(amount=10_000_000, volume=10_000, close=10.0, low=9.8, high=10.2)
    assert round(price, 2) == 10.0
    assert method.startswith("vwap_lot")


def test_buy_delay_after_limit_up() -> None:
    dates = np.array(["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"])
    opens = np.array([10.0, 11.0, 11.2, 11.3])
    highs = np.array([10.0, 11.0, 11.4, 11.5])
    lows = np.array([10.0, 11.0, 11.1, 11.0])
    closes = np.array([10.0, 11.0, 11.3, 11.4])
    volumes = np.array([1000.0, 1000.0, 1000.0, 1000.0])
    amounts = closes * volumes * 100
    trades = build_fixed_holding_trades(
        code="000001",
        dates=dates,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        amounts=amounts,
        signal_indices=[0],
        holding_periods=[1],
        buy_delay_days=2,
    )[1]
    assert trades[0]["buy_date"] == "2026-05-03"
    assert trades[0]["delay_buy_days"] == 1


def test_sell_delay_after_limit_down() -> None:
    dates = np.array(["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"])
    opens = np.array([10.0, 10.1, 9.09, 9.2])
    highs = np.array([10.2, 10.2, 9.09, 9.4])
    lows = np.array([9.9, 10.0, 9.09, 9.1])
    closes = np.array([10.0, 10.1, 9.09, 9.3])
    volumes = np.array([1000.0, 1000.0, 1000.0, 1000.0])
    amounts = closes * volumes * 100
    trades = build_fixed_holding_trades(
        code="000001",
        dates=dates,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        amounts=amounts,
        signal_indices=[0],
        holding_periods=[1],
    )[1]
    assert trades[0]["sell_date"] == "2026-05-04"
    assert trades[0]["delay_sell_days"] == 1


def test_formula_exit_sell_rule() -> None:
    dates = np.array([f"2026-05-{i:02d}" for i in range(1, 9)])
    closes = np.array([10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4])
    opens = closes.copy()
    highs = closes + 0.1
    lows = closes - 0.1
    volumes = np.ones(len(closes)) * 1000.0
    amounts = closes * volumes * 100
    exits = np.zeros(len(closes), dtype=bool)
    exits[4] = True
    trades = build_sell_rule_trades(
        code="000001",
        dates=dates,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        amounts=amounts,
        signal_indices=[1],
        sell_rule="formula_exit_or_5",
        exit_signals=exits,
    )
    assert trades[0]["buy_date"] == "2026-05-03"
    assert trades[0]["sell_date"] == "2026-05-05"
    assert trades[0]["sell_rule"] == "formula_exit_or_5"
    assert trades[0]["exit_triggered"] is True


def main() -> None:
    test_guarded_vwap_lot_volume()
    test_buy_delay_after_limit_up()
    test_sell_delay_after_limit_down()
    test_formula_exit_sell_rule()
    print("execution_model_smoke: ok")


if __name__ == "__main__":
    main()
