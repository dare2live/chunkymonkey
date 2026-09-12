"""``_livedb_guard`` 自测 (每条只违反它宣称守护的那一条, 其它全满足)。

见 test_isolation_r1.md §4.6。G1-G11 在本进程内直接触发违规/放行; G12-G13 用
``pytester`` 跑一个真实子进程, 证明"被 try/except 吞掉的违规"与"写真实告警旗标"
在 teardown 阶段仍然会让用例变红——不是靠调用方老实转发异常。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import duckdb
import pytest

import _livedb_guard

BACKEND = Path(__file__).resolve().parents[1]
TESTS_DIR = BACKEND / "tests"

# pytest 在这台机器上是 user site-packages 安装 (不在 .venv 自己的 site-packages 里)。
# pytester 的 runpytest_subprocess 会把子进程 HOME 重定向到它自己的 tmp 目录 (隔离
# 真实 ~/.config 一类文件) —— 副作用: site.getusersitepackages() 跟着 HOME 漂移,
# 子进程 `python -m pytest` 会报 "No module named pytest" (与本次改动无关的既有环境
# 事实, 2026-09-12 实测确认)。在此模块导入时 (pytester 还没来得及改 HOME 之前) 把当前
# 真实 sys.path 存下来, G12/G13 显式把它塞进子进程 PYTHONPATH。
_INHERITED_SITE_PATHS = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))

GUARDED = sorted(_livedb_guard.guarded_data_dirs())[0]
probe = Path(GUARDED) / f"_guard_probe_{os.getpid()}.duckdb"


@pytest.fixture(autouse=True)
def _probe_absent_before_and_after():
    assert not probe.exists(), f"probe 不应预先存在: {probe}"
    yield
    assert not probe.exists(), f"probe 不应被任何用例真的创建: {probe}"


def test_rw_open_of_guarded_path_raises_and_creates_nothing():
    with _livedb_guard.expect_violation():
        duckdb.connect(str(probe))
    assert not probe.exists()


def test_unmarked_ro_open_of_guarded_path_raises():
    with _livedb_guard.expect_violation():
        duckdb.connect(str(probe), read_only=True)


@pytest.mark.live_db_readonly
def test_marked_ro_open_passes_through():
    # 放行到了真 connect (不是 LiveDbAccessError) —— 但 probe 不存在, 所以真 connect
    # 自己会抛 IOException; 这正好证明了"放行"而不是"路径没被识别成受守"。
    with pytest.raises(duckdb.IOException):
        duckdb.connect(str(probe), read_only=True)


@pytest.mark.live_db_readonly
def test_marker_never_allows_rw():
    with _livedb_guard.expect_violation():
        duckdb.connect(str(probe))


def test_live_db_readonly_marker_is_registered(pytestconfig):
    """M25: pytest.ini 里的 ``live_db_readonly`` 登记行被删掉时, collection 只报
    PytestUnknownMarkWarning 不报失败——本用例直接查 ini 登记表, 登记消失即变红。"""

    markers = pytestconfig.getini("markers")
    assert any(m.split(":")[0].strip() == "live_db_readonly" for m in markers), markers
    (registered,) = (m for m in markers if m.split(":")[0].strip() == "live_db_readonly")
    description = registered.split(":", 1)[1]
    assert "read_only" in description, description
    assert "read_write" in description, description


def test_duck_adapter_connect_is_covered():
    from services.duck_adapter import connect

    with _livedb_guard.expect_violation():
        connect(str(probe), read_only=False)


def test_attach_spec_is_covered():
    from services.duck_adapter import connect

    # DuckConn.__init__ 对 attach 失败只 logger.warning, 不向上抛——所以这里靠
    # teardown 记账变红, 不是这次调用本身抛异常。
    c = connect(":memory:", attach={"x": {"path": str(probe), "read_only": False}})
    c.close()
    v = _livedb_guard.take_violations()
    assert len(v) == 1
    assert "READ_WRITE" in v[0]
    assert str(probe) in v[0]


def test_tmp_data_dir_is_not_guarded(tmp_path):
    p = tmp_path / "data" / "x.duckdb"
    p.parent.mkdir()
    assert _livedb_guard.is_guarded_path(p) is None
    duckdb.connect(str(p)).close()
    assert p.exists()


def test_sandbox_guard_disable_keeps_livedb_guard():
    from services import sandbox_guard

    sandbox_guard.enable_sandbox_guard()
    sandbox_guard.disable_sandbox_guard()
    assert duckdb.connect is _livedb_guard._guarded_connect
    with _livedb_guard.expect_violation():
        duckdb.connect(str(probe))


def test_degraded_flag_is_redirected_per_test(tmp_path):
    from services.pipeline import context
    from services.pipeline.context import PipelineContext

    assert context.DEGRADED_FLAG.parent == tmp_path
    real = Path("/tmp/chunkymonkey_ALERT_daily_update_degraded.flag")
    before = real.stat() if real.exists() else None

    ctx = PipelineContext(date="20260101")
    ctx.degraded("probe")
    ctx.close()

    assert context.DEGRADED_FLAG.read_text().endswith("probe\n")
    after = real.stat() if real.exists() else None
    assert (before is None) == (after is None)
    if before is not None and after is not None:
        assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)


def test_alert_sentinel_detects_change(tmp_path):
    """不用 monkeypatch 改 ALERT_FLAG_DIR: 实测 conftest 的 autouse
    ``_real_alert_flag_sentinel`` 先于 ``monkeypatch`` 的 fixture finalizer 跑 (fixture
    teardown 顺序不是本用例期望的那样), 若用 monkeypatch.setattr, 哨兵会在
    ALERT_FLAG_DIR 被还原**之前**执行自己的收尾比对, 对着还指向 tmp 的假目录算 diff,
    产生与本用例无关的假 real-alert-flag-changed —— 手动 try/finally 在测试体内完成
    改回, 不依赖 fixture 拆除顺序。"""
    d = tmp_path / "flags"
    d.mkdir()
    saved = _livedb_guard.ALERT_FLAG_DIR
    _livedb_guard.ALERT_FLAG_DIR = d
    try:
        before = _livedb_guard.snapshot_alert_flags()
        (d / "chunkymonkey_ALERT_x.flag").write_text("x")
        with pytest.raises(pytest.fail.Exception, match="real alert flag changed"):
            _livedb_guard.assert_alert_flags_unchanged(before)
    finally:
        _livedb_guard.ALERT_FLAG_DIR = saved


def test_hooks_are_registered(request):
    hook = request.config.hook
    setup_impls = hook.pytest_runtest_setup.get_hookimpls()
    teardown_impls = hook.pytest_runtest_teardown.get_hookimpls()
    assert any(i.function is _livedb_guard.pytest_runtest_setup for i in setup_impls)
    assert any(i.function is _livedb_guard.pytest_runtest_teardown for i in teardown_impls)


_CONFTEST_MIN = (
    "import _livedb_guard\n"
    "_livedb_guard.install()\n"
    "from _livedb_guard import pytest_runtest_setup, pytest_runtest_teardown  # noqa: F401\n"
    "import pytest\n"
    "\n"
    "@pytest.fixture(autouse=True)\n"
    "def _isolate_alert_flags(monkeypatch, tmp_path):\n"
    "    from services.pipeline import context\n"
    "    monkeypatch.setattr(context, 'DEGRADED_FLAG', tmp_path / 'chunkymonkey_ALERT_daily_update_degraded.flag')\n"
    "\n"
    "@pytest.fixture(autouse=True)\n"
    "def _real_alert_flag_sentinel():\n"
    "    before = _livedb_guard.snapshot_alert_flags()\n"
    "    yield\n"
    "    _livedb_guard.assert_alert_flags_unchanged(before)\n"
)

# M11a: G12/G13 跑在 pytester 的子进程里, 用的是上面 _CONFTEST_MIN 这份硬编码副本,
# 完全不导入外层真实 conftest.py —— 实测把真实 conftest.py 里的
# ``_real_alert_flag_sentinel`` 整个删掉, G12/G13 与其余 11 条自测照样全绿。
# 下面两条分别钉住"真 conftest 装没装这个夹具"与"硬编码副本有没有跟真 conftest 脱节"。
_AUTOUSE_FIXTURE_RE = re.compile(r"@pytest\.fixture\(autouse=True\)\ndef (_\w+)\(")


def _autouse_fixture_names(source: str) -> set[str]:
    return set(_AUTOUSE_FIXTURE_RE.findall(source))


def test_real_conftest_mounts_alert_flag_sentinel(request):
    """直接问这条用例实际挂载的 fixture 集合——真 conftest.py 删掉
    ``_real_alert_flag_sentinel`` 时这条必须变红 (它不经过 pytester 子进程, 用的是
    本文件正常收 conftest.py 的那条真实路径)。"""

    assert "_real_alert_flag_sentinel" in request.fixturenames


def test_embedded_conftest_copy_matches_real_fixture_names():
    """钉住"硬编码 _CONFTEST_MIN 与真 conftest.py 脱节": 真 conftest.py 还有一个
    与告警旗标无关的 autouse 夹具 (``_isolate_tdxhub_host_memory``), _CONFTEST_MIN
    不需要镜像它——所以断言方向是"_CONFTEST_MIN 里出现的名字都能在真 conftest.py
    里找到同名 autouse 夹具", 不是完全相等。真 conftest.py 删掉或改名
    ``_real_alert_flag_sentinel``/``_isolate_alert_flags`` 时, 这条会变红。"""

    real_text = (Path(__file__).parent / "conftest.py").read_text()
    real_names = _autouse_fixture_names(real_text)
    assert {"_real_alert_flag_sentinel", "_isolate_alert_flags"} <= real_names, real_names

    embedded_names = _autouse_fixture_names(_CONFTEST_MIN)
    assert embedded_names, "regex 没有从 _CONFTEST_MIN 里取到任何夹具名, 先检查正则本身"
    assert embedded_names <= real_names, (embedded_names, real_names)


def _assert_teardown_flagged(result):
    """实测 (2026-09-12): pytest 把 teardown 阶段的 pytest.fail 计成 error, 但 call
    阶段本身仍算 passed —— 终端汇总是 "1 passed, 1 error", 不是设计草稿设想的
    "0 passed"。断言只认"确有一条 error/failed 记录", 不认 passed 计数。"""
    outcomes = result.parseoutcomes()
    assert outcomes.get("errors", 0) == 1 or outcomes.get("failed", 0) == 1, outcomes


def _inner_pythonpath() -> str:
    return os.pathsep.join([str(BACKEND), str(TESTS_DIR), _INHERITED_SITE_PATHS])


def test_swallowed_violation_still_fails_the_test(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", _inner_pythonpath())
    pytester.makeconftest(_CONFTEST_MIN)
    pytester.makeini("[pytest]\nmarkers =\n    live_db_readonly: x\n")
    inner_probe = Path(GUARDED) / f"_guard_probe_pytester_g12_{os.getpid()}.duckdb"
    pytester.makepyfile(
        f"""
        import duckdb

        def test_x():
            try:
                duckdb.connect({str(inner_probe)!r})
            except Exception:
                pass
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-q")
    _assert_teardown_flagged(result)
    result.stdout.fnmatch_lines(["*opened live db*"])
    assert not inner_probe.exists()


def test_writing_real_alert_flag_fails_the_test(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", _inner_pythonpath())
    flags_dir = tmp_path / "g13_flags"
    flags_dir.mkdir()
    pytester.makeconftest(
        _CONFTEST_MIN + f"\nfrom pathlib import Path as _P\n"
        f"_livedb_guard.ALERT_FLAG_DIR = _P({str(flags_dir)!r})\n"
    )
    pytester.makeini("[pytest]\nmarkers =\n    live_db_readonly: x\n")
    pytester.makepyfile(
        f"""
        from pathlib import Path

        def test_x():
            Path({str(flags_dir)!r}, "chunkymonkey_ALERT_x.flag").write_text("x")
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-q")
    _assert_teardown_flagged(result)
    result.stdout.fnmatch_lines(["*real alert flag changed*"])
