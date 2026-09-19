"""data_layer_audit.py `_live_tables()` 的 `_` 前缀豁免回归测试 (2026-09-18
cut_lineage_drift §2.4, 返修 blocking finding 补测)。

背景: ruling_lineage_drift.md §2.4 要求删两份 `_` 前缀豁免——
`services/lineage/builder.py:83`(已有 H1 隔离用例覆盖, 见
`backend/tests/services/test_lineage.py::test_H1_underscore_prefixed_table_no_longer_hides_from_ghosts`)
与 `backend/scripts/data_layer_audit.py:68`。后者删豁免时没有配套测试——全仓
grep 找不到任何测试 import 或调用 `_live_tables()`, 把刚删掉的
`not r[0].startswith("_")` 条件加回去不会有任何测试变红。本文件补上这一份,
用真实 (临时) DuckDB 文件验证 `_` 前缀表不再天然隐身于 untagged 执法之外。

自足 fixture (feedback-test-must-carry-its-own-fixture): 不连生产库, 用
tmp_path 建临时 duckdb 文件, monkeypatch `_db_path` 指向它。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "data_layer_audit", REPO / "backend" / "scripts" / "data_layer_audit.py")
dla = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dla)


def test_underscore_prefixed_table_is_reported_live_not_hidden(tmp_path, monkeypatch):
    """其它全满足只违反它的隔离用例: 一张 `_scratch` 表, 无任何登记, 单一受管库
    → `_live_tables()` 必须把它算作 live (untagged 执法才能抓到它)。

    变异: 把 `_live_tables` 里 `if not r[0].startswith("_")` 的过滤条件加回去
    → 本用例断言 `_scratch` in live 会红 (fixture 只造了这一张表, 加回豁免后
    live 会变成空集, "_scratch" not in set() 使断言直接失败)。
    """
    db_path = tmp_path / "scratch.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE _scratch (id INTEGER)")
    conn.close()

    monkeypatch.setattr(dla, "_db_path", lambda key: db_path)

    live = dla._live_tables(dbs=("scratch_db",))
    assert "_scratch" in live, (
        "`_` 前缀表必须出现在 live 集合里, 否则会永久躲过 untagged 执法 "
        "(ruling_lineage_drift.md §2.4)"
    )


def test_non_underscore_table_still_reported_live(tmp_path, monkeypatch):
    """回归锁: 普通表名的既有行为不受这次删豁免影响 (§2.4 只删 `_` 前缀这一条
    路径, 不改其它)。"""
    db_path = tmp_path / "scratch2.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE normal_table (id INTEGER)")
    conn.close()

    monkeypatch.setattr(dla, "_db_path", lambda key: db_path)

    live = dla._live_tables(dbs=("scratch_db",))
    assert live == {"normal_table"}
