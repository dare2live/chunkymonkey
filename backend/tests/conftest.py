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

# 生产库守卫必须在**任何** services 模块被导入之前装好 (次序陷阱见
# _livedb_guard 模块 docstring / test_isolation_r1.md §1.5): 否则后续被导入的
# services.sandbox_guard 会在自己的模块级 `_ORIG_CONNECT = duckdb.connect` 里捕获到
# 裸 duckdb.connect, 它的只读放行路径就会绕开本守卫。
import _livedb_guard

_livedb_guard.install()
from _livedb_guard import pytest_runtest_setup, pytest_runtest_teardown  # noqa: F401,E402 — pytest 按名字收 hook

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


@pytest.fixture(autouse=True)
def _isolate_alert_flags(monkeypatch, tmp_path):
    """真实告警旗标的唯一进程内写者是 ``context.degraded()``; 每例重定向到 tmp。

    ``PipelineContext.__post_init__`` 在没显式给 ``log_path`` 时取
    ``DEGRADED_FLAG.parent``, 所以重定向 DEGRADED_FLAG 同时把默认日志文件也带去 tmp
    (2026-09-11 实测: 未隔离的用例真的写了两次 /tmp/chunkymonkey_ALERT_daily_update_degraded.flag)。
    """

    from services.pipeline import context

    monkeypatch.setattr(
        context, "DEGRADED_FLAG", tmp_path / "chunkymonkey_ALERT_daily_update_degraded.flag"
    )


@pytest.fixture(autouse=True)
def _real_alert_flag_sentinel():
    """跑前后快照真实 /tmp 告警 flag 集合; 变了就 fail (证明没有代码路径逃过上面的隔离)。"""

    before = _livedb_guard.snapshot_alert_flags()
    yield
    _livedb_guard.assert_alert_flags_unchanged(before)


@pytest.fixture
def tmp_manifest(tmp_path, monkeypatch):
    """manifest 根指到 ``tmp_path/repo``: 所有走 database_manifest 路由的代码落 tmp。

    覆盖三处导入期绑定 (``resolver._MANIFEST`` / ``db_connection._MANIFEST`` /
    ``db_connection.DB_PATH`` / ``db_connection.DB_DIR``) 与 ``database_manifest`` 自身的
    单例缓存 (``_CACHED``, 供 ``get_database_manifest()`` 的直接调用方——如
    ``project_status._connect`` / ``sandbox_guard._main_db_paths``——生效)。

    ``services.margin_acceptance._FROZEN_LIVE_DB`` (导入期 ``path_for("tushare_raw").resolve()``)
    也是导入期绑定, 本夹具不覆盖它 (本轮改动的 35 例都不经过它)。
    """

    from services import database_manifest as dm, db_connection
    from services.data_access import resolver

    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    mf = dm.load_database_manifest(repo_root=root)
    monkeypatch.setattr(dm, "_CACHED", mf)
    monkeypatch.setattr(resolver, "_MANIFEST", mf)
    monkeypatch.setattr(db_connection, "_MANIFEST", mf)
    monkeypatch.setattr(db_connection, "DB_PATH", mf.path_for("smartmoney"))
    monkeypatch.setattr(db_connection, "DB_DIR", mf.path_for("smartmoney").parent)
    return mf


# 历史 import 形式: 一些测试 ``from conftest import duck_mem``;
# 也允许 ``import conftest as c; c.duck_mem()``.
# ``tmp_manifest``: 需要真实 database_manifest 路由 (resolver.connect_ro /
# services.db.get_conn / project_status._connect 等) 落在 tmp 而不是 <repo>/data 的用例用它。
__all__ = ["duck_mem", "DuckConn", "tmp_manifest"]
