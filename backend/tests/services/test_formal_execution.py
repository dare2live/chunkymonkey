"""Formal DatasetExecutionContract handoff / consumer identity tests."""
from __future__ import annotations

import pytest

from services.data_sources.formal_execution import (
    FormalExecutionHandoffError,
    consumer_domains,
    propagate_formal_execution_contract,
    receive_margin_execution_contract,
)
from services.data_sources.population_scope import bind_execution_contract
from services.data_sources.sync_runner import domain_spec, load_registry


def _margin_execution():
    spec = domain_spec(load_registry(), "margin")
    from services.data_sources.contracts import dataset_contract_from_spec

    dataset = dataset_contract_from_spec("margin", spec)
    return bind_execution_contract(dataset, spec, None), spec


def test_margin_consumer_is_registered_and_returns_same_object():
    execution, _spec = _margin_execution()

    assert "margin" in consumer_domains()
    received = receive_margin_execution_contract(execution)
    assert received is execution


def test_propagate_margin_execution_attests_identity():
    execution, _spec = _margin_execution()

    attested = propagate_formal_execution_contract("margin", execution)
    assert attested is execution


def test_propagate_without_consumer_is_not_propagated():
    execution, _spec = _margin_execution()

    with pytest.raises(
        FormalExecutionHandoffError, match="no formal execution consumer"
    ) as caught:
        propagate_formal_execution_contract("orphan_formal", execution)

    assert caught.value.reason == "execution_contract_not_propagated"


def test_consumer_returning_rebinding_fails_identity(monkeypatch):
    import services.data_sources.formal_execution as formal_execution

    execution, spec = _margin_execution()

    def _rebinding_consumer(received):
        return bind_execution_contract(received.dataset, spec, None)

    monkeypatch.setitem(
        formal_execution._FORMAL_EXECUTION_CONSUMERS,
        "margin",
        _rebinding_consumer,
    )

    with pytest.raises(ValueError, match="identity mismatch"):
        propagate_formal_execution_contract("margin", execution)
