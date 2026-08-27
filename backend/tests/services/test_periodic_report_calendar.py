"""Statutory completeness clock: all four period types, not only H1 08-31."""
from __future__ import annotations

import pytest

from services.data_sources.periodic_report_calendar import (
    disclosure_deadline_iso,
    is_past_completeness_deadline,
)


@pytest.mark.parametrize(
    "report_date,deadline",
    [
        ("2026-03-31", "2026-04-30"),
        ("2026-06-30", "2026-08-31"),
        ("2026-09-30", "2026-10-31"),
        ("2025-12-31", "2026-04-30"),
    ],
)
def test_disclosure_deadline_all_four_period_types(report_date, deadline):
    assert disclosure_deadline_iso(report_date) == deadline


@pytest.mark.parametrize(
    "report_date,today,due",
    [
        ("2026-03-31", "2026-04-29", False),
        ("2026-03-31", "2026-04-30", True),
        ("2026-06-30", "2026-08-30", False),
        ("2026-06-30", "2026-08-31", True),
        ("2026-09-30", "2026-10-30", False),
        ("2026-09-30", "2026-10-31", True),
        ("2025-12-31", "2026-04-29", False),
        ("2025-12-31", "2026-04-30", True),
    ],
)
def test_completeness_due_on_deadline_not_day_before(report_date, today, due):
    assert is_past_completeness_deadline(report_date, today) is due


def test_annual_and_q1_share_april_30_but_are_checked_separately():
    """04-30 closes both 年报 and Q1; latest_statutory_complete returns only Q1."""
    assert is_past_completeness_deadline("2025-12-31", "2026-04-30") is True
    assert is_past_completeness_deadline("2026-03-31", "2026-04-30") is True
    from services.data_sources.periodic_report_calendar import (
        latest_statutory_complete_report_period,
    )

    assert latest_statutory_complete_report_period("20260430") == "20260331"
