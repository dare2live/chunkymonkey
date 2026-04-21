"""Phase II 子模块单元测试：Cox / stock_character / institution_style / HMM."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from services.sef.schema import migrate_phase1
from services.sef import cox_survival, stock_character, institution_style, hmm_regime


@pytest.fixture
def base_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE fact_institution_event(
            institution_id TEXT NOT NULL, stock_code TEXT NOT NULL,
            report_date TEXT NOT NULL, notice_date TEXT, event_type TEXT NOT NULL,
            PRIMARY KEY(institution_id, stock_code, report_date)
        );
        CREATE TABLE research_holding_chains(
            institution_id TEXT, stock_code TEXT, chain_id INTEGER,
            PRIMARY KEY(institution_id, stock_code, chain_id)
        );
        CREATE TABLE dim_active_a_stock(stock_code TEXT PRIMARY KEY, stock_name TEXT);
        CREATE TABLE dim_stock_tdx_industry(stock_code TEXT PRIMARY KEY, tdx_l1 TEXT, tdx_l2 TEXT);
        """
    )
    migrate_phase1(c)
    yield c
    c.close()


@pytest.fixture
def mkt_conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE price_kline(code TEXT, date TEXT, freq TEXT, adjust TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL)"
    )
    yield c
    c.close()


def _seed_alpha_truth(conn, rows):
    for r in rows:
        conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth(
                institution_id, stock_code, research_chain_id,
                entry_date, eval_date, status, chain_days, tb_label,
                chain_follow_pnl, chain_follow_max_dd, chain_inst_pnl,
                industry_l1, industry_l2
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            r,
        )
    conn.commit()


# ============================================================================
# Layer 1 · Cox Survival
# ============================================================================


def test_cox_aggregate_group_median():
    group = [
        {"chain_follow_pnl": 10, "chain_follow_max_dd": -5},
        {"chain_follow_pnl": 20, "chain_follow_max_dd": -2},
        {"chain_follow_pnl": -5, "chain_follow_max_dd": -15},
        {"chain_follow_pnl": 15, "chain_follow_max_dd": -3},
        {"chain_follow_pnl": 25, "chain_follow_max_dd": -1},
    ]
    s = cox_survival._aggregate_group(group)
    assert s["sample_count"] == 5
    assert s["alpha_median"] == pytest.approx(15)
    assert s["max_dd_median"] == pytest.approx(-3)
    assert s["sharpe"] is not None


def test_cox_assign_expert_level():
    assert cox_survival._assign_expert_level(None, 3, None) == 0
    assert cox_survival._assign_expert_level(5, 7, None) == 1
    assert cox_survival._assign_expert_level(15, 12, 5) == 2
    assert cox_survival._assign_expert_level(25, 25, 15) == 3


def test_cox_build_capability_minimal(base_conn):
    # 5 个 institution × 10 chain 每个
    rows = []
    rc_id = 1
    for i in range(5):
        inst = f"inst{i}"
        for j in range(10):
            label = "upper" if j < 7 else "lower"  # 70% upper
            rows.append((
                inst, "600519", rc_id, "2024-01-01", "2024-03-01",
                "closed", 60, label,
                20 + j, -5, 15, "T01", "T0101",
            ))
            rc_id += 1
    _seed_alpha_truth(base_conn, rows)
    report = cox_survival.build_institution_capability(base_conn, min_sample_l2=5)
    assert report["written"] >= 5  # 至少每机构一行 L2 + L1


# ============================================================================
# Layer 2A · stock_character
# ============================================================================


def test_car_around_event():
    stock_returns = [
        ("2024-01-02", 0.01),
        ("2024-01-03", 0.02),
        ("2024-01-04", 0.03),
        ("2024-01-05", 0.02),
        ("2024-01-06", 0.01),
        ("2024-01-09", 0.0),
    ]
    mkt = {d: 0.005 for d, _ in stock_returns}
    car = stock_character._car_around_event(stock_returns, mkt, "2024-01-02", window_days=5)
    # excess = 5*(avg_ret - 0.005)
    # actual: 0.01+0.02+0.03+0.02+0.01 = 0.09; mkt: 5*0.005 = 0.025; excess=0.065
    assert car == pytest.approx(0.09 - 0.025, rel=1e-3)


def test_car_around_event_missing():
    assert stock_character._car_around_event([], {}, "2024-01-01", 5) is None


def test_compute_stock_betas_simple(mkt_conn):
    # 构造 60 天收益，入场事件后 CAR 为正
    import sqlite3

    for i in range(60):
        d = f"2024-{((i // 30) + 1):02d}-{((i % 30) + 1):02d}"
        close = 10 + i * 0.1
        mkt_conn.execute(
            "INSERT INTO price_kline VALUES(?,?,'daily','qfq',0,0,0,?,0,0)",
            ("600519", d, close),
        )
    mkt_conn.commit()

    market_ret = {f"2024-{((i//30)+1):02d}-{((i%30)+1):02d}": 0.001 for i in range(60)}

    events = [
        {"event_type": "new_entry", "notice_date": "2024-01-15", "report_date": "2024-01-15"},
        {"event_type": "increase", "notice_date": "2024-02-10", "report_date": "2024-02-10"},
    ]
    betas = stock_character._compute_stock_betas("600519", mkt_conn, events, market_ret)
    assert "beta_inst_entry" in betas
    assert "noise_floor" in betas
    assert betas["noise_floor"] >= 0


# ============================================================================
# Layer 2B · Sharpe Style Analysis
# ============================================================================


def test_fit_sharpe_style_basic():
    # 构造 100 天样本，机构 = 0.6 * T01 + 0.4 * T02 + α=0.001
    rng = np.random.default_rng(123)
    n = 100
    dates = [f"2024-{(i//30)+1:02d}-{(i%30)+1:02d}" for i in range(n)]
    t01 = rng.normal(0.001, 0.01, n)
    t02 = rng.normal(0.0005, 0.015, n)
    y = 0.6 * t01 + 0.4 * t02 + 0.001

    sector_ret = {
        "T01": dict(zip(dates, t01)),
        "T02": dict(zip(dates, t02)),
    }
    inst_series = dict(zip(dates, y))
    fit = institution_style._fit_sharpe_style(inst_series, sector_ret)
    assert fit is not None
    exp = fit["exposure"]
    assert abs(exp["T01"] + exp["T02"] - 1.0) < 1e-4  # weights sum to 1
    assert exp["T01"] == pytest.approx(0.6, abs=0.1)
    assert fit["r2"] > 0.9


def test_fit_sharpe_style_short_series():
    sector_ret = {"T01": {"2024-01-01": 0.01}, "T02": {"2024-01-01": 0.01}}
    inst_series = {"2024-01-01": 0.01}
    assert institution_style._fit_sharpe_style(inst_series, sector_ret) is None


# ============================================================================
# HMM Regime
# ============================================================================


def test_label_regimes_by_mean_3states():
    means = np.array([0.001, -0.002, 0.0005])  # idx 1=lowest, 0=highest, 2=middle
    labels = hmm_regime._label_regimes_by_mean(means)
    assert labels[1] == "bear"
    assert labels[0] == "bull"
    assert labels[2] == "sideways"


def test_label_regimes_by_mean_2states():
    means = np.array([-0.001, 0.002])
    labels = hmm_regime._label_regimes_by_mean(means)
    assert labels[0] == "bear"
    assert labels[1] == "bull"
