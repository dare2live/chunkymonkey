import pytest

from conftest import duck_mem
from scripts.build_lhb_events import _apply_forward_returns, dedup_and_parse
from scripts.build_lhb_events import write_fact


def test_dedup_and_parse_aggregates_lhb_records():
    rows = [
        {
            "trade_date": "2026-04-28",
            "stock_code": "1",
            "rank_reason": "日涨幅偏离值达7%",
            "interpretation": "2家机构买入",
            "close_price": 10.0,
            "change_pct": 7.5,
            "net_buy": 100.0,
            "buy_amount": 200.0,
            "sell_amount": 100.0,
            "turnover": 300.0,
            "turnover_rate": 1.2,
            "float_cap": 10000.0,
            "net_buy_pct": 0.01,
        },
        {
            "trade_date": "2026-04-28",
            "stock_code": "000001",
            "rank_reason": "日换手率达20%",
            "interpretation": "1家机构买入",
            "close_price": 10.1,
            "change_pct": 8.0,
            "net_buy": 120.0,
            "buy_amount": 210.0,
            "sell_amount": 90.0,
            "turnover": 310.0,
            "turnover_rate": 1.3,
            "float_cap": 10001.0,
            "net_buy_pct": 0.02,
        },
    ]

    events = dedup_and_parse(rows)

    assert len(events) == 1
    event = events[0]
    assert event["stock_code"] == "000001"
    assert event["n_rank_reasons"] == 2
    assert event["rank_reasons"] == "日换手率达20%|日涨幅偏离值达7%"
    assert event["net_buy"] == 120.0
    assert event["buy_amount"] == 210.0
    assert event["inst_buy_seats"] == 2
    assert event["is_inst_net_buy"] == 1


def test_apply_forward_returns_uses_next_trading_day_entry():
    events = [{"trade_date": "2026-04-28", "stock_code": "000001"}]
    prices = [
        {"code": "000001", "date": "2026-04-28", "close": 9.0},
        {"code": "000001", "date": "2026-04-29", "close": 10.0},
        {"code": "000001", "date": "2026-04-30", "close": 9.5},
        {"code": "000001", "date": "2026-05-06", "close": 11.0},
    ]

    enriched = _apply_forward_returns(events, prices)

    assert enriched[0]["gain_20d"] == pytest.approx(0.1)
    assert enriched[0]["max_drawdown_20d"] == pytest.approx(-0.05)
    assert enriched[0]["gain_60d"] == pytest.approx(0.1)
    assert enriched[0]["max_drawdown_60d"] == pytest.approx(-0.05)


def test_write_fact_uses_duckdb_executemany_path():
    conn = duck_mem()
    try:
        write_fact(
            conn,
            [
                {
                    "trade_date": "2026-04-28",
                    "stock_code": "000001",
                    "n_rank_reasons": 1,
                    "rank_reasons": "日涨幅偏离值达7%",
                    "close_price": 10.0,
                    "change_pct": 7.5,
                    "net_buy": 100.0,
                    "buy_amount": 200.0,
                    "sell_amount": 100.0,
                    "turnover": 300.0,
                    "turnover_rate": 1.2,
                    "float_cap": 10000.0,
                    "net_buy_pct": 0.01,
                    "interpretation": "1家机构买入",
                    "inst_buy_seats": 1,
                    "is_inst_net_buy": 1,
                    "gain_20d": 0.1,
                    "gain_60d": 0.2,
                    "max_drawdown_20d": -0.03,
                    "max_drawdown_60d": -0.05,
                }
            ],
        )

        row = conn.execute(
            "SELECT stock_code, is_inst_net_buy, gain_20d FROM fact_lhb_event"
        ).fetchone()

        assert row["stock_code"] == "000001"
        assert row["is_inst_net_buy"] == 1
        assert row["gain_20d"] == pytest.approx(0.1)
    finally:
        conn.close()
