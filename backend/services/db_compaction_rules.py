"""db_compaction_rules — 日更压缩阈值 typed 配置 (backend/config/db_compaction.yaml)。

单一定义点：库清单 + 触发阈值 + 磁盘余量下限，供
``services.pipeline.store.compact_bloated_databases``（日更 store 阶段统一压缩）与
``backend/scripts/db_compact.py``（手动 CLI 的磁盘余量门）共用。取代此前散落三处的
字面量——``duckdb_compact.COMPACT_FREE_PCT=10.0``、
``build_price_kline_qfq_tushare.py`` 的点状压缩、``db_compact.py`` 里硬编码的
``free < 10``（cut_db_compaction, 2026-09-19）。

loader 放在独立文件而非 ``duckdb_compact.py``：前者是"规则"（阈值/清单，typed YAML +
fail-closed 校验），后者是"机制"（怎么压缩一个库的文件级操作）——CLAUDE.md #13
"一件事若无法机器验证，写进规则，别写进闸"的另一面：能机器验证的判断（未知键/悬空
别名）就该有独立、可单测的 loader，不掺进执行逻辑里。

``databases`` 集合必须与 ``db_invariants.yaml`` 里带 ``bloat_ratio_`` 前缀检查覆盖的
库集合相等——这条由测试断言（非本 loader 运行时强制，两份 config 服务不同治理体系,
耦合进 loader 会让每次日更都要多解析一份不相关的 yaml），见
``backend/tests/services/test_db_compaction_rules.py::test_databases_set_matches_db_invariants_bloat_checks``，
防止"有报警、没人修"的库再次出现（2026-09-18 smartmoney 打到 24.0272% free_blocks
就是 db_invariants 断言覆盖面扩到 5 个库、压缩钩子仍停在 3 个写者上的直接后果）。

owner: 本文件 + ``backend/config/db_compaction.yaml``。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from services.database_manifest import get_database_manifest

__all__ = [
    "DbCompactionConfig",
    "DbCompactionConfigError",
    "load_db_compaction_config",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "backend" / "config" / "db_compaction.yaml"

_EXPECTED_ROOT_KEYS = {"version", "trigger_free_block_pct", "databases", "min_free_disk_gb"}


class DbCompactionConfigError(ValueError):
    """Config 缺键 / 未知键 / 类型不对 / 悬空别名引用 -> fail closed，不做默认值兜底。"""


@dataclass(frozen=True)
class DbCompactionConfig:
    """typed 快照；由 :func:`load_db_compaction_config` 产出，不接受直接构造。"""

    version: int
    trigger_free_block_pct: float
    databases: tuple[str, ...]
    min_free_disk_gb: float


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DbCompactionConfigError(f"{field} must be a mapping, got {type(value).__name__}")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise DbCompactionConfigError(f"{field} missing keys: {missing}")
    if unknown:
        raise DbCompactionConfigError(f"{field} unknown keys: {unknown}")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DbCompactionConfigError(f"{field} must be a number, got {type(value).__name__}")
    return float(value)


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise DbCompactionConfigError(f"{field} must be a non-empty list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise DbCompactionConfigError(f"{field} entries must be non-empty strings, got {item!r}")
        out.append(item)
    return tuple(out)


def load_db_compaction_config(path: Path | str | None = None) -> DbCompactionConfig:
    """Load one strict config snapshot.

    Missing/unknown top-level keys, non-numeric thresholds, or a ``databases``
    alias absent from ``database_manifest.yaml`` all fail closed (raise), never
    silently default (CLAUDE.md #11: 未知键/悬空引用 fail-closed)。
    """
    p = Path(path) if path is not None else _CONFIG_PATH
    if not p.is_file():
        raise DbCompactionConfigError(f"missing db_compaction.yaml: {p}")
    try:
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DbCompactionConfigError(f"unreadable db_compaction.yaml: {exc}") from exc
    raw = _mapping(loaded, "root")
    _exact_keys(raw, _EXPECTED_ROOT_KEYS, "root")

    version = raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise DbCompactionConfigError("version must be a positive int")

    trigger_free_block_pct = _number(raw["trigger_free_block_pct"], "trigger_free_block_pct")
    if not (0 < trigger_free_block_pct < 100):
        raise DbCompactionConfigError("trigger_free_block_pct must satisfy 0 < value < 100")

    databases = _string_list(raw["databases"], "databases")
    known_aliases = set(get_database_manifest().databases)
    dangling = sorted(set(databases) - known_aliases)
    if dangling:
        raise DbCompactionConfigError(
            f"databases dangling alias(es) not registered in database_manifest.yaml: "
            f"{dangling} (rule 11: 悬空引用 fail-closed)"
        )

    min_free_disk_gb = _number(raw["min_free_disk_gb"], "min_free_disk_gb")
    if min_free_disk_gb < 0:
        raise DbCompactionConfigError("min_free_disk_gb must be >= 0")

    return DbCompactionConfig(
        version=version,
        trigger_free_block_pct=trigger_free_block_pct,
        databases=databases,
        min_free_disk_gb=min_free_disk_gb,
    )
