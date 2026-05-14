"""Phase ψ.γ.dict.2 — field_dictionary.yaml 加载器 (单一职责).

⚠ 唯一从 backend/config/field_dictionary.yaml 读字段 schema 的地方.

设计 (跟 services/optimization/config.py 同款):
  - frozen dataclass 防意外改 schema
  - load_field_dictionary(override=...) 单测可注入
  - lookup_table('fact_signal_context' 或 'smartmoney.duckdb.fact_signal_context')
    支持 short name (无 db prefix) 和 full name (有 db prefix).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "field_dictionary.yaml"


# ─────────────────────────────────────────────────────────────────────
# Frozen dataclasses (跟 yaml 结构对齐)
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldSpec:
    """单字段 schema."""
    name: str
    type: str                              # DATE / DOUBLE / TEXT / INTEGER / JSON / BOOL / TIMESTAMP
    role: Optional[str] = None             # pk / pit-key / business-canonical / in-sample-only / "pk + pit-key"
    unit: Optional[str] = None             # CNY / fraction / percent / dimensionless / "MIXED..."
    description: Optional[str] = None
    enum: Optional[tuple] = None
    outlier_cap: Optional[Union[float, tuple]] = None   # 单 float 或 (lo, hi)
    sign: Optional[str] = None             # positive / negative
    warning: Optional[str] = None


@dataclass(frozen=True)
class TableSpec:
    """单表 schema."""
    db: str                                # 'market.duckdb' / 'smartmoney.duckdb' / 'etf.duckdb'
    name: str                              # 表名 (不含 db prefix)
    description: Optional[str] = None
    pit_key: Optional[str] = None          # 主 PIT key 字段名
    row_count_approx: Optional[int] = None
    coverage: Optional[str] = None
    fields: dict = field(default_factory=dict)   # {field_name: FieldSpec}
    notes: tuple = ()

    @property
    def full_name(self) -> str:
        """'<db>.<table>' 形式."""
        return f"{self.db}.{self.name}"


@dataclass(frozen=True)
class FieldDictionary:
    """整个字典."""
    conventions: dict
    tables: dict                            # {full_name: TableSpec}
    join_templates: dict
    known_inconsistencies: dict

    def lookup_table(self, name: str) -> Optional[TableSpec]:
        """根据 short name ('fact_signal_context') 或 full name ('smartmoney.duckdb.fact_signal_context') 查表.

        Raises:
            ValueError: short name 跨 DB 重复时 (歧义).
        """
        if name in self.tables:
            return self.tables[name]
        # Short name lookup: 后缀匹配
        candidates = [
            (full_name, spec) for full_name, spec in self.tables.items()
            if full_name.endswith("." + name)
        ]
        if len(candidates) == 1:
            return candidates[0][1]
        if len(candidates) > 1:
            dbs = [spec.db for _, spec in candidates]
            raise ValueError(
                f"Table '{name}' 跨 DB 重复 (在 {dbs}), 用 full name 'db.{name}' 区分"
            )
        return None


# ─────────────────────────────────────────────────────────────────────
# Load + parse
# ─────────────────────────────────────────────────────────────────────


def _parse_field(name: str, raw: dict) -> FieldSpec:
    """单字段 dict → FieldSpec."""
    enum = raw.get("enum")
    if enum is not None:
        enum = tuple(enum)
    outlier = raw.get("outlier_cap")
    if isinstance(outlier, list):
        outlier = tuple(outlier)
    return FieldSpec(
        name=name,
        type=str(raw.get("type", "")).upper(),
        role=raw.get("role"),
        unit=raw.get("unit"),
        description=raw.get("description"),
        enum=enum,
        outlier_cap=outlier,
        sign=raw.get("sign"),
        warning=raw.get("warning"),
    )


def _parse_table(db: str, name: str, raw: dict) -> TableSpec:
    """单表 dict → TableSpec."""
    fields = {
        fname: _parse_field(fname, fraw or {})
        for fname, fraw in (raw.get("fields") or {}).items()
    }
    notes = tuple(raw.get("notes") or ())
    return TableSpec(
        db=db,
        name=name,
        description=raw.get("description"),
        pit_key=raw.get("pit_key"),
        row_count_approx=raw.get("row_count_approx"),
        coverage=raw.get("coverage"),
        fields=fields,
        notes=notes,
    )


def load_field_dictionary(
    path: Optional[Path] = None,
    override: Optional[dict] = None,
) -> FieldDictionary:
    """加载 + 解析 field_dictionary.yaml.

    Args:
        path:     默认 backend/config/field_dictionary.yaml
        override: 单测注入 (深合并)
    """
    p = path or _CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if override:
        def _deep_merge(base: dict, ov: dict) -> dict:
            out = dict(base)
            for k, v in ov.items():
                if isinstance(v, dict) and isinstance(out.get(k), dict):
                    out[k] = _deep_merge(out[k], v)
                else:
                    out[k] = v
            return out
        raw = _deep_merge(raw, override)

    tables = {}
    for db_name, db_raw in (raw.get("databases") or {}).items():
        if not isinstance(db_raw, dict):
            continue
        for tname, traw in db_raw.items():
            if tname == "description" or not isinstance(traw, dict):
                continue
            tspec = _parse_table(db_name, tname, traw)
            tables[tspec.full_name] = tspec

    return FieldDictionary(
        conventions=raw.get("conventions", {}) or {},
        tables=tables,
        join_templates=raw.get("join_templates", {}) or {},
        known_inconsistencies=raw.get("known_inconsistencies", {}) or {},
    )


# 模块级单例 cache (避免每次 load yaml)
_CACHED: Optional[FieldDictionary] = None


def get_field_dictionary() -> FieldDictionary:
    """单例 — 业务代码用这个."""
    global _CACHED
    if _CACHED is None:
        _CACHED = load_field_dictionary()
    return _CACHED


def reload_field_dictionary() -> FieldDictionary:
    """单测 / 配置改动后强制 reload."""
    global _CACHED
    _CACHED = None
    return get_field_dictionary()
