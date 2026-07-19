"""Accepted land→accept boundary for nominal daily OHLCV partitions."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.data_sources.nominal_ohlcv_contract import (
    NominalOhlcvContract,
    load_nominal_ohlcv_contract,
    verify_nominal_ohlcv_contract,
)
from services.data_sources.nominal_ohlcv_schema import DOMAIN
from services.data_sources.security_day_partition import (
    SecurityDayAcceptanceOutcome,
    SecurityDayError,
    SecurityDayLandingBatch,
    accept_security_day_batch,
    ensure_security_day_schema,
    land_security_day_batch,
)

NominalOhlcvLandingBatch = SecurityDayLandingBatch
NominalOhlcvAcceptanceOutcome = SecurityDayAcceptanceOutcome
NominalOhlcvAcceptanceError = SecurityDayError


def ensure_nominal_ohlcv_acceptance_schema(conn) -> None:
    ensure_security_day_schema(conn, DOMAIN)


def land_nominal_ohlcv_batch(
    conn,
    batch: SecurityDayLandingBatch,
    contract: NominalOhlcvContract | None = None,
    *,
    after_step: Callable[[str], None] | None = None,
) -> str:
    contract = verify_nominal_ohlcv_contract(contract or load_nominal_ohlcv_contract())
    return land_security_day_batch(
        conn,
        DOMAIN,
        batch,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
    )


def accept_nominal_ohlcv_batch(
    conn,
    batch_id: str,
    contract: NominalOhlcvContract | None = None,
    *,
    after_step: Callable[[str], None] | None = None,
) -> SecurityDayAcceptanceOutcome:
    contract = verify_nominal_ohlcv_contract(contract or load_nominal_ohlcv_contract())
    return accept_security_day_batch(
        conn,
        DOMAIN,
        batch_id,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
    )


def runtime_surface() -> dict[str, Any]:
    return {
        "dataset_id": DOMAIN.dataset_id,
        "landing_table": DOMAIN.landing_table,
        "canonical_table": DOMAIN.canonical_table,
        "writer_id": DOMAIN.writer_id,
        "legacy_raw_write": "forbidden",
        "provider_sync": "disabled_until_authorized_canary",
    }


__all__ = [
    "NominalOhlcvAcceptanceError",
    "NominalOhlcvAcceptanceOutcome",
    "NominalOhlcvLandingBatch",
    "accept_nominal_ohlcv_batch",
    "ensure_nominal_ohlcv_acceptance_schema",
    "land_nominal_ohlcv_batch",
    "runtime_surface",
]
