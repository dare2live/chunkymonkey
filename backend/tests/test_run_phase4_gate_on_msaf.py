from __future__ import annotations

import numpy as np
import duckdb
import pandas as pd

from scripts.run_phase4_gate_on_msaf import (
    compute_port_returns,
    load_model_train_log,
    load_predictions,
    resolve_is_oos_metrics,
)
from services.ml_ranking.ddl import (
    create_fact_model_train_log_ddl,
    create_lambdamart_v6_predictions_ddl,
)
from services.strategies.ensemble import EnsembleVerdict
from services.strategies.regime.regime_state import RegimeVerdict


def test_load_predictions_falls_back_to_lambdamart_v6_table(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE mart_p0b_oos_predictions (
            signal_date DATE,
            stock_code TEXT,
            score DOUBLE,
            model_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_p0b_lambdamart_v6_predictions (
            signal_date DATE,
            stock_code TEXT,
            score DOUBLE,
            model_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_p0a_label_panel (
            signal_date DATE,
            stock_code TEXT,
            fwd_cost_after_5d DOUBLE,
            fwd_cost_after_10d DOUBLE,
            fwd_cost_after_20d DOUBLE
        )
        """
    )
    conn.execute(
        "INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES "
        "('2026-01-02', '000001', 0.7, 'phase5_model')"
    )
    conn.execute(
        "INSERT INTO mart_p0a_label_panel VALUES "
        "('2026-01-02', '000001', 0.01, 0.02, 0.03)"
    )
    conn.close()

    df = load_predictions(str(db_path), "phase5_model")

    assert len(df) == 1
    assert df.attrs["prediction_table"] == "mart_p0b_lambdamart_v6_predictions"
    assert df.iloc[0]["fwd_cost_after_20d"] == 0.03


def test_load_predictions_prefers_legacy_oos_table(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE mart_p0b_oos_predictions (
            signal_date DATE,
            stock_code TEXT,
            score DOUBLE,
            model_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_p0b_lambdamart_v6_predictions (
            signal_date DATE,
            stock_code TEXT,
            score DOUBLE,
            model_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_p0a_label_panel (
            signal_date DATE,
            stock_code TEXT,
            fwd_cost_after_5d DOUBLE,
            fwd_cost_after_10d DOUBLE,
            fwd_cost_after_20d DOUBLE
        )
        """
    )
    conn.execute(
        "INSERT INTO mart_p0b_oos_predictions VALUES "
        "('2026-01-02', '000001', 0.8, 'model_a')"
    )
    conn.execute(
        "INSERT INTO mart_p0b_lambdamart_v6_predictions VALUES "
        "('2026-01-02', '000001', 0.7, 'model_a')"
    )
    conn.execute(
        "INSERT INTO mart_p0a_label_panel VALUES "
        "('2026-01-02', '000001', 0.01, 0.02, 0.03)"
    )
    conn.close()

    df = load_predictions(str(db_path), "model_a")

    assert len(df) == 1
    assert df.attrs["prediction_table"] == "mart_p0b_oos_predictions"
    assert df.iloc[0]["score"] == 0.8


def test_resolve_is_oos_metrics_uses_true_train_log_when_available(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    create_fact_model_train_log_ddl(conn)
    conn.execute(
        """
        INSERT INTO fact_model_train_log
        (model_id, run_id, is_rank_ic, oos_rank_ic_avg, n_train_rows, n_windows,
         walk_forward_mode, built_at)
        VALUES
        ('model_a', 'older', 0.050, 0.020, 100, 2, 'expanding_monthly', '2026-05-20T00:00:00Z'),
        ('model_a', 'latest', 0.040, 0.033, 120, 3, 'expanding_monthly', '2026-05-21T00:00:00Z')
        """
    )
    conn.close()

    row = load_model_train_log(str(db_path), "model_a")
    metrics = resolve_is_oos_metrics(str(db_path), "model_a", np.array([0.10, 0.05, 0.02, 0.01]))

    assert row is not None
    assert row["run_id"] == "latest"
    assert metrics["is_oos_proxy_mode"] is False
    assert metrics["is_oos_evidence"] == "true-train-log-PIT"
    assert metrics["is_metric"] == 0.04
    assert metrics["oos_metric"] == 0.033
    assert metrics["train_log"]["source_table"] == "fact_model_train_log"


def test_resolve_is_oos_metrics_rejects_partial_train_log_coverage(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    create_fact_model_train_log_ddl(conn)
    create_lambdamart_v6_predictions_ddl(conn)
    conn.execute(
        """
        INSERT INTO fact_model_train_log
        (model_id, run_id, is_rank_ic, oos_rank_ic_avg, n_train_rows, n_windows,
         walk_forward_mode, built_at)
        VALUES
        ('model_a', 'partial', 0.040, 0.033, 120, 2, 'expanding_monthly', '2026-05-21T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO mart_p0b_lambdamart_v6_predictions
        (stock_code, signal_date, score, model_id, model_version, feature_version, label_version,
         walk_forward_mode, train_start, train_end, test_start, test_end, is_final_holdout, built_at)
        VALUES
        ('000001', '2026-01-02', 0.7, 'model_a', 'v6.lambdamart', 'p0a_v4', 'horizon_governance_v1',
         'expanding_monthly', '2025-01-01', '2025-12-31', '2026-01-01', '2026-01-31', false, '2026-05-21T00:00:00Z'),
        ('000001', '2026-02-02', 0.8, 'model_a', 'v6.lambdamart', 'p0a_v4', 'horizon_governance_v1',
         'expanding_monthly', '2025-01-01', '2026-01-31', '2026-02-01', '2026-02-28', false, '2026-05-21T00:00:00Z'),
        ('000001', '2026-03-02', 0.9, 'model_a', 'v6.lambdamart', 'p0a_v4', 'horizon_governance_v1',
         'expanding_monthly', '2025-01-01', '2026-02-28', '2026-03-01', '2026-03-31', false, '2026-05-21T00:00:00Z')
        """
    )
    conn.close()

    metrics = resolve_is_oos_metrics(str(db_path), "model_a", np.array([0.10, 0.05, 0.02, 0.01]))

    assert metrics["is_oos_proxy_mode"] is True
    assert metrics["is_oos_evidence"] == "degraded-split-half-not-train-log"
    assert metrics["train_log"] is None
    assert metrics["train_log_rejected"]["reason"] == "partial-train-log-window-coverage"
    assert metrics["train_log_rejected"]["expected_windows"] == 3
    assert metrics["train_log_rejected"]["actual_windows"] == 2


def test_resolve_is_oos_metrics_falls_back_to_split_half_without_train_log(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.close()

    metrics = resolve_is_oos_metrics(str(db_path), "missing_model", np.array([0.10, 0.05, 0.02, 0.01]))

    assert metrics["is_oos_proxy_mode"] is True
    assert metrics["is_oos_evidence"] == "degraded-split-half-not-train-log"
    assert metrics["is_metric"] == 0.07500000000000001
    assert metrics["oos_metric"] == 0.015
    assert metrics["train_log"] is None


def test_compute_port_returns_applies_score_filter_and_exposure(monkeypatch):
    preds = pd.DataFrame(
        [
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000001",
                "score": 0.9,
                "fwd_cost_after_5d": 0.10,
            },
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000002",
                "score": 0.8,
                "fwd_cost_after_5d": 0.20,
            },
        ]
    )

    def fake_regime(signal_date, hs300):
        return RegimeVerdict(
            signal_date=signal_date,
            state="bull",
            hs300_close=1.0,
            hs300_ma60=1.0,
            above_ma60=True,
            ret_60d=0.1,
            breadth_pct=None,
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            reasoning="test",
        )

    def fake_ensemble_scores(**kwargs):
        return EnsembleVerdict(
            signal_date=kwargs["signal_date"],
            regime_state="bull",
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            top_k_codes=["000001", "000002"],
            top_k_scores=[0.9, 0.4],
            cash_pct=0.0,
            detail={"reasoning": "test"},
        )

    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.compute_regime_state", fake_regime)
    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.ensemble_scores", fake_ensemble_scores)

    obs = compute_port_returns(
        preds,
        "5d",
        pd.DataFrame(),
        min_top_score=0.5,
        score_exposure_floor=0.8,
        score_exposure_ceiling=1.0,
        score_min_exposure=0.5,
    )

    assert obs == [(pd.Timestamp("2026-01-02"), 0.037500000000000006)]


def test_compute_port_returns_applies_rank_decay(monkeypatch):
    preds = pd.DataFrame(
        [
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000001",
                "score": 0.9,
                "fwd_cost_after_5d": 0.10,
            },
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000002",
                "score": 0.8,
                "fwd_cost_after_5d": 0.20,
            },
        ]
    )

    def fake_regime(signal_date, hs300):
        return RegimeVerdict(
            signal_date=signal_date,
            state="bull",
            hs300_close=1.0,
            hs300_ma60=1.0,
            above_ma60=True,
            ret_60d=0.1,
            breadth_pct=None,
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            reasoning="test",
        )

    def fake_ensemble_scores(**kwargs):
        return EnsembleVerdict(
            signal_date=kwargs["signal_date"],
            regime_state="bull",
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            top_k_codes=["000001", "000002"],
            top_k_scores=[0.9, 0.8],
            cash_pct=0.0,
            detail={"reasoning": "test"},
        )

    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.compute_regime_state", fake_regime)
    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.ensemble_scores", fake_ensemble_scores)

    obs = compute_port_returns(preds, "5d", pd.DataFrame(), rank_decay=0.5)

    assert obs == [(pd.Timestamp("2026-01-02"), 0.13333333333333333)]


def test_compute_port_returns_applies_sniper_floor(monkeypatch):
    preds = pd.DataFrame(
        [
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000001",
                "score": 0.9,
                "fwd_cost_after_5d": 0.10,
            },
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000002",
                "score": 0.8,
                "fwd_cost_after_5d": 0.20,
            },
        ]
    )

    def fake_regime(signal_date, hs300):
        return RegimeVerdict(
            signal_date=signal_date,
            state="bull",
            hs300_close=1.0,
            hs300_ma60=1.0,
            above_ma60=True,
            ret_60d=0.1,
            breadth_pct=None,
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            reasoning="test",
        )

    def fake_ensemble_scores(**kwargs):
        return EnsembleVerdict(
            signal_date=kwargs["signal_date"],
            regime_state="bull",
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            top_k_codes=["000001", "000002"],
            top_k_scores=[0.9, 0.8],
            cash_pct=0.0,
            detail={"reasoning": "test"},
        )

    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.compute_regime_state", fake_regime)
    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.ensemble_scores", fake_ensemble_scores)

    obs = compute_port_returns(
        preds,
        "5d",
        pd.DataFrame(),
        sniper_by_sd={pd.Timestamp("2026-01-02"): pd.Series({"000001": 0.6, "000002": 0.1})},
        min_sniper_score=0.5,
    )

    assert obs == [(pd.Timestamp("2026-01-02"), 0.05)]


def test_compute_port_returns_passes_primary_max_positions(monkeypatch):
    preds = pd.DataFrame(
        [
            {
                "signal_date": pd.Timestamp("2026-01-02"),
                "stock_code": "000001",
                "score": 0.9,
                "fwd_cost_after_5d": 0.10,
            }
        ]
    )
    seen = {}

    def fake_regime(signal_date, hs300):
        return RegimeVerdict(
            signal_date=signal_date,
            state="bull",
            hs300_close=1.0,
            hs300_ma60=1.0,
            above_ma60=True,
            ret_60d=0.1,
            breadth_pct=None,
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            reasoning="test",
        )

    def fake_ensemble_scores(**kwargs):
        seen["max_positions"] = kwargs["max_positions"]
        return EnsembleVerdict(
            signal_date=kwargs["signal_date"],
            regime_state="bull",
            weights={"lambdamart": 1.0, "sniper": 0.0, "institution": 0.0, "cash": 0.0},
            top_k_codes=["000001"],
            top_k_scores=[0.9],
            cash_pct=0.0,
            detail={"reasoning": "test"},
        )

    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.compute_regime_state", fake_regime)
    monkeypatch.setattr("scripts.run_phase4_gate_on_msaf.ensemble_scores", fake_ensemble_scores)

    compute_port_returns(preds, "5d", pd.DataFrame(), max_positions=3)

    assert seen["max_positions"] == 3
