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
    # 缺省 t+1 (2026-07-09 全审计修复): 原缺省 "eod"(当日可用)是不安全方向 — 未来 entity 漏写
    # 该键会静默变成"当天可用"声明, 与 sync_runner._available_after_passed 对缺失返 False 的
    # 保守缺省方向相反。改为最保守的 t+1, 漏写只会导致数据显得晚一天(不泄漏), 不会提前可见。
    available_after: str = "t+1"
    taxonomy_version: str | None = None
    compute_fn: str | None = None   # None = 厂商现成 (raw entity); 非空 = 派生
    code_input: str = ""            # plain | ts_from_plain | ts_passthrough; 空=按 code_col 推断

    @property
    def code_mode(self) -> str:
        """主键模式 (不变量#1): plain(6位直用) / ts_from_plain(6位→ts_code过滤,输出归6位) /
        ts_passthrough(指数码 000300.SH 直用不转, code_to_ts_code 股票前缀规则对指数无效)。
        """
        if self.code_input:
            return self.code_input
        return "ts_from_plain" if self.code_col == "ts_code" else "plain"

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
            available_after=e.get("available_after", "t+1"),  # 保守缺省, 与 dataclass 定义同向
            taxonomy_version=e.get("taxonomy_version"),
            compute_fn=e.get("compute_fn"),
            code_input=e.get("code_input", ""),
        )
    return AccessRegistry(entities=ents, clean_rules=raw.get("clean_rules") or {})
