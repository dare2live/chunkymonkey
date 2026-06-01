import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_sources import resolve  # noqa: E402


def test_resolve_routes_lhb_daily_with_dates_to_date_bounded_aif10(monkeypatch) -> None:
    called = {}

    def fake_fetch_lhb_aif10(start_date: str, end_date: str):
        called["args"] = (start_date, end_date)
        return [
            {
                "SECURITY_CODE": "000090",
                "SECUCODE": "000090.SZ",
                "SECURITY_NAME_ABBR": "天健集团",
                "TRADE_DATE": "2026-05-29",
                "EXPLAIN": "1家机构买入",
                "CLOSE_PRICE": 3.85,
                "CHANGE_RATE": 10.0,
                "BILLBOARD_NET_AMT": 85534641.02,
                "BILLBOARD_BUY_AMT": 147772623.35,
                "BILLBOARD_SELL_AMT": 62237982.33,
                "BILLBOARD_DEAL_AMT": 210010605.68,
                "ACCUM_AMOUNT": 571537819.0,
                "DEAL_NET_RATIO": 14.965700987147,
                "DEAL_AMOUNT_RATIO": 36.744830997789,
                "TURNOVERRATE": 6.6575,
                "FREE_MARKET_CAP": 7193461020.9,
                "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
                "D1_CLOSE_ADJCHRATE": None,
                "D2_CLOSE_ADJCHRATE": None,
                "D5_CLOSE_ADJCHRATE": None,
                "D10_CLOSE_ADJCHRATE": None,
                "SECURITY_TYPE_CODE": "058001001",
            }
        ]

    monkeypatch.setattr("services.lhb_client._fetch_lhb_aif10", fake_fetch_lhb_aif10)

    data, source = resolve("lhb_daily", prefer_source="aif10", start_date="20260529", end_date="20260529")

    assert source == "aif10"
    assert called["args"] == ("20260529", "20260529")
    assert len(data) == 1
    assert data[0]["SECURITY_CODE"] == "000090"
    assert data[0]["TRADE_DATE"] == "2026-05-29"
