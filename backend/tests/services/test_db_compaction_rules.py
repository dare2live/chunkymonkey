"""backend/config/db_compaction.yaml typed loader 测试 (cut_db_compaction 2026-09-19)。

A1: 真配置可加载 + 未知顶层键/缺键/阈值非数/悬空别名各自独立报错 (隔离用例——每条
只违反一个条件, 其它字段全合法)。
A2: 真 db_compaction.yaml 与真 db_invariants.yaml 的库集合相等——防止"有报警、没人
修"的库再次出现 (2026-09-18 smartmoney 打到 24.0272% free_blocks 正是二者不同步:
db_invariants 断言覆盖面扩到 5 个库, 压缩钩子仍停在 3 个写者上)。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "db_compaction.yaml"
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return p


def _valid_payload() -> dict:
    return {
        "version": 1,
        "trigger_free_block_pct": 5.0,
        "databases": ["smartmoney"],
        "min_free_disk_gb": 10,
    }


def test_real_config_loads():
    from services.db_compaction_rules import load_db_compaction_config

    cfg = load_db_compaction_config()
    assert cfg.version == 1
    assert cfg.trigger_free_block_pct > 0
    assert len(cfg.databases) >= 1
    assert cfg.min_free_disk_gb >= 0


def test_unknown_top_level_key_fails_closed(tmp_path):
    from services.db_compaction_rules import DbCompactionConfigError, load_db_compaction_config

    payload = _valid_payload()
    payload["extra_bogus_key"] = 1
    path = _write(tmp_path, payload)
    with pytest.raises(DbCompactionConfigError, match="extra_bogus_key"):
        load_db_compaction_config(path)


def test_missing_trigger_free_block_pct_fails_closed(tmp_path):
    from services.db_compaction_rules import DbCompactionConfigError, load_db_compaction_config

    payload = _valid_payload()
    del payload["trigger_free_block_pct"]
    path = _write(tmp_path, payload)
    with pytest.raises(DbCompactionConfigError, match="trigger_free_block_pct"):
        load_db_compaction_config(path)


def test_threshold_non_numeric_fails_closed(tmp_path):
    from services.db_compaction_rules import DbCompactionConfigError, load_db_compaction_config

    payload = _valid_payload()
    payload["trigger_free_block_pct"] = "five"
    path = _write(tmp_path, payload)
    with pytest.raises(DbCompactionConfigError, match="trigger_free_block_pct"):
        load_db_compaction_config(path)


def test_missing_config_file_fails_closed(tmp_path):
    """A1 补: 文件不存在 -> DbCompactionConfigError, 不是裸 FileNotFoundError

    (blocking finding cut_db_compaction: load_db_compaction_config 曾直接调用
    p.read_text()，store.py 的 except DbCompactionConfigError 接不住
    FileNotFoundError，导致整个 store 阶段崩溃、日报全无)。
    """
    from services.db_compaction_rules import DbCompactionConfigError, load_db_compaction_config

    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(DbCompactionConfigError, match="missing db_compaction.yaml"):
        load_db_compaction_config(missing)


def test_malformed_yaml_fails_closed(tmp_path):
    """A1 补: YAML 语法损坏 -> DbCompactionConfigError, 不是裸 yaml.YAMLError。"""
    from services.db_compaction_rules import DbCompactionConfigError, load_db_compaction_config

    path = tmp_path / "db_compaction.yaml"
    path.write_text("version: 1\n  bad_indent: [unclosed\n", encoding="utf-8")
    with pytest.raises(DbCompactionConfigError, match="unreadable db_compaction.yaml"):
        load_db_compaction_config(path)


def test_databases_dangling_alias_fails_closed(tmp_path):
    from services.db_compaction_rules import DbCompactionConfigError, load_db_compaction_config

    payload = _valid_payload()
    payload["databases"] = ["smartmoney", "this_alias_does_not_exist_anywhere"]
    path = _write(tmp_path, payload)
    with pytest.raises(DbCompactionConfigError, match="this_alias_does_not_exist_anywhere"):
        load_db_compaction_config(path)


def test_databases_set_matches_db_invariants_bloat_checks():
    """A2: 真 db_compaction.yaml.databases == 真 db_invariants.yaml 里 bloat_ratio_ 前缀检查的库集合。"""
    from services.db_compaction_rules import load_db_compaction_config

    repo = Path(__file__).resolve().parents[3]
    invariants_raw = yaml.safe_load(
        (repo / "backend" / "config" / "db_invariants.yaml").read_text(encoding="utf-8")
    )
    bloat_dbs = {
        entry["db"]
        for entry in invariants_raw.get("invariants", [])
        if str(entry.get("id", "")).startswith("bloat_ratio_")
    }
    assert bloat_dbs, "db_invariants.yaml 应至少有一条 bloat_ratio_* 检查 (fixture 假设崩了)"

    cfg = load_db_compaction_config()
    assert set(cfg.databases) == bloat_dbs
