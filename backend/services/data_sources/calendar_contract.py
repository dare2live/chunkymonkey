"""Typed contract for one complete accepted SSE trading-calendar generation.

The factory consumes one already-merged registry snapshot.  It deliberately
does not load YAML, consult module globals for policy, or accept caller-supplied
request dates.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from services.data_sources.calendar_schema import (
    CALENDAR_SCHEMA_HASH,
    CONTRACT_VERSION,
    DATASET_ID,
    WRITER_ID,
)


_GENERATION_KEYS = frozenset(
    {
        "contract_version",
        "coverage_start",
        "required_through_rule",
        "timezone",
        "availability_rule",
        "canonicalization_version",
    }
)
_SCOPE_KEYS = frozenset(
    {"kind", "venue_field", "venue_ids", "population_label", "method", "unit"}
)
_EXPECTED_SCOPE = {
    "kind": "external_aggregate",
    "venue_field": "exchange",
    "venue_ids": ["SSE"],
    "population_label": "sse_trading_calendar",
    "method": "tushare_trade_cal",
    "unit": "calendar_day_status",
}
_EXPECTED_TRANSPORT = {
    "domain": "trade_cal",
    "source": "tushare",
    "api": "trade_cal",
    "target_db": "tushare_raw",
    "target_table": "raw_tushare_trade_cal",
    "grain": ["exchange", "cal_date"],
    "batch_mode": "full_refresh",
    "fixed_params": {"exchange": "SSE"},
    "write_mode": "replace_snapshot",
}
_EXPECTED_GENERATION = {
    "contract_version": CONTRACT_VERSION,
    "coverage_start": "19901219",
    "required_through_rule": "observed_year_end",
    "timezone": "Asia/Shanghai",
    "availability_rule": "response_completed",
    "canonicalization_version": "1",
}


def _hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"trade_cal: {field} must be a mapping")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], field: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise ValueError(f"trade_cal: missing {field} keys: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"trade_cal: unknown {field} keys: {', '.join(unknown)}")


@dataclass(frozen=True)
class CalendarPopulationScope:
    kind: str
    venue_field: str
    venue_ids: tuple[str, ...]
    population_label: str
    method: str
    unit: str

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "venue_field": self.venue_field,
            "venue_ids": list(self.venue_ids),
            "population_label": self.population_label,
            "method": self.method,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class CalendarGenerationContract:
    domain: str
    dataset_id: str
    contract_version: str
    schema_hash: str
    writer_id: str
    coverage_start: str
    required_through_rule: str
    timezone: str
    availability_rule: str
    canonicalization_version: str
    source: str
    api: str
    target_db: str
    target_table: str
    batch_mode: str
    write_mode: str
    grain: tuple[str, ...]
    fixed_params: tuple[tuple[str, str], ...]
    page_limit: int
    population_scope: CalendarPopulationScope
    config_hash: str
    contract_hash: str

    def required_through(self, observed_at: datetime) -> date:
        """Return the last calendar date promised by this observed generation."""

        if (
            not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be a timezone-aware datetime")
        local = observed_at.astimezone(ZoneInfo(self.timezone))
        return date(local.year, 12, 31)

    def request_for_page(self, observed_at: datetime, offset: int) -> dict[str, Any]:
        """Derive one provider request; callers cannot override contract bounds."""

        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        end_date = self.required_through(observed_at).strftime("%Y%m%d")
        return {
            "exchange": dict(self.fixed_params)["exchange"],
            "start_date": self.coverage_start,
            "end_date": end_date,
            "limit": self.page_limit,
            "offset": offset,
        }


def calendar_contract_for_spec(spec: Mapping[str, Any]) -> CalendarGenerationContract:
    """Derive a frozen calendar contract from one caller-owned registry snapshot."""

    spec = _mapping(spec, "domain spec")
    for key, expected in _EXPECTED_TRANSPORT.items():
        if key not in spec:
            raise ValueError(f"trade_cal: missing calendar transport field: {key}")
        if spec[key] != expected:
            raise ValueError(
                f"trade_cal: calendar transport drift for {key}: "
                f"actual={spec[key]!r} expected={expected!r}"
            )

    page_limit = spec.get("page_limit")
    if (
        isinstance(page_limit, bool)
        or not isinstance(page_limit, int)
        or page_limit <= 0
    ):
        raise ValueError("trade_cal: page_limit must be a positive integer")

    if "calendar_generation" not in spec:
        raise ValueError("trade_cal: missing calendar_generation")
    generation = _mapping(spec["calendar_generation"], "calendar_generation")
    _exact_keys(generation, _GENERATION_KEYS, "calendar_generation")
    for key, expected in _EXPECTED_GENERATION.items():
        if generation[key] != expected:
            raise ValueError(
                f"trade_cal: calendar generation drift: {key} must be {expected}"
            )

    if "population_scope" not in spec:
        raise ValueError("trade_cal: missing population_scope")
    scope_raw = _mapping(spec["population_scope"], "population_scope")
    _exact_keys(scope_raw, _SCOPE_KEYS, "population_scope")
    for key, expected in _EXPECTED_SCOPE.items():
        if scope_raw[key] != expected:
            raise ValueError(
                f"trade_cal: calendar population drift for {key}: "
                f"actual={scope_raw[key]!r} expected={expected!r}"
            )

    scope = CalendarPopulationScope(
        kind=str(scope_raw["kind"]),
        venue_field=str(scope_raw["venue_field"]),
        venue_ids=tuple(str(value) for value in scope_raw["venue_ids"]),
        population_label=str(scope_raw["population_label"]),
        method=str(scope_raw["method"]),
        unit=str(scope_raw["unit"]),
    )
    transport_payload = {
        **_EXPECTED_TRANSPORT,
        "page_limit": page_limit,
    }
    policy_payload = {key: generation[key] for key in sorted(_GENERATION_KEYS)}
    config_hash = _hash(
        {
            "transport": transport_payload,
            "calendar_generation": policy_payload,
            "population_scope": scope.payload(),
        }
    )
    contract_hash = _hash(
        {
            "dataset_id": DATASET_ID,
            "contract_version": CONTRACT_VERSION,
            "schema_hash": CALENDAR_SCHEMA_HASH,
            "writer_id": WRITER_ID,
            "config_hash": config_hash,
        }
    )
    return CalendarGenerationContract(
        domain="trade_cal",
        dataset_id=DATASET_ID,
        contract_version=CONTRACT_VERSION,
        schema_hash=CALENDAR_SCHEMA_HASH,
        writer_id=WRITER_ID,
        coverage_start=str(generation["coverage_start"]),
        required_through_rule=str(generation["required_through_rule"]),
        timezone=str(generation["timezone"]),
        availability_rule=str(generation["availability_rule"]),
        canonicalization_version=str(generation["canonicalization_version"]),
        source="tushare",
        api="trade_cal",
        target_db="tushare_raw",
        target_table="raw_tushare_trade_cal",
        batch_mode="full_refresh",
        write_mode="replace_snapshot",
        grain=("exchange", "cal_date"),
        fixed_params=(("exchange", "SSE"),),
        page_limit=page_limit,
        population_scope=scope,
        config_hash=config_hash,
        contract_hash=contract_hash,
    )


__all__ = [
    "CalendarGenerationContract",
    "CalendarPopulationScope",
    "calendar_contract_for_spec",
]
