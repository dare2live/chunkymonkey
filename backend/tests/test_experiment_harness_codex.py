"""判断法典工具单测 (2026-06-15 P0 制度先行) — R1 对称门 + C-WinReturn 联合门 + C-R1 转正强制。

red->green 实证: 删 tradability_verdict 的 IC_POSITIVE_BUT_UNTRADABLE 分支 / record_verdict 的 guard,
本测试必转红 (反例守门真触发, 非装饰). owner=docs/strategy_validation_contract.md 判断法典。
"""
from __future__ import annotations

import pytest

from services.experiment_harness import tradability_verdict, kpi_verdict, block_bootstrap_return_null
from services import experiment_store as es


# ---- C-R1 对称可交易性门 ----
def test_tradability_ic_positive_but_untradable():
    """Phase B 铁证: IC 真 (>0) 但含成本绝对收益<=0 -> 不可交易, 不许凭 IC 转正。"""
    v = tradability_verdict(0.156, -0.028)   # Stage1.5 全市场实测
    assert v["verdict"] == "IC_POSITIVE_BUT_UNTRADABLE"


def test_tradability_real_edge_tradable():
    v = tradability_verdict(0.10, 0.35)
    assert v["verdict"] == "TRADABLE"


def test_tradability_no_edge_when_ic_nonpositive():
    assert tradability_verdict(-0.01, 0.5)["verdict"] == "NO_EDGE"


def test_tradability_unknown_without_backtest():
    """缺含成本 backtest -> UNKNOWN (IC 单独不构成 edge 证据, measured not estimated)。"""
    assert tradability_verdict(0.20, None)["verdict"] == "UNKNOWN"


# ---- C-WinReturn 联合验收门 (胜率诊断量, 收益+max_dd 目标量) ----
def test_kpi_high_winrate_but_negative_return_fails():
    """高月胜率单独不构成放行: 年化为负 -> KPI_FAIL。"""
    r = kpi_verdict({"annual_return": -0.028, "max_drawdown": -0.44, "monthly_win_rate": 0.62})
    assert r["verdict"] == "KPI_FAIL"
    assert r["passes"]["monthly_win_rate"] is True      # 胜率达标
    assert r["passes"]["annual_return"] is False        # 但目标量不达标 -> 整体 FAIL


def test_kpi_all_pass_with_positive_expectancy():
    r = kpi_verdict({"annual_return": 0.35, "max_drawdown": -0.15, "monthly_win_rate": 0.6,
                     "win_rate": 0.45, "payoff_ratio": 2.0})
    assert r["verdict"] == "KPI_PASS"
    assert r["passes"]["positive_expectancy"] is True   # 0.45*2 - 0.55 = 0.35 > 0


def test_kpi_low_winrate_high_payoff_positive_expectancy():
    """40% 胜率 x 3:1 盈亏比 -> 正期望 (胜率脱离盈亏比无意义的反证)。"""
    r = kpi_verdict({"annual_return": 0.40, "max_drawdown": -0.18, "monthly_win_rate": 0.56,
                     "win_rate": 0.40, "payoff_ratio": 3.0})
    assert r["diagnostics"]["expectancy"] > 0           # 0.4*3 - 0.6 = 0.6
    assert r["verdict"] == "KPI_PASS"


# ---- C-R1 转正强制 (confirmed_by_owner=1 必须看过钱) ----
class _FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, *a, **k):
        self.calls.append(a)


def test_record_verdict_promotion_blocks_without_money_evidence():
    """缺陷 N3 / 自欺死: confirmed_by_owner=1 无含成本绝对收益证据 -> raise (不许纯凭 IC 转正)。"""
    with pytest.raises(ValueError, match="C-R1"):
        es.record_verdict(_FakeConn(), run_id="t", family="f", verdict="REAL_EDGE",
                          judges={"oos_rank_ic": 0.2}, confirmed_by_owner=1)


def test_record_verdict_promotion_allowed_with_money_evidence():
    """带含成本绝对收益证据 -> 过 guard, 正常写入。"""
    conn = _FakeConn()
    es.record_verdict(conn, run_id="t", family="f", verdict="REAL_EDGE",
                      judges={"kpi_verdict": {"verdict": "KPI_PASS"}, "tradability": "TRADABLE"},
                      confirmed_by_owner=1)
    assert len(conn.calls) == 1                          # 写入执行


def test_record_verdict_non_promotion_no_money_required():
    """非转正 (confirmed_by_owner=0) 不强制 money 证据 (只有盖章才严)。"""
    conn = _FakeConn()
    es.record_verdict(conn, run_id="t", family="f", verdict="STAT_EDGE_CONFIRMED",
                      judges={"oos_rank_ic": 0.2}, confirmed_by_owner=0)
    assert len(conn.calls) == 1


# ---- 绝对收益 null (R1 armory, N1): block bootstrap ----
def test_block_bootstrap_null_positive_series_robust():
    """稳健正收益序列 -> p_le_zero 低 (累计收益稳健 > 0)。"""
    r = [0.05, 0.03, 0.04, 0.06, 0.02, 0.05, 0.03, 0.04]
    out = block_bootstrap_return_null(r, n_boot=500, block=2)
    assert out["p_le_zero"] < 0.1
    assert out["boot_mean_total"] > 0


def test_block_bootstrap_null_negative_series_flagged():
    """负收益序列 -> p_le_zero 高 (绝对收益不稳健正, R1 不许凭 IC 转正)。"""
    r = [-0.04, -0.02, -0.05, -0.03, -0.06, -0.02, -0.04, -0.03]
    out = block_bootstrap_return_null(r, n_boot=500, block=2)
    assert out["p_le_zero"] > 0.9


def test_block_bootstrap_null_small_sample_unknown():
    assert block_bootstrap_return_null([0.1, 0.2])["p_le_zero"] is None
