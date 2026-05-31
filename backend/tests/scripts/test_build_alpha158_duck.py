from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from scripts import build_alpha158_duck as subject


def _seed_market_db(path, *, end: date) -> None:
    start = date(2026, 1, 1)
    rows = []
    current = start
    idx = 0
    while current <= end:
        for offset, code in enumerate(("000001", "000002")):
            close = 10.0 + idx * 0.1 + offset
            rows.append(
                (
                    code,
                    current.isoformat(),
                    close - 0.1,
                    close + 0.2,
                    close - 0.3,
                    close,
                    1000.0 + idx + offset,
                    100000.0 + idx * 10 + offset,
                    "daily",
                    "qfq",
                )
            )
        current += timedelta(days=1)
        idx += 1

    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE price_kline_tdxhub (
                code TEXT,
                date TEXT,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                amount DOUBLE,
                freq TEXT,
                adjust TEXT
            )
            """
        )
        con.executemany("INSERT INTO price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        con.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")
    finally:
        con.close()


def _patch_latest_completed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    import services.market_db as market_db

    monkeypatch.setattr(market_db, "_latest_completed_trade_date_for_write", lambda: value)


def test_window_replacement_preserves_older_rows_and_replaces_dirty_window(tmp_path, monkeypatch):
    market_db = tmp_path / "market.duckdb"
    alpha_db = tmp_path / "alpha158.duckdb"
    _seed_market_db(market_db, end=date(2026, 5, 5))
    _patch_latest_completed(monkeypatch, "2026-05-05")

    subject.build(
        str(alpha_db),
        "2026-01-01",
        end_date="2026-05-02",
        replace_table=True,
        market_db=str(market_db),
    )

    con = duckdb.connect(str(alpha_db))
    try:
        con.execute(
            "UPDATE fact_alpha158_panel SET close = 777.0 WHERE stock_code = '000001' AND date = DATE '2026-02-01'"
        )
        con.execute(
            "UPDATE fact_alpha158_panel SET close = 999.0 WHERE stock_code = '000001' AND date = DATE '2026-05-01'"
        )
    finally:
        con.close()

    summary = subject.build(
        str(alpha_db),
        "2026-03-01",
        end_date="2026-05-05",
        write_start_date="2026-05-01",
        market_db=str(market_db),
    )

    con = duckdb.connect(str(alpha_db), read_only=True)
    try:
        preserved = con.execute(
            "SELECT close FROM fact_alpha158_panel WHERE stock_code = '000001' AND date = DATE '2026-02-01'"
        ).fetchone()[0]
        replaced = con.execute(
            "SELECT close FROM fact_alpha158_panel WHERE stock_code = '000001' AND date = DATE '2026-05-01'"
        ).fetchone()[0]
        max_date = con.execute("SELECT MAX(date) FROM fact_alpha158_panel").fetchone()[0]
        duplicates = con.execute(
            """
            SELECT COUNT(*)
              FROM (
                SELECT stock_code, date, COUNT(*) AS n
                  FROM fact_alpha158_panel
                 GROUP BY stock_code, date
                HAVING COUNT(*) > 1
              )
            """
        ).fetchone()[0]
    finally:
        con.close()

    assert summary["mode"] == "replace_window"
    assert summary["window_rows"] > 0
    assert preserved == 777.0
    assert replaced != 999.0
    assert str(max_date) == "2026-05-05"
    assert duplicates == 0


def test_empty_window_refuses_to_delete_existing_rows(tmp_path, monkeypatch):
    market_db = tmp_path / "market.duckdb"
    alpha_db = tmp_path / "alpha158.duckdb"
    _seed_market_db(market_db, end=date(2026, 5, 2))
    _patch_latest_completed(monkeypatch, "2026-05-05")

    subject.build(
        str(alpha_db),
        "2026-01-01",
        end_date="2026-05-02",
        replace_table=True,
        market_db=str(market_db),
    )

    con = duckdb.connect(str(alpha_db))
    try:
        con.execute(
            "CREATE TEMP TABLE stale_window_row AS SELECT * FROM fact_alpha158_panel "
            "WHERE stock_code = '000001' AND date = DATE '2026-05-02'"
        )
        con.execute("UPDATE stale_window_row SET date = DATE '2026-05-05', close = 999.0")
        con.execute("INSERT INTO fact_alpha158_panel SELECT * FROM stale_window_row")
    finally:
        con.close()

    with pytest.raises(RuntimeError, match="produced 0 rows"):
        subject.build(
            str(alpha_db),
            "2026-05-03",
            end_date="2026-05-05",
            write_start_date="2026-05-03",
            market_db=str(market_db),
        )

    con = duckdb.connect(str(alpha_db), read_only=True)
    try:
        row = con.execute(
            "SELECT close FROM fact_alpha158_panel WHERE stock_code = '000001' AND date = DATE '2026-05-05'"
        ).fetchone()
    finally:
        con.close()

    assert row == (999.0,)


def test_explicit_future_end_date_is_rejected(tmp_path, monkeypatch):
    market_db = tmp_path / "market.duckdb"
    alpha_db = tmp_path / "alpha158.duckdb"
    _seed_market_db(market_db, end=date(2026, 5, 6))
    _patch_latest_completed(monkeypatch, "2026-05-05")

    with pytest.raises(ValueError, match="after latest completed trading date"):
        subject.build(
            str(alpha_db),
            "2026-01-01",
            end_date="2026-05-06",
            market_db=str(market_db),
        )
