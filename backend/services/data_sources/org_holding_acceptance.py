"""E0 formal land→validate→accept for miaoxiang org_holding.

Requires disclosure execution handoff before any write.  Legacy
``raw_org_holding_aif10`` direct writes remain NONCONFORMING strangler until
cutover; this module never publishes DatasetSnapshot readiness.
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
from services.data_sources.org_holding_contract import (
    OrgHoldingContract,
    load_org_holding_contract,
    verify_org_holding_contract,
)
from services.data_sources.org_holding_schema import (
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

OrgHoldingLandingBatch = DisclosureEventLandingBatch
OrgHoldingAcceptanceOutcome = DisclosureEventAcceptanceOutcome


class OrgHoldingAcceptanceError(DisclosureEventError):
    """org_holding formal acceptance cannot proceed safely."""


class OrgHoldingValidationError(DisclosureEventValidationError):
    pass


def _validate_provider_row(row: Mapping[str, Any], partition: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise OrgHoldingValidationError("INVALID_ROW", "provider row must be a mapping")
    missing = [name for name in PROVIDER_FIELDS if name not in row]
    if missing:
        raise OrgHoldingValidationError(
            "MISSING_FIELDS", f"missing provider fields: {missing}"
        )
    available = row.get("available_date")
    if available is None or str(available).strip() == "":
        raise OrgHoldingValidationError(
            "MISSING_AVAILABLE_DATE",
            "available_date is required for availability axis",
        )
    available_compact = partition_yyyymmdd(available, field="available_date")
    if available_compact != partition:
        raise OrgHoldingValidationError(
            "PARTITION_MISMATCH",
            f"row available_date={available_compact} partition={partition}",
        )
    report = partition_yyyymmdd(row.get("report_date"), field="report_date")
    if available_compact < report:
        raise OrgHoldingValidationError(
            "AVAILABLE_BEFORE_REPORT",
            f"available_date={available_compact} < report_date={report}",
        )
    stock = str(row.get("stock_code") or "").strip()
    if not stock:
        raise OrgHoldingValidationError("INVALID_STOCK", "stock_code required")
    holder_code = str(row.get("holder_code") or "").strip()
    if not holder_code:
        raise OrgHoldingValidationError("INVALID_HOLDER", "holder_code required")
    fund_derive = row.get("fund_derivecode")
    if fund_derive is None:
        fund_derive = ""
    else:
        fund_derive = str(fund_derive).strip()
    holder_name = row.get("holder_name")
    if holder_name is not None:
        holder_name = str(holder_name).strip() or None
    org_type_name = row.get("org_type_name")
    if org_type_name is not None:
        org_type_name = str(org_type_name).strip() or None

    def _opt_float(value: Any, field: str) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise OrgHoldingValidationError(
                "INVALID_NUMERIC", f"{field}={value!r}"
            ) from exc

    return {
        "report_date": report,
        "available_date": available_compact,
        "stock_code": stock,
        "holder_code": holder_code,
        "fund_derivecode": fund_derive,
        "holder_name": holder_name,
        "org_type_name": org_type_name,
        "total_shares": _opt_float(row.get("total_shares"), "total_shares"),
        "free_shares_ratio": _opt_float(
            row.get("free_shares_ratio"), "free_shares_ratio"
        ),
    }


DOMAIN = DisclosureEventDomain(
    domain="org_holding",
    dataset_id=DATASET_ID,
    landing_table=LANDING_TABLE,
    canonical_table=CANONICAL_TABLE,
    source=SOURCE,
    writer_id=WRITER_ID,
    partition_field=PARTITION_FIELD,
    grain=GRAIN,
    content_hash_fields=GRAIN
    + ("holder_name", "org_type_name", "total_shares", "free_shares_ratio"),
    schema_contract=SCHEMA_CONTRACT,
    validate_provider_row=_validate_provider_row,
    canonical_delete_scope="report_dates_in_batch",
)


def ensure_org_holding_acceptance_schema(conn) -> None:
    ensure_disclosure_event_schema(
        conn, DOMAIN, error_type=OrgHoldingAcceptanceError
    )


def land_org_holding_batch(
    conn,
    batch: OrgHoldingLandingBatch,
    contract: OrgHoldingContract,
    *,
    handoff: OrgHoldingContract | None = None,
    after_step: Callable[[str], None] | None = None,
) -> str:
    contract = require_disclosure_handoff(
        domain="org_holding",
        contract=contract,
        handoff=handoff,
        verify=verify_org_holding_contract,
        error_type=OrgHoldingAcceptanceError,
    )
    return land_disclosure_event_batch(
        conn,
        DOMAIN,
        batch,
        contract_version=contract.contract_version,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
        error_type=OrgHoldingAcceptanceError,
    )


def accept_org_holding_batch(
    conn,
    batch_id: str,
    contract: OrgHoldingContract,
    *,
    handoff: OrgHoldingContract | None = None,
    after_step: Callable[[str], None] | None = None,
    merge_grains: bool = False,
) -> OrgHoldingAcceptanceOutcome:
    contract = require_disclosure_handoff(
        domain="org_holding",
        contract=contract,
        handoff=handoff,
        verify=verify_org_holding_contract,
        error_type=OrgHoldingAcceptanceError,
    )
    return accept_disclosure_event_batch(
        conn,
        DOMAIN,
        batch_id,
        contract_version=contract.contract_version,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
        error_type=OrgHoldingAcceptanceError,
        merge_grains=merge_grains,
    )


def publish_accepted_org_holding_partition(
    conn,
    batch: OrgHoldingLandingBatch,
    contract: OrgHoldingContract | None = None,
) -> OrgHoldingAcceptanceOutcome:
    """Deprecated fused helper: thin alias to caller-only S1→S2 transport."""

    from services.data_sources.disclosure_transport import (
        land_then_accept_disclosure_partition,
    )

    _ = verify_org_holding_contract(contract or load_org_holding_contract())
    return land_then_accept_disclosure_partition(
        "org_holding",
        conn,
        partition=str(batch.partition_value),
        rows=list(batch.rows),
        observed_at=batch.observed_at,
        available_at=batch.available_at,
        batch_id=str(batch.batch_id),
        request=dict(batch.request),
    )


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
    "OrgHoldingAcceptanceError",
    "OrgHoldingAcceptanceOutcome",
    "OrgHoldingLandingBatch",
    "OrgHoldingValidationError",
    "accept_org_holding_batch",
    "ensure_org_holding_acceptance_schema",
    "land_org_holding_batch",
    "publish_accepted_org_holding_partition",
    "runtime_surface",
]
