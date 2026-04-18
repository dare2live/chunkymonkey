import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pandas as pd
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import market_db, xdxr_client


class _DummyXdxrClient:
    def xdxr(self, symbol: str):
        assert symbol == "600036"
        return pd.DataFrame(
            [
                {
                    "year": 2024,
                    "month": 6,
                    "day": 18,
                    "category": 1,
                    "name": "除权除息",
                    "fenhong": 1.25,
                    "peigujia": None,
                    "songzhuangu": 0.5,
                    "peigu": 0.0,
                    "suogu": None,
                    "panqianliutong": 1000.0,
                    "panhouliutong": 1500.0,
                    "qianzongguben": 2000.0,
                    "houzongguben": 2500.0,
                    "fenshu": None,
                    "xingquanjia": None,
                },
                {
                    "year": 2025,
                    "month": 1,
                    "day": 10,
                    "category": 5,
                    "name": "股本变化",
                    "fenhong": None,
                    "peigujia": None,
                    "songzhuangu": None,
                    "peigu": None,
                    "suogu": None,
                    "panqianliutong": 1500.0,
                    "panhouliutong": 1800.0,
                    "qianzongguben": 2500.0,
                    "houzongguben": 2800.0,
                    "fenshu": None,
                    "xingquanjia": None,
                },
            ]
        )


@pytest.mark.asyncio
async def test_sync_xdxr_for_codes_persists_rows_and_state():
    with TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db_path = data_dir / "market_data.db"

        with mock.patch.object(market_db, "_DB_DIR", data_dir), mock.patch.object(market_db, "_DB_PATH", db_path):
            market_db.init_market_db()
            conn = market_db.get_market_conn()
            try:
                with mock.patch.object(
                    xdxr_client,
                    "call_tdx_quotes_with_retry",
                    return_value=(_DummyXdxrClient().xdxr("600036"), "tdxhub_1.2.3.4:7709"),
                ):
                    result = await xdxr_client.sync_xdxr_for_codes(conn, ["600036"])

                assert result["status"] == "success"
                assert result["success_codes"] == 1
                assert result["rows"] == 2

                rows = market_db.get_xdxr_events(conn, "600036")
                assert [row["date"] for row in rows] == ["2024-06-18", "2025-01-10"]
                assert rows[0]["category"] == 1
                assert rows[0]["fenhong"] == 1.25
                assert rows[1]["name"] == "股本变化"

                states = market_db.get_all_xdxr_sync_states(conn)
                assert len(states) == 1
                assert states[0]["code"] == "600036"
                assert states[0]["row_count"] == 2
                assert states[0]["last_error"] is None
            finally:
                conn.close()


@pytest.mark.asyncio
async def test_sync_xdxr_skips_recent_successful_codes():
    with TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db_path = data_dir / "market_data.db"

        with mock.patch.object(market_db, "_DB_DIR", data_dir), mock.patch.object(market_db, "_DB_PATH", db_path):
            market_db.init_market_db()
            conn = market_db.get_market_conn()
            try:
                market_db.update_xdxr_sync_state(
                    conn,
                    "600036",
                    source="tdxhub_cached",
                    min_date="2024-06-18",
                    max_date="2025-01-10",
                    row_count=2,
                )

                with mock.patch.object(
                    xdxr_client,
                    "call_tdx_quotes_with_retry",
                    side_effect=AssertionError("recently synced code should be skipped"),
                ):
                    result = await xdxr_client.sync_xdxr_for_codes(conn, ["600036"], cooldown_hours=24)

                assert result["status"] == "skipped"
                assert result["total_codes"] == 0
                assert result["skipped_recent"] == 1
            finally:
                conn.close()


@pytest.mark.asyncio
async def test_sync_xdxr_reports_progress_snapshots():
    with TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db_path = data_dir / "market_data.db"

        with mock.patch.object(market_db, "_DB_DIR", data_dir), mock.patch.object(market_db, "_DB_PATH", db_path):
            market_db.init_market_db()
            conn = market_db.get_market_conn()
            progress = []

            async def _fake_fetch(code: str):
                return ([{"code": code, "date": "2025-01-10", "category": 1, "name": "除权除息", "fenhong": 1.0, "peigujia": None, "songzhuangu": None, "peigu": None, "suogu": None, "panqianliutong": None, "panhouliutong": None, "qianzongguben": None, "houzongguben": None, "fenshu": None, "xingquanjia": None}], "tdxhub_1.2.3.4:7709")

            try:
                with mock.patch.object(xdxr_client, "fetch_stock_xdxr", side_effect=_fake_fetch):
                    result = await xdxr_client.sync_xdxr_for_codes(
                        conn,
                        ["600036", "000001"],
                        progress_callback=progress.append,
                        concurrency=2,
                        progress_every=1,
                    )

                assert result["status"] == "success"
                assert result["success_codes"] == 2
                assert progress[0]["done_codes"] == 1
                assert progress[-1]["done_codes"] == 2
                assert progress[-1]["concurrency"] == 2
            finally:
                conn.close()