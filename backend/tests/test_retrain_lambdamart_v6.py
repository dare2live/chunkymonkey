from __future__ import annotations

import pandas as pd

from conftest import duck_mem
from scripts.retrain_lambdamart_v6 import (
    complete_lambdamart_params,
    make_model_id,
    persist_predictions,
    register_lambdamart_v6_asset,
)
from services.ml_ranking.ddl import (
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
