"""测试公共夹具.

CLAUDE.md 规则 #11: 测试必须用与生产一致的 DB 引擎 (DuckDB), 不准用 sqlite3.
本文件提供 ``duck_mem()`` 辅助, 内部走 ``services.duck_adapter.connect(':memory:')``,
返回的对象 API 完全兼容 sqlite3.Connection 的常用方法 (execute/executemany/
executescript/cursor/fetchall/fetchone/commit/rollback/close + Row dict 索引).

迁移指南 (sqlite3 → DuckDB):
- 删 ``import sqlite3``
- ``sqlite3.connect(':memory:')`` → ``duck_mem()`` (来自本 conftest)
- 删 ``conn.row_factory = sqlite3.Row`` (DuckConn 默认返回 Row 对象)
- ``sqlite3.OperationalError`` → ``Exception`` 或具体的 ``duckdb.Error``
  (大多数测试不应该 catch 这个, 让 DuckDB 真错暴露出来)
- ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``INTEGER PRIMARY KEY`` (DuckDB 自增需 SEQUENCE)
- 时间函数: ``datetime('now')`` → ``CURRENT_TIMESTAMP``;
            ``julianday(x)`` → ``EXTRACT(EPOCH FROM x) / 86400``
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让所有 backend/tests/* 都能 ``from conftest import duck_mem``
TEST_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TEST_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from services.duck_adapter import connect as _duck_connect, DuckConn  # noqa: E402


def duck_mem(*, attach: dict | None = None) -> DuckConn:
    """返回一个内存 DuckDB 连接, API 兼容旧测试中的 ``sqlite3.connect(':memory:')``."""

    return _duck_connect(":memory:", attach=attach)


# 历史 import 形式: 一些测试 ``from conftest import duck_mem``;
# 也允许 ``import conftest as c; c.duck_mem()``.
__all__ = ["duck_mem", "DuckConn"]
