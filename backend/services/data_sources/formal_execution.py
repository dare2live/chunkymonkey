"""Formal DatasetExecutionContract handoff before any sync side effects.

Binding alone is not authorization.  A registered consumer must receive the
exact factory-owned object (identity ``is``) before a formal domain may leave
the preflight surface.  After a successful handoff, domain runtimes apply their
own refuse/retire rules — margin never falls through to the legacy sync runner.
"""
from __future__ import annotations

from collections.abc import Callable

from services.data_sources.population_scope import (
    DatasetExecutionContract,
    require_same_execution_contract,
    verify_execution_contract,
)


class FormalExecutionHandoffError(ValueError):
    """Formal execution handoff failed before or during consumer receipt."""

    def __init__(self, domain: str, *, reason: str, detail: str) -> None:
        self.domain = domain
        self.reason = reason
        self.detail = detail
        super().__init__(f"domain={domain} {detail}")


def receive_margin_execution_contract(
    execution: DatasetExecutionContract,
) -> DatasetExecutionContract:
    """Margin acceptance/state entry: attest the same object reached this consumer."""

    domain = execution.dataset.domain
    if domain != "margin":
        raise FormalExecutionHandoffError(
            domain,
            reason="formal_consumer_domain_mismatch",
            detail="margin consumer received a non-margin execution contract",
        )
    return require_same_execution_contract(execution, execution)


_FORMAL_EXECUTION_CONSUMERS: dict[
    str, Callable[[DatasetExecutionContract], DatasetExecutionContract]
] = {
    "margin": receive_margin_execution_contract,
}


def propagate_formal_execution_contract(
    domain: str,
    execution: DatasetExecutionContract,
) -> DatasetExecutionContract:
    """Hand the verified contract to the domain consumer; prove object identity."""

    verified = verify_execution_contract(execution)
    consumer = _FORMAL_EXECUTION_CONSUMERS.get(domain)
    if consumer is None:
        raise FormalExecutionHandoffError(
            domain,
            reason="execution_contract_not_propagated",
            detail=(
                "population scope parsed but no formal execution consumer is "
                "registered to receive DatasetExecutionContract"
            ),
        )
    received = consumer(verified)
    return require_same_execution_contract(verified, received)


def consumer_domains() -> tuple[str, ...]:
    """Test helper: domains with a registered formal execution consumer."""

    return tuple(sorted(_FORMAL_EXECUTION_CONSUMERS))


__all__ = [
    "FormalExecutionHandoffError",
    "consumer_domains",
    "propagate_formal_execution_contract",
    "receive_margin_execution_contract",
]
