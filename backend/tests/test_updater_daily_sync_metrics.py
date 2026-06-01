import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.updater import (  # noqa: E402
    _build_daily_sync_batch_summary,
    _format_sync_source_metrics,
    _normalize_update_step_detail,
    _record_sync_source_metric,
    _step_sync_raw,
    _snapshot_sync_source_metrics,
)


class _FetchOne:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class _SyncRawConn:
    def __init__(self, before: int, after: int):
        self._counts = [before, after]

    def execute(self, *_args, **_kwargs):
        return _FetchOne(self._counts.pop(0))


def _stub_extra_stats():
    return {
        "status": "completed",
        "raw_rows": 0,
        "holder_count_rows": 0,
        "trade_b_rows": 0,
        "control_rows": 0,
        "common_major_holder_rows": 0,
        "fund_holding_rows": 0,
        "fund_holding_rejected_rows": 0,
        "skipped_non_format_b": 0,
        "skipped_no_extra_section": 0,
        "errors": [],
    }


class DailySyncMetricsTests(unittest.TestCase):
    def test_snapshot_and_format_include_avg_and_peak(self):
        stats = {}
        _record_sync_source_metric(stats, "tdxhub", 15.2, 160)
        _record_sync_source_metric(stats, "tdxhub", 16.8, 180)
        _record_sync_source_metric(stats, "tx", 2.4, 30)

        snapshot = _snapshot_sync_source_metrics(stats)

        self.assertEqual(snapshot["tdxhub"]["count"], 2)
        self.assertEqual(snapshot["tdxhub"]["rows"], 340)
        self.assertEqual(snapshot["tdxhub"]["avg_elapsed_sec"], 16.0)
        self.assertEqual(snapshot["tdxhub"]["max_elapsed_sec"], 16.8)
        self.assertEqual(snapshot["tx"]["count"], 1)
        self.assertIn("tdxhub=2只/均16.00s/峰16.80s/行340", _format_sync_source_metrics(snapshot))
        self.assertIn("tx=1只/均2.40s/峰2.40s/行30", _format_sync_source_metrics(snapshot))

    def test_batch_summary_derives_failed_count_from_range(self):
        stats = {}
        _record_sync_source_metric(stats, "tdxhub", 15.0, 160)
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
        self.assertEqual(summary["source_stats"]["tdxhub"]["count"], 1)

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


def test_sync_raw_reports_failed_when_all_attempted_fetches_error(monkeypatch):
    fake_mod = types.ModuleType("scripts.ingest_holders_tdxhub")
    fake_mod.run = lambda **_kwargs: {
        "done": 3,
        "ok": 0,
        "err": 3,
        "skipped_unchanged": 0,
        "skipped_no_f10": 0,
    }
    monkeypatch.setitem(sys.modules, "scripts.ingest_holders_tdxhub", fake_mod)

    import services.tdx_f10_extra_client as extra_client

    monkeypatch.setattr(extra_client, "sync_tdx_f10_extra_facts", lambda _conn: _stub_extra_stats())

    result = asyncio.run(_step_sync_raw(_SyncRawConn(before=10, after=10)))

    assert result["status"] == "failed"
    assert result["err"] == 3
    assert "err_rate=100.0%" in result["message"]


def test_sync_raw_reports_partial_for_low_error_rate(monkeypatch):
    fake_mod = types.ModuleType("scripts.ingest_holders_tdxhub")
    fake_mod.run = lambda **_kwargs: {
        "done": 10,
        "ok": 9,
        "err": 1,
        "skipped_unchanged": 0,
        "skipped_no_f10": 0,
    }
    monkeypatch.setitem(sys.modules, "scripts.ingest_holders_tdxhub", fake_mod)

    import services.tdx_f10_extra_client as extra_client

    monkeypatch.setattr(extra_client, "sync_tdx_f10_extra_facts", lambda _conn: _stub_extra_stats())

    result = asyncio.run(_step_sync_raw(_SyncRawConn(before=10, after=19)))

    assert result["status"] == "partial"
    assert result["written"] == 9


def test_sync_raw_reports_partial_when_extra_rejects_fund_rows(monkeypatch):
    fake_mod = types.ModuleType("scripts.ingest_holders_tdxhub")
    fake_mod.run = lambda **_kwargs: {
        "done": 3,
        "ok": 3,
        "err": 0,
        "skipped_unchanged": 0,
        "skipped_no_f10": 0,
    }
    monkeypatch.setitem(sys.modules, "scripts.ingest_holders_tdxhub", fake_mod)

    import services.tdx_f10_extra_client as extra_client

    monkeypatch.setattr(
        extra_client,
        "sync_tdx_f10_extra_facts",
        lambda _conn: {
            "status": "completed_with_rejections",
            "raw_rows": 3,
            "holder_count_rows": 0,
            "trade_b_rows": 0,
            "control_rows": 0,
            "common_major_holder_rows": 0,
            "fund_holding_rows": 1,
            "fund_holding_rejected_rows": 2,
            "skipped_non_format_b": 1,
            "skipped_no_extra_section": 0,
            "errors": [],
        },
    )

    result = asyncio.run(_step_sync_raw(_SyncRawConn(before=10, after=13)))

    assert result["status"] == "partial"
    assert "extra_fund_rejected=2" in result["message"]
    assert "extra_skip_non_b=1" in result["message"]


if __name__ == "__main__":
    unittest.main()
