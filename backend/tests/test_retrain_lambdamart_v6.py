from __future__ import annotations

import json

import pandas as pd
import optuna
import pytest

import scripts.retrain_lambdamart_v6 as retrain_module
from conftest import duck_mem
from scripts.run_p0b_lambdamart_v6 import enqueue_warm_start_trial, load_warm_start_params
from scripts.retrain_lambdamart_v6 import (
    build_train_log_window_record,
    build_train_log_record,
    complete_lambdamart_params,
    load_verified_train_log_windows,
    load_checkpoint_best_payload,
    load_checkpoint_best_params,
    make_model_id,
    make_train_log_params_hash,
    materialize_best_predictions_with_train_log,
    persist_materialization_outputs,
    persist_predictions,
    persist_train_log,
    persist_train_log_window,
    register_lambdamart_v6_asset,
    train_log_window_key,
)
from services.ml_ranking.ddl import (
    FACT_MODEL_TRAIN_LOG_TABLE,
    FACT_MODEL_TRAIN_LOG_WINDOW_TABLE,
    LAMBDAMART_V6_PREDICTIONS_TABLE,
    OOS_PREDICTIONS_DDL,
    create_lambdamart_v6_predictions_ddl,
)


def _describe(conn, table_name: str) -> list[tuple[str, str]]:
    return [(r["column_name"], r["column_type"]) for r in conn.execute(f"DESCRIBE {table_name}").fetchall()]


def test_model_id_uses_lambdamart_v6_date_prefix():
    assert make_model_id("2026-05-18") == "lambdamart_v6_20260518"
    assert make_model_id("20260518") == "lambdamart_v6_20260518"


def test_complete_params_adds_fixed_lightgbm_fields(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "4")
    params = complete_lambdamart_params({"max_depth": 4, "num_leaves": 15}, seed=7, n_estimators=50)

    assert params["max_depth"] == 4
    assert params["num_leaves"] == 15
    assert params["random_state"] == 7
    assert params["n_estimators"] == 50
    assert params["n_jobs"] == 4
    assert params["num_threads"] == 4


def test_load_checkpoint_best_params(tmp_path):
    checkpoint = tmp_path / "best.json"
    checkpoint.write_text(
        '{"best_params": {"max_depth": 7, "num_leaves": 40}, "best_value": 0.42}',
        encoding="utf-8",
    )

    assert load_checkpoint_best_params(checkpoint) == {"max_depth": 7, "num_leaves": 40}
    assert load_checkpoint_best_payload(checkpoint)["best_value"] == 0.42


def test_load_checkpoint_best_params_rejects_missing_params(tmp_path):
    checkpoint = tmp_path / "bad.json"
    checkpoint.write_text('{"best_value": 0.42}', encoding="utf-8")

    with pytest.raises(ValueError, match="best_params"):
        load_checkpoint_best_params(checkpoint)


def test_load_warm_start_params_reads_best_params(tmp_path):
    checkpoint = tmp_path / "warm.best.json"
    checkpoint.write_text(
        '{"best_params": {"max_depth": 7, "num_leaves": 40}, "best_value": 0.42}',
        encoding="utf-8",
    )

    assert load_warm_start_params(checkpoint) == {"max_depth": 7, "num_leaves": 40}


def test_enqueue_warm_start_trial_marks_pending_trial():
    study = optuna.create_study(direction="maximize")

    enqueue_warm_start_trial(
        study,
        {"max_depth": 7, "num_leaves": 40},
        source="data/reports/optuna/example.best.json",
    )

    assert len(study.trials) == 1
    trial = study.trials[0]
    assert trial.state == optuna.trial.TrialState.WAITING
    assert trial.params == {}
    assert trial.system_attrs["fixed_params"] == {"max_depth": 7, "num_leaves": 40}
    assert trial.user_attrs["warm_start"] is True
    assert trial.user_attrs["warm_start_source"] == "data/reports/optuna/example.best.json"


def test_v6_predictions_schema_persist_and_registry():
    with duck_mem() as conn:
        conn.execute(OOS_PREDICTIONS_DDL)
        create_lambdamart_v6_predictions_ddl(conn)

        conn.execute(
            """
            CREATE TABLE dim_data_asset (
                table_name TEXT PRIMARY KEY,
                layer TEXT NOT NULL,
                purpose TEXT,
                writer_module TEXT,
                expected_freshness TEXT,
                schema_version TEXT,
                auto_discovered BOOLEAN,
                last_updated_at TIMESTAMP
            )
            """
        )

        expected = _describe(conn, "mart_p0b_oos_predictions")
        actual = _describe(conn, LAMBDAMART_V6_PREDICTIONS_TABLE)
        assert actual == expected

        predictions = pd.DataFrame(
            [
                {
                    "stock_code": "600001",
                    "signal_date": "2024-07-01",
                    "score": 0.42,
                    "fwd_cost_after_5d": None,
                    "fwd_cost_after_10d": None,
                    "fwd_cost_after_20d": 0.03,
                    "model_id": "lambdamart_v6_20260518",
                    "model_version": "v6.lambdamart",
                    "feature_version": "p0a_v4",
                    "label_version": "horizon_governance_v1",
                    "walk_forward_mode": "expanding_monthly",
                    "train_start": "2024-01-01",
                    "train_end": "2024-06-30",
                    "test_start": "2024-07-01",
                    "test_end": "2024-07-31",
                    "is_final_holdout": False,
                    "built_at": "2026-05-18T00:00:00+00:00",
                }
            ]
        )

        assert persist_predictions(conn, predictions, model_id="lambdamart_v6_20260518") == 1
        register_lambdamart_v6_asset(conn)

        row = conn.execute(
            f"SELECT model_id, score, fwd_cost_after_20d FROM {LAMBDAMART_V6_PREDICTIONS_TABLE}"
        ).fetchone()
        assert row["model_id"] == "lambdamart_v6_20260518"
        assert row["score"] == 0.42
        assert row["fwd_cost_after_20d"] == 0.03

        asset = conn.execute(
            "SELECT layer, writer_module, schema_version, auto_discovered FROM dim_data_asset WHERE table_name = ?",
            [LAMBDAMART_V6_PREDICTIONS_TABLE],
        ).fetchone()
        assert asset["layer"] == "mart"
        assert asset["writer_module"] == "backend/scripts/retrain_lambdamart_v6.py"
        assert asset["schema_version"] == "v1"
        assert asset["auto_discovered"] is False

        version = conn.execute(
            "SELECT expected_version, actual_version FROM dim_schema_version WHERE table_name = ?",
            [LAMBDAMART_V6_PREDICTIONS_TABLE],
        ).fetchone()
        assert version["expected_version"] == "v1"
        assert version["actual_version"] == "v1"


def test_train_log_record_persists_true_is_oos_evidence():
    class _Window:
        train_start = "2024-01-02"
        train_end = "2024-12-31"
        test_start = "2025-01-02"
        test_end = "2025-01-31"

    record = build_train_log_record(
        model_id="lambdamart_v6_unit",
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        windows=[_Window()],
        window_metrics=[
            {
                "window_idx": 0,
                "n_train_rows": 100,
                "n_test_rows": 10,
                "train_metrics": {"rank_ic": 0.04, "ndcg5": 0.61, "ndcg10": 0.58, "ndcg20": 0.55},
                "oos_metrics": {"rank_ic": 0.033, "ndcg5": 0.57},
            }
        ],
        built_at="2026-05-21T00:00:00+00:00",
        seed=42,
        n_trials=50,
        optuna_best_value=0.42,
    )
    record["n_features"] = 3

    with duck_mem() as conn:
        assert persist_train_log(conn, record) == 1

        row = conn.execute(
            f"""
            SELECT model_id, is_rank_ic, oos_rank_ic_avg, n_windows, n_train_rows, metrics_json
              FROM {FACT_MODEL_TRAIN_LOG_TABLE}
             WHERE model_id = ?
            """,
            ["lambdamart_v6_unit"],
        ).fetchone()

        assert row["model_id"] == "lambdamart_v6_unit"
        assert row["is_rank_ic"] == 0.04
        assert row["oos_rank_ic_avg"] == 0.033
        assert row["n_windows"] == 1
        assert row["n_train_rows"] == 100
        assert json.loads(row["metrics_json"])["metric_family"] == "rank_ic"


def test_train_log_window_checkpoint_reuses_only_verified_complete_rows():
    class _Window:
        train_idx = [0, 1, 2]
        test_idx = [3, 4]
        train_start = "2024-01-02"
        train_end = "2024-12-31"
        test_start = "2025-01-02"
        test_end = "2025-01-31"

    window = _Window()
    params_hash = make_train_log_params_hash(
        params={"max_depth": 4, "n_estimators": 10},
        label_col="fwd_cost_after_20d",
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        seed=42,
        windows=[window],
    )
    record = build_train_log_window_record(
        model_id="lambdamart_v6_unit",
        replay_id="unit-replay",
        params_hash=params_hash,
        window_idx=0,
        window=window,
        n_train_rows=3,
        n_test_rows=2,
        n_features=5,
        train_metrics={"rank_ic": 0.04},
        oos_metrics={"rank_ic": 0.033},
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        built_at="2026-05-21T00:00:00+00:00",
    )

    with duck_mem() as conn:
        assert persist_train_log_window(conn, record) == 1
        verified = load_verified_train_log_windows(
            conn,
            model_id="lambdamart_v6_unit",
            replay_id="unit-replay",
            params_hash=params_hash,
            windows=[window],
        )

        key = train_log_window_key(window)
        assert list(verified) == [key]
        assert verified[key]["n_train_rows"] == 3
        assert verified[key]["oos_metrics"]["rank_ic"] == 0.033

        conn.execute(
            f"UPDATE {FACT_MODEL_TRAIN_LOG_WINDOW_TABLE} SET n_test_rows = 0 WHERE model_id = ?",
            ["lambdamart_v6_unit"],
        )
        assert load_verified_train_log_windows(
            conn,
            model_id="lambdamart_v6_unit",
            replay_id="unit-replay",
            params_hash=params_hash,
            windows=[window],
        ) == {}


def test_train_log_aggregate_rejects_incomplete_window_metrics():
    class _WindowA:
        train_idx = [0, 1]
        test_idx = [2]
        train_start = "2024-01-02"
        train_end = "2024-12-31"
        test_start = "2025-01-02"
        test_end = "2025-01-31"

    class _WindowB:
        train_idx = [0, 1, 2]
        test_idx = [3]
        train_start = "2024-01-02"
        train_end = "2025-01-31"
        test_start = "2025-02-03"
        test_end = "2025-02-28"

    with pytest.raises(ValueError, match="incomplete train-log replay"):
        retrain_module._assert_complete_train_log_window_metrics(
            [_WindowA(), _WindowB()],
            [
                {
                    "window_idx": 0,
                    "train_start": "2024-01-02",
                    "train_end": "2024-12-31",
                    "test_start": "2025-01-02",
                    "test_end": "2025-01-31",
                    "n_train_rows": 2,
                    "n_test_rows": 1,
                    "train_metrics": {"rank_ic": 0.04},
                    "oos_metrics": {"rank_ic": 0.033},
                }
            ],
        )


def test_train_log_only_persists_evidence_without_replacing_predictions():
    class _Window:
        train_start = "2024-01-02"
        train_end = "2024-12-31"
        test_start = "2025-01-02"
        test_end = "2025-01-31"

    record = build_train_log_record(
        model_id="lambdamart_v6_unit",
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        windows=[_Window()],
        window_metrics=[
            {
                "window_idx": 0,
                "n_train_rows": 100,
                "n_test_rows": 10,
                "train_metrics": {"rank_ic": 0.04},
                "oos_metrics": {"rank_ic": 0.033},
            }
        ],
        built_at="2026-05-21T00:00:00+00:00",
        seed=42,
        n_trials=50,
        optuna_best_value=0.42,
    )
    record["n_features"] = 3
    replacement_predictions = pd.DataFrame(
        [
            {
                "stock_code": "600001",
                "signal_date": "2024-07-01",
                "score": 0.99,
                "fwd_cost_after_5d": None,
                "fwd_cost_after_10d": None,
                "fwd_cost_after_20d": 0.09,
                "model_id": "lambdamart_v6_unit",
                "model_version": "v6.lambdamart",
                "feature_version": "p0a_v4",
                "label_version": "horizon_governance_v1",
                "walk_forward_mode": "expanding_monthly",
                "train_start": "2024-01-01",
                "train_end": "2024-06-30",
                "test_start": "2024-07-01",
                "test_end": "2024-07-31",
                "is_final_holdout": False,
                "built_at": "2026-05-21T00:00:00+00:00",
            }
        ]
    )

    with duck_mem() as conn:
        create_lambdamart_v6_predictions_ddl(conn)
        conn.execute(
            f"""
            INSERT INTO {LAMBDAMART_V6_PREDICTIONS_TABLE}
            (stock_code, signal_date, score,
             fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
             model_id, model_version, feature_version, label_version,
             walk_forward_mode, train_start, train_end, test_start, test_end,
             is_final_holdout, built_at)
            VALUES
            ('600001', '2024-07-01', 0.42, NULL, NULL, 0.03,
             'lambdamart_v6_unit', 'v6.lambdamart', 'p0a_v4', 'horizon_governance_v1',
             'expanding_monthly', '2024-01-01', '2024-06-30', '2024-07-01', '2024-07-31',
             false, '2026-05-18T00:00:00+00:00')
            """
        )

        n_predictions, n_train_logs = persist_materialization_outputs(
            conn,
            replacement_predictions,
            record,
            model_id="lambdamart_v6_unit",
            train_log_only=True,
        )

        assert n_predictions == 0
        assert n_train_logs == 1
        existing = conn.execute(
            f"SELECT score, fwd_cost_after_20d FROM {LAMBDAMART_V6_PREDICTIONS_TABLE}"
        ).fetchone()
        assert existing["score"] == 0.42
        assert existing["fwd_cost_after_20d"] == 0.03
        train_log = conn.execute(
            f"SELECT oos_rank_ic_avg FROM {FACT_MODEL_TRAIN_LOG_TABLE} WHERE model_id = ?",
            ["lambdamart_v6_unit"],
        ).fetchone()
        assert train_log["oos_rank_ic_avg"] == 0.033


def test_train_log_resume_skips_verified_windows_and_persists_missing(monkeypatch):
    class _X:
        def __getitem__(self, idx):
            return list(idx)

    class _Panel:
        X = _X()
        feature_columns = ["f1", "f2"]

    class _WindowA:
        train_idx = [0, 1]
        test_idx = [2]
        train_start = "2024-01-02"
        train_end = "2024-12-31"
        test_start = "2025-01-02"
        test_end = "2025-01-31"

    class _WindowB:
        train_idx = [0, 1, 2]
        test_idx = [3]
        train_start = "2024-01-02"
        train_end = "2025-01-31"
        test_start = "2025-02-03"
        test_end = "2025-02-28"

    class _Model:
        def predict(self, rows):
            return pd.Series([0.1] * len(rows))

    windows = [_WindowA(), _WindowB()]
    params_hash = make_train_log_params_hash(
        params={"max_depth": 4},
        label_col="fwd_cost_after_20d",
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        seed=42,
        windows=windows,
    )
    first_record = build_train_log_window_record(
        model_id="lambdamart_v6_unit",
        replay_id="unit-replay",
        params_hash=params_hash,
        window_idx=0,
        window=windows[0],
        n_train_rows=2,
        n_test_rows=1,
        n_features=2,
        train_metrics={"rank_ic": 0.04},
        oos_metrics={"rank_ic": 0.02},
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        built_at="2026-05-21T00:00:00+00:00",
    )

    fit_windows: list[str] = []

    def _fake_fit(_panel, window, _params):
        fit_windows.append(window.test_start)
        return _Model()

    def _fake_prediction_frame(_panel, idx, pred, *, label_col):
        return pd.DataFrame(
            {
                "stock_code": [f"60000{i}" for i in idx],
                "signal_date": ["2025-02-03"] * len(idx),
                "score": list(pred),
                label_col: [0.01] * len(idx),
            }
        )

    def _fake_evaluate_predictions(df, *, label_col):
        return {"rank_ic": 0.03 if len(df) == 3 else 0.01}

    monkeypatch.setattr(retrain_module, "_fit_lambdamart_window_model", _fake_fit)
    monkeypatch.setattr(retrain_module, "_prediction_frame", _fake_prediction_frame)
    monkeypatch.setattr(retrain_module, "evaluate_predictions", _fake_evaluate_predictions)

    with duck_mem() as conn:
        persist_train_log_window(conn, first_record)
        predictions, train_log = materialize_best_predictions_with_train_log(
            panel=_Panel(),
            windows=windows,
            params={"max_depth": 4},
            label_col="fwd_cost_after_20d",
            model_id="lambdamart_v6_unit",
            feature_version="p0a_v4",
            label_version="horizon_governance_v1",
            built_at="2026-05-21T00:00:00+00:00",
            seed=42,
            n_trials=50,
            optuna_best_value=0.42,
            include_predictions=False,
            checkpoint_conn=conn,
            resume_train_log=True,
            replay_id="unit-replay",
            params_hash=params_hash,
        )

        assert predictions.empty
        assert fit_windows == ["2025-02-03"]
        assert train_log["n_windows"] == 2
        assert train_log["n_train_rows"] == 5
        assert train_log["is_rank_ic"] == pytest.approx(0.035)
        assert train_log["oos_rank_ic_avg"] == pytest.approx(0.015)
        row_count = conn.execute(f"SELECT COUNT(*) AS n FROM {FACT_MODEL_TRAIN_LOG_WINDOW_TABLE}").fetchone()
        assert row_count["n"] == 2


def test_train_log_only_materialization_skips_prediction_output(monkeypatch):
    class _X:
        def __getitem__(self, idx):
            return list(idx)

    class _Panel:
        X = _X()
        feature_columns = ["f1", "f2"]

    class _Window:
        train_idx = [0, 1]
        test_idx = [2]
        train_start = "2024-01-02"
        train_end = "2024-12-31"
        test_start = "2025-01-02"
        test_end = "2025-01-31"

    class _Model:
        def predict(self, rows):
            return pd.Series([0.1] * len(rows))

    def _fake_prediction_frame(_panel, idx, pred, *, label_col):
        return pd.DataFrame(
            {
                "stock_code": [f"60000{i}" for i in idx],
                "signal_date": ["2025-01-02"] * len(idx),
                "score": list(pred),
                label_col: [0.01] * len(idx),
            }
        )

    def _fake_evaluate_predictions(df, *, label_col):
        return {"rank_ic": 0.04 if len(df) == 2 else 0.033, "ndcg5": 0.5}

    def _fail_prediction_output(*_args, **_kwargs):
        raise AssertionError("train-log-only should not build persisted prediction rows")

    monkeypatch.setattr(retrain_module, "_fit_lambdamart_window_model", lambda *_args, **_kwargs: _Model())
    monkeypatch.setattr(retrain_module, "_prediction_frame", _fake_prediction_frame)
    monkeypatch.setattr(retrain_module, "evaluate_predictions", _fake_evaluate_predictions)
    monkeypatch.setattr(retrain_module, "_prediction_output_frame", _fail_prediction_output)

    predictions, train_log = materialize_best_predictions_with_train_log(
        panel=_Panel(),
        windows=[_Window()],
        params={},
        label_col="fwd_cost_after_20d",
        model_id="lambdamart_v6_unit",
        feature_version="p0a_v4",
        label_version="horizon_governance_v1",
        built_at="2026-05-21T00:00:00+00:00",
        seed=42,
        n_trials=50,
        optuna_best_value=0.42,
        include_predictions=False,
    )

    assert predictions.empty
    assert train_log["n_windows"] == 1
    assert train_log["n_train_rows"] == 2
    assert train_log["n_features"] == 2
    assert train_log["is_rank_ic"] == 0.04
    assert train_log["oos_rank_ic_avg"] == 0.033
