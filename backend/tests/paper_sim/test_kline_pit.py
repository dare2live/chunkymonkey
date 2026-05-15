"""Paper Sim — _load_kline_today PIT-safe regression (Codex aa2d79d2 CRITICAL fix).

回归测试: amount_ma20 必须用 prior 20 days only (date < today strict),
不能包含今日 amount. 实盘 T+1 09:25 决策时今日 amount 未知, paper_sim 必须 mirror.

历史 bug: ma20 CTE 用 date <= ? (include today) → 实测 0.46% diff (600519 2025-09-01).
CLAUDE §10 PIT/leakage CRITICAL 不允许折中.
"""
from __future__ import annotations

import duckdb
import pytest


@pytest.fixture
def mock_kline_conn():
    """In-memory market 库 + v_price_kline_qfq 视图 + 5 day 测试数据."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE v_price_kline_qfq (
            code TEXT, date TEXT, freq TEXT, adjust TEXT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE
        )
    """)
    # 600519: 历史 amount 平稳 100M, 今日突然 amount=1B (10× 跳变)
    rows = [
        ('600519', '2025-08-25', 'daily', 'qfq', 1500, 1510, 1490, 1500, 1000, 100_000_000),
        ('600519', '2025-08-26', 'daily', 'qfq', 1500, 1510, 1490, 1500, 1000, 100_000_000),
        ('600519', '2025-08-27', 'daily', 'qfq', 1500, 1510, 1490, 1500, 1000, 100_000_000),
        ('600519', '2025-08-28', 'daily', 'qfq', 1500, 1510, 1490, 1500, 1000, 100_000_000),
        ('600519', '2025-08-29', 'daily', 'qfq', 1500, 1510, 1490, 1500, 1000, 100_000_000),
        # 今日突然爆量 — 实盘 09:25 决策时这数据未知
        ('600519', '2025-09-01', 'daily', 'qfq', 1500, 1550, 1490, 1540, 10000, 1_000_000_000),
    ]
    conn.executemany(
        "INSERT INTO v_price_kline_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return conn


def test_amount_ma20_excludes_today(mock_kline_conn):
    """PIT-safe: 2025-09-01 决策时 ma20 不含今日 1B 爆量, 应 ≈ 100M (prior 5 day 平均)."""
    from services.paper_sim.driver import _load_kline_today
    k = _load_kline_today(mock_kline_conn, ['600519'], '2025-09-01')
    assert '600519' in k
    ma20 = k['600519']['amount_ma20']
    # 期望: 仅 prior 5 day 平均 = 100M, 不含今日 1B
    assert ma20 == 100_000_000, f"ma20 should exclude today, got {ma20}"


def test_amount_ma20_returns_none_when_no_prior(mock_kline_conn):
    """没 prior 日的 stock → ma20 = None."""
    from services.paper_sim.driver import _load_kline_today
    # Query 2025-08-25 (该 stock 第一天 in fixture) → 没 prior 日
    k = _load_kline_today(mock_kline_conn, ['600519'], '2025-08-25')
    # ma20 应为 None (LEFT JOIN 缺 prior row)
    assert k['600519']['amount_ma20'] is None or k['600519']['amount_ma20'] == 0


def test_pre_close_excludes_today(mock_kline_conn):
    """PIT-safe: pre_close 应是前一交易日 close (2025-08-29 = 1500), 不是今日 1540."""
    from services.paper_sim.driver import _load_kline_today
    k = _load_kline_today(mock_kline_conn, ['600519'], '2025-09-01')
    pre_close = k['600519']['pre_close']
    assert pre_close == 1500, f"pre_close should be prior day close, got {pre_close}"


def test_today_data_returns_correct(mock_kline_conn):
    """今日 OHLCV 字段正确传出 (区别 ma20/pre_close PIT)."""
    from services.paper_sim.driver import _load_kline_today
    k = _load_kline_today(mock_kline_conn, ['600519'], '2025-09-01')
    assert k['600519']['close'] == 1540
    assert k['600519']['high'] == 1550
    assert k['600519']['amount'] == 1_000_000_000
    assert k['600519']['volume'] == 10000
