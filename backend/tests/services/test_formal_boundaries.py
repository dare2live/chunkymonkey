"""A5 adversarial tests for formal adapter/landing/canonical boundaries."""
from __future__ import annotations

import dataclasses

import pytest

from services.data_sources.formal_boundaries import (
    LIVE_ADAPTER,
    _FORMAL_BOUNDARIES,
    FormalBoundaryError,
    boundary_inventory,
    formal_domains,
    refuse_legacy_raw_write_for_formal_domain,
    require_live_adapter,
)
from services.data_sources import sync_runner as sr


def test_live_adapter_is_tushare_only() -> None:
    # trade_cal switched to baostock 2026-08-30 (authorized source change) — use
    # "daily" here, one of the three formal domains still pinned to LIVE_ADAPTER.
    assert require_live_adapter("tushare", domain="daily") == LIVE_ADAPTER
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="daily")


def test_trade_cal_live_adapter_is_baostock_only() -> None:
    """2026-08-30 authorized source switch: trade_cal is the one formal domain
    whose adapter is no longer LIVE_ADAPTER (tushare) — see formal_boundaries.py
    _FORMAL_BOUNDARIES["trade_cal"]."""
    assert require_live_adapter("baostock", domain="trade_cal") == "baostock"
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("tushare", domain="trade_cal")


def test_wildcard_domain_accepts_any_registered_formal_adapter() -> None:
    # domain="*" is the sync_runner._adapter(source_name) call site, which
    # only has a source name in hand, never a single domain. It must accept
    # any adapter declared by any registered formal domain.
    assert require_live_adapter("tushare", domain="*") == "tushare"


def test_wildcard_domain_rejects_unregistered_adapter() -> None:
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="*")


def test_unregistered_domain_falls_back_to_live_adapter_only() -> None:
    assert require_live_adapter("tushare", domain="no_such_domain") == LIVE_ADAPTER
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="no_such_domain")


def test_per_domain_adapter_override_is_isolated_to_its_own_domain(monkeypatch) -> None:
    # trade_cal itself is now permanently baostock (2026-08-30), so exercise the
    # override mechanism against "daily" instead — temporarily declare it using a
    # different adapter than its LIVE_ADAPTER default, and confirm
    # require_live_adapter enforces that per-domain, without leaking into sibling
    # domains (including trade_cal's own real, non-monkeypatched baostock
    # adapter). monkeypatch.setitem restores _FORMAL_BOUNDARIES["daily"]
    # automatically after the test.
    original = _FORMAL_BOUNDARIES["daily"]
    monkeypatch.setitem(
        _FORMAL_BOUNDARIES,
        "daily",
        dataclasses.replace(original, adapter="baostock"),
    )

    assert require_live_adapter("baostock", domain="daily") == "baostock"
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("tushare", domain="daily")

    # Sibling domains are unaffected by daily's override: margin/stock_st stay
    # tushare-only, and trade_cal's real (unrelated) baostock adapter still works.
    assert require_live_adapter("tushare", domain="margin") == LIVE_ADAPTER
    assert require_live_adapter("baostock", domain="trade_cal") == "baostock"


def test_inventory_declares_three_boundaries_for_formal_domains() -> None:
    domains = set(formal_domains())
    assert {"margin", "trade_cal", "daily", "stock_st"} <= domains
    inventory = {item["domain"]: item for item in boundary_inventory()}
    assert inventory["margin"]["runtime_state"] == "retired_readonly"
    assert inventory["trade_cal"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    assert inventory["daily"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    assert inventory["stock_st"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    # 2026-08-30 授权换源: trade_cal 是唯一 adapter!=tushare 的 formal 域 (per-domain
    # adapter, 见 formal_boundaries.py _FORMAL_BOUNDARIES["trade_cal"] 注释); 其余
    # 三个仍是 LIVE_ADAPTER。
    assert inventory["trade_cal"]["adapter"] == "baostock"
    for name in ("margin", "daily", "stock_st"):
        assert inventory[name]["adapter"] == "tushare"
    for item in inventory.values():
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
