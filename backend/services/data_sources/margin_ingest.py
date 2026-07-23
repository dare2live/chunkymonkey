"""Formal margin contract parser and accepted-state helpers.

Knife 1b: contract_version>=3 enables bounded catchup via ``margin_catchup``.
This module still owns only contract parse + read projections — it does not
start provider I/O on import.
"""
from __future__ import annotations

from typing import Any, Callable

from services.data_sources.contracts import dataset_contract_from_spec
from services.data_sources.margin_population_scope import (
    assert_margin_accepted_population_scope,
    assert_margin_transport_matches_accepted_scope,
)
from services.data_sources.margin_schema import DATASET_ID
from services.data_sources.margin_state import (
    accepted_margin_dates,
    latest_accepted_margin_frontier,
)


def contract_for_spec(spec: dict[str, Any]):
    """Return the frozen margin-v2 contract; never execution authorization."""
    domain = str(spec.get("domain") or "").strip()
    metadata = spec.get("dataset_contract")
    if domain == "margin" and (
        not isinstance(metadata, dict) or metadata.get("dataset_id") != DATASET_ID
    ):
        raise ValueError(
            "margin is a blocking formal Tier0 dataset; missing or mismatched "
            "dataset_contract cannot fall back to the legacy path"
        )
    if not isinstance(metadata, dict):
        return None
    if metadata.get("dataset_id") == DATASET_ID and domain != "margin":
        raise ValueError(
            f"formal margin dataset_contract cannot be attached to domain={domain!r}"
        )
    if metadata.get("dataset_id") != DATASET_ID:
        return None
    if domain == "margin":
        # Fail closed on wrong accepted scope before any hash/transport proof.
        assert_margin_accepted_population_scope(spec)
        assert_margin_transport_matches_accepted_scope(spec)
    contract = dataset_contract_from_spec(domain, spec)
    if contract.dataset_id != DATASET_ID:
        raise ValueError(f"formal margin contract id drift: {contract.dataset_id!r}")
    transport_mismatches = {}
    if str(spec.get("batch_mode") or "") != "by_trade_date":
        transport_mismatches["batch_mode"] = spec.get("batch_mode")
    if str(spec.get("date_param") or "trade_date") != "trade_date":
        transport_mismatches["date_param"] = spec.get("date_param")
    if str(spec.get("write_mode") or "") != "replace_partition":
        transport_mismatches["write_mode"] = spec.get("write_mode")
    split = spec.get("split_by")
    if not isinstance(split, dict) or str(split.get("param") or "") != "exchange_id":
        transport_mismatches["split_by.param"] = (
            split.get("param") if isinstance(split, dict) else None
        )
        configured_groups: set[str] = set()
    else:
        raw_groups = split.get("values")
        if not isinstance(raw_groups, list) or not raw_groups:
            transport_mismatches["split_by.values"] = raw_groups
            configured_groups = set()
        elif any(
            not isinstance(value, str)
            or not value
            or value.strip() != value
            or value.upper() != value
            for value in raw_groups
        ):
            transport_mismatches["split_by.values"] = raw_groups
            configured_groups = set()
        elif len(raw_groups) != len(set(raw_groups)):
            transport_mismatches["split_by.values"] = raw_groups
            configured_groups = set()
        else:
            configured_groups = set(raw_groups)
    required_groups = set(contract.batch_completeness.required_groups)
    required_groups.update(
        group
        for group, _effective_date in (
            contract.batch_completeness.required_groups_since
        )
    )
    if (
        "split_by.values" not in transport_mismatches
        and configured_groups != required_groups
    ):
        transport_mismatches["split_by.values"] = sorted(configured_groups)
    if transport_mismatches:
        raise ValueError(
            "formal margin transport wiring drift: "
            f"actual={transport_mismatches} required_groups={sorted(required_groups)}"
        )
    return contract


def accepted_frontier(
    spec: dict[str, Any],
    *,
    contract=None,
    conn=None,
    target_conn_factory: Callable[[dict[str, Any]], Any] | None = None,
):
    """Read progress from accepted facts, never legacy raw or a watermark."""
    own = conn is None
    if own:
        if target_conn_factory is None:
            raise ValueError("accepted_frontier requires conn or target_conn_factory")
        conn = target_conn_factory(spec)
    try:
        return latest_accepted_margin_frontier(conn, contract=contract)
    finally:
        if own:
            conn.close()


def accepted_dates(conn, *, contract=None) -> set[str]:
    """Return partitions proven by the current AcceptedPartition contract."""
    return accepted_margin_dates(conn, contract=contract)


__all__ = [
    "accepted_dates",
    "accepted_frontier",
    "contract_for_spec",
]
