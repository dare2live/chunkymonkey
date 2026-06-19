import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import build_akshare_panel as subject


def test_date_range_uses_stdlib_timedelta():
    assert subject._date_range("20260130", "20260202") == [
        "20260130",
        "20260131",
        "20260201",
        "20260202",
    ]


def test_build_hot_rank_daily_accepts_record_payload(monkeypatch):
    conn = duck_mem()
    try:
        monkeypatch.setattr(
            subject.ak,
            "stock_hot_rank_em",
            lambda: [
                {"当前排名": 1, "代码": "1", "股票名称": "Alpha", "最新价": 10.5},
                {"当前排名": 2, "代码": "000001", "股票名称": "Alpha Dup", "最新价": 10.6},
                {"当前排名": 3, "代码": "2", "股票名称": "Beta", "最新价": 11.0},
            ],
        )

        inserted = subject.build_hot_rank_daily(conn)
        rows = conn.execute(
            "SELECT stock_code, stock_name, rank_value FROM fact_hot_rank_daily ORDER BY stock_code"
        ).fetchall()

        assert inserted == 2
        assert rows[0]["stock_code"] == "000001"
        assert rows[0]["stock_name"] == "Alpha"
        assert rows[0]["rank_value"] == 1
        assert rows[1]["stock_code"] == "000002"
    finally:
        conn.close()


