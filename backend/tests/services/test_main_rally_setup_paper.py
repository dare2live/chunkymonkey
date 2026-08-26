from __future__ import annotations

from pathlib import Path

import pytest

from services.main_rally_setup_paper import (
    RALLY_SETUP_PNL_SOURCE,
    SETUP_HORIZON_CALENDAR_DAYS,
    RallySetupPaperError,
    simulate_rally_setup_paper,
)
from services.strategy_paper import SignalEvent, StrategyPaperError, simulate_signal_hold_paper
from services.strategy_spec import load_strategy_spec


DAYS = (
    "20250102",
    "20250103",
    "20250106",
    "20250107",
    "20250108",
    "20250109",
    "20250110",
)
CODE = "000001.SZ"


def _bars() -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    prev = 10.0
    for day in DAYS:
        out[day] = [
            {
                "ts_code": CODE,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "pre_close": prev,
                "vol": 1000.0,
            }
        ]
        prev = 10.0
    return out


def test_setup_paper_buys_next_open_and_exits_named_horizon() -> None:
    fills = simulate_rally_setup_paper(
        _bars(),
        ts_code=CODE,
        bottom_date="20250102",
    )
    assert SETUP_HORIZON_CALENDAR_DAYS == 5
    assert len(fills) == 1
    fill = fills[0]
    assert fill.status == "filled"
    assert fill.entry_date == "20250103"
    assert fill.exit_date == "20250108"
    assert fill.exit_reason == "max_hold"
    assert fill.pnl_source == RALLY_SETUP_PNL_SOURCE
    live = load_strategy_spec("main_rally_v1")
    assert live.exit_kind == "not_implemented_full_episode"
    assert live.max_hold_calendar_days is None
    assert live.paper_status == "setup_signal_only"
    assert live.pnl_source == "not_applicable"


def test_yaml_spec_is_not_full_episode_paper() -> None:
    with pytest.raises(StrategyPaperError, match="missing_exit"):
        simulate_signal_hold_paper(
            _bars(),
            (SignalEvent(CODE, "20250102", "entry"),),
            load_strategy_spec("main_rally_v1"),
            pnl_source="not_applicable",
            event_exit_reason="peak",
        )


def test_holdout_bottom_date_is_refused() -> None:
    with pytest.raises(RallySetupPaperError, match="holdout_partition_refused"):
        simulate_rally_setup_paper(
            _bars(),
            ts_code=CODE,
            bottom_date="20250601",
        )


def test_setup_module_does_not_read_peak_outcomes() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "services" / "main_rally_setup_paper.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "peak_date" not in text
    assert "gain_to_peak" not in text
    assert "load_snapshot_bound_nominal_bars_by_day" not in text
    assert "consume_single_touch" not in text
    assert "StrategyRelease" not in text
