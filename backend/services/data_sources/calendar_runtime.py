"""Live-capable accepted calendar publication boundary.

This is the only sync-facing entry for publishing one SSE calendar generation.
It never writes ``raw_tushare_trade_cal`` or ``dim_trading_calendar``.  Dim remains
an open-day serve projection and is not accepted truth.

Authorized manual sync (``execution_policy.mode=enabled``) captures provider
fragments through :func:`capture_and_publish_authorized_calendar_generation`.
Tests may still publish already-captured fragments via
:func:`publish_accepted_calendar_generation`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from math import isinf, isnan
from typing import Any

from services.data_sources.calendar_acceptance import (
    CalendarAcceptanceOutcome,
    accept_calendar_batch,
)
from services.data_sources.calendar_contract import (
    CalendarGenerationContract,
    verify_calendar_generation_contract,
)
from services.data_sources.calendar_landing import (
    CalendarFragmentCapture,
    CalendarLandingBatch,
    land_calendar_batch,
)
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    PROVIDER_FIELDS,
    ensure_calendar_acceptance_schema,
)

# Explicit role marker: never treat the open-day dim as this runtime's truth.
DIM_TRADING_CALENDAR_ROLE = "serve_projection_open_days_only"
ACCEPTED_PUBLICATION_TABLES = (FRAGMENT_TABLE, LANDING_TABLE, CANONICAL_TABLE)
_MAX_PROVIDER_PAGES = 10


class CalendarRuntimeError(RuntimeError):
    """Accepted calendar runtime refused an unsafe or incomplete request."""


def bootstrap_calendar_acceptance_schema(conn) -> None:
    """Explicit DDL entrypoint.  Land/accept never create tables implicitly."""

    ensure_calendar_acceptance_schema(conn)


def publish_accepted_calendar_generation(
    conn,
    batch: CalendarLandingBatch,
    contract: CalendarGenerationContract,
    *,
    bootstrap: bool = False,
) -> CalendarAcceptanceOutcome:
    """Land then accept one generation.  Optional bootstrap is opt-in only."""

    contract = verify_calendar_generation_contract(contract)
    if bootstrap:
        bootstrap_calendar_acceptance_schema(conn)
    # Land validates input before schema verify; do not DDL/verify ahead of that.
    land_calendar_batch(conn, batch, contract)
    return accept_calendar_batch(conn, str(batch.batch_id), contract)


def refuse_legacy_calendar_raw_write(*, detail: str = "") -> None:
    """Hard wall: formal calendar publication must not use legacy raw replace."""

    suffix = f": {detail}" if detail else ""
    raise CalendarRuntimeError(
        "legacy_calendar_raw_write_forbidden"
        f"{suffix}; use publish_accepted_calendar_generation "
        f"for {LANDING_TABLE}/{CANONICAL_TABLE}"
    )


def dim_is_accepted_calendar_truth() -> bool:
    """Always false: dim is an open-day serve projection, not accepted truth."""

    return False


def runtime_surface() -> dict[str, Any]:
    """Static inventory for doctor/gates; does not claim live readiness."""

    return {
        "publication_tables": list(ACCEPTED_PUBLICATION_TABLES),
        "dim_trading_calendar_role": DIM_TRADING_CALENDAR_ROLE,
        "dim_is_accepted_truth": dim_is_accepted_calendar_truth(),
        "legacy_raw_write": "forbidden",
        "provider_sync": "authorized_manual_generation",
    }


def _normalize_provider_value(value: Any) -> Any:
    """Provider nulls often arrive as float NaN; landing JSON requires None."""

    if isinstance(value, float) and (isnan(value) or isinf(value)):
        return None
    return value


def _project_provider_row(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in PROVIDER_FIELDS if field not in row]
    if missing:
        raise CalendarRuntimeError(
            "calendar_provider_row_missing_fields "
            f"missing={missing!r}"
        )
    return {
        field: _normalize_provider_value(row[field]) for field in PROVIDER_FIELDS
    }


def build_calendar_landing_batch(
    contract: CalendarGenerationContract,
    *,
    pages: Sequence[Sequence[Mapping[str, Any]]],
    observed_at: datetime,
    batch_id: str,
) -> CalendarLandingBatch:
    """Assemble one landing batch from already-ordered provider pages.

    Pages must be contract-shaped: every non-terminal page has exactly
    ``page_limit`` rows; the terminal page is shorter (possibly empty when the
    prior page filled the limit exactly).
    """

    contract = verify_calendar_generation_contract(contract)
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise CalendarRuntimeError("observed_at must be a timezone-aware datetime")
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise CalendarRuntimeError("batch_id must be non-empty")
    if not pages:
        raise CalendarRuntimeError("calendar capture requires at least one provider page")

    fragments: list[CalendarFragmentCapture] = []
    for ordinal, page in enumerate(pages):
        request = contract.request_for_page(observed_at, ordinal * contract.page_limit)
        projected = tuple(_project_provider_row(row) for row in page)
        if ordinal < len(pages) - 1 and len(projected) != contract.page_limit:
            raise CalendarRuntimeError(
                "calendar_non_terminal_page_size_mismatch "
                f"ordinal={ordinal} rows={len(projected)} "
                f"page_limit={contract.page_limit}"
            )
        if ordinal == len(pages) - 1 and len(projected) >= contract.page_limit:
            raise CalendarRuntimeError(
                "calendar_terminal_page_must_be_short "
                f"rows={len(projected)} page_limit={contract.page_limit}"
            )
        fragments.append(
            CalendarFragmentCapture(
                fragment_ordinal=ordinal,
                request=request,
                rows=projected,
                outcome="COMPLETED",
                completed_at=observed_at,
            )
        )
    return CalendarLandingBatch(batch_id, observed_at, tuple(fragments))


def capture_calendar_provider_pages(
    contract: CalendarGenerationContract,
    *,
    fetch_page: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime,
) -> tuple[tuple[tuple[dict[str, Any], ...], ...], str]:
    """Fetch one complete SSE generation as ordered provider pages."""

    contract = verify_calendar_generation_contract(contract)
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise CalendarRuntimeError("observed_at must be a timezone-aware datetime")

    pages: list[tuple[dict[str, Any], ...]] = []
    for ordinal in range(_MAX_PROVIDER_PAGES):
        request = contract.request_for_page(observed_at, ordinal * contract.page_limit)
        page = fetch_page(request)
        if page is None:
            raise CalendarRuntimeError(
                "calendar_provider_page_fetch_failed "
                f"ordinal={ordinal} offset={request['offset']}"
            )
        projected = tuple(_project_provider_row(row) for row in page)
        pages.append(projected)
        if len(projected) < contract.page_limit:
            end_date = contract.required_through(observed_at).strftime("%Y%m%d")
            stamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            batch_id = (
                f"trade_cal:SSE:{contract.coverage_start}_{end_date}:{stamp}"
            )
            return tuple(pages), batch_id

    raise CalendarRuntimeError(
        "calendar_provider_pagination_exceeded "
        f"max_pages={_MAX_PROVIDER_PAGES} page_limit={contract.page_limit}"
    )


def capture_and_publish_authorized_calendar_generation(
    conn,
    contract: CalendarGenerationContract,
    *,
    fetch_page: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    observed_at: datetime | None = None,
    bootstrap: bool = True,
) -> CalendarAcceptanceOutcome:
    """Authorized canary/manual path: fetch → land → accept one generation."""

    contract = verify_calendar_generation_contract(contract)
    observed = observed_at or datetime.now(timezone.utc)
    pages, batch_id = capture_calendar_provider_pages(
        contract, fetch_page=fetch_page, observed_at=observed
    )
    batch = build_calendar_landing_batch(
        contract,
        pages=pages,
        observed_at=observed,
        batch_id=batch_id,
    )
    return publish_accepted_calendar_generation(
        conn, batch, contract, bootstrap=bootstrap
    )


__all__ = [
    "ACCEPTED_PUBLICATION_TABLES",
    "CalendarRuntimeError",
    "DIM_TRADING_CALENDAR_ROLE",
    "bootstrap_calendar_acceptance_schema",
    "build_calendar_landing_batch",
    "capture_and_publish_authorized_calendar_generation",
    "capture_calendar_provider_pages",
    "dim_is_accepted_calendar_truth",
    "publish_accepted_calendar_generation",
    "refuse_legacy_calendar_raw_write",
    "runtime_surface",
]
