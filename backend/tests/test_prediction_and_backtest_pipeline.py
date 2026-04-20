import sqlite3
import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import backtest_engine
from services.qlib_full_engine import _calc_topk_avg_return, _normalize_predictions


class _Recorder:
    def __init__(self, pred, label):
        self._pred = pred
        self._label = label

    def load_object(self, name):
        if name == "pred.pkl":
            return self._pred
        if name == "label.pkl":
            return self._label
        raise KeyError(name)


def test_normalize_predictions_accepts_multiindex_series_and_drops_bad_values():
    index = pd.MultiIndex.from_tuples(
        [
            ("2026-04-10", "SH600000"),
            ("2026-04-10", "SZ000001"),
        ],
        names=["datetime", "instrument"],
    )
    pred = pd.Series([0.25, "bad"], index=index)

    normalized = _normalize_predictions(pred)

    assert list(normalized.columns) == ["datetime", "instrument", "qlib_score"]
    assert len(normalized) == 1
    assert normalized.iloc[0]["instrument"] == "SH600000"
    assert normalized.iloc[0]["qlib_score"] == 0.25


def test_calc_topk_avg_return_averages_top_bucket_per_day():
    index = pd.MultiIndex.from_tuples(
        [
            ("2026-04-10", "SH600000"),
            ("2026-04-10", "SZ000001"),
            ("2026-04-11", "SH600000"),
            ("2026-04-11", "SZ000001"),
        ],
        names=["datetime", "instrument"],
    )
    pred = pd.DataFrame({"score": [0.90, 0.10, 0.20, 0.80]}, index=index)
    label = pd.DataFrame({"label": [0.05, -0.02, -0.01, 0.03]}, index=index)

    result = _calc_topk_avg_return(_Recorder(pred, label), topk=1)

    assert result == 0.04


def test_run_full_backtest_aggregates_all_sections(monkeypatch):
    monkeypatch.setattr(backtest_engine, "build_inst_industry_performance", lambda conn: {"rows": 11})
    monkeypatch.setattr(backtest_engine, "build_holding_chains", lambda conn: {"rows": 7})
    monkeypatch.setattr(backtest_engine, "build_cross_factor_analysis", lambda conn: {"rows": 3})
    monkeypatch.setattr(backtest_engine, "build_signal_transfer", lambda conn: {"rows": 2})

    result = backtest_engine.run_full_backtest(sqlite3.connect(":memory:"), sqlite3.connect(":memory:"))

    assert result == {
        "inst_industry": {"rows": 11},
        "holding_chains": {"rows": 7},
        "cross_factor": {"rows": 3},
        "signal_transfer": {"rows": 2},
    }
