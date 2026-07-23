"""Fail-closed population-scope rules for the formal margin dataset.

Accepted business claim for margin is an ``external_aggregate`` over SSE+SZSE
only. Venue aggregates must never be relabelled as ``project_universe_pit``.
BSE may exist in immutable v2 frozen evidence / legacy transport shape while
execution stays disabled; enabling live land/accept requires transport to match
the corrected accepted venues (Knife 1b).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.data_sources.population_scope import ExternalAggregateScope

MARGIN_ACCEPTED_VENUE_IDS: tuple[str, ...] = ("SSE", "SZSE")


class MarginPopulationScopeError(ValueError):
    """Margin accepted population scope is not the corrected SSE+SZSE claim."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarginPopulationScopeError(f"margin: {field} must be a mapping")
    return value


def assert_margin_accepted_population_scope(
    spec: Mapping[str, Any],
) -> ExternalAggregateScope:
    """Prove registry accepted scope is SSE+SZSE external_aggregate only."""

    domain = str(spec.get("domain") or "").strip()
    if domain and domain != "margin":
        raise MarginPopulationScopeError(
            f"margin population-scope assert cannot run for domain={domain!r}"
        )
    if "population_scope" not in spec or spec.get("population_scope") is None:
        raise MarginPopulationScopeError("margin: missing population_scope")
    raw = _mapping(spec.get("population_scope"), "population_scope")
    kind = str(raw.get("kind") or "").strip()
    if kind == "project_universe_pit":
        raise MarginPopulationScopeError(
            "margin: accepted population_scope must not be project_universe_pit; "
            "venue aggregates are external_aggregate only"
        )
    if kind != "external_aggregate":
        raise MarginPopulationScopeError(
            f"margin: accepted population_scope.kind must be external_aggregate, "
            f"got {kind!r}"
        )
    venue_field = str(raw.get("venue_field") or "").strip()
    if venue_field != "exchange_id":
        raise MarginPopulationScopeError(
            "margin: population_scope.venue_field must be exchange_id"
        )
    raw_venues = raw.get("venue_ids")
    if not isinstance(raw_venues, list) or not raw_venues:
        raise MarginPopulationScopeError(
            "margin: population_scope.venue_ids must be a non-empty list"
        )
    venues = tuple(sorted({str(v).strip().upper() for v in raw_venues}))
    if venues != MARGIN_ACCEPTED_VENUE_IDS:
        raise MarginPopulationScopeError(
            "margin: accepted population_scope.venue_ids must be exactly "
            f"{list(MARGIN_ACCEPTED_VENUE_IDS)} (BSE is not a project-facing "
            f"margin claim); got {list(venues)}"
        )
    label = str(raw.get("population_label") or "").strip()
    method = str(raw.get("method") or "").strip()
    unit = str(raw.get("unit") or "").strip()
    if not label or not method or not unit:
        raise MarginPopulationScopeError(
            "margin: population_scope requires population_label, method, unit"
        )
    return ExternalAggregateScope(
        venue_field=venue_field,
        venue_ids=venues,
        population_label=label,
        method=method,
        unit=unit,
    )


def assert_margin_transport_matches_accepted_scope(
    spec: Mapping[str, Any],
) -> None:
    """When execution is enabled, transport groups must match accepted venues.

    Frozen v2 may still declare BSE in ``split_by`` / ``required_groups_since``
    while ``mode=disabled``; lifting the freeze without aligning transport is
    banned (would re-open wrong-scope accept).
    """

    assert_margin_accepted_population_scope(spec)
    policy = spec.get("execution_policy")
    if not isinstance(policy, Mapping):
        return
    if str(policy.get("mode") or "").strip() != "enabled":
        return

    completeness = spec.get("batch_completeness")
    if not isinstance(completeness, Mapping):
        raise MarginPopulationScopeError(
            "margin: enabled execution requires batch_completeness"
        )
    required = {
        str(v).strip().upper()
        for v in (completeness.get("required_groups") or [])
        if v is not None
    }
    since = completeness.get("required_groups_since") or {}
    if not isinstance(since, Mapping):
        raise MarginPopulationScopeError(
            "margin: required_groups_since must be a mapping when enabled"
        )
    since_groups = {str(k).strip().upper() for k in since}
    if "BSE" in required or "BSE" in since_groups:
        raise MarginPopulationScopeError(
            "margin: enabled execution forbids BSE in batch_completeness; "
            "corrected accepted claim is SSE+SZSE only (need contract v3+)"
        )
    if required != set(MARGIN_ACCEPTED_VENUE_IDS):
        raise MarginPopulationScopeError(
            "margin: enabled required_groups must be exactly "
            f"{list(MARGIN_ACCEPTED_VENUE_IDS)}; got {sorted(required)}"
        )

    split = spec.get("split_by")
    if not isinstance(split, Mapping):
        raise MarginPopulationScopeError(
            "margin: enabled execution requires split_by"
        )
    values = split.get("values")
    if not isinstance(values, list):
        raise MarginPopulationScopeError(
            "margin: enabled split_by.values must be a list"
        )
    split_venues = {str(v).strip().upper() for v in values}
    if split_venues != set(MARGIN_ACCEPTED_VENUE_IDS):
        raise MarginPopulationScopeError(
            "margin: enabled split_by.values must be exactly "
            f"{list(MARGIN_ACCEPTED_VENUE_IDS)}; got {sorted(split_venues)}"
        )


__all__ = [
    "MARGIN_ACCEPTED_VENUE_IDS",
    "MarginPopulationScopeError",
    "assert_margin_accepted_population_scope",
    "assert_margin_transport_matches_accepted_scope",
]
