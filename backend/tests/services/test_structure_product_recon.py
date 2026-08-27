"""Structure products: next open, rally B0/B1 setup only, follow notice PIT."""
from __future__ import annotations

import pytest

from services.structure_product_recon import (
    attest_structure_products,
    reject_follow_report_end,
    reject_rally_requires_b3,
    reject_same_day_close_entry,
)


def test_same_day_close_entry_is_rejected():
    with pytest.raises(ValueError, match="same-day close"):
        reject_same_day_close_entry("same_day_close")
    with pytest.raises(ValueError, match="same-day close"):
        reject_same_day_close_entry("vwap")
    assert reject_same_day_close_entry("next_tradable_open") == "next_tradable_open"


def test_follow_report_end_is_not_pit():
    with pytest.raises(ValueError, match="not report-period end"):
        reject_follow_report_end("end_date")
    with pytest.raises(ValueError, match="not report-period end"):
        reject_follow_report_end("trade_date")
    assert reject_follow_report_end("notice_available_at") == "notice_available_at"
    assert reject_follow_report_end("ann_date") == "ann_date"


def test_rally_must_not_require_b3():
    with pytest.raises(ValueError, match="must not require B3"):
        reject_rally_requires_b3(["B1", "B3"])
    with pytest.raises(ValueError, match="must not require B3"):
        reject_rally_requires_b3(["optuna"])
    assert reject_rally_requires_b3(["B0", "B1"]) == ("B0", "B1")
    assert reject_rally_requires_b3(()) == ()


def test_live_packages_attest_without_claiming_identity():
    report = attest_structure_products()
    assert report["status"] == "attested"
    assert report["identity"] is False
    assert report["claimable"] is False
    assert report["strategy_release"] is False
    assert report["optuna"] is False
    assert report["formula_winner_rate"] is False
    assert report["formulas"]["entry_kind"] == "next_tradable_open"
    assert report["follow"]["entry_after"] == "notice_available_at"
    assert report["follow"]["entry_kind"] == "next_tradable_open"
    assert report["rally"]["paper_status"] == "setup_signal_only"
    assert report["rally"]["exit_kind"] == "not_implemented_full_episode"
    assert report["rally"]["b3_required"] is False
    assert report["primary_cut"] is False
