"""data_access.yaml 载入 + EntitySpec (SERVE 读层 entity 声明)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "data_access.yaml"


@dataclass(frozen=True)
class EntitySpec:
    name: str
    db: str                 # database_manifest 别名
    table: str
    layer: str
    code_col: str
    asof_col: str
    asof_format: str        # 'iso' | 'yyyymmdd'
    columns: tuple[str, ...]
    vendor: str
    available_after: str = "eod"
    taxonomy_version: str | None = None
    compute_fn: str | None = None   # None = 厂商现成 (raw entity); 非空 = 派生

    @property
    def code_is_ts(self) -> bool:
        """code_col 为 ts_code → 输入按 ts_code 过滤, 输出归一 6 位。"""
        return self.code_col == "ts_code"

    def provenance(self, as_of: str | None) -> dict[str, Any]:
        """携带溯源信封 (血缘 per-entity 面, 非 per-row)。"""
        return {
            "source_entity": self.name,
            "source_table": f"{self.db}.{self.table}",
            "layer": self.layer,
            "vendor": self.vendor,
            "as_of": as_of,
            "asof_anchor": self.asof_col,
            "available_after": self.available_after,
            "compute_fn": self.compute_fn,
            "taxonomy_version": self.taxonomy_version,
        }


@dataclass(frozen=True)
class AccessRegistry:
    entities: dict[str, EntitySpec]
    clean_rules: dict[str, Any] = field(default_factory=dict)

    def entity(self, name: str) -> EntitySpec:
        try:
            return self.entities[name]
        except KeyError as exc:
            known = ", ".join(sorted(self.entities))
            raise ValueError(f"未知 data_access entity {name!r}; 已声明: {known}") from exc


def load_registry(path: str | Path | None = None) -> AccessRegistry:
    p = Path(path) if path else _DEFAULT_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    ents: dict[str, EntitySpec] = {}
    for name, e in (raw.get("entities") or {}).items():
        ents[name] = EntitySpec(
            name=name,
            db=e["db"],
            table=e["table"],
            layer=e["layer"],
            code_col=e["code_col"],
            asof_col=e["asof_col"],
            asof_format=e["asof_format"],
            columns=tuple(e["columns"]),
            vendor=e["vendor"],
            available_after=e.get("available_after", "eod"),
            taxonomy_version=e.get("taxonomy_version"),
            compute_fn=e.get("compute_fn"),
        )
    return AccessRegistry(entities=ents, clean_rules=raw.get("clean_rules") or {})
