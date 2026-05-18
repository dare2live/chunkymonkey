from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.strategies.sniper.confluence import evaluate_confluence
from services.strategies.sniper.kelly_sizer import (
    kelly_fraction_from_ev,
    normalize_kelly_weights,
)
from services.strategies.sniper.exit_rules import evaluate_exit as evaluate_sniper_exit


def _history(n: int = 260) -> list[dict]:
    start = date(2025, 1, 1)
    rows = []
    for i in range(n):
        rows.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "ret_60d": i / 1000,
            "lhb_inst_net_buy": i * 10_000,
            "main_capital_net_inflow_5d": i * 20_000,
            "sector_momentum": i / 2000,
            "sue": i / 100,
            "unlock_ratio_180d": i / 1000,
            "pledge_ratio": i / 900,
        })
    return rows


def test_confluence_score():
    history = _history()
    features = {
        "ret_60d": 0.50,
        "lhb_inst_net_buy": 10_000_000,
        "main_capital_net_inflow_5d": 20_000_000,
        "sector_momentum": 0.40,
        "sue": 5.0,
        "yesterday_limit_up": True,
        "unlock_ratio_180d": 0.01,
        "pledge_ratio": 0.01,
    }
    verdict = evaluate_confluence("2026-01-01", features, history=history)  # rule-compliance: ok evidence=test-fixture

    assert 0 <= verdict.confluence_score <= 7
    assert verdict.confluence_score == 7
    assert verdict.triggered is True


def test_confluence_oos():
    history = _history()
    features = {
        "ret_60d": 0.50,
        "lhb_inst_net_buy": 10_000_000,
        "main_capital_net_inflow_5d": 20_000_000,
        "sector_momentum": 0.40,
        "sue": 5.0,
        "yesterday_limit_up": True,
        "unlock_ratio_180d": 0.01,
        "pledge_ratio": 0.01,
    }
    baseline = evaluate_confluence("2026-01-01", features, history=history)  # rule-compliance: ok evidence=test-fixture
    injected = history + [{
        "date": "2026-02-01",  # rule-compliance: ok evidence=test-fixture
        "ret_60d": 999.0,
        "lhb_inst_net_buy": 999_000_000,
        "main_capital_net_inflow_5d": 999_000_000,
        "sector_momentum": 999.0,
        "sue": 999.0,
        "unlock_ratio_180d": 999.0,
        "pledge_ratio": 999.0,
    }]
    after_injection = evaluate_confluence("2026-01-01", features, history=injected)  # rule-compliance: ok evidence=test-fixture

    assert after_injection.confluence_score == baseline.confluence_score
    assert after_injection.triggered == baseline.triggered
    assert after_injection.thresholds == baseline.thresholds


def test_kelly_half_kelly():
    for ev in (-10.0, -0.5, 0.0, 0.5, 10.0, 10_000.0):
        assert 0.0 <= kelly_fraction_from_ev(ev, win_loss_ratio=2.0) <= 0.30


def test_kelly_multi_trade():
    weights = normalize_kelly_weights({"A": 0.30, "B": 0.30, "C": 0.30, "D": 0.30})

    assert sum(weights.values()) <= 1.0
    assert all(value >= 0.0 for value in weights.values())


def test_exit_trailing():
    signal = evaluate_sniper_exit(
        entry_date="2026-01-01",  # rule-compliance: ok evidence=test-fixture
        entry_price=100.0,
        current_date="2026-01-08",  # rule-compliance: ok evidence=test-fixture
        current_price=101.0,
        running_high_price=110.0,
    )

    assert signal is not None
    assert signal.exit_type == "trailing_stop"


def test_exit_target():
    signal = evaluate_sniper_exit(
        entry_date="2026-01-01",  # rule-compliance: ok evidence=test-fixture
        entry_price=100.0,
        current_date="2026-01-06",  # rule-compliance: ok evidence=test-fixture
        current_price=118.0,
        current_high_price=121.0,
    )

    assert signal is not None
    assert signal.exit_type == "target_exit"
    assert signal.exit_price == 120.0


def test_exit_time():
    signal = evaluate_sniper_exit(
        entry_date="2026-01-01",  # rule-compliance: ok evidence=test-fixture
        entry_price=100.0,
        current_date="2026-01-21",  # rule-compliance: ok evidence=test-fixture
        current_price=103.0,
        running_high_price=105.0,
    )

    assert signal is not None
    assert signal.exit_type == "time_stop"
