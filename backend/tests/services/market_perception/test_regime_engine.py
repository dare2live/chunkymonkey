from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.market_perception.regime_engine import (
    compute_regime_for_date,
    compute_regime_for_range,
    get_regime_config,
    get_regime_source_max_date,
)


def _make_conn(snapshot: date, *, trend: float, breadth_ratio: float, prior_breadth: float):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    conn.execute("CREATE TABLE mart_index_daily (trade_date TEXT, index_code TEXT, close DOUBLE)")
    conn.execute("CREATE TABLE fact_stock_kline_daily (trade_date TEXT, stock_code TEXT, pct_change DOUBLE)")
    conn.execute("CREATE TABLE fact_lhb_event (trade_date TEXT, stock_code TEXT, built_at TEXT)")

    days = [snapshot - timedelta(days=i) for i in range(130)]
    days = sorted(days)
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)", [(d.isoformat(),) for d in days])

    start_close = 100.0
    for d in days[:-61]:
        conn.execute("INSERT INTO mart_index_daily VALUES (?, '000300', ?)", [d.isoformat(), start_close])
    for i, d in enumerate(days[-61:]):
        close = start_close * (1.0 + trend * i / 60.0)
        conn.execute("INSERT INTO mart_index_daily VALUES (?, '000300', ?)", [d.isoformat(), close])

    for d in days[-100:]:
        ratio = breadth_ratio if d == snapshot else prior_breadth
        up = int(round(ratio * 20))
        rows = []
        for i in range(20):
            pct = 1.0 if i < up else -1.0
            rows.append((d.isoformat(), f"{i:06d}", pct))
        conn.executemany("INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?)", rows)
    return conn


def test_regime_score_risk_on():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=0.20, breadth_ratio=0.70, prior_breadth=0.50)

    row = compute_regime_for_date(conn, snapshot)

    assert row["regime_score"] > 0.3
    assert row["breadth_state"] == "健康扩散"


def test_regime_score_risk_off():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=-0.15, breadth_ratio=0.25, prior_breadth=0.60)

    row = compute_regime_for_date(conn, snapshot)

    assert row["regime_score"] < -0.3
    assert row["breadth_state"] == "杀跌"


def test_pit_strict_today_excluded():
    today = date.today()
    conn = _make_conn(today, trend=0.05, breadth_ratio=0.50, prior_breadth=0.50)

    with pytest.raises(ValueError, match="must be earlier than today"):
        compute_regime_for_date(conn, today)


def test_no_lookahead():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=0.05, breadth_ratio=0.55, prior_breadth=0.50)
    conn.execute("INSERT INTO fact_lhb_event VALUES (?, '000001', ?)", [snapshot.isoformat(), "2026-01-15 18:00:00"])
    conn.execute("INSERT INTO fact_lhb_event VALUES (?, '000002', ?)", [snapshot.isoformat(), "2026-01-16 09:00:00"])

    row = compute_regime_for_date(conn, snapshot)

    assert row["lhb_event_count"] == 1


def test_regime_config_loaded_from_yaml():
    cfg = get_regime_config()

    assert cfg.hs300_code == "000300"
    assert cfg.ret_days == 60
    assert cfg.vol_days == 20
    assert cfg.breadth_p75_days == 90
    assert cfg.regime_score_abs_max == 0.95


def test_range_matches_per_date_results():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=0.12, breadth_ratio=0.58, prior_breadth=0.50)
    conn.execute("INSERT INTO fact_lhb_event VALUES (?, '000001', ?)", [snapshot.isoformat(), "2026-01-15 18:00:00"])
    conn.execute("INSERT INTO fact_lhb_event VALUES (?, '000002', ?)", [snapshot.isoformat(), "2026-01-16 09:00:00"])

    frame = compute_regime_for_range(conn, snapshot - timedelta(days=2), snapshot)
    expected = [compute_regime_for_date(conn, snapshot - timedelta(days=i)) for i in [2, 1, 0]]

    assert frame["snapshot_date"].tolist() == [row["snapshot_date"] for row in expected]
    assert frame["regime_score"].tolist() == [row["regime_score"] for row in expected]
    assert frame["breadth_state"].tolist() == [row["breadth_state"] for row in expected]
    assert frame["lhb_event_count"].tolist() == [row["lhb_event_count"] for row in expected]


def test_range_missing_hs300_fails_fast():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=0.12, breadth_ratio=0.58, prior_breadth=0.50)
    conn.execute("DELETE FROM mart_index_daily WHERE trade_date = ?", [snapshot.isoformat()])

    with pytest.raises(ValueError, match="HS300 row missing"):
        compute_regime_for_range(conn, snapshot - timedelta(days=2), snapshot)


def test_range_missing_breadth_fails_fast():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=0.12, breadth_ratio=0.58, prior_breadth=0.50)
    conn.execute("DELETE FROM fact_stock_kline_daily WHERE trade_date = ?", [snapshot.isoformat()])

    with pytest.raises(ValueError, match="breadth row missing"):
        compute_regime_for_range(conn, snapshot - timedelta(days=2), snapshot)


def test_source_max_date_uses_core_input_minimum():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, trend=0.12, breadth_ratio=0.58, prior_breadth=0.50)
    conn.execute("DELETE FROM mart_index_daily WHERE trade_date = ?", [snapshot.isoformat()])

    assert get_regime_source_max_date(conn) == snapshot - timedelta(days=1)
