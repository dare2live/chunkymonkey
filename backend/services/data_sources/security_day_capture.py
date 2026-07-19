"""Provider capture helpers for authorized single-day security partitions.

Kept separate from land→accept mechanics so the acceptance module stays under
the god-file ratchet.  Domain runtimes own publication; this module only shapes
provider pages into :class:`SecurityDayLandingBatch`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from math import isinf, isnan
from typing import Any

from services.data_sources.security_day_partition import (
    SecurityDayDomain,
    SecurityDayError,
    SecurityDayLandingBatch,
    _partition,
)


def _normalize_provider_value(value: Any) -> Any:
    """Provider nulls often arrive as float NaN; landing JSON requires None."""

    if isinstance(value, float) and (isnan(value) or isinf(value)):
        return None
    return value


def project_security_day_provider_row(
    domain: SecurityDayDomain, row: Mapping[str, Any]
) -> dict[str, Any]:
    """Project one provider row onto the domain's declared fields only."""

    if not isinstance(row, Mapping):
        raise SecurityDayError(f"{domain.domain}: provider row must be a mapping")
    missing = [name for name in domain.provider_fields if name not in row]
    if missing:
        raise SecurityDayError(
            f"{domain.domain}_provider_row_missing_fields missing={missing!r}"
        )
    return {
        name: _normalize_provider_value(row[name]) for name in domain.provider_fields
    }


def build_security_day_landing_batch(
    domain: SecurityDayDomain,
    *,
    trade_date: str,
    rows: Sequence[Mapping[str, Any]],
    observed_at: datetime,
    batch_id: str,
) -> SecurityDayLandingBatch:
    """Assemble one landing batch from an already-captured provider page."""

    partition = _partition(trade_date)
    if partition < domain.coverage_start:
        raise SecurityDayError(
            f"{domain.domain}: trade_date={partition} before "
            f"coverage_start={domain.coverage_start}"
        )
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise SecurityDayError("observed_at must be a timezone-aware datetime")
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise SecurityDayError("batch_id must be non-empty")
    if not rows:
        raise SecurityDayError(
            f"{domain.domain} capture rejects empty provider rows "
            f"trade_date={partition}"
        )
    projected = tuple(project_security_day_provider_row(domain, row) for row in rows)
    for row in projected:
        compact = _partition(row["trade_date"])
        if compact != partition:
            raise SecurityDayError(
                f"{domain.domain}_partition_mismatch "
                f"row_trade_date={compact} expected={partition}"
            )
    return SecurityDayLandingBatch(
        batch_id=batch_id,
        partition_value=partition,
        observed_at=observed_at,
        available_at=observed_at,
        rows=projected,
        request={"api": domain.api, "trade_date": partition},
        source=domain.source,
        contract_version=domain.contract_version,
    )


def capture_security_day_provider_rows(
    domain: SecurityDayDomain,
    *,
    trade_date: str,
    fetch_rows: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime,
) -> SecurityDayLandingBatch:
    """Fetch one trade_date partition and build a landing batch."""

    partition = _partition(trade_date)
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise SecurityDayError("observed_at must be a timezone-aware datetime")
    request = {"api": domain.api, "trade_date": partition}
    page = fetch_rows(request)
    if page is None:
        raise SecurityDayError(
            f"{domain.domain}_provider_fetch_failed trade_date={partition}"
        )
    stamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_id = f"{domain.domain}:{partition}:{stamp}"
    return build_security_day_landing_batch(
        domain,
        trade_date=partition,
        rows=page,
        observed_at=observed_at,
        batch_id=batch_id,
    )


__all__ = [
    "build_security_day_landing_batch",
    "capture_security_day_provider_rows",
    "project_security_day_provider_row",
]
