"""Public publication boundary for accepted nominal OHLCV partitions.

Transport strangler surfaces (independent):
- :func:`capture_and_land_authorized_nominal_ohlcv_partition` — S1 land-only
- :func:`accept_nominal_ohlcv_from_landing` — S2 accept-from-landing (zero fetch)
- :func:`capture_and_publish_authorized_nominal_ohlcv_partition` — deprecated fused
  helper retained for unit tests only (production sync is caller-only S1→S2)

It never writes ``raw_tushare_daily``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

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
from services.data_sources.nominal_ohlcv_schema import (
    CANONICAL_TABLE,
    DOMAIN,
    LANDING_TABLE,
)
from services.data_sources.security_day_capture import (
    capture_security_day_provider_rows,
)
from services.data_sources.security_day_partition import (
    SecurityDayError,
    SecurityDayLandingBatch,
)


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


def capture_and_land_authorized_nominal_ohlcv_partition(
    conn,
    contract: NominalOhlcvContract,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime | None = None,
    bootstrap: bool = True,
) -> SecurityDayLandingBatch:
    """S1: fetch → LANDING only. Does not write canonical / accepted_partition."""

    contract = verify_nominal_ohlcv_contract(contract)
    observed = observed_at or datetime.now(timezone.utc)
    try:
        batch = capture_security_day_provider_rows(
            DOMAIN,
            trade_date=trade_date,
            fetch_rows=fetch_rows,
            observed_at=observed,
        )
    except SecurityDayError as exc:
        raise NominalOhlcvRuntimeError(str(exc)) from exc
    if bootstrap:
        bootstrap_nominal_ohlcv_acceptance_schema(conn)
    land_nominal_ohlcv_batch(conn, batch, contract)
    return batch


def accept_nominal_ohlcv_from_landing(
    conn,
    batch_id: str,
    contract: NominalOhlcvContract,
    *,
    bootstrap: bool = False,
) -> NominalOhlcvAcceptanceOutcome:
    """S2: publish accepted from an existing LANDED batch. Zero provider fetch."""

    contract = verify_nominal_ohlcv_contract(contract)
    if bootstrap:
        bootstrap_nominal_ohlcv_acceptance_schema(conn)
    return accept_nominal_ohlcv_batch(conn, str(batch_id), contract)


def capture_and_publish_authorized_nominal_ohlcv_partition(
    conn,
    contract: NominalOhlcvContract,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime | None = None,
    bootstrap: bool = True,
) -> NominalOhlcvAcceptanceOutcome:
    """Deprecated fused path: fetch → land → accept one trade_date.

    Production sync must call S1 then S2 (or ``land_then_accept_*``). Kept for
    unit-test fixtures that exercise the fused helper directly.
    """

    batch = capture_and_land_authorized_nominal_ohlcv_partition(
        conn,
        contract,
        trade_date=trade_date,
        fetch_rows=fetch_rows,
        observed_at=observed_at,
        bootstrap=bootstrap,
    )
    return accept_nominal_ohlcv_from_landing(
        conn, str(batch.batch_id), contract, bootstrap=False
    )


def refuse_legacy_nominal_ohlcv_raw_write(*, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    raise NominalOhlcvRuntimeError(
        "legacy_nominal_ohlcv_raw_write_forbidden"
        f"{suffix}; use publish_accepted_nominal_ohlcv_partition "
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
    "NominalOhlcvRuntimeError",
    "accept_nominal_ohlcv_from_landing",
    "bootstrap_nominal_ohlcv_acceptance_schema",
    "capture_and_land_authorized_nominal_ohlcv_partition",
    "capture_and_publish_authorized_nominal_ohlcv_partition",
    "publish_accepted_nominal_ohlcv_partition",
    "refuse_legacy_nominal_ohlcv_raw_write",
    "runtime_surface",
]
