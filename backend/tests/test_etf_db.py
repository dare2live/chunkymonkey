import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import etf_db  # noqa: E402


class EtfDbTests(unittest.TestCase):
    def test_get_etf_conn_initializes_schema_without_legacy_bootstrap(self):
        with TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            db_path = data_dir / "etf.duckdb"

            with mock.patch.object(etf_db, "_DB_DIR", data_dir), mock.patch.object(etf_db, "_DB_PATH", db_path), mock.patch(
                "services.db.get_conn",
                side_effect=AssertionError("legacy business db should not be used"),
            ), mock.patch(
                "services.market_db.get_market_conn",
                side_effect=AssertionError("legacy market db should not be used"),
            ):
                conn = etf_db.get_etf_conn()
                try:
                    tables = {
                        row["table_name"]
                        for row in conn.execute(
                            """
                            SELECT table_name
                              FROM information_schema.tables
                             WHERE table_schema = 'main'
                               AND table_type = 'BASE TABLE'
                            """
                        )
                    }

                    # M2 Stage E: etf_price_kline (mootdx) DDL 已退役物删, K线源切 tushare qfq 表。
                    self.assertTrue(
                        {"etf_asset_universe", "mart_etf_snapshot_state"} <= tables
                    )
                    self.assertNotIn("etf_price_kline", tables)
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) AS cnt FROM etf_asset_universe").fetchone()["cnt"],
                        0,
                    )
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) AS cnt FROM mart_etf_snapshot_state").fetchone()["cnt"],
                        0,
                    )
                finally:
                    conn.close()


if __name__ == "__main__":
    unittest.main()
