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
    assert inventory["daily"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    assert inventory["stock_st"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    for item in inventory.values():
        assert item["adapter"] == "tushare"
        assert item["legacy_raw_write"] == "forbidden"
        assert item["landing_writer"]
        assert item["canonical_writer"]
        assert not str(item["landing_writer"]).startswith("pending:")
        assert not str(item["canonical_writer"]).startswith("pending:")


def test_trade_cal_and_margin_cannot_use_legacy_raw_writer() -> None:
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("trade_cal")
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("margin")


def test_daily_and_stock_st_cannot_use_legacy_raw_writer() -> None:
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("daily")
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("stock_st")


def test_enabled_trade_cal_uses_formal_publish_not_legacy_raw(monkeypatch) -> None:
    registry = sr.load_registry()
    monkeypatch.setattr(
        sr,
        "_publish_trade_cal_accepted_generation",
        lambda _spec: {
            "domain": "trade_cal",
            "status": "ok",
            "batches": 1,
            "rows": 1,
            "failed_batches": 0,
            "publication": "accepted_calendar_generation",
        },
    )
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("_write_batch")),
    )
    result = sr.run_domain("trade_cal", registry=registry)
    assert result["publication"] == "accepted_calendar_generation"
