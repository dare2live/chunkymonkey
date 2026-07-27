"""Training-window boundary guard for the future Tier3 holdout contract.

This module enforces one current fact: training and tuning data must end before
``holdout_start`` from ``backend/config/holdout_policy.yaml``. It does not claim
to implement preregistration, a global single-touch budget, parameter freeze, or
StrategyRelease. Those controls remain requirements in
``docs/strategy_validation_contract.md`` until a real Tier3 release runtime owns
an atomic evidence store and concurrent-writer tests.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO / "backend" / "config" / "holdout_policy.yaml"


class HoldoutPolicyError(RuntimeError):
    """Holdout policy violation base class."""


class HoldoutBoundaryViolation(HoldoutPolicyError):
    """Training or tuning data reaches the reserved holdout window."""


def load_policy(path: Path | None = None) -> dict:
    """Load the active boundary policy; future release rules do not live here."""
    raw = yaml.safe_load((path or POLICY_PATH).read_text(encoding="utf-8")) or {}
    if "holdout_start" not in raw:
        raise ValueError("holdout_policy.yaml missing holdout_start")
    return raw


def _norm_yyyymmdd(value) -> str:
    """Normalize date/datetime/ISO strings to ``YYYYMMDD``."""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    text = str(value).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"expected YYYYMMDD, YYYY-MM-DD, or date: {value!r}")
    return text


def training_cutoff_before_holdout(path: Path | None = None) -> str:
    """Return the natural day before the first holdout day."""
    holdout_start = datetime.strptime(
        _norm_yyyymmdd(load_policy(path)["holdout_start"]), "%Y%m%d"
    ).date()
    return (holdout_start - timedelta(days=1)).strftime("%Y%m%d")


def assert_holdout_untouched(
    data_end_date,
    conn=None,
    *,
    actual_data_end=None,
) -> None:
    """Reject training/tuning when declared or actual end reaches holdout.

    ``actual_data_end`` is the max partition/day actually loaded into the run
    (e.g. snapshot nominal date_set max). Declared-only checks are insufficient
    when consumers previously read live full accepted calendars.
    """
    del conn  # Compatibility with current caller signatures; no database is read.
    holdout_start = _norm_yyyymmdd(load_policy()["holdout_start"])
    data_end = _norm_yyyymmdd(data_end_date)
    if data_end >= holdout_start:
        raise HoldoutBoundaryViolation(
            f"training data_end_date={data_end} reaches holdout_start={holdout_start}; "
            "holdout use requires a future atomic Tier3 release runtime"
        )
    if actual_data_end is not None:
        actual = _norm_yyyymmdd(actual_data_end)
        if actual >= holdout_start:
            raise HoldoutBoundaryViolation(
                f"actual_data_end={actual} reaches holdout_start={holdout_start}; "
                "loaded partitions must stay strictly before holdout"
            )
        if actual > data_end:
            raise HoldoutBoundaryViolation(
                f"actual_data_end={actual} exceeds declared data_end_date={data_end}; "
                "runner must not load past the prereg/training cutoff"
            )


__all__ = [
    "HoldoutBoundaryViolation",
    "HoldoutPolicyError",
    "assert_holdout_untouched",
    "load_policy",
    "training_cutoff_before_holdout",
]
