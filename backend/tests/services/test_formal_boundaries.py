"""A5 adversarial tests for formal adapter/landing/canonical boundaries."""
from __future__ import annotations

import pytest

from services.data_sources.formal_boundaries import (
    LIVE_ADAPTER,
    FormalBoundaryError,
    boundary_inventory,
    formal_domains,
    refuse_legacy_raw_write_for_formal_domain,
    require_live_adapter,
)
from services.data_sources import sync_runner as sr


def test_live_adapter_is_tushare_only() -> None:
    assert require_live_adapter("tushare", domain="trade_cal") == LIVE_ADAPTER
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="trade_cal")


def test_inventory_declares_three_boundaries_for_formal_domains() -> None:
    domains = set(formal_domains())
    assert {"margin", "trade_cal", "daily", "stock_st"} <= domains
    inventory = {item["domain"]: item for item in boundary_inventory()}
    assert inventory["margin"]["runtime_state"] == "retired_readonly"
    assert inventory["trade_cal"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    assert inventory["daily"]["runtime_state"] == "writers_pending"
    assert inventory["stock_st"]["runtime_state"] == "writers_pending"
    for item in inventory.values():
        assert item["adapter"] == "tushare"
        assert item["legacy_raw_write"] == "forbidden"
        assert item["landing_writer"]
        assert item["canonical_writer"]


def test_trade_cal_and_margin_cannot_use_legacy_raw_writer() -> None:
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("trade_cal")
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("margin")


def test_writers_pending_domains_are_not_write_walled_yet() -> None:
    # daily/stock_st remain on temporary legacy path until accepted writers exist.
    refuse_legacy_raw_write_for_formal_domain("daily")
    refuse_legacy_raw_write_for_formal_domain("stock_st")


def test_enabled_trade_cal_still_blocked_by_formal_boundary(monkeypatch) -> None:
    registry = sr.load_registry()
    registry = {
        **registry,
        "domains": {
            **registry["domains"],
            "trade_cal": {
                **registry["domains"]["trade_cal"],
                "execution_policy": {"mode": "enabled", "reason": "forced_for_test"},
            },
        },
    }
    for name in ("_adapter", "_target_conn", "_write_batch"):
        monkeypatch.setattr(
            sr,
            name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)),
        )
    with pytest.raises(sr.ExecutionPolicyError) as caught:
        sr.run_domain("trade_cal", registry=registry)
    message = str(caught.value).lower()
    assert "formal boundary" in message or "legacy" in message
