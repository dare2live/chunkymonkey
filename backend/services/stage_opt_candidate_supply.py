"""Config-owned stage-opt candidate supply contract."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "stage_opt_candidate_supply.yaml"
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


@dataclass(frozen=True)
class StageOptSupplySource:
    source_id: str
    table: str
    semantic_role: str
    eligibility: str
    pit_status: str
    grain: tuple[str, ...]
    required_joins: tuple[str, ...]
    allowed_consumers: tuple[str, ...]
    include_formula_ids: tuple[str, ...] | None = None

    def include_for_formula_filter(self, formula_ids: list[str] | tuple[str, ...] | None) -> bool:
        if self.include_formula_ids is None:
            return True
        if not formula_ids:
            return True
        requested = {str(formula_id) for formula_id in formula_ids}
        return bool(requested.intersection(self.include_formula_ids))

    def require_consumer(self, consumer: str) -> None:
        if consumer not in self.allowed_consumers:
            raise ValueError(
                f"stage-opt candidate supply source {self.source_id} does not allow consumer {consumer}"
            )

    def to_report(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "source_id": self.source_id,
            "table": self.table,
            "semantic_role": self.semantic_role,
            "eligibility": self.eligibility,
            "pit_status": self.pit_status,
            "grain": list(self.grain),
            "required_joins": list(self.required_joins),
            "allowed_consumers": list(self.allowed_consumers),
        }
        if self.include_formula_ids is not None:
            report["include_formula_ids"] = list(self.include_formula_ids)
        return report


@dataclass(frozen=True)
class StageOptCandidateSupplyContract:
    version: int
    allowed_stage_bins: tuple[str, ...]
    min_signals_per_key: int
    sources: tuple[StageOptSupplySource, ...]
    formula_scope_overrides: dict[str, tuple[str, ...]]

    @property
    def allowed_stage_set(self) -> set[str]:
        return set(self.allowed_stage_bins)

    def source(self, source_id: str) -> StageOptSupplySource:
        for item in self.sources:
            if item.source_id == source_id:
                return item
        raise KeyError(f"unknown stage-opt candidate supply source: {source_id}")

    def formula_ids_for_scope(self, scope: str) -> tuple[str, ...]:
        return self.formula_scope_overrides.get(scope, ())

    def formula_scopes(
        self,
        formula_id: str,
        *,
        live_formula_ids: tuple[str, ...],
        registered_formula_ids: tuple[str, ...],
    ) -> list[str]:
        scopes: list[str] = []
        if formula_id in live_formula_ids:
            scopes.append("live")
        elif formula_id in registered_formula_ids:
            scopes.append("registered_non_live")
        else:
            scopes.append("unregistered")
        for scope, formula_ids in sorted(self.formula_scope_overrides.items()):
            if formula_id in formula_ids:
                scopes.append(scope)
        return scopes

    def to_report(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "allowed_stage_bins": list(self.allowed_stage_bins),
            "readiness": {
                "min_signals_per_key": self.min_signals_per_key,
            },
            "sources": [source.to_report() for source in self.sources],
            "formula_scope_overrides": {
                scope: list(formula_ids)
                for scope, formula_ids in sorted(self.formula_scope_overrides.items())
            },
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: expected mapping")
    return loaded


def _require_str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path.name}: {key} must be a non-empty string")
    return value.strip()


def _require_str_tuple(raw: dict[str, Any], key: str, path: Path) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path.name}: {key} must be a non-empty list")
    items = tuple(str(item).strip() for item in value if str(item).strip())
    if len(items) != len(value):
        raise ValueError(f"{path.name}: {key} must contain only non-empty strings")
    return items


def _load_min_signals_per_key(raw_readiness: Any, path: Path) -> int:
    if not isinstance(raw_readiness, dict):
        raise ValueError(f"{path.name}: readiness must be a mapping")
    value = raw_readiness.get("min_signals_per_key")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path.name}: readiness.min_signals_per_key must be a positive integer")
    return value


def _load_sources(raw_sources: Any, path: Path) -> tuple[StageOptSupplySource, ...]:
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError(f"{path.name}: sources must be a non-empty list")
    sources: list[StageOptSupplySource] = []
    seen: set[str] = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise ValueError(f"{path.name}: each source must be a mapping")
        source_id = _require_str(raw_source, "source_id", path)
        if source_id in seen:
            raise ValueError(f"{path.name}: duplicate source_id {source_id}")
        seen.add(source_id)
        table = _require_str(raw_source, "table", path)
        if not _TABLE_NAME_RE.match(table):
            raise ValueError(f"{path.name}: invalid table name for {source_id}: {table}")
        include_formula_ids = None
        if raw_source.get("include_formula_ids") is not None:
            include_formula_ids = _require_str_tuple(raw_source, "include_formula_ids", path)
        sources.append(
            StageOptSupplySource(
                source_id=source_id,
                table=table,
                semantic_role=_require_str(raw_source, "semantic_role", path),
                eligibility=_require_str(raw_source, "eligibility", path),
                pit_status=_require_str(raw_source, "pit_status", path),
                grain=_require_str_tuple(raw_source, "grain", path),
                required_joins=_require_str_tuple(raw_source, "required_joins", path),
                allowed_consumers=_require_str_tuple(raw_source, "allowed_consumers", path),
                include_formula_ids=include_formula_ids,
            )
        )
    return tuple(sources)


def _load_formula_scope_overrides(raw_overrides: Any, path: Path) -> dict[str, tuple[str, ...]]:
    if raw_overrides is None:
        return {}
    if not isinstance(raw_overrides, dict):
        raise ValueError(f"{path.name}: formula_scope_overrides must be a mapping")
    overrides: dict[str, tuple[str, ...]] = {}
    for scope, formula_ids in raw_overrides.items():
        scope_name = str(scope).strip()
        if not scope_name:
            raise ValueError(f"{path.name}: formula scope name must be non-empty")
        if not isinstance(formula_ids, list):
            raise ValueError(f"{path.name}: formula scope {scope_name} must be a list")
        items = tuple(str(item).strip() for item in formula_ids if str(item).strip())
        if len(items) != len(formula_ids):
            raise ValueError(f"{path.name}: formula scope {scope_name} has empty formula id")
        overrides[scope_name] = items
    return overrides


def load_stage_opt_candidate_supply_contract(path: str | Path | None = None) -> StageOptCandidateSupplyContract:
    config_path = Path(path) if path is not None else CONFIG_PATH
    raw = _load_yaml(config_path)
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ValueError(f"{config_path.name}: version must be a positive integer")
    allowed_stage_bins = _require_str_tuple(raw, "allowed_stage_bins", config_path)
    return StageOptCandidateSupplyContract(
        version=version,
        allowed_stage_bins=allowed_stage_bins,
        min_signals_per_key=_load_min_signals_per_key(raw.get("readiness"), config_path),
        sources=_load_sources(raw.get("sources"), config_path),
        formula_scope_overrides=_load_formula_scope_overrides(
            raw.get("formula_scope_overrides"),
            config_path,
        ),
    )


DEFAULT_STAGE_OPT_CANDIDATE_SUPPLY_CONTRACT = load_stage_opt_candidate_supply_contract()
