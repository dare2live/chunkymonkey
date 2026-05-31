import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services.stock_graph_read import get_stock_related


def test_stock_related_does_not_filter_related_stocks_by_name_cache():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE dim_stock_tdx_industry (
                stock_code TEXT,
                tdx_l1 TEXT,
                tdx_l1_name TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE dim_active_a_stock (stock_code TEXT, stock_name TEXT)"
        )
        conn.executemany(
            "INSERT INTO dim_stock_tdx_industry VALUES (?, ?, ?)",
            [
                ("000001", "finance", "金融"),
                ("000002", "finance", "金融"),
            ],
        )
        conn.execute(
            "INSERT INTO dim_active_a_stock VALUES (?, ?)",
            ("000001", "平安银行"),
        )

        payload = get_stock_related(conn, "000001")

        assert payload["related"] == [
            {
                "stock_code": "000002",
                "stock_name": "000002",
                "industry": "金融",
                "relation": "same_industry",
                "weight": 1.0,
                "source": "dim_stock_tdx_industry",
            }
        ]
    finally:
        conn.close()
