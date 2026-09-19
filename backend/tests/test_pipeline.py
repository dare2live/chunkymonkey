"""daily_update 四阶段管线 smoke 测试 (2026-06-23 重设计)。

锁住: (1) 包/各阶段可 import (防 port 笔误回归); (2) PipelineContext degraded/log 机制;
(3) run.main 编排顺序 preflight→获取→清洗→加工→存储 (monkeypatch 阶段, 不碰真 DB/网络)。
"""
import io
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
    """Pipeline tests must not touch the production writer lock, logs, or alert flag.

    store.py/run.py 各自 `from .context import REPO` 绑定了自己的模块级名字
    (store.py:16 / run.py:23) —— patch `context.REPO` 对它们无效, 必须分别 patch
    两处 (test_pipeline_delta_manifest.py:123 已是这么做的)。之前只隔离了
    DEGRADED_FLAG/writer lock, 没隔离 REPO: 任何调 `write_report_and_alert` 的测试
    (例如 test_tier0_acquire_block_stops_every_downstream_stage, date=20260101) 都会
    在真实仓库 data/reports/ 下留一份 pytest 副产物, 而 ops_manual_run.py 按 mtime 选
    「最新报告」会把它当成真实最近一次日更 (fable_review_chain_run_stop_rule.md
    任务1「store.py:124 泄漏」)。

    回归锁: 上面两行 store.REPO/run_mod.REPO monkeypatch 才是真正防泄漏的代码 —— 万一
    日后有人手误删掉这两行, 被测代码会改用 `services.pipeline.context.REPO`(真实仓库
    根, 本 fixture 不 patch 它) 写 daily_{date}.json / watermark_sla*_{date}.json, 而
    本文件其余测试只会因为读不到 tmp_path 下的文件连带变红, 没有任何测试点名"真实仓库
    又被写脏了"。跑前后快照真实证据文件集合 (glob 由 pipeline_evidence_paths.yaml 的
    模板派生, 不另抄字面量), 变了就 fail —— 同 conftest.py::_real_alert_flag_sentinel
    对 DEGRADED_FLAG 的写法。
    """
    from services.pipeline import context, run as run_mod, store
    from services.pipeline.evidence_paths import load_pipeline_evidence_paths
    from services.writer_lock import WRITER_LOCK_PATH_ENV

    monkeypatch.setenv(WRITER_LOCK_PATH_ENV, str(tmp_path / "pipeline-writer.lock"))
    monkeypatch.setattr(context, "DEGRADED_FLAG", tmp_path / "pipeline-alert.flag")
    monkeypatch.setattr(store, "REPO", tmp_path)
    monkeypatch.setattr(run_mod, "REPO", tmp_path)

    evidence_paths = load_pipeline_evidence_paths()
    evidence_globs = sorted(
        {
            evidence_paths.daily_report_path_template.replace("{date}", "*"),
            evidence_paths.watermark_sla_path_template.replace("{date}", "*"),
            evidence_paths.watermark_sla_before_path_template.replace("{date}", "*"),
        }
    )
    # 绑定当前的真实仓库根 (Path 不可变对象): 少数用例 (例如
    # test_post_acquire_sla_alert_is_the_final_degraded_verdict) 会自己额外
    # monkeypatch `context.REPO`(为了让 ctx.run_script 的子进程 cwd 也落 tmp_path) ——
    # 若这里在 yield 之后才重新读 `context.REPO` 属性, 读到的会是那次 monkeypatch 换上
    # 的新对象而不是真实仓库, 导致本回归锁把"合法的额外隔离"误判成"泄漏"。真实仓库
    # 只需在本 fixture 自己的 monkeypatch 生效之前锚定一次。
    real_repo = context.REPO

    def _snapshot_real_evidence_files():
        return {g: sorted(str(f) for f in real_repo.glob(g)) for g in evidence_globs}

    before_real_evidence = _snapshot_real_evidence_files()
    yield
    after_real_evidence = _snapshot_real_evidence_files()
    assert after_real_evidence == before_real_evidence, (
        "pipeline test leaked a daily_update evidence file into the real repo "
        "(store.REPO/run_mod.REPO isolation regressed): "
        f"before={before_real_evidence} after={after_real_evidence}"
    )



# ── on_demand 缺口自愈测试的两个共用桩 (2026-09-03) ─────────────────────────────
# 背景: 下面几个 formal on_demand 测试把 `services.duck_adapter.connect` 整个换成一个
# 只支持 fetchone 的桩。那是**霰弹式** patch —— 进程里任何开库的代码都会拿到它, 包括
# `sync_runner.trading_days()` 走的 `resolver.dim_read_conn(None, "dim_trading_calendar")`,
# 而它调的是 `.fetchall()` → AttributeError。
#
# 更麻烦的是它顺序依赖: `resolver.py:12` 写的是
# `from services.duck_adapter import connect as duck_connect`(导入时绑定), 所以只有当
# resolver **恰好在 monkeypatch 生效期间首次导入**时才会绑到桩上。单跑本文件 → 绑桩 → 6 红;
# 与更大 suite 同跑(resolver 已被别的文件先导入) → 绑真函数 → 只剩 3 红。
# 也就是说其中 3 个测试此前是**靠别的测试的副作用**才绿的。
#
# 正解不是给桩补 fetchall, 而是**让日历根本不经过 DB**: 注入确定交易日历。
# acquire.py:373-379 的注释在 2026-08-22 就为同一件事写过结论
# (「本地因为有真实 reference.duckdb 且真实日历恰好与 fixture 日期吻合而『碰巧绿』」),
# 当时给 `_recent_unaccepted_days` 加了 `trading_days_fn` 注入参数, 但
# `_sync_formal_on_demand_security_days` 调它时没把这个参数穿透过去, 所以从编排入口
# 进来的测试注入不了 —— 改用 monkeypatch 模块属性(实测有效; 那条注释里说「patch 根本
# 没生效」指的是 patch sys.modules, 不是 patch 模块属性)。
def _stub_trading_days(monkeypatch, sync_runner, days):
    """注入确定交易日历, 让被测代码不依赖真实 reference.duckdb。"""
    monkeypatch.setattr(sync_runner, "trading_days", lambda *_a, **_k: list(days))


class _AcceptedExceptConn:
    """accepted_partition 桩: 除 `missing` 里的日子外, 其余分区都算已接受。

    `_accepted_partition_exists` 判的是 `fetchone() is not None`, 参数是
    [dataset_id, partition_value] —— 所以按 partition_value 判即可精确表达
    「只缺这几天」。原实现一律返回 None(= 一天都没接受过), 与
    「pulls_single_missing_eligible_day」这类测试名直接矛盾: gap-heal 会把窗口内
    每一天都当成洞, 断言 2 次调用实际得到 20 次。
    """

    def __init__(self, missing):
        self._missing = {str(x) for x in missing}

    def execute(self, _sql, params=None):
        pv = str((params or [None, None])[-1])
        row = None if pv in self._missing else (1,)
        return SimpleNamespace(
            fetchone=lambda: row,
            fetchall=lambda: ([] if row is None else [row]),
        )

    def close(self):
        pass


def _disabled_trade_calendar_registry():
    """trade_cal 被合法地标 disabled —— 2026-09-12 起这**不再**阻塞 preflight。

    改判经过: `automatic_domains` 原先只看 `sync_policy`, 于是 disabled 域照样进 all-due,
    再被 `preflight_execution_policies` 判死。那个组合让 daily_update 从 2026-09-07 起
    连续 5 天 0 秒 hard_fail(exit 4)、四阶段一步未启动 —— 而 16 个 disabled 恰恰是按
    `check_tushare_sunset` 检查 8 的官方建议写上去的。修法是在选域处排除 disabled,
    所以「合法的 disabled」现在**必须不阻塞**; 本 fixture 因此改用于
    test_disabled_domain_no_longer_blocks_preflight(正向孪生)。
    要验「无效策略仍 fail-closed」请用 _invalid_policy_registry。
    """
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


def _invalid_policy_registry():
    """trade_cal 的 execution_policy **形状非法**(mode 不在值域内) —— 必须 fail-closed。

    这是三条 "blocks before calendar/auth/SLA/stage" 用例真正想守的东西: 策略本身读不出来时,
    不许在日历探针 / 供应商授权 / SLA 落盘 / stage 执行之前放行。原先它们借「合法 disabled」
    来触发阻塞, 而那条路已被上面的改判关掉; 用非法策略触发才对准被守的东西
    (`execution_policy_for_spec` 抛 ExecutionPolicyError(invalid_execution_policy),
    `automatic_domains` 与 `preflight_execution_policies` 都会把它冒泡到
    `PipelinePreflightError`; domain 由 `domain_spec` 塞入 spec, 所以消息里指得出域名)。
    """
    return {
        "defaults": {},
        "domains": {
            "healthy_domain": {
                "execution_policy": {"mode": "enabled", "reason": "manual_only"},
            },
            "trade_cal": {
                "execution_policy": {
                    "mode": "paused",  # 非法值域: 只允许 enabled / disabled
                    "reason": "accepted_generation_pending",
                },
            },
        },
    }


# ── 2026-09-10 tushare 授权到期不续期整改: 三种 registry 形状 fixture ──────────
# (供 ensure_tushare_authorized / tushare_dependent_domains 的三种验收场景复用；
#  测试必须自带 fixture, 不许借真实 sync_registry.yaml 的当前状态过关 —— 那会随
#  域迁移悄悄改变含义, 见 feedback-test-must-carry-its-own-fixture。)


def _no_tushare_domain_registry():
    """全部 enabled 域源已切非 tushare (daily=tdxhub, stock_st=stock_st_derive) — 场景1。"""
    return {
        "defaults": {},
        "domains": {
            "daily": {
                "execution_policy": {"mode": "enabled", "reason": "authorized_manual_generation"},
                "source": "tdxhub",
            },
            "stock_st": {
                "execution_policy": {"mode": "enabled", "reason": "authorized_manual_generation"},
                "source": "stock_st_derive",
            },
            # disabled 的 tushare 域必须不计入 (mode 过滤是否生效的反向证据)。
            "frozen_tushare_domain": {
                "execution_policy": {"mode": "disabled", "reason": "frozen"},
                "source": "tushare",
            },
        },
    }


def _tushare_domain_due_registry():
    """混合注册表: 一个 enabled tushare 域仍 due, 一个 enabled 非 tushare 域也 due — 场景2。"""
    return {
        "defaults": {"auth_expiry_warn_days": 14, "auth_probe_timeout_seconds": 10},
        "domains": {
            "daily_basic": {
                "execution_policy": {"mode": "enabled", "reason": "automatic"},
                "source": "tushare",
            },
            "daily": {
                "execution_policy": {"mode": "enabled", "reason": "authorized_manual_generation"},
                "source": "tdxhub",
            },
        },
    }


def _unresolvable_source_registry():
    """execution_policy 畸形 (漏必填键) → domain_spec/execution_policy_for_spec 必抛错 — 场景3。"""
    return {
        "defaults": {"auth_expiry_warn_days": 14, "auth_probe_timeout_seconds": 10},
        "domains": {
            "mystery_domain": {
                # 故意漏 execution_policy: 触发 fail-safe (判不出 = 当作需要 tushare)。
                "source": "tushare",
            },
        },
    }


# ── tushare_dependent_domains: 域集判定单元测试 (每条含反向验证) ──────────────


def test_tushare_dependent_domains_empty_when_no_enabled_domain_is_tushare():
    from services.pipeline.preflight import tushare_dependent_domains

    result = tushare_dependent_domains(_no_tushare_domain_registry())
    assert result == [], "非 tushare 源域(tdxhub/stock_st_derive)不得计入"


def test_tushare_dependent_domains_lists_only_the_tushare_sourced_domain():
    from services.pipeline.preflight import tushare_dependent_domains

    result = tushare_dependent_domains(_tushare_domain_due_registry())
    assert result == ["daily_basic"], "只有 source=tushare 的域该入选"
    assert "daily" not in result, "反向验证: 非 tushare 源域(daily/tdxhub)不得混入"


def test_tushare_dependent_domains_ignores_disabled_tushare_domain():
    """反向验证: mode=disabled 的 tushare 域即便 source=tushare 也不计入 (mode 过滤生效)。"""
    from services.pipeline.preflight import tushare_dependent_domains

    registry = {
        "defaults": {},
        "domains": {
            "frozen": {
                "execution_policy": {"mode": "disabled", "reason": "frozen"},
                "source": "tushare",
            },
        },
    }
    assert tushare_dependent_domains(registry) == []


def test_tushare_dependent_domains_fails_safe_to_none_on_malformed_domain():
    from services.pipeline.preflight import tushare_dependent_domains

    assert tushare_dependent_domains(_unresolvable_source_registry()) is None


def test_tushare_dependent_domains_fails_safe_to_none_on_missing_registry():
    from services.pipeline.preflight import tushare_dependent_domains

    assert tushare_dependent_domains(None) is None


def test_tushare_dependent_domains_fails_safe_to_none_on_empty_domains():
    from services.pipeline.preflight import tushare_dependent_domains

    assert tushare_dependent_domains({"defaults": {}, "domains": {}}) is None
    assert tushare_dependent_domains({"defaults": {}}) is None, "domains 键整个缺失同样判不出"


# ── ensure_tushare_authorized: 验收断言 1/2/3 (每条含反向验证) ──────────────


def test_ensure_tushare_authorized_skips_probe_when_no_tushare_domain_due(monkeypatch, tmp_path):
    """验收断言1: 无 tushare 源域 due -> 不探测 (--today 20260911 模拟到期后场景)。"""
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _no_tushare_domain_registry)

    class ForbiddenSource:
        def __init__(self):
            raise AssertionError("probe must not run when no tushare domain is due")

    monkeypatch.setattr(preflight, "TuShareSource", ForbiddenSource)
    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        result = preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert result is None
    assert ctx.tushare_auth_status is None
    assert ctx.degraded_msgs == []
    assert "本次无 tushare 源域 due" in (tmp_path / "run.log").read_text()


def test_ensure_tushare_authorized_probes_and_blocks_when_tushare_domain_due_and_expired(
    monkeypatch, tmp_path
):
    """验收断言2 (反向于上一条): 有 tushare 域 due 且过期 -> 照常探测且照实抛出阻断。"""
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
    calls = []

    class ExpiredSource:
        def authorization_status(self):
            calls.append("user")
            raise TuShareAuthorizationError("auth_expired")

    monkeypatch.setattr(preflight, "TuShareSource", ExpiredSource)
    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(TuShareAuthorizationError, match="auth_expired"):
            preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert calls == ["user"], "tushare 域 due 时必须真的探测, 不能因为顺带存在非 tushare 域就跳过"
    assert ctx.tushare_auth_status is None
    assert ctx.tushare_auth_blocked_reason == "auth_expired"


def test_ensure_tushare_authorized_still_probes_when_domain_source_unresolvable(
    monkeypatch, tmp_path
):
    """验收断言3 (fail-safe 反向验证): registry 判不出源 -> 仍然探测, 不许跳过。"""
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _unresolvable_source_registry)
    calls = []
    status = {
        "opened_at": datetime(2026, 6, 17, tzinfo=ZoneInfo("Asia/Shanghai")),
        "expires_at": datetime(2099, 8, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        "remaining_weeks": 4,
    }

    class SpySource:
        def authorization_status(self):
            calls.append("user")
            return status

    monkeypatch.setattr(preflight, "TuShareSource", SpySource)
    ctx = PipelineContext(
        date="20260911", log_path=tmp_path / "run.log", auth_expiry_warning_days=14
    )
    try:
        preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert calls == ["user"], "registry 域源解析不出时必须保持探测, 不许静默跳过"
    assert ctx.tushare_auth_status == status


def test_ensure_tushare_authorized_still_probes_when_registry_itself_unloadable(
    monkeypatch, tmp_path
):
    """fail-safe 反向验证的另一半: load_registry() 本身抛错时必须绝不静默跳过(不返回 None)。

    本函数内部对 load_registry() 有 try/except 保护 (registry_for_scope=None ->
    tushare_dependent_domains(None) -> None -> 判不出走探测); 但 registry 若整体
    读不出, _auth_probe_timeout_seconds() 自己那次 load_registry() 调用(此函数无
    fail-safe 包装, 本次改动范围外) 会先炸出 RuntimeError —— 这仍然满足"不静默跳过"
    的底线: 宁可报错也不能悄悄放行成 SKIP。
    """
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    def _boom():
        raise RuntimeError("registry file corrupt")

    monkeypatch.setattr(sync_runner, "load_registry", _boom)
    calls = []

    class ForbiddenSource:
        # 构造本身无害 (probe_authorization(TuShareSource(), timeout_seconds=...) 里
        # TuShareSource() 先于 _auth_probe_timeout_seconds() 求值); 真正不许发生的是
        # authorization_status() 被调用 —— timeout 计算会先炸, probe_authorization
        # 整体都不会被进入。
        def authorization_status(self):
            calls.append("user")
            raise AssertionError("must not be reached in this failure mode")

    monkeypatch.setattr(preflight, "TuShareSource", ForbiddenSource)
    ctx = PipelineContext(
        date="20260911", log_path=tmp_path / "run.log", auth_expiry_warning_days=14
    )
    try:
        with pytest.raises(RuntimeError, match="registry file corrupt"):
            preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert calls == [], "provider 的 authorization_status 不该被触达 —— timeout 配置先炸"
    assert ctx.tushare_auth_status is None, "绝不能把读不出 registry 悄悄当成 SKIP 成功"


def test_ensure_tushare_authorized_caches_blocked_reason_without_reprobing(monkeypatch, tmp_path):
    """同一次 run 内重复调用: 被拒结论要复用, 不许对每次调用都重新打网络探针。"""
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext
    from services.data_sources.sources.tushare import TuShareAuthorizationError

    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
    calls = []

    class ExpiredSource:
        def authorization_status(self):
            calls.append("user")
            raise TuShareAuthorizationError("auth_expired")

    monkeypatch.setattr(preflight, "TuShareSource", ExpiredSource)
    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(TuShareAuthorizationError):
            preflight.ensure_tushare_authorized(ctx)
        with pytest.raises(TuShareAuthorizationError):
            preflight.ensure_tushare_authorized(ctx)
    finally:
        ctx.close()

    assert calls == ["user"], "第二次调用必须复用缓存的 blocked reason, 不重打探针"


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
    """chunkyctl pipeline acquire (独立单阶段触发) 未随本次整改改动, 仍是硬 exit 3 ——

    与 daily_update 全链新语义(降级续跑)不一致, 是本次已知未处理的残留(见改动报告);
    这里只补一个显式 tushare 域 fixture(测试不许借真实 registry 状态过关), 断言本身不变。
    """
    from services import writer_lock as lock_mod
    from services.data_sources import sync_runner
    from services.data_sources.sources.tushare import TuShareAuthorizationError
    from services.pipeline import preflight, stage_runner
    from services.pipeline.context import PipelineContext

    monkeypatch.setenv(lock_mod.WRITER_LOCK_PATH_ENV, str(tmp_path / "writer.lock"))
    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
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


def test_run_auth_block_degrades_and_still_runs_all_stages(monkeypatch, tmp_path):
    """2026-09-10 tushare 到期不续期整改: run.py 的 TuShareAuthorizationError 兜底

    从"exit 3 + 四阶段未启动"改成"降级 + 四阶段继续"。本测试直接让 run_preflight 抛出
    (模拟 run_preflight/run_acquire 内部吸收失手的防御性兜底路径, 不依赖真实 registry/探针),
    钉住 run.py 这一层新语义: 不再硬 exit 3, 不再跳过任何阶段。
    """
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

    assert rc != 3, "授权阻断不再是硬 exit 3"
    assert called == ["run_acquire", "run_clean", "run_process", "run_store"], (
        "四阶段必须全部启动, 一个都不许因授权阻断被跳过"
    )
    log_text = (tmp_path / "run.log").read_text()
    flag_text = (tmp_path / "flag").read_text()
    assert "auth_denied" in log_text and "authorization_blocked" in log_text
    assert "auth_denied" in flag_text and "authorization_blocked" in flag_text
    # 反向验证: 降级消息不能带 "AUTH BLOCK" 等 run_outcome._HARD_RE 字样,
    # 否则即便四阶段跑了, run_outcome 仍会把它误判回 hard_fail exit 3。
    assert "AUTH BLOCK" not in log_text.upper().replace("AUTHORIZATION_BLOCKED", "")


def test_run_end_to_end_skips_probe_and_all_stages_start_when_no_tushare_domain_due(
    monkeypatch, tmp_path
):
    """验收断言1 端到端: --date 20260911 干跑, registry 全 enabled 域已非 tushare 源

    -> 不探测 (TuShareSource 若被实例化即失败); exit != 3; 四阶段全部有启动记录。
    这是最贴近真实 "--today 20260911" 场景的用例: run_preflight/ensure_tushare_authorized
    走真代码 (不 mock 掉), 只 mock 掉 registry + 四个阶段体 + 无关的日历/SLA 子进程。
    """
    from services.data_sources import sync_runner
    from services.pipeline import preflight, run as run_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _no_tushare_domain_registry)
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _c: None)
    monkeypatch.setattr(preflight, "ensure_calendar_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(preflight, "run_watermark_sla_check", lambda *_a, **_k: 0)

    class ForbiddenSource:
        def __init__(self):
            raise AssertionError("tushare probe must not run when no tushare domain is due")

    monkeypatch.setattr(preflight, "TuShareSource", ForbiddenSource)
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    started = []
    for name in ("run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: started.append(_n))

    rc = run_mod.main(["--dry", "--date", "20260911"])

    assert rc != 3
    assert started == ["run_acquire", "run_clean", "run_process", "run_store"]
    assert "本次无 tushare 源域 due" in (tmp_path / "run.log").read_text()


def test_run_end_to_end_degrades_not_hard_when_tushare_domain_due_and_expired(
    monkeypatch, tmp_path
):
    """验收断言2 端到端: --date 20260911 干跑, registry 有一个仍 due 的 tushare 域且过期

    -> 探针真的跑了且被拒 (authorization_blocked 落 degraded); exit != 3; 四阶段全部
    有启动记录 (非 tushare 域/后续阶段不因这一个域被牵连熄火)。
    """
    from services.data_sources import sync_runner
    from services.data_sources.sources.tushare import TuShareAuthorizationError
    from services.pipeline import preflight, run as run_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _c: None)
    monkeypatch.setattr(preflight, "ensure_calendar_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(preflight, "run_watermark_sla_check", lambda *_a, **_k: 0)

    calls = []

    class ExpiredSource:
        def authorization_status(self):
            calls.append("user")
            raise TuShareAuthorizationError("auth_expired")

    monkeypatch.setattr(preflight, "TuShareSource", ExpiredSource)
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    started = []
    for name in ("run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: started.append(_n))

    rc = run_mod.main(["--dry", "--date", "20260911"])

    assert calls == ["user"], "有 tushare 域 due 时必须真的探测"
    assert rc != 3
    assert started == ["run_acquire", "run_clean", "run_process", "run_store"]
    log_text = (tmp_path / "run.log").read_text()
    assert "authorization_blocked" in log_text and "auth_expired" in log_text


def test_run_end_to_end_today_behavior_unchanged_when_auth_not_expired(monkeypatch, tmp_path):
    """验收断言4: 今天(授权未到期)行为不变 —— 正常 probe、正常通过、四阶段启动、无降级。"""
    from services.data_sources import sync_runner
    from services.pipeline import preflight, run as run_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _c: None)
    monkeypatch.setattr(preflight, "ensure_calendar_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(preflight, "run_watermark_sla_check", lambda *_a, **_k: 0)

    calls = []
    status = {
        "opened_at": datetime(2026, 6, 17, tzinfo=ZoneInfo("Asia/Shanghai")),
        "expires_at": datetime(2099, 8, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        "remaining_weeks": 4,
    }

    class HealthySource:
        def authorization_status(self):
            calls.append("user")
            return status

    monkeypatch.setattr(preflight, "TuShareSource", HealthySource)
    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    started = []
    for name in ("run_acquire", "run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx, _n=name: started.append(_n))

    rc = run_mod.main(["--dry", "--date", "20260908"])

    assert calls == ["user"]
    assert rc == 0
    assert started == ["run_acquire", "run_clean", "run_process", "run_store"]
    assert "authorization_blocked" not in (tmp_path / "run.log").read_text()


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

    monkeypatch.setattr(sync_runner, "load_registry", _invalid_policy_registry)
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
        # 2026-09-12: 触发条件从「合法 disabled」换成「策略形状非法」—— 合法 disabled 现在
        # 由选域处排除、不再阻塞(见 _disabled_trade_calendar_registry 头注与同名正向孪生用例);
        # 而本用例真正要守的是「策略读不出来时, 不许在日历/授权/SLA 之前放行」, 那条仍然成立。
        with pytest.raises(
            preflight.PipelinePreflightError,
            match="sync_execution_blocked:trade_cal:invalid_execution_policy",
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

    monkeypatch.setattr(sync_runner, "load_registry", _invalid_policy_registry)
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
        # 2026-09-12: 同上, 触发条件换成「策略形状非法」。守的东西不变:
        # 策略读不出来时不许走到供应商授权与 writer step。
        with pytest.raises(
            preflight.PipelinePreflightError,
            match="sync_execution_blocked:trade_cal:invalid_execution_policy",
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
    monkeypatch.setattr(sync_runner, "load_registry", _invalid_policy_registry)
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
    # 2026-09-12: reason 随 fixture 一起换成 invalid_execution_policy —— 守的仍是
    # 「策略读不出来时, stage 不许启动」(exit 5 = WRITER/stage block)。
    assert "sync_execution_blocked:trade_cal:invalid_execution_policy" in (
        tmp_path / "stage.log"
    ).read_text()


def test_disabled_domain_no_longer_blocks_preflight(monkeypatch, tmp_path):
    """合法 disabled 的域**不再**阻塞 preflight —— 2026-09-07→09-12 五天全链死锁的正向孪生。

    上面三条用例守的是「策略形状非法 → fail-closed」; 这一条守相反的那半:
    一个域被**合法地**标成 disabled(台账裁决 freeze/derive 后按 check_tushare_sunset 检查 8 的
    建议写上去的), preflight 必须放行, 让四阶段照常启动。
    缺了这一条, 「disabled 不该阻塞」这个语义就没有任何测试覆盖 —— 而那正是同形态在 48 天内
    复发两次的原因 (a84e0867 2026-07-21 margin / 01f8f41a 2026-09-07 十六个 freeze 域)。

    断言 ensure_calendar_ready 被调用: preflight 走过了选域与执行策略这两关。
    """
    from services.data_sources import sync_runner
    from services.pipeline import preflight
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(sync_runner, "load_registry", _disabled_trade_calendar_registry)
    reached = []
    monkeypatch.setattr(
        preflight, "ensure_calendar_ready", lambda *_a, **_k: reached.append("calendar")
    )
    monkeypatch.setattr(
        preflight, "ensure_tushare_authorized", lambda *_a, **_k: reached.append("auth")
    )
    monkeypatch.setattr(
        preflight, "run_watermark_sla_check", lambda *_a, **_k: reached.append("sla") or 0
    )
    ctx = PipelineContext(date="20260719", log_path=tmp_path / "pipeline.log")
    try:
        preflight.run_preflight(ctx)
    finally:
        ctx.close()

    assert "calendar" in reached, (
        "preflight 在选域/执行策略这一关就停了 —— 合法 disabled 又把全链锁死了"
    )
    log = (tmp_path / "pipeline.log").read_text()
    assert "sync_execution_blocked" not in log
    # healthy_domain 是唯一 enabled 且非 on_demand 的域, 所以 all-due 集合恰好剩它
    assert sync_runner.automatic_domains(_disabled_trade_calendar_registry()) == [
        "healthy_domain"
    ]


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
    # Lone non-hard "other" degrade → integrity_observe (not soft_waiting mislabel).
    assert "DONE integrity_observe" in (tmp_path / "run.log").read_text()
    assert "DONE soft_waiting_clock" not in (tmp_path / "run.log").read_text()


def test_run_done_log_uses_soft_waiting_when_named_clock(monkeypatch, tmp_path):
    from services.pipeline import run as run_mod
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(
        run_mod,
        "PipelineContext",
        lambda **kw: PipelineContext(**{**kw, "log_path": tmp_path / "run.log"}),
    )
    monkeypatch.setattr("services.pipeline.context.DEGRADED_FLAG", tmp_path / "flag")
    monkeypatch.setattr(run_mod, "run_preflight", lambda ctx: None)
    monkeypatch.setattr(
        run_mod,
        "run_acquire",
        lambda ctx: ctx.degraded(
            "domain daily pending_publish reason=pre_available_after_zero_rows"
        ),
    )
    for name in ("run_clean", "run_process", "run_store"):
        monkeypatch.setattr(run_mod, name, lambda ctx: None)

    rc = run_mod.main(["--dry", "--skip-sync", "--date", "20260101"])
    assert rc == 1
    log = (tmp_path / "run.log").read_text()
    assert "DONE soft_waiting_clock" in log
    assert "DONE integrity_observe" not in log


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
        acquire,
        "_run_drain_subprocess",
        lambda _ctx, _cmd: (
            1,
            json.dumps(
                [
                    {
                        "domain": "margin",
                        "status": "partial",
                        "still_failed": ["20260716"],
                        "truncated": False,
                    }
                ]
            ),
            "",
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


def test_drain_subprocess_streams_stderr_live_to_log(monkeypatch, tmp_path, capsys):
    """Owner 2026-07-22: drain must not buffer ~40 min of output until it ends.

    stderr (per-domain progress) streams into the parent log *as it arrives*;
    stdout stays a single JSON list parsed by the caller. Regression against the
    old ``subprocess.run(capture_output=True)`` that made the UI look hung.
    """
    import subprocess as _sp

    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    stderr_lines = [
        "[drain 1/2] domain=adj_factor …\n",
        "[drain 2/2] domain=moneyflow …\n",
    ]
    written_when_first_stderr_seen: dict[str, str] = {}

    class _FakePopen:
        def __init__(self, *_a, **_k):
            self.returncode = 0
            self.stdout = io.StringIO(
                json.dumps([{"domain": "adj_factor", "status": "clean"}])
            )
            self.stderr = iter(stderr_lines)

        def wait(self):
            return 0

    monkeypatch.setattr(_sp, "Popen", _FakePopen)
    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        rc, out, err = acquire._run_drain_subprocess(ctx, ["fake", "cmd"])
    finally:
        ctx.close()
    assert rc == 0
    assert "adj_factor" in out  # child stdout captured whole for json.loads
    log_text = (tmp_path / "run.log").read_text()
    # Per-domain progress reached the date-suffixed pipeline log (not buffered).
    assert "[drain 1/2] domain=adj_factor" in log_text
    assert "[drain 2/2] domain=moneyflow" in log_text
    assert err.count("[drain") == 2
    # …and our stdout (the wrapper's job log the workbench reads) — live UI progress.
    ui_stdout = capsys.readouterr().out
    assert "[drain 1/2] domain=adj_factor" in ui_stdout
    assert "[drain 2/2] domain=moneyflow" in ui_stdout


def test_sync_registry_drain_skips_margin_gate_when_disabled(monkeypatch, tmp_path):
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "_margin_hard_gate_required", lambda registry=None: False)
    monkeypatch.setattr(
        acquire,
        "_run_drain_subprocess",
        lambda _ctx, _cmd: (
            0,
            json.dumps([{"domain": "adj_factor", "status": "clean"}]),
            "",
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

    # 本测试的意图是"最新日已接受 → 一次都不拉", 桩返回真值(全部已接受)与之自洽;
    # 唯一缺的是确定日历 (见文件上方 _stub_trading_days 注释)。
    class _Conn:
        def execute(self, *_a, **_k):
            return SimpleNamespace(
                fetchone=lambda: {"ok": 1}, fetchall=lambda: [({"ok": 1},)]
            )

        def close(self):
            pass

    _stub_trading_days(monkeypatch, sync_runner, ["20260717", "20260720", "20260721"])
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
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260722"}),
    )
    ctx = PipelineContext(date="20260721", log_path=tmp_path / "run.log")
    try:
        acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()
    assert calls == []


def test_observe_frozen_margin_logs_calendar_lag_without_fetch(monkeypatch, tmp_path):
    """Frozen margin: calendar eligible known; catchup blocked; no provider call."""
    from services.data_sources import sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    registry = {
        "domains": {
            "margin": {
                "domain": "margin",
                "sync_policy": "on_demand",
                "target_table": "raw_tushare_margin",
                "execution_policy": {"mode": "disabled", "reason": "scope_blocked"},
            }
        }
    }
    run_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            if "canonical_margin" in sql:
                raise RuntimeError("missing")
            return SimpleNamespace(fetchone=lambda: ("20260716",))

        def close(self):
            pass

    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(
            eligible_end="20260722", reason="next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sync_runner,
        "run_domain",
        lambda *a, **k: run_calls.append((a, k)) or {"status": "ok"},
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())
    from services.pipeline.frozen_domain_observe import observe_frozen_on_demand_domains

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = observe_frozen_on_demand_domains(ctx)
    finally:
        ctx.close()
    assert run_calls == []
    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "observe_frozen"
    assert outcomes[0]["eligible_end"] == "20260722"
    assert outcomes[0]["local_max"] == "20260716"
    assert outcomes[0]["catchup_blocked"] is True
    assert outcomes[0]["policy_reason"] == "scope_blocked"


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

    # 测试名与断言都说"只缺 eligible_end 那一天 → 每域各拉一次"。原实现的桩一律返回 None
    # (= 一天都没接受过), 于是 2026-08-21 加的 gap-heal 把窗口内每一天都当成洞,
    # 断言 2 次实际得到 20 次。改用按 partition_value 判的桩, 让"只缺一天"能被精确表达。
    _stub_trading_days(monkeypatch, sync_runner, ["20260717", "20260720", "20260721"])
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
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260721"}),
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


def test_formal_on_demand_catchup_soft_skips_pending_publish(
    monkeypatch, tmp_path, capsys
):
    """Morning UI: same-day empty formal must not TIER0-block --all-due drain."""
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

    # 2026-09-03: 断言 outcomes 全为 pending_publish ⇒ 只测 eligible 日, 不混 gap-heal。
    # 原桩一律返回 None(= 一天都没接受过), gap-heal 会把窗口内每天都当成洞。
    _stub_trading_days(monkeypatch, sync_runner, ["20260720", "20260721", "20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(
        sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain]
    )
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(
            eligible_end="20260722", reason="manual_calendar_eligible"
        ),
    )

    def _run(domain, **kwargs):
        reason = (
            "pre_available_after_zero_rows"
            if domain == "daily"
            else "same_day_vendor_vacuum"
        )
        return {
            "domain": domain,
            "status": "ok",
            "failed_batches": 0,
            "pending_publish": True,
            "pending_publish_reason": reason,
            "rows": 0,
        }

    monkeypatch.setattr(sync_runner, "run_domain", _run)
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260722"}),
    )
    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()
    out = capsys.readouterr().out
    assert "pending_publish" in out
    assert "same_day_vendor_vacuum" in out
    assert "20260722" in out
    assert all(o.get("action") == "pending_publish" for o in outcomes)
    assert not ctx.degraded_msgs


def test_formal_hard_fail_degrades_not_raises_and_continues_sibling(
    monkeypatch, tmp_path, capsys
):
    """C1 invariant: formal hard-fail stays domain-local (no Tier0AcquireError / no sibling abort).

    Architecture plan Phase 2 — no cross-sibling hard-raise; do not invent a DAG.
    """
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

    # 2026-09-03: 断言 seen == [daily, stock_st] ⇒ 每域恰好一次。
    # 原桩一律返回 None(= 一天都没接受过), gap-heal 会把窗口内每天都当成洞。
    _stub_trading_days(monkeypatch, sync_runner, ["20260720", "20260721", "20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(
        sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain]
    )
    monkeypatch.setattr(
        sync_runner,
        "eligible_end_date",
        lambda _spec, **_kwargs: SimpleNamespace(
            eligible_end="20260722", reason="manual_calendar_eligible"
        ),
    )
    seen: list[str] = []

    def _run(domain, **kwargs):
        seen.append(domain)
        if domain == "daily":
            return {
                "domain": domain,
                "status": "error",
                "failed_batches": 1,
                "error": "daily capture rejects empty provider rows",
            }
        return {
            "domain": domain,
            "status": "ok",
            "failed_batches": 0,
            "pending_publish": True,
            "pending_publish_reason": "same_day_vendor_vacuum",
            "rows": 0,
        }

    monkeypatch.setattr(sync_runner, "run_domain", _run)
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260722"}),
    )
    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()
    assert seen == ["daily", "stock_st"], "sibling stock_st must still run"
    assert any(o.get("action") == "failed" and o.get("domain") == "daily" for o in outcomes)
    assert any(o.get("action") == "pending_publish" for o in outcomes)
    assert any("formal daily" in msg for msg in ctx.degraded_msgs)


# ---------------------------------------------------------------------------
# C7/C8 (ST 契约 v2 刀3, spec §5.4 / 附录 C7-C8): answerable_dates 源能力探测 +
# 逐域 规划→执行 顺序。
# ---------------------------------------------------------------------------


def _stock_st_only_registry(*, daily_source="fake_daily", stock_st_source="fake_stock_st"):
    return {
        "domains": {
            "daily": {
                "domain": "daily",
                "sync_policy": "on_demand",
                "source": daily_source,
                "execution_policy": {"mode": "enabled", "reason": "authorized_manual_generation"},
            },
            "stock_st": {
                "domain": "stock_st",
                "sync_policy": "on_demand",
                "source": stock_st_source,
                "execution_policy": {"mode": "enabled", "reason": "authorized_manual_generation"},
            },
        }
    }


def test_c7_answerable_dates_splits_holes_run_domain_called_only_for_answerable(
    monkeypatch, tmp_path
):
    """假 adapter answerable_dates 判 3 洞中 2 不可答: run_domain 只被调 1 次 (那个
    可答的日子); outcomes 含 2 条 action=='unanswerable'; ctx.degraded 0 次。"""
    from services.data_sources import sync_runner
    from services.data_sources.sources.stock_st_derive import DateAnswerability
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS", ("stock_st",))
    registry = _stock_st_only_registry()
    # 4 个交易日, eligible_end=20260722; 720/721/722 缺, 719 已接受 -> 3 个洞。
    _stub_trading_days(monkeypatch, sync_runner, ["20260719", "20260720", "20260721", "20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner, "eligible_end_date",
        lambda _spec, **_k: SimpleNamespace(eligible_end="20260722", reason="manual_calendar_eligible"),
    )

    calls: list[str] = []
    monkeypatch.setattr(
        sync_runner, "run_domain",
        lambda domain, **kwargs: calls.append(kwargs["start"])
        or {"domain": domain, "status": "ok", "failed_batches": 0},
    )

    class _FakeAdapter:
        def answerable_dates(self, dates, *, conn):
            out = {}
            for d in dates:
                if d == "20260722":
                    out[d] = DateAnswerability(True, "baostock_reservoir", None, None)
                else:
                    out[d] = DateAnswerability(False, None, "no_local_source_for_date", "fill it")
            return out

    monkeypatch.setattr(sync_runner, "_adapter", lambda _source: _FakeAdapter())
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260720", "20260721", "20260722"}),
    )

    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()

    assert calls == ["20260722"], "只有可答的那个日子该进 run_domain"
    unanswerable = [o for o in outcomes if o.get("action") == "unanswerable"]
    assert {o["trade_date"] for o in unanswerable} == {"20260720", "20260721"}
    assert all(o["reason"] == "no_local_source_for_date" for o in unanswerable)
    assert ctx.degraded_msgs == []


def test_c7_run_domain_typed_unanswerable_result_does_not_degrade(monkeypatch, tmp_path):
    """run_domain 本身 (取数时) 返回 status='unanswerable' -> outcome 收口成
    action=='unanswerable', 不进 ctx.degraded (与 'failed' 分支互斥的另一条路径:
    取数时才发现答不出, 不是规划阶段 answerable_dates 就已经拦下)。"""
    from services.data_sources import sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS", ("stock_st",))
    registry = _stock_st_only_registry()
    _stub_trading_days(monkeypatch, sync_runner, ["20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner, "eligible_end_date",
        lambda _spec, **_k: SimpleNamespace(eligible_end="20260722", reason="manual_calendar_eligible"),
    )
    monkeypatch.setattr(
        sync_runner, "run_domain",
        lambda domain, **kwargs: {
            "domain": domain, "status": "unanswerable", "failed_batches": 0,
            "unanswerable": True, "unanswerable_reason": "no_local_source_for_date",
            "remedy": "ingest_baostock_daily_k_reservoir.py --start 20260722 --end 20260722 --execute",
        },
    )
    # 没有 answerable_dates 方法 -> 规划阶段全部放行, 真正的"答不出"在取数时才暴露。
    monkeypatch.setattr(sync_runner, "_adapter", lambda _source: object())
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260722"}),
    )

    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()

    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "unanswerable"
    assert outcomes[0]["reason"] == "no_local_source_for_date"
    assert ctx.degraded_msgs == []


def test_c7_adapter_without_answerable_dates_plans_all_holes(monkeypatch, tmp_path):
    """没有 answerable_dates 方法的适配器 (今天的 fuyao/tushare) = 任何日期都可试,
    3 个洞全部原样进入执行循环, 行为与刀 3 之前完全一致。"""
    from services.data_sources import sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS", ("stock_st",))
    registry = _stock_st_only_registry()
    _stub_trading_days(monkeypatch, sync_runner, ["20260719", "20260720", "20260721", "20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner, "eligible_end_date",
        lambda _spec, **_k: SimpleNamespace(eligible_end="20260722", reason="manual_calendar_eligible"),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        sync_runner, "run_domain",
        lambda domain, **kwargs: calls.append(kwargs["start"])
        or {"domain": domain, "status": "ok", "failed_batches": 0},
    )
    monkeypatch.setattr(sync_runner, "_adapter", lambda _source: object())  # 无 answerable_dates
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260720", "20260721", "20260722"}),
    )

    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()

    assert sorted(calls) == ["20260720", "20260721", "20260722"]
    assert not any(o.get("action") == "unanswerable" for o in outcomes)


def test_c7_answerable_dates_raising_fails_open_with_warning(monkeypatch, tmp_path):
    """取适配器或调 answerable_dates 本身抛异常 -> fail-open (3 个洞全规划, 全部
    进执行循环) + 一条 warning 日志, 不会因为探测失败而静默漏判。"""
    from services.data_sources import sync_runner
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "FORMAL_ON_DEMAND_SECURITY_DAY_DOMAINS", ("stock_st",))
    registry = _stock_st_only_registry()
    _stub_trading_days(monkeypatch, sync_runner, ["20260719", "20260720", "20260721", "20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner, "eligible_end_date",
        lambda _spec, **_k: SimpleNamespace(eligible_end="20260722", reason="manual_calendar_eligible"),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        sync_runner, "run_domain",
        lambda domain, **kwargs: calls.append(kwargs["start"])
        or {"domain": domain, "status": "ok", "failed_batches": 0},
    )

    class _ExplodingAdapter:
        def answerable_dates(self, dates, *, conn):
            raise RuntimeError("reservoir table is on fire")

    monkeypatch.setattr(sync_runner, "_adapter", lambda _source: _ExplodingAdapter())
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260720", "20260721", "20260722"}),
    )

    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()

    assert sorted(calls) == ["20260720", "20260721", "20260722"]
    assert not any(o.get("action") == "unanswerable" for o in outcomes)
    log_text = Path(ctx.log_path).read_text(encoding="utf-8")
    assert "answerable_dates 探测失败" in log_text
    assert "reservoir table is on fire" in log_text


def test_c8_daily_write_within_same_run_makes_stock_st_hole_answerable(monkeypatch, tmp_path):
    """同次顺序 (F10): daily 的 run_domain 桩向假水库写入 D 的行 -> 同一次调用里
    stock_st 对 D 的洞判可答并被规划执行 (逐域 规划->执行, 不是先全规划后执行)。"""
    from services.data_sources import sync_runner
    from services.data_sources.sources.stock_st_derive import DateAnswerability
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    registry = _stock_st_only_registry()
    reservoir_written_for: set[str] = set()

    _stub_trading_days(monkeypatch, sync_runner, ["20260722"])
    monkeypatch.setattr(sync_runner, "load_registry", lambda: registry)
    monkeypatch.setattr(sync_runner, "domain_spec", lambda reg, domain: reg["domains"][domain])
    monkeypatch.setattr(
        sync_runner, "eligible_end_date",
        lambda _spec, **_k: SimpleNamespace(eligible_end="20260722", reason="manual_calendar_eligible"),
    )

    def _run(domain, **kwargs):
        if domain == "daily":
            reservoir_written_for.add(kwargs["start"])
        return {"domain": domain, "status": "ok", "failed_batches": 0}

    monkeypatch.setattr(sync_runner, "run_domain", _run)

    class _StockStAdapter:
        def answerable_dates(self, dates, *, conn):
            return {
                d: (
                    DateAnswerability(True, "baostock_reservoir", None, None)
                    if d in reservoir_written_for
                    else DateAnswerability(False, None, "no_local_source_for_date", "fill it")
                )
                for d in dates
            }

    def _adapter(source_name):
        if source_name == "fake_daily":
            return object()  # daily 适配器没有 answerable_dates
        return _StockStAdapter()

    monkeypatch.setattr(sync_runner, "_adapter", _adapter)
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *_a, **_k: _AcceptedExceptConn({"20260722"}),
    )

    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        outcomes = acquire._sync_formal_on_demand_security_days(ctx)
    finally:
        ctx.close()

    assert "20260722" in reservoir_written_for  # daily 确实先执行过
    assert any(
        o.get("domain") == "stock_st" and o.get("action") == "accepted"
        for o in outcomes
    ), (
        "stock_st 的洞应在同一次运行里 (daily 执行之后) 被判可答并执行, "
        "不该落成 unanswerable —— 若代码退回'先把两域全规划完再执行', "
        "本断言会变红 (daily 那一步的水库写入此刻还没发生)"
    )


def test_acquire_runs_registry_drain_before_formal_and_despite_formal_hard(
    monkeypatch, tmp_path
):
    """C1 invariant: drain-first; formal hard must not kidnap --all-due / abort acquire.

    Architecture plan Phase 2 — order + no-raise; second true kidnap not observed → no DAG.
    """
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    order: list[str] = []

    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_pipeline_sync_ready", lambda _c: None
    )
    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_tushare_authorized", lambda _c: None
    )
    monkeypatch.setattr(
        acquire, "_sync_holders_aif10", lambda _c: order.append("holders")
    )
    monkeypatch.setattr(acquire, "_sync_qfii", lambda: order.append("qfii"))
    # acquire.py:83 是 `ctx.step(lambda: _sync_org_holding(ctx), ...)` —— 带 ctx 调。
    # 桩少一个参数会抛 TypeError, 而 ctx.step 的降级路径把它吞成 DEGRADED,
    # 于是**测试照绿而桩是死的**。2026-09-06 在全新克隆里才暴露 (本地 data/ 齐全时看不见)。
    monkeypatch.setattr(acquire, "_sync_org_holding", lambda _c: order.append("org"))
    monkeypatch.setattr(
        acquire,
        "_sync_registry_drain",
        lambda _c: order.append("drain"),
    )
    # 本条测的是**步骤顺序**, 不是 margin 追赶。同文件另两条测试(见下方)都打了这个桩,
    # 只有这条漏了 —— 于是它在 data/reference.duckdb 存在的机器上碰巧绿, 在全新克隆(CI)里红。
    monkeypatch.setattr(
        "services.pipeline.margin_catchup_acquire.run_margin_bounded_catchup", lambda _c: []
    )
    monkeypatch.setattr(
        "services.pipeline.frozen_domain_observe.observe_frozen_on_demand_domains", lambda _c: []
    )

    def _formal(ctx):
        order.append("formal")
        ctx.degraded("formal daily land_then_accept failed for 20260722: simulated")
        return [{"domain": "daily", "action": "failed"}]

    monkeypatch.setattr(acquire, "_sync_formal_on_demand_security_days", _formal)
    monkeypatch.setattr(
        acquire, "_build_trading_calendar", lambda: order.append("calendar")
    )
    # acquire.py:124 带 ctx 调 —— 少参数会被 ctx.step 降级吞掉, 桩静默失效
    monkeypatch.setattr(
        acquire, "_refresh_active_a_stock_master", lambda _c: order.append("active")
    )
    # acquire.py:95/129 两处未打桩会真的开 tushare_raw/smartmoney/reference (§1.1/§1.7)。
    monkeypatch.setattr(
        "services.type_b_fact_publish_catchup.run_acquire_type_b_publish_catchup",
        lambda ctx: order.append("type_b") or {"status": "skipped"},
    )
    monkeypatch.setattr(
        acquire,
        "_finalize_acquire_delta",
        lambda ctx, drain_results=None, formal_outcomes=None: order.append("finalize"),
    )

    ctx = PipelineContext(date="20260722", log_path=tmp_path / "run.log")
    try:
        acquire.run_acquire(ctx)
    finally:
        ctx.close()
    assert order.index("drain") < order.index("formal"), order
    assert "drain" in order and "formal" in order
    assert "calendar" in order
    # 桩必须承重: 不断言它就等于没打桩 —— 上面那个 0 参数的死桩正是这么藏了下来。
    assert "org" in order, "org_holding 桩没被调到 (多半是签名不匹配被降级吞了)"
    assert "holders" in order and "qfii" in order
    assert "active" in order, "active_stock 桩没被调到 (签名不匹配?)"
    # Structural: formal hard → degraded, not Tier0AcquireError abort.
    assert any("formal daily" in msg for msg in ctx.degraded_msgs)
    assert order.index("drain") < order.index("type_b") < order.index("formal"), order
    assert order[-1] == "finalize", order


def test_acquire_continues_non_tushare_steps_when_top_level_auth_blocked(
    monkeypatch, tmp_path
):
    """验收断言2 (acquire 层): 顶层探针被拒不得让 ACQUIRE 整体流产——

    drain/formal/calendar/active 这些非 tushare 步骤必须照常尝试, 且该次授权阻断要
    以含 'authorization_blocked' 字样的 degraded 记录下来 (可 grep 追责, 不是静默吞)。
    """
    from services.data_sources.sources.tushare import TuShareAuthorizationError
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    order: list[str] = []

    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_pipeline_sync_ready", lambda _c: None
    )

    def _blocked(_ctx):
        raise TuShareAuthorizationError("auth_expired")

    monkeypatch.setattr("services.pipeline.preflight.ensure_tushare_authorized", _blocked)
    monkeypatch.setattr(acquire, "_sync_holders_aif10", lambda _c: order.append("holders"))
    monkeypatch.setattr(acquire, "_sync_qfii", lambda: order.append("qfii"))
    monkeypatch.setattr(acquire, "_sync_org_holding", lambda _c: order.append("org"))
    monkeypatch.setattr(acquire, "_sync_registry_drain", lambda _c: order.append("drain") or [])
    monkeypatch.setattr(
        acquire,
        "_sync_formal_on_demand_security_days",
        lambda ctx: order.append("formal") or [],
    )
    monkeypatch.setattr(acquire, "_build_trading_calendar", lambda: order.append("calendar"))
    monkeypatch.setattr(
        acquire, "_refresh_active_a_stock_master", lambda _c: order.append("active")
    )
    # margin/frozen-observe 是既有、与本次授权改动无关的两步, 但都会触真 DB (dim_trading_calendar);
    # 与 test_acquire_runs_registry_drain_before_formal_and_despite_formal_hard 同理隔离掉,
    # 否则本测试单独跑绿、混进全文件顺序跑会被同一个预置的真 DB/state 依赖问题带崩 (pre-existing,
    # 不是本次改动引入 —— 见改动报告"发现但未修"一节)。
    monkeypatch.setattr(
        "services.pipeline.margin_catchup_acquire.run_margin_bounded_catchup", lambda _c: []
    )
    monkeypatch.setattr(
        "services.pipeline.frozen_domain_observe.observe_frozen_on_demand_domains", lambda _c: []
    )
    monkeypatch.setattr(
        "services.type_b_fact_publish_catchup.run_acquire_type_b_publish_catchup",
        lambda ctx: order.append("type_b") or {"status": "skipped"},
    )
    monkeypatch.setattr(
        acquire,
        "_finalize_acquire_delta",
        lambda ctx, drain_results=None, formal_outcomes=None: order.append("finalize"),
    )

    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        acquire.run_acquire(ctx)  # 不应抛出 — 授权阻断只降级
    finally:
        ctx.close()

    assert order == [
        "holders", "qfii", "org", "drain", "type_b", "formal", "calendar", "active", "finalize",
    ], "顶层授权阻断不得让任何一步被跳过"
    assert any(
        "authorization_blocked" in msg and "auth_expired" in msg for msg in ctx.degraded_msgs
    )


def test_acquire_top_level_auth_success_leaves_no_authorization_blocked_message(
    monkeypatch, tmp_path
):
    """反向验证: 授权正常时不得凭空产生 authorization_blocked 降级消息。"""
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_pipeline_sync_ready", lambda _c: None
    )
    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_tushare_authorized", lambda _c: None
    )
    monkeypatch.setattr(acquire, "_sync_holders_aif10", lambda _c: None)
    monkeypatch.setattr(acquire, "_sync_qfii", lambda: None)
    monkeypatch.setattr(acquire, "_sync_org_holding", lambda _c: None)
    monkeypatch.setattr(acquire, "_sync_registry_drain", lambda _c: [])
    monkeypatch.setattr(acquire, "_sync_formal_on_demand_security_days", lambda ctx: [])
    monkeypatch.setattr(acquire, "_build_trading_calendar", lambda: None)
    monkeypatch.setattr(acquire, "_refresh_active_a_stock_master", lambda _c: None)
    monkeypatch.setattr(
        "services.pipeline.margin_catchup_acquire.run_margin_bounded_catchup", lambda _c: []
    )
    monkeypatch.setattr(
        "services.pipeline.frozen_domain_observe.observe_frozen_on_demand_domains", lambda _c: []
    )
    monkeypatch.setattr(
        "services.type_b_fact_publish_catchup.run_acquire_type_b_publish_catchup",
        lambda ctx: {"status": "skipped"},
    )
    monkeypatch.setattr(
        acquire,
        "_finalize_acquire_delta",
        lambda ctx, drain_results=None, formal_outcomes=None: None,
    )

    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        acquire.run_acquire(ctx)
    finally:
        ctx.close()

    # 今天(未打桩前) degraded_msgs 里会静默混进两条 type_b 降级消息, 测试原先只查
    # authorization_blocked 所以照绿 —— 断言改强为完全为空, 才能证明 type_b 已被打桩。
    assert ctx.degraded_msgs == []


def test_sync_registry_drain_degrades_not_raises_on_batch_auth_block(monkeypatch, tmp_path):
    """验收断言2 (drain 层): --all-due --drain 整批探针被拒时 degrade + 返回空列表, 不 raise。

    这是 _sync_registry_drain 本身 (非顶层探针) 的授权阻断路径 —— 子进程 exit 3。
    """
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    def _fake_drain_subprocess(_ctx, _cmd):
        import json

        payload = json.dumps({"status": "authorization_blocked", "reason": "auth_expired"})
        return 3, payload, ""

    monkeypatch.setattr(acquire, "_run_drain_subprocess", _fake_drain_subprocess)
    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        results = acquire._sync_registry_drain(ctx)  # 不应抛出
    finally:
        ctx.close()

    assert results == []
    assert any(
        "authorization_blocked" in msg and "auth_expired" in msg for msg in ctx.degraded_msgs
    )


def test_sync_registry_drain_raises_tier0_on_non_auth_bad_output(monkeypatch, tmp_path):
    """反向验证: returncode==3 才是授权阻断专属通道——非 3 的坏输出仍要走原有 Tier0AcquireError,

    不能因为新增的 degrade 分支而连带把其它坏路径也悄悄咽掉。
    """
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    def _fake_drain_subprocess(_ctx, _cmd):
        return 1, "not-json{{{", ""

    monkeypatch.setattr(acquire, "_run_drain_subprocess", _fake_drain_subprocess)
    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    try:
        with pytest.raises(acquire.Tier0AcquireError):
            acquire._sync_registry_drain(ctx)
    finally:
        ctx.close()


def test_unrelated_sync_failure_degrades_only_after_margin_gate_passes(
    monkeypatch, tmp_path
):
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr(acquire, "_margin_hard_gate_required", lambda registry=None: True)
    monkeypatch.setattr(
        acquire,
        "_run_drain_subprocess",
        lambda _ctx, _cmd: (
            1,
            json.dumps(
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
            "",
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
    from services.data_sources import sync_runner
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

    # 测试必须自带 fixture, 不许借真实 sync_registry.yaml 当前是否还有 tushare 源域过关
    # (那会随域迁移悄悄改变含义) —— 显式给一个仍 due 的 tushare 域。
    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
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
        # 2026-09-03: 本桩拦的是**全部** subprocess 调用, 不只 update_watermark_sla.py。
        # store.run_store 的 system_health 自检还会调 check_db_invariants.py --json-out
        # (注意是 --json-out, 不是 --json-output —— 两个脚本参数名本来就不同),
        # 原实现无守卫直接 cmd.index("--json-output") → ValueError: '--json-output' is not in list,
        # 被 store 吞成 "subprocess 异常" 并让本测试红。
        # 同文件 test_post_acquire_sla_only_when_alert_persists 的同款桩早就有这道守卫,
        # 两处只修了一处。
        if "--json-output" not in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
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
    from services.data_sources import sync_runner
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

    # 显式给一个仍 due 的 tushare 域 (测试自带 fixture, 不借真实 registry 当前状态过关)。
    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
    monkeypatch.setattr(preflight, "TuShareSource", FakeSource)
    monkeypatch.setattr(preflight, "ensure_pipeline_sync_ready", lambda _ctx: None)
    # dry 路径末尾仍会调 _finalize_acquire_delta → probe_dc_source_frontier()
    # (smartmoney RO + attach tushare_raw RO); 本测试测的是授权探针, 不是它。
    monkeypatch.setattr(
        acquire,
        "_finalize_acquire_delta",
        lambda ctx, drain_results=None, formal_outcomes=None: None,
    )
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
    from services.data_sources import sync_runner
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

    # 显式给一个仍 due 的 tushare 域 (测试自带 fixture, 不借真实 registry 当前状态过关)。
    monkeypatch.setattr(sync_runner, "load_registry", _tushare_domain_due_registry)
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


# ── 2026-09-18: result dict 里的 errors/status 被吞, 只印不降级 ────────────────
#
# 根因: holders_aif10 CARRY_FIELDS 对着真实 canonical 表 BinderException, 连续 13 天
# (20260904→20260917) 每个 notice_date 都失败, sync_holders_aif10_incremental 把
# 失败收进 result["errors"] 正常 return(从不 raise), _sync_holders_aif10 只把它打进
# print, acquire.py 顶层 `ctx.step(..., degraded_msg=...)` 只在**抛异常**时才降级 ——
# 于是整条日更能在十大股东静默停摆的情况下 exit 0。本节测试锁住两处修复:
# _sync_holders_aif10 本身 result.errors 非空必须转 degraded; 以及扫同类找到的
# 同一形态问题 _sync_org_holding(非 repair_ 前缀 action 的 status="partial" 之前完全
# 不降级)。


class _FakeConnCloseOnly:
    """占位连接: 只需要 `.close()` 存在, 真实 DB I/O 已被下面的 monkeypatch 挡在外面。"""

    def close(self) -> None:
        pass


def test_sync_holders_aif10_degrades_when_result_has_errors(monkeypatch, tmp_path):
    """result["errors"] 非空必须 ctx.degraded 一次, 消息含域名与错误条数。"""
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr("services.db.get_conn", lambda: _FakeConnCloseOnly())
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10_incremental",
        lambda _conn: {
            "watermark": "20260904",
            "net_new_notice_rows": 0,
            "notice_partitions_touched": 0,
            "rewrite_amplification_rows": 0,
            "errors": [
                "20260910:write:BinderException:Binder Error: Referenced column "
                '"hold_amount" not found in FROM clause!'
            ],
        },
    )
    ctx = PipelineContext(date="20260918", log_path=tmp_path / "run.log")
    try:
        acquire._sync_holders_aif10(ctx)
    finally:
        ctx.close()
    assert len(ctx.degraded_msgs) == 1, ctx.degraded_msgs
    assert "holders_aif10" in ctx.degraded_msgs[0]
    assert "1" in ctx.degraded_msgs[0]


def test_sync_holders_aif10_no_degrade_when_result_errors_empty(monkeypatch, tmp_path):
    """result["errors"] 为空时不得误报降级 (回归: 别把每次同步都变成假警报)。"""
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    monkeypatch.setattr("services.db.get_conn", lambda: _FakeConnCloseOnly())
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10_incremental",
        lambda _conn: {
            "watermark": "20260917",
            "net_new_notice_rows": 12,
            "notice_partitions_touched": 1,
            "rewrite_amplification_rows": 0,
            "errors": [],
        },
    )
    ctx = PipelineContext(date="20260918", log_path=tmp_path / "run.log")
    try:
        acquire._sync_holders_aif10(ctx)
    finally:
        ctx.close()
    assert ctx.degraded_msgs == []


def _org_holding_result(*, status, action, accept_status=None):
    return {
        "domain": "org_holding",
        "count": 0,
        "status": status,
        "action": action,
        "report_date": "20260630",
        "available_date": "20260830",
        "written": 0,
        "fetch_status": "ok",
        "accept": {"status": accept_status, "partitions": []},
        "gap": {},
        "message": f"check: action={action} status={status}",
    }


def _patch_org_holding_sync(monkeypatch, result: dict):
    monkeypatch.setattr(
        "services.org_holding_db.connect_org_holding", lambda: _FakeConnCloseOnly()
    )
    monkeypatch.setattr(
        "services.org_holding_aif10.org_holding_period_gap_report", lambda _conn: {}
    )

    async def _fake_sync(_conn):
        return result

    monkeypatch.setattr(
        "services.org_holding_aif10.sync_org_holding_incremental", _fake_sync
    )


def test_sync_org_holding_degrades_on_partial_status_without_repair_prefix(
    monkeypatch, tmp_path
):
    """扫同类命中: fetch_then_accept(非 repair_ 前缀) accept 失败 status="partial"
    时, 旧判断(只挑 action.startswith("repair_"))完全不降级 —— 与 holders_aif10 的
    result["errors"] 被吞同一形态, 一并修。"""
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    _patch_org_holding_sync(
        monkeypatch,
        _org_holding_result(
            status="partial", action="fetch_then_accept", accept_status="accept_failed"
        ),
    )
    ctx = PipelineContext(date="20260918", log_path=tmp_path / "run.log")
    try:
        acquire._sync_org_holding(ctx)
    finally:
        ctx.close()
    assert len(ctx.degraded_msgs) == 1, ctx.degraded_msgs
    assert "org_holding" in ctx.degraded_msgs[0]
    assert "partial" in ctx.degraded_msgs[0]


def test_sync_org_holding_still_degrades_on_repair_prefix_partial(monkeypatch, tmp_path):
    """回归: repair_ 前缀 action 失败原本就该降级, 泛化判断后行为不能变。"""
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    _patch_org_holding_sync(
        monkeypatch,
        _org_holding_result(
            status="partial", action="repair_fetch_period", accept_status="accept_failed"
        ),
    )
    ctx = PipelineContext(date="20260918", log_path=tmp_path / "run.log")
    try:
        acquire._sync_org_holding(ctx)
    finally:
        ctx.close()
    assert len(ctx.degraded_msgs) == 1, ctx.degraded_msgs
    assert "org_holding" in ctx.degraded_msgs[0]


def test_sync_org_holding_no_degrade_on_completed_status(monkeypatch, tmp_path):
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    _patch_org_holding_sync(
        monkeypatch,
        _org_holding_result(
            status="completed", action="fetch_then_accept", accept_status="accepted"
        ),
    )
    ctx = PipelineContext(date="20260918", log_path=tmp_path / "run.log")
    try:
        acquire._sync_org_holding(ctx)
    finally:
        ctx.close()
    assert ctx.degraded_msgs == []


def test_sync_org_holding_no_degrade_on_skipped_status(monkeypatch, tmp_path):
    """尚无 plannable 期是正常空转 (status="skipped"), 不该被泛化后的判断误伤。"""
    from services.pipeline import acquire
    from services.pipeline.context import PipelineContext

    _patch_org_holding_sync(
        monkeypatch, _org_holding_result(status="skipped", action="none")
    )
    ctx = PipelineContext(date="20260918", log_path=tmp_path / "run.log")
    try:
        acquire._sync_org_holding(ctx)
    finally:
        ctx.close()
    assert ctx.degraded_msgs == []
