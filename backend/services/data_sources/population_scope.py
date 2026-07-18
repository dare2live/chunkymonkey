"""Typed population scope bound to one immutable dataset execution.

The binder consumes the already-derived :class:`DatasetContract` and the
explicitly injected universe-policy snapshot.  It deliberately does not load
registry YAML or reach for the module-level active universe policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any, Literal, TypeAlias, final

from services.data_sources.contracts import DatasetContract
from services.universe import UniverseDataError, UniversePolicy, verify_universe_policy


PopulationScopeKind: TypeAlias = Literal[
    "raw_evidence", "external_aggregate", "project_universe_pit"
]

_EXTERNAL_KEYS = frozenset(
    {"kind", "venue_field", "venue_ids", "population_label", "method", "unit"}
)
_RAW_KEYS = frozenset({"kind", "population_label", "usage"})
_PROJECT_KEYS = frozenset(
    {
        "kind",
        "universe_policy_id",
        "security_field",
        "as_of_field",
        "as_of_role",
    }
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
_VENUE_ID = re.compile(r"[A-Z][A-Z0-9_]{1,15}\Z")
_AS_OF_ROLES = frozenset({"observation_time", "availability_time"})


@dataclass(frozen=True)
class RawEvidenceScope:
    """Provider evidence accepted for dependencies, never for direct serving."""

    population_label: str
    usage: Literal["evidence_only"] = "evidence_only"
    kind: Literal["raw_evidence"] = field(default="raw_evidence", init=False)

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "population_label": self.population_label,
            "usage": self.usage,
        }


@dataclass(frozen=True)
class ExternalAggregateScope:
    """A venue/provider aggregate that is not a project-universe metric."""

    venue_field: str
    venue_ids: tuple[str, ...]
    population_label: str
    method: str
    unit: str
    kind: Literal["external_aggregate"] = field(
        default="external_aggregate", init=False
    )

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "venue_field": self.venue_field,
            "venue_ids": list(self.venue_ids),
            "population_label": self.population_label,
            "method": self.method,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class ProjectUniversePitScope:
    """A security-grained publication filtered using an as-of universe."""

    universe_policy_id: str
    security_field: str
    as_of_field: str
    as_of_role: Literal["observation_time", "availability_time"]
    kind: Literal["project_universe_pit"] = field(
        default="project_universe_pit", init=False
    )

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "universe_policy_id": self.universe_policy_id,
            "security_field": self.security_field,
            "as_of_field": self.as_of_field,
            "as_of_role": self.as_of_role,
        }


PopulationScope: TypeAlias = (
    RawEvidenceScope | ExternalAggregateScope | ProjectUniversePitScope
)


@final
@dataclass(frozen=True, init=False)
class DatasetExecutionContract:
    """One dataset contract bound by :func:`bind_execution_contract` only."""

    dataset: DatasetContract
    landing_scope: RawEvidenceScope
    accepted_scope: PopulationScope
    universe_policy: UniversePolicy | None
    execution_hash: str

    def __new__(cls, *_args, **_kwargs):
        raise TypeError("use bind_execution_contract()")


def _mapping(value: Any, field_name: str, domain: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{domain}: {field_name} must be a mapping")
    return value


def _text(value: Any, field_name: str, domain: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        raise ValueError(
            f"{domain}: {field_name} must be a non-empty string without "
            "surrounding whitespace"
        )
    return value


def _unique_venue_ids(value: Any, field_name: str, domain: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{domain}: {field_name} must be a non-empty list")
    venues: list[str] = []
    for raw in value:
        venue = _text(raw, field_name, domain)
        if _VENUE_ID.fullmatch(venue) is None:
            raise ValueError(f"{domain}: {field_name} contains malformed venue {venue!r}")
        if venue in venues:
            raise ValueError(f"{domain}: {field_name} contains duplicate venue {venue!r}")
        venues.append(venue)
    # Venue population is a set-valued contract.  Canonicalize its serialized
    # order so a harmless YAML reorder cannot manufacture a new execution hash.
    return tuple(sorted(venues))


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], domain: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise ValueError(
            f"{domain}: missing population_scope keys: {', '.join(missing)}"
        )
    if unknown:
        raise ValueError(
            f"{domain}: unknown population_scope keys: {', '.join(unknown)}"
        )


def _raw_scope(
    dataset: DatasetContract,
    raw: Mapping[str, Any],
    policy: UniversePolicy | None,
) -> RawEvidenceScope:
    _exact_keys(raw, _RAW_KEYS, dataset.domain)
    if policy is not None:
        raise ValueError(f"{dataset.domain}: raw_evidence universe policy must be None")
    population_label = _text(
        raw["population_label"], "population_scope.population_label", dataset.domain
    )
    usage = _text(raw["usage"], "population_scope.usage", dataset.domain)
    if population_label != "provider_response":
        raise ValueError(
            f"{dataset.domain}: raw_evidence population_label must be 'provider_response'"
        )
    if usage != "evidence_only":
        raise ValueError(f"{dataset.domain}: raw_evidence usage must be 'evidence_only'")
    return RawEvidenceScope(population_label=population_label, usage="evidence_only")


def _hash(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


def _external_scope(
    dataset: DatasetContract,
    raw: Mapping[str, Any],
    policy: UniversePolicy | None,
) -> ExternalAggregateScope:
    _exact_keys(raw, _EXTERNAL_KEYS, dataset.domain)
    if policy is not None:
        raise ValueError(
            f"{dataset.domain}: external_aggregate universe policy must be None"
        )
    scope = ExternalAggregateScope(
        venue_field=_text(
            raw["venue_field"], "population_scope.venue_field", dataset.domain
        ),
        venue_ids=_unique_venue_ids(
            raw["venue_ids"], "population_scope.venue_ids", dataset.domain
        ),
        population_label=_text(
            raw["population_label"],
            "population_scope.population_label",
            dataset.domain,
        ),
        method=_text(raw["method"], "population_scope.method", dataset.domain),
        unit=_text(raw["unit"], "population_scope.unit", dataset.domain),
    )
    if scope.venue_field not in dataset.grain:
        raise ValueError(
            f"{dataset.domain}: population_scope venue_field must be in dataset grain"
        )
    return scope


def _project_scope(
    dataset: DatasetContract,
    raw: Mapping[str, Any],
    policy: UniversePolicy | None,
) -> ProjectUniversePitScope:
    _exact_keys(raw, _PROJECT_KEYS, dataset.domain)
    if policy is None:
        raise ValueError(
            f"{dataset.domain}: project_universe_pit universe policy is required"
        )
    if not isinstance(policy, UniversePolicy):
        raise ValueError(
            f"{dataset.domain}: project_universe_pit policy must be UniversePolicy"
        )
    try:
        verify_universe_policy(policy)
    except UniverseDataError as exc:
        raise ValueError(
            f"{dataset.domain}: project_universe_pit policy is not a valid "
            "factory-owned snapshot"
        ) from exc
    scope = ProjectUniversePitScope(
        universe_policy_id=_text(
            raw["universe_policy_id"],
            "population_scope.universe_policy_id",
            dataset.domain,
        ),
        security_field=_text(
            raw["security_field"],
            "population_scope.security_field",
            dataset.domain,
        ),
        as_of_field=_text(
            raw["as_of_field"], "population_scope.as_of_field", dataset.domain
        ),
        as_of_role=_text(
            raw["as_of_role"], "population_scope.as_of_role", dataset.domain
        ),
    )
    if scope.security_field not in dataset.grain:
        raise ValueError(
            f"{dataset.domain}: population_scope security_field must be in dataset grain"
        )
    if scope.as_of_field not in dataset.grain:
        raise ValueError(
            f"{dataset.domain}: population_scope as_of_field must be in dataset grain"
        )
    if scope.as_of_role not in _AS_OF_ROLES:
        raise ValueError(
            f"{dataset.domain}: unsupported population_scope "
            f"as_of_role={scope.as_of_role!r}"
        )
    if scope.security_field == scope.as_of_field:
        raise ValueError(
            f"{dataset.domain}: population_scope security_field and as_of_field "
            "must be distinct"
        )
    if scope.security_field != "ts_code":
        raise ValueError(
            f"{dataset.domain}: project_universe_pit security_field must be 'ts_code'"
        )
    if scope.as_of_role != "observation_time":
        raise ValueError(
            f"{dataset.domain}: project_universe_pit as_of_role must be "
            "'observation_time'"
        )
    if scope.as_of_field != dataset.partition_by:
        raise ValueError(
            f"{dataset.domain}: project_universe_pit as_of_field must equal "
            "dataset partition_by"
        )
    if dataset.availability_policy.axis != "trading_day":
        raise ValueError(
            f"{dataset.domain}: project_universe_pit requires a trading_day "
            "availability axis"
        )
    if policy.eligibility_rule != "traded_on_observation_date":
        raise ValueError(
            f"{dataset.domain}: unsupported universe eligibility rule"
        )
    if scope.universe_policy_id != policy.policy_id:
        raise ValueError(
            f"{dataset.domain}: population_scope universe_policy_id "
            f"{scope.universe_policy_id!r} does not match injected policy "
            f"{policy.policy_id!r}"
        )
    if (
        isinstance(policy.policy_version, bool)
        or not isinstance(policy.policy_version, int)
        or policy.policy_version <= 0
    ):
        raise ValueError(
            f"{dataset.domain}: injected universe policy version must be positive"
        )
    if _SHA256_HEX.fullmatch(policy.config_hash) is None:
        raise ValueError(
            f"{dataset.domain}: injected universe policy hash must be 64 lowercase hex"
        )
    return scope


def bind_execution_contract(
    dataset: DatasetContract,
    spec: Mapping[str, Any],
    policy: UniversePolicy | None,
) -> DatasetExecutionContract:
    """Bind one frozen dataset to explicit population and policy snapshots."""

    if not isinstance(dataset, DatasetContract):
        raise ValueError("dataset must be a DatasetContract")
    spec = _mapping(spec, "dataset spec", dataset.domain)
    if "population_scope" not in spec:
        raise ValueError(f"{dataset.domain}: missing population_scope")
    raw = _mapping(spec["population_scope"], "population_scope", dataset.domain)
    if "kind" not in raw:
        raise ValueError(f"{dataset.domain}: missing population_scope keys: kind")
    kind = _text(raw["kind"], "population_scope.kind", dataset.domain)

    accepted_scope: PopulationScope
    if kind == "raw_evidence":
        accepted_scope = _raw_scope(dataset, raw, policy)
    elif kind == "external_aggregate":
        accepted_scope = _external_scope(dataset, raw, policy)
    elif kind == "project_universe_pit":
        accepted_scope = _project_scope(dataset, raw, policy)
    else:
        raise ValueError(
            f"{dataset.domain}: unsupported population_scope kind={kind!r}"
        )

    landing_scope = RawEvidenceScope("provider_response")
    if _SHA256_HEX.fullmatch(dataset.contract_hash) is None:
        raise ValueError(
            f"{dataset.domain}: dataset contract_hash must be 64 lowercase hex"
        )
    if _SHA256_HEX.fullmatch(dataset.config_hash) is None:
        raise ValueError(
            f"{dataset.domain}: dataset config_hash must be 64 lowercase hex"
        )
    policy_binding = (
        {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_config_hash": policy.config_hash,
        }
        if policy is not None
        else None
    )
    execution_hash = _hash(
        {
            "dataset_contract_hash": dataset.contract_hash,
            "dataset_config_hash": dataset.config_hash,
            "landing_scope": landing_scope.payload(),
            "accepted_scope": accepted_scope.payload(),
            "universe_policy": policy_binding,
        }
    )
    bound = object.__new__(DatasetExecutionContract)
    object.__setattr__(bound, "dataset", dataset)
    object.__setattr__(bound, "landing_scope", landing_scope)
    object.__setattr__(bound, "accepted_scope", accepted_scope)
    object.__setattr__(bound, "universe_policy", policy)
    object.__setattr__(bound, "execution_hash", execution_hash)
    return bound


__all__ = [
    "DatasetExecutionContract",
    "ExternalAggregateScope",
    "PopulationScopeKind",
    "ProjectUniversePitScope",
    "PopulationScope",
    "RawEvidenceScope",
    "bind_execution_contract",
]
