"""Public publication boundary for accepted same-day ST membership."""
from __future__ import annotations

from services.data_sources.security_day_partition import SecurityDayLandingBatch
from services.data_sources.stock_st_acceptance import (
    StockStAcceptanceOutcome,
    accept_stock_st_batch,
    ensure_stock_st_acceptance_schema,
    land_stock_st_batch,
)
from services.data_sources.stock_st_contract import (
    StockStContract,
    verify_stock_st_contract,
)


class StockStRuntimeError(RuntimeError):
    """Accepted stock_st runtime refused an unsafe request."""


def bootstrap_stock_st_acceptance_schema(conn) -> None:
    ensure_stock_st_acceptance_schema(conn)


def publish_accepted_stock_st_partition(
    conn,
    batch: SecurityDayLandingBatch,
    contract: StockStContract,
    *,
    bootstrap: bool = False,
) -> StockStAcceptanceOutcome:
    contract = verify_stock_st_contract(contract)
    if bootstrap:
        bootstrap_stock_st_acceptance_schema(conn)
    land_stock_st_batch(conn, batch, contract)
    return accept_stock_st_batch(conn, str(batch.batch_id), contract)


__all__ = [
    "StockStRuntimeError",
    "bootstrap_stock_st_acceptance_schema",
    "publish_accepted_stock_st_partition",
]
