"""Typed contract tests for the accepted SSE trading-calendar generation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from services.data_sources.calendar_contract import calendar_contract_for_spec
from services.data_sources.calendar_schema import (
    CALENDAR_SCHEMA_HASH,
    CONTRACT_VERSION,
    DATASET_ID,
    WRITER_ID,
)


def _spec() -> dict:
    return {
        "domain": "trade_cal",
        "source": "tushare",
        "api": "trade_cal",
        "target_db": "tushare_raw",
        "target_table": "raw_tushare_trade_cal",
        "grain": ["exchange", "cal_date"],
        "batch_mode": "full_refresh",
        "fixed_params": {"exchange": "SSE"},
        "page_limit": 6000,
        "write_mode": "replace_snapshot",
        "population_scope": {
            "kind": "external_aggregate",
            "venue_field": "exchange",
            "venue_ids": ["SSE"],
            "population_label": "sse_trading_calendar",
            "method": "tushare_trade_cal",
            "unit": "calendar_day_status",
        },
        "calendar_generation": {
            "contract_version": "1",
            "coverage_start": "19901219",
            "required_through_rule": "observed_year_end",
            "timezone": "Asia/Shanghai",
            "availability_rule": "response_completed",
            "canonicalization_version": "1",
        },
    }


def test_contract_is_factory_owned_and_derives_requests_from_observation_time() -> None:
    contract = calendar_contract_for_spec(_spec())

    assert contract.dataset_id == DATASET_ID
    assert contract.contract_version == CONTRACT_VERSION
    assert contract.schema_hash == CALENDAR_SCHEMA_HASH
    assert contract.writer_id == WRITER_ID
    assert contract.coverage_start == "19901219"
    assert contract.population_scope.venue_ids == ("SSE",)
    observed_at = datetime(2025, 12, 31, 16, 0, tzinfo=timezone.utc)
    assert contract.required_through(observed_at).isoformat() == "2026-12-31"
    assert contract.request_for_page(observed_at, 6000) == {
        "exchange": "SSE",
        "start_date": "19901219",
        "end_date": "20261231",
        "limit": 6000,
        "offset": 6000,
    }
    assert len(contract.config_hash) == 64
    assert len(contract.contract_hash) == 64


@pytest.mark.parametrize("key", sorted(_spec()["calendar_generation"]))
def test_calendar_generation_rejects_missing_keys(key: str) -> None:
    spec = _spec()
    del spec["calendar_generation"][key]

    with pytest.raises(ValueError, match="missing calendar_generation keys"):
        calendar_contract_for_spec(spec)


def test_calendar_generation_rejects_unknown_key() -> None:
    spec = _spec()
    spec["calendar_generation"]["extra"] = "shadow-policy"

    with pytest.raises(ValueError, match="unknown calendar_generation keys: extra"):
        calendar_contract_for_spec(spec)


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_population_scope_keys_are_exact(mutation: str) -> None:
    spec = _spec()
    if mutation == "missing":
        del spec["population_scope"]["unit"]
        message = "missing population_scope keys: unit"
    else:
        spec["population_scope"]["extra"] = "shadow-scope"
        message = "unknown population_scope keys: extra"

    with pytest.raises(ValueError, match=message):
        calendar_contract_for_spec(spec)


def test_calendar_generation_rejects_legacy_coverage_start() -> None:
    spec = _spec()
    spec["calendar_generation"]["coverage_start"] = "20050104"

    with pytest.raises(ValueError, match="coverage_start must be 19901219"):
        calendar_contract_for_spec(spec)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("domain",), "calendar"),
        (("source",), "other"),
        (("api",), "calendar"),
        (("target_db",), "reference"),
        (("target_table",), "calendar_shadow"),
        (("batch_mode",), "by_date_range"),
        (("write_mode",), "append"),
        (("grain",), ["cal_date", "exchange"]),
        (("fixed_params",), {"exchange": "SZSE"}),
        (("page_limit",), 0),
        (("population_scope", "venue_ids"), ["SSE", "SZSE"]),
        (("population_scope", "method"), "self_declared"),
    ],
)
def test_contract_rejects_transport_and_population_drift(
    path: tuple[str, ...], value: object
) -> None:
    spec = deepcopy(_spec())
    owner = spec
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value

    with pytest.raises(ValueError, match="calendar .* drift|page_limit"):
        calendar_contract_for_spec(spec)


def test_hashes_change_when_adjustable_page_limit_changes() -> None:
    first = calendar_contract_for_spec(_spec())
    changed = _spec()
    changed["page_limit"] = 3000
    second = calendar_contract_for_spec(changed)

    assert first.config_hash != second.config_hash
    assert first.contract_hash != second.contract_hash


@pytest.mark.parametrize(
    "observed_at",
    [datetime(2026, 1, 1), "2026-01-01T00:00:00Z", None],
)
def test_required_through_rejects_untyped_or_naive_observation_time(observed_at) -> None:
    contract = calendar_contract_for_spec(_spec())

    with pytest.raises(ValueError, match="timezone-aware datetime"):
        contract.required_through(observed_at)


@pytest.mark.parametrize("offset", [-1, True, 1.5, "0"])
def test_request_rejects_invalid_offset(offset) -> None:
    contract = calendar_contract_for_spec(_spec())

    with pytest.raises(ValueError, match="offset must be a non-negative integer"):
        contract.request_for_page(datetime.now(timezone.utc), offset)
