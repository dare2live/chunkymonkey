import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import audit, db, financial_client, market_db


def test_run_quality_audit_uses_financial_universe_for_latest_snapshot_coverage():
    with TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        business_db_path = data_dir / "business.duckdb"
        market_db_path = data_dir / "market.duckdb"

        with mock.patch.object(db, "DB_DIR", data_dir), mock.patch.object(db, "DB_PATH", business_db_path), mock.patch.object(market_db, "_DB_DIR", data_dir), mock.patch.object(market_db, "_DB_PATH", market_db_path):
            db.init_db()
            market_db.init_market_db()

            conn = db.get_conn()
            try:
                financial_client.ensure_tables(conn)
                conn.execute(
                    "INSERT INTO inst_institutions (id, name, enabled, blacklisted, merged_into) VALUES (?, ?, ?, ?, ?)",
                    ("inst_1", "测试机构", 1, 0, None),
                )
                conn.executemany(
                    "INSERT INTO dim_active_a_stock (stock_code, stock_name, market, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                    [
                        ("000001", "平安银行", "SZ", "test", "2026-04-14T09:00:00"),
                        ("000002", "万科A", "SZ", "test", "2026-04-14T09:00:00"),
                        ("000003", "测试排除", "SZ", "test", "2026-04-14T09:00:00"),
                    ],
                )
                conn.execute(
                    "INSERT INTO excluded_stocks (stock_code, category, reason, created_at) VALUES (?, ?, ?, ?)",
                    ("000003", "test", "test", "2026-04-14T09:00:00"),
                )
                conn.execute(
                    "INSERT INTO inst_holdings (institution_id, holder_name, stock_code, stock_name, report_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("inst_1", "测试机构", "000001", "平安银行", "2026-03-31", "2026-04-14T09:00:00"),
                )
                conn.executemany(
                    "INSERT INTO raw_gpcw_financial (stock_code, report_date, ingested_at) VALUES (?, ?, ?)",
                    [
                        ("000001", "2025-12-31", "2026-04-14T09:00:00"),
                        ("000002", "2025-12-31", "2026-04-14T09:00:00"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO fact_financial_derived (stock_code, report_date, updated_at) VALUES (?, ?, ?)",
                    [
                        ("000001", "2025-12-31", "2026-04-14T09:00:00"),
                        ("000002", "2025-12-31", "2026-04-14T09:00:00"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO dim_financial_latest (stock_code, latest_report_date, updated_at) VALUES (?, ?, ?)",
                    [
                        ("000001", "2025-12-31", "2026-04-14T09:00:00"),
                        ("000002", "2025-12-31", "2026-04-14T09:00:00"),
                    ],
                )
                conn.commit()

                mkt_conn = market_db.get_market_conn()
                try:
                    mkt_conn.execute(
                        "INSERT INTO price_kline (code, date, freq, adjust, close) VALUES (?, ?, ?, ?, ?)",
                        ("000001", "2026-04-14", "daily", "qfq", 10.0),
                    )
                    mkt_conn.commit()
                finally:
                    mkt_conn.close()

                audit.invalidate_audit_cache()
                payload = audit.run_quality_audit(conn, use_cache=False)
            finally:
                conn.close()

        financial = payload["layers"]["financial"]
        assert financial["latest_count"] == 2
        assert financial["expected_stocks"] == 2
        assert financial["tracked_latest_count"] == 1
        assert financial["tracked_expected_stocks"] == 1
