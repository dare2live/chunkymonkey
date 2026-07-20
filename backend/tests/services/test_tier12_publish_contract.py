"""Phase C scaffolding: Tier1/2 publish lineage fail-closed (not publish-complete)."""
from __future__ import annotations

import pytest

from services.tier12_publish_contract import (
    MarketContextPublishEnvelope,
    StockStateDaily,
    attest_market_context_publishable,
    attest_stock_state_publishable,
    config_hash_for,
    stock_state_from_form_row,
)


def test_stock_state_missing_lineage_is_not_publishable() -> None:
    row = StockStateDaily(
        stock_code="600000",
        trade_date="20260717",
        axis_trend="up",
        definition_version=None,
        config_hash=None,
        input_snapshot_id=None,
        eligible_universe_id=None,
        available_at=None,
    )
    report = attest_stock_state_publishable(row)
    assert report.publishable is False
    assert "definition_version" in report.missing_fields
    assert "config_hash" in report.missing_fields
    assert "available_at" in report.missing_fields
    assert report.status == "NOT_PUBLISHABLE"


def test_stock_state_complete_lineage_is_publishable_scaffold() -> None:
    cfg = {"axes": ["trend", "pos"], "definition": "stock_state_stage_pattern_v0"}
    row = StockStateDaily(
        stock_code="600000",
        trade_date="20260717",
        axis_trend="up",
        definition_version="stock_state_stage_pattern_v0",
        config_hash=config_hash_for(cfg),
        input_snapshot_id="nominal_ohlcv:20260717",
        eligible_universe_id="traded_on_observation_date:v1",
        available_at="20260717T160000+0800",
    )
    report = attest_stock_state_publishable(row)
    assert report.publishable is True
    assert report.missing_fields == ()
    assert report.status == "PUBLISHABLE_SCAFFOLD"
    # Scaffold ≠ accepted partition / DB publish.
    assert report.published is False


def test_form_row_bridge_without_lineage_stays_not_publishable() -> None:
    bridged = stock_state_from_form_row(
        {
            "stock_code": "000001",
            "trade_date": "20260716",
            "axis_trend": "up",
            "is_breakout_event": True,
        }
    )
    report = attest_stock_state_publishable(bridged)
    assert bridged.definition_version is None
    assert report.publishable is False
    assert report.status == "NOT_PUBLISHABLE"


def test_market_context_requires_config_hash_and_available_at() -> None:
    env = MarketContextPublishEnvelope(
        decision_time="20260717",
        available_at=None,
        definition_version="market_sensing_project_breadth_v0",
        config_hash=None,
        input_snapshot_id="breadth:20260717",
        eligible_universe_id="project_board_prefixes:v1",
        trust_status="READY",
        risk_on=True,
    )
    report = attest_market_context_publishable(env)
    assert report.publishable is False
    assert "available_at" in report.missing_fields
    assert "config_hash" in report.missing_fields


def test_market_context_complete_lineage_scaffold_only() -> None:
    cfg = {"method": "board_filtered_nominal_breadth", "min_adv_dec": 1.0}
    env = MarketContextPublishEnvelope(
        decision_time="20260717",
        available_at="20260717T160000+0800",
        definition_version="market_sensing_project_breadth_v0",
        config_hash=config_hash_for(cfg),
        input_snapshot_id="nominal_breadth:20260717",
        eligible_universe_id="project_board_prefixes:v1",
        trust_status="READY",
        risk_on=True,
    )
    report = attest_market_context_publishable(env)
    assert report.publishable is True
    assert report.status == "PUBLISHABLE_SCAFFOLD"
    assert report.published is False


def test_config_hash_stable_and_order_independent() -> None:
    a = config_hash_for({"b": 2, "a": 1})
    b = config_hash_for({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64
    with pytest.raises(ValueError):
        config_hash_for("not-a-mapping")  # type: ignore[arg-type]
