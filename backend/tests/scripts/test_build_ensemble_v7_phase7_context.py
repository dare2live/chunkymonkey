from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import duck_mem


def _load_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "build_ensemble_v7_phase7_context.py"
    spec = importlib.util.spec_from_file_location("build_ensemble_v7_phase7_context", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


subject = _load_script()


def _seed_kline(conn) -> None:
    conn.execute("CREATE SCHEMA market")
    conn.execute(
        """
        CREATE TABLE market.v_price_kline_qfq (
            code TEXT,
            date DATE,
            close DOUBLE,
            freq TEXT,
            adjust TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO market.v_price_kline_qfq VALUES (?, ?, ?, ?, ?)",
        [
            ("000001", "2023-06-30", 9.0, "daily", "qfq"),
            ("000001", "2023-07-01", 10.0, "daily", "qfq"),
            ("000001", "2023-07-02", 11.0, "weekly", "qfq"),
            ("000001", "2023-07-03", 12.0, "daily", "hfq"),
            ("000002", "2023-07-01", 20.0, "daily", "qfq"),
            ("000003", "2023-07-01", 30.0, "daily", "qfq"),
        ],
    )


def test_load_kline_frame_bulk_filters_requested_codes_and_daily_qfq():
    conn = duck_mem()
    try:
        _seed_kline(conn)

        frame = subject._load_kline_frame(conn, ["000002", "000001", "000001"])

        assert list(frame.columns) == ["stock_code", "date", "close"]
        assert frame[["stock_code", "close"]].to_records(index=False).tolist() == [
            ("000001", 10.0),
            ("000002", 20.0),
        ]
    finally:
        conn.close()


def test_build_contexts_skips_short_or_null_price_histories():
    long_history = pd.DataFrame(
        {
            "stock_code": ["000001"] * 31,
            "date": pd.date_range("2024-01-01", periods=31, freq="D"),
            "close": np.linspace(10.0, 13.0, 31),
        }
    )
    short_history = pd.DataFrame(
        {
            "stock_code": ["000002"] * 29,
            "date": pd.date_range("2024-01-01", periods=29, freq="D"),
            "close": [None] * 29,
        }
    )

    contexts = subject._build_contexts(pd.concat([long_history, short_history], ignore_index=True))

    assert ("000001", pd.Timestamp("2024-01-02")) in contexts
    assert all(stock != "000002" for stock, _date in contexts)


def test_apply_context_filter_preserves_scores_only_for_positive_contexts():
    frame = pd.DataFrame(
        {
            "stock_code": ["000001", "000002", "000003"],
            "signal_date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-02"]),
            "v7_score": [0.9, 0.8, 0.7],
        }
    )
    contexts = {
        ("000001", pd.Timestamp("2024-01-02")): "below_zero_rebound_probe",
        ("000002", pd.Timestamp("2024-01-02")): "dead_cross",
    }

    out = subject._apply_context_filter(frame, contexts)

    assert out["ctx"].tolist()[:2] == ["below_zero_rebound_probe", "dead_cross"]
    assert pd.isna(out["ctx"].tolist()[2])
    assert out["score_out"].tolist()[0] == pytest.approx(0.9)
    assert pd.isna(out["score_out"].tolist()[1])
    assert pd.isna(out["score_out"].tolist()[2])
