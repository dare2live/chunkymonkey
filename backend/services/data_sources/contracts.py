"""Strict typed contract for the first accepted Tier0 dataset (``margin``).

Only formal publication fields are resolved here.  Legacy sync-runner knobs in
the same registry entry intentionally stay outside this contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

import yaml

from services.data_sources.availability import (
    AvailabilityPolicy,
    availability_policy_from_mapping,
)


REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "sync_registry.yaml"

_META_KEYS = frozenset(
    {
        "dataset_id",
        "contract_version",
        "schema_id",
        "schema_hash",
        "coverage_start",
        "canonical_table",
        "owner",
        "writer",
        "criticality",
        "failure_policy",
        "allowed_fallbacks",
        "consumers",
        "retention",
        "rebuild_policy",
        "retirement_condition",
    }
)
_OUTER_KEYS = (
    "source",
    "api",
    "target_db",
    "target_table",
    "grain",
    "partition_by",
    "available_after",
    "availability_policy",
    "batch_completeness",
)
_BATCH_KEYS = frozenset({"group_from", "required_groups", "required_groups_since"})
_GROUP_TRANSFORMS = frozenset({"identity", "exchange_suffix"})
_GROUP_TOKEN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_TABLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SCHEMA_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class BatchCompletenessContract:
    group_column: str
    group_transform: str
    required_groups: tuple[str, ...]
    required_groups_since: tuple[tuple[str, str], ...]

    def required_groups_for(self, partition: str) -> tuple[str, ...]:
        """Return the exact configured group set for one real date partition."""

        compact = _compact_date(
            partition, "partition", "batch_completeness"
        )
        groups = set(self.required_groups)
        groups.update(
            group
            for group, effective_date in self.required_groups_since
            if compact >= effective_date
        )
        return tuple(sorted(groups))

    def payload(self) -> dict[str, Any]:
        return {
            "group_from": {"column": self.group_column, "transform": self.group_transform},
            "required_groups": list(self.required_groups),
            "required_groups_since": dict(self.required_groups_since),
        }


@dataclass(frozen=True)
class DatasetContract:
    domain: str
    dataset_id: str
    contract_version: str
    schema_id: str
    schema_hash: str
    coverage_start: str
    owner: str
    writer: str
    criticality: str
    failure_policy: str
    allowed_fallbacks: tuple[str, ...]
    consumers: tuple[str, ...]
    retention: str
    rebuild_policy: str
    retirement_condition: str
    source: str
    api: str
    target_db: str
    canonical_table: str
    compatibility_table: str
    grain: tuple[str, ...]
    partition_by: str
    available_after: str
    availability_policy: AvailabilityPolicy
    batch_completeness: BatchCompletenessContract
    contract_hash: str
    config_hash: str


def _hash(value: Mapping[str, Any]) -> str:
    blob = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


def _mapping(value: Any, field: str, domain: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{domain}: {field} must be a mapping")
    return value


def _text(value: Any, field: str, domain: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{domain}: {field} must be a non-empty string")
    return value.strip()


def _table_identifier(value: Any, field: str, domain: str) -> str:
    identifier = _text(value, field, domain)
    if _TABLE_IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(f"{domain}: {field} must be a simple SQL table identifier")
    return identifier


def _schema_identifier(value: Any, field: str, domain: str) -> str:
    identifier = _text(value, field, domain)
    if _SCHEMA_IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(
            f"{domain}: {field} must be a stable dotted schema identifier"
        )
    return identifier


def _sha256_hex(value: Any, field: str, domain: str) -> str:
    digest = _text(value, field, domain)
    if _SHA256_HEX.fullmatch(digest) is None:
        raise ValueError(f"{domain}: {field} must be 64 lowercase hex characters")
    return digest


def _compact_date(value: Any, field: str, domain: str) -> str:
    text = _text(value, field, domain)
    try:
        if len(text) != 8 or not text.isdigit():
            raise ValueError
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(
            f"{domain}: {field} must be a valid YYYYMMDD date"
        ) from exc
    return text


def _groups(value: Any, field: str, domain: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{domain}: {field} must be a list")
    items = []
    for value_item in value:
        item = _text(value_item, field, domain)
        if value_item != item or _GROUP_TOKEN.fullmatch(item) is None:
            raise ValueError(
                f"{domain}: {field} group must be a canonical uppercase token"
            )
        items.append(item)
    if not items:
        raise ValueError(f"{domain}: {field} must be non-empty")
    if len(items) != len(set(items)):
        raise ValueError(f"{domain}: {field} contains duplicate values")
    return tuple(sorted(items))


def _texts(
    value: Any,
    field: str,
    domain: str,
    *,
    empty_ok: bool = False,
    sort: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{domain}: {field} must be a list")
    items = tuple(_text(item, field, domain) for item in value)
    if not items and not empty_ok:
        raise ValueError(f"{domain}: {field} must be non-empty")
    if len(items) != len(set(items)):
        raise ValueError(f"{domain}: {field} contains duplicate values")
    return tuple(sorted(items)) if sort else items


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], field: str, domain: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise ValueError(f"{domain}: missing {field} keys: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{domain}: unknown {field} keys: {', '.join(unknown)}")


def _batch(value: Any, domain: str, grain: tuple[str, ...]) -> BatchCompletenessContract:
    raw = _mapping(value, "batch_completeness", domain)
    _exact_keys(raw, _BATCH_KEYS, "batch_completeness", domain)
    group_from = _mapping(raw["group_from"], "batch_completeness.group_from", domain)
    _exact_keys(
        group_from,
        frozenset({"column", "transform"}),
        "batch_completeness.group_from",
        domain,
    )
    group_column = _text(
        group_from["column"], "batch_completeness.group_from.column", domain
    )
    if group_column not in grain:
        raise ValueError(f"{domain}: batch_completeness group column must be in grain")

    required = _groups(
        raw["required_groups"], "batch_completeness.required_groups", domain
    )
    since_raw = _mapping(
        raw["required_groups_since"], "batch_completeness.required_groups_since", domain
    )
    since_items = []
    for group, date in since_raw.items():
        group_text = _groups(
            [group], "batch_completeness.required_groups_since", domain
        )[0]
        date_text = _compact_date(
            date, f"batch_completeness.required_groups_since.{group_text}", domain
        )
        since_items.append((group_text, date_text))
    since = tuple(sorted(since_items))
    if set(required) & {group for group, _ in since}:
        raise ValueError(f"{domain}: a required group cannot also have a required_since date")
    transform = _text(
        group_from["transform"], "batch_completeness.group_from.transform", domain
    )
    if transform not in _GROUP_TRANSFORMS:
        raise ValueError(
            f"{domain}: unsupported batch group transform={transform!r}"
        )
    return BatchCompletenessContract(
        group_column=group_column,
        group_transform=transform,
        required_groups=required,
        required_groups_since=since,
    )


def dataset_contract_from_spec(domain: str, spec: Mapping[str, Any]) -> DatasetContract:
    """Resolve one already-defaulted registry domain into an immutable contract."""

    domain = _text(domain, "domain", "dataset_contract")
    spec = _mapping(spec, "domain spec", domain)
    missing = [key for key in (*_OUTER_KEYS, "dataset_contract") if key not in spec]
    if missing:
        raise ValueError(f"{domain}: missing outer contract fields: {', '.join(missing)}")

    meta = _mapping(spec["dataset_contract"], "dataset_contract", domain)
    _exact_keys(meta, _META_KEYS, "dataset_contract", domain)
    grain = _texts(spec["grain"], "grain", domain)
    partitions = _texts(spec["partition_by"], "partition_by", domain, empty_ok=True)
    if len(partitions) != 1:
        raise ValueError(f"{domain}: partition_by must contain exactly one column")
    if partitions[0] not in grain:
        raise ValueError(f"{domain}: partition_by column must be part of grain")

    version = meta["contract_version"]
    if isinstance(version, bool) or not isinstance(version, (str, int)) or not str(version).strip():
        raise ValueError(f"{domain}: contract_version must be a non-empty string or integer")
    failure_policy = _text(meta["failure_policy"], "failure_policy", domain)
    if failure_policy != "fail_closed":
        raise ValueError(f"{domain}: failure_policy must be fail_closed")
    fallbacks = _texts(
        meta["allowed_fallbacks"], "allowed_fallbacks", domain, empty_ok=True
    )
    if fallbacks:
        raise ValueError(f"{domain}: allowed_fallbacks must be empty")
    criticality = _text(meta["criticality"], "criticality", domain)
    if criticality != "blocking":
        raise ValueError(f"{domain}: criticality must be blocking for Tier0")

    batch = _batch(spec["batch_completeness"], domain, grain)
    legacy_available_after = _text(
        spec["available_after"], "available_after", domain
    )
    availability_policy = availability_policy_from_mapping(
        spec["availability_policy"], owner=domain
    )
    expected_legacy_hint = (
        "t+1"
        if availability_policy.rule in {
            "next_trading_session_at",
            "next_calendar_day_at",
        }
        else availability_policy.at.strftime("%H:%M")
    )
    if legacy_available_after != expected_legacy_hint:
        raise ValueError(
            f"{domain}: legacy available_after={legacy_available_after!r} conflicts "
            "with typed availability_policy; typed policy is the formal owner"
        )

    values: dict[str, Any] = {
        "domain": domain,
        "dataset_id": _text(meta["dataset_id"], "dataset_id", domain),
        "contract_version": str(version).strip(),
        "schema_id": _schema_identifier(meta["schema_id"], "schema_id", domain),
        "schema_hash": _sha256_hex(meta["schema_hash"], "schema_hash", domain),
        "coverage_start": _compact_date(
            meta["coverage_start"], "coverage_start", domain
        ),
        "owner": _text(meta["owner"], "owner", domain),
        "writer": _text(meta["writer"], "writer", domain),
        "criticality": criticality,
        "failure_policy": failure_policy,
        "allowed_fallbacks": fallbacks,
        "consumers": _texts(meta["consumers"], "consumers", domain, sort=True),
        "retention": _text(meta["retention"], "retention", domain),
        "rebuild_policy": _text(meta["rebuild_policy"], "rebuild_policy", domain),
        "retirement_condition": _text(
            meta["retirement_condition"], "retirement_condition", domain
        ),
        "source": _text(spec["source"], "source", domain),
        "api": _text(spec["api"], "api", domain),
        "target_db": _text(spec["target_db"], "target_db", domain),
        "canonical_table": _table_identifier(
            meta["canonical_table"], "canonical_table", domain
        ),
        # The outer target_table remains the legacy sync_runner write/read surface
        # during shadow migration.  Derive it once instead of duplicating the raw
        # table name inside dataset_contract.
        "compatibility_table": _table_identifier(
            spec["target_table"], "target_table", domain
        ),
        "grain": grain,
        "partition_by": partitions[0],
        "available_after": legacy_available_after,
        "availability_policy": availability_policy,
        "batch_completeness": batch,
    }
    if values["canonical_table"] == values["compatibility_table"]:
        raise ValueError(
            f"{domain}: canonical_table and target_table compatibility surface "
            "must have different single writers"
        )
    config_payload = {
        **values,
        "allowed_fallbacks": list(fallbacks),
        "consumers": list(values["consumers"]),
        "grain": list(grain),
        "availability_policy": values["availability_policy"].payload(),
        "batch_completeness": batch.payload(),
    }
    config_hash = _hash(config_payload)
    contract_hash = _hash(
        {
            "dataset_id": values["dataset_id"],
            "contract_version": values["contract_version"],
            "config_hash": config_hash,
        }
    )
    return DatasetContract(**values, contract_hash=contract_hash, config_hash=config_hash)


def load_dataset_contract(
    domain: str, registry_path: Path | None = None
) -> DatasetContract:
    """Load one contract, inheriting only ``defaults.target_db``."""

    path = Path(registry_path or REGISTRY_PATH)
    registry = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "registry", str(path))
    domains = _mapping(registry.get("domains"), "domains", str(path))
    raw_spec = domains.get(domain)
    if not isinstance(raw_spec, Mapping):
        raise KeyError(f"{path}: unknown dataset contract domain {domain!r}")
    spec = dict(raw_spec)
    defaults = registry.get("defaults")
    if "target_db" not in spec and isinstance(defaults, Mapping):
        if "target_db" in defaults:
            spec["target_db"] = defaults["target_db"]
    return dataset_contract_from_spec(domain, spec)


__all__ = [
    "BatchCompletenessContract",
    "DatasetContract",
    "dataset_contract_from_spec",
    "load_dataset_contract",
]
