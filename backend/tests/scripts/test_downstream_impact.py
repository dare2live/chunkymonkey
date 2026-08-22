"""check_continuity_integrity FAIL 项附下游数据消费方 (血缘 fan-in) 单测.

owner: backend/scripts/check_continuity_integrity.py 的 _downstream_impact +
run_checks 收尾遍历 (2026-08-22 接线) —— 数据质量门 FAIL 时以前从不查下游,
人只知道"某域坏了", 不知道这条坏数据已经流到哪些产物里。

全部 monkeypatch, 不连生产库、不依赖真实 data/lineage/graph.json:
  1. fail 项 + service 类消费方 → downstream.consumer_count > 0, detail 带路径
  2. fail 项 + 只有 config/test 类消费方 → consumer_count == 0, detail 带"无下游"
  3. pass 项不查 (monkeypatch 一个会抛异常的实现证明没调用)
  4. 血缘图加载失败 → fail 项仍正常返回 consumer_count == 0, 门不崩
  5. 消费方超过 8 个 → 只保留 8 条
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from conftest import duck_mem  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_continuity_integrity", REPO / "backend" / "scripts" / "check_continuity_integrity.py")
cci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cci)


# ── fixtures (与 test_check_continuity_integrity.py 同款, 独立复制避免耦合) ──

def _weekdays(start: str, n: int) -> list[str]:
    d = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _mkspec(**kw) -> dict:
    base = {"domain": "dom", "db": "mem", "table": "t", "grain": ["ts_code", "trade_date"],
            "batch_mode": "by_trade_date", "data_start": "20260601", "sla": 1,
            "freshness_date_column": None, "date_param": None, "known_empty_days": set(),
            "gap_tolerance": "none", "freshness_group_col": None, "dead_groups": [],
            "known_group_gaps": {}, "row_dip_tolerance": False}
    base.update(kw)
    return base


def _mktable(conn, days_rows: dict[str, int], table: str = "t"):
    conn.execute(f"CREATE TABLE {table} (ts_code TEXT, trade_date TEXT)")
    rows = []
    for d, n in days_rows.items():
        rows += [(f"c{i}", d) for i in range(n)]
    if rows:
        conn.executemany(f"INSERT INTO {table} VALUES (?, ?)", rows)


def _fail_result(tds: list[str]) -> tuple[list[dict], list[dict]]:
    """produce one fail_interior_gaps result via run_checks(only=calendar_gaps)."""
    hole = tds[5]

    def _conn(_alias):
        c = duck_mem()
        _mktable(c, {d: 3 for d in tds if d != hole})
        return c

    results, failures = cci.run_checks(
        [_mkspec()], _conn, tds, tds[-1], only="calendar_gaps",
    )
    assert results[0]["status"] == "fail_interior_gaps"
    return results, failures


def _pass_result(tds: list[str]):
    def _conn(_alias):
        c = duck_mem()
        _mktable(c, {d: 3 for d in tds})
        return c

    results, failures = cci.run_checks(
        [_mkspec()], _conn, tds, tds[-1], only="calendar_gaps",
    )
    assert results[0]["status"] == "pass"
    return results, failures


# ── 1. fail + service 类消费方 ──────────────────────────────────────────

def test_fail_item_with_service_consumers_gets_downstream(monkeypatch):
    tds = _weekdays("20260601", 15)

    def _fake_downstream(table):
        assert table == "t"
        return {
            "consumer_count": 2,
            "consumers": ["backend/scripts/foo.py", "backend/services/market_pulse.py"],
        }

    monkeypatch.setattr(cci, "_downstream_impact", _fake_downstream)
    results, failures = _fail_result(tds)

    r = results[0]
    assert r["downstream"] == {
        "consumer_count": 2,
        "consumers": ["backend/scripts/foo.py", "backend/services/market_pulse.py"],
    }
    assert "下游数据消费方 2 个" in r["detail"]
    assert "backend/services/market_pulse.py" in r["detail"]
    assert failures and failures[0] is r


# ── 2. fail + 只有 config/test 类消费方 (等效 consumer_count 0) ──────────

def test_fail_item_with_only_config_test_consumers_shows_no_downstream(monkeypatch):
    tds = _weekdays("20260601", 15)

    def _fake_downstream(table):
        return {"consumer_count": 0, "consumers": []}

    monkeypatch.setattr(cci, "_downstream_impact", _fake_downstream)
    results, _failures = _fail_result(tds)

    r = results[0]
    assert r["downstream"] == {"consumer_count": 0, "consumers": []}
    assert "无下游数据消费方" in r["detail"]


def test_downstream_impact_none_treated_same_as_zero_consumers(monkeypatch):
    """_downstream_impact 返回 None (吞异常路径) 与显式 0 消费方同一处理。"""
    tds = _weekdays("20260601", 15)
    monkeypatch.setattr(cci, "_downstream_impact", lambda table: None)
    results, _failures = _fail_result(tds)

    r = results[0]
    assert r["downstream"] == {"consumer_count": 0, "consumers": []}
    assert "无下游数据消费方" in r["detail"]


# ── 3. pass 项不查 ────────────────────────────────────────────────────

def test_pass_item_never_queries_downstream(monkeypatch):
    tds = _weekdays("20260601", 15)
    calls = []

    def _boom(table):
        calls.append(table)
        raise AssertionError("pass 项不该查血缘 — run_checks 必须先按 status 过滤")

    monkeypatch.setattr(cci, "_downstream_impact", _boom)
    results, failures = _pass_result(tds)

    assert calls == []
    assert "downstream" not in results[0]
    assert failures == []


# ── 4. 血缘图加载失败 → 门不崩, fail 项仍正常返回 consumer_count 0 ───────

def test_lineage_graph_load_failure_does_not_crash_gate(monkeypatch):
    def _boom_loader():
        raise RuntimeError("graph.json 格式变了 / 读盘失败 (模拟)")

    monkeypatch.setattr(cci, "_load_lineage_graph_cached", _boom_loader)

    # 单元级: _downstream_impact 本身吞异常返回 None, 不上溯。
    assert cci._downstream_impact("raw_tushare_margin_detail") is None

    # 门级: run_checks 端到端仍正常产出 fail 结果, 门本身不崩, 且
    # downstream 降级为 0 消费方 (而不是让整个 run_checks 抛异常)。
    tds = _weekdays("20260601", 15)
    results, failures = _fail_result(tds)
    r = results[0]
    assert r["status"] == "fail_interior_gaps"
    assert r["downstream"] == {"consumer_count": 0, "consumers": []}
    assert "无下游数据消费方" in r["detail"]
    assert failures and failures[0] is r


def test_lineage_import_failure_swallowed_as_none(monkeypatch):
    """import 链本身断掉 (lineage 包坏了) 也必须吞成 None, 不能上溯到审计门。"""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "services.lineage.query" or name.startswith("services.lineage.query"):
            raise ImportError("simulated broken lineage import")
        return real_import(name, *args, **kwargs)

    # graph load 本身要成功 (返回非 None), 让代码走到 impact 调用那一步再炸,
    # 证明保护的是"调用 impact"这一环, 不只是图加载那一环。
    monkeypatch.setattr(cci, "_load_lineage_graph_cached", lambda: object())
    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert cci._downstream_impact("raw_tushare_margin_detail") is None


# ── 5. 消费方超过 8 个 → 只保留 8 条 ─────────────────────────────────────

def test_more_than_eight_consumers_truncated_to_eight(monkeypatch):
    ten_paths = sorted(f"backend/services/svc_{i:02d}.py" for i in range(10))

    def _fake_impact(_graph, table):
        assert table == "raw_tushare_moneyflow"
        return {
            "consumer_count": 19,
            "consumers_by_type": {
                "service": ten_paths,
                "config": ["backend/config/sync_registry.yaml"],
                "test": ["backend/tests/test_moneyflow.py"],
            },
        }

    monkeypatch.setattr(cci, "_load_lineage_graph_cached", lambda: object())
    monkeypatch.setattr("services.lineage.query.impact", _fake_impact)

    result = cci._downstream_impact("raw_tushare_moneyflow")
    assert result is not None
    assert result["consumer_count"] == 10          # config/test 已剔除, 全量真实消费方数
    assert result["consumers"] == ten_paths[:8]     # 路径列表截断到 8, 字典序
    assert len(result["consumers"]) == 8


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
