"""Generic YAML research-facet loader: tags + exact/regex entities + presets.

Institution and seat names live in YAML. This module has no CJK literals.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Literal

import yaml

_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_BASE_ROOT_KEYS = frozenset(
    {"version", "definition_id", "namespace", "tags", "entities", "presets"}
)
_TAG_KEYS = frozenset({"id", "label", "aggregatable"})
_ENTITY_KEYS = frozenset(
    {"match", "pattern", "tag", "evidence", "exclude", "alias", "alias_kind"}
)
_PRESET_KEYS = frozenset({"id", "tags"})
_MATCH_KINDS = frozenset({"exact", "regex"})
_ALIAS_KINDS = frozenset({"folk", "official"})


class ResearchFacetError(ValueError):
    """Policy YAML is missing, unknown, or violates fail-closed construction."""


def _mapping(value: Any, field: str, error_cls: type[ResearchFacetError]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_cls(f"{field} must be a mapping")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    field: str,
    error_cls: type[ResearchFacetError],
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise error_cls(f"{field} missing keys: {missing}")
    if unknown:
        raise error_cls(f"{field} unknown keys: {unknown}")


def _non_empty_text(
    value: Any,
    field: str,
    error_cls: type[ResearchFacetError],
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise error_cls(f"{field} must be a non-empty string without surrounding whitespace")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise error_cls(f"{field} contains malformed value {value!r}")
    return value


def _bool(value: Any, field: str, error_cls: type[ResearchFacetError]) -> bool:
    if isinstance(value, bool):
        return value
    raise error_cls(f"{field} must be a boolean")


def _http_urls(value: Any, field: str, error_cls: type[ResearchFacetError]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise error_cls(f"{field} must be a non-empty list")
    out: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        url = _non_empty_text(item, f"{field}[{index}]", error_cls)
        if not url.startswith(("https://", "http://")):
            raise error_cls(f"{field}[{index}] must be an http(s) URL")
        if url in seen:
            raise error_cls(f"{field} duplicate URL {url!r}")
        seen.add(url)
        out.append(url)
    return tuple(out)


def _id_list(value: Any, field: str, error_cls: type[ResearchFacetError]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise error_cls(f"{field} must be a non-empty list")
    out: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _non_empty_text(item, f"{field}[{index}]", error_cls, pattern=_ID)
        if text in seen:
            raise error_cls(f"{field} duplicate id {text!r}")
        seen.add(text)
        out.append(text)
    return tuple(out)


def _exclude_names(value: Any, field: str, error_cls: type[ResearchFacetError]) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise error_cls(f"{field} must be a list")
    out: set[str] = set()
    for index, item in enumerate(value):
        text = _non_empty_text(item, f"{field}[{index}]", error_cls)
        if text in out:
            raise error_cls(f"{field} duplicate {text!r}")
        out.add(text)
    return frozenset(out)


@dataclass(frozen=True)
class TagSpec:
    tag_id: str
    label: str
    aggregatable: bool


@dataclass(frozen=True)
class EntityRule:
    match: Literal["exact", "regex"]
    pattern: str
    tag: str
    evidence: tuple[str, ...]
    exclude: frozenset[str]
    alias: str | None
    alias_kind: str | None
    compiled: re.Pattern[str] | None


@dataclass(frozen=True)
class PresetSpec:
    preset_id: str
    tags: frozenset[str]


@dataclass(frozen=True)
class FacetMatch:
    key: str
    tags: tuple[str, ...]
    presets: tuple[str, ...]
    alias: str | None
    alias_kind: str | None


@dataclass(frozen=True)
class FacetPolicy:
    version: int
    definition_id: str
    namespace: str
    tags: tuple[TagSpec, ...]
    entities: tuple[EntityRule, ...]
    presets: tuple[PresetSpec, ...]
    extras: Mapping[str, Any]
    config_hash: str
    path: Path

    def tag_ids(self) -> frozenset[str]:
        return frozenset(item.tag_id for item in self.tags)

    def preset_by_id(self, preset_id: str) -> PresetSpec:
        for item in self.presets:
            if item.preset_id == preset_id:
                return item
        raise ResearchFacetError(f"unknown preset {preset_id!r}")

    def label_for(self, tag_id: str) -> str:
        for item in self.tags:
            if item.tag_id == tag_id:
                return item.label
        raise ResearchFacetError(f"unknown tag {tag_id!r}")


def _load_tags(raw: Any, error_cls: type[ResearchFacetError]) -> tuple[TagSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise error_cls("tags must be a non-empty list")
    out: list[TagSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        mapping = _mapping(item, f"tags[{index}]", error_cls)
        _exact_keys(mapping, _TAG_KEYS, f"tags[{index}]", error_cls)
        tag_id = _non_empty_text(mapping["id"], f"tags[{index}].id", error_cls, pattern=_ID)
        if tag_id in seen:
            raise error_cls(f"duplicate tag id {tag_id!r}")
        seen.add(tag_id)
        out.append(
            TagSpec(
                tag_id=tag_id,
                label=_non_empty_text(mapping["label"], f"tags[{index}].label", error_cls),
                aggregatable=_bool(
                    mapping["aggregatable"], f"tags[{index}].aggregatable", error_cls
                ),
            )
        )
    return tuple(out)


def _load_entities(
    raw: Any,
    tag_ids: frozenset[str],
    error_cls: type[ResearchFacetError],
) -> tuple[EntityRule, ...]:
    if not isinstance(raw, list) or not raw:
        raise error_cls("entities must be a non-empty list")
    out: list[EntityRule] = []
    seen_exact: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        mapping = _mapping(item, f"entities[{index}]", error_cls)
        unknown = sorted(set(mapping) - _ENTITY_KEYS)
        if unknown:
            raise error_cls(f"entities[{index}] unknown keys: {unknown}")
        missing = sorted({"match", "pattern", "tag", "evidence"} - set(mapping))
        if missing:
            raise error_cls(f"entities[{index}] missing keys: {missing}")
        kind = _non_empty_text(mapping["match"], f"entities[{index}].match", error_cls)
        if kind not in _MATCH_KINDS:
            raise error_cls(
                f"entities[{index}].match must be exact or regex, got {kind!r}"
            )
        pattern = _non_empty_text(mapping["pattern"], f"entities[{index}].pattern", error_cls)
        tag = _non_empty_text(
            mapping["tag"], f"entities[{index}].tag", error_cls, pattern=_ID
        )
        if tag not in tag_ids:
            raise error_cls(f"entities[{index}].tag unknown tag {tag!r}")
        alias = mapping.get("alias")
        alias_kind = mapping.get("alias_kind")
        if alias is None:
            if alias_kind is not None:
                raise error_cls(f"entities[{index}].alias_kind requires alias")
            alias_text = None
            alias_kind_text = None
        else:
            alias_text = _non_empty_text(alias, f"entities[{index}].alias", error_cls)
            alias_kind_text = _non_empty_text(
                alias_kind, f"entities[{index}].alias_kind", error_cls
            )
            if alias_kind_text not in _ALIAS_KINDS:
                raise error_cls(
                    f"entities[{index}].alias_kind must be folk or official"
                )
        compiled: re.Pattern[str] | None = None
        if kind == "regex":
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise error_cls(
                    f"entities[{index}].pattern is not a valid regex: {exc}"
                ) from exc
        else:
            key = (pattern, tag)
            if key in seen_exact:
                raise error_cls(f"entities[{index}] duplicate exact pattern {pattern!r}")
            seen_exact.add(key)
        out.append(
            EntityRule(
                match="regex" if kind == "regex" else "exact",
                pattern=pattern,
                tag=tag,
                evidence=_http_urls(
                    mapping["evidence"], f"entities[{index}].evidence", error_cls
                ),
                exclude=_exclude_names(
                    mapping.get("exclude"), f"entities[{index}].exclude", error_cls
                ),
                alias=alias_text,
                alias_kind=alias_kind_text,
                compiled=compiled,
            )
        )
    return tuple(out)


def _load_presets(
    raw: Any,
    tag_ids: frozenset[str],
    required_presets: frozenset[str],
    error_cls: type[ResearchFacetError],
) -> tuple[PresetSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise error_cls("presets must be a non-empty list")
    out: list[PresetSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        mapping = _mapping(item, f"presets[{index}]", error_cls)
        _exact_keys(mapping, _PRESET_KEYS, f"presets[{index}]", error_cls)
        preset_id = _non_empty_text(
            mapping["id"], f"presets[{index}].id", error_cls, pattern=_ID
        )
        if preset_id in seen:
            raise error_cls(f"duplicate preset id {preset_id!r}")
        seen.add(preset_id)
        tags = _id_list(mapping["tags"], f"presets[{index}].tags", error_cls)
        unknown = sorted(set(tags) - tag_ids)
        if unknown:
            raise error_cls(f"presets[{index}].tags unknown tag ids: {unknown}")
        out.append(PresetSpec(preset_id=preset_id, tags=frozenset(tags)))
    missing = sorted(required_presets - seen)
    if missing:
        raise error_cls(f"presets missing required ids: {missing}")
    return tuple(out)


ExtrasHook = Callable[[Mapping[str, Any], tuple[PresetSpec, ...]], Mapping[str, Any]]


def load_research_facet(
    path: str | Path,
    *,
    expected_namespace: str,
    extra_root_keys: frozenset[str] = frozenset(),
    required_presets: frozenset[str] = frozenset(),
    extras_hook: ExtrasHook | None = None,
    error_cls: type[ResearchFacetError] = ResearchFacetError,
) -> FacetPolicy:
    config_path = Path(path)
    raw_text = config_path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw_text)
    mapping = _mapping(loaded, expected_namespace, error_cls)
    _exact_keys(mapping, _BASE_ROOT_KEYS | extra_root_keys, expected_namespace, error_cls)
    namespace = _non_empty_text(mapping["namespace"], "namespace", error_cls, pattern=_ID)
    if namespace != expected_namespace:
        raise error_cls(f"namespace must be {expected_namespace!r}, got {namespace!r}")
    version = mapping["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise error_cls("version must be a positive integer")
    tags = _load_tags(mapping["tags"], error_cls)
    tag_ids = frozenset(item.tag_id for item in tags)
    entities = _load_entities(mapping["entities"], tag_ids, error_cls)
    presets = _load_presets(mapping["presets"], tag_ids, required_presets, error_cls)
    extras: Mapping[str, Any] = {}
    if extras_hook is not None:
        extras = extras_hook(mapping, presets)
    digest = sha256(raw_text.encode("utf-8")).hexdigest()
    definition_id = _non_empty_text(mapping["definition_id"], "definition_id", error_cls)
    config_hash = sha256(f"{definition_id}\n{digest}".encode("utf-8")).hexdigest()
    return FacetPolicy(
        version=version,
        definition_id=definition_id,
        namespace=namespace,
        tags=tags,
        entities=entities,
        presets=presets,
        extras=extras,
        config_hash=config_hash,
        path=config_path,
    )


def classify_facet_key(
    key: str,
    policy: FacetPolicy,
    *,
    extra_tags: frozenset[str] = frozenset(),
    error_cls: type[ResearchFacetError] = ResearchFacetError,
) -> FacetMatch:
    if not isinstance(key, str):
        raise error_cls("key must be a string")
    name = key.strip()
    if not name:
        return FacetMatch(key=key, tags=(), presets=(), alias=None, alias_kind=None)
    tags: set[str] = set()
    alias: str | None = None
    alias_kind: str | None = None
    for entity in policy.entities:
        if name in entity.exclude:
            continue
        hit = False
        if entity.match == "exact":
            hit = name == entity.pattern
        elif entity.compiled is not None and entity.compiled.fullmatch(name) is not None:
            hit = True
        if not hit:
            continue
        tags.add(entity.tag)
        if alias is None and entity.alias:
            alias = entity.alias
            alias_kind = entity.alias_kind
    tags.update(extra_tags)
    ordered_tags = tuple(sorted(tags))
    presets = tuple(
        item.preset_id for item in policy.presets if tags.intersection(item.tags)
    )
    return FacetMatch(
        key=name,
        tags=ordered_tags,
        presets=presets,
        alias=alias,
        alias_kind=alias_kind,
    )


def overlay_dict(match: FacetMatch, policy: FacetPolicy) -> dict[str, Any]:
    return {
        "namespace": policy.namespace,
        "definition_id": policy.definition_id,
        "config_hash": policy.config_hash,
        "tags": list(match.tags),
        "tag_labels": [policy.label_for(tag) for tag in match.tags],
        "presets": list(match.presets),
        "alias": match.alias,
        "alias_kind": match.alias_kind,
    }


__all__ = [
    "EntityRule",
    "FacetMatch",
    "FacetPolicy",
    "PresetSpec",
    "ResearchFacetError",
    "TagSpec",
    "classify_facet_key",
    "load_research_facet",
    "overlay_dict",
]
