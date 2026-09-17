"""Typed loader for ``backend/config/nominal_ohlcv_acquire.yaml`` (daily domain).

**What this file is for**: the daily nominal OHLCV domain's ``pre_close_origin``
enrichment column needs a machine-checkable set of legal values (which
provider/derivation path produced ``pre_close``, or why it is unknown) plus a
couple of small acquisition parameters (reference source, consistency
tolerance, per-day unknown-row tolerance, dump incremental floor, and the
one-time historical source→origin backfill map). None of this belongs in
``nominal_ohlcv_schema.py``'s ``_SCHEMA_PAYLOAD`` — putting it there would
change ``SCHEMA_HASH`` and force a restamp of every accepted partition for no
benefit (see the YAML file's header comment). It also does not belong
hardcoded in Python: these are exactly the kind of values 业主 2026-09-16 明令
must live in typed YAML (thresholds, tolerances, date floors, provenance
labels, a source→label backfill map) — a module here only *reads and
enforces* the config, it does not restate it.

2026-09-16 刀2 追加: the fuyao dump + baostock adapter (``sources/fuyao_daily_k.py``)
needs several more of exactly this kind of value — dump column name mapping,
unit-conversion divisors, the ``adjusted`` filter value, the dump timestamp
timezone, the baostock query field list, the ts_code-suffix→baostock-prefix
map, the prefetch window, and the change/pct_chg rounding precision. All of
it lives under ``reference``/``dump`` alongside the values cut 1 already put
there — one YAML, one loader, not a second config file for the same domain.

返修 (blocking 发现修复) 追加两项, 同一理由: ``reference.api`` (baostock 端点名,
校验必须落在 ``sources/baostock.py::API_FUNCTION_NAMES`` 白名单里 —— 供应商端点会
随吞吐场景变, 之前以字面量散落在 adapter 两处) 与 ``dump.cache_dir`` (fuyao dump
parquet 的仓库相对缓存目录 —— 之前以字面量写死在 adapter 模块顶层, 且与
``backend/scripts/recon_fuyao_kline.py`` 各自另定义一份同样的路径)。

Fail-closed by construction (project rule 11: 规则进 typed YAML, 未知键 fail-
closed): any unrecognized top-level key, unknown ``kind``, empty value set,
non-positive threshold, malformed date floor, or a ``backfill_origin_by_source``
value that is not itself a registered ``pre_close_origin`` key raises
``ValueError`` — never partially applied, never silently ignored.

This module also owns the row-level enforcement built on top of the loaded
rules: :func:`build_pre_close_origin_validator` returns the callable that
``security_day_partition.py::_candidate_rows`` invokes (via
``SecurityDayDomain.enrichment_validator``) once a row's ``pre_close_origin``
has been assembled. It raises
``services.data_sources.security_day_partition.SecurityDayValidationError``
with code ``INVALID_ENRICHMENT`` on either violation:

  1. ``pre_close_origin`` not in the registered value set.
  2. ``kind == "unknown"`` XOR ``pre_close IS NULL`` (i.e. the two must always
     agree — an ``unknown`` label with a real value, or a ``provider``/
     ``derived`` label with a NULL value, are both lies about provenance).

``stock_st`` and every other ``SecurityDayDomain`` do not set
``enrichment_validator`` and therefore default to ``None`` — this module is
daily-only and is never imported by the shared land→accept mechanics.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from services.data_sources.security_day_partition import SecurityDayValidationError
from services.data_sources.sources.baostock import API_FUNCTION_NAMES

# backend/services/data_sources/nominal_ohlcv_acquire_rules.py — two parents up
# is backend/.
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "nominal_ohlcv_acquire.yaml"

_TOP_LEVEL_KEYS = frozenset(
    {"version", "pre_close_origin", "reference", "dump", "backfill_origin_by_source"}
)
_ORIGIN_ENTRY_KEYS = frozenset({"kind", "note"})
_ALLOWED_KINDS = frozenset({"provider", "derived", "unknown"})
_REFERENCE_KEYS = frozenset(
    {
        "source",
        "api",
        "close_tolerance",
        "sh_sz_max_unknown_rows",
        "prefetch_window_days",
        "baostock_fields",
        "exchange_suffix_to_baostock_prefix",
        "change_pct_round_digits",
    }
)
_DUMP_KEYS = frozenset(
    {
        "cache_dir",
        "incremental_floor",
        "column_map",
        "vol_divisor",
        "amount_divisor",
        "adjusted_filter_value",
        "timezone",
    }
)
_INCREMENTAL_FLOOR_RE = re.compile(r"^\d{8}$")

# ``dump.column_map`` 的目标列集合 (values) 必须恰好等于这个集合 —— PROVIDER_FIELDS
# (nominal_ohlcv_schema.py) 里"由 fuyao dump 直接提供"的那 8 列: ts_code/trade_date
# (身份/时间) + open/high/low/close/vol/amount (六个价量列)。pre_close/change/pct_chg
# 来自 baostock 一条腿或由它派生, 不该出现在 dump 的列映射目标里。
#
# 这是**结构常量**不是参数 (CLAUDE.md「数据结构的键名...属于结构，留在代码里」):
# PROVIDER_FIELDS 本身已经是 nominal_ohlcv_schema.py 的结构声明, 这里只是它的一个
# 固定子集, 不会因业务/供应商/时间变化。刻意不从 nominal_ohlcv_schema 导入 ——
# 该模块的顶层 DOMAIN 构造反过来要 import 本模块的 load_nominal_ohlcv_acquire_rules
# (见 nominal_ohlcv_schema.py 的 enrichment_validator 那行), 逆向 import 会成环。
_DUMP_SOURCED_PROVIDER_FIELDS = frozenset(
    {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
)
# 返修 (blocking 发现修复): 这里曾经把 exchange_suffix_to_baostock_prefix 的键集合钉死为
# {"SH", "SZ"} —— 但"哪些交易所有 baostock 参考"是供应商事实, 会随 baostock 覆盖范围变
# (业主 09-16 参数规则: 名单类参数改配置不改代码, 不许在两处各定义一份)。钉死集合会在
# 业主给 BJ 之类新增一行时让这个 loader 抛"键必须恰好是 [SH, SZ]", 而它已经是
# nominal_ohlcv_schema.py import 期间构造 DOMAIN 的必经路径 —— 配置改不动, 必须改代码。
# 未登记的后缀本就由 ``_exchange_prefix_code`` (fuyao_daily_k.py) 在使用时 fail-closed
# 报错, 钉死这个集合没有额外的守护价值, 只留下这里只校验形状 (非空、键是大写后缀、值非空
# 字符串)。
_EXCHANGE_SUFFIX_RE = re.compile(r"^[A-Z]{1,4}$")


@dataclass(frozen=True, slots=True)
class PreCloseOriginRule:
    kind: str
    note: str


@dataclass(frozen=True, slots=True)
class NominalOhlcvAcquireRules:
    version: int
    pre_close_origin: Mapping[str, PreCloseOriginRule]
    reference_source: str
    close_tolerance: float
    sh_sz_max_unknown_rows: int
    dump_incremental_floor: str
    backfill_origin_by_source: Mapping[str, str]
    # 2026-09-16 刀2 追加 (fuyao dump + baostock 适配器用):
    prefetch_window_days: int
    baostock_fields: tuple[str, ...]
    exchange_suffix_to_baostock_prefix: Mapping[str, str]
    change_pct_round_digits: int
    dump_column_map: Mapping[str, str]
    dump_vol_divisor: float
    dump_amount_divisor: float
    dump_adjusted_filter_value: str
    dump_timezone: str
    # 返修 (blocking 发现修复) 追加: baostock 端点名与 dump 缓存目录 —— 两者都曾以字面量
    # 形式散落在 fuyao_daily_k.py 里, 收进配置消灭字面量副本。
    reference_api: str
    dump_cache_dir: str

    @property
    def allowed_pre_close_origins(self) -> frozenset[str]:
        return frozenset(self.pre_close_origin)

    @property
    def baostock_fields_csv(self) -> str:
        return ",".join(self.baostock_fields)


def _is_positive_int(value: Any) -> bool:
    # bool is an int subclass in Python — reject it explicitly so a stray
    # `true`/`false` in the YAML can never masquerade as 1/0.
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    )


def load_nominal_ohlcv_acquire_rules(path: Path | None = None) -> NominalOhlcvAcquireRules:
    """Load and validate ``nominal_ohlcv_acquire.yaml``. Fail-closed on every
    unrecognized shape — see the module docstring for the full rule list.

    Every ``ValueError`` message is prefixed ``nominal_ohlcv_acquire:`` so a
    failure names which part of the config it came from.
    """

    cfg_path = path if path is not None else _CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise ValueError(
            f"nominal_ohlcv_acquire: 顶层键必须恰好是 {sorted(_TOP_LEVEL_KEYS)}, "
            f"got {sorted(raw) if isinstance(raw, dict) else type(raw).__name__}"
        )

    version = raw["version"]
    if not _is_positive_int(version):
        raise ValueError(f"nominal_ohlcv_acquire: version 必须是正整数, got {version!r}")

    origin_raw = raw["pre_close_origin"]
    if not isinstance(origin_raw, dict) or not origin_raw:
        raise ValueError("nominal_ohlcv_acquire: pre_close_origin 取值集不能为空")
    origin_rules: dict[str, PreCloseOriginRule] = {}
    for name, entry in origin_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"nominal_ohlcv_acquire: pre_close_origin 键必须是非空字符串, got {name!r}"
            )
        if not isinstance(entry, dict) or set(entry) != _ORIGIN_ENTRY_KEYS:
            raise ValueError(
                f"nominal_ohlcv_acquire: pre_close_origin.{name} 键必须恰好是 "
                f"{sorted(_ORIGIN_ENTRY_KEYS)}, got "
                f"{sorted(entry) if isinstance(entry, dict) else type(entry).__name__}"
            )
        kind = entry["kind"]
        if kind not in _ALLOWED_KINDS:
            raise ValueError(
                f"nominal_ohlcv_acquire: pre_close_origin.{name}.kind={kind!r} "
                f"不在 {sorted(_ALLOWED_KINDS)} 里"
            )
        note = entry["note"]
        if not isinstance(note, str) or not note.strip():
            raise ValueError(
                f"nominal_ohlcv_acquire: pre_close_origin.{name}.note 必须是非空字符串"
            )
        origin_rules[name] = PreCloseOriginRule(kind=str(kind), note=note)

    reference_raw = raw["reference"]
    if not isinstance(reference_raw, dict) or set(reference_raw) != _REFERENCE_KEYS:
        raise ValueError(
            f"nominal_ohlcv_acquire: reference 键必须恰好是 {sorted(_REFERENCE_KEYS)}, "
            f"got {sorted(reference_raw) if isinstance(reference_raw, dict) else type(reference_raw).__name__}"
        )
    reference_source = reference_raw["source"]
    if not isinstance(reference_source, str) or not reference_source.strip():
        raise ValueError("nominal_ohlcv_acquire: reference.source 必须是非空字符串")
    reference_api = reference_raw["api"]
    if not isinstance(reference_api, str) or reference_api not in API_FUNCTION_NAMES:
        raise ValueError(
            "nominal_ohlcv_acquire: reference.api 必须是 "
            f"sources/baostock.py::API_FUNCTION_NAMES 白名单里的一个, "
            f"got {reference_api!r} (已知: {sorted(API_FUNCTION_NAMES)})"
        )
    close_tolerance = reference_raw["close_tolerance"]
    if not _is_positive_number(close_tolerance):
        raise ValueError(
            f"nominal_ohlcv_acquire: reference.close_tolerance 必须是正数, got {close_tolerance!r}"
        )
    max_unknown = reference_raw["sh_sz_max_unknown_rows"]
    if not _is_positive_int(max_unknown):
        raise ValueError(
            "nominal_ohlcv_acquire: reference.sh_sz_max_unknown_rows 必须是正整数, "
            f"got {max_unknown!r}"
        )
    prefetch_window_days = reference_raw["prefetch_window_days"]
    if not _is_positive_int(prefetch_window_days):
        raise ValueError(
            "nominal_ohlcv_acquire: reference.prefetch_window_days 必须是正整数, "
            f"got {prefetch_window_days!r}"
        )
    baostock_fields_raw = reference_raw["baostock_fields"]
    if (
        not isinstance(baostock_fields_raw, list)
        or not baostock_fields_raw
        or not all(isinstance(f, str) and f.strip() for f in baostock_fields_raw)
        or len(set(baostock_fields_raw)) != len(baostock_fields_raw)
    ):
        raise ValueError(
            "nominal_ohlcv_acquire: reference.baostock_fields 必须是非空且不重复的字符串列表, "
            f"got {baostock_fields_raw!r}"
        )
    baostock_fields = tuple(str(f) for f in baostock_fields_raw)
    exchange_map_raw = reference_raw["exchange_suffix_to_baostock_prefix"]
    if not isinstance(exchange_map_raw, dict) or not exchange_map_raw:
        raise ValueError(
            "nominal_ohlcv_acquire: reference.exchange_suffix_to_baostock_prefix 不能为空, "
            f"got {exchange_map_raw!r}"
        )
    exchange_map: dict[str, str] = {}
    for suffix, prefix in exchange_map_raw.items():
        if not isinstance(suffix, str) or not _EXCHANGE_SUFFIX_RE.match(suffix):
            raise ValueError(
                "nominal_ohlcv_acquire: reference.exchange_suffix_to_baostock_prefix 键必须是"
                f"大写交易所后缀 (如 SH/SZ/BJ), got {suffix!r}"
            )
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError(
                f"nominal_ohlcv_acquire: reference.exchange_suffix_to_baostock_prefix.{suffix}"
                f" 必须是非空字符串, got {prefix!r}"
            )
        exchange_map[str(suffix)] = str(prefix)
    round_digits = reference_raw["change_pct_round_digits"]
    if not _is_positive_int(round_digits):
        raise ValueError(
            "nominal_ohlcv_acquire: reference.change_pct_round_digits 必须是正整数, "
            f"got {round_digits!r}"
        )

    dump_raw = raw["dump"]
    if not isinstance(dump_raw, dict) or set(dump_raw) != _DUMP_KEYS:
        raise ValueError(
            f"nominal_ohlcv_acquire: dump 键必须恰好是 {sorted(_DUMP_KEYS)}, "
            f"got {sorted(dump_raw) if isinstance(dump_raw, dict) else type(dump_raw).__name__}"
        )
    cache_dir = dump_raw["cache_dir"]
    if (
        not isinstance(cache_dir, str)
        or not cache_dir.strip()
        or cache_dir.startswith("/")
        or ".." in Path(cache_dir).parts
    ):
        raise ValueError(
            "nominal_ohlcv_acquire: dump.cache_dir 必须是非空、仓库相对 (不以 / 开头、"
            f"不含 ..) 的路径字符串, got {cache_dir!r}"
        )
    floor = dump_raw["incremental_floor"]
    if not isinstance(floor, str) or not _INCREMENTAL_FLOOR_RE.match(floor):
        raise ValueError(
            f"nominal_ohlcv_acquire: dump.incremental_floor 必须是 YYYYMMDD 字符串, got {floor!r}"
        )
    column_map_raw = dump_raw["column_map"]
    if not isinstance(column_map_raw, dict) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        for k, v in column_map_raw.items()
    ):
        raise ValueError(
            "nominal_ohlcv_acquire: dump.column_map 必须是非空字符串到非空字符串的映射, "
            f"got {column_map_raw!r}"
        )
    column_map = {str(k): str(v) for k, v in column_map_raw.items()}
    if set(column_map.values()) != _DUMP_SOURCED_PROVIDER_FIELDS:
        raise ValueError(
            "nominal_ohlcv_acquire: dump.column_map 的目标列集合必须恰好是 "
            f"{sorted(_DUMP_SOURCED_PROVIDER_FIELDS)} (PROVIDER_FIELDS 里由 dump 直接提供的"
            f"列), got {sorted(set(column_map.values()))}"
        )
    vol_divisor = dump_raw["vol_divisor"]
    if not _is_positive_number(vol_divisor):
        raise ValueError(
            f"nominal_ohlcv_acquire: dump.vol_divisor 必须是正数, got {vol_divisor!r}"
        )
    amount_divisor = dump_raw["amount_divisor"]
    if not _is_positive_number(amount_divisor):
        raise ValueError(
            f"nominal_ohlcv_acquire: dump.amount_divisor 必须是正数, got {amount_divisor!r}"
        )
    adjusted_filter_value = dump_raw["adjusted_filter_value"]
    if not isinstance(adjusted_filter_value, str) or not adjusted_filter_value.strip():
        raise ValueError(
            "nominal_ohlcv_acquire: dump.adjusted_filter_value 必须是非空字符串, "
            f"got {adjusted_filter_value!r}"
        )
    dump_timezone = dump_raw["timezone"]
    if not isinstance(dump_timezone, str) or not dump_timezone.strip():
        raise ValueError(
            f"nominal_ohlcv_acquire: dump.timezone 必须是非空字符串, got {dump_timezone!r}"
        )
    try:
        ZoneInfo(dump_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"nominal_ohlcv_acquire: dump.timezone={dump_timezone!r} 不能被 zoneinfo 解析"
        ) from exc

    backfill_raw = raw["backfill_origin_by_source"]
    if not isinstance(backfill_raw, dict) or not backfill_raw:
        raise ValueError("nominal_ohlcv_acquire: backfill_origin_by_source 不能为空")
    backfill: dict[str, str] = {}
    for source_name, origin_label in backfill_raw.items():
        if not isinstance(source_name, str) or not source_name.strip():
            raise ValueError(
                "nominal_ohlcv_acquire: backfill_origin_by_source 键必须是非空字符串, "
                f"got {source_name!r}"
            )
        if origin_label not in origin_rules:
            raise ValueError(
                f"nominal_ohlcv_acquire: backfill_origin_by_source.{source_name}="
                f"{origin_label!r} 不在 pre_close_origin 取值集 {sorted(origin_rules)} 里"
            )
        backfill[source_name] = str(origin_label)

    return NominalOhlcvAcquireRules(
        version=version,
        pre_close_origin=origin_rules,
        reference_source=reference_source,
        close_tolerance=float(close_tolerance),
        sh_sz_max_unknown_rows=max_unknown,
        dump_incremental_floor=floor,
        backfill_origin_by_source=backfill,
        prefetch_window_days=prefetch_window_days,
        baostock_fields=baostock_fields,
        exchange_suffix_to_baostock_prefix=exchange_map,
        change_pct_round_digits=round_digits,
        dump_column_map=column_map,
        dump_vol_divisor=float(vol_divisor),
        dump_amount_divisor=float(amount_divisor),
        dump_adjusted_filter_value=adjusted_filter_value,
        dump_timezone=dump_timezone,
        reference_api=reference_api,
        dump_cache_dir=cache_dir,
    )


def build_pre_close_origin_validator(
    rules: NominalOhlcvAcquireRules,
) -> Callable[[Mapping[str, Any]], None]:
    """Build the row-level ``enrichment_validator`` for ``SecurityDayDomain``.

    The returned callable takes the merged ``{**provider, **enrichment}``
    mapping for one row (i.e. it must contain ``pre_close`` and
    ``pre_close_origin``) and raises ``SecurityDayValidationError`` with code
    ``INVALID_ENRICHMENT`` if either check fails. It never returns a value —
    absence of an exception is the only signal.
    """

    allowed = rules.allowed_pre_close_origins
    kind_by_origin = {name: rule.kind for name, rule in rules.pre_close_origin.items()}

    def validate(row: Mapping[str, Any]) -> None:
        origin = row.get("pre_close_origin")
        if origin not in allowed:
            raise SecurityDayValidationError(
                "INVALID_ENRICHMENT",
                f"pre_close_origin={origin!r} 不在合法取值集 {sorted(allowed)} 里",
            )
        kind = kind_by_origin[str(origin)]
        pre_close_is_null = row.get("pre_close") is None
        if (kind == "unknown") != pre_close_is_null:
            raise SecurityDayValidationError(
                "INVALID_ENRICHMENT",
                f"pre_close_origin={origin!r} kind={kind!r} 与 pre_close is None="
                f"{pre_close_is_null} 不满足 kind==unknown⇔NULL "
                "(unknown 标签必须配 NULL 值, provider/derived 标签必须配非 NULL 值)",
            )

    return validate


__all__ = [
    "NominalOhlcvAcquireRules",
    "PreCloseOriginRule",
    "build_pre_close_origin_validator",
    "load_nominal_ohlcv_acquire_rules",
]
