from __future__ import annotations

import pytest

from services.strategy_paper import (
    FOLLOWER_PNL_SOURCE,
    DisclosureEvent,
    StrategyPaperError,
    simulate_follow_hold_paper,
)
from services.strategy_spec import StrategySpec, load_strategy_spec


DAYS = (
    "20250102",
    "20250103",
    "20250106",
    "20250107",
    "20250108",
    "20250109",
    "20250110",
    "20250113",
    "20250114",
    "20250115",
    "20250116",
    "20250117",
    "20250120",
)
CODE = "000001.SZ"


def _bars(*, open_by_day: dict[str, float] | None = None) -> dict[str, list[dict[str, object]]]:
    opens = open_by_day or {}
    out: dict[str, list[dict[str, object]]] = {}
    prev_close = 10.0
    for day in DAYS:
        open_px = float(opens.get(day, 10.0))
        close_px = open_px
        out[day] = [
            {
                "ts_code": CODE,
                "open": open_px,
                "high": open_px,
                "low": open_px,
                "close": close_px,
                "pre_close": prev_close,
                "vol": 1000.0,
            }
        ]
        prev_close = close_px
    return out


def _hold_spec(**overrides: object) -> StrategySpec:
    spec = load_strategy_spec("institution_follow_v1")
    payload = {
        "package_id": spec.package_id,
        "spec_id": spec.spec_id,
        "candidate_generation": spec.candidate_generation,
        "ranking": spec.ranking,
        "sizing": spec.sizing,
        "entry_kind": spec.entry_kind,
        "entry_after": spec.entry_after,
        "exit_kind": spec.exit_kind,
        "exit_event": spec.exit_event,
        "pnl_source": spec.pnl_source,
        "paper_status": spec.paper_status,
        "max_chase_days": spec.max_chase_days,
        "max_hold_calendar_days": spec.max_hold_calendar_days,
        "named_not_run_max_hold_calendar_days": spec.named_not_run_max_hold_calendar_days,
        "applicable_states": spec.applicable_states,
        "config_hash": spec.config_hash,
    }
    payload.update(overrides)
    return StrategySpec(**payload)  # type: ignore[arg-type]


def test_follow_smoke_buys_next_open_after_increase_and_sells_after_decrease() -> None:
    fills = simulate_follow_hold_paper(
        _bars(open_by_day={"20250113": 11.0}),
        (
            DisclosureEvent(CODE, "20250102", "increase"),
            DisclosureEvent(CODE, "20250110", "decrease"),
        ),
        load_strategy_spec("institution_follow_v1"),
    )
    assert len(fills) == 1
    fill = fills[0]
    assert fill.status == "filled"
    assert fill.entry_date == "20250103"
    assert fill.exit_date == "20250113"
    assert fill.exit_reason == "event_decrease"
    assert fill.pnl_source == FOLLOWER_PNL_SOURCE
    assert fill.net_return is not None and fill.net_return > 0
    institution_vwap_alpha = 0.42
    assert fill.net_return != institution_vwap_alpha


def test_follow_smoke_chases_limit_up_entry() -> None:
    fills = simulate_follow_hold_paper(
        _bars(open_by_day={"20250103": 11.0, "20250106": 10.2}),
        (DisclosureEvent(CODE, "20250102", "increase"),),
        _hold_spec(max_hold_calendar_days=7),
    )
    assert fills[0].status == "filled"
    assert fills[0].entry_date == "20250106"
    assert fills[0].exit_reason == "max_hold"


def test_follow_smoke_exits_on_max_hold_when_no_decrease() -> None:
    fills = simulate_follow_hold_paper(
        _bars(),
        (DisclosureEvent(CODE, "20250102", "increase"),),
        _hold_spec(max_hold_calendar_days=7),
    )
    assert fills[0].status == "filled"
    assert fills[0].entry_date == "20250103"
    assert fills[0].exit_date == "20250110"
    assert fills[0].exit_reason == "max_hold"
    live = load_strategy_spec("institution_follow_v1")
    assert live.named_not_run_max_hold_calendar_days == (180,)
    assert live.max_hold_calendar_days == 90


def test_follow_smoke_keeps_one_position_when_increase_repeats() -> None:
    fills = simulate_follow_hold_paper(
        _bars(open_by_day={"20250113": 11.0}),
        (
            DisclosureEvent(CODE, "20250102", "increase"),
            DisclosureEvent(CODE, "20250107", "increase"),
            DisclosureEvent(CODE, "20250110", "decrease"),
        ),
        load_strategy_spec("institution_follow_v1"),
    )
    assert len(fills) == 1
    assert fills[0].entry_date == "20250103"
    assert fills[0].exit_date == "20250113"


def test_follow_smoke_chases_limit_down_exit() -> None:
    fills = simulate_follow_hold_paper(
        _bars(open_by_day={"20250113": 9.0, "20250114": 10.1}),
        (
            DisclosureEvent(CODE, "20250102", "increase"),
            DisclosureEvent(CODE, "20250110", "decrease"),
        ),
        load_strategy_spec("institution_follow_v1"),
    )
    assert fills[0].status == "filled"
    assert fills[0].entry_date == "20250103"
    assert fills[0].exit_date == "20250114"
    assert fills[0].exit_reason == "event_decrease"


def test_hold_paper_does_not_sell_on_entry_day() -> None:
    fills = simulate_follow_hold_paper(
        _bars(),
        (
            DisclosureEvent(CODE, "20250102", "increase"),
            DisclosureEvent(CODE, "20250102", "decrease"),
        ),
        load_strategy_spec("institution_follow_v1"),
    )
    assert fills[0].status == "filled"
    assert fills[0].entry_date == "20250103"
    assert fills[0].exit_date == "20250106"
    assert fills[0].exit_date > fills[0].entry_date


def test_hold_paper_reenters_after_exit() -> None:
    fills = simulate_follow_hold_paper(
        _bars(),
        (
            DisclosureEvent(CODE, "20250102", "increase"),
            DisclosureEvent(CODE, "20250107", "decrease"),
            DisclosureEvent(CODE, "20250110", "increase"),
            DisclosureEvent(CODE, "20250115", "decrease"),
        ),
        load_strategy_spec("institution_follow_v1"),
    )
    filled = [row for row in fills if row.status == "filled"]
    assert len(filled) == 2
    assert filled[0].entry_date == "20250103"
    assert filled[0].exit_date == "20250108"
    assert filled[1].entry_date == "20250113"
    assert filled[1].exit_date == "20250116"
    assert filled[1].entry_date > filled[0].exit_date


def test_hold_paper_refuses_non_follow_spec() -> None:
    with pytest.raises(StrategyPaperError, match="hold_paper_requires_institution_follow_spec"):
        simulate_follow_hold_paper(
            _bars(),
            (DisclosureEvent(CODE, "20250102", "increase"),),
            load_strategy_spec("main_rally_v1"),
        )
