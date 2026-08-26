"""Main-rally setup paper: next open after bottom_date, named short horizon.

This is not a published main-rally package and not a full-episode hunter.
Exit is a named calendar horizon. Peak outcome columns are not inputs.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from services.one_name_pointer_bars import (
    OneNamePointerError,
    assert_replay_before_holdout,
    compact_day,
)
from services.strategy_paper import SignalEvent, simulate_signal_hold_paper
from services.strategy_spec import StrategySpec, load_strategy_spec


SETUP_HORIZON_CALENDAR_DAYS = 5
RALLY_SETUP_PNL_SOURCE = "rally_setup_next_open_to_horizon_open"


class RallySetupPaperError(RuntimeError):
    """Setup paper refused a full-episode spec or a holdout bottom date."""


def simulate_rally_setup_paper(
    bars_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    ts_code: str,
    bottom_date: str,
    spec: StrategySpec | None = None,
):
    loaded = spec or load_strategy_spec("main_rally_v1")
    if loaded.package_id != "main_rally_v1":
        raise RallySetupPaperError("setup_paper_requires_main_rally_spec")
    if loaded.exit_kind != "not_implemented_full_episode":
        raise RallySetupPaperError("full_episode_must_remain_stub")
    if loaded.paper_status != "setup_signal_only":
        raise RallySetupPaperError("paper_status_must_remain_setup_signal_only")
    if loaded.sizing != "not_implemented":
        raise RallySetupPaperError("sizing_must_remain_not_implemented")

    anchor = compact_day(bottom_date)
    if len(anchor) != 8:
        raise RallySetupPaperError("invalid_bottom_date")
    try:
        assert_replay_before_holdout([anchor, *bars_by_day.keys()])
    except OneNamePointerError as exc:
        raise RallySetupPaperError(str(exc)) from exc

    overlay = replace(
        loaded,
        max_hold_calendar_days=SETUP_HORIZON_CALENDAR_DAYS,
        pnl_source=RALLY_SETUP_PNL_SOURCE,
    )
    events = (SignalEvent(ts_code=ts_code, available_at=anchor, kind="entry"),)
    return simulate_signal_hold_paper(
        bars_by_day,
        events,
        overlay,
        pnl_source=RALLY_SETUP_PNL_SOURCE,
        event_exit_reason="setup_horizon",
    )


__all__ = [
    "RALLY_SETUP_PNL_SOURCE",
    "RallySetupPaperError",
    "SETUP_HORIZON_CALENDAR_DAYS",
    "simulate_rally_setup_paper",
]
