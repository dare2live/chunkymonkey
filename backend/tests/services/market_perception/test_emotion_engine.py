from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb

from services.market_perception.emotion_engine import compute_emotion_for_date, compute_emotion_for_range


def _make_conn(snapshot: date, *, up_ratio: float, limit_up: int, limit_down: int):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    conn.execute(
        """
        CREATE TABLE fact_stock_kline_daily (
            trade_date TEXT,
            stock_code TEXT,
            pct_change DOUBLE,
            amount DOUBLE
        )
        """
    )
    conn.execute("CREATE TABLE fact_lhb_event (trade_date TEXT, stock_code TEXT, built_at TEXT)")
    days = [snapshot - timedelta(days=i) for i in range(5)]
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)", [(d.isoformat(),) for d in days])
    for d in days:
        rows = []
        up_count = int(round(up_ratio * 100))
        down_count = 100 - up_count
        for i in range(up_count):
            pct = 9.8 if i < limit_up and d == snapshot else 1.0
            rows.append((d.isoformat(), f"{i:06d}", pct, 1000.0 + i))
        for i in range(down_count):
            pct = -9.8 if i < limit_down and d == snapshot else -1.0
            rows.append((d.isoformat(), f"{i + up_count:06d}", pct, 500.0 + i))
        conn.executemany("INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?, ?)", rows)
    return conn


def test_emotion_score_risk_on():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, up_ratio=0.75, limit_up=30, limit_down=2)

    row = compute_emotion_for_date(conn, snapshot)

    assert row["emotion_score"] > 0.3
    assert row["emotion_state"] == "赚钱效应扩张"
    assert row["action_bias"] == "追强有效"


def test_emotion_score_risk_off():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, up_ratio=0.25, limit_up=2, limit_down=30)

    row = compute_emotion_for_date(conn, snapshot)

    assert row["emotion_score"] < -0.3
    assert row["emotion_state"] == "亏钱效应扩散"
    assert row["action_bias"] == "降低仓位"


def test_unavailable_limit_ecology_fields_are_unknown_not_zero():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, up_ratio=0.55, limit_up=8, limit_down=4)

    row = compute_emotion_for_date(conn, snapshot)

    unknown = set(json.loads(row["unknown_metrics"]))
    for field in [
        "first_board_count",
        "second_board_count",
        "third_plus_count",
        "promotion_rate_1_to_2",
        "promotion_rate_2_to_3",
        "open_board_rate",
        "next_day_premium",
    ]:
        assert row[field] is None
        assert field in unknown


def test_emotion_lhb_no_lookahead_in_range():
    snapshot = date(2026, 1, 15)
    conn = _make_conn(snapshot, up_ratio=0.55, limit_up=8, limit_down=4)
    conn.execute("INSERT INTO fact_lhb_event VALUES (?, '000001', ?)", [snapshot.isoformat(), "2026-01-15 18:00:00"])
    conn.execute("INSERT INTO fact_lhb_event VALUES (?, '000002', ?)", [snapshot.isoformat(), "2026-01-16 09:00:00"])

    frame = compute_emotion_for_range(conn, snapshot - timedelta(days=1), snapshot)

    assert frame.iloc[-1]["snapshot_date"] == snapshot.isoformat()
    assert int(frame.iloc[-1]["lhb_event_count"]) == 1
