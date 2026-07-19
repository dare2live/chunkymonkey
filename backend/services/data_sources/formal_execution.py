"""Formal DatasetExecutionContract handoff before any sync side effects.

Binding alone is not authorization.  A registered consumer must receive the
exact factory-owned object (identity ``is``) before a formal domain may leave
the preflight surface.  After a successful handoff, domain runtimes apply their
own refuse/retire rules — margin never falls through to the legacy sync runner.

Disclosure domains (E0) use a parallel handoff for schema-owned contracts that
are not TuShare ``DatasetExecutionContract`` registry bindings.  Formal
disclosure writes must call ``propagate_disclosure_execution_contract``; naked
``_write_batch`` remains NONCONFORMING strangler only.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


def receive_holders_top10_contract(contract: Any) -> Any:
    """holders_top10 land/accept entry: same object + factory hash verify."""

    from services.data_sources.holders_top10_contract import (
        HoldersTop10Contract,
        verify_holders_top10_contract,
    )

    if not isinstance(contract, HoldersTop10Contract):
        raise FormalExecutionHandoffError(
            "holders_top10",
            reason="formal_consumer_domain_mismatch",
            detail="holders_top10 consumer requires HoldersTop10Contract",
        )
    if contract.domain != "holders_top10":
        raise FormalExecutionHandoffError(
            contract.domain,
            reason="formal_consumer_domain_mismatch",
            detail="holders_top10 consumer received a mismatched contract domain",
        )
    verify_holders_top10_contract(contract)
    return contract


def receive_org_holding_contract(contract: Any) -> Any:
    """org_holding land/accept entry: same object + factory hash verify."""

    from services.data_sources.org_holding_contract import (
        OrgHoldingContract,
        verify_org_holding_contract,
    )

    if not isinstance(contract, OrgHoldingContract):
        raise FormalExecutionHandoffError(
            "org_holding",
            reason="formal_consumer_domain_mismatch",
            detail="org_holding consumer requires OrgHoldingContract",
        )
    if contract.domain != "org_holding":
        raise FormalExecutionHandoffError(
            contract.domain,
            reason="formal_consumer_domain_mismatch",
            detail="org_holding consumer received a mismatched contract domain",
        )
    verify_org_holding_contract(contract)
    return contract


def receive_stk_holdertrade_contract(contract: Any) -> Any:
    """stk_holdertrade land/accept entry: same object + factory hash verify."""

    from services.data_sources.stk_holdertrade_contract import (
        StkHoldertradeContract,
        verify_stk_holdertrade_contract,
    )

    if not isinstance(contract, StkHoldertradeContract):
        raise FormalExecutionHandoffError(
            "stk_holdertrade",
            reason="formal_consumer_domain_mismatch",
            detail="stk_holdertrade consumer requires StkHoldertradeContract",
        )
    if contract.domain != "stk_holdertrade":
        raise FormalExecutionHandoffError(
            contract.domain,
            reason="formal_consumer_domain_mismatch",
            detail="stk_holdertrade consumer received a mismatched contract domain",
        )
    verify_stk_holdertrade_contract(contract)
    return contract


_DISCLOSURE_EXECUTION_CONSUMERS: dict[str, Callable[[Any], Any]] = {
    "holders_top10": receive_holders_top10_contract,
    "org_holding": receive_org_holding_contract,
    "stk_holdertrade": receive_stk_holdertrade_contract,
}


def propagate_disclosure_execution_contract(domain: str, contract: Any) -> Any:
    """Hand a disclosure schema contract to its consumer; prove object identity."""

    consumer = _DISCLOSURE_EXECUTION_CONSUMERS.get(domain)
    if consumer is None:
        raise FormalExecutionHandoffError(
            domain,
            reason="execution_contract_not_propagated",
            detail=(
                "no disclosure execution consumer is registered; "
                "naked legacy _write_batch cannot claim formal publication"
            ),
        )
    received = consumer(contract)
    if received is not contract:
        raise FormalExecutionHandoffError(
            domain,
            reason="execution_contract_identity_lost",
            detail="disclosure consumer returned a different contract object",
        )
    return received


def consumer_domains() -> tuple[str, ...]:
    """Test helper: domains with a registered formal execution consumer."""

    return tuple(sorted(_FORMAL_EXECUTION_CONSUMERS))


def disclosure_consumer_domains() -> tuple[str, ...]:
    """Test helper: disclosure domains with a registered execution consumer."""

    return tuple(sorted(_DISCLOSURE_EXECUTION_CONSUMERS))


__all__ = [
    "FormalExecutionHandoffError",
    "consumer_domains",
    "disclosure_consumer_domains",
    "propagate_disclosure_execution_contract",
    "propagate_formal_execution_contract",
    "receive_holders_top10_contract",
    "receive_margin_execution_contract",
    "receive_org_holding_contract",
    "receive_stk_holdertrade_contract",
]
