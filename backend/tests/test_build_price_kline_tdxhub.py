import sys
from pathlib import Path

from conftest import duck_mem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_price_kline_tdxhub as builder  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []

    def bars_records(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["start"] == 0:
            return [
                {
                    "datetime": "2026-05-04T00:00:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                },
                {
                    "datetime": "2026-05-04T10:00:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                },
            ]
        return []


def test_pull_and_normalize_price_records():
    client = FakeClient()

    records = builder.pull_one_stock(client, "1", pages=2)
    normalized = builder.normalize(records, "batch-1")

    assert len(records) == 2
    assert client.calls[0]["symbol"] == "1"
    assert normalized == [
        {
            "code": "000001",
            "date": "2026-05-04",
            "freq": "daily",
            "adjust": "qfq",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
            "amount": 10500.0,
            "factor": 1.0,
            "source": "tdxhub",
            "batch_id": "batch-1",
        }
    ]


def test_write_batch_uses_records():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        rows = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-04",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                }
            ],
            "batch-1",
        )

        assert builder.write_batch(conn, rows) == 1
        assert builder.write_batch(conn, rows) == 1

        saved = conn.execute(
            "SELECT code, date, close, source, batch_id FROM price_kline_tdxhub"
        ).fetchall()
        assert [tuple(row) for row in saved] == [("000001", "2026-05-04", 10.5, "tdxhub", "batch-1")]
    finally:
        conn.close()


def test_incremental_filter_uses_per_stock_latest_date():
    conn = duck_mem()
    try:
        conn.executescript(builder.TABLE_DDL)
        existing = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-03",
                    "open": 9,
                    "high": 10,
                    "low": 8,
                    "close": 9.5,
                    "vol": 900,
                    "amount": 9000,
                    "factor": 1,
                }
            ],
            "batch-old",
        )
        builder.write_batch(conn, existing)

        latest = builder.load_latest_dates(conn)
        rows = builder.normalize(
            [
                {
                    "code": "000001",
                    "datetime": "2026-05-03",
                    "open": 9,
                    "high": 10,
                    "low": 8,
                    "close": 9.5,
                    "vol": 900,
                    "amount": 9000,
                    "factor": 1,
                },
                {
                    "code": "000001",
                    "datetime": "2026-05-04",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 1000,
                    "amount": 10500,
                    "factor": 1,
                },
                {
                    "code": "000002",
                    "datetime": "2026-05-01",
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "close": 20.5,
                    "vol": 2000,
                    "amount": 41000,
                    "factor": 1,
                },
            ],
            "batch-new",
        )

        filtered = builder.filter_after_latest(rows, latest)

        assert [(row["code"], row["date"]) for row in filtered] == [
            ("000001", "2026-05-04"),
            ("000002", "2026-05-01"),
        ]
    finally:
        conn.close()
