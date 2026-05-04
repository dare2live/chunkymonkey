import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import build_fundamental_quarterly as subject


class _QuarterTable:
    empty = False

    def to_dict(self, orient):
        assert orient == "index"
        return {
            "1": {
                "基本每股收益": "0.56",
                "净利润(万元)": "1200.5",
                "股东人数(户)": None,
            },
            "000001": {
                "基本每股收益": "0.99",
                "净利润(万元)": "9999",
            },
            "2": {
                "基本每股收益": "",
                "净利润(万元)": "3000",
                "股东人数(户)": "15000",
            },
        }


def test_parse_one_quarter_normalizes_records_and_dedupes_codes(monkeypatch, tmp_path):
    class _Affair:
        @staticmethod
        def fetch(downdir, filename):
            Path(downdir, filename).write_bytes(b"x" * 10_001)

        @staticmethod
        def parse(downdir, filename):
            return _QuarterTable()

    monkeypatch.setattr(subject, "Affair", _Affair)

    rows = subject.parse_one_quarter(str(tmp_path), "gpcw20260331.zip")

    assert rows == [
        {
            "stock_code": "000001",
            "report_date": "20260331",
            "eps_basic": 0.56,
            "net_profit_10k": 1200.5,
            "shareholder_count": None,
        },
        {
            "stock_code": "000002",
            "report_date": "20260331",
            "eps_basic": None,
            "net_profit_10k": 3000.0,
            "shareholder_count": 15000.0,
        },
    ]
    assert not Path(tmp_path, "gpcw20260331.zip").exists()


def test_insert_quarter_rows_upserts_without_table_payload_registration():
    conn = duck_mem()
    try:
        conn.executescript(subject.build_ddl())
        rows = [
            {
                "stock_code": "000001",
                "report_date": "20260331",
                "eps_basic": 0.5,
                "net_profit_10k": 1200.0,
            },
            {
                "stock_code": "000001",
                "report_date": "20260331",
                "eps_basic": 0.6,
                "net_profit_10k": 1300.0,
            },
        ]

        inserted = subject.insert_quarter_rows(conn, rows, "2026-05-05T00:00:00")
        row = conn.execute(
            """
            SELECT eps_basic, net_profit_10k, built_at
            FROM fact_fundamental_quarterly
            WHERE stock_code = '000001' AND report_date = '20260331'
            """
        ).fetchone()

        assert inserted == 2
        assert row["eps_basic"] == pytest.approx(0.6)
        assert row["net_profit_10k"] == pytest.approx(1300.0)
        assert row["built_at"] == "2026-05-05T00:00:00"
    finally:
        conn.close()
