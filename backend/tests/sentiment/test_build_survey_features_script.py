"""Tests for the survey feature build script's refresh-window guards."""
from __future__ import annotations

import pytest


def test_write_start_uses_configured_lookback_when_start_omitted():
    from scripts.build_survey_features import _resolve_windows
    from services.sentiment.configs import WindowConfig

    read_start, write_start, write_end = _resolve_windows(
        arg_start=None,
        arg_write_start="2026-05-10",
        arg_end="2026-05-12",
        earliest_event_date="2026-01-01",
        default_end="2026-05-20",
        win_cfg=WindowConfig(survey_short_days=2, survey_long_days=5),
    )

    assert read_start == "2026-05-06"
    assert write_start == "2026-05-10"
    assert write_end == "2026-05-12"


def test_explicit_start_is_read_start_for_write_window():
    from scripts.build_survey_features import _resolve_windows
    from services.sentiment.configs import WindowConfig

    read_start, write_start, write_end = _resolve_windows(
        arg_start="2026-05-01",
        arg_write_start="2026-05-10",
        arg_end=None,
        earliest_event_date="2026-01-01",
        default_end="2026-05-20",
        win_cfg=WindowConfig(survey_short_days=2, survey_long_days=5),
    )

    assert read_start == "2026-05-01"
    assert write_start == "2026-05-10"
    assert write_end == "2026-05-20"


def test_start_after_write_start_is_rejected():
    from scripts.build_survey_features import _resolve_windows

    with pytest.raises(ValueError, match="cannot be after --write-start"):
        _resolve_windows(
            arg_start="2026-05-11",
            arg_write_start="2026-05-10",
            arg_end="2026-05-12",
            earliest_event_date="2026-01-01",
            default_end="2026-05-20",
        )


def test_empty_refresh_window_requires_explicit_override():
    from scripts.build_survey_features import _require_non_empty_window

    with pytest.raises(RuntimeError, match="allow-empty-window"):
        _require_non_empty_window(
            label="产出行数",
            count=0,
            write_start="2026-05-10",
            write_end="2026-05-12",
            allow_empty_window=False,
        )

    _require_non_empty_window(
        label="产出行数",
        count=0,
        write_start="2026-05-10",
        write_end="2026-05-12",
        allow_empty_window=True,
    )
