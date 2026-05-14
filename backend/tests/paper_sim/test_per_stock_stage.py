"""Paper Sim v2 — Phase ψ.γ.2 per-stock × stage 参数加载单测.

验证 _load_per_stock_stage_optimal 返回结构 + 空输入安全 + min_n_traded 过滤.

Integration test (真 DB query) 等 Optuna 跑完不占锁后跑.
"""
from __future__ import annotations

from services.paper_sim.selector import _load_per_stock_stage_optimal


def test_empty_input_returns_empty_dict():
    """空 pairs 不查 DB, 返回 {}."""
    # conn=None 也不报错 (因为短路)
    out = _load_per_stock_stage_optimal(conn=None, stock_stage_pairs=[])
    assert out == {}


def test_pairs_with_none_stage_filtered_out():
    """stage=None / '' 的 pairs 应被过滤 (无法 JOIN PK)."""
    out = _load_per_stock_stage_optimal(conn=None, stock_stage_pairs=[
        ("600000", None),    # type: ignore[arg-type]
        ("000001", ""),
    ])
    assert out == {}


class _MockConn:
    """模拟 DuckConn — 单 stage 一行 best params."""

    def __init__(self, mock_rows: dict[str, list[tuple]]):
        self.mock_rows = mock_rows
        self.last_stage: str = ""

    def execute(self, sql: str, params: list) -> "_MockConn":
        # params: [stage, ...stock_codes..., min_n_traded]
        self.last_stage = params[0]
        return self

    def fetchall(self) -> list[tuple]:
        return self.mock_rows.get(self.last_stage, [])


def test_mock_conn_per_stage_rows():
    """模拟 DB 返回: stage='1' 有 600000, stage='2' 有 000001."""
    mock = _MockConn({
        "1": [("600000", "reversal_1m_deep", 15, -0.08, 0.25, 0.06)],
        "2": [("000001", "macd_golden", 30, -0.12, 0.30, 0.08)],
    })
    pairs = [("600000", "1"), ("000001", "2"), ("000002", "1")]
    out = _load_per_stock_stage_optimal(mock, pairs, min_n_traded=5)
    assert ("600000", "1") in out
    assert ("000001", "2") in out
    assert ("000002", "1") not in out, "未在 mock_rows 中 → 不应返回"
    assert out[("600000", "1")]["hp"] == 15
    assert out[("600000", "1")]["stop_pct"] == -0.08
    assert out[("600000", "1")]["target_pct"] == 0.25
    assert out[("600000", "1")]["trailing_pct"] == 0.06
    assert out[("600000", "1")]["source_formula"] == "reversal_1m_deep"


def test_mock_conn_db_exception_returns_empty_for_that_stage():
    """单 stage query exception → 该 stage 跳过, 其他 stage 仍处理."""

    class _ErrorConn:
        def execute(self, sql, params):
            stage = params[0]
            if stage == "bad":
                raise Exception("simulated DB error")
            self._stage = stage
            return self

        def fetchall(self):
            return [("600000", "f1", 10, -0.05, 0.15, 0.04)] if self._stage == "1" else []

    out = _load_per_stock_stage_optimal(
        _ErrorConn(),
        [("600000", "1"), ("000001", "bad")],
        min_n_traded=5,
    )
    # stage="1" 成功
    assert ("600000", "1") in out
    # stage="bad" 抛错 → 该组没结果, 但不影响其他
    assert ("000001", "bad") not in out
