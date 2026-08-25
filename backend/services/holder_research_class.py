"""Typed loader and matcher for holder_research_class policy.

Institution names live only in YAML. Vendor holder_type is not an input.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.research_facet import (
    FacetMatch,
    FacetPolicy,
    PresetSpec,
    ResearchFacetError,
    classify_facet_key,
    load_research_facet,
)

CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "holder_research_class.yaml"
)
NAMESPACE = "holder_research_class"
_DEFAULT_PRESET = "national_team_stabilizer"
_WIND_PRESET = "national_team_wind"
_NSSF_TAG = "nssf"
_PENSION_TAG = "pension"
_EXTRA_ROOT = frozenset({"include_nssf_in_default", "default_preset"})


class HolderResearchClassError(ResearchFacetError):
    """Policy YAML is missing, unknown, or violates fail-closed construction."""


@dataclass(frozen=True)
class HolderClassMatch:
    holder_name: str
    tags: tuple[str, ...]
    presets: tuple[str, ...]


@dataclass(frozen=True)
class HolderResearchClassPolicy:
    version: int
    definition_id: str
    namespace: str
    include_nssf_in_default: bool
    default_preset: str
    tags: tuple[Any, ...]
    entities: tuple[Any, ...]
    presets: tuple[PresetSpec, ...]
    config_hash: str
    path: Path
    facet: FacetPolicy

    def tag_ids(self) -> frozenset[str]:
        return self.facet.tag_ids()

    def preset_by_id(self, preset_id: str) -> PresetSpec:
        try:
            return self.facet.preset_by_id(preset_id)
        except ResearchFacetError as exc:
            raise HolderResearchClassError(str(exc)) from exc


def _holder_extras(mapping: Mapping[str, Any], presets: tuple[PresetSpec, ...]) -> Mapping[str, Any]:
    flag = mapping["include_nssf_in_default"]
    if not isinstance(flag, bool):
        raise HolderResearchClassError("include_nssf_in_default must be a boolean")
    default_preset = mapping["default_preset"]
    if not isinstance(default_preset, str) or not default_preset.strip():
        raise HolderResearchClassError("default_preset must be a non-empty string")
    preset_ids = {item.preset_id for item in presets}
    if default_preset not in preset_ids:
        raise HolderResearchClassError(f"default_preset unknown preset {default_preset!r}")
    default_tags = next(item.tags for item in presets if item.preset_id == default_preset)
    has_nssf = _NSSF_TAG in default_tags
    has_pension = _PENSION_TAG in default_tags
    if flag:
        if not has_nssf:
            raise HolderResearchClassError(
                "include_nssf_in_default is true but default preset omits nssf"
            )
    elif has_nssf or has_pension:
        raise HolderResearchClassError(
            "include_nssf_in_default is false; default preset must omit nssf and pension"
        )
    return {"include_nssf_in_default": flag, "default_preset": default_preset}


def load_holder_research_class(
    path: str | Path | None = None,
) -> HolderResearchClassPolicy:
    return _load_holder_research_class(str(Path(path or CONFIG_PATH).resolve()))


@lru_cache(maxsize=16)
def _load_holder_research_class(resolved: str) -> HolderResearchClassPolicy:
    facet = load_research_facet(
        resolved,
        expected_namespace=NAMESPACE,
        extra_root_keys=_EXTRA_ROOT,
        required_presets=frozenset({_DEFAULT_PRESET, _WIND_PRESET}),
        extras_hook=_holder_extras,
        error_cls=HolderResearchClassError,
    )
    extras = facet.extras
    return HolderResearchClassPolicy(
        version=facet.version,
        definition_id=facet.definition_id,
        namespace=facet.namespace,
        include_nssf_in_default=bool(extras["include_nssf_in_default"]),
        default_preset=str(extras["default_preset"]),
        tags=facet.tags,
        entities=facet.entities,
        presets=facet.presets,
        config_hash=facet.config_hash,
        path=facet.path,
        facet=facet,
    )


def _from_facet(match: FacetMatch) -> HolderClassMatch:
    return HolderClassMatch(
        holder_name=match.key, tags=match.tags, presets=match.presets
    )


def classify_holder_name(
    holder_name: str,
    *,
    policy: HolderResearchClassPolicy | None = None,
) -> HolderClassMatch:
    loaded = policy if policy is not None else load_holder_research_class()
    try:
        match = classify_facet_key(
            holder_name, loaded.facet, error_cls=HolderResearchClassError
        )
    except HolderResearchClassError:
        raise
    except ResearchFacetError as exc:
        raise HolderResearchClassError(str(exc)) from exc
    return _from_facet(match)


def holder_in_preset(
    holder_name: str,
    preset_id: str,
    *,
    policy: HolderResearchClassPolicy | None = None,
) -> bool:
    loaded = policy if policy is not None else load_holder_research_class()
    loaded.preset_by_id(preset_id)
    return preset_id in classify_holder_name(holder_name, policy=loaded).presets


__all__ = [
    "CONFIG_PATH",
    "NAMESPACE",
    "HolderClassMatch",
    "HolderResearchClassError",
    "HolderResearchClassPolicy",
    "classify_holder_name",
    "holder_in_preset",
    "load_holder_research_class",
]
