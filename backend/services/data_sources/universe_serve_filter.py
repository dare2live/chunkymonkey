"""Canonical/serve universe filter with reason + policy hash evidence.

Landing must preserve the provider response.  Project-universe membership is
applied only at canonical/serve, never by deleting rows before raw write.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.universe import UniversePolicy, verify_universe_policy

_FULL_TS_CODE = re.compile(r"^\d{6}\.(SH|SZ|BJ|OC)$")
_BARE_DIGITS = re.compile(r"^\d{6}$")
_BJ_BOARD_PREFIXES = frozenset({"83", "87", "88", "89", "92"})


def normalize_provider_security_code(code: Any) -> str:
    """Normalize bare 6-digit codes to NNNNNN.EX without widening format rules.

    Vendor batches (e.g. share_float) sometimes emit BJ codes as ``874075``
    instead of ``874075.BJ``. Landing preserves the row after suffix repair;
    validate_universe_filter_column still requires full ``\\d{6}.(SH|SZ|BJ|OC)``.
    """

    raw = str(code or "").strip()
    if not raw:
        return raw
    upper = raw.upper()
    if _FULL_TS_CODE.fullmatch(upper):
        return upper
    if not _BARE_DIGITS.fullmatch(upper):
        return raw
    prefix = upper[:2]
    from services.universe import UNIVERSE_POLICY

    for rule in UNIVERSE_POLICY.venue_rules:
        if prefix == rule.board_prefix:
            return f"{upper}.{rule.ts_suffix}"
    if prefix in _BJ_BOARD_PREFIXES or upper[0] in {"4", "8"}:
        return f"{upper}.BJ"
    return raw




@dataclass(frozen=True)
class UniverseFilterEvidence:
    """Immutable evidence for one serve-time universe filter application."""

    policy_id: str
    policy_version: int
    policy_hash: str
    filter_column: str
    prefixes: tuple[str, ...]
    input_row_count: int
    kept_row_count: int
    excluded_row_count: int
    exclusion_reason_counts: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "filter_column": self.filter_column,
            "prefixes": list(self.prefixes),
            "input_row_count": self.input_row_count,
            "kept_row_count": self.kept_row_count,
            "excluded_row_count": self.excluded_row_count,
            "exclusion_reason_counts": dict(self.exclusion_reason_counts),
        }


def _prefixes_for_spec(spec: Mapping[str, Any], policy: UniversePolicy) -> tuple[str, ...]:
    configured = spec.get("universe_filter_prefixes")
    if configured is None:
        return tuple(policy.allowed_board_prefixes)
    if not isinstance(configured, Sequence) or isinstance(configured, (str, bytes)):
        raise ValueError("universe_filter_prefixes must be a sequence of prefixes")
    prefixes = tuple(str(item) for item in configured)
    if not prefixes:
        raise ValueError("universe_filter_prefixes must be non-empty when provided")
    return prefixes


def validate_universe_filter_column(
    values: Sequence[Any],
    *,
    filter_column: str,
    table: str,
) -> None:
    """Fail closed on filter-column miswiring without dropping landing rows."""

    if not values:
        return
    sample = [str(value) for value in list(values)[:20]]
    if all(__import__("re").fullmatch(r"\d{6}\.(SH|SZ|BJ|OC)", item) for item in sample):
        return
    raise ValueError(
        f"universe_filter_col={filter_column!r} on {table} does not look like "
        f"security codes (sample={sample[:3]}); refuse silent miswiring. "
        "Landing rows are not dropped — fix the registry column."
    )


def apply_universe_serve_filter(
    rows: Sequence[Mapping[str, Any]],
    *,
    policy: UniversePolicy,
    filter_column: str,
    prefixes: Sequence[str] | None = None,
) -> tuple[tuple[dict[str, Any], ...], UniverseFilterEvidence]:
    """Filter provider/canonical rows for project-universe serve consumers."""

    attested = verify_universe_policy(policy)
    prefix_set = tuple(
        str(item) for item in (prefixes if prefixes is not None else attested.allowed_board_prefixes)
    )
    if not prefix_set:
        raise ValueError("serve universe filter requires at least one board prefix")
    allowed = set(prefix_set)

    kept: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    for raw in rows:
        row = dict(raw)
        code = str(row.get(filter_column, "") or "")
        prefix = code[:2]
        if prefix in allowed:
            kept.append(row)
            continue
        reason = "board_prefix_not_in_project_universe"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    evidence = UniverseFilterEvidence(
        policy_id=attested.policy_id,
        policy_version=attested.policy_version,
        policy_hash=attested.config_hash,
        filter_column=filter_column,
        prefixes=prefix_set,
        input_row_count=len(rows),
        kept_row_count=len(kept),
        excluded_row_count=len(rows) - len(kept),
        exclusion_reason_counts=tuple(sorted(reason_counts.items())),
    )
    return tuple(kept), evidence


def serve_filter_from_spec(
    rows: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    policy: UniversePolicy,
) -> tuple[tuple[dict[str, Any], ...], UniverseFilterEvidence]:
    """Apply registry-declared serve filter using one factory-owned policy."""

    grain = list(spec.get("grain") or [])
    filter_column = str(spec.get("universe_filter_col") or (grain[0] if grain else "ts_code"))
    prefixes = _prefixes_for_spec(spec, policy)
    return apply_universe_serve_filter(
        rows,
        policy=policy,
        filter_column=filter_column,
        prefixes=prefixes,
    )


__all__ = [
    "UniverseFilterEvidence",
    "apply_universe_serve_filter",
    "normalize_provider_security_code",
    "serve_filter_from_spec",
    "validate_universe_filter_column",
]
