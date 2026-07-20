"""Thin transport orchestration for formal daily / stock_st partitions.

Strangler surfaces (caller-only composition; not a second dragon):
- S1 land-only via runtime ``capture_and_land_*``
- S2 accept-from-landing via runtime ``accept_*_from_landing`` (zero fetch)
- ``land_then_accept_*`` = S1 then S2 in the caller
- local legacy-raw materializer = explicit acquire→landing with lineage

Production ``sync_runner`` default path calls :func:`land_then_accept_authorized_security_day`
(caller-only). Deprecated fused ``capture_and_publish_*`` helpers remain test-only.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from services.data_sources.security_day_capture import (
    build_security_day_landing_batch,
)
from services.data_sources.security_day_partition import (
    SecurityDayError,
    SecurityDayLandingBatch,
)

SECURITY_DAY_TRANSPORT_DOMAINS = frozenset({"daily", "stock_st"})


def _domain_runtime(domain: str):
    if domain == "daily":
        from services.data_sources import nominal_ohlcv_runtime as runtime
        from services.data_sources.nominal_ohlcv_schema import DOMAIN

        return runtime, DOMAIN
    if domain == "stock_st":
        from services.data_sources import stock_st_runtime as runtime
        from services.data_sources.stock_st_schema import DOMAIN

        return runtime, DOMAIN
    raise SecurityDayError(
        f"security_day_transport unsupported domain={domain!r}; "
        f"allowed={sorted(SECURITY_DAY_TRANSPORT_DOMAINS)}"
    )


def materialize_security_day_landing_from_legacy_raw_rows(
    domain: str,
    conn,
    contract: Any,
    *,
    trade_date: str,
    raw_rows: Sequence[Mapping[str, Any]],
    observed_at: datetime,
    bootstrap: bool = True,
    lineage_note: str = "local_legacy_raw_materialize",
    batch_id: str | None = None,
) -> SecurityDayLandingBatch:
    """Explicit acquire→landing from local ``raw_tushare_*`` shaped rows.

    Never writes canonical. Callers must accept via S2 separately.
    ``available_at`` follows the domain publication cutoff (honest PIT).
    """

    runtime, domain_spec = _domain_runtime(domain)
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise SecurityDayError("observed_at must be a timezone-aware datetime")
    stamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    partition = str(trade_date).replace("-", "")
    resolved_batch_id = batch_id or f"{domain}:{partition}:localraw:{stamp}"
    batch = build_security_day_landing_batch(
        domain_spec,
        trade_date=partition,
        rows=raw_rows,
        observed_at=observed_at,
        batch_id=resolved_batch_id,
    )
    # Rebuild with honest lineage on the request (acquire mode, not silent raw→canon).
    batch = SecurityDayLandingBatch(
        batch_id=batch.batch_id,
        partition_value=batch.partition_value,
        observed_at=batch.observed_at,
        available_at=batch.available_at,
        rows=batch.rows,
        request={
            **dict(batch.request),
            "acquire_mode": "local_legacy_raw_materialize",
            "lineage_note": str(lineage_note),
            "compatibility_table": domain_spec.compatibility_table,
        },
        source=batch.source,
        contract_version=batch.contract_version,
    )
    if bootstrap:
        if domain == "daily":
            runtime.bootstrap_nominal_ohlcv_acceptance_schema(conn)
        else:
            runtime.bootstrap_stock_st_acceptance_schema(conn)
    if domain == "daily":
        from services.data_sources.nominal_ohlcv_acceptance import land_nominal_ohlcv_batch

        land_nominal_ohlcv_batch(conn, batch, contract)
    else:
        from services.data_sources.stock_st_acceptance import land_stock_st_batch

        land_stock_st_batch(conn, batch, contract)
    return batch


def land_then_accept_authorized_security_day(
    domain: str,
    conn,
    contract: Any,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime | None = None,
    bootstrap: bool = True,
) -> Any:
    """Caller-only S1→S2 composition (thin orchestration toward S3)."""

    runtime, _domain_spec = _domain_runtime(domain)
    if domain == "daily":
        batch = runtime.capture_and_land_authorized_nominal_ohlcv_partition(
            conn,
            contract,
            trade_date=trade_date,
            fetch_rows=fetch_rows,
            observed_at=observed_at,
            bootstrap=bootstrap,
        )
        return runtime.accept_nominal_ohlcv_from_landing(
            conn, str(batch.batch_id), contract, bootstrap=False
        )
    batch = runtime.capture_and_land_authorized_stock_st_partition(
        conn,
        contract,
        trade_date=trade_date,
        fetch_rows=fetch_rows,
        observed_at=observed_at,
        bootstrap=bootstrap,
    )
    return runtime.accept_stock_st_from_landing(
        conn, str(batch.batch_id), contract, bootstrap=False
    )


__all__ = [
    "SECURITY_DAY_TRANSPORT_DOMAINS",
    "land_then_accept_authorized_security_day",
    "materialize_security_day_landing_from_legacy_raw_rows",
]
