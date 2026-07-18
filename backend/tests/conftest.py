"""测试公共夹具.

docs/engineering_governance.md §5: 测试必须用与生产一致的 DB 引擎。
本文件提供 ``duck_mem()`` 辅助, 内部走 ``services.duck_adapter.connect(':memory:')``,
返回的对象支持 execute/executemany/executescript/cursor/fetchall/fetchone/
commit/rollback/close + Row dict 索引。新测试不要引入其它内存数据库替身。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

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


@pytest.fixture
def deterministic_margin_calendar(monkeypatch):
    """Keep acceptance tests isolated from the workstation reference DB.

    Policy-specific weekend/holiday behavior is tested separately with explicit
    calendar vectors; ordinary transaction tests only need a stable partition
    and its next weekday session.
    """

    from services.data_sources import margin_validation

    def _days(
        partition: str, *, limit: int | None = 2
    ) -> tuple[str, ...]:
        current = datetime.strptime(partition, "%Y%m%d").date()
        values = [partition]
        target = limit if limit is not None else 32
        following = current
        while len(values) < target:
            following += timedelta(days=1)
            if following.weekday() < 5:
                values.append(following.strftime("%Y%m%d"))
        return tuple(values)

    monkeypatch.setattr(
        margin_validation,
        "load_margin_publication_sessions",
        _days,
    )


# 历史 import 形式: 一些测试 ``from conftest import duck_mem``;
# 也允许 ``import conftest as c; c.duck_mem()``.
__all__ = ["duck_mem", "DuckConn"]
