"""Typed contract for one complete accepted SSE trading-calendar generation.

The factory consumes one already-merged registry snapshot.  It deliberately
does not load YAML, consult module globals for policy, or accept caller-supplied
request dates.  Downstream land/accept/read paths must call
:func:`verify_calendar_generation_contract` so ``dataclasses.replace`` or field
tampering cannot forge a nominal contract with stale hashes.

Legacy ``target_table`` / ``write_mode`` describe the disabled raw writer only;
they are not publication identity and do not enter config/contract hashes.
Publication topology is the fixed landing/canonical tables from
``calendar_schema``.  Availability is a typed ``axis/rule/at`` object.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from typing import Any, final
from zoneinfo import ZoneInfo

from services.data_sources.calendar_schema import (
    CALENDAR_SCHEMA_HASH,
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    WRITER_ID,
)


_GENERATION_KEYS = frozenset(
    {
        "contract_version",
        "coverage_start",
        "required_through_rule",
        "timezone",
        "availability",
        "canonicalization_version",
    }
)
_AVAILABILITY_KEYS = frozenset({"axis", "rule", "at"})
_SCOPE_KEYS = frozenset(
    {"kind", "venue_field", "venue_ids", "population_label", "method", "unit"}
)
# 2026-08-31 授权换源 (trade_cal: baostock -> calendar_rule, 业主已明确授权; 日历自此
# 不向任何供应商取数, 交易日 = 周一~周五 − 法定节假日, 1990-2026 共 13,162 天逐字段
# 零差异): source/api/method 改成 calendar_rule
# 对应值。target_db/target_table/grain/batch_mode/fixed_params 等表/库名相关字段依设计
# 一律不动 —— 物理表名带 tushare 是历史遗留, 换 adapter 不改表。
_EXPECTED_SCOPE = {
    "kind": "external_aggregate",
    "venue_field": "exchange",
    "venue_ids": ["SSE"],
    "population_label": "sse_trading_calendar",
    "method": "calendar_rule_weekday_minus_holidays",
    "unit": "calendar_day_status",
}
_EXPECTED_PROVIDER_TRANSPORT = {
    "domain": "trade_cal",
    "source": "calendar_rule",
    "api": "query_trade_dates",
    "target_db": "tushare_raw",
    "grain": ["exchange", "cal_date"],
    "batch_mode": "full_refresh",
    "fixed_params": {"exchange": "SSE"},
}
_EXPECTED_LEGACY_COMPATIBILITY = {
    "target_table": "raw_tushare_trade_cal",
    "write_mode": "replace_snapshot",
}
_EXPECTED_AVAILABILITY = {
    "axis": "provider_response",
    "rule": "response_completed",
    "at": "response_completed_at",
}
_EXPECTED_GENERATION = {
    "contract_version": CONTRACT_VERSION,
    "coverage_start": "19901219",
    "required_through_rule": "observed_year_end",
    "timezone": "Asia/Shanghai",
    "canonicalization_version": "1",
}
_EXPECTED_PUBLICATION = {
    "landing_table": LANDING_TABLE,
    "fragment_table": FRAGMENT_TABLE,
    "canonical_table": CANONICAL_TABLE,
    "dataset_id": DATASET_ID,
}


def _hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"trade_cal: {field_name} must be a mapping")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], field_name: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise ValueError(
            f"trade_cal: missing {field_name} keys: {', '.join(missing)}"
        )
    if unknown:
        raise ValueError(
            f"trade_cal: unknown {field_name} keys: {', '.join(unknown)}"
        )


@dataclass(frozen=True)
class CalendarAvailabilityPolicy:
    """Typed publication availability; naked t+1 / string tokens are rejected."""

    axis: str
    rule: str
    at: str

    def payload(self) -> dict[str, str]:
        return {"axis": self.axis, "rule": self.rule, "at": self.at}


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


@final
@dataclass(frozen=True, init=False)
class CalendarGenerationContract:
    """One calendar generation contract bound by :func:`calendar_contract_for_spec` only."""

    domain: str
    dataset_id: str
    contract_version: str
    schema_hash: str
    writer_id: str
    coverage_start: str
    required_through_rule: str
    timezone: str
    availability: CalendarAvailabilityPolicy
    canonicalization_version: str
    source: str
    api: str
    target_db: str
    batch_mode: str
    grain: tuple[str, ...]
    fixed_params: tuple[tuple[str, str], ...]
    page_limit: int
    landing_table: str
    fragment_table: str
    canonical_table: str
    legacy_target_table: str
    legacy_write_mode: str
    population_scope: CalendarPopulationScope
    config_hash: str
    contract_hash: str

    def __new__(cls, *_args, **_kwargs):
        raise TypeError("use calendar_contract_for_spec()")

    @property
    def availability_rule(self) -> str:
        """Compatibility alias for the typed availability rule token."""

        return self.availability.rule

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


def _parse_availability(raw: Any) -> CalendarAvailabilityPolicy:
    if isinstance(raw, str):
        raise ValueError(
            "trade_cal: naked availability_rule string is forbidden; "
            "use typed availability {axis,rule,at}"
        )
    availability = _mapping(raw, "availability")
    _exact_keys(availability, _AVAILABILITY_KEYS, "availability")
    for key, expected in _EXPECTED_AVAILABILITY.items():
        if availability[key] != expected:
            raise ValueError(
                f"trade_cal: availability drift for {key}: "
                f"actual={availability[key]!r} expected={expected!r}"
            )
    return CalendarAvailabilityPolicy(
        axis=str(availability["axis"]),
        rule=str(availability["rule"]),
        at=str(availability["at"]),
    )


def _expected_hashes(
    *,
    page_limit: int,
    generation: Mapping[str, Any],
    availability: CalendarAvailabilityPolicy,
    scope: CalendarPopulationScope,
) -> tuple[str, str]:
    # 2026-09-01: source/api 不参与 config_hash —— 同型手术见
    # nominal_ohlcv_contract.py / stock_st_contract.py 及
    # docs/engineering_governance.md §15.5。它们是传输轴 (从哪取), 不是语义轴
    # (数据是什么); formal_boundaries.py 开篇即 "Transport axis only. Business tiers
    # must not own these seams."。让取数地址参与语义指纹, 会把"换个供应商取同样的日历"
    # 误判成"契约变更", 拒读既有 accepted 分区 (trade_cal 实测 accepted_partition 里
    # 已有 4 个分区因两次换源背出 3 种 config_hash)。
    # 语义变更仍被完整覆盖: domain/target_db/grain/batch_mode/fixed_params/page_limit +
    # publication(landing/fragment/canonical 表名 + dataset_id) +
    # calendar_generation(coverage_start/required_through_rule/timezone/
    # canonicalization_version/availability) + population_scope + schema_hash
    # (经 contract_hash 纳入) —— 任一真实语义变化都会动到其中至少一项。registry 与当前
    # adapter 的 source/api 一致性由本函数调用方 calendar_contract_for_spec() 对
    # _EXPECTED_PROVIDER_TRANSPORT 的逐字段校验独立守卫, 不依赖这个 hash。
    transport_payload = {
        key: value
        for key, value in _EXPECTED_PROVIDER_TRANSPORT.items()
        if key not in ("source", "api")
    }
    transport_payload["page_limit"] = page_limit
    policy_payload = {
        key: generation[key]
        for key in (
            "contract_version",
            "coverage_start",
            "required_through_rule",
            "timezone",
            "canonicalization_version",
        )
    }
    policy_payload["availability"] = availability.payload()
    config_hash = _hash(
        {
            "provider_transport": transport_payload,
            "publication": dict(_EXPECTED_PUBLICATION),
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
    return config_hash, contract_hash


def verify_calendar_generation_contract(
    contract: CalendarGenerationContract,
) -> CalendarGenerationContract:
    """Recompute hashes so forged/replaced/tampered contracts fail closed."""

    if type(contract) is not CalendarGenerationContract:
        raise ValueError("calendar generation contract must be factory-owned")
    if contract.domain != "trade_cal":
        raise ValueError("trade_cal: factory-owned contract domain drift")
    if contract.dataset_id != DATASET_ID:
        raise ValueError("trade_cal: factory-owned contract dataset_id drift")
    if contract.contract_version != CONTRACT_VERSION:
        raise ValueError("trade_cal: factory-owned contract_version drift")
    if contract.schema_hash != CALENDAR_SCHEMA_HASH:
        raise ValueError("trade_cal: factory-owned schema_hash drift")
    if contract.writer_id != WRITER_ID:
        raise ValueError("trade_cal: factory-owned writer_id drift")
    if (
        isinstance(contract.page_limit, bool)
        or not isinstance(contract.page_limit, int)
        or contract.page_limit <= 0
    ):
        raise ValueError("trade_cal: page_limit must be a positive integer")

    generation = {
        "contract_version": contract.contract_version,
        "coverage_start": contract.coverage_start,
        "required_through_rule": contract.required_through_rule,
        "timezone": contract.timezone,
        "canonicalization_version": contract.canonicalization_version,
    }
    for key, expected in _EXPECTED_GENERATION.items():
        if generation[key] != expected:
            raise ValueError(
                f"trade_cal: calendar generation drift: {key} must be {expected}"
            )
    if type(contract.availability) is not CalendarAvailabilityPolicy:
        raise ValueError("trade_cal: availability must be CalendarAvailabilityPolicy")
    if contract.availability.payload() != _EXPECTED_AVAILABILITY:
        raise ValueError("trade_cal: factory-owned availability drift")
    if (
        # 2026-08-31 授权换源 (业主已明确授权): baostock -> calendar_rule。target_db 等表/库名字段不动。
        contract.source != "calendar_rule"
        or contract.api != "query_trade_dates"
        or contract.target_db != "tushare_raw"
        or contract.batch_mode != "full_refresh"
        or contract.grain != ("exchange", "cal_date")
        or contract.fixed_params != (("exchange", "SSE"),)
        or contract.landing_table != LANDING_TABLE
        or contract.fragment_table != FRAGMENT_TABLE
        or contract.canonical_table != CANONICAL_TABLE
    ):
        raise ValueError("trade_cal: factory-owned transport/publication drift")
    if (
        contract.legacy_target_table != _EXPECTED_LEGACY_COMPATIBILITY["target_table"]
        or contract.legacy_write_mode != _EXPECTED_LEGACY_COMPATIBILITY["write_mode"]
    ):
        raise ValueError("trade_cal: factory-owned legacy compatibility drift")
    # Publication identity must never collapse to the disabled legacy raw table.
    if contract.landing_table == contract.legacy_target_table:
        raise ValueError("trade_cal: publication table must not equal legacy raw table")
    scope = contract.population_scope
    if type(scope) is not CalendarPopulationScope:
        raise ValueError("trade_cal: population_scope must be CalendarPopulationScope")
    for key, expected in _EXPECTED_SCOPE.items():
        actual = (
            list(scope.venue_ids) if key == "venue_ids" else getattr(scope, key)
        )
        if actual != expected:
            raise ValueError(
                f"trade_cal: calendar population drift for {key}: "
                f"actual={actual!r} expected={expected!r}"
            )

    config_hash, contract_hash = _expected_hashes(
        page_limit=contract.page_limit,
        generation=generation,
        availability=contract.availability,
        scope=scope,
    )
    if contract.config_hash != config_hash:
        raise ValueError("trade_cal: config_hash does not match factory-owned payload")
    if contract.contract_hash != contract_hash:
        raise ValueError(
            "trade_cal: contract_hash does not match factory-owned payload"
        )
    return contract


def calendar_contract_for_spec(spec: Mapping[str, Any]) -> CalendarGenerationContract:
    """Derive a frozen calendar contract from one caller-owned registry snapshot."""

    spec = _mapping(spec, "domain spec")
    for key, expected in _EXPECTED_PROVIDER_TRANSPORT.items():
        if key not in spec:
            raise ValueError(f"trade_cal: missing calendar transport field: {key}")
        if spec[key] != expected:
            raise ValueError(
                f"trade_cal: calendar transport drift for {key}: "
                f"actual={spec[key]!r} expected={expected!r}"
            )
    for key, expected in _EXPECTED_LEGACY_COMPATIBILITY.items():
        if key not in spec:
            raise ValueError(
                f"trade_cal: missing legacy compatibility field: {key}"
            )
        if spec[key] != expected:
            raise ValueError(
                f"trade_cal: legacy compatibility drift for {key}: "
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
    if "availability_rule" in generation:
        raise ValueError(
            "trade_cal: naked availability_rule is forbidden; "
            "use typed availability {axis,rule,at}"
        )
    availability = _parse_availability(generation["availability"])

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
    config_hash, contract_hash = _expected_hashes(
        page_limit=page_limit,
        generation=generation,
        availability=availability,
        scope=scope,
    )
    bound = object.__new__(CalendarGenerationContract)
    for name, value in (
        ("domain", "trade_cal"),
        ("dataset_id", DATASET_ID),
        ("contract_version", CONTRACT_VERSION),
        ("schema_hash", CALENDAR_SCHEMA_HASH),
        ("writer_id", WRITER_ID),
        ("coverage_start", str(generation["coverage_start"])),
        ("required_through_rule", str(generation["required_through_rule"])),
        ("timezone", str(generation["timezone"])),
        ("availability", availability),
        ("canonicalization_version", str(generation["canonicalization_version"])),
        # 2026-08-31 授权换源 (业主已明确授权): baostock -> calendar_rule。target_db 等表/库名字段不动。
        ("source", "calendar_rule"),
        ("api", "query_trade_dates"),
        ("target_db", "tushare_raw"),
        ("batch_mode", "full_refresh"),
        ("grain", ("exchange", "cal_date")),
        ("fixed_params", (("exchange", "SSE"),)),
        ("page_limit", page_limit),
        ("landing_table", LANDING_TABLE),
        ("fragment_table", FRAGMENT_TABLE),
        ("canonical_table", CANONICAL_TABLE),
        ("legacy_target_table", str(spec["target_table"])),
        ("legacy_write_mode", str(spec["write_mode"])),
        ("population_scope", scope),
        ("config_hash", config_hash),
        ("contract_hash", contract_hash),
    ):
        object.__setattr__(bound, name, value)
    return verify_calendar_generation_contract(bound)


__all__ = [
    "CalendarAvailabilityPolicy",
    "CalendarGenerationContract",
    "CalendarPopulationScope",
    "calendar_contract_for_spec",
    "verify_calendar_generation_contract",
]
