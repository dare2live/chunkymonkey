import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.updater import (  # noqa: E402
    _build_daily_sync_batch_summary,
    _format_sync_source_metrics,
    _normalize_update_step_detail,
    _record_sync_source_metric,
    _snapshot_sync_source_metrics,
)


class DailySyncMetricsTests(unittest.TestCase):
    def test_snapshot_and_format_include_avg_and_peak(self):
        stats = {}
        _record_sync_source_metric(stats, "mootdx", 15.2, 160)
        _record_sync_source_metric(stats, "mootdx", 16.8, 180)
        _record_sync_source_metric(stats, "tx", 2.4, 30)

        snapshot = _snapshot_sync_source_metrics(stats)

        self.assertEqual(snapshot["mootdx"]["count"], 2)
        self.assertEqual(snapshot["mootdx"]["rows"], 340)
        self.assertEqual(snapshot["mootdx"]["avg_elapsed_sec"], 16.0)
        self.assertEqual(snapshot["mootdx"]["max_elapsed_sec"], 16.8)
        self.assertEqual(snapshot["tx"]["count"], 1)
        self.assertIn("mootdx=2只/均16.00s/峰16.80s/行340", _format_sync_source_metrics(snapshot))
        self.assertIn("tx=1只/均2.40s/峰2.40s/行30", _format_sync_source_metrics(snapshot))

    def test_batch_summary_derives_failed_count_from_range(self):
        stats = {}
        _record_sync_source_metric(stats, "mootdx", 15.0, 160)
        _record_sync_source_metric(stats, "tx", 3.0, 20)

        summary = _build_daily_sync_batch_summary(
            1,
            5,
            stats=stats,
            batch_elapsed_sec=40.1234,
        )

        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failed_count"], 3)
        self.assertEqual(summary["batch_elapsed_sec"], 40.123)
        self.assertEqual(summary["source_stats"]["mootdx"]["count"], 1)

    def test_normalize_update_step_detail_backfills_daily_preflight_fields(self):
        detail = {
            "daily_sync": {
                "status": "success",
                "done_codes": 3,
                "total_codes": 3,
            }
        }

        normalized = _normalize_update_step_detail(detail)

        self.assertFalse(normalized["daily_sync"]["prefer_fallback"])
        self.assertIsNone(normalized["daily_sync"]["strategy_reason"])
        self.assertIsNone(normalized["daily_sync"]["preflight_sample"])


if __name__ == "__main__":
    unittest.main()