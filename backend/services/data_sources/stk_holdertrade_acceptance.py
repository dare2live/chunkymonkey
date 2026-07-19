"""E0 formal land→validate→accept for tushare stk_holdertrade.

Requires disclosure execution handoff before any write.  Legacy
``raw_tushare_stk_holdertrade`` direct writes remain NONCONFORMING strangler
until cutover; this module never publishes DatasetSnapshot readiness.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from services.data_sources.disclosure_event_partition import (
    DisclosureEventAcceptanceOutcome,
    DisclosureEventDomain,
    DisclosureEventError,
    DisclosureEventLandingBatch,
    DisclosureEventValidationError,
    accept_disclosure_event_batch,
    ensure_disclosure_event_schema,
    land_disclosure_event_batch,
    partition_yyyymmdd,
    require_disclosure_handoff,
)
from services.data_sources.stk_holdertrade_contract import (
    StkHoldertradeContract,
    load_stk_holdertrade_contract,
    verify_stk_holdertrade_contract,
)
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE,
    CONTRACT_VERSION,
    DATASET_ID,
    GRAIN,
    LANDING_TABLE,
    PARTITION_FIELD,
    PROVIDER_FIELDS,
    SCHEMA_CONTRACT,
    SOURCE,
    WRITER_ID,
)

StkHoldertradeLandingBatch = DisclosureEventLandingBatch
StkHoldertradeAcceptanceOutcome = DisclosureEventAcceptanceOutcome


class StkHoldertradeAcceptanceError(DisclosureEventError):
    """stk_holdertrade formal acceptance cannot proceed safely."""


class StkHoldertradeValidationError(DisclosureEventValidationError):
    pass


def _validate_provider_row(row: Mapping[str, Any], partition: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise StkHoldertradeValidationError(
            "INVALID_ROW", "provider row must be a mapping"
        )
    missing = [name for name in PROVIDER_FIELDS if name not in row]
    if missing:
        raise StkHoldertradeValidationError(
            "MISSING_FIELDS", f"missing provider fields: {missing}"
        )
    ann = row.get("ann_date")
    if ann is None or str(ann).strip() == "":
        raise StkHoldertradeValidationError(
            "MISSING_ANN_DATE", "ann_date is required for availability axis"
        )
    ann_compact = partition_yyyymmdd(ann, field="ann_date")
    if ann_compact != partition:
        raise StkHoldertradeValidationError(
            "PARTITION_MISMATCH",
            f"row ann_date={ann_compact} partition={partition}",
        )
    ts_code = str(row.get("ts_code") or "").strip()
    if not ts_code:
        raise StkHoldertradeValidationError("INVALID_TS_CODE", "ts_code required")
    holder_name = str(row.get("holder_name") or "").strip()
    if not holder_name:
        raise StkHoldertradeValidationError("EMPTY_TEXT", "holder_name cannot be empty")
    in_de = str(row.get("in_de") or "").strip().upper()
    if in_de not in {"IN", "DE"}:
        raise StkHoldertradeValidationError(
            "INVALID_IN_DE", f"in_de must be IN/DE, got={in_de!r}"
        )
    holder_type = row.get("holder_type")
    if holder_type is not None:
        holder_type = str(holder_type).strip() or None

    def _opt_float(value: Any, field: str) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise StkHoldertradeValidationError(
                "INVALID_NUMERIC", f"{field}={value!r}"
            ) from exc

    return {
        "ts_code": ts_code,
        "ann_date": ann_compact,
        "holder_name": holder_name,
        "in_de": in_de,
        "holder_type": holder_type,
        "change_vol": _opt_float(row.get("change_vol"), "change_vol"),
        "change_ratio": _opt_float(row.get("change_ratio"), "change_ratio"),
        "after_share": _opt_float(row.get("after_share"), "after_share"),
        "after_ratio": _opt_float(row.get("after_ratio"), "after_ratio"),
        "avg_price": _opt_float(row.get("avg_price"), "avg_price"),
        "total_share": _opt_float(row.get("total_share"), "total_share"),
    }


DOMAIN = DisclosureEventDomain(
    domain="stk_holdertrade",
    dataset_id=DATASET_ID,
    landing_table=LANDING_TABLE,
    canonical_table=CANONICAL_TABLE,
    source=SOURCE,
    writer_id=WRITER_ID,
    partition_field=PARTITION_FIELD,
    grain=GRAIN,
    content_hash_fields=GRAIN
    + (
        "holder_type",
        "change_vol",
        "change_ratio",
        "after_share",
        "after_ratio",
        "avg_price",
        "total_share",
    ),
    schema_contract=SCHEMA_CONTRACT,
    validate_provider_row=_validate_provider_row,
)


def ensure_stk_holdertrade_acceptance_schema(conn) -> None:
    ensure_disclosure_event_schema(
        conn, DOMAIN, error_type=StkHoldertradeAcceptanceError
    )


def land_stk_holdertrade_batch(
    conn,
    batch: StkHoldertradeLandingBatch,
    contract: StkHoldertradeContract,
    *,
    handoff: StkHoldertradeContract | None = None,
    after_step: Callable[[str], None] | None = None,
) -> str:
    contract = require_disclosure_handoff(
        domain="stk_holdertrade",
        contract=contract,
        handoff=handoff,
        verify=verify_stk_holdertrade_contract,
        error_type=StkHoldertradeAcceptanceError,
    )
    return land_disclosure_event_batch(
        conn,
        DOMAIN,
        batch,
        contract_version=contract.contract_version,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
        error_type=StkHoldertradeAcceptanceError,
    )


def accept_stk_holdertrade_batch(
    conn,
    batch_id: str,
    contract: StkHoldertradeContract,
    *,
    handoff: StkHoldertradeContract | None = None,
    after_step: Callable[[str], None] | None = None,
) -> StkHoldertradeAcceptanceOutcome:
    contract = require_disclosure_handoff(
        domain="stk_holdertrade",
        contract=contract,
        handoff=handoff,
        verify=verify_stk_holdertrade_contract,
        error_type=StkHoldertradeAcceptanceError,
    )
    return accept_disclosure_event_batch(
        conn,
        DOMAIN,
        batch_id,
        contract_version=contract.contract_version,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
        error_type=StkHoldertradeAcceptanceError,
    )


def publish_accepted_stk_holdertrade_partition(
    conn,
    batch: StkHoldertradeLandingBatch,
    contract: StkHoldertradeContract | None = None,
) -> StkHoldertradeAcceptanceOutcome:
    from services.data_sources.formal_execution import (
        propagate_disclosure_execution_contract,
    )

    contract = verify_stk_holdertrade_contract(
        contract or load_stk_holdertrade_contract()
    )
    handed = propagate_disclosure_execution_contract("stk_holdertrade", contract)
    land_stk_holdertrade_batch(conn, batch, handed, handoff=handed)
    return accept_stk_holdertrade_batch(conn, batch.batch_id, handed, handoff=handed)


def runtime_surface() -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        "writer_id": WRITER_ID,
        "production_write": "formal_only",
        "legacy_direct_write": "nonconforming_escape_hatch",
        "dataset_snapshot": "blocked_until_e0_cutover",
        "provider_sync": "fixture_or_authorized_manual_only",
        "contract_version": CONTRACT_VERSION,
    }


__all__ = [
    "StkHoldertradeAcceptanceError",
    "StkHoldertradeAcceptanceOutcome",
    "StkHoldertradeLandingBatch",
    "StkHoldertradeValidationError",
    "accept_stk_holdertrade_batch",
    "ensure_stk_holdertrade_acceptance_schema",
    "land_stk_holdertrade_batch",
    "publish_accepted_stk_holdertrade_partition",
    "runtime_surface",
]
