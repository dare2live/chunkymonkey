#!/usr/bin/env python3
"""Benchmark TDXHub K-line fetch latency after local freshness preflight.

This script is diagnostic by default. It records pipeline-manifest timing
evidence and uses a temporary DuckDB table for write timing, so a fresh local
K-line store does not trigger unnecessary network requests or mutate
production price rows.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.market_db import get_market_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.pipeline_timing import PipelineTimer  # noqa: E402
from services.tdx_source import call_tdx_quotes_with_retry  # noqa: E402

import build_price_kline_tdxhub as kline  # noqa: E402


logger = logging.getLogger("tdx_kline_fetch_benchmark")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


@dataclass(frozen=True)
class BenchmarkConfig:
    run_id: str
    sample_size: int = 10
    limit: int = 0
    pages: int = 1
    adjust_mode: str = "raw"
    target_date: str | None = None
    connect_timeout: float = 1.5
    max_server_attempts: int = 8
    allow_stock_list_network: bool = False
    probe_when_fresh: bool = False
    write_benchmark: bool = True


def _source_for_adjust(source_name: str, adjust_mode: str) -> str:
    if adjust_mode == "raw" and "raw_incremental" not in source_name:
        return f"{source_name}_raw_incremental"
    return source_name


def _attempt_server_text(attempt: dict[str, Any]) -> str:
    server = attempt.get("server")
    if isinstance(server, (list, tuple)) and len(server) == 2:
        return f"{server[0]}:{server[1]}"
    return str(server or "")


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    errors: dict[str, int] = {}
    servers: set[str] = set()
    total_elapsed = 0.0
    total_connect = 0.0
    total_operation = 0.0
    total_lock_wait = 0.0
    ok_count = 0
    pooled_count = 0
    for attempt in attempts:
        servers.add(_attempt_server_text(attempt))
        total_elapsed += float(attempt.get("elapsed_sec") or 0.0)
        total_connect += float(attempt.get("connect_elapsed_sec") or 0.0)
        total_operation += float(attempt.get("operation_elapsed_sec") or 0.0)
        total_lock_wait += float(attempt.get("lock_wait_sec") or 0.0)
        if attempt.get("pooled_client"):
            pooled_count += 1
        if attempt.get("ok"):
            ok_count += 1
        else:
            key = str(attempt.get("error_type") or "error")
            errors[key] = errors.get(key, 0) + 1
    return {
        "attempt_count": len(attempts),
        "ok_attempt_count": ok_count,
        "failed_attempt_count": len(attempts) - ok_count,
        "pooled_attempt_count": pooled_count,
        "server_count": len([item for item in servers if item]),
        "servers": sorted(item for item in servers if item),
        "error_counts": errors,
        "attempt_elapsed_s": round(total_elapsed, 3),
        "connect_elapsed_s": round(total_connect, 3),
        "operation_elapsed_s": round(total_operation, 3),
        "lock_wait_s": round(total_lock_wait, 3),
    }


def fetch_stock_with_attempts(
    code: str,
    *,
    pages: int,
    adjust_mode: str,
    connect_timeout: float,
    max_server_attempts: int,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    adjust = None if adjust_mode == "raw" else "qfq"
    try:
        records, source_name, attempts = call_tdx_quotes_with_retry(
            lambda client: kline.pull_one_stock(
                client,
                code,
                pages=pages,
                adjust=adjust,
                raise_errors=True,
            ),
            action_name=f"benchmark_tdx_kline_fetch.bars[{code}]",
            collect_attempts=True,
            max_attempts=max_server_attempts,
            connect_timeout=connect_timeout,
            prefer_last_success=False,
        )
        return list(records), _source_for_adjust(source_name, adjust_mode), list(attempts)
    except Exception as exc:
        attempts = list(getattr(exc, "tdx_attempts", []) or [])
        error = RuntimeError(f"{code} fetch failed: {exc}")
        setattr(error, "tdx_attempts", attempts)
        raise error from exc


def write_temp_kline_rows(conn: Any, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_tdx_kline_fetch_benchmark_write (
            code TEXT,
            date TEXT,
            freq TEXT,
            adjust TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            factor REAL,
            source TEXT,
            batch_id TEXT
        )
        """
    )
    conn.execute("DELETE FROM tmp_tdx_kline_fetch_benchmark_write")
    conn.executemany(
        """
        INSERT INTO tmp_tdx_kline_fetch_benchmark_write
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["code"],
                row["date"],
                row["freq"],
                row["adjust"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["amount"],
                row["factor"],
                row["source"],
                row["batch_id"],
            )
            for row in rows
        ],
    )
    return len(rows)


def _limited(stock_list: list[tuple[str, int]], limit: int) -> list[tuple[str, int]]:
    if limit and limit > 0:
        return stock_list[:limit]
    return stock_list


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    started_at = utc_now_iso()
    started = time.perf_counter()
    timer = PipelineTimer()
    blockers: list[str] = []
    conn = get_market_conn()
    try:
        with timer.stage("ensure_price_table_s"):
            conn.executescript(kline.TABLE_DDL)

        with timer.stage("latest_date_scan_s"):
            latest_dates = kline.load_latest_dates(conn)

        with timer.stage("calendar_preflight_s"):
            target_date, target_source = kline.choose_incremental_target_date(conn, config.target_date)

        with timer.stage("local_active_universe_s"):
            stock_list, stock_list_source = kline.load_local_active_a_stock_list()

        if not stock_list and config.allow_stock_list_network:
            with timer.stage("tdx_stock_list_fetch_s"):
                stock_list, _client, stock_list_source = kline.open_quotes_client_with_retry(
                    max_attempts=config.max_server_attempts,
                    connect_timeout=config.connect_timeout,
                )
        elif not stock_list:
            blockers.append(f"local_active_universe_unavailable:{stock_list_source}")

        stock_list = _limited(stock_list, config.limit)
        with timer.stage("stale_filter_s"):
            stale_stock_list = (
                kline.filter_stale_stock_list(stock_list, latest_dates, target_date)
                if target_date
                else list(stock_list)
            )

        network_reason = "stale_stock_sample"
        sample_stock_list = stale_stock_list[: max(0, int(config.sample_size))]
        if not sample_stock_list and config.probe_when_fresh and stock_list:
            sample_stock_list = stock_list[: max(0, int(config.sample_size))]
            network_reason = "forced_probe_when_fresh"

        fetched: list[dict[str, Any]] = []
        raw_payloads: list[tuple[list[dict[str, Any]], str]] = []
        normalized_rows: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        failed_codes: list[str] = []
        if sample_stock_list:
            with timer.stage("fetch_requests_s"):
                for code, _market in sample_stock_list:
                    try:
                        records, source_name, stock_attempts = fetch_stock_with_attempts(
                            code,
                            pages=config.pages,
                            adjust_mode=config.adjust_mode,
                            connect_timeout=config.connect_timeout,
                            max_server_attempts=config.max_server_attempts,
                        )
                    except Exception as exc:
                        logger.warning("code=%s benchmark fetch failed: %s", code, exc)
                        attempts.extend(list(getattr(exc, "tdx_attempts", []) or []))
                        failed_codes.append(code)
                        continue
                    fetched.append(
                        {
                            "code": code,
                            "source": source_name,
                            "raw_row_count": len(records),
                            "attempt_count": len(stock_attempts),
                        }
                    )
                    attempts.extend(stock_attempts)
                    raw_payloads.append((records, source_name))

        with timer.stage("row_decode_normalize_s"):
            for records, source_name in raw_payloads:
                normalized_rows.extend(kline.normalize(records, config.run_id, source_name=source_name))

        xdxr_gap_event_count = 0
        xdxr_gap_code_count = 0
        with timer.stage("xdxr_gap_scan_s"):
            if config.adjust_mode == "raw" and target_date and sample_stock_list:
                xdxr_events = kline.load_xdxr_gap_events(conn, latest_dates, target_date)
                selected_codes = {code for code, _market in sample_stock_list}
                xdxr_events = {code: events for code, events in xdxr_events.items() if code in selected_codes}
                xdxr_gap_code_count = len(xdxr_events)
                xdxr_gap_event_count = sum(len(events) for events in xdxr_events.values())

        with timer.stage("qfq_adjustment_s"):
            qfq_adjustment_mode = (
                "not_required_for_qfq_fetch"
                if config.adjust_mode == "qfq"
                else "dry_run_requires_production_fetch_for_mutating_adjustment"
            )

        temp_write_rows = 0
        if config.write_benchmark:
            with timer.stage("duckdb_write_benchmark_s"):
                temp_write_rows = write_temp_kline_rows(conn, normalized_rows)

        ended_at = utc_now_iso()
        duration_s = round(time.perf_counter() - started, 3)
        preflight = {
            "target_date": target_date,
            "target_source": target_source,
            "stock_list_source": stock_list_source,
            "latest_date_stock_count": len(latest_dates),
            "stock_count": len(stock_list),
            "stale_stock_count": len(stale_stock_list),
            "sample_stock_count": len(sample_stock_list),
            "network_reason": network_reason if sample_stock_list else "no_stale_stock_no_network",
            "network_touched": bool(sample_stock_list),
        }
        fetch_summary = {
            "fetched_stock_count": len(fetched),
            "failed_stock_count": len(failed_codes),
            "failed_codes": failed_codes[:20],
            "raw_row_count": sum(int(item.get("raw_row_count") or 0) for item in fetched),
            "normalized_row_count": len(normalized_rows),
            "attempts": summarize_attempts(attempts),
            "sample": fetched[:20],
        }
        result = {
            "run_id": config.run_id,
            "pipeline_name": "benchmark_tdx_kline_fetch",
            "duration_s": duration_s,
            "gate_result": "blocked" if blockers else "pass",
            "blockers": blockers,
            "preflight": preflight,
            "fetch_summary": fetch_summary,
            "write_benchmark": {
                "enabled": config.write_benchmark,
                "temp_rows_written": temp_write_rows,
            },
            "qfq_adjustment": {
                "mode": qfq_adjustment_mode,
                "xdxr_gap_code_count": xdxr_gap_code_count,
                "xdxr_gap_event_count": xdxr_gap_event_count,
            },
            "stage_timings": dict(timer.stage_timings),
        }
        record_pipeline_run(
            conn,
            run_id=config.run_id,
            pipeline_name="benchmark_tdx_kline_fetch",
            status="blocked" if blockers else "success",
            started_at=started_at,
            ended_at=ended_at,
            duration_s=duration_s,
            commit_sha=git_commit_sha(),
            input_tables=["price_kline_tdxhub"],
            output_tables=["mart_pipeline_run_manifest"],
            gate_result=result["gate_result"],
            blockers=blockers,
            perf_summary={
                "stage_timings": dict(timer.stage_timings),
                "config": config.__dict__,
                "preflight": preflight,
                "fetch_summary": fetch_summary,
                "write_benchmark": result["write_benchmark"],
                "qfq_adjustment": result["qfq_adjustment"],
            },
        )
        return result
    finally:
        conn.close()


def parse_args(argv: list[str] | None = None) -> BenchmarkConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=f"tdx_kline_fetch_benchmark_{time.strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--adjust-mode", choices=["raw", "qfq"], default="raw")
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--connect-timeout", type=float, default=1.5)
    parser.add_argument("--max-server-attempts", type=int, default=8)
    parser.add_argument("--allow-stock-list-network", action="store_true")
    parser.add_argument("--probe-when-fresh", action="store_true")
    parser.add_argument("--no-write-benchmark", action="store_true")
    args = parser.parse_args(argv)
    return BenchmarkConfig(
        run_id=args.run_id,
        sample_size=max(0, int(args.sample_size)),
        limit=max(0, int(args.limit)),
        pages=max(1, int(args.pages)),
        adjust_mode=str(args.adjust_mode),
        target_date=args.target_date,
        connect_timeout=float(args.connect_timeout),
        max_server_attempts=max(1, int(args.max_server_attempts)),
        allow_stock_list_network=bool(args.allow_stock_list_network),
        probe_when_fresh=bool(args.probe_when_fresh),
        write_benchmark=not bool(args.no_write_benchmark),
    )


def main(argv: list[str] | None = None) -> None:
    config = parse_args(argv)
    result = run_benchmark(config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
