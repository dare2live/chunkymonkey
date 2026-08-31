"""Factory-owned immutable contract for nominal daily OHLCV partitions."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, final

from services.data_sources.availability import AvailabilityPolicy
from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    DOMAIN,
    LANDING_TABLE,
    SCHEMA_HASH,
    SCHEMA_ID,
    WRITER_ID,
)


def _hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


@final
@dataclass(frozen=True, slots=True)
class NominalOhlcvContract:
    domain: str
    dataset_id: str
    contract_version: str
    schema_id: str
    schema_hash: str
    writer_id: str
    landing_table: str
    canonical_table: str
    coverage_start: str
    source: str
    api: str
    target_db: str
    compatibility_table: str
    grain: tuple[str, ...]
    partition_by: str
    availability: AvailabilityPolicy
    population_kind: str
    population_label: str
    population_usage: str
    config_hash: str
    contract_hash: str


def _expected_transport(spec: Mapping[str, Any]) -> None:
    expected = {
        "source": DOMAIN.source,
        "api": DOMAIN.api,
        "target_db": "tushare_raw",
        "grain": list(DOMAIN.grain),
        "batch_mode": "by_trade_date",
    }
    for key, value in expected.items():
        actual = spec.get(key)
        if key == "grain":
            actual = list(actual or [])
        if actual != value:
            raise ValueError(f"daily: transport {key} drift actual={actual!r}")
    if str(spec.get("target_table") or "") != DOMAIN.compatibility_table:
        raise ValueError("daily: legacy target_table drift")
    if str(spec.get("available_after") or "") != DOMAIN.available_after_legacy:
        raise ValueError("daily: legacy available_after conflicts with typed policy")


def nominal_ohlcv_contract_for_spec(spec: Mapping[str, Any]) -> NominalOhlcvContract:
    if not isinstance(spec, Mapping):
        raise ValueError("daily: registry spec must be a mapping")
    _expected_transport(spec)
    raw_scope = spec.get("population_scope")
    if not isinstance(raw_scope, Mapping):
        raise ValueError("daily: missing population_scope")
    if (
        raw_scope.get("kind") != DOMAIN.population_kind
        or raw_scope.get("population_label") != DOMAIN.population_label
        or raw_scope.get("usage") != DOMAIN.population_usage
    ):
        raise ValueError("daily: population_scope must be raw_evidence/provider_response")
    raw_avail = spec.get("availability_policy")
    if not isinstance(raw_avail, Mapping):
        raise ValueError("daily: missing availability_policy")
    availability = DOMAIN.availability_policy
    if (
        raw_avail.get("axis") != availability.axis
        or raw_avail.get("rule") != availability.rule
        or raw_avail.get("at") != availability.at.strftime("%H:%M")
    ):
        raise ValueError("daily: availability_policy drift")
    security_day = spec.get("security_day_partition")
    if not isinstance(security_day, Mapping):
        raise ValueError("daily: missing security_day_partition")
    if (
        str(security_day.get("contract_version") or "") != CONTRACT_VERSION
        or str(security_day.get("coverage_start") or "") != DOMAIN.coverage_start
        or str(security_day.get("schema_hash") or "") != SCHEMA_HASH
    ):
        raise ValueError("daily: security_day_partition drift")

    config_payload = {
        "dataset_id": DATASET_ID,
        "schema_id": SCHEMA_ID,
        "schema_hash": SCHEMA_HASH,
        "writer_id": WRITER_ID,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        "coverage_start": DOMAIN.coverage_start,
        "availability": availability.payload(),
        "population_scope": {
            "kind": DOMAIN.population_kind,
            "population_label": DOMAIN.population_label,
            "usage": DOMAIN.population_usage,
        },
        "grain": list(DOMAIN.grain),
        "partition_by": DOMAIN.partition_field,
        # 2026-09-01: **source / api 不参与 config_hash**。
        # 它们是传输轴 (从哪取), 不是语义轴 (数据是什么)。本模块开篇的 formal_boundaries
        # 契约就写着 "Transport axis only" —— 让取数地址参与语义指纹, 会把"换个供应商取
        # 同样的 OHLCV"误判成"契约变更", 进而让 security_day_reader 的严格 hash 相等校验
        # 拒读全部既有 accepted 分区 (实测 daily 1,858 个 + stock_st 1,128 个)。
        # 语义变更仍被完整覆盖: schema_hash(字段/类型/单位) + grain + partition_by +
        # population_scope + availability + 表名 + coverage_start, 任一真实语义变化都会动到
        # 其中至少一项。registry 与 DOMAIN 的 source 一致性由 _expected_transport 独立守卫,
        # 不依赖 hash。
    }
    contract_payload = {
        **config_payload,
        "contract_version": CONTRACT_VERSION,
    }
    return NominalOhlcvContract(
        domain="daily",
        dataset_id=DATASET_ID,
        contract_version=CONTRACT_VERSION,
        schema_id=SCHEMA_ID,
        schema_hash=SCHEMA_HASH,
        writer_id=WRITER_ID,
        landing_table=LANDING_TABLE,
        canonical_table=CANONICAL_TABLE,
        coverage_start=DOMAIN.coverage_start,
        source=DOMAIN.source,
        api=DOMAIN.api,
        target_db=DOMAIN.target_db,
        compatibility_table=DOMAIN.compatibility_table,
        grain=DOMAIN.grain,
        partition_by=DOMAIN.partition_field,
        availability=availability,
        population_kind=DOMAIN.population_kind,
        population_label=DOMAIN.population_label,
        population_usage=DOMAIN.population_usage,
        config_hash=_hash(config_payload),
        contract_hash=_hash(contract_payload),
    )


def load_nominal_ohlcv_contract() -> NominalOhlcvContract:
    from services.data_sources.sync_runner import load_registry

    registry = load_registry()
    spec = dict(registry["domains"]["daily"])
    spec["domain"] = "daily"
    if "target_db" not in spec:
        spec["target_db"] = "tushare_raw"
    return nominal_ohlcv_contract_for_spec(spec)


def verify_nominal_ohlcv_contract(contract: NominalOhlcvContract) -> NominalOhlcvContract:
    fresh = load_nominal_ohlcv_contract()
    if (
        contract.contract_hash != fresh.contract_hash
        or contract.config_hash != fresh.config_hash
    ):
        raise ValueError("daily: NominalOhlcvContract hashes drifted from factory")
    return contract


__all__ = [
    "NominalOhlcvContract",
    "load_nominal_ohlcv_contract",
    "nominal_ohlcv_contract_for_spec",
    "verify_nominal_ohlcv_contract",
]
