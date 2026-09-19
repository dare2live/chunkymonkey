"""Typed loader for ``backend/config/stock_st_acquire.yaml`` (ST domain, v2).

**What this file is for**: the ST domain's ``st_origin`` enrichment column
(v2 contract) needs a machine-checkable set of legal values (which
provider/derivation path produced this membership row, whether it reports a
security name, and what exchange coverage it can answer for) plus the
snapshot-day attribution parameters and the one-time historical
source→origin backfill map. None of this belongs in ``stock_st_schema.py``'s
``_SCHEMA_PAYLOAD`` — putting it there would change ``SCHEMA_HASH`` and force
a restamp for no benefit. It also does not belong hardcoded in Python: these
are exactly the values 业主 2026-09-16 明令 must live in typed YAML
(thresholds, dates, windows, provenance labels, name lists, mappings,
timezones) — a module here only *reads and enforces* the config, it does not
restate it.

Fail-closed by construction (project rule 11: 规则进 typed YAML, 未知键
fail-closed): any unrecognized top-level key, unknown ``kind``, a
``coverage`` reference that does not name a registered coverage group, a
malformed timezone/cutoff, or a ``backfill_origin_by_source`` value that is
not itself a registered ``st_origin`` key raises ``ValueError`` — never
partially applied, never silently ignored.

ST has no ``unknown`` kind (unlike daily's ``pre_close_origin``): a row's mere
presence in canonical already asserts ST membership. "we don't know if this
is ST" means the row does not exist, not that it exists with an unknown
label — so ``st_origin.*.kind`` only ever takes ``provider``/``derived``.

This module also owns the row-level enforcement built on top of the loaded
rules: :func:`build_st_origin_validator` returns the callable that
``security_day_partition.py::_candidate_rows`` invokes (via
``SecurityDayDomain.enrichment_validator``) once a row's ``st_origin`` has
been assembled. It raises
``services.data_sources.security_day_partition.SecurityDayValidationError``
with code ``INVALID_ENRICHMENT`` on either violation:

  1. ``st_origin`` not in the registered value set.
  2. ``reports_name`` (declared per ``st_origin``) disagrees with whether
     ``name`` is ``None`` for this row — an origin that claims to report a
     name must always supply one, and one that does not must never supply a
     stray value that would misrepresent its confidence.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from services.data_sources.security_day_partition import SecurityDayValidationError

# backend/services/data_sources/stock_st_acquire_rules.py — two parents up is
# backend/.
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "stock_st_acquire.yaml"

_TOP_LEVEL_KEYS = frozenset(
    {"version", "st_origin", "coverage", "membership_labels", "name_snapshot", "reservoir",
     "backfill_origin_by_source"}
)
_ORIGIN_ENTRY_KEYS = frozenset({"kind", "reports_name", "coverage", "note"})
# ST 没有 "unknown" —— 一行存在就肯定是 ST, 与 daily 的 pre_close_origin 不同
# (那里 "不知道" 本身是一种合法状态, 因为一行 OHLCV 总归存在)。
_ALLOWED_KINDS = frozenset({"provider", "derived"})
_COVERAGE_ENTRY_KEYS = frozenset({"exchanges"})
_MEMBERSHIP_LABEL_KEYS = frozenset({"type", "type_name"})
_NAME_SNAPSHOT_KEYS = frozenset({"table", "timezone", "attribution_cutoff_local"})
_RESERVOIR_KEYS = frozenset({"table", "isst_field", "isst_true_value"})
_EXCHANGE_RE = re.compile(r"^[A-Z]{1,4}$")
_CUTOFF_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True, slots=True)
class StOriginRule:
    kind: str
    reports_name: bool
    coverage: str
    note: str


@dataclass(frozen=True, slots=True)
class StockStAcquireRules:
    version: int
    st_origin: Mapping[str, StOriginRule]
    coverage: Mapping[str, frozenset[str]]
    membership_type: str
    membership_type_name: str
    name_snapshot_table: str
    name_snapshot_timezone: str
    name_snapshot_attribution_cutoff_local: str
    reservoir_table: str
    reservoir_isst_field: str
    reservoir_isst_true_value: str
    backfill_origin_by_source: Mapping[str, str]

    @property
    def allowed_st_origins(self) -> frozenset[str]:
        return frozenset(self.st_origin)

    def coverage_exchanges_for(self, origin: str) -> frozenset[str]:
        """Exchange suffixes (``SH``/``SZ``/``BJ``) this ``st_origin`` can
        answer for. Raises ``ValueError`` (not ``KeyError``) for an
        unregistered origin — fail-closed like every other lookup here."""

        rule = self.st_origin.get(origin)
        if rule is None:
            raise ValueError(
                f"stock_st_acquire: st_origin={origin!r} 未在 st_origin 取值集里注册 "
                f"(已注册: {sorted(self.st_origin)})"
            )
        return self.coverage[rule.coverage]


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def partition_coverage(
    rules: StockStAcquireRules, origins: Iterable[str]
) -> frozenset[str]:
    """Exchange coverage of one accepted partition = the intersection of the
    ``coverage`` groups of every distinct ``st_origin`` value present in that
    partition's rows. Empty ``origins`` (an empty partition — no evidence to
    claim any coverage) returns the empty set."""

    unique = sorted(set(origins))
    if not unique:
        return frozenset()
    result = rules.coverage_exchanges_for(unique[0])
    for origin in unique[1:]:
        result = result & rules.coverage_exchanges_for(origin)
    return result


def load_stock_st_acquire_rules(path: Path | None = None) -> StockStAcquireRules:
    """Load and validate ``stock_st_acquire.yaml``. Fail-closed on every
    unrecognized shape — see the module docstring for the full rule list.

    Every ``ValueError`` message is prefixed ``stock_st_acquire:`` so a
    failure names which part of the config it came from, and names the exact
    key path that failed.
    """

    cfg_path = path if path is not None else _CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise ValueError(
            f"stock_st_acquire: 顶层键必须恰好是 {sorted(_TOP_LEVEL_KEYS)}, "
            f"got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )

    version = raw["version"]
    if not _is_positive_int(version):
        raise ValueError(f"stock_st_acquire: version 必须是正整数, got {version!r}")

    coverage_raw = raw["coverage"]
    if not isinstance(coverage_raw, dict) or not coverage_raw:
        raise ValueError("stock_st_acquire: coverage 取值集不能为空")
    coverage: dict[str, frozenset[str]] = {}
    for cov_name, cov_entry in coverage_raw.items():
        if not isinstance(cov_name, str) or not cov_name.strip():
            raise ValueError(f"stock_st_acquire: coverage 键必须是非空字符串, got {cov_name!r}")
        if not isinstance(cov_entry, dict) or set(cov_entry) != _COVERAGE_ENTRY_KEYS:
            raise ValueError(
                f"stock_st_acquire: coverage.{cov_name} 键必须恰好是 "
                f"{sorted(_COVERAGE_ENTRY_KEYS)}, got "
                f"{sorted(cov_entry) if isinstance(cov_entry, dict) else type(cov_entry).__name__}"
            )
        exchanges_raw = cov_entry["exchanges"]
        if (
            not isinstance(exchanges_raw, list)
            or not exchanges_raw
            or not all(isinstance(e, str) and _EXCHANGE_RE.match(e) for e in exchanges_raw)
        ):
            raise ValueError(
                f"stock_st_acquire: coverage.{cov_name}.exchanges 必须是非空的大写交易所"
                f"后缀列表 (如 SH/SZ/BJ), got {exchanges_raw!r}"
            )
        coverage[cov_name] = frozenset(exchanges_raw)

    origin_raw = raw["st_origin"]
    if not isinstance(origin_raw, dict) or not origin_raw:
        raise ValueError("stock_st_acquire: st_origin 取值集不能为空")
    origin_rules: dict[str, StOriginRule] = {}
    for name, entry in origin_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"stock_st_acquire: st_origin 键必须是非空字符串, got {name!r}")
        if not isinstance(entry, dict) or set(entry) != _ORIGIN_ENTRY_KEYS:
            raise ValueError(
                f"stock_st_acquire: st_origin.{name} 键必须恰好是 {sorted(_ORIGIN_ENTRY_KEYS)}, "
                f"got {sorted(entry) if isinstance(entry, dict) else type(entry).__name__}"
            )
        kind = entry["kind"]
        if kind not in _ALLOWED_KINDS:
            raise ValueError(
                f"stock_st_acquire: st_origin.{name}.kind={kind!r} 不在 {sorted(_ALLOWED_KINDS)} 里"
            )
        reports_name = entry["reports_name"]
        if not isinstance(reports_name, bool):
            raise ValueError(
                f"stock_st_acquire: st_origin.{name}.reports_name 必须是布尔值, got {reports_name!r}"
            )
        cov_ref = entry["coverage"]
        if not isinstance(cov_ref, str) or cov_ref not in coverage:
            raise ValueError(
                f"stock_st_acquire: st_origin.{name}.coverage={cov_ref!r} 不在已注册的 "
                f"coverage 组 {sorted(coverage)} 里"
            )
        note = entry["note"]
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"stock_st_acquire: st_origin.{name}.note 必须是非空字符串")
        origin_rules[name] = StOriginRule(
            kind=str(kind), reports_name=reports_name, coverage=str(cov_ref), note=note
        )

    labels_raw = raw["membership_labels"]
    if not isinstance(labels_raw, dict) or set(labels_raw) != _MEMBERSHIP_LABEL_KEYS:
        raise ValueError(
            f"stock_st_acquire: membership_labels 键必须恰好是 {sorted(_MEMBERSHIP_LABEL_KEYS)}, "
            f"got {sorted(labels_raw) if isinstance(labels_raw, dict) else type(labels_raw).__name__}"
        )
    membership_type = labels_raw["type"]
    membership_type_name = labels_raw["type_name"]
    if not isinstance(membership_type, str) or not membership_type.strip():
        raise ValueError("stock_st_acquire: membership_labels.type 必须是非空字符串")
    if not isinstance(membership_type_name, str) or not membership_type_name.strip():
        raise ValueError("stock_st_acquire: membership_labels.type_name 必须是非空字符串")

    snap_raw = raw["name_snapshot"]
    if not isinstance(snap_raw, dict) or set(snap_raw) != _NAME_SNAPSHOT_KEYS:
        raise ValueError(
            f"stock_st_acquire: name_snapshot 键必须恰好是 {sorted(_NAME_SNAPSHOT_KEYS)}, "
            f"got {sorted(snap_raw) if isinstance(snap_raw, dict) else type(snap_raw).__name__}"
        )
    snap_table = snap_raw["table"]
    if not isinstance(snap_table, str) or not snap_table.strip():
        raise ValueError("stock_st_acquire: name_snapshot.table 必须是非空字符串")
    snap_tz = snap_raw["timezone"]
    if not isinstance(snap_tz, str) or not snap_tz.strip():
        raise ValueError(f"stock_st_acquire: name_snapshot.timezone 必须是非空字符串, got {snap_tz!r}")
    try:
        ZoneInfo(snap_tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"stock_st_acquire: name_snapshot.timezone={snap_tz!r} 不能被 zoneinfo 解析"
        ) from exc
    cutoff = snap_raw["attribution_cutoff_local"]
    if not isinstance(cutoff, str) or not _CUTOFF_RE.match(cutoff):
        raise ValueError(
            "stock_st_acquire: name_snapshot.attribution_cutoff_local 必须是 HH:MM (24小时制) "
            f"格式, got {cutoff!r}"
        )

    reservoir_raw = raw["reservoir"]
    if not isinstance(reservoir_raw, dict) or set(reservoir_raw) != _RESERVOIR_KEYS:
        raise ValueError(
            f"stock_st_acquire: reservoir 键必须恰好是 {sorted(_RESERVOIR_KEYS)}, "
            f"got {sorted(reservoir_raw) if isinstance(reservoir_raw, dict) else type(reservoir_raw).__name__}"
        )
    reservoir_table = reservoir_raw["table"]
    if not isinstance(reservoir_table, str) or not reservoir_table.strip():
        raise ValueError("stock_st_acquire: reservoir.table 必须是非空字符串")
    isst_field = reservoir_raw["isst_field"]
    if not isinstance(isst_field, str) or not isst_field.strip():
        raise ValueError("stock_st_acquire: reservoir.isst_field 必须是非空字符串")
    isst_true_value = reservoir_raw["isst_true_value"]
    if not isinstance(isst_true_value, str) or not isst_true_value.strip():
        raise ValueError("stock_st_acquire: reservoir.isst_true_value 必须是非空字符串")

    backfill_raw = raw["backfill_origin_by_source"]
    if not isinstance(backfill_raw, dict) or not backfill_raw:
        raise ValueError("stock_st_acquire: backfill_origin_by_source 不能为空")
    backfill: dict[str, str] = {}
    for source_name, origin_label in backfill_raw.items():
        if not isinstance(source_name, str) or not source_name.strip():
            raise ValueError(
                f"stock_st_acquire: backfill_origin_by_source 键必须是非空字符串, got {source_name!r}"
            )
        if origin_label not in origin_rules:
            raise ValueError(
                f"stock_st_acquire: backfill_origin_by_source.{source_name}={origin_label!r} "
                f"不在 st_origin 取值集 {sorted(origin_rules)} 里"
            )
        backfill[source_name] = str(origin_label)

    return StockStAcquireRules(
        version=version,
        st_origin=origin_rules,
        coverage=coverage,
        membership_type=str(membership_type),
        membership_type_name=str(membership_type_name),
        name_snapshot_table=str(snap_table),
        name_snapshot_timezone=str(snap_tz),
        name_snapshot_attribution_cutoff_local=str(cutoff),
        reservoir_table=str(reservoir_table),
        reservoir_isst_field=str(isst_field),
        reservoir_isst_true_value=str(isst_true_value),
        backfill_origin_by_source=backfill,
    )


def build_st_origin_validator(
    rules: StockStAcquireRules,
) -> Callable[[Mapping[str, Any]], None]:
    """Build the row-level ``enrichment_validator`` for ``SecurityDayDomain``.

    The returned callable takes the merged ``{**provider, **enrichment}``
    mapping for one row (i.e. it must contain ``name`` and ``st_origin``) and
    raises ``SecurityDayValidationError`` with code ``INVALID_ENRICHMENT`` if
    either check fails. It never returns a value — absence of an exception is
    the only signal.
    """

    allowed = rules.allowed_st_origins
    reports_name_by_origin = {name: rule.reports_name for name, rule in rules.st_origin.items()}

    def validate(row: Mapping[str, Any]) -> None:
        origin = row.get("st_origin")
        if origin not in allowed:
            raise SecurityDayValidationError(
                "INVALID_ENRICHMENT",
                f"st_origin={origin!r} 不在合法取值集 {sorted(allowed)} 里",
            )
        reports_name = reports_name_by_origin[str(origin)]
        name_is_none = row.get("name") is None
        if reports_name == name_is_none:
            raise SecurityDayValidationError(
                "INVALID_ENRICHMENT",
                f"st_origin={origin!r} reports_name={reports_name!r} 与 name is None="
                f"{name_is_none} 不满足 reports_name⇔name非NULL "
                "(reports_name=True 必须配非 NULL 的 name, reports_name=False 必须配 NULL)",
            )

    return validate


__all__ = [
    "StOriginRule",
    "StockStAcquireRules",
    "build_st_origin_validator",
    "load_stock_st_acquire_rules",
    "partition_coverage",
]
