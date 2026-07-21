"""daily_update 四阶段管线 smoke 测试 (2026-06-23 重设计)。

锁住: (1) 包/各阶段可 import (防 port 笔误回归); (2) PipelineContext degraded/log 机制;
(3) run.main 编排顺序 preflight→获取→清洗→加工→存储 (monkeypatch 阶段, 不碰真 DB/网络)。
"""
import json
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


def _disabled_trade_calendar_registry():
    return {
        "defaults": {},
        "domains": {
            "healthy_domain": {
                "execution_policy": {"mode": "enabled", "reason": "manual_only"},
            },
            "trade_cal": {
                "execution_policy": {
                    "mode": "disabled",
                    "reason": "accepted_generation_pending",
                },
            },
        },
    }


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
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _ctx: None)
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


def test_full_pipeline_execution_policy_blocks_before_calendar_auth_or_sla(
    monkeypatch, tmp_path
):
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _disabled_trade_calendar_registry)
    monkeypatch.setattr(
        preflight,
        "ensure_calendar_ready",
        lambda *_a, **_k: pytest.fail("calendar probe/repair must not start"),
    )
    monkeypatch.setattr(
        preflight,
        "ensure_tushare_authorized",
        lambda *_a, **_k: pytest.fail("provider auth must not start"),
    )
    monkeypatch.setattr(
        preflight,
        "run_watermark_sla_check",
        lambda *_a, **_k: pytest.fail("SLA DB/report work must not start"),
    )
    ctx = PipelineContext(date="20260719", log_path=tmp_path / "pipeline.log")
    try:
        with pytest.raises(
            preflight.PipelinePreflightError,
            match="sync_execution_blocked:trade_cal:accepted_generation_pending",
        ):
            preflight.run_preflight(ctx)
    finally:
        ctx.close()
    assert ctx.tushare_auth_status is None


def test_direct_acquire_execution_policy_blocks_before_auth_or_write_steps(
    monkeypatch, tmp_path
):
    from services.data_sources import sync_runner
    from services.pipeline import acquire, preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _disabled_trade_calendar_registry)
    monkeypatch.setattr(
        preflight,
        "ensure_tushare_authorized",
        lambda *_a, **_k: pytest.fail("provider auth must not start"),
    )
    ctx = PipelineContext(date="20260719", log_path=tmp_path / "acquire.log")
    monkeypatch.setattr(
        ctx,
        "step",
        lambda *_a, **_k: pytest.fail("acquire writer step must not start"),
    )
    try:
        with pytest.raises(
            preflight.PipelinePreflightError,
            match="sync_execution_blocked:trade_cal:accepted_generation_pending",
        ):
            acquire.run_acquire(ctx)
    finally:
        ctx.close()


def test_independent_acquire_stage_policy_blocks_before_calendar_auth_and_stage(
    monkeypatch, tmp_path
):
    from services import writer_lock as lock_mod
    from services.data_sources import sync_runner
    from services.pipeline import preflight, stage_runner
    from services.pipeline.context import PipelineContext

    monkeypatch.setenv(lock_mod.WRITER_LOCK_PATH_ENV, str(tmp_path / "writer.lock"))
    monkeypatch.setattr(sync_runner, "load_registry", _disabled_trade_calendar_registry)
    monkeypatch.setattr(
        stage_runner,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "stage.log"}),
    )
    monkeypatch.setattr(
        preflight,
        "ensure_calendar_ready",
        lambda *_a, **_k: pytest.fail("calendar probe/repair must not start"),
    )
    monkeypatch.setattr(
        preflight,
        "ensure_tushare_authorized",
        lambda *_a, **_k: pytest.fail("provider auth must not start"),
    )
    monkeypatch.setitem(
        stage_runner.STAGES,
        "acquire",
        lambda _ctx: pytest.fail("acquire stage must not start"),
    )

    assert stage_runner.run_stage("acquire", dry=False, date="20260719") == 5
    assert "sync_execution_blocked:trade_cal:accepted_generation_pending" in (
        tmp_path / "stage.log"
    ).read_text()


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
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _ctx: None)
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


def test_tier0_acquire_block_stops_every_downstream_stage(monkeypatch, tmp_path):
    from services.pipeline import run as run_mod
    from services.pipeline.acquire import Tier0AcquireError
    from services.pipeline.context import PipelineContext

    called = []
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(run_mod, "run_preflight", lambda _ctx: called.append("preflight"))
    monkeypatch.setattr(
        run_mod,
        "run_acquire",
        lambda _ctx: (_ for _ in ()).throw(Tier0AcquireError("margin partial")),
    )
    for name in ("run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda _ctx, _name=name: called.append(_name))

    assert run_mod.main(["--dry", "--date", "20260101"]) == 5
    assert called == ["preflight"]
    assert "TIER0 BLOCK" in (tmp_path / "run.log").read_text()


def test_sync_registry_margin_partial_is_a_tier0_block(monkeypatch, tmp_path):
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "_margin_hard_gate_required", lambda registry=None: True)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                [
                    {
                        "domain": "margin",
                        "status": "partial",
                        "still_failed": ["20260716"],
                        "truncated": False,
                    }
                ]
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        acquire,
        "_assert_margin_shadow_parity",
        lambda _ctx: pytest.fail("parity cannot run after a partial margin result"),
    )
    ctx = PipelineContext(date="20260717", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(acquire.Tier0AcquireError, match="did not close"):
            acquire._sync_registry_drain(ctx)
    finally:
        ctx.close()


def test_sync_registry_drain_skips_margin_gate_when_disabled(monkeypatch, tmp_path):
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "_margin_hard_gate_required", lambda registry=None: False)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"domain": "adj_factor", "status": "clean"}]),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        acquire,
        "_assert_margin_shadow_parity",
        lambda _ctx: pytest.fail("disabled margin must not run shadow parity"),
    )
    ctx = PipelineContext(date="20260717", log_path=tmp_path / "run.log")
    try:
        acquire._sync_registry_drain(ctx)
        assert ctx.degraded_msgs == []
        assert "margin drain/shadow hard-gate SKIP" in (tmp_path / "run.log").read_text()
    finally:
        ctx.close()


def test_formal_on_demand_catchup_skips_when_latest_accepted(monkeypatch, tmp_path):
    from services.data_sources import sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    registry = {
        "domains": {
            "daily": {
                "domain": "daily",
                "sync_policy": "on_demand",
                "execution_policy": {
                    "mode": "enabled",
                    "reason": "authorized_manual_generation",
                },
            },
            "stock_st": {
                "domain": "stock_st",
                "sync_policy": "on_demand",
                "execution_policy": {
                    "mode": "enabled",
                    "reason": "authorized_manual_generation",
                },
            },
        }
    }
    calls = []

    class _Conn:
        def execute(self, *_a, **_k):
            return SimpleNamespace(fetchone=lambda: {"ok": 1})

        def close(self):
            pass

    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(
            eligible_end="20260721", reason="manual_calendar_eligible"
        ),
    )
    monkeypatch.setattr(
        sync_runner,
        "run_domain",
        lambda *a, **k: calls.append((a, k)) or {"status": "ok", "failed_batches": 0},
    )
    monkeypatch.setattr(
        "services.duck_adapter.connect", lambda *_a, **_k: _Conn()
    )
    ctx = PipelineContext(date="20260721", log_path=tmp_path / "run.log")
    try:
        acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()
    assert calls == []


def test_formal_on_demand_catchup_pulls_single_missing_eligible_day(
    monkeypatch, tmp_path
):
    from services.data_sources import sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    registry = {
        "domains": {
            "daily": {
                "domain": "daily",
                "sync_policy": "on_demand",
                "execution_policy": {
                    "mode": "enabled",
                    "reason": "authorized_manual_generation",
                },
            },
            "stock_st": {
                "domain": "stock_st",
                "sync_policy": "on_demand",
                "execution_policy": {
                    "mode": "enabled",
                    "reason": "authorized_manual_generation",
                },
            },
        }
    }
    calls = []

    class _Conn:
        def execute(self, *_a, **_k):
            return SimpleNamespace(fetchone=lambda: None)

        def close(self):
            pass

    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(
            eligible_end="20260721", reason="manual_calendar_eligible"
        ),
    )
    monkeypatch.setattr(
        sync_runner,
        "run_domain",
        lambda domain, **kwargs: calls.append((domain, kwargs))
        or {"domain": domain, "status": "ok", "failed_batches": 0, "rows": 1},
    )
    monkeypatch.setattr(
        "services.duck_adapter.connect", lambda *_a, **_k: _Conn()
    )
    ctx = PipelineContext(date="20260721", log_path=tmp_path / "run.log")
    try:
        acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()
    assert [c[0] for c in calls] == ["daily", "stock_st"]
    for _domain, kwargs in calls:
        assert kwargs["start"] == "20260721"
        assert kwargs["end"] == "20260721"
        assert kwargs["trigger_mode"] == "manual"
        assert kwargs["registry"] is registry


def test_unrelated_sync_failure_degrades_only_after_margin_gate_passes(
    monkeypatch, tmp_path
):
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "_margin_hard_gate_required", lambda registry=None: True)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                [
                    {
                        "domain": "margin",
                        "status": "clean",
                        "still_failed": [],
                        "truncated": False,
                    },
                    {"domain": "other", "status": "partial"},
                ]
            ),
            stderr="",
        ),
    )
    gates = []
    monkeypatch.setattr(
        acquire, "_assert_margin_shadow_parity", lambda _ctx: gates.append("margin")
    )
    ctx = PipelineContext(date="20260717", log_path=tmp_path / "run.log")
    try:
        acquire._sync_registry_drain(ctx)
        assert gates == ["margin"]
        assert len(ctx.degraded_msgs) == 1
    finally:
        ctx.close()


def test_margin_pipeline_gate_consumes_one_typed_readiness_verdict(
    monkeypatch, tmp_path
):
    from services import duck_adapter
    from services.data_sources import margin_ingest, margin_readiness, sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    class FakeConn:
        closed = False

        def close(self):
            self.closed = True

    conn = FakeConn()
    calls = []
    planned_contract = SimpleNamespace(coverage_start="20260715")
    monkeypatch.setattr(duck_adapter, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(sync_runner, "load_registry", lambda: {})
    monkeypatch.setattr(sync_runner, "domain_spec", lambda _reg, _domain: {})
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(eligible_end="20260716", reason="t_plus_one"),
    )
    monkeypatch.setattr(
        sync_runner,
        "trading_days",
        lambda _start, _end: ["20260715", "20260716"],
    )
    monkeypatch.setattr(
        margin_ingest,
        "contract_for_spec",
        lambda _spec: planned_contract,
    )
    monkeypatch.setattr(
        margin_readiness,
        "evaluate_margin_readiness",
        lambda actual, expected, **kwargs: calls.append(
            (actual, tuple(expected), kwargs.get("contract"))
        )
        or SimpleNamespace(
            ready=True,
            eligible_end="20260716",
            eligibility_reason="t_plus_one",
            expected=("20260715", "20260716"),
            accepted_state=SimpleNamespace(partitions=(object(), object())),
            missing=(),
            unexpected=(),
            reconcile_failures=(),
        ),
    )
    ctx = PipelineContext(date="20260717", log_path=tmp_path / "run.log")
    try:
        acquire._assert_margin_shadow_parity(ctx)
    finally:
        ctx.close()

    assert len(calls) == 1
    assert calls[0][0] is conn
    assert calls[0][1] == ("20260715", "20260716")
    assert calls[0][2] is planned_contract
    assert conn.closed is True


def test_margin_pipeline_gate_blocks_typed_readiness_failure(monkeypatch, tmp_path):
    from services import duck_adapter
    from services.data_sources import margin_ingest, margin_readiness, sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    conn = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(duck_adapter, "connect", lambda *_a, **_k: conn)
    monkeypatch.setattr(sync_runner, "load_registry", lambda: {})
    monkeypatch.setattr(sync_runner, "domain_spec", lambda _reg, _domain: {})
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(eligible_end="20260716", reason="t_plus_one"),
    )
    monkeypatch.setattr(
        sync_runner,
        "trading_days",
        lambda _start, _end: ["20260715", "20260716"],
    )
    monkeypatch.setattr(
        margin_ingest,
        "contract_for_spec",
        lambda _spec: SimpleNamespace(coverage_start="20260715"),
    )
    monkeypatch.setattr(
        margin_readiness,
        "evaluate_margin_readiness",
        lambda _conn, _expected, **_kwargs: SimpleNamespace(
            ready=False,
            eligible_end="20260716",
            eligibility_reason="t_plus_one",
            expected=("20260715", "20260716"),
            accepted_state=SimpleNamespace(partitions=(object(),)),
            missing=("20260716",),
            unexpected=(),
            reconcile_failures=(),
        ),
    )
    ctx = PipelineContext(date="20260717", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(acquire.Tier0AcquireError, match="missing=\\['20260716'\\]"):
            acquire._assert_margin_shadow_parity(ctx)
    finally:
        ctx.close()


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
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _ctx: None)
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


def test_post_acquire_sla_replaces_preflight_alert_after_accepted_repair(
    monkeypatch, tmp_path
):
    """采集前 alert 只是 before 证据；AcceptedPartition 修复后最终报告必须消费 after。"""
    from services.pipeline import context, preflight, store
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(context, "REPO", tmp_path)
    monkeypatch.setattr(store, "REPO", tmp_path)
    monkeypatch.setattr(preflight, "ensure_calendar_ready", lambda *_a, **_k: None)

    accepted = {"ready": False}
    outputs = []

    def fake_run(cmd, **kwargs):
        output_arg = cmd[cmd.index("--json-output") + 1]
        outputs.append(output_arg)
        output_path = Path(kwargs["cwd"]) / output_arg
        output_path.parent.mkdir(parents=True, exist_ok=True)
        alert = not accepted["ready"]
        output_path.write_text(
            json.dumps(
                {
                    "n_updates": 0,
                    "n_alerts": int(alert),
                    "sources": [
                        {"source_name": "tushare", "alert": alert},
                    ],
                }
            )
        )
        return SimpleNamespace(returncode=2 if alert else 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = PipelineContext(
        dry=True,
        skip_sync=True,
        date="20260717",
        log_path=tmp_path / "pipeline.log",
    )
    try:
        preflight.run_preflight(ctx)
        assert ctx.degraded_msgs == []
        accepted["ready"] = True
        store.run_store(ctx)
    finally:
        ctx.close()

    before = json.loads(
        (tmp_path / "data/audit/watermark_sla_before_20260717.json").read_text()
    )
    after = json.loads(
        (tmp_path / "data/audit/watermark_sla_20260717.json").read_text()
    )
    report = json.loads((tmp_path / "data/reports/daily_20260717.json").read_text())
    assert before["n_alerts"] == 1
    assert after["n_alerts"] == 0
    assert report["sla_summary"]["n_alerts"] == 0
    assert report["sla_warn"] is False
    assert report["phase_status"]["preflight"] == "OK"
    assert report["phase_status"]["post_acquire_sla"] == "OK"
    assert report["phase_status"]["chain"] == "OK"
    assert outputs == [
        "data/audit/watermark_sla_before_20260717.json",
        "data/audit/watermark_sla_20260717.json",
    ]


def test_post_acquire_sla_alert_is_the_final_degraded_verdict(monkeypatch, tmp_path):
    """Store 重算仍有 alert 时才把 SLA 作为本次最终 degraded 结论。"""
    from services.pipeline import context, store
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(context, "REPO", tmp_path)
    monkeypatch.setattr(store, "REPO", tmp_path)

    def fake_run(cmd, **kwargs):
        if "--json-output" not in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output_arg = cmd[cmd.index("--json-output") + 1]
        output_path = Path(kwargs["cwd"]) / output_arg
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "n_updates": 0,
                    "n_alerts": 1,
                    "sources": [{"source_name": "tushare", "alert": True}],
                }
            )
        )
        return SimpleNamespace(returncode=2, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = PipelineContext(
        dry=True,
        skip_sync=True,
        date="20260717",
        log_path=tmp_path / "pipeline.log",
    )
    try:
        store.run_store(ctx)
    finally:
        ctx.close()

    report = json.loads((tmp_path / "data/reports/daily_20260717.json").read_text())
    assert any("post-acquire watermark SLA alert" in msg for msg in ctx.degraded_msgs)
    assert report["sla_warn"] is True
    assert report["phase_status"]["chain"] == "DEGRADED_PARTIAL"


def test_post_acquire_sla_crash_cannot_reuse_same_day_stale_artifact(
    monkeypatch, tmp_path
):
    """checker crash 前先删同日旧 artifact，最终报告不得拿旧绿结果冒充 after。"""
    from services.pipeline import context, store
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(context, "REPO", tmp_path)
    monkeypatch.setattr(store, "REPO", tmp_path)
    stale = tmp_path / "data/audit/watermark_sla_20260717.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"n_alerts": 0, "sources": []}))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    ctx = PipelineContext(
        dry=True,
        skip_sync=True,
        date="20260717",
        log_path=tmp_path / "pipeline.log",
    )
    try:
        store.run_store(ctx)
    finally:
        ctx.close()

    report = json.loads((tmp_path / "data/reports/daily_20260717.json").read_text())
    assert not stale.exists()
    assert report["phase_status"]["post_acquire_sla"] == "ERR"
    assert report["phase_status"]["chain"] == "DEGRADED_PARTIAL"
    assert "sla_summary" not in report
    assert any("最终 SLA 失明" in msg for msg in ctx.degraded_msgs)


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
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _ctx: None)
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
