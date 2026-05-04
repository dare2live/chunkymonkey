"""测试公共夹具.

CLAUDE.md 规则 #11: 测试必须用与生产一致的 DB 引擎。
本文件提供 ``duck_mem()`` 辅助, 内部走 ``services.duck_adapter.connect(':memory:')``,
返回的对象支持 execute/executemany/executescript/cursor/fetchall/fetchone/
commit/rollback/close + Row dict 索引。新测试不要引入其它内存数据库替身。
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
    """返回一个内存 DuckDB 连接。"""

    return _duck_connect(":memory:", attach=attach)


# 历史 import 形式: 一些测试 ``from conftest import duck_mem``;
# 也允许 ``import conftest as c; c.duck_mem()``.
__all__ = ["duck_mem", "DuckConn"]
