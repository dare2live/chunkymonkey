from __future__ import annotations

from pathlib import Path

from services.duck_adapter import connect as duck_connect
from services.northbound_market_flow import latest_northbound_market_flow


def test_northbound_missing_db(tmp_path: Path) -> None:
    out = latest_northbound_market_flow(raw_db=tmp_path / "missing.duckdb")
    assert out["status"] == "unavailable"
    assert out["grain"] == "market_day"
    assert out["north_money"] is None


def test_northbound_reads_latest_market_row(tmp_path: Path) -> None:
    path = tmp_path / "tushare_raw.duckdb"
    con = duck_connect(str(path), read_only=False)
    con.execute(
        """
        CREATE TABLE raw_tushare_moneyflow_hsgt (
            trade_date VARCHAR, ggt_ss VARCHAR, ggt_sz VARCHAR,
            hgt VARCHAR, sgt VARCHAR, north_money VARCHAR, south_money VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO raw_tushare_moneyflow_hsgt VALUES "
        "('20260819','1','1','10','20','100','50'),"
        "('20260820','1','1','11','21','200.5','60')"
    )
    con.close()
    out = latest_northbound_market_flow(raw_db=path)
    assert out["status"] == "ok"
    assert out["trade_date"] == "20260820"
    assert out["north_money"] == 200.5
    assert out["grain"] == "market_day"
