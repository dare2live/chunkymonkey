"""Knife 1a: margin accepted population scope is SSE+SZSE external_aggregate."""
from __future__ import annotations

import pytest

from services.data_sources.margin_population_scope import (
    MARGIN_ACCEPTED_VENUE_IDS,
    MarginPopulationScopeError,
    assert_margin_accepted_population_scope,
    assert_margin_transport_matches_accepted_scope,
)
from services.data_sources.sync_runner import domain_spec, load_registry


def _corrected_scope(**updates: object) -> dict:
    scope = {
        "kind": "external_aggregate",
        "venue_field": "exchange_id",
        "venue_ids": ["SSE", "SZSE"],
        "population_label": "sse_szse_venue_reported_margin",
        "method": "tushare_margin_exchange_summary_sse_szse",
        "unit": "provider_declared_fields",
    }
    scope.update(updates)
    return scope


def test_live_registry_accepted_scope_is_sse_szse_only():
    spec = domain_spec(load_registry(), "margin")
    bound = assert_margin_accepted_population_scope(spec)
    assert bound.venue_ids == MARGIN_ACCEPTED_VENUE_IDS
    assert bound.kind == "external_aggregate"
    # Knife 1b: enabled v3 transport must match accepted SSE+SZSE claim.
    assert spec["execution_policy"]["mode"] == "enabled"
    assert spec["split_by"]["values"] == ["SSE", "SZSE"]
    assert_margin_transport_matches_accepted_scope(spec)


def test_rejects_bse_in_accepted_venue_ids():
    with pytest.raises(MarginPopulationScopeError, match="exactly"):
        assert_margin_accepted_population_scope(
            {
                "domain": "margin",
                "population_scope": _corrected_scope(
                    venue_ids=["SSE", "SZSE", "BSE"]
                ),
            }
        )


def test_rejects_project_universe_relabel():
    with pytest.raises(MarginPopulationScopeError, match="project_universe_pit"):
        assert_margin_accepted_population_scope(
            {
                "domain": "margin",
                "population_scope": {
                    "kind": "project_universe_pit",
                    "universe_policy_id": "active_a_share_trading_universe",
                    "security_field": "ts_code",
                    "as_of_field": "trade_date",
                    "as_of_role": "observation_time",
                },
            }
        )


def test_enabled_mode_requires_transport_without_bse():
    spec = {
        "domain": "margin",
        "execution_policy": {"mode": "enabled", "reason": "active"},
        "population_scope": _corrected_scope(),
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {"BSE": "20230213"},
        },
        "split_by": {"param": "exchange_id", "values": ["SSE", "SZSE", "BSE"]},
    }
    with pytest.raises(MarginPopulationScopeError, match="forbids BSE"):
        assert_margin_transport_matches_accepted_scope(spec)


def test_enabled_mode_passes_when_transport_aligned():
    spec = {
        "domain": "margin",
        "execution_policy": {"mode": "enabled", "reason": "active"},
        "population_scope": _corrected_scope(),
        "batch_completeness": {
            "group_from": {"column": "exchange_id", "transform": "identity"},
            "required_groups": ["SSE", "SZSE"],
            "required_groups_since": {},
        },
        "split_by": {"param": "exchange_id", "values": ["SSE", "SZSE"]},
    }
    assert_margin_transport_matches_accepted_scope(spec)
