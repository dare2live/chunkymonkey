"""Factory-owned immutable contract for stk_holdertrade formal land→accept."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, final

from services.data_sources.stk_holdertrade_schema import (
    API,
    CANONICAL_TABLE,
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
class StkHoldertradeContract:
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
    grain: tuple[str, ...]
    partition_by: str
    availability_axis: str
    availability_rule: str
    population_kind: str
    config_hash: str
    contract_hash: str


def load_stk_holdertrade_contract() -> StkHoldertradeContract:
    """Schema-owned factory — registry grain/api mirrored; not a sync_runner path."""

    config_payload = {
        "dataset_id": DATASET_ID,
        "schema_id": SCHEMA_ID,
        "schema_hash": SCHEMA_HASH,
        "writer_id": WRITER_ID,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        "source": SOURCE,
        "api": API,
        "compatibility_table": COMPATIBILITY_TABLE,
        "grain": list(GRAIN),
        "partition_by": PARTITION_FIELD,
        "availability": {
            "axis": "ann_date",
            "rule": "announcement_date_event_time",
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
    return StkHoldertradeContract(
        domain="stk_holdertrade",
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
        grain=GRAIN,
        partition_by=PARTITION_FIELD,
        availability_axis="ann_date",
        availability_rule="announcement_date_event_time",
        population_kind="raw_evidence",
        config_hash=_hash(config_payload),
        contract_hash=_hash(contract_payload),
    )


def verify_stk_holdertrade_contract(
    contract: StkHoldertradeContract,
) -> StkHoldertradeContract:
    fresh = load_stk_holdertrade_contract()
    if (
        contract.contract_hash != fresh.contract_hash
        or contract.config_hash != fresh.config_hash
        or contract.schema_hash != fresh.schema_hash
    ):
        raise ValueError(
            "stk_holdertrade: StkHoldertradeContract hashes drifted from factory"
        )
    return contract


__all__ = [
    "StkHoldertradeContract",
    "load_stk_holdertrade_contract",
    "verify_stk_holdertrade_contract",
]
