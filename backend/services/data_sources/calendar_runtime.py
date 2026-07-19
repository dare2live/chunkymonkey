"""Live-capable accepted calendar publication boundary.

This is the only sync-facing entry for publishing one SSE calendar generation.
It never writes ``raw_tushare_trade_cal`` or ``dim_trading_calendar``.  Dim remains
an open-day serve projection and is not accepted truth.

Provider mass-fetch stays behind ``execution_policy.mode=disabled`` until an
authorized canary; tests and controlled callers may publish from already-captured
fragments through :func:`publish_accepted_calendar_generation`.
"""
from __future__ import annotations

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
    CalendarLandingBatch,
    land_calendar_batch,
)
from services.data_sources.calendar_schema import (
    CANONICAL_TABLE,
    FRAGMENT_TABLE,
    LANDING_TABLE,
    ensure_calendar_acceptance_schema,
)

# Explicit role marker: never treat the open-day dim as this runtime's truth.
DIM_TRADING_CALENDAR_ROLE = "serve_projection_open_days_only"
ACCEPTED_PUBLICATION_TABLES = (FRAGMENT_TABLE, LANDING_TABLE, CANONICAL_TABLE)


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
        "provider_sync": "disabled_until_authorized_canary",
    }


__all__ = [
    "ACCEPTED_PUBLICATION_TABLES",
    "CalendarRuntimeError",
    "DIM_TRADING_CALENDAR_ROLE",
    "bootstrap_calendar_acceptance_schema",
    "dim_is_accepted_calendar_truth",
    "publish_accepted_calendar_generation",
    "refuse_legacy_calendar_raw_write",
    "runtime_surface",
]
