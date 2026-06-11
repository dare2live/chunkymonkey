"""champion model 单一真相源 reader (governance).

背景 (2026-06-11 体检 HIGH): champion model_id 散在 3 处且冲突:
  - scripts/daily_update.sh:50  CHAMPION_MODEL_ID=lgbm_20260517_governance_v1_20d
  - SESSION_HANDOFF.md:34       lgbm_phase5_v9b_20260523T083000Z (实为 F2 训练模型)
  - smartmoney.duckdb::mart_model_lifecycle status='champion' = 运行时真相

第一性原理 (CLAUDE.md §1.0): champion 的运行时真相源是 DB lifecycle 表, 不是任何 hardcode
字符串. 本模块:
  1. load_champion_registry() — 读 backend/config/champion_registry.yaml (治理声明).
  2. get_expected_champion_model_id() — 返回声明的 champion (脚本/服务统一从这里取, 不各写各的).
  3. assert_consistent_with_db() — 把 yaml 声明跟 DB lifecycle 真相对账, 不一致 raise.

用法:
    from services.trading_config.champion_registry import get_expected_champion_model_id
    mid = get_expected_champion_model_id()

    # CI / 启动 health check:
    from services.trading_config.champion_registry import assert_consistent_with_db
    assert_consistent_with_db()  # yaml 声明 vs DB lifecycle 真相不一致即 raise
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "champion_registry.yaml"
# 仓库根 = backend 的上级
_REPO_ROOT = Path(__file__).resolve().parents[3]


class ChampionRegistryError(RuntimeError):
    """champion 声明与 DB 真相源不一致 / 配置缺失."""


@dataclass(frozen=True)
class ChampionRegistry:
    expected_model_id: str
    promoted_at: str
    source_commit: str
    f2_training_model_id: str | None
    db_database: str
    db_table: str
    db_status_value: str
    raw: dict[str, Any]


def load_champion_registry(path: Path | None = None) -> ChampionRegistry:
    """读 champion_registry.yaml 治理声明 (单一真相源)."""
    p = path or _CONFIG_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ChampionRegistryError(f"{p.name} 必须是 mapping")
    expected = raw.get("expected") or {}
    if not expected.get("model_id"):
        raise ChampionRegistryError(f"{p.name}: expected.model_id 缺失")
    db = raw.get("db_truth_source") or {}
    roles = raw.get("roles") or {}
    f2 = (roles.get("f2_training_model") or {}).get("model_id")
    return ChampionRegistry(
        expected_model_id=str(expected["model_id"]),
        promoted_at=str(expected.get("promoted_at", "unknown")),
        source_commit=str(expected.get("source_commit", "unknown")),
        f2_training_model_id=f2,
        # rule-compliance: ok evidence=yaml-fallback-default; 主值来自 champion_registry.yaml db_truth_source.database
        db_database=str(db.get("database", "smartmoney.duckdb")),
        db_table=str(db.get("table", "mart_model_lifecycle")),
        db_status_value=str(db.get("status_value", "champion")),
        raw=raw,
    )


def get_expected_champion_model_id(path: Path | None = None) -> str:
    """声明的 champion model_id — 脚本/服务统一入口, 替代散落 hardcode."""
    return load_champion_registry(path).expected_model_id


def get_db_champion_model_id(
    registry: ChampionRegistry | None = None,
    db_path: Path | None = None,
) -> str | None:
    """从 DB lifecycle 真相源读当前 champion (read-only). 表/库不存在返回 None.

    只读连接 (绝不写生产库, 走中央 duck_adapter). 测试可传 db_path 指 fixture.
    """
    from services.duck_adapter import connect

    reg = registry or load_champion_registry()
    p = db_path or (_REPO_ROOT / "data" / reg.db_database)
    if not p.exists():
        return None
    con = connect(str(p), read_only=True)
    try:
        exists = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [reg.db_table],
        ).fetchone()
        if not exists or not exists[0]:
            return None
        row = con.execute(
            f"SELECT model_id FROM {reg.db_table} WHERE status = ? "
            "ORDER BY model_id LIMIT 1",
            [reg.db_status_value],
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def assert_consistent_with_db(
    registry: ChampionRegistry | None = None,
    db_path: Path | None = None,
    *,
    require_db: bool = True,
) -> None:
    """对账: yaml 声明的 champion == DB lifecycle 真相. 不一致 raise.

    Args:
        require_db: True 时 DB/表缺失也视为 fail (默认, 防静默漏对账);
            False 时 DB 不可用 (e.g. CI 无生产库) 跳过 — 仅用于无 DB 环境.
    """
    reg = registry or load_champion_registry()
    db_champion = get_db_champion_model_id(reg, db_path)
    if db_champion is None:
        if require_db:
            raise ChampionRegistryError(
                f"DB 真相源不可读 ({reg.db_database}::{reg.db_table} status="
                f"{reg.db_status_value}) — 无法对账 champion. "
                "require_db=False 可在无 DB 环境跳过."
            )
        return
    if db_champion != reg.expected_model_id:
        raise ChampionRegistryError(
            "champion 真相源分裂! champion_registry.yaml expected="
            f"{reg.expected_model_id} != DB {reg.db_table}.status="
            f"{reg.db_status_value} = {db_champion}. "
            "先在 mart_model_lifecycle promote, 再同步 champion_registry.yaml; "
            "不允许只改 yaml/脚本绕过 DB 真相."
        )
