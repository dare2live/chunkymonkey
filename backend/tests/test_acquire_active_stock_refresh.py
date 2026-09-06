"""dim_active_a_stock 日常刷新契约单测 (2026-07-06 全面数据审计根因根治).

背景: services.security_master.refresh_active_a_stock_master 一直存在且正确 (raw_tushare_
stock_basic → reference.dim_active_a_stock 全量重写), 但从未被任何 daily_update 步骤调用过。
25 个消费方读的这张 universe 身份真相源表只能靠人工手动跑脚本刷新, 实测发现已静默 stale
8 天 (dim_active_a_stock.updated_at 停在 8 天前)。

本门锁定: (1) acquire._refresh_active_a_stock_master 正确调用底层 writer 并打印行数;
(2) run_acquire 主流程里这一步真的被调用 (不是孤儿函数, 与当年 dim_trading_calendar 那次
"写函数存在但从未被调用"同型 bug 不能重演); (3) 写函数失败时 degraded 续跑不炸全链
(与 _build_trading_calendar 同一降级语义)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.pipeline import acquire
from services.pipeline.context import PipelineContext

# 2026-09-06: 原先这里手搓了一个 _FakeCtx。它必然漂移 —— PipelineContext 后来长出
# delta_manifest, 假件没跟, 于是本文件长期红着; 而它不在 ci_pytest_surface.yaml 里,
# 所以没人看见。改用**真 PipelineContext**(全默认值 dataclass, 直接可造),
# 从此它不可能再落后于被测类。


def test_refresh_active_a_stock_master_calls_writer_and_reports_rows(monkeypatch, capsys, tmp_path):
    calls = []

    def _fake_refresh(conn):
        calls.append(conn)
        return 5211

    monkeypatch.setattr(
        "services.security_master.refresh_active_a_stock_master", _fake_refresh
    )
    # date 必填(刻意: 由 run.py 注入不取 wall-clock, 防跨午夜); log_path 隔离到 tmp
    ctx = PipelineContext(date="20260911", log_path=tmp_path / "run.log")
    # 前后快照读 reference 库; 库不在时函数自己 try/except 降级成 ctx.log ——
    # 所以本测试在没有 data/ 的全新克隆里也必须能跑 (这正是它此前红的环境)。
    acquire._refresh_active_a_stock_master(ctx)
    assert calls == [None], "writer 应被调用一次 (conn 参数已不再被内部使用, 传 None 即可)"
    assert "5211" in capsys.readouterr().out
    # 传感器是 observer-only: 读不到就是 None, **不许伪造成空集合**(红线 3)
    assert ctx.dim_active_codes_before is None or isinstance(ctx.dim_active_codes_before, set)
    assert ctx.dim_active_codes_after is None or isinstance(ctx.dim_active_codes_after, set)


def test_run_acquire_wires_active_stock_refresh_step(monkeypatch, tmp_path):
    """端到端: run_acquire 的步骤序列里必须真的包含 dim_active_a_stock 刷新这一步
    (不能只是定义了函数却没接进主流程——这正是本次要根治的 bug 模式)。"""
    calls = []
    monkeypatch.setattr(acquire, "_sync_holders_aif10", lambda ctx: calls.append("holders_aif10"))
    monkeypatch.setattr(acquire, "_sync_qfii", lambda: calls.append("qfii"))
    # 带 ctx 调 (acquire.py:83); 少参数会被 ctx.step 降级吞掉, 桩静默失效
    monkeypatch.setattr(acquire, "_sync_org_holding", lambda _c: calls.append("org_holding"))
    monkeypatch.setattr(acquire, "_sync_registry_drain", lambda ctx: calls.append("drain"))
    monkeypatch.setattr(
        acquire,
        "_sync_formal_on_demand_security_days",
        lambda ctx: calls.append("formal") or [],
    )
    monkeypatch.setattr(acquire, "_build_trading_calendar", lambda: calls.append("calendar"))
    # acquire.py:124 带 ctx 调
    monkeypatch.setattr(acquire, "_refresh_active_a_stock_master", lambda _c: calls.append("active_stock"))
    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_pipeline_sync_ready",
        lambda ctx: None,
    )
    monkeypatch.setattr(
        "services.pipeline.preflight.ensure_tushare_authorized",
        lambda ctx: calls.append("auth"),
    )

    # 这两步会去开 reference / tushare_raw 库; 本测试测的是**步骤顺序**不是它们,
    # 不打桩就会在没有 data/ 的环境里炸 (同 test_pipeline 的处理)。
    monkeypatch.setattr(
        "services.pipeline.margin_catchup_acquire.run_margin_bounded_catchup", lambda _c: []
    )
    monkeypatch.setattr(
        "services.pipeline.frozen_domain_observe.observe_frozen_on_demand_domains", lambda _c: []
    )

    acquire.run_acquire(PipelineContext(date="20260911", log_path=tmp_path / "run.log"))
    assert calls[0] == "auth", "独立 acquire 必须先过授权硬门"
    assert "active_stock" in calls, "dim_active_a_stock 刷新步骤必须真的被 run_acquire 调用"
    # Published drain before formal on_demand (structural sibling isolation).
    assert calls.index("drain") < calls.index("formal"), calls
    # 顺序断言: 紧随 calendar 之后 (raw stock_basic 已被 drain 同步完, 立即重建派生表)
    assert calls.index("active_stock") == calls.index("calendar") + 1
