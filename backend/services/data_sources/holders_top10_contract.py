"""Factory-owned immutable contract for holders_top10 formal land→accept."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, final

from services.data_sources.holders_top10_schema import (
    API,
    CANONICAL_TABLE,
    COMPATIBILITY_RETIRED,
    COMPATIBILITY_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    GRAIN,
    LANDING_TABLE,
    PARTITION_FIELD,
    SCHEMA_HASH,
    SCHEMA_ID,
    SOURCE,
    WRITER_ID,
)


def _hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


@final
@dataclass(frozen=True, slots=True)
class HoldersTop10Contract:
    domain: str
    dataset_id: str
    contract_version: str
    schema_id: str
    schema_hash: str
    writer_id: str
    landing_table: str
    canonical_table: str
    source: str
    api: str
    compatibility_table: str
    compatibility_retired: bool
    grain: tuple[str, ...]
    partition_by: str
    availability_axis: str
    availability_rule: str
    population_kind: str
    config_hash: str
    contract_hash: str


def load_holders_top10_contract() -> HoldersTop10Contract:
    """Schema-owned factory — not loaded from TuShare sync_registry."""

    config_payload = {
        "dataset_id": DATASET_ID,
        "schema_id": SCHEMA_ID,
        "schema_hash": SCHEMA_HASH,
        "writer_id": WRITER_ID,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        # 2026-09-01: **source / api 不参与 config_hash**。
        # 它们是传输轴 (从哪取), 不是语义轴 (数据是什么)。同一手术已在 nominal_ohlcv_contract.py /
        # stock_st_contract.py 做过 (git log --grep source_api_transport_axis) —— 让取数地址参与语义
        # 指纹, 会把"换个供应商取同样的数据"误判成"契约变更", 让 accept 路径的严格 hash 相等校验
        # 拒读全部既有 accepted 分区。语义变更仍被完整覆盖: schema_hash(字段/类型/单位) + grain +
        # partition_by + population_scope + availability + landing_table/canonical_table/
        # compatibility_table/compatibility_retired, 任一真实语义变化都会动到其中至少一项。
        # source/api 仍是 HoldersTop10Contract 的字段 (值来自 schema 常量), 不依赖 hash。
        "compatibility_table": COMPATIBILITY_TABLE,
        "compatibility_retired": COMPATIBILITY_RETIRED,
        "grain": list(GRAIN),
        "partition_by": PARTITION_FIELD,
        "availability": {
            "axis": "notice_date",
            "rule": "event_time_notice_or_page_update",
        },
        "population_scope": {
            "kind": "raw_evidence",
            "population_label": "provider_response",
            "usage": "evidence_only",
        },
    }
    contract_payload = {
        **config_payload,
        "contract_version": CONTRACT_VERSION,
    }
    return HoldersTop10Contract(
        domain="holders_top10",
        dataset_id=DATASET_ID,
        contract_version=CONTRACT_VERSION,
        schema_id=SCHEMA_ID,
        schema_hash=SCHEMA_HASH,
        writer_id=WRITER_ID,
        landing_table=LANDING_TABLE,
        canonical_table=CANONICAL_TABLE,
        source=SOURCE,
        api=API,
        compatibility_table=COMPATIBILITY_TABLE,
        compatibility_retired=COMPATIBILITY_RETIRED,
        grain=GRAIN,
        partition_by=PARTITION_FIELD,
        availability_axis="notice_date",
        availability_rule="event_time_notice_or_page_update",
        population_kind="raw_evidence",
        config_hash=_hash(config_payload),
        contract_hash=_hash(contract_payload),
    )


def verify_holders_top10_contract(contract: HoldersTop10Contract) -> HoldersTop10Contract:
    fresh = load_holders_top10_contract()
    if (
        contract.contract_hash != fresh.contract_hash
        or contract.config_hash != fresh.config_hash
        or contract.schema_hash != fresh.schema_hash
    ):
        raise ValueError("holders_top10: HoldersTop10Contract hashes drifted from factory")
    return contract


__all__ = [
    "HoldersTop10Contract",
    "load_holders_top10_contract",
    "verify_holders_top10_contract",
]
