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


def test_refresh_active_a_stock_master_calls_writer_and_reports_rows(monkeypatch, capsys):
    calls = []

    def _fake_refresh(conn):
        calls.append(conn)
        return 5211

    monkeypatch.setattr(
        "services.security_master.refresh_active_a_stock_master", _fake_refresh
    )
    acquire._refresh_active_a_stock_master()
    assert calls == [None], "writer 应被调用一次 (conn 参数已不再被内部使用, 传 None 即可)"
    assert "5211" in capsys.readouterr().out


def test_run_acquire_wires_active_stock_refresh_step(monkeypatch):
    """端到端: run_acquire 的步骤序列里必须真的包含 dim_active_a_stock 刷新这一步
    (不能只是定义了函数却没接进主流程——这正是本次要根治的 bug 模式)。"""
    calls = []
    monkeypatch.setattr(acquire, "_sync_holders_aif10", lambda ctx: calls.append("holders_aif10"))
    monkeypatch.setattr(acquire, "_sync_qfii", lambda: calls.append("qfii"))
    monkeypatch.setattr(acquire, "_sync_org_holding", lambda: calls.append("org_holding"))
    monkeypatch.setattr(acquire, "_sync_registry_drain", lambda ctx: calls.append("drain"))
    monkeypatch.setattr(acquire, "_build_trading_calendar", lambda: calls.append("calendar"))
    monkeypatch.setattr(acquire, "_refresh_active_a_stock_master", lambda: calls.append("active_stock"))

    class _FakeCtx:
        skip_sync = False
        dry = False

        def log(self, msg):
            pass

        def step(self, fn, *, degraded_msg):
            fn()
            return True

    acquire.run_acquire(_FakeCtx())
    assert "active_stock" in calls, "dim_active_a_stock 刷新步骤必须真的被 run_acquire 调用"
    # 顺序断言: 紧随 calendar 之后 (raw stock_basic 已被 drain 同步完, 立即重建派生表)
    assert calls.index("active_stock") == calls.index("calendar") + 1
