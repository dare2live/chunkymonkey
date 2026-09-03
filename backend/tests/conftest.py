"""测试公共夹具.

测试必须用与生产一致的 DB 引擎 (CLAUDE.md 红线 2 / 数据 6)。
本文件提供 ``duck_mem()`` 辅助, 内部走 ``services.duck_adapter.connect(':memory:')``,
返回的对象支持 execute/executemany/executescript/cursor/fetchall/fetchone/
commit/rollback/close + Row dict 索引。新测试不要引入其它内存数据库替身。
"""

from __future__ import annotations

import re
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


@pytest.fixture(autouse=True)
def _isolate_tdxhub_host_memory(monkeypatch, tmp_path_factory, request):
    """把 tdxhub 主机记忆文件指向 pytest 临时目录, 不碰工作站真实文件。

    与上面 ``deterministic_margin_calendar`` 同型 (测试不得读写工作站运行时
    状态)。2026-08-30 主机记忆从进程内 dict 改为跨进程落盘后, 未隔离的用例会把
    测试假主机写进真实的 ``data/scratch/tdxhub_host_memory.json``: 实测
    ``test_tdxhub_kline_recon.py`` 首跑绿并写出 ``{"hq": {"ip": "3.3.3.3"}}``,
    次跑即红 —— 污染跨进程存活, 本地与 CI 都会从第二次起持续失败。
    每个用例给一个独立文件名, 用例之间也不互相串。
    """

    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.nodeid)[-80:]
    target = tmp_path_factory.getbasetemp() / f"tdxhub_host_memory_{stem}.json"
    monkeypatch.setenv("TDXHUB_HOST_MEMORY_PATH", str(target))


# 历史 import 形式: 一些测试 ``from conftest import duck_mem``;
# 也允许 ``import conftest as c; c.duck_mem()``.
__all__ = ["duck_mem", "DuckConn"]
