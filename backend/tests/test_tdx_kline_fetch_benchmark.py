import json
import sys
from pathlib import Path

from conftest import duck_mem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import benchmark_tdx_kline_fetch as bench  # noqa: E402


class NoCloseConn:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return None


def _seed_price_row(conn, *, code: str = "000001", date: str = "2026-05-06") -> None:
    conn.executescript(bench.kline.TABLE_DDL)
    conn.execute(
        """
        INSERT INTO price_kline_tdxhub (
            code, date, freq, adjust, open, high, low, close,
            volume, amount, factor, source, batch_id
        ) VALUES (?, ?, 'daily', 'qfq', 10, 11, 9, 10.5, 1000, 10500, 1.0, 'unit', 'unit')
        """,
        (code, date),
    )


def test_tdx_kline_fetch_benchmark_skips_network_when_preflight_is_fresh(monkeypatch):
    conn = duck_mem()
    _seed_price_row(conn, date="2026-05-06")
    monkeypatch.setattr(bench, "get_market_conn", lambda: NoCloseConn(conn))
    monkeypatch.setattr(bench.kline, "load_calendar_target_date", lambda: "2026-05-06")
    monkeypatch.setattr(
        bench.kline,
        "load_local_active_a_stock_list",
        lambda: ([("000001", 0)], "dim_active_a_stock"),
    )
    monkeypatch.setattr(
        bench,
        "call_tdx_quotes_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network should be skipped")),
    )

    result = bench.run_benchmark(bench.BenchmarkConfig(run_id="unit_fresh_preflight"))

    assert result["gate_result"] == "pass"
    assert result["preflight"]["stale_stock_count"] == 0
    assert result["preflight"]["network_touched"] is False
    assert result["fetch_summary"]["fetched_stock_count"] == 0
    assert "calendar_preflight_s" in result["stage_timings"]
    row = conn.execute(
        """
        SELECT pipeline_name, perf_summary_json
          FROM mart_pipeline_run_manifest
         WHERE run_id = 'unit_fresh_preflight'
        """
    ).fetchone()
    perf = json.loads(row[1])
    assert row[0] == "benchmark_tdx_kline_fetch"
    assert perf["preflight"]["network_touched"] is False
    assert "stage_timings" in perf


def test_tdx_kline_fetch_benchmark_records_attempt_and_write_breakdown(monkeypatch):
    conn = duck_mem()
    _seed_price_row(conn, date="2026-05-05")
    monkeypatch.setattr(bench, "get_market_conn", lambda: NoCloseConn(conn))
    monkeypatch.setattr(bench.kline, "load_calendar_target_date", lambda: "2026-05-06")
    monkeypatch.setattr(
        bench.kline,
        "load_local_active_a_stock_list",
        lambda: ([("000001", 0)], "dim_active_a_stock"),
    )

    class FakeClient:
        def bars_records(self, **kwargs):
            assert kwargs["symbol"] == "000001"
            assert "adjust" not in kwargs
            return [
                {
                    "datetime": "2026-05-06",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 1000.0,
                    "amount": 10500.0,
                    "factor": 1.0,
                }
            ]

    def fake_retry(operation, **kwargs):
        assert kwargs["collect_attempts"] is True
        assert kwargs["prefer_last_success"] is False
        attempts = [
            {
                "server": ("1.1.1.1", 7709),
                "ok": True,
                "elapsed_sec": 0.02,
                "lock_wait_sec": 0.0,
                "connect_elapsed_sec": 0.005,
                "operation_elapsed_sec": 0.015,
                "pooled_client": False,
                "rows": 1,
            }
        ]
        return operation(FakeClient()), "tdxhub_1.1.1.1:7709", attempts

    monkeypatch.setattr(bench, "call_tdx_quotes_with_retry", fake_retry)

    result = bench.run_benchmark(
        bench.BenchmarkConfig(run_id="unit_stale_fetch", sample_size=1, pages=1)
    )

    attempts = result["fetch_summary"]["attempts"]
    assert result["preflight"]["network_touched"] is True
    assert result["preflight"]["stale_stock_count"] == 1
    assert result["fetch_summary"]["fetched_stock_count"] == 1
    assert result["fetch_summary"]["normalized_row_count"] == 1
    assert attempts["connect_elapsed_s"] == 0.005
    assert attempts["operation_elapsed_s"] == 0.015
    assert result["write_benchmark"]["temp_rows_written"] == 1
    assert {"fetch_requests_s", "row_decode_normalize_s", "duckdb_write_benchmark_s"} <= set(
        result["stage_timings"]
    )


def test_tdx_kline_fetch_benchmark_can_probe_in_parallel(monkeypatch):
    conn = duck_mem()
    _seed_price_row(conn, code="000001", date="2026-05-05")
    _seed_price_row(conn, code="000002", date="2026-05-05")
    monkeypatch.setattr(bench, "get_market_conn", lambda: NoCloseConn(conn))
    monkeypatch.setattr(bench.kline, "load_calendar_target_date", lambda: "2026-05-06")
    monkeypatch.setattr(
        bench.kline,
        "load_local_active_a_stock_list",
        lambda: ([("000001", 0), ("000002", 0)], "dim_active_a_stock"),
    )
    prefer_last_success_values = []
    symbols = []

    class FakeClient:
        def bars_records(self, **kwargs):
            symbols.append(kwargs["symbol"])
            return [
                {
                    "datetime": "2026-05-06",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "vol": 1000.0,
                    "amount": 10500.0,
                    "factor": 1.0,
                }
            ]

    def fake_retry(operation, **kwargs):
        prefer_last_success_values.append(kwargs["prefer_last_success"])
        attempts = [
            {
                "server": ("1.1.1.1", 7709),
                "ok": True,
                "elapsed_sec": 0.02,
                "lock_wait_sec": 0.0,
                "connect_elapsed_sec": 0.005,
                "operation_elapsed_sec": 0.015,
                "pooled_client": False,
                "rows": 1,
            }
        ]
        return operation(FakeClient()), "tdxhub_1.1.1.1:7709", attempts

    monkeypatch.setattr(bench, "call_tdx_quotes_with_retry", fake_retry)

    result = bench.run_benchmark(
        bench.BenchmarkConfig(run_id="unit_parallel_fetch", sample_size=2, pages=1, workers=2)
    )

    assert result["fetch_summary"]["fetched_stock_count"] == 2
    assert result["fetch_summary"]["normalized_row_count"] == 2
    assert sorted(symbols) == ["000001", "000002"]
    assert prefer_last_success_values == [False, False]
    row = conn.execute(
        """
        SELECT perf_summary_json
          FROM mart_pipeline_run_manifest
         WHERE run_id = 'unit_parallel_fetch'
        """
    ).fetchone()
    assert json.loads(row[0])["config"]["workers"] == 2
