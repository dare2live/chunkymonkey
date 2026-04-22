"""Phase III 单元测试: Bayesian / Meta-Labeling / Black-Litterman."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from services.sef.schema import migrate_phase1
from services.sef import bayesian_updater, meta_labeling, black_litterman


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
        CREATE TABLE dim_stock_tdx_industry(
            stock_code TEXT PRIMARY KEY, tdx_l1 TEXT, tdx_l2 TEXT
        );
        """
    )
    migrate_phase1(c)
    yield c
    c.close()


# ============================================================================
# Bayesian Updater
# ============================================================================


def test_update_posterior_pure_prior():
    """无观测时后验等于先验."""
    mu, sigma = bayesian_updater._update_posterior(10.0, 5.0, [])
    assert mu == pytest.approx(10.0)
    assert sigma == pytest.approx(5.0)


def test_update_posterior_with_observations():
    """多观测向证据靠拢."""
    mu, sigma = bayesian_updater._update_posterior(
        prior_mu=0.0, prior_sigma=10.0,
        observations=[(20.0, 2.0), (25.0, 2.0)],
    )
    # 观测精度远高于先验，μ_post 应接近观测均值
    assert mu > 20.0
    assert sigma < 2.0


def test_update_posterior_skip_zero_sigma():
    mu, sigma = bayesian_updater._update_posterior(
        prior_mu=5.0, prior_sigma=10.0,
        observations=[(100.0, 0), (100.0, None)],
    )
    # 无效观测被跳过
    assert mu == pytest.approx(5.0)


def test_time_decay():
    # halflife 60d, 60 天后应为 0.5
    d = bayesian_updater._time_decay("2024-01-01", "2024-03-01", 60.0)
    assert 0.45 < d < 0.55
    # 同一天 = 1.0
    assert bayesian_updater._time_decay("2024-01-01", "2024-01-01", 60) == 1.0
    # None halflife = 不衰减
    assert bayesian_updater._time_decay("2024-01-01", "2024-06-01", None) == 1.0


def test_build_bayesian_posterior_e2e(base_conn):
    # 构造 1 个行业 L1=T01，1 个机构，1 只股票，1 条 capability，1 条近期信号
    base_conn.execute(
        "INSERT INTO dim_stock_tdx_industry VALUES('600519', 'T01', 'T0101')"
    )
    base_conn.execute(
        """
        INSERT INTO mart_institution_capability(
            institution_id, industry_level, industry_code,
            alpha_median, alpha_se, sample_count, expert_level, alpha_halflife_days
        ) VALUES('inst1', 'L1', 'T01', 15.0, 3.0, 10, 2, 90),
                ('inst1', 'L2', 'T0101', 18.0, 4.0, 8, 2, 80)
        """
    )
    base_conn.execute(
        """
        INSERT INTO fact_institution_event(
            institution_id, stock_code, report_date, notice_date, event_type
        ) VALUES('inst1', '600519', '20260101', '20260101', 'new_entry')
        """
    )
    base_conn.commit()

    report = bayesian_updater.build_bayesian_posterior(
        base_conn, as_of_date="2026-01-05", lookback_days=180
    )
    assert report["stocks_with_posterior"] == 1
    row = base_conn.execute(
        "SELECT mu_posterior, sigma_posterior, n_signals FROM mart_bayesian_posterior"
    ).fetchone()
    # 观测 ≈ 18，先验 ≈ 15 → post 介于两者
    assert 10.0 <= row[0] <= 20.0
    assert row[2] == 1


# ============================================================================
# Meta-Labeling
# ============================================================================


def test_row_to_features_shape():
    row = {
        "cap_alpha": 10.0, "cap_se": 2.0, "cap_n": 20, "cap_level": 2,
        "cap_halflife": 60.0, "sty_r2": 0.5, "sty_alpha": 0.001,
        "entry_prem": -2.0, "entry_price": 100.0, "chain_days": 45,
        "industry_l1": "T05",
    }
    # simulate Row behavior via dict-like
    class R:
        def __init__(self, d): self.d = d
        def __getitem__(self, k): return self.d[k]
        def keys(self): return self.d.keys()
    vec = meta_labeling._row_to_features(R(row))
    assert len(vec) == len(meta_labeling.FEATURE_NAMES)
    # T05 one-hot 应该在第 5 个行业位（T01 是 index 0）置 1
    idx_t05 = meta_labeling.FEATURE_NAMES.index("ind_T05")
    assert vec[idx_t05] == 1.0


# ============================================================================
# Black-Litterman
# ============================================================================


def test_bl_posterior_no_views_returns_prior():
    Pi = np.array([0.05, 0.07, 0.03])
    Sigma = np.diag([0.01, 0.02, 0.015])
    P = np.empty((0, 3))
    Q = np.empty(0)
    Omega = np.empty((0, 0))
    post = black_litterman._bl_posterior(Pi, Sigma, P, Q, Omega)
    np.testing.assert_allclose(post, Pi)


def test_bl_posterior_moves_towards_views():
    # 1 只股票，先验 5%，view 说 20%，view 精度高
    Pi = np.array([0.05])
    Sigma = np.array([[0.04]])
    P = np.array([[1.0]])
    Q = np.array([0.20])
    Omega = np.array([[1e-6]])  # 极高置信度
    post = black_litterman._bl_posterior(Pi, Sigma, P, Q, Omega, tau=0.05)
    assert post[0] > 0.15


def test_solve_portfolio_basic():
    n = 5
    rng = np.random.default_rng(42)
    E_R = np.array([0.001, 0.002, -0.0005, 0.0015, 0.0008])
    A = rng.normal(size=(n, n)) * 0.01
    Sigma = A @ A.T + np.eye(n) * 0.01  # PSD
    w = black_litterman._solve_portfolio(
        E_R, Sigma, max_weight=0.5, max_holdings=5
    )
    assert w is not None
    assert w.shape == (n,)
    assert abs(w.sum() - 1.0) < 1e-3
    assert (w >= -1e-9).all()


def test_solve_portfolio_turnover_constraint():
    n = 4
    E_R = np.array([0.002, 0.001, 0.001, 0.001])
    Sigma = np.eye(n) * 0.01
    prev = np.array([0.25, 0.25, 0.25, 0.25])
    # max_weight must allow sum(w)=1 with n=4: 1/n=0.25 → 0.5 上限即可
    w = black_litterman._solve_portfolio(
        E_R, Sigma, prev_weights=prev, max_turnover=0.10,
        max_weight=0.5,
    )
    assert w is not None
    # L1 距离 ≤ 0.10 (+ 数值松弛)
    assert abs(w - prev).sum() <= 0.11
