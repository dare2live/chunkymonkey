import sys
from pathlib import Path

from conftest import duck_mem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_orderbook_snapshot as builder  # noqa: E402


def _quote_row(code="1"):
    row = {
        "code": code,
        "price": 10,
        "last_close": 9.8,
        "vol": 1000,
        "amount": 10000,
        "cur_vol": 200,
        "b_vol": 300,
        "s_vol": 150,
    }
    for level in range(1, 6):
        row[f"bid{level}"] = 10 - level * 0.01
        row[f"ask{level}"] = 10 + level * 0.01
        row[f"bid_vol{level}"] = 100 * level
        row[f"ask_vol{level}"] = 50 * level
    return row


def test_compute_and_normalize_orderbook_records():
    rows = builder.compute_derived_features([_quote_row(), _quote_row()])
    normalized = builder.normalize_snapshot_rows(rows, "20260505")

    assert len(normalized) == 1
    row = normalized[0]
    assert row["stock_code"] == "000001"
    assert row["snapshot_date"] == "20260505"
    assert round(row["imbalance_1"], 6) == round((100 - 50) / 150, 6)
    assert row["imbalance_5"] == (1500 - 750) / 2250
    assert row["active_buy_ratio"] == 0.2
    assert row["inside_outside_ratio"] == 2.0


def test_write_batch_persists_orderbook_records():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        rows = builder.normalize_snapshot_rows(
            builder.compute_derived_features([_quote_row()]),
            "20260505",
        )

        assert builder.write_batch(conn, rows) == 1
        assert builder.write_batch(conn, rows) == 1
        saved = conn.execute(
            "SELECT snapshot_date, stock_code, spread_bps FROM fact_orderbook_snapshot"
        ).fetchone()
        assert saved["snapshot_date"] == "20260505"
        assert saved["stock_code"] == "000001"
        assert saved["spread_bps"] > 0
        assert conn.execute("SELECT COUNT(*) FROM fact_orderbook_snapshot").fetchone()[0] == 1
    finally:
        conn.close()
