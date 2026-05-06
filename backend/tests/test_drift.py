import math
from datetime import date, timedelta

import pytest

from conftest import duck_mem
from services.ml_lifecycle import drift as subject
from services.ml_lifecycle.drift import compute_psi, severity_for_psi


class _NoCloseConn:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


def test_compute_psi_filters_non_finite_values():
    train = [1, 2, 3, 4, 5, math.nan, math.inf, None]
    recent = [1, 2, 3, 4, 5, None, -math.inf]

    psi, n_train, n_recent = compute_psi(train, recent, n_bins=5)

    assert n_train == 5
    assert n_recent == 5
    assert psi < 1e-8


def test_compute_psi_detects_shifted_distribution():
    train = list(range(1, 101))
    recent = list(range(51, 151))

    psi, n_train, n_recent = compute_psi(train, recent, n_bins=10)

    assert n_train == 100
    assert n_recent == 100
    assert severity_for_psi(psi) == "critical"


def test_compute_feature_drift_filters_candidate_feature_set(monkeypatch: pytest.MonkeyPatch):
    conn = duck_mem()
    today = date.today()
    train_day = (today - timedelta(days=60)).isoformat()
    recent_day = (today - timedelta(days=5)).isoformat()
    conn.execute("CREATE TABLE fact_feature_panel_candidate (feature_set_id TEXT, date TEXT, signal DOUBLE)")
    rows = []
    for idx in range(20):
        value = float(idx + 1)
        rows.append(("set_a", train_day, value))
        rows.append(("set_a", recent_day, value))
        rows.append(("set_b", train_day, value))
        rows.append(("set_b", recent_day, value + 100.0))
    conn.executemany("INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?)", rows)
    monkeypatch.setattr(subject, "get_conn", lambda: _NoCloseConn(conn))

    result = subject.compute_feature_drift(
        feature_table="fact_feature_panel_candidate",
        feature_set_id="set_a",
        feature_columns=["signal"],
        train_window_days=90,
        recent_window_days=30,
        model_id="model_a",
    )

    assert result[0]["n_train"] == 20
    assert result[0]["n_recent"] == 20
    assert result[0]["severity"] == "ok"
    assert result[0]["feature_set_id"] == "set_a"


def test_compute_feature_drift_defaults_to_latest_available_feature_date(monkeypatch: pytest.MonkeyPatch):
    conn = duck_mem()
    conn.execute("CREATE TABLE fact_feature_panel_candidate (feature_set_id TEXT, date TEXT, signal DOUBLE)")
    rows = []
    for idx in range(20):
        rows.append(("set_a", "2025-09-15", float(idx + 1)))
        rows.append(("set_a", "2025-12-31", float(idx + 1)))
    conn.executemany("INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?)", rows)
    monkeypatch.setattr(subject, "get_conn", lambda: _NoCloseConn(conn))

    result = subject.compute_feature_drift(
        feature_table="fact_feature_panel_candidate",
        feature_set_id="set_a",
        feature_columns=["signal"],
        train_window_days=120,
        recent_window_days=30,
        model_id="model_a",
    )

    assert result[0]["as_of_date"] == "2025-12-31"
    assert result[0]["n_train"] == 20
    assert result[0]["n_recent"] == 20
    assert result[0]["severity"] == "ok"


def test_write_drift_snapshot_persists_feature_set_id(monkeypatch: pytest.MonkeyPatch):
    conn = duck_mem()
    monkeypatch.setattr(subject, "get_conn", lambda: _NoCloseConn(conn))

    written = subject.write_drift_snapshot(
        [
            {
                "model_id": "model_a",
                "feature_set_id": "set_a",
                "feature": "signal",
                "psi": 0.01,
                "n_train": 20,
                "n_recent": 20,
                "severity": "ok",
            }
        ],
        snapshot_at="2026-05-06 10:00:00",
        window_days=30,
    )

    assert written == 1
    row = conn.execute("SELECT feature_set_id FROM mart_feature_drift").fetchone()
    assert row["feature_set_id"] == "set_a"


def test_histogram_cache_bucket_version_includes_feature_set(monkeypatch: pytest.MonkeyPatch):
    conn = duck_mem()
    today = date.today()
    train_day = (today - timedelta(days=60)).isoformat()
    recent_day = (today - timedelta(days=5)).isoformat()
    conn.execute("CREATE TABLE fact_feature_panel_candidate (feature_set_id TEXT, date TEXT, signal DOUBLE)")
    rows = []
    for idx in range(20):
        value = float(idx + 1)
        rows.append(("set_a", train_day, value))
        rows.append(("set_a", recent_day, value))
    conn.executemany("INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?)", rows)
    monkeypatch.setattr(subject, "get_conn", lambda: _NoCloseConn(conn))

    subject.compute_feature_drift_with_histogram_cache(
        feature_table="fact_feature_panel_candidate",
        feature_set_id="set_a",
        feature_columns=["signal"],
        train_window_days=90,
        recent_window_days=30,
        model_id="model_a",
    )

    row = conn.execute(
        "SELECT DISTINCT bucket_version FROM mart_feature_drift_histogram"
    ).fetchone()
    assert "feature_set=set_a" in row["bucket_version"]


def test_histogram_cache_handles_sparse_count_features(monkeypatch: pytest.MonkeyPatch):
    conn = duck_mem()
    conn.execute("CREATE TABLE fact_feature_panel_candidate (feature_set_id TEXT, date TEXT, sparse_count DOUBLE)")
    rows = []
    for idx in range(120):
        rows.append(("set_a", "2025-09-15", 0.0 if idx < 110 else float(idx % 4 + 1)))
        rows.append(("set_a", "2025-12-31", 0.0 if idx < 100 else float(idx % 4 + 1)))
    conn.executemany("INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?)", rows)
    monkeypatch.setattr(subject, "get_conn", lambda: _NoCloseConn(conn))

    result = subject.compute_feature_drift_with_histogram_cache(
        feature_table="fact_feature_panel_candidate",
        feature_set_id="set_a",
        feature_columns=["sparse_count"],
        train_window_days=120,
        recent_window_days=30,
        model_id="model_a",
    )

    assert result[0]["as_of_date"] == "2025-12-31"
    assert result[0]["n_train"] == 120
    assert result[0]["n_recent"] == 120
    assert result[0]["severity"] != "unknown"
