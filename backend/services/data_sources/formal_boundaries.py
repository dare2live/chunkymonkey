"""Formal adapter → landing → canonical writer boundaries.

Transport axis only.  Business tiers must not own these seams.  TuShare is the
sole live adapter; accepted truth is always the landing/canonical pair, never
the adapter response object itself.

Domains registered here must never fall through to legacy ``_write_batch`` raw
replace/merge.  Runtime may still be disabled/retired pending canary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RuntimeState = Literal[
    "retired_readonly",
    "accepted_runtime_ready_canary_pending",
    "writers_pending",
]


@dataclass(frozen=True)
class FormalDomainBoundary:
    domain: str
    adapter: str
    landing_writer: str
    canonical_writer: str
    dataset_id: str
    runtime_state: RuntimeState
    legacy_raw_write: Literal["forbidden"] = "forbidden"


# Sole live adapter for formal domains in this repository.
LIVE_ADAPTER = "tushare"

_FORMAL_BOUNDARIES: dict[str, FormalDomainBoundary] = {
    "margin": FormalDomainBoundary(
        domain="margin",
        adapter=LIVE_ADAPTER,
        landing_writer="services.data_sources.margin_acceptance.land_margin_batch",
        canonical_writer="services.data_sources.margin_acceptance.accept_margin_batch",
        dataset_id="tier0.market_data.margin_exchange_daily",
        runtime_state="retired_readonly",
    ),
    "trade_cal": FormalDomainBoundary(
        domain="trade_cal",
        adapter=LIVE_ADAPTER,
        landing_writer="services.data_sources.calendar_landing.land_calendar_batch",
        canonical_writer="services.data_sources.calendar_acceptance.accept_calendar_batch",
        dataset_id="tier0.reference.sse_trading_calendar_generation",
        runtime_state="accepted_runtime_ready_canary_pending",
    ),
    # Declared for A3/A5 visibility; writers not yet implemented.
    "daily": FormalDomainBoundary(
        domain="daily",
        adapter=LIVE_ADAPTER,
        landing_writer="pending:nominal_ohlcv_landing",
        canonical_writer="pending:nominal_ohlcv_accept",
        dataset_id="tier0.market_data.nominal_ohlcv_daily",
        runtime_state="writers_pending",
    ),
    "stock_st": FormalDomainBoundary(
        domain="stock_st",
        adapter=LIVE_ADAPTER,
        landing_writer="pending:stock_st_landing",
        canonical_writer="pending:stock_st_accept",
        dataset_id="tier0.security_identity.stock_st_daily",
        runtime_state="writers_pending",
    ),
}


class FormalBoundaryError(RuntimeError):
    """A formal transport boundary was violated before side effects."""

    def __init__(self, domain: str, *, reason: str, detail: str):
        self.domain = domain
        self.reason = reason
        self.detail = detail
        super().__init__(f"domain={domain} reason={reason} {detail}")


def formal_boundary(domain: str) -> FormalDomainBoundary | None:
    return _FORMAL_BOUNDARIES.get(domain)


def formal_domains() -> tuple[str, ...]:
    return tuple(sorted(_FORMAL_BOUNDARIES))


def require_live_adapter(adapter_name: str, *, domain: str) -> str:
    name = str(adapter_name or "").strip()
    if name != LIVE_ADAPTER:
        raise FormalBoundaryError(
            domain,
            reason="unsupported_live_adapter",
            detail=(
                f"formal domains only allow adapter={LIVE_ADAPTER!r}; "
                f"got {name!r}"
            ),
        )
    return name


def refuse_legacy_raw_write_for_formal_domain(domain: str) -> None:
    """Hard wall for domains whose formal writers exist or are canary-ready.

    ``writers_pending`` domains remain on the temporary legacy path until their
    landing/accept modules land; they are inventory-visible but not write-walled.
    """

    boundary = formal_boundary(domain)
    if boundary is None or boundary.runtime_state == "writers_pending":
        return
    if boundary.legacy_raw_write != "forbidden":
        raise FormalBoundaryError(
            domain,
            reason="invalid_boundary_declaration",
            detail="legacy_raw_write must be forbidden for formal domains",
        )
    raise FormalBoundaryError(
        domain,
        reason="formal_legacy_raw_write_forbidden",
        detail=(
            f"domain={domain} has formal boundary "
            f"landing={boundary.landing_writer} "
            f"canonical={boundary.canonical_writer} "
            f"runtime_state={boundary.runtime_state}; "
            "legacy _write_batch/raw replace is forbidden"
        ),
    )


def boundary_inventory() -> tuple[dict[str, str], ...]:
    """Static inventory for doctor/gates; does not claim live readiness."""

    return tuple(
        {
            "domain": item.domain,
            "adapter": item.adapter,
            "landing_writer": item.landing_writer,
            "canonical_writer": item.canonical_writer,
            "dataset_id": item.dataset_id,
            "runtime_state": item.runtime_state,
            "legacy_raw_write": item.legacy_raw_write,
        }
        for item in (_FORMAL_BOUNDARIES[name] for name in formal_domains())
    )


__all__ = [
    "LIVE_ADAPTER",
    "FormalBoundaryError",
    "FormalDomainBoundary",
    "boundary_inventory",
    "formal_boundary",
    "formal_domains",
    "refuse_legacy_raw_write_for_formal_domain",
    "require_live_adapter",
]
