"""Public publication boundary for accepted nominal OHLCV partitions."""
from __future__ import annotations

from services.data_sources.nominal_ohlcv_acceptance import (
    NominalOhlcvAcceptanceOutcome,
    accept_nominal_ohlcv_batch,
    ensure_nominal_ohlcv_acceptance_schema,
    land_nominal_ohlcv_batch,
)
from services.data_sources.nominal_ohlcv_contract import (
    NominalOhlcvContract,
    verify_nominal_ohlcv_contract,
)
from services.data_sources.security_day_partition import SecurityDayLandingBatch


class NominalOhlcvRuntimeError(RuntimeError):
    """Accepted nominal OHLCV runtime refused an unsafe request."""


def bootstrap_nominal_ohlcv_acceptance_schema(conn) -> None:
    ensure_nominal_ohlcv_acceptance_schema(conn)


def publish_accepted_nominal_ohlcv_partition(
    conn,
    batch: SecurityDayLandingBatch,
    contract: NominalOhlcvContract,
    *,
    bootstrap: bool = False,
) -> NominalOhlcvAcceptanceOutcome:
    contract = verify_nominal_ohlcv_contract(contract)
    if bootstrap:
        bootstrap_nominal_ohlcv_acceptance_schema(conn)
    land_nominal_ohlcv_batch(conn, batch, contract)
    return accept_nominal_ohlcv_batch(conn, str(batch.batch_id), contract)


__all__ = [
    "NominalOhlcvRuntimeError",
    "bootstrap_nominal_ohlcv_acceptance_schema",
    "publish_accepted_nominal_ohlcv_partition",
]
