"""Vendor scope registry — loader for ``backend/config/vendor_scope.yaml`` (v2).

**What this file is for**: registering, for every (source, api) taking part in
data acquisition, how each *out-of-scope security category* (today: B股/EQB,
业主 2026-09-12 原话「不用管B股，以后也不做，获取完的数据删除清理干净」) is
handled. This module only describes what to check and what to compare — it
does not decide what to run or in what order (红线 11: 禁 plugin bus / 通用
DAG / YAML DSL). Whether/when to fetch is still entirely owned by
``sync_registry.yaml`` + ``sync_runner``; this module answers exactly one
question for a caller that already decided to fetch: *does this vendor row
need to be excluded, and if so, how*.

**v1 → v2**: v1 (``{version: 1, miaoxiang: {block_trade: {...}}}``) could only
describe a single (vendor, api) pair — its loader
(``sources/miaoxiang.py::load_vendor_scope``, now removed) hard-coded the api
key set to ``{'block_trade'}``, so registering a second domain meant editing
code. v2 generalizes the shape to cover every acquiring (source, api) in the
project, keyed as ``"<source>.<api>"``.

**Design provenance**: this loader implements the registration format and L1–L12
rules from the r1 spec
(``scratchpad/b_share_exclusion_r1.md`` §3, 2026-09-12) — that document is the
authority for the *shape*; this module is its literal implementation, not a
reinterpretation.

**Layer boundary (C1, spec §2.1)**: the landing side of this project only
ever acts in one of two ways on a vendor row — *request-side* (any project
vocabulary is fine; the request is already ours to shape:
``request_exclude`` / ``request_enumeration``), or *response-side*, and only
on a genuine **vendor** field (``response_exclude``, field name confined to
security-code-identity columns' complement — see L10). Project vocabulary
like a category's ``code_patterns`` (e.g. B股的 900/200 前缀) is **not**
allowed to leak into an adapter or ``sync_runner`` as a landing-time filter —
that is exactly the "range-outside category" vs "universe/stock-pool policy"
line this project has had to redraw five times after some filter's scope
quietly grew past its stated intent
(``feedback-warn-only-degrades-to-warn-nothing.md``). ``out_of_scope_code_patterns()``
below exists only for the canonical/audit layer (a later slice); a static
test in ``test_vendor_scope.py`` enforces that no adapter or ``sync_runner``
module imports it.

**Mutual exclusion (C2, spec §2.2)**: a category's ``code_patterns`` prefixes
must never overlap ``universe_rules.yaml``'s board-prefix vocabulary
(``include.board_prefixes`` ∪ ``exclude.excluded_boards``) — that file is a
*policy* (versioned, hashed, changing it bumps ``policy_version``); this file
registers a *fact* (no version, enforced as a 0-row invariant everywhere,
including raw). L7 below is the loader-side half of that boundary.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import yaml

from services.universe import UNIVERSE_POLICY

# backend/config/vendor_scope.yaml — this module lives at
# backend/services/data_sources/vendor_scope.py, three parents up is backend/.
_VENDOR_SCOPE_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "vendor_scope.yaml"

_TOP_LEVEL_KEYS = frozenset(
    {"version", "security_code_columns", "out_of_scope_classes", "dispositions", "non_registry_sources"}
)
_COLUMN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CLASS_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PREFIX_RE = re.compile(r"^\d{2,3}$")
_VALID_SUFFIXES = frozenset({"SH", "SZ", "BJ"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DISPOSITION_KEY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_-]+$")
_CODE_PREFIX_LIKE_RE = re.compile(r"^\d{1,3}$")
_CODE_FULL_LIKE_RE = re.compile(r"^\d{6}(\.[A-Z]{2})?$")
_CODE_LIKE_FIELD_NAMES = frozenset({"SECUCODE", "SECURITY_CODE", "SYMBOL", "CODE"})

_VALID_MODES = frozenset(
    {"request_exclude", "response_exclude", "no_vendor_axis", "request_enumeration", "population_disjoint"}
)
_MODE_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "request_exclude": frozenset({"mode", "filters"}),
    "response_exclude": frozenset({"mode", "field", "values"}),
    "no_vendor_axis": frozenset({"mode", "checked_at", "evidence"}),
    "request_enumeration": frozenset({"mode", "code_source"}),
    "population_disjoint": frozenset({"mode", "why"}),
}


class VendorScopeError(LookupError):
    """Raised by :func:`vendor_exclusions` when ``(source, api)`` has no
    registered disposition. Never silently treated as "no exclusions" —
    an unregistered acquiring path is an unaddressed unknown, not a green
    field (宪法红线3: 缺失只能传播为缺失)."""


@dataclass(frozen=True)
class CodePattern:
    """One out-of-scope code segment, e.g. B股 900.SH / 200.SZ."""

    prefix: str
    suffix: str


@dataclass(frozen=True)
class OutOfScopeClass:
    ruling: str
    code_patterns: tuple[CodePattern, ...]


@dataclass(frozen=True)
class Disposition:
    """A single (source.api, class) → handling-mode registration. Only the
    fields relevant to ``mode`` are populated; the rest keep their default."""

    mode: str
    filters: tuple[str, ...] = ()
    field_name: str | None = None
    values: frozenset[str] = field(default_factory=frozenset)
    checked_at: str | None = None
    evidence: str | None = None
    code_source: str | None = None
    why: str | None = None


@dataclass(frozen=True)
class VendorScope:
    security_code_columns: frozenset[str]
    out_of_scope_classes: Mapping[str, OutOfScopeClass]
    dispositions: Mapping[str, Mapping[str, Disposition]]
    non_registry_sources: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class VendorExclusions:
    """What an adapter should do for one (source, api) call. Empty tuples mean
    "nothing to do here" (``no_vendor_axis`` / ``request_enumeration`` /
    ``population_disjoint`` all resolve to this) — the disposition still had
    to exist (see :class:`VendorScopeError`), it just doesn't act on landing."""

    request_filters: tuple[str, ...]
    response_excludes: tuple[tuple[str, frozenset[str]], ...]


def _is_valid_calendar_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _resolve_code_source(code_source: str, *, key: str, class_name: str) -> Any:
    """Resolve a dotted path (``pkg.mod.Class.ATTR`` or ``pkg.mod:Class.ATTR``)
    to an existing object, so a ``request_enumeration`` disposition can never
    point at a code path that doesn't exist (L9 dangling-reference check)."""
    module_path, sep, attr_path = code_source.partition(":")
    if sep:
        try:
            obj: Any = importlib.import_module(module_path)
        except ImportError as exc:
            raise ValueError(
                f"vendor_scope: L9 request_enumeration dispositions[{key!r}][{class_name!r}]"
                f".code_source {code_source!r} module {module_path!r} is not importable"
            ) from exc
        attr_parts = attr_path.split(".") if attr_path else []
    else:
        parts = code_source.split(".")
        obj = None
        last_exc: Exception | None = None
        idx = len(parts)
        while idx > 0:
            candidate = ".".join(parts[:idx])
            try:
                obj = importlib.import_module(candidate)
                break
            except ImportError as exc:
                last_exc = exc
                idx -= 1
        if obj is None:
            raise ValueError(
                f"vendor_scope: L9 request_enumeration dispositions[{key!r}][{class_name!r}]"
                f".code_source {code_source!r} does not resolve to any importable module prefix"
            ) from last_exc
        attr_parts = parts[idx:]

    for part in attr_parts:
        if not hasattr(obj, part):
            raise ValueError(
                f"vendor_scope: L9 request_enumeration dispositions[{key!r}][{class_name!r}]"
                f".code_source {code_source!r}: {obj!r} has no attribute {part!r}"
            )
        obj = getattr(obj, part)
    return obj


def load_vendor_scope(path: Path | str | None = None) -> VendorScope:
    """Load and validate ``backend/config/vendor_scope.yaml`` (v2).

    Fail-closed on every unrecognized shape (未知键 / 悬空引用 / 违反 C2 互斥
    一律 ``ValueError``, 不部分生效, 不静默忽略) — see the L1–L10 rule table in
    ``scratchpad/b_share_exclusion_r1.md`` §3.1. L11/L12 (registry
    cross-checks) are deliberately **not** here: they need ``sync_registry.yaml``
    which this loader has no reason to depend on, and per spec they are static
    tests, not loader behavior.

    Every ``ValueError`` message is prefixed ``vendor_scope: L#`` so a failure
    names exactly which rule it violated.
    """
    cfg_path = Path(path) if path is not None else _VENDOR_SCOPE_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # L1
    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise ValueError(
            f"vendor_scope: L1 top-level keys must be exactly {sorted(_TOP_LEVEL_KEYS)}, "
            f"got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )

    # L2
    if raw.get("version") != 2:
        raise ValueError(f"vendor_scope: L2 version must be 2, got {raw.get('version')!r}")

    # L3
    code_cols_raw = raw.get("security_code_columns")
    if not isinstance(code_cols_raw, list) or not code_cols_raw:
        raise ValueError(
            f"vendor_scope: L3 security_code_columns must be a non-empty list, got {code_cols_raw!r}"
        )
    seen_cols: set[str] = set()
    for col in code_cols_raw:
        if not isinstance(col, str) or not _COLUMN_NAME_RE.match(col):
            raise ValueError(
                f"vendor_scope: L3 security_code_columns entries must match "
                f"{_COLUMN_NAME_RE.pattern!r}, got {col!r}"
            )
        if col in seen_cols:
            raise ValueError(f"vendor_scope: L3 security_code_columns has duplicate entry {col!r}")
        seen_cols.add(col)
    security_code_columns = frozenset(code_cols_raw)

    # L4
    classes_raw = raw.get("out_of_scope_classes")
    if not isinstance(classes_raw, dict) or not classes_raw:
        raise ValueError(
            f"vendor_scope: L4 out_of_scope_classes must be a non-empty mapping, got {classes_raw!r}"
        )
    for class_name in classes_raw:
        if not isinstance(class_name, str) or not _CLASS_NAME_RE.match(class_name):
            raise ValueError(
                f"vendor_scope: L4 out_of_scope_classes key must match "
                f"{_CLASS_NAME_RE.pattern!r}, got {class_name!r}"
            )

    allowed_prefixes = set(UNIVERSE_POLICY.allowed_board_prefixes) | set(dict(UNIVERSE_POLICY.excluded_boards))

    out_of_scope_classes: dict[str, OutOfScopeClass] = {}
    for class_name, class_cfg in classes_raw.items():
        # L5
        if not isinstance(class_cfg, dict) or set(class_cfg) != {"ruling", "code_patterns"}:
            raise ValueError(
                f"vendor_scope: L5 out_of_scope_classes.{class_name} keys must be exactly "
                f"{{'ruling', 'code_patterns'}}, got "
                f"{sorted(class_cfg) if isinstance(class_cfg, dict) else type(class_cfg).__name__}"
            )
        ruling = class_cfg["ruling"]
        if not isinstance(ruling, str) or not ruling.strip():
            raise ValueError(
                f"vendor_scope: L5 out_of_scope_classes.{class_name}.ruling must be a non-empty string"
            )

        # L6
        patterns_raw = class_cfg["code_patterns"]
        if not isinstance(patterns_raw, list) or not patterns_raw:
            raise ValueError(
                f"vendor_scope: L6 out_of_scope_classes.{class_name}.code_patterns must be a non-empty list"
            )
        seen_pairs: set[tuple[str, str]] = set()
        patterns: list[CodePattern] = []
        for item in patterns_raw:
            if not isinstance(item, dict) or set(item) != {"prefix", "suffix"}:
                raise ValueError(
                    f"vendor_scope: L6 out_of_scope_classes.{class_name} code_patterns item keys must "
                    f"be exactly {{'prefix', 'suffix'}}, got "
                    f"{sorted(item) if isinstance(item, dict) else type(item).__name__}"
                )
            prefix = item["prefix"]
            suffix = item["suffix"]
            if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix):
                raise ValueError(
                    f"vendor_scope: L6 out_of_scope_classes.{class_name} code_patterns.prefix must "
                    f"match {_PREFIX_RE.pattern!r}, got {prefix!r}"
                )
            if suffix not in _VALID_SUFFIXES:
                raise ValueError(
                    f"vendor_scope: L6 out_of_scope_classes.{class_name} code_patterns.suffix must be "
                    f"one of {sorted(_VALID_SUFFIXES)}, got {suffix!r}"
                )
            pair = (prefix, suffix)
            if pair in seen_pairs:
                raise ValueError(
                    f"vendor_scope: L6 out_of_scope_classes.{class_name} has duplicate code_patterns "
                    f"entry {pair!r}"
                )
            seen_pairs.add(pair)

            # L7 (C2 mutual exclusion): a range-outside category's prefix — at
            # any of its 1/2/3-digit lengths — must never collide with
            # universe_rules.yaml's board-prefix vocabulary. Colliding would
            # let a stock-pool *policy* prefix (versioned, hashed) masquerade
            # as a permanent range-outside *fact* (unversioned, enforced
            # everywhere including raw) — exactly the boundary §2.2 draws.
            for candidate in {prefix[:1], prefix[:2], prefix}:
                if candidate in allowed_prefixes:
                    raise ValueError(
                        f"vendor_scope: L7 out_of_scope_classes.{class_name} code_patterns prefix "
                        f"{prefix!r} collides with universe_rules.yaml board prefix {candidate!r} "
                        "(range-outside categories must not overlap universe include/exclude board "
                        "prefixes — see spec §2.2 C2)"
                    )
            patterns.append(CodePattern(prefix=prefix, suffix=suffix))
        out_of_scope_classes[class_name] = OutOfScopeClass(ruling=ruling.strip(), code_patterns=tuple(patterns))

    # L8 + L9 + L10
    dispositions_raw = raw.get("dispositions")
    if not isinstance(dispositions_raw, dict):
        raise ValueError(f"vendor_scope: L8 dispositions must be a mapping, got {type(dispositions_raw).__name__}")

    forbidden_response_fields = {c.upper() for c in security_code_columns} | _CODE_LIKE_FIELD_NAMES

    dispositions: dict[str, dict[str, Disposition]] = {}
    for key, per_class_raw in dispositions_raw.items():
        if not isinstance(key, str) or not _DISPOSITION_KEY_RE.match(key):
            raise ValueError(
                f"vendor_scope: L8 dispositions key must match {_DISPOSITION_KEY_RE.pattern!r}, got {key!r}"
            )
        if not isinstance(per_class_raw, dict) or not per_class_raw:
            raise ValueError(
                f"vendor_scope: L8 dispositions[{key!r}] must be a non-empty mapping, got {per_class_raw!r}"
            )
        built: dict[str, Disposition] = {}
        for class_name, dispo_cfg in per_class_raw.items():
            if class_name not in out_of_scope_classes:
                raise ValueError(
                    f"vendor_scope: L8 dispositions[{key!r}] references undeclared class {class_name!r}"
                )
            if not isinstance(dispo_cfg, dict) or "mode" not in dispo_cfg:
                raise ValueError(
                    f"vendor_scope: L9 dispositions[{key!r}][{class_name!r}] must be a mapping with a "
                    "'mode' key"
                )
            mode = dispo_cfg.get("mode")
            if mode not in _VALID_MODES:
                raise ValueError(
                    f"vendor_scope: L9 dispositions[{key!r}][{class_name!r}] unknown mode {mode!r}; "
                    f"known={sorted(_VALID_MODES)}"
                )
            got_keys = set(dispo_cfg)
            expected_keys = _MODE_REQUIRED_KEYS[mode]
            if got_keys != expected_keys:
                raise ValueError(
                    f"vendor_scope: L9 {mode} dispositions[{key!r}][{class_name!r}] keys must be exactly "
                    f"{sorted(expected_keys)}, got {sorted(got_keys)}"
                )

            if mode == "request_exclude":
                filters = dispo_cfg["filters"]
                if (
                    not isinstance(filters, list)
                    or not filters
                    or not all(isinstance(f, str) and f.strip() for f in filters)
                ):
                    raise ValueError(
                        f"vendor_scope: L9 request_exclude dispositions[{key!r}][{class_name!r}].filters "
                        "must be a non-empty list of non-empty strings"
                    )
                built[class_name] = Disposition(mode=mode, filters=tuple(f.strip() for f in filters))

            elif mode == "response_exclude":
                field_name = dispo_cfg["field"]
                if not isinstance(field_name, str) or not field_name.strip():
                    raise ValueError(
                        f"vendor_scope: L9 response_exclude dispositions[{key!r}][{class_name!r}].field "
                        "must be a non-empty string"
                    )
                field_name = field_name.strip()
                # L10: field must be a genuine vendor category field, not a
                # security-code-identity column (that would degrade this into
                # a disguised per-code exclusion list — exactly what C1 bans).
                if field_name.upper() in forbidden_response_fields:
                    raise ValueError(
                        f"vendor_scope: L10 response_exclude dispositions[{key!r}][{class_name!r}].field "
                        f"{field_name!r} is a security-code-identity column, not a vendor category field"
                    )
                values = dispo_cfg["values"]
                if (
                    not isinstance(values, list)
                    or not values
                    or not all(isinstance(v, str) and v.strip() for v in values)
                ):
                    raise ValueError(
                        f"vendor_scope: L9 response_exclude dispositions[{key!r}][{class_name!r}].values "
                        "must be a non-empty list of non-empty strings"
                    )
                if len(set(values)) != len(values):
                    raise ValueError(
                        f"vendor_scope: L10 response_exclude dispositions[{key!r}][{class_name!r}].values "
                        f"must be unique, got {values!r}"
                    )
                for v in values:
                    if _CODE_PREFIX_LIKE_RE.match(v) or _CODE_FULL_LIKE_RE.match(v):
                        raise ValueError(
                            f"vendor_scope: L10 response_exclude dispositions[{key!r}][{class_name!r}]"
                            f".values entry {v!r} looks like a security code/prefix, not a vendor "
                            "category value"
                        )
                built[class_name] = Disposition(mode=mode, field_name=field_name, values=frozenset(values))

            elif mode == "no_vendor_axis":
                checked_at = dispo_cfg["checked_at"]
                if (
                    not isinstance(checked_at, str)
                    or not _DATE_RE.match(checked_at)
                    or not _is_valid_calendar_date(checked_at)
                ):
                    raise ValueError(
                        f"vendor_scope: L9 no_vendor_axis dispositions[{key!r}][{class_name!r}]"
                        f".checked_at must be a valid YYYY-MM-DD date, got {checked_at!r}"
                    )
                evidence = dispo_cfg["evidence"]
                if not isinstance(evidence, str) or not evidence.strip():
                    raise ValueError(
                        f"vendor_scope: L9 no_vendor_axis dispositions[{key!r}][{class_name!r}]"
                        ".evidence must be a non-empty string"
                    )
                built[class_name] = Disposition(
                    mode=mode, checked_at=checked_at, evidence=evidence.strip()
                )

            elif mode == "request_enumeration":
                code_source = dispo_cfg["code_source"]
                if not isinstance(code_source, str) or not code_source.strip():
                    raise ValueError(
                        f"vendor_scope: L9 request_enumeration dispositions[{key!r}][{class_name!r}]"
                        ".code_source must be a non-empty string"
                    )
                code_source = code_source.strip()
                _resolve_code_source(code_source, key=key, class_name=class_name)
                built[class_name] = Disposition(mode=mode, code_source=code_source)

            else:  # population_disjoint
                why = dispo_cfg["why"]
                if not isinstance(why, str) or not why.strip():
                    raise ValueError(
                        f"vendor_scope: L9 population_disjoint dispositions[{key!r}][{class_name!r}]"
                        ".why must be a non-empty string"
                    )
                built[class_name] = Disposition(mode=mode, why=why.strip())

        dispositions[key] = built

    # non_registry_sources — presence + shape only (L1 requires the key;
    # nothing in L1-L12 imposes a stricter per-entry contract than "a mapping
    # of string key to mapping"). Acquisition paths outside sync_registry.yaml
    # (aif10 scripts) are recorded here so L11's dead-reference half can treat
    # them as legitimate dispositions targets without requiring one.
    non_registry_raw = raw.get("non_registry_sources")
    if not isinstance(non_registry_raw, dict):
        raise ValueError(
            f"vendor_scope: non_registry_sources must be a mapping, got {type(non_registry_raw).__name__}"
        )
    non_registry_sources: dict[str, dict[str, Any]] = {}
    for nr_key, nr_cfg in non_registry_raw.items():
        if not isinstance(nr_key, str) or not isinstance(nr_cfg, dict):
            raise ValueError(
                f"vendor_scope: non_registry_sources[{nr_key!r}] must map a string key to a mapping"
            )
        non_registry_sources[nr_key] = dict(nr_cfg)

    return VendorScope(
        security_code_columns=security_code_columns,
        out_of_scope_classes=out_of_scope_classes,
        dispositions=dispositions,
        non_registry_sources=non_registry_sources,
    )


def vendor_exclusions(source: str, api: str, *, scope: VendorScope | None = None) -> VendorExclusions:
    """What an adapter must do for this ``(source, api)`` call.

    Fail-closed by design: an unregistered ``(source, api)`` raises
    :class:`VendorScopeError` rather than returning an "empty" result that a
    caller could mistake for "nothing to exclude" — see the class docstring.
    Callers that want a domain-specific exception (e.g. miaoxiang's
    ``MiaoxiangSourceError``) catch this and re-raise.
    """
    resolved = scope if scope is not None else load_vendor_scope()
    key = f"{source}.{api}"
    per_class = resolved.dispositions.get(key)
    if per_class is None:
        raise VendorScopeError(f"vendor_scope: no disposition registered for {key!r}")

    request_filters: list[str] = []
    response_excludes: list[tuple[str, frozenset[str]]] = []
    for dispo in per_class.values():
        if dispo.mode == "request_exclude":
            request_filters.extend(dispo.filters)
        elif dispo.mode == "response_exclude":
            assert dispo.field_name is not None  # guaranteed by load_vendor_scope
            response_excludes.append((dispo.field_name, dispo.values))
        # no_vendor_axis / request_enumeration / population_disjoint: the
        # disposition exists (checked above) but the adapter has nothing to
        # do at landing time for it.
    return VendorExclusions(
        request_filters=tuple(request_filters),
        response_excludes=tuple(response_excludes),
    )


def out_of_scope_code_patterns(*, scope: VendorScope | None = None) -> tuple[CodePattern, ...]:
    """All registered range-outside code patterns (e.g. B股 900.SH/200.SZ),
    for the canonical/audit layer only (C1). **Adapters and ``sync_runner``
    must never import this** — a static test in ``test_vendor_scope.py``
    enforces that boundary by grep."""
    resolved = scope if scope is not None else load_vendor_scope()
    patterns: list[CodePattern] = []
    for cls in resolved.out_of_scope_classes.values():
        patterns.extend(cls.code_patterns)
    return tuple(patterns)


def landing_tables_with_no_vendor_axis(
    registry: Mapping[str, Any], *, scope: VendorScope | None = None
) -> frozenset[tuple[str, str]]:
    """``(target_db, target_table)`` pairs for every registry domain whose
    ``(source, api)`` carries a ``no_vendor_axis`` disposition for any class —
    the only tables a runtime out-of-scope-row invariant (a later slice) may
    observe without failing. ``registry`` is the parsed ``sync_registry.yaml``
    mapping (top-level ``sources``/``domains`` keys)."""
    resolved = scope if scope is not None else load_vendor_scope()
    no_axis_keys = {
        key
        for key, per_class in resolved.dispositions.items()
        if any(dispo.mode == "no_vendor_axis" for dispo in per_class.values())
    }
    domains = registry.get("domains") or {}
    sources_cfg = registry.get("sources") or {}
    tables: set[tuple[str, str]] = set()
    for domain_cfg in domains.values():
        if not isinstance(domain_cfg, Mapping):
            continue
        source = domain_cfg.get("source")
        api = domain_cfg.get("api")
        if f"{source}.{api}" not in no_axis_keys:
            continue
        table = domain_cfg.get("target_table")
        db = domain_cfg.get("target_db") or (sources_cfg.get(source) or {}).get("target_db")
        if db and table:
            tables.add((str(db), str(table)))
    return frozenset(tables)


__all__ = [
    "CodePattern",
    "Disposition",
    "OutOfScopeClass",
    "VendorExclusions",
    "VendorScope",
    "VendorScopeError",
    "landing_tables_with_no_vendor_axis",
    "load_vendor_scope",
    "out_of_scope_code_patterns",
    "vendor_exclusions",
]
