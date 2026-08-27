"""Fuyao dump vs accepted nominal K: set difference, banned baseline, dump probe."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conftest import duck_mem
from services.data_sources.fuyao_kline_recon import (
    ACCEPTED_K_TABLE,
    BANNED_BASELINE_TABLES,
    compare_events,
    compare_kline,
    dump_catalog_status,
    load_fuyao_events,
    load_fuyao_kline,
    probe_dump_kinds,
    reject_banned_baseline,
    shanghai_date_sql,
)

SH = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 20)


def _day_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=SH).timestamp() * 1000)


def _write_kline_parquet(path: Path, rows: list[tuple]) -> None:
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE src (
            thscode VARCHAR,
            currency VARCHAR,
            interval VARCHAR,
            adjusted VARCHAR,
            date_ms BIGINT,
            open_price DOUBLE,
            high_price DOUBLE,
            low_price DOUBLE,
            close_price DOUBLE,
            volume DOUBLE,
            turnover DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO src VALUES (?, 'CNY', '1d', 'none', ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.execute(f"COPY src TO '{path}' (FORMAT PARQUET)")
    con.close()


def _write_events_parquet(path: Path, rows: list[tuple]) -> None:
    con = duck_mem()
    con.execute(
        """
        CREATE TABLE src (
            thscode VARCHAR,
            ticker VARCHAR,
            ex_date_ms BIGINT,
            dividend_per_share DOUBLE,
            per_share_bonus DOUBLE,
            allotment_ratio DOUBLE,
            allotment_price DOUBLE,
            currency VARCHAR
        )
        """
    )
    con.executemany(
        "INSERT INTO src VALUES (?, ?, ?, ?, 0, 0, 0, 'CNY')",
        rows,
    )
    con.execute(f"COPY src TO '{path}' (FORMAT PARQUET)")
    con.close()


def test_banned_raw_tushare_daily_cannot_be_baseline():
    assert "raw_tushare_daily" in BANNED_BASELINE_TABLES
    with pytest.raises(ValueError, match="banned baseline"):
        reject_banned_baseline("tr.raw_tushare_daily")
    assert reject_banned_baseline(ACCEPTED_K_TABLE) == ACCEPTED_K_TABLE


def test_one_kind_http_404_is_not_catalog_offline():
    def sign(kind: str):
        if kind == "daily-k":
            raise RuntimeError("download HTTP 404: missing")
        return {"presigned_url": "https://example.test/x.parquet"}

    probes = probe_dump_kinds(sign)
    by_kind = {p.kind: p.outcome for p in probes}
    assert by_kind["daily-k"] == "http_404"
    assert by_kind["daily-k-10d"] == "ok"
    assert by_kind["adjustment-factors"] == "ok"
    assert dump_catalog_status(probes) == "partial_or_ready"

    all_ok = probe_dump_kinds(lambda _kind: {"presigned_url": "https://example.test/x.parquet"})
    assert dump_catalog_status(all_ok) == "ready"


def test_shanghai_date_sql_roundtrip(tmp_path: Path):
    parquet = tmp_path / "k.parquet"
    ms = _day_ms(DAY)
    _write_kline_parquet(
        parquet,
        [("000001.SZ", ms, 10.0, 10.1, 9.9, 10.05, 10000.0, 100500.0)],
    )
    con = duck_mem()
    load_fuyao_kline(con, parquet)
    row = con.execute("SELECT trade_date FROM fuyao_k").fetchone()
    assert str(row[0]) == "2026-08-20"
    expr = shanghai_date_sql("date_ms")
    assert "28800000" in expr


def test_kline_set_difference_and_ohlc_tolerance(tmp_path: Path):
    parquet = tmp_path / "k.parquet"
    ms = _day_ms(DAY)
    _write_kline_parquet(
        parquet,
        [
            ("000001.SZ", ms, 10.0, 10.2, 9.8, 10.1, 100_000.0, 1_010_000.0),
            ("600519.SH", ms, 1400.0, 1410.0, 1390.0, 1405.05, 50_000.0, 70_000_000.0),
            ("830001.BJ", ms, 5.0, 5.1, 4.9, 5.0, 10_000.0, 50_000.0),
        ],
    )
    con = duck_mem()
    load_fuyao_kline(con, parquet)
    con.execute(
        f"""
        CREATE TABLE {ACCEPTED_K_TABLE} (
            ts_code VARCHAR,
            trade_date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            vol DOUBLE, amount DOUBLE
        )
        """
    )
    con.executemany(
        f"INSERT INTO {ACCEPTED_K_TABLE} VALUES (?, DATE '2026-08-20', ?, ?, ?, ?, ?, ?)",
        [
            ("000001.SZ", 10.0, 10.2, 9.8, 10.1, 1000.0, 1010.0),
            ("600519.SH", 1400.0, 1410.0, 1390.0, 1400.00, 500.0, 70000.0),
            ("600000.SH", 8.0, 8.1, 7.9, 8.0, 100.0, 80.0),
        ],
    )
    report = compare_kline(con)
    assert report["intersection"] == 2
    assert report["only_fuyao"] == 1
    assert report["only_accepted"] == 1
    assert report["by_date"][0]["trade_date"] == "2026-08-20"
    prefixes = {row["code_prefix"]: row for row in report["by_prefix"]}
    assert prefixes["830.BJ"]["only_fuyao"] == 1
    assert prefixes["600.SH"]["only_accepted"] == 1
    assert report["ohlc_match"] == 1
    assert report["ohlc_mismatch"] == 1
    assert report["vol_scale_hypothesis"] == "confirmed"
    assert report["amount_scale_hypothesis"] == "confirmed"
    assert report["vol_mismatch_after_scale"] == 0
    assert report["ohlc_mismatch_samples"][0]["ts_code"] == "600519.SH"


def test_compare_kline_rejects_stopped_raw_daily():
    con = duck_mem()
    con.execute("CREATE TABLE fuyao_k AS SELECT '000001.SZ' AS ts_code, DATE '2026-08-20' AS trade_date, 1.0 AS open, 1.0 AS high, 1.0 AS low, 1.0 AS close, 1.0 AS volume_share, 1.0 AS turnover_cny")
    con.execute("CREATE TABLE raw_tushare_daily AS SELECT * FROM fuyao_k")
    with pytest.raises(ValueError, match="banned baseline"):
        compare_kline(con, accepted_table="raw_tushare_daily")


def test_events_match_implemented_dividend_and_adj_jump(tmp_path: Path):
    parquet = tmp_path / "e.parquet"
    ex = date(2026, 6, 12)
    _write_events_parquet(
        parquet,
        [
            ("000001.SZ", "000001", _day_ms(ex), 0.10),
            ("000002.SZ", "000002", _day_ms(ex), 0.20),
        ],
    )
    con = duck_mem()
    load_fuyao_events(con, parquet)
    con.execute(
        """
        CREATE TABLE raw_tushare_dividend (
            ts_code VARCHAR, div_proc VARCHAR, ex_date VARCHAR,
            cash_div DOUBLE, cash_div_tax DOUBLE, stk_div DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO raw_tushare_dividend VALUES (?, ?, ?, ?, ?, 0)",
        [
            ("000001.SZ", "实施", "20260612", 0.09, 0.10),
            ("600000.SH", "实施", "20260612", 0.05, 0.05),
            ("000001.SZ", "预案", "20260612", 0.10, 0.10),
        ],
    )
    con.execute(
        """
        CREATE TABLE raw_tushare_adj_factor (
            ts_code VARCHAR, trade_date VARCHAR, adj_factor DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO raw_tushare_adj_factor VALUES (?, ?, ?)",
        [
            ("000001.SZ", "20260611", 1.0),
            ("000001.SZ", "20260612", 1.1),
            ("000002.SZ", "20260611", 1.0),
            ("000002.SZ", "20260612", 1.0),
        ],
    )
    report = compare_events(con)
    assert report["matched_ex_date"] == 1
    assert report["only_fuyao"] == 1
    assert report["only_dividend"] == 1
    assert report["cash_div_mismatch"] == 1
    assert report["cash_div_tax_mismatch"] == 0
    assert report["jumps_with_fuyao_event"] == 1
    assert report["fuyao_events_without_jump"] == 1
