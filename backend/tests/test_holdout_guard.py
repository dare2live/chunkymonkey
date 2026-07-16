"""Tests for the currently active holdout training-boundary guard."""
from __future__ import annotations

from datetime import date

import pytest

from services import holdout_guard as hg


def test_assert_gate_rejects_holdout_start_and_later() -> None:
    holdout_start = str(hg.load_policy()["holdout_start"])
    for value in (holdout_start, "2025-06-02", "2026-07-01", date(2025, 12, 31)):
        with pytest.raises(hg.HoldoutBoundaryViolation):
            hg.assert_holdout_untouched(value)


def test_assert_gate_accepts_training_window() -> None:
    hg.assert_holdout_untouched("20250531")
    hg.assert_holdout_untouched("2025-05-31")
    hg.assert_holdout_untouched(date(2024, 12, 31))


def test_default_training_cutoff_is_before_holdout() -> None:
    policy = hg.load_policy()
    cutoff = hg.training_cutoff_before_holdout()
    assert cutoff < str(policy["holdout_start"])
    hg.assert_holdout_untouched(cutoff)


def test_assert_gate_rejects_invalid_date() -> None:
    with pytest.raises(ValueError):
        hg.assert_holdout_untouched("not-a-date")


def test_policy_does_not_claim_unimplemented_single_touch_runtime() -> None:
    policy = hg.load_policy()
    assert set(policy) == {"version", "status", "holdout_start"}
    assert policy["status"] == "training_boundary_only"
