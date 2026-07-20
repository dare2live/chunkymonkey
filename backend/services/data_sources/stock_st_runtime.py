"""Public publication boundary for accepted same-day ST membership.

Transport strangler surfaces (independent):
- :func:`capture_and_land_authorized_stock_st_partition` — S1 land-only
- :func:`accept_stock_st_from_landing` — S2 accept-from-landing (zero fetch)
- :func:`capture_and_publish_authorized_stock_st_partition` — legacy fused path

It never writes ``raw_tushare_stock_st``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from services.data_sources.security_day_capture import (
    capture_security_day_provider_rows,
)
from services.data_sources.security_day_partition import (
    SecurityDayError,
    SecurityDayLandingBatch,
)
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
from services.data_sources.stock_st_schema import (
    CANONICAL_TABLE,
    DOMAIN,
    LANDING_TABLE,
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


def capture_and_land_authorized_stock_st_partition(
    conn,
    contract: StockStContract,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime | None = None,
    bootstrap: bool = True,
) -> SecurityDayLandingBatch:
    """S1: fetch → LANDING only. Does not write canonical / accepted_partition."""

    contract = verify_stock_st_contract(contract)
    observed = observed_at or datetime.now(timezone.utc)
    try:
        batch = capture_security_day_provider_rows(
            DOMAIN,
            trade_date=trade_date,
            fetch_rows=fetch_rows,
            observed_at=observed,
        )
    except SecurityDayError as exc:
        raise StockStRuntimeError(str(exc)) from exc
    if bootstrap:
        bootstrap_stock_st_acceptance_schema(conn)
    land_stock_st_batch(conn, batch, contract)
    return batch


def accept_stock_st_from_landing(
    conn,
    batch_id: str,
    contract: StockStContract,
    *,
    bootstrap: bool = False,
) -> StockStAcceptanceOutcome:
    """S2: publish accepted from an existing LANDED batch. Zero provider fetch."""

    contract = verify_stock_st_contract(contract)
    if bootstrap:
        bootstrap_stock_st_acceptance_schema(conn)
    return accept_stock_st_batch(conn, str(batch_id), contract)


def capture_and_publish_authorized_stock_st_partition(
    conn,
    contract: StockStContract,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime | None = None,
    bootstrap: bool = True,
) -> StockStAcceptanceOutcome:
    """Authorized fused path: fetch → land → accept one trade_date.

    Prefer S1+S2 composition for new callers; kept for sync_runner until S3.
    """

    batch = capture_and_land_authorized_stock_st_partition(
        conn,
        contract,
        trade_date=trade_date,
        fetch_rows=fetch_rows,
        observed_at=observed_at,
        bootstrap=bootstrap,
    )
    return accept_stock_st_from_landing(
        conn, str(batch.batch_id), contract, bootstrap=False
    )


def refuse_legacy_stock_st_raw_write(*, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    raise StockStRuntimeError(
        "legacy_stock_st_raw_write_forbidden"
        f"{suffix}; use publish_accepted_stock_st_partition "
        f"for {LANDING_TABLE}/{CANONICAL_TABLE}"
    )


def runtime_surface() -> dict[str, Any]:
    return {
        "dataset_id": DOMAIN.dataset_id,
        "landing_table": LANDING_TABLE,
        "canonical_table": CANONICAL_TABLE,
        "writer_id": DOMAIN.writer_id,
        "legacy_raw_write": "forbidden",
        "provider_sync": "authorized_manual_generation",
    }


__all__ = [
    "StockStRuntimeError",
    "accept_stock_st_from_landing",
    "bootstrap_stock_st_acceptance_schema",
    "capture_and_land_authorized_stock_st_partition",
    "capture_and_publish_authorized_stock_st_partition",
    "publish_accepted_stock_st_partition",
    "refuse_legacy_stock_st_raw_write",
    "runtime_surface",
]
