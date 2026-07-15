"""daily_update 四阶段管线 smoke 测试 (2026-06-23 重设计)。

锁住: (1) 包/各阶段可 import (防 port 笔误回归); (2) PipelineContext degraded/log 机制;
(3) run.main 编排顺序 preflight→获取→清洗→加工→存储 (monkeypatch 阶段, 不碰真 DB/网络)。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_pipeline_runtime_paths(tmp_path, monkeypatch):
    """Pipeline tests must not touch the production writer lock, logs, or alert flag."""
    from services.pipeline import context
    from services.writer_lock import WRITER_LOCK_PATH_ENV

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "pipeline-writer.lock"))
    monkeypatch.setattr(context, "DEGRADED_FLAG", tmp_path / "pipeline-alert.flag")


def test_pipeline_package_imports():
    """各阶段模块 + 入口可 import (catch port 笔误 / 死引用)。"""
    from services.pipeline import acquire, clean, context, preflight, process, run, store
    assert hasattr(run, "main")
    assert hasattr(acquire, "run_acquire")
    assert hasattr(clean, "run_clean")
    assert hasattr(process, "run_process")
    assert hasattr(store, "run_store")
    assert hasattr(preflight, "run_preflight")


def test_context_degraded_and_log(tmp_path, monkeypatch):
    from services.pipeline import context
    from services.pipeline.context import PipelineContext
    # degraded() 写全局 DEGRADED_FLAG (/tmp/chunkymonkey_ALERT_daily_update_degraded.flag) —
    # 隔离到 tmp_path 防测试污染真实生产告警文件 (2026-06-29 批4发现: 未隔离时 pytest 全量跑
    # 会把测试字面量"步骤X失败"写进真实 alert flag, 误导下次 session 启动检查)。
    monkeypatch.setattr(context, "DEGRADED_FLAG", tmp_path / "alert.flag")
    ctx = PipelineContext(dry=True, date="20260101", log_path=tmp_path / "t.log")
    ctx.degraded("步骤X失败")
    assert "步骤X失败" in ctx.degraded_msgs
    ctx.log("普通日志")
    ctx.close()
    content = (tmp_path / "t.log").read_text()
    assert "步骤X失败" in content and "普通日志" in content
    assert "步骤X失败" in (tmp_path / "alert.flag").read_text()


def test_default_log_follows_isolated_alert_directory(tmp_path, monkeypatch):
    """默认日志不得独立钉死 /tmp，否则测试 patch 告警后仍会污染真实运维日志。"""
    from services.pipeline import context
    from services.pipeline.context import PipelineContext

    alert = tmp_path / "isolated" / "alert.flag"
    alert.parent.mkdir()
    monkeypatch.setattr(context, "DEGRADED_FLAG", alert)
    ctx = PipelineContext(dry=True, date="20991231")
    try:
        assert ctx.log_path == alert.parent / "chunkymonkey_daily_update_20991231.log"
        ctx.degraded("synthetic test failure")
    finally:
        ctx.close()
    assert alert.exists()


def test_context_requires_date():
    from services.pipeline.context import PipelineContext
    with pytest.raises(ValueError):
        PipelineContext(date="")


def test_context_subprocess_env_only_propagates_explicit_writer_lease(tmp_path, monkeypatch):
    from services.pipeline.context import PipelineContext
    from services.writer_lock import (
        AUTH_VERIFIED_LEASE_ENV,
        WRITER_LEASE_ENV,
        WRITER_LOCK_FD_ENV,
    )

    monkeypatch.setenv(WRITER_LEASE_ENV, "stale-parent-value")
    monkeypatch.setenv(WRITER_LOCK_FD_ENV, "99")
    monkeypatch.setenv(AUTH_VERIFIED_LEASE_ENV, "stale-auth-proof")
    without_lease = PipelineContext(date="20260101", log_path=tmp_path / "a.log")
    with_lease = PipelineContext(
        date="20260101", log_path=tmp_path / "b.log", writer_lease_id="controller-lease",
        writer_lock_fd=17, tushare_auth_status={"sanitized": True},
    )
    try:
        no_lease_env = without_lease._subprocess_env()
        assert WRITER_LEASE_ENV not in no_lease_env
        assert WRITER_LOCK_FD_ENV not in no_lease_env
        assert AUTH_VERIFIED_LEASE_ENV not in no_lease_env
        child_env = with_lease._subprocess_env()
        assert child_env[WRITER_LEASE_ENV] == "controller-lease"
        assert child_env[WRITER_LOCK_FD_ENV] == "17"
        assert child_env[AUTH_VERIFIED_LEASE_ENV] == "controller-lease"
        assert with_lease._subprocess_pass_fds() == (17,)
    finally:
        without_lease.close()
        with_lease.close()


def test_run_orchestration_order(monkeypatch, tmp_path):
    """run.main 按 preflight→获取→清洗→加工→存储 顺序调四阶段 (mock 不碰真 DB)。"""
    from services.pipeline import run as run_mod

    called = []
    for name in ("run_preflight", "run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: called.append(_n))
    # 把日志/flag 引到 tmp, 不污染 /tmp
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")

    rc = run_mod.main(["--dry", "--skip-sync", "--date", "20260101"])
    assert rc == 0
    assert called == ["run_preflight", "run_acquire", "run_clean", "run_process", "run_store"]


def test_run_writer_busy_does_not_create_context_or_start_stages(monkeypatch, tmp_path):
    from services import writer_lock as lock_mod
    from services.pipeline import run as run_mod

    monkeypatch.setenv(lock_mod.WRITER_LOCK_PATH_ENV, str(tmp_path / "writer.lock"))
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("context must not start")),
    )
    with lock_mod.writer_lock(owner="other"):
        assert run_mod.main(["--dry", "--date", "20260101"]) == 2


def test_independent_stage_writer_busy_refuses_before_stage(monkeypatch, tmp_path):
    from services import writer_lock as lock_mod
    from services.pipeline import stage_runner

    monkeypatch.setenv(lock_mod.WRITER_LOCK_PATH_ENV, str(tmp_path / "writer.lock"))
    monkeypatch.setitem(
        stage_runner.STAGES,
        "clean",
        lambda _ctx: (_ for _ in ()).throw(AssertionError("stage must not start")),
    )
    with lock_mod.writer_lock(owner="other"):
        assert stage_runner.run_stage("clean", dry=True, date="20260101", force=True) == 4


def test_independent_acquire_authorization_block_is_exit_three(monkeypatch, tmp_path):
    from services import writer_lock as lock_mod
    from services.data_sources.sources.tushare import TuShareAuthorizationError
    from services.pipeline import preflight, stage_runner
    from services.pipeline.context import PipelineContext

    monkeypatch.setenv(lock_mod.WRITER_LOCK_PATH_ENV, str(tmp_path / "writer.lock"))
    monkeypatch.setattr(
        stage_runner,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "stage.log"}),
    )
    monkeypatch.setattr(
        preflight,
        "TuShareSource",
        lambda: SimpleNamespace(
            authorization_status=lambda: (_ for _ in ()).throw(
                TuShareAuthorizationError("auth_denied")
            )
        ),
    )
    monkeypatch.setattr(preflight, "ensure_calendar_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(
        stage_runner.STAGES,
        "acquire",
        lambda _ctx: (_ for _ in ()).throw(AssertionError("stage must not start")),
    )

    assert stage_runner.run_stage("acquire", dry=True, date="20260101") == 3
    assert "auth_denied" in (tmp_path / "stage.log").read_text()


def test_run_no_flags_parses(monkeypatch, tmp_path):
    """无 flag (全量真实模式) 也能解析 — 防 bash wrapper 空参传成空字符串 arg 的回归
    (2026-06-23: wrapper 用 ${arr[@]:-} 在空数组时传 '' → argparse unrecognized arguments)。

    2026-07-10 修真库污染(全栈审计HIGH): 本测试 dry=False 只 patch 了 5 个阶段函数, 漏了
    状态记录与日志两条副作用路径 — run_and_record 经 _record_stage_best_effort→get_conn()
    真写生产 smartmoney.mart_pipeline_run_manifest(每次全量 pytest 灌 4 行亚秒级 check_pass,
    污染 stage_runner 的 upstream check_pass 门真相源, 真实 check_fail 被下一次 pytest 盖掉),
    且默认 log_path 写真实 /tmp/chunkymonkey_daily_update_20260101.log。2026-06-29 已修过
    同类 DEGRADED_FLAG 污染但漏了这两条 — 同一测试的多条副作用路径必须逐条隔离。"""
    from services.pipeline import run as run_mod
    from services.pipeline import stage_status as ss_mod
    from services.pipeline.context import PipelineContext
    for name in ("run_preflight", "run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx: None)
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(run_mod, "PipelineContext",
                        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}))
    recorded = []
    monkeypatch.setattr(ss_mod, "_record_stage_best_effort",
                        lambda ctx, stage, status, gate_result=None: recorded.append(stage))
    assert run_mod.main(["--date", "20260101"]) == 0  # 无 --dry/--skip-sync
    assert recorded == ["acquire", "clean", "process", "store"], \
        "状态记录必须被 stub 捕获而非写真库 (捕获顺序同时验证阶段链)"


def test_run_auth_hard_block_skips_all_stages_and_exits_three(monkeypatch, tmp_path):
    from services.data_sources.sources.tushare import TuShareAuthorizationError
    from services.pipeline import run as run_mod
    from services.pipeline.context import PipelineContext

    called = []
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(
        run_mod,
        "run_preflight",
        lambda ctx: (_ for _ in ()).throw(TuShareAuthorizationError("auth_denied")),
    )
    for name in ("run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: called.append(_n))

    rc = run_mod.main(["--dry", "--date", "20260101"])

    assert rc == 3
    assert called == []
    assert "auth_denied" in (tmp_path / "run.log").read_text()
    assert "auth_denied" in (tmp_path / "flag").read_text()


def test_run_calendar_hard_block_skips_all_stages_and_exits_four(monkeypatch, tmp_path):
    from services.pipeline import run as run_mod
    from services.pipeline.context import PipelineContext
    from services.pipeline.preflight import PipelinePreflightError

    called = []
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(
        run_mod,
        "run_preflight",
        lambda ctx: (_ for _ in ()).throw(PipelinePreflightError("calendar_not_ready")),
    )
    for name in ("run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: called.append(_n))

    assert run_mod.main(["--dry", "--date", "20260101"]) == 4
    assert called == []
    assert "calendar_not_ready" in (tmp_path / "run.log").read_text()
    assert "calendar_not_ready" in (tmp_path / "flag").read_text()


def test_calendar_preflight_reuses_continuity_gate_and_fails_closed(monkeypatch, tmp_path):
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    seen = []

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        return SimpleNamespace(returncode=1, stdout="calendar horizon FAIL", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = PipelineContext(date="20260101", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(preflight.PipelinePreflightError, match="calendar_not_ready"):
            preflight.ensure_calendar_ready(ctx)
    finally:
        ctx.close()

    assert seen[0][2:] == [
        "--only", "calendar_horizon", "--domain", "trade_cal", "--strict", "--json"
    ]


def test_calendar_preflight_repairs_once_then_rechecks_same_gate(monkeypatch, tmp_path):
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    outcomes = iter([SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)])
    calls = []
    monkeypatch.setattr(preflight, "_calendar_gate", lambda _ctx: next(outcomes))
    monkeypatch.setattr(
        preflight,
        "_repair_calendar_foundation",
        lambda _ctx: calls.append("raw-full-refresh+builder"),
    )
    ctx = PipelineContext(date="20260101", log_path=tmp_path / "run.log")
    try:
        preflight.ensure_calendar_ready(ctx, allow_repair=True)
    finally:
        ctx.close()
    assert calls == ["raw-full-refresh+builder"]


def test_independent_acquire_calendar_block_is_exit_five(monkeypatch, tmp_path):
    from services import writer_lock as lock_mod
    from services.pipeline import preflight, stage_runner
    from services.pipeline.context import PipelineContext

    monkeypatch.setenv(lock_mod.WRITER_LOCK_PATH_ENV, str(tmp_path / "writer.lock"))
    monkeypatch.setattr(
        stage_runner,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "stage.log"}),
    )
    monkeypatch.setattr(
        preflight,
        "ensure_calendar_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            preflight.PipelinePreflightError("calendar_not_ready")
        ),
    )
    monkeypatch.setattr(
        preflight,
        "ensure_tushare_authorized",
        lambda *_args, **_kwargs: pytest.fail("auth must not run after calendar block"),
    )
    monkeypatch.setitem(
        stage_runner.STAGES,
        "acquire",
        lambda _ctx: pytest.fail("stage must not start"),
    )
    assert stage_runner.run_stage("acquire", dry=True, date="20260101") == 5
    assert "calendar_not_ready" in (tmp_path / "stage.log").read_text()


def test_run_returns_one_when_any_stage_degrades(monkeypatch, tmp_path):
    from services.pipeline import run as run_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(run_mod, "run_preflight", lambda ctx: None)
    monkeypatch.setattr(run_mod, "run_acquire", lambda ctx: ctx.degraded("acquire partial"))
    for name in ("run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx: None)

    rc = run_mod.main(["--dry", "--skip-sync", "--date", "20260101"])

    assert rc == 1
    assert "DONE with degraded" in (tmp_path / "run.log").read_text()


def test_preflight_dry_run_still_probes_auth_and_caches_sanitized_status(monkeypatch, tmp_path):
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    calls = []
    status = {
        "opened_at": datetime(2026, 6, 17, 10, 48, 58, tzinfo=ZoneInfo("Asia/Shanghai")),
        "expires_at": datetime(2099, 8, 12, 15, 43, tzinfo=ZoneInfo("Asia/Shanghai")),
        "remaining_weeks": 4,
    }

    class FakeSource:
        def authorization_status(self):
            calls.append("user")
            return status

    monkeypatch.setattr(preflight, "TuShareSource", FakeSource)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ctx = PipelineContext(
        dry=True,
        date="20260101",
        log_path=tmp_path / "run.log",
        auth_expiry_warning_days=14,
    )
    try:
        preflight.run_preflight(ctx)
        preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert calls == ["user"]
    assert ctx.tushare_auth_status == status


def test_authorization_probe_uses_configured_socket_timeout_and_restores_it(monkeypatch, tmp_path):
    import socket

    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    observed = []
    status = {
        "opened_at": datetime(2026, 6, 17, tzinfo=ZoneInfo("Asia/Shanghai")),
        "expires_at": datetime(2099, 8, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        "remaining_weeks": 4,
    }

    class FakeSource:
        def authorization_status(self):
            observed.append(socket.getdefaulttimeout())
            return status

    monkeypatch.setattr(preflight, "TuShareSource", FakeSource)
    monkeypatch.setattr(
        sync_runner,
        "load_registry",
        lambda: {
            "defaults": {"auth_expiry_warn_days": 14, "auth_probe_timeout_seconds": 7},
            "domains": {},
        },
    )
    previous = socket.getdefaulttimeout()
    ctx = PipelineContext(date="20260101", log_path=tmp_path / "run.log")
    try:
        preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()
    assert observed == [7.0]
    assert socket.getdefaulttimeout() == previous


def test_independent_acquire_dry_run_cannot_bypass_auth_probe(monkeypatch, tmp_path):
    from services.pipeline import acquire, preflight
    from services.pipeline.context import PipelineContext

    calls = []
    status = {
        "opened_at": datetime(2026, 6, 17, tzinfo=ZoneInfo("Asia/Shanghai")),
        "expires_at": datetime(2099, 8, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        "remaining_weeks": 4,
    }

    class FakeSource:
        def authorization_status(self):
            calls.append("user")
            return status

    monkeypatch.setattr(preflight, "TuShareSource", FakeSource)
    ctx = PipelineContext(
        dry=True,
        date="20260101",
        log_path=tmp_path / "run.log",
        auth_expiry_warning_days=14,
    )
    try:
        acquire.run_acquire(ctx)
    finally:
        ctx.close()

    assert calls == ["user"]
    assert ctx.tushare_auth_status == status


def test_skip_sync_skips_authorization_probe(monkeypatch, tmp_path):
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    class ForbiddenSource:
        def __init__(self):
            raise AssertionError("authorization probe must be skipped")

    monkeypatch.setattr(preflight, "TuShareSource", ForbiddenSource)
    ctx = PipelineContext(skip_sync=True, date="20260101", log_path=tmp_path / "run.log")
    try:
        result = preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert result is None
    assert ctx.tushare_auth_status is None


def test_authorization_expiry_warning_uses_single_injected_threshold(monkeypatch, tmp_path):
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    tz = ZoneInfo("Asia/Shanghai")
    status = {
        "opened_at": datetime.now(tz) - timedelta(days=30),
        "expires_at": datetime.now(tz) + timedelta(days=13),
        "remaining_weeks": 1,
    }

    class FakeSource:
        def authorization_status(self):
            return status

    monkeypatch.setattr(preflight, "TuShareSource", FakeSource)
    ctx = PipelineContext(
        date="20260101",
        log_path=tmp_path / "run.log",
        auth_expiry_warning_days=14,
    )
    try:
        preflight.ensure_tushare_authorized(ctx)
        preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert len(ctx.degraded_msgs) == 1
    assert "warning_days=14" in ctx.degraded_msgs[0]


def test_authorization_expiry_warning_reads_registry_default(monkeypatch, tmp_path):
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    tz = ZoneInfo("Asia/Shanghai")
    status = {
        "opened_at": datetime.now(tz) - timedelta(days=30),
        "expires_at": datetime.now(tz) + timedelta(days=8),
        "remaining_weeks": 1,
    }

    class FakeSource:
        def authorization_status(self):
            return status

    monkeypatch.setattr(preflight, "TuShareSource", FakeSource)
    monkeypatch.setattr(
        sync_runner,
        "load_registry",
        lambda: {
            "defaults": {"auth_expiry_warn_days": 9, "auth_probe_timeout_seconds": 20},
            "domains": {},
        },
    )
    ctx = PipelineContext(date="20260101", log_path=tmp_path / "run.log")
    try:
        preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert len(ctx.degraded_msgs) == 1
    assert "warning_days=9" in ctx.degraded_msgs[0]


def test_invalid_expiry_warning_config_blocks_before_provider_probe(monkeypatch, tmp_path):
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    calls = []

    class ForbiddenSource:
        def authorization_status(self):
            calls.append("user")
            raise AssertionError("provider must not run with invalid config")

    monkeypatch.setattr(preflight, "TuShareSource", ForbiddenSource)
    monkeypatch.setattr(
        sync_runner,
        "load_registry",
        lambda: {"defaults": {}, "domains": {}},
    )
    ctx = PipelineContext(date="20260101", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(ValueError, match="auth_expiry_warn_days"):
            preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert calls == []
    assert ctx.tushare_auth_status is None
