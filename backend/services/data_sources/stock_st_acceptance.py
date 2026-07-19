"""Accepted land→accept boundary for same-day ST membership partitions."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.data_sources.security_day_partition import (
    SecurityDayAcceptanceOutcome,
    SecurityDayError,
    SecurityDayLandingBatch,
    accept_security_day_batch,
    ensure_security_day_schema,
    land_security_day_batch,
)
from services.data_sources.stock_st_contract import (
    StockStContract,
    load_stock_st_contract,
    verify_stock_st_contract,
)
from services.data_sources.stock_st_schema import DOMAIN

StockStLandingBatch = SecurityDayLandingBatch
StockStAcceptanceOutcome = SecurityDayAcceptanceOutcome
StockStAcceptanceError = SecurityDayError


def ensure_stock_st_acceptance_schema(conn) -> None:
    ensure_security_day_schema(conn, DOMAIN)


def land_stock_st_batch(
    conn,
    batch: SecurityDayLandingBatch,
    contract: StockStContract | None = None,
    *,
    after_step: Callable[[str], None] | None = None,
) -> str:
    contract = verify_stock_st_contract(contract or load_stock_st_contract())
    return land_security_day_batch(
        conn,
        DOMAIN,
        batch,
        contract_hash=contract.contract_hash,
        config_hash=contract.config_hash,
        after_step=after_step,
    )


def accept_stock_st_batch(
    conn,
    batch_id: str,
    contract: StockStContract | None = None,
    *,
    after_step: Callable[[str], None] | None = None,
) -> SecurityDayAcceptanceOutcome:
    contract = verify_stock_st_contract(contract or load_stock_st_contract())
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
    "StockStAcceptanceError",
    "StockStAcceptanceOutcome",
    "StockStLandingBatch",
    "accept_stock_st_batch",
    "ensure_stock_st_acceptance_schema",
    "land_stock_st_batch",
    "runtime_surface",
]
