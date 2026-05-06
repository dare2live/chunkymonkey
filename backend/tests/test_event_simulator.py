import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.event_simulator import simulate_events  # noqa: E402


def test_simulate_events_accepts_records_and_reports_take_profit():
    prices = {
        "000001": [
            {"date": "2026-01-02", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
            {"date": "2026-01-03", "open": 104.0, "high": 112.0, "low": 103.0, "close": 108.0},
            {"date": "2026-01-04", "open": 108.0, "high": 109.0, "low": 106.0, "close": 107.0},
        ]
    }

    result = simulate_events(
        [{"institution_id": "inst-1", "stock_code": "000001", "notice_date": "20260102"}],
        {"entry_lag": 0, "max_hold_days": 3, "stop_loss": -0.05, "take_profit": 0.10},
        prices_by_code=prices,
    )

    assert result["n_events"] == 1
    assert result["n_filled"] == 1
    assert result["avg_pnl"] == 0.10
    assert result["win_rate"] == 1.0
    assert result["exit_reason_counts"] == {"take_profit": 1}
    assert result["positions"][0]["exit_reason"] == "take_profit"
    assert result["positions"][0]["hold_days"] == 1
    assert result["positions"][0]["entry_price"] == pytest.approx(99.0)
    assert result["positions"][0]["entry_price_method"] == "entry_day_vwap_qfq_fallback_open"


def test_simulate_events_prefers_conservative_stop_when_both_thresholds_hit():
    prices = {
        "000001": [
            {"date": "2026-01-02", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
            {"date": "2026-01-03", "open": 100.0, "high": 112.0, "low": 94.0, "close": 108.0},
        ]
    }

    result = simulate_events(
        [{"institution_id": "inst-1", "stock_code": "000001", "notice_date": "2026-01-02"}],
        {"entry_lag": 0, "max_hold_days": 3, "stop_loss": -0.05, "take_profit": 0.10},
        prices_by_code=prices,
    )

    assert result["n_filled"] == 1
    assert result["avg_pnl"] == -0.05
    assert result["exit_reason_counts"] == {"stop_loss_conservative": 1}
    assert result["positions"][0]["entry_price"] == pytest.approx(99.0)
    assert result["positions"][0]["entry_price_method"] == "entry_day_vwap_qfq_fallback_open"
    assert result["positions"][0]["intra_maxdd"] == pytest.approx(94.0 / 99.0 - 1.0)


def test_simulate_events_can_use_entry_day_vwap_for_follow_cost():
    prices = {
        "000001": [
            {
                "date": "2026-01-02",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "amount": 1000.0,
                "volume": 10.0,
            },
            {"date": "2026-01-03", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0},
        ]
    }

    result = simulate_events(
        [{"institution_id": "inst-1", "stock_code": "000001", "notice_date": "2026-01-02"}],
        {"entry_lag": 0, "max_hold_days": 1, "entry_price_mode": "entry_day_vwap_qfq"},
        prices_by_code=prices,
    )

    assert result["n_filled"] == 1
    assert result["positions"][0]["entry_price"] == pytest.approx(100.0)
    assert result["positions"][0]["entry_price_method"] == "entry_day_vwap_qfq"
    assert result["positions"][0]["pnl"] == pytest.approx(0.05)


def test_simulate_events_reports_unfilled_when_no_price_panel_rows():
    result = simulate_events(
        [{"institution_id": "inst-1", "stock_code": "000001", "notice_date": "2026-01-02"}],
        {"entry_lag": 1, "max_hold_days": 3},
        prices_by_code={},
    )

    assert result == {"n_events": 1, "n_filled": 0}
