"""测试进程内的生产库守卫.

指向 `<checkout>/data` 与 `<git 主 worktree>/data` 的 duckdb 打开: 读写一律拒绝;
只读只放行带 `@pytest.mark.live_db_readonly`(或既有 `realdb`)标记的用例。违规同时
记账 (`_STATE["violations"]`), 用例 teardown 时凡有账就 `pytest.fail` —— 被
`try/except Exception` 吞掉的违规也逃不掉 (这是"响亮"的关键: 生产代码里常见的
`except Exception: 降级` 模式不能替被守护的越界打开消音)。

install() 必须在 conftest **导入时**调用 (不能放进 fixture): 只有这样, 后续被导入的
`services.sandbox_guard` 在模块级 `_ORIG_CONNECT = duckdb.connect` 捕获的才是本模块的
`_guarded_connect`, 而不是裸 duckdb.connect —— 否则 sandbox_guard 的只读放行路径会绕开
本守卫, 见 test_isolation_r1.md §1.5。
"""
from __future__ import annotations

import functools
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest

TEST_DIR = Path(__file__).resolve().parent


class LiveDbAccessError(RuntimeError):
    """测试进程尝试打开受守生产库。"""


ALERT_FLAG_DIR = Path("/tmp")
ALERT_FLAG_GLOB = "chunkymonkey_ALERT_*.flag"
MARKERS_ALLOWING_READONLY = ("live_db_readonly", "realdb")

_STATE: dict = {"item": None, "violations": []}
_ORIG_CONNECT = duckdb.connect  # 模块首次导入时的真 connect
_ORIG_ATTACH = None  # install() 时取 services.duck_adapter.attach_with_retry
_INSTALLED = False


@functools.lru_cache(maxsize=1)
def guarded_data_dirs() -> frozenset:
    """{realpath(<本 checkout>/data)} ∪ {realpath(<git 主 worktree>/data)}。

    目录不需要真实存在 (RW 打开会**创建**文件, 必须在创建前拦, 不能靠"文件已存在"判断)。
    """

    dirs: set[str] = set()
    own = TEST_DIR.parents[1] / "data"
    dirs.add(os.path.realpath(str(own)))
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(TEST_DIR),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        common_dir = out.stdout.strip()
        if common_dir:
            main_data = Path(common_dir).resolve().parent / "data"
            dirs.add(os.path.realpath(str(main_data)))
    except Exception:  # noqa: BLE001 — git 不可用时只守自己的 data/
        pass
    return frozenset(dirs)


def is_guarded_path(database) -> str | None:
    """database 是否落在受守目录下; 返回 realpath, 否则 None。

    None / "" / ":memory:" 前缀 / 非 str|PathLike → None。不看后缀, 不看文件是否存在——
    判定只用目录前缀, 因为 RW 打开会在受守目录下新建一个此前不存在的文件。
    """

    if database is None:
        return None
    if isinstance(database, str):
        if database == "" or database.startswith(":memory:"):
            return None
    elif isinstance(database, os.PathLike):
        pass
    else:
        return None
    try:
        rp = os.path.realpath(os.fspath(database))
    except Exception:  # noqa: BLE001
        return None
    for d in guarded_data_dirs():
        if not d:
            continue
        if rp == d or rp.startswith(d + os.sep):
            return rp
    return None


def _read_only_of(args, kwargs) -> bool:
    if "read_only" in kwargs:
        return bool(kwargs["read_only"])
    if args:
        return bool(args[0])
    cfg = kwargs.get("config")
    if isinstance(cfg, dict):
        try:
            return str(cfg.get("access_mode", "")).upper() == "READ_ONLY"
        except Exception:  # noqa: BLE001
            return False
    return False


def _decide(path: str, read_only: bool) -> None:
    item = _STATE.get("item")
    nodeid = item.nodeid if item is not None else "<no test item>"
    marked = bool(
        item is not None
        and any(item.get_closest_marker(m) for m in MARKERS_ALLOWING_READONLY)
    )
    if read_only and marked:
        return
    mode = "read_only" if read_only else "READ_WRITE"
    hint = (
        "read_write is never allowed in tests: use :memory:, tmp_path or the tmp_manifest fixture"
        if not read_only
        else "mark the test @pytest.mark.live_db_readonly if it is designed to read the local live db, else use a fixture"
    )
    msg = f"opened live db {mode}: {path} in {nodeid}; {hint}"
    _STATE["violations"].append(msg)
    raise LiveDbAccessError(msg)


def _guarded_connect(database=":memory:", *args, **kwargs):
    rp = is_guarded_path(kwargs.get("database", database))
    if rp is not None:
        _decide(rp, _read_only_of(args, kwargs))
    return _ORIG_CONNECT(database, *args, **kwargs)


def _guarded_attach(conn, alias, db_path, *, read_only, timeout=30):
    rp = is_guarded_path(db_path)
    if rp is not None:
        _decide(rp, bool(read_only))
    return _ORIG_ATTACH(conn, alias, db_path, read_only=read_only, timeout=timeout)


def install() -> None:
    """幂等。必须在 conftest 导入时调用 (不是 fixture 里) —— 见模块 docstring。"""

    global _INSTALLED, _ORIG_ATTACH
    if _INSTALLED:
        return
    duckdb.connect = _guarded_connect  # type: ignore[assignment]
    from services import duck_adapter

    _ORIG_ATTACH = duck_adapter.attach_with_retry
    duck_adapter.attach_with_retry = _guarded_attach  # type: ignore[assignment]
    _INSTALLED = True


def take_violations() -> list:
    """返回并清空当前累积的违规账 (自测用)。"""

    v = list(_STATE["violations"])
    _STATE["violations"] = []
    return v


@contextmanager
def expect_violation():
    """with 块内必须抛出 LiveDbAccessError; 退出时把这条账清掉 (自测用)。"""

    try:
        yield
    except LiveDbAccessError:
        take_violations()
    else:
        take_violations()
        pytest.fail("expected LiveDbAccessError, but none was raised")


def snapshot_alert_flags() -> dict:
    """{文件名: (mtime_ns, size)} —— 真实 /tmp 告警 flag 的哨兵快照。"""

    out: dict = {}
    try:
        for p in ALERT_FLAG_DIR.glob(ALERT_FLAG_GLOB):
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.name] = (st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return out


def assert_alert_flags_unchanged(before: dict) -> None:
    after = snapshot_alert_flags()
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(k for k in (set(after) & set(before)) if after[k] != before[k])
    if added or removed or modified:
        pytest.fail(
            "real alert flag changed during test: "
            f"added={added} modified={modified} removed={removed}"
        )


def pytest_runtest_setup(item) -> None:
    _STATE["item"] = item
    _STATE["violations"] = []


def pytest_runtest_teardown(item) -> None:
    v = _STATE["violations"]
    _STATE["violations"] = []
    _STATE["item"] = None
    if v:
        pytest.fail(
            "test opened live db (violation recorded even if the exception was "
            "swallowed):\n" + "\n".join(v)
        )
