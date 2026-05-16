import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem  # noqa: E402

import services.akshare_client as akshare_client  # noqa: E402
import services.etf_db as etf_db  # noqa: E402
from services.akshare_client import fetch_etf_kline, fetch_etf_list, fetch_etf_list_with_source, test_kline_availability as check_kline_availability  # noqa: E402
from services.etf_engine import sync_etf_universe  # noqa: E402
from services.kline_source import aggregate_monthly_from_daily  # noqa: E402
from services.etf_snapshot_manager import _build_etf_source_status  # noqa: E402


def _kline_rows(date: str = "2026-04-01"):
    return [
        {
            "date": date,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 1000.0,
            "amount": 1200.0,
        }
    ]


class KlineSourceFallbackTests(unittest.IsolatedAsyncioTestCase):
    @patch("services.akshare_client._fetch_daily_akshare_fallbacks", new_callable=AsyncMock)
    @patch("services.akshare_client._fetch_daily_tdxhub_with_diagnostics", new_callable=AsyncMock)
    async def test_kline_probe_marks_fallback_as_available(self, tdxhub_mock, fallback_mock):
        tdxhub_mock.return_value = (
            None,
            None,
            {
                "ok": False,
                "attempts": [{"server": "119.147.212.81:7709", "error_type": "ResponseHeaderRecvFails"}],
                "summary": "tdxhub ResponseHeaderRecvFails (1服)",
            },
        )
        fallback_mock.return_value = (
            _kline_rows(),
            "tx",
            {"ok": True, "attempts": [{"source": "tx", "ok": True}]},
        )

        probe = await check_kline_availability()

        self.assertTrue(probe["available"])
        self.assertEqual(probe["effective_source"], "tx")
        self.assertIn("tx fallback", probe["detail"])
        self.assertIn("tdxhub", probe["detail"])

    @patch("services.akshare_client._fetch_daily_akshare_fallbacks", new_callable=AsyncMock)
    @patch("services.akshare_client._fetch_daily_tdxhub_with_diagnostics", new_callable=AsyncMock)
    async def test_fetch_etf_kline_falls_back_when_tdxhub_unavailable(self, tdxhub_mock, fallback_mock):
        tdxhub_mock.return_value = (
            None,
            None,
            {
                "ok": False,
                "attempts": [{"server": "119.147.212.81:7709", "error_type": "timeout"}],
                "summary": "tdxhub timeout (1服)",
            },
        )
        fallback_mock.return_value = (
            _kline_rows(),
            "tx",
            {"ok": True, "attempts": [{"source": "tx", "ok": True}]},
        )

        rows, source = await fetch_etf_kline("159695", "20260320", "20260410")

        self.assertTrue(rows)
        self.assertEqual(source, "tx")

    @patch("services.akshare_client._fetch_daily_akshare_fallbacks", new_callable=AsyncMock)
    async def test_fetch_daily_with_fallback_skips_tdxhub_when_circuit_is_open(self, fallback_mock):
        fallback_mock.return_value = (
            _kline_rows(),
            "tx",
            {"ok": True, "attempts": [{"source": "tx", "ok": True}]},
        )
        akshare_client._mark_tdxhub_unavailable("tdxhub timeout x2，切换 fallback", [], cooldown_seconds=180)

        try:
            rows, source = await akshare_client._fetch_daily_with_fallback("000001", "20260401", "20260410")
        finally:
            akshare_client._clear_tdxhub_unavailable()

        self.assertTrue(rows)
        self.assertEqual(source, "tx")

    @patch("services.akshare_client._fetch_daily_akshare_fallbacks", new_callable=AsyncMock)
    @patch("services.akshare_client._fetch_daily_tdxhub_with_diagnostics", new_callable=AsyncMock)
    async def test_fetch_daily_with_fallback_prefers_akshare_before_tdxhub(self, tdxhub_mock, fallback_mock):
        fallback_mock.return_value = (
            _kline_rows("2026-04-10"),
            "tx",
            {"ok": True, "attempts": [{"source": "tx", "ok": True}]},
        )

        rows, source = await akshare_client._fetch_daily_with_fallback(
            "000001",
            "20260401",
            "20260410",
            prefer_fallback=True,
        )

        self.assertTrue(rows)
        self.assertEqual(source, "tx")
        tdxhub_mock.assert_not_awaited()

    @patch("services.akshare_client._fetch_daily_akshare_fallbacks", new_callable=AsyncMock)
    @patch("services.akshare_client._fetch_daily_tdxhub_with_diagnostics", new_callable=AsyncMock)
    async def test_fetch_daily_with_fallback_prefers_fresher_fallback_result(self, tdxhub_mock, fallback_mock):
        tdxhub_mock.return_value = (
            _kline_rows("2026-04-03"),
            "tdxhub",
            {"ok": True, "summary": "tdxhub stale", "fallback_recommended": False},
        )
        fallback_mock.return_value = (
            _kline_rows("2026-04-10"),
            "tx",
            {"ok": True, "attempts": [{"source": "tx", "ok": True}]},
        )

        rows, source = await akshare_client._fetch_daily_with_fallback("000001", "20260401", "20260410")

        self.assertEqual(source, "tx")
        self.assertEqual(max(row["date"] for row in rows), "2026-04-10")

    @patch("services.akshare_client._fetch_daily_akshare_fallbacks", new_callable=AsyncMock)
    @patch("services.akshare_client._fetch_daily_tdxhub_with_diagnostics", new_callable=AsyncMock)
    async def test_fetch_daily_with_fallback_prefers_fresher_tdxhub_result_after_fallback_probe(self, tdxhub_mock, fallback_mock):
        fallback_mock.return_value = (
            _kline_rows("2026-04-03"),
            "tx",
            {"ok": True, "attempts": [{"source": "tx", "ok": True}]},
        )
        tdxhub_mock.return_value = (
            _kline_rows("2026-04-10"),
            "tdxhub",
            {"ok": True, "summary": "tdxhub healthy", "fallback_recommended": False},
        )

        rows, source = await akshare_client._fetch_daily_with_fallback(
            "000001",
            "20260401",
            "20260410",
            prefer_fallback=True,
        )

        self.assertEqual(source, "tdxhub")
        self.assertEqual(max(row["date"] for row in rows), "2026-04-10")

    @patch("services.akshare_client._fetch_daily_tdxhub_with_diagnostics", new_callable=AsyncMock)
    async def test_probe_stock_kline_fallback_preference_marks_degraded_tdxhub(self, tdxhub_mock):
        tdxhub_mock.return_value = (
            _kline_rows(),
            "tdxhub",
            {
                "ok": True,
                "summary": "tdxhub 218.6.170.47:7709 · timeout x3",
                "timeout_failures": 3,
                "fallback_recommended": True,
                "elapsed_sec": 15.7,
            },
        )

        probe = await akshare_client.probe_stock_kline_fallback_preference(
            "000001",
            "20260401",
            "20260410",
        )

        self.assertTrue(probe["prefer_fallback"])
        self.assertEqual(probe["sample_code"], "000001")
        self.assertEqual(probe["timeout_failures"], 3)
        self.assertIn("timeout x3", probe["reason"])

    @patch("services.akshare_client._fetch_etf_list_ths", new_callable=AsyncMock)
    @patch("services.akshare_client._fetch_etf_list_tdxhub", new_callable=AsyncMock)
    async def test_fetch_etf_list_falls_back_to_ths(self, tdxhub_mock, ths_mock):
        fallback_rows = [
            {"code": "159695", "name": "通信ETF", "market": "sz", "asset_type": "etf"},
            {"code": "512010", "name": "医药ETF", "market": "sh", "asset_type": "etf"},
        ]
        tdxhub_mock.return_value = []
        ths_mock.return_value = fallback_rows

        rows = await fetch_etf_list()

        self.assertEqual(rows, fallback_rows)

    @patch("services.akshare_client._fetch_etf_list_tdxhub", new_callable=AsyncMock)
    async def test_fetch_etf_list_with_source_returns_effective_source(self, tdxhub_mock):
        rows = [{"code": "159695", "name": "通信ETF", "market": "sz", "asset_type": "etf"}]
        tdxhub_mock.return_value = (rows, "tdxhub_1.1.1.1:7709")

        result_rows, source = await fetch_etf_list_with_source()

        self.assertEqual(result_rows, rows)
        self.assertEqual(source, "tdxhub_1.1.1.1:7709")

    @patch("services.akshare_client.fetch_etf_kline", new_callable=AsyncMock)
    @patch("services.akshare_client.fetch_etf_list_with_source", new_callable=AsyncMock)
    async def test_sync_etf_universe_records_list_and_kline_sources(self, list_mock, kline_mock):
        conn = duck_mem()
        etf_db._ensure_schema(conn)
        try:
            list_mock.return_value = (
                [{"code": "159695", "name": "通信ETF", "market": "sz", "asset_type": "etf"}],
                "tdxhub_1.1.1.1:7709",
            )
            kline_mock.return_value = (_kline_rows(), "tx")

            result = await sync_etf_universe(
                conn,
                conn,
                sync_kline=True,
                kline_start_date="20260401",
            )
            source_status = _build_etf_source_status(conn, conn)

            self.assertEqual(result["list_source"], "tdxhub_1.1.1.1:7709")
            self.assertEqual(result["kline_source_breakdown"], [{"source": "tx", "count": 1}])
            self.assertEqual(source_status["universe_source"], "tdxhub_1.1.1.1:7709")
            self.assertEqual(source_status["source_breakdown"], [{"source": "tx", "count": 1}])
            self.assertEqual(source_status["kline_etf_count"], 1)
        finally:
            conn.close()


class KlineSourceHelperTests(unittest.TestCase):
    def test_aggregate_monthly_from_daily_rolls_up_ohlcv(self):
        # governance v1: volume unit = lots, amount = volume * 100 shares * close (rough)
        # close=1.2, volume=100 lots = 10000 shares, amount = 10000 * 1.2 = 12000 元
        monthly = aggregate_monthly_from_daily([
            {"date": "2026-03-03", "open": 1.0, "high": 1.3, "low": 0.9, "close": 1.2, "volume": 100.0, "amount": 12000.0},
            {"date": "2026-03-28", "open": 1.2, "high": 1.4, "low": 1.1, "close": 1.35, "volume": 120.0, "amount": 15600.0},
            {"date": "2026-04-02", "open": 1.35, "high": 1.5, "low": 1.3, "close": 1.45, "volume": 90.0, "amount": 12600.0},
        ])

        self.assertEqual([row["date"] for row in monthly], ["2026-03-01", "2026-04-01"])
        self.assertEqual(monthly[0]["open"], 1.0)
        self.assertEqual(monthly[0]["close"], 1.35)
        self.assertEqual(monthly[0]["high"], 1.4)
        self.assertEqual(monthly[0]["low"], 0.9)
        self.assertEqual(monthly[0]["volume"], 220.0)
        self.assertEqual(monthly[0]["amount"], 27600.0)


if __name__ == "__main__":
    unittest.main()
