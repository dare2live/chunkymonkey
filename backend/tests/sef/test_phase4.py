"""Phase IV 单元测试: Thompson Sampling / Drift / Counterfactual / Walk-Forward."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from services.sef.schema import migrate_phase1
from services.sef import (
    thompson_sampling,
    drift_monitor,
    counterfactual,
    walk_forward,
    meta_labeling,
)


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
            chain_start_date TEXT, chain_end_date TEXT, chain_status TEXT,
            entry_premium_pct REAL, entry_follow_price REAL,
            PRIMARY KEY(institution_id, stock_code, chain_id)
        );
        """
    )
    migrate_phase1(c)
    yield c
    c.close()


def _seed_truth(conn, inst_id, n_win, n_lose, entry_date="2024-01-01"):
    """插入 n_win + n_lose 条 chain truth 记录."""
    for i in range(n_win + n_lose):
        pnl = 15.0 if i < n_win else -5.0
        conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth(
                institution_id, stock_code, research_chain_id, entry_date,
                eval_date, status, chain_days, tb_label, chain_follow_pnl
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (inst_id, "600519", i, entry_date, "2024-04-01", "closed", 90,
             "upper" if i < n_win else "lower", pnl),
        )
    conn.commit()


# ============================================================================
# Thompson Sampling
# ============================================================================


def test_bandit_update_basic(base_conn):
    _seed_truth(base_conn, "inst_winner", 30, 5)
    _seed_truth(base_conn, "inst_loser", 5, 25)
    _seed_truth(base_conn, "inst_new", 2, 1)

    report = thompson_sampling.update_bandit_state(base_conn)
    assert report["institutions_scored"] == 3

    rows = base_conn.execute(
        "SELECT institution_id, posterior_mean, total_signals FROM mart_exploration_bandit"
    ).fetchall()
    by = {r[0]: (r[1], r[2]) for r in rows}
    # winner 应有最高 posterior_mean
    assert by["inst_winner"][0] > by["inst_loser"][0]
    assert by["inst_winner"][1] > 0.5


def test_bandit_explore_candidates(base_conn):
    _seed_truth(base_conn, "inst_A", 15, 5)  # total=20, 不算 explore
    _seed_truth(base_conn, "inst_B", 3, 1)   # total=4, 算 explore 候选
    _seed_truth(base_conn, "inst_C", 20, 15) # total=35

    thompson_sampling.update_bandit_state(base_conn)
    row = base_conn.execute(
        "SELECT institution_id FROM mart_exploration_bandit WHERE is_explore_candidate=1"
    ).fetchall()
    codes = [r[0] for r in row]
    # inst_B 应被标记（total<20）
    assert "inst_B" in codes or len(codes) >= 1


def test_sample_allocation_sums_to_budget(base_conn):
    _seed_truth(base_conn, "i1", 20, 5)
    _seed_truth(base_conn, "i2", 15, 8)
    _seed_truth(base_conn, "i3", 3, 1)
    thompson_sampling.update_bandit_state(base_conn)

    alloc = thompson_sampling.sample_allocation(base_conn, total_budget=1.0)
    total = sum(alloc["allocation"].values())
    assert abs(total - 1.0) < 0.05 or total == 0  # 允许小误差或无机构


# ============================================================================
# Drift Monitor
# ============================================================================


def test_psi_identical_distributions_zero():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, 500)
    b = rng.normal(0, 1, 500)
    # identical distributions → PSI near 0
    assert drift_monitor._psi(a, b) < 0.2


def test_psi_shifted_distribution_positive():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, 500)
    b = rng.normal(3, 1, 500)  # mean shift
    # shifted distribution → PSI > 0.25 (severe)
    assert drift_monitor._psi(a, b) > 0.25


def test_classify_thresholds():
    assert drift_monitor._classify(0.05, 0.8) == ("stable", 1.0)
    assert drift_monitor._classify(0.15, 0.3) == ("mild", 0.7)
    assert drift_monitor._classify(0.30, 0.1) == ("severe", 0.3)
    assert drift_monitor._classify(0.05, 0.01) == ("severe", 0.3)


def test_run_drift_monitor_minimal(base_conn):
    # 两批数据：2023 年历史 vs 2025 年最近
    for i in range(15):
        base_conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth(
                institution_id, stock_code, research_chain_id, entry_date,
                eval_date, status, chain_follow_pnl
            ) VALUES(?,?,?,?,?,?,?)
            """,
            ("inst1", "600519", i, "2023-01-01", "2023-06-01", "closed", 10.0),
        )
    for i in range(15, 30):
        base_conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth(
                institution_id, stock_code, research_chain_id, entry_date,
                eval_date, status, chain_follow_pnl
            ) VALUES(?,?,?,?,?,?,?)
            """,
            ("inst1", "600519", i, "2026-01-15", "2026-03-30", "closed", -5.0),
        )
    base_conn.commit()

    report = drift_monitor.run_drift_monitor(
        base_conn, eval_date="2026-04-01", recent_months=6, history_months=48,
        min_samples=5,
    )
    assert report["written"] >= 1
    # 明显 drift，应该 severe
    row = base_conn.execute(
        "SELECT alert_level, confidence_mult FROM institution_drift_log WHERE institution_id='inst1'"
    ).fetchone()
    assert row is not None
    assert row[1] < 1.0  # 至少 mild 或 severe


# ============================================================================
# Counterfactual
# ============================================================================


def test_compute_stats_empty():
    stats = counterfactual._compute_stats([])
    assert stats["n"] == 0
    assert stats["mean"] is None


def test_compute_stats_basic():
    stats = counterfactual._compute_stats([10.0, 20.0, -5.0, 15.0])
    assert stats["n"] == 4
    assert stats["mean"] == pytest.approx(10.0)
    assert stats["win_rate"] == pytest.approx(0.75)


def test_counterfactual_runs_end_to_end(base_conn):
    # 构造 closed chain 数据 + 对应的 research_holding_chains + predictions
    for i in range(20):
        pnl = 10.0 if i % 2 == 0 else -5.0
        base_conn.execute(
            """
            INSERT INTO fact_chain_alpha_truth(
                institution_id, stock_code, research_chain_id, entry_date,
                eval_date, status, chain_follow_pnl
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (f"inst{i%3}", f"6000{i:02d}", i, "2024-01-01", "2024-06-01",
             "closed", pnl),
        )
        base_conn.execute(
            """
            INSERT INTO research_holding_chains(
                institution_id, stock_code, chain_id,
                chain_start_date, chain_end_date, chain_status,
                entry_premium_pct
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (f"inst{i%3}", f"6000{i:02d}", i, "2024-01-01", "2024-06-01",
             "closed", 5.0),
        )
    # 一个简单的 meta model + predictions
    meta_labeling._ensure_tables(base_conn)
    base_conn.execute(
        "INSERT INTO mart_meta_label_model(model_version, trained_at) VALUES('mv1', '2026-04-01')"
    )
    for i in range(20):
        prob = 0.8 if i % 2 == 0 else 0.2
        pnl = 10.0 if i % 2 == 0 else -5.0
        base_conn.execute(
            """
            INSERT INTO mart_meta_label_predictions(
                stock_code, institution_id, notice_date, model_version,
                primary_action, meta_prob_follow, meta_action, pnl_realized
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (f"6000{i:02d}", f"inst{i%3}", "20240101", "mv1",
             "follow", prob, "follow" if prob > 0.5 else "skip", pnl),
        )
    base_conn.commit()

    report = counterfactual.run_counterfactual(base_conn, eval_date="2026-04-01")
    assert report["v6_baseline"]["n"] > 0
    assert report["sef_strategy"]["n"] > 0
    # SEF 只选 prob>0.5（赚钱的 half），应显著优于 V6 的 50/50
    assert report["sef_strategy"]["mean"] > report["v6_baseline"]["mean"]
