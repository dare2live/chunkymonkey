"""paper_portfolio 单测 — 入池/出池/mark/KPI 数值证伪门 (mock SERVE 取价 + 内存库)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import paper_portfolio as pp


@pytest.fixture
def mem_conn(monkeypatch):
    c = duck_mem()
    pp.ensure_tables(c)
    # mock 地基依赖: 交易日/SERVE 取价 (单测不碰真库)
    monkeypatch.setattr(pp, "_latest_trade_date", lambda: "2026-07-01")
    prices = {"600519": 100.0, "000001": 20.0}
    monkeypatch.setattr(pp, "_close_price", lambda code, as_of, conn=None: prices.get(code))
    monkeypatch.setattr(pp, "_bench_close", lambda as_of: 4000.0)
    yield c
    c.close()


def test_add_position_by_amount_rounds_to_lot(mem_conn):
    """按金额入池: 100万×10% = 10万 @100 = 1000股 (整手); 佣金万2.5。"""
    r = pp.add_position("600519", amount=100_000, strategy_tag="inst_follow", conn=mem_conn)
    assert r["shares"] == 1000
    assert r["entry_price"] == 100.0
    assert r["fee"] == pytest.approx(1000 * 100 * 0.00025)


def test_cash_guard_rejects_over_budget(mem_conn):
    """现金护栏: 超过 init_cash 拒绝。"""
    pp.add_position("600519", amount=990_000, conn=mem_conn)
    with pytest.raises(ValueError, match="现金不足"):
        pp.add_position("000001", amount=500_000, conn=mem_conn)


def test_close_and_kpi_win_rate(mem_conn, monkeypatch):
    """出池含印花税; KPI 胜率=盈利平仓占比。"""
    r1 = pp.add_position("600519", shares=100, conn=mem_conn)  # entry 100
    # 涨到 120 平仓 → 盈利
    monkeypatch.setattr(pp, "_close_price", lambda code, as_of, conn=None: 120.0)
    out = pp.close_position(r1["position_id"], conn=mem_conn)
    assert out["ret_pct"] == pytest.approx(20.0)
    # pnl = 100×(120−100) − 买佣 100×100×2.5bp − 卖(佣+税) 100×120×(2.5bp+10bp)
    assert out["pnl"] == pytest.approx(100 * 20 - 100 * 100 * 0.00025 - 100 * 120 * 0.00125)
    kpi = pp.portfolio_kpi(conn=mem_conn)
    assert kpi["n_closed"] == 1 and kpi["win_rate"] == 1.0


def test_mark_to_market_nav_and_excess(mem_conn, monkeypatch):
    """mark: nav = cash + 持仓市值; 超额 = 组合收益 − HS300 收益。"""
    pp.add_position("600519", shares=1000, conn=mem_conn)   # 10万持仓
    snap = pp.mark_to_market(as_of="2026-07-01", conn=mem_conn)
    assert snap["nav"] == pytest.approx(1_000_000 - 1000 * 100 * 0.00025, abs=1)  # 现金+市值≈init−佣金
    # 次日: 股价 110, HS300 4000→4040 (+1%)
    monkeypatch.setattr(pp, "_close_price", lambda code, as_of, conn=None: 110.0)
    monkeypatch.setattr(pp, "_bench_close", lambda as_of: 4040.0)
    pp.mark_to_market(as_of="2026-07-02", conn=mem_conn)
    kpi = pp.portfolio_kpi(conn=mem_conn)
    # 组合: 持仓 1000×(110−100)=+1万 → ret≈+1% ; bench +1% → excess≈0 (差在佣金)
    assert kpi["ret_cum"] == pytest.approx(0.0097, abs=0.002)
    assert kpi["bench_ret_cum"] == pytest.approx(0.01, abs=1e-6)
    assert abs(kpi["excess_cum"]) < 0.005


def test_mark_idempotent_same_day(mem_conn):
    """同日重跑 mark 覆盖非重复 (幂等)。"""
    pp.mark_to_market(as_of="2026-07-01", conn=mem_conn)
    pp.mark_to_market(as_of="2026-07-01", conn=mem_conn)
    n = mem_conn.execute("SELECT COUNT(*) FROM paper_nav_daily WHERE nav_date='2026-07-01'").fetchone()[0]
    assert n == 1
