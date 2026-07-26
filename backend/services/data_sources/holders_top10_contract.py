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
        "source": SOURCE,
        "api": API,
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
