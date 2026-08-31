"""Typed contract tests for the accepted SSE trading-calendar generation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from services.data_sources.calendar_contract import (
    CalendarGenerationContract,
    calendar_contract_for_spec,
    verify_calendar_generation_contract,
)
from services.data_sources.calendar_schema import (
    CALENDAR_SCHEMA_HASH,
    CONTRACT_VERSION,
    DATASET_ID,
    WRITER_ID,
)


def _spec() -> dict:
    return {
        "domain": "trade_cal",
        # 2026-08-31 授权换源: baostock -> calendar_rule (日历改规则推导, 不再取数)。
        # 表/库名相关字段依设计不动 —— 换 adapter 不改表。
        "source": "calendar_rule",
        "api": "query_trade_dates",
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
            "method": "calendar_rule_weekday_minus_holidays",
            "unit": "calendar_day_status",
        },
        "calendar_generation": {
            "contract_version": "1",
            "coverage_start": "19901219",
            "required_through_rule": "observed_year_end",
            "timezone": "Asia/Shanghai",
            "availability": {
                "axis": "provider_response",
                "rule": "response_completed",
                "at": "response_completed_at",
            },
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

    with pytest.raises(
        ValueError,
        match="calendar .* drift|page_limit|legacy compatibility drift",
    ):
        calendar_contract_for_spec(spec)


def test_naked_availability_rule_string_is_rejected() -> None:
    spec = _spec()
    spec["calendar_generation"]["availability"] = "response_completed"

    with pytest.raises(ValueError, match="naked availability_rule|typed availability"):
        calendar_contract_for_spec(spec)


def test_publication_tables_are_formal_not_legacy_raw() -> None:
    from services.data_sources.calendar_schema import (
        CANONICAL_TABLE,
        FRAGMENT_TABLE,
        LANDING_TABLE,
    )

    contract = calendar_contract_for_spec(_spec())

    assert contract.landing_table == LANDING_TABLE
    assert contract.fragment_table == FRAGMENT_TABLE
    assert contract.canonical_table == CANONICAL_TABLE
    assert contract.legacy_target_table == "raw_tushare_trade_cal"
    assert contract.landing_table != contract.legacy_target_table
    assert contract.availability.payload() == {
        "axis": "provider_response",
        "rule": "response_completed",
        "at": "response_completed_at",
    }


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


def test_calendar_contract_cannot_be_constructed_without_factory() -> None:
    with pytest.raises(TypeError, match="calendar_contract_for_spec"):
        CalendarGenerationContract(
            domain="trade_cal",
            dataset_id=DATASET_ID,
            contract_version=CONTRACT_VERSION,
            schema_hash=CALENDAR_SCHEMA_HASH,
            writer_id=WRITER_ID,
            coverage_start="19901219",
            required_through_rule="observed_year_end",
            timezone="Asia/Shanghai",
            availability=calendar_contract_for_spec(_spec()).availability,
            canonicalization_version="1",
            source="tushare",
            api="trade_cal",
            target_db="tushare_raw",
            batch_mode="full_refresh",
            grain=("exchange", "cal_date"),
            fixed_params=(("exchange", "SSE"),),
            page_limit=6000,
            landing_table="landing_tushare_trade_cal",
            fragment_table="landing_tushare_trade_cal_fragment",
            canonical_table="canonical_sse_trading_calendar_generation",
            legacy_target_table="raw_tushare_trade_cal",
            legacy_write_mode="replace_snapshot",
            population_scope=calendar_contract_for_spec(_spec()).population_scope,
            config_hash="a" * 64,
            contract_hash="b" * 64,
        )


def test_dataclasses_replace_cannot_forge_calendar_contract() -> None:
    contract = calendar_contract_for_spec(_spec())

    with pytest.raises(TypeError, match="calendar_contract_for_spec"):
        replace(contract, page_limit=1)


def test_verify_rejects_stale_hash_after_field_tamper() -> None:
    contract = calendar_contract_for_spec(_spec())
    object.__setattr__(contract, "page_limit", 1)

    with pytest.raises(ValueError, match="factory-owned|config_hash|contract_hash"):
        verify_calendar_generation_contract(contract)


def test_verify_accepts_factory_owned_contract() -> None:
    contract = calendar_contract_for_spec(_spec())

    assert verify_calendar_generation_contract(contract) is contract
