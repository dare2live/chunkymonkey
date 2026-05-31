import pytest

from conftest import duck_mem
from scripts.build_executive_trade_events import (
    _apply_forward_returns,
    _load_prices_for_codes,
    aggregate_events,
    normalize,
    write_fact,
    write_raw,
)


def test_normalize_filters_and_maps_akshare_records():
    rows = [
        {
            "代码": "1",
            "名称": "平安银行",
            "股东名称": "张三",
            "持股变动信息-增减": "增持",
            "持股变动信息-变动数量": 10.0,
            "持股变动信息-占总股本比例": 0.01,
            "公告日": "2026-04-28",
        },
        {
            "代码": "2",
            "持股变动信息-增减": "不变",
            "公告日": "2026-04-28",
        },
    ]

    normalized = normalize(rows)

    assert len(normalized) == 1
    assert normalized[0]["stock_code"] == "000001"
    assert normalized[0]["direction"] == "增持"
    assert normalized[0]["notice_date"] == "2026-04-28"


def test_aggregate_events_groups_by_notice_stock_direction():
    rows = [
        {
            "notice_date": "2026-04-28",
            "stock_code": "000001",
            "shareholder_name": "张三",
            "direction": "增持",
            "change_qty_wan": 10.0,
            "change_pct_total": 0.01,
        },
        {
            "notice_date": "2026-04-28",
            "stock_code": "000001",
            "shareholder_name": "某某投资有限公司",
            "direction": "增持",
            "change_qty_wan": 20.0,
            "change_pct_total": 0.03,
        },
    ]

    events = aggregate_events(rows)

    assert len(events) == 1
    event = events[0]
    assert event["direction"] == "buy"
    assert event["n_shareholders"] == 2
    assert event["total_change_qty_wan"] == pytest.approx(30.0)
    assert event["total_change_pct_total"] == pytest.approx(0.04)
    assert event["max_change_pct_total"] == pytest.approx(0.03)
    assert event["any_individual"] == 1
    assert event["any_corporate"] == 1


def test_apply_forward_returns_uses_notice_date_entry():
    events = [{"notice_date": "2026-04-28", "stock_code": "000001"}]
    prices = [
        {"code": "000001", "date": "2026-04-28", "close": 9.0},
        {"code": "000001", "date": "2026-04-29", "close": 10.0},
        {"code": "000001", "date": "2026-04-30", "close": 9.0},
        {"code": "000001", "date": "2026-05-06", "close": 12.0},
    ]

    enriched = _apply_forward_returns(events, prices)

    assert enriched[0]["gain_20d"] == pytest.approx(0.2)
    assert enriched[0]["max_drawdown_20d"] == pytest.approx(-0.1)


def test_apply_forward_returns_indexes_unsorted_prices_per_stock():
    events = [
        {"notice_date": "2026-04-28", "stock_code": "000001"},
        {"notice_date": "2026-04-30", "stock_code": "000001"},
    ]
    prices = [
        {"code": "000001", "date": "2026-05-07", "close": 13.0},
        {"code": "000001", "date": "2026-04-28", "close": 9.0},
        {"code": "000001", "date": "2026-05-06", "close": 12.0},
        {"code": "000001", "date": "2026-04-30", "close": 9.0},
        {"code": "000001", "date": "2026-04-29", "close": 10.0},
    ]

    enriched = _apply_forward_returns(events, prices)

    assert enriched[0]["gain_20d"] == pytest.approx(0.3)
    assert enriched[0]["max_drawdown_20d"] == pytest.approx(-0.1)
    assert enriched[1]["gain_20d"] == pytest.approx(13.0 / 12.0 - 1)
    assert enriched[1]["max_drawdown_20d"] == pytest.approx(13.0 / 12.0 - 1)


def test_load_prices_for_codes_uses_bulk_code_join(monkeypatch):
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE test_kline (
                code TEXT,
                date TEXT,
                close DOUBLE,
                freq TEXT,
                adjust TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO test_kline VALUES (?, ?, ?, ?, ?)",
            [
                ("000001", "2026-04-30", 9.0, "daily", "qfq"),
                ("000001", "2026-04-29", 10.0, "daily", "qfq"),
                ("000001", "2026-04-28", 8.0, "weekly", "qfq"),
                ("000002", "2026-04-29", 20.0, "daily", "hfq"),
                ("000002", "2026-04-30", 21.0, "daily", "qfq"),
                ("000003", "2026-04-30", 31.0, "daily", "qfq"),
            ],
        )
        monkeypatch.setattr("scripts.build_executive_trade_events.KLINE_DAILY_QFQ_RELATION", "test_kline")

        rows = _load_prices_for_codes(conn, ["000002", "000001", "000001"])

        assert [(row["code"], row["date"], row["close"]) for row in rows] == [
            ("000001", "2026-04-29", 10.0),
            ("000001", "2026-04-30", 9.0),
            ("000002", "2026-04-30", 21.0),
        ]
    finally:
        conn.close()


def test_write_raw_and_fact_use_duckdb_executemany_paths():
    conn = duck_mem()
    try:
        raw = [
            {
                "notice_date": "2026-04-28",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "shareholder_name": "张三",
                "direction": "增持",
                "change_qty_wan": 10.0,
                "change_pct_total": 0.01,
                "change_pct_float": 0.02,
                "after_qty_wan": 100.0,
                "after_pct_total": 0.1,
                "start_date": "2026-04-01",
                "end_date": "2026-04-20",
            }
        ]
        fact = [
            {
                "notice_date": "2026-04-28",
                "stock_code": "000001",
                "direction": "buy",
                "n_shareholders": 1,
                "total_change_qty_wan": 10.0,
                "total_change_pct_total": 0.01,
                "max_change_pct_total": 0.01,
                "any_individual": 1,
                "any_corporate": 0,
                "gain_20d": 0.1,
                "gain_60d": 0.2,
                "max_drawdown_20d": -0.03,
                "max_drawdown_60d": -0.05,
            }
        ]

        write_raw(conn, raw)
        write_fact(conn, fact)

        raw_row = conn.execute("SELECT stock_code, direction FROM raw_executive_trade").fetchone()
        fact_row = conn.execute(
            "SELECT stock_code, direction, gain_20d FROM fact_executive_trade_event"
        ).fetchone()

        assert raw_row["stock_code"] == "000001"
        assert raw_row["direction"] == "增持"
        assert fact_row["stock_code"] == "000001"
        assert fact_row["direction"] == "buy"
        assert fact_row["gain_20d"] == pytest.approx(0.1)
    finally:
        conn.close()
