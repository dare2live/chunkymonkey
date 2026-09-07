"""Knife 1b: margin v3 bounded calendar catchup (SSE+SZSE only)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.data_sources import sync_runner as sr
from services.data_sources.margin_catchup import (
    MarginCatchupError,
    land_then_accept_margin_day,
)
from services.pipeline.frozen_domain_observe import margin_hard_gate_required


def test_live_hard_gate_stays_off_without_product_blocking():
    assert margin_hard_gate_required() is False


def test_hard_gate_requires_explicit_product_blocking(monkeypatch):
    registry = sr.load_registry()
    spec = dict(sr.domain_spec(registry, "margin"))
    spec["product_blocking"] = True
    spec["execution_policy"] = {"mode": "enabled", "reason": "bounded_calendar_catchup"}
    monkeypatch.setattr(
        sr,
        "load_registry",
        lambda: {"defaults": registry.get("defaults", {}), "domains": {"margin": spec}},
    )
    monkeypatch.setattr(sr, "domain_spec", lambda reg, domain: reg["domains"][domain])
    assert margin_hard_gate_required(registry={"domains": {"margin": spec}}) is True


def test_catchup_window_refuses_pre_coverage_start():
    with pytest.raises(sr.SyncWindowError, match="coverage_start"):
        sr._require_authorized_margin_catchup_window(
            backfill=False,
            resume=False,
            start="20260715",
            end="20260717",
            max_dates=None,
            eligible_end="20260722",
            coverage_start="20260717",
        )


def test_catchup_window_refuses_beyond_eligible_end():
    with pytest.raises(sr.SyncWindowError, match="eligible_end"):
        sr._require_authorized_margin_catchup_window(
            backfill=False,
            resume=False,
            start="20260717",
            end="20260723",
            max_dates=None,
            eligible_end="20260722",
            coverage_start="20260717",
        )


def test_land_then_accept_requires_v3(monkeypatch):
    spec = sr.domain_spec(sr.load_registry(), "margin")
    bad = dict(spec)
    bad["dataset_contract"] = {
        **spec["dataset_contract"],
        "contract_version": "2",
    }
    with pytest.raises(MarginCatchupError, match="contract_version>=3"):
        land_then_accept_margin_day(
            object(),
            object(),
            bad,
            "20260717",
            fetch_logical_batch=lambda *_a, **_k: [],
        )


def test_drain_margin_is_inapplicable(monkeypatch):
    """守: drain_domain 对 margin 有专门分支——它只能走 bounded_calendar_catchup
    落库, 永远不能走 legacy 日历缺口重放 (sync_runner.py ~3930, reason=
    accepted_partition_is_bounded_calendar_catchup_only)。

    2026-09-07 tushare_sunset freeze 把 margin 全局 execution_policy 转成
    disabled 后, drain_domain 顶部的 `_require_execution_enabled(spec)` 会先
    抛 ExecutionPolicyError, 这条分支现实中够不着——但那是更强的保证(禁用的域
    连 drain 入口都进不去, 不只是"进去了也走不到 legacy 重放"), 不代表这条分支
    是死代码: 它是当 margin 将来被重新 enable(例如换非 tushare 源接回两融)时,
    唯一防止 drain 把它当成普通 by_trade_date 域重放的守卫。用注入的"假装
    enabled"策略单独测这条分支本身, 不依赖 margin 当前在 live registry 里是不是
    被冻结。
    """
    monkeypatch.setattr(
        sr,
        "execution_policy_for_spec",
        lambda spec: sr.DomainExecutionPolicy(
            mode="enabled", reason="test_forces_enabled"
        ),
    )
    result = sr.drain_domain("margin")
    assert result["status"] == "drain_inapplicable"
    assert "bounded_calendar_catchup" in result["reason"]


def test_acquire_margin_catchup_plans_gap(monkeypatch, tmp_path):
    """守: run_margin_bounded_catchup 的日历缺口规划数学——没有 v3 accepted 证据时
    从 coverage_start 排到 eligible_end, 调 run_domain 传对 start/end/trigger_mode。

    2026-09-07 tushare_sunset freeze 前, run_margin_bounded_catchup 顶部本来就有
    `if policy.mode != "enabled": return []` 这道守卫(Knife 1b 从建这个模块起就有,
    见 commit 0f5af7e80, 不是这次新加的)——现在 margin 全局 disabled, 这道守卫会
    在缺口数学之前就先短路返回 []。这道守卫本身没问题(生产行为符合预期: 冻结时
    acquire 每次都静默跳过, 不报错不重试), 但它会挡住这条测试真正想验证的东西:
    "如果 margin 被启用, 缺口规划数学算得对不对"。这个逻辑是 margin 专属的
    (硬编码 domain="margin"/dataset_contract), 换不了"未冻结的域", 所以改用注入
    的 execution_policy(假装 enabled), 与生产代码那道守卫解耦, 不依赖 live
    registry 当前是不是被冻结——冻结是可逆决策(tushare 恢复/换源都可能重新
    enable), 这条缺口数学不能因为暂时冻结就没人测。
    """
    from services.pipeline.context import PipelineContext
    from services.pipeline import margin_catchup_acquire

    run_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            # No v3 accepted / canonical yet → plan from coverage_start.
            return SimpleNamespace(fetchone=lambda: (None,))

        def close(self):
            pass

    monkeypatch.setattr(
        sr,
        "execution_policy_for_spec",
        lambda spec: sr.DomainExecutionPolicy(
            mode="enabled", reason="test_forces_enabled"
        ),
    )
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end: [
            d
            for d in (
                "20260716",
                "20260717",
                "20260720",
                "20260721",
                "20260722",
            )
            if start <= d <= end
        ],
    )
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: run_calls.append((domain, kwargs))
        or {
            "status": "ok",
            "rows": 2,
            "last_date": "20260722",
            "failed_batches": 0,
            "contract_version": "3",
        },
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = margin_catchup_acquire.run_margin_bounded_catchup(ctx)
    finally:
        ctx.close()

    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "land_then_accept"
    assert outcomes[0]["start"] == "20260717"
    assert outcomes[0]["eligible_end"] == "20260722"
    assert len(run_calls) == 1
    assert run_calls[0][0] == "margin"
    assert run_calls[0][1]["start"] == "20260717"
    assert run_calls[0][1]["end"] == "20260722"
    assert run_calls[0][1]["trigger_mode"] == "manual"


def test_acquire_margin_catchup_skips_when_current(monkeypatch, tmp_path):
    """守: 已经追平 eligible_end 时, run_margin_bounded_catchup 只 skip + 重投影
    ops watermark, 绝不调 run_domain。同 test_acquire_margin_catchup_plans_gap
    的形状——production 里 `policy.mode != "enabled": return []` 那道守卫(Knife
    1b 从建库起就有)现在会在这条 skip 逻辑之前先短路, 用注入的 enabled policy
    绕开它, 单独验证"已追平时该怎么收尾"这段逻辑本身, 不依赖 margin 当前是否
    被 tushare_sunset 冻结。
    """
    from services.pipeline.context import PipelineContext
    from services.pipeline import margin_catchup_acquire

    run_calls = []
    project_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            # v3 accepted already at eligible_end → skip, do not call run_domain.
            return SimpleNamespace(fetchone=lambda: ("20260722",))

        def close(self):
            pass

    monkeypatch.setattr(
        sr,
        "execution_policy_for_spec",
        lambda spec: sr.DomainExecutionPolicy(
            mode="enabled", reason="test_forces_enabled"
        ),
    )
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end: [
            d
            for d in ("20260720", "20260721", "20260722")
            if start <= d <= end
        ],
    )
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: run_calls.append((domain, kwargs))
        or {"status": "ok"},
    )
    monkeypatch.setattr(
        sr,
        "_project_margin_accepted_ops_watermark",
        lambda *a, **k: project_calls.append(k) or None,
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = margin_catchup_acquire.run_margin_bounded_catchup(ctx)
    finally:
        ctx.close()

    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "latest_eligible_already_present"
    assert outcomes[0]["local_max"] == "20260722"
    assert outcomes[0]["ops_watermark_projected"] is True
    assert run_calls == []
    assert project_calls and project_calls[0]["through"] == "20260722"


def test_acquire_margin_catchup_schedules_partial_gap(monkeypatch, tmp_path):
    """Stale v3 local_max with later eligible_end → schedule once from next day.

    同上两条形状: production 的 `policy.mode != "enabled": return []` 守卫
    (Knife 1b 从建库起就有, 不是本轮新加)会在这条部分缺口调度逻辑之前先短路,
    用注入的 enabled policy 绕开, 单独验证调度数学, 不依赖 margin 当前是否
    被 tushare_sunset 冻结。
    """
    from services.pipeline.context import PipelineContext
    from services.pipeline import margin_catchup_acquire

    run_calls = []

    class _Conn:
        def execute(self, sql, *_a, **_k):
            return SimpleNamespace(fetchone=lambda: ("20260717",))

        def close(self):
            pass

    monkeypatch.setattr(
        sr,
        "execution_policy_for_spec",
        lambda spec: sr.DomainExecutionPolicy(
            mode="enabled", reason="test_forces_enabled"
        ),
    )
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda *_a, **_k: sr.DomainEligibility(
            "20260722", True, "next_trading_session_published"
        ),
    )
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda start, end: [
            d
            for d in (
                "20260717",
                "20260720",
                "20260721",
                "20260722",
            )
            if start <= d <= end
        ],
    )
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **kwargs: run_calls.append((domain, kwargs))
        or {
            "status": "ok",
            "rows": 6,
            "last_date": "20260722",
            "failed_batches": 0,
            "contract_version": "3",
        },
    )
    monkeypatch.setattr("services.duck_adapter.connect", lambda *_a, **_k: _Conn())

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "run.log")
    try:
        outcomes = margin_catchup_acquire.run_margin_bounded_catchup(ctx)
    finally:
        ctx.close()

    assert outcomes[0]["action"] == "land_then_accept"
    assert outcomes[0]["start"] == "20260720"
    assert run_calls[0][1]["start"] == "20260720"
    assert run_calls[0][1]["end"] == "20260722"


def test_run_acquire_wires_margin_catchup(monkeypatch, tmp_path):
    """Click-update acquire path must invoke margin planner (not only one-shot CLI)."""
    from services.pipeline import acquire as acquire_mod
    from services.pipeline import context as context_mod
    from services.pipeline import preflight as preflight_mod
    from services.pipeline.context import PipelineContext

    called = {"n": 0}

    # Isolate real /tmp alert flag — prior monkeypatch arity bugs polluted doctor WARN.
    monkeypatch.setattr(context_mod, "DEGRADED_FLAG", tmp_path / "degraded.flag")

    monkeypatch.setattr(preflight_mod, "ensure_pipeline_sync_ready", lambda ctx: None)
    monkeypatch.setattr(preflight_mod, "ensure_tushare_authorized", lambda ctx: None)
    monkeypatch.setattr(acquire_mod, "_sync_holders_aif10", lambda ctx: None)
    # Production `_sync_qfii` / `_build_trading_calendar` take no args (ctx.step(fn)).
    monkeypatch.setattr(acquire_mod, "_sync_qfii", lambda: None)
    monkeypatch.setattr(acquire_mod, "_sync_org_holding", lambda ctx: None)
    monkeypatch.setattr(acquire_mod, "_sync_registry_drain", lambda ctx: [])
    monkeypatch.setattr(
        acquire_mod, "_sync_formal_on_demand_security_days", lambda ctx: []
    )
    monkeypatch.setattr(acquire_mod, "_build_trading_calendar", lambda: None)
    monkeypatch.setattr(
        acquire_mod, "_refresh_active_a_stock_master", lambda ctx: None
    )
    monkeypatch.setattr(
        acquire_mod,
        "_finalize_acquire_delta",
        lambda ctx, drain_results=None, formal_outcomes=None: None,
    )

    import services.pipeline.margin_catchup_acquire as mca
    import services.pipeline.frozen_domain_observe as fdo

    def _catchup(ctx):
        called["n"] += 1
        return [
            {
                "domain": "margin",
                "action": "skip",
                "reason": "latest_eligible_already_present",
            }
        ]

    monkeypatch.setattr(mca, "run_margin_bounded_catchup", _catchup)
    monkeypatch.setattr(fdo, "observe_frozen_on_demand_domains", lambda ctx: [])

    ctx = PipelineContext(date="20260723", log_path=tmp_path / "acq.log", dry=False)
    try:
        acquire_mod.run_acquire(ctx)
    finally:
        ctx.close()

    assert called["n"] == 1
    assert not (tmp_path / "degraded.flag").exists()
