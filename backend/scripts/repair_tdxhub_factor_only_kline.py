#!/usr/bin/env python3
"""Repair factor-only TDXHub daily qfq rows from TDXHub raw bars.

Some historical ``tdxhub`` qfq pulls returned rows with a valid adjustment
factor but NULL OHLCV/amount on corporate-action dates. The canonical view then
falls back to non-TDXHub providers for those dates, which is not acceptable for
model validation. This repair keeps TDXHub as the price source by refetching raw
TDXHub bars and applying the nearest valid TDXHub qfq factor.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from bisect import bisect_right
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STOCK_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(STOCK_ROOT / "tdxhub"))

from build_price_kline_tdxhub import normalize, pull_one_stock, pull_one_stock_with_retry  # noqa: E402
from services.data_processing_monitor import ProcessingToolStats, record_data_processing_tool_run  # noqa: E402
from services.market_db import get_market_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402
from services.tdx_source import call_tdx_quotes_with_retry  # noqa: E402


logger = logging.getLogger("repair_tdxhub_factor_only_kline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

POLICY_ID = "tdxhub_factor_only_repair_v1_raw_bars_nearest_qfq_factor"


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _load_factor_only_rows(conn: Any, *, limit_codes: int = 0) -> dict[str, list[dict[str, Any]]]:
    limit_sql = f"LIMIT {int(limit_codes)}" if limit_codes > 0 else ""
    codes = [
        str(row[0]).zfill(6)
        for row in conn.execute(
            f"""
            SELECT code
              FROM price_kline_tdxhub
             WHERE freq = 'daily'
               AND adjust = 'qfq'
               AND (
                   open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                   OR volume IS NULL OR amount IS NULL
               )
             GROUP BY code
             ORDER BY code
             {limit_sql}
            """
        ).fetchall()
    ]
    if not codes:
        return {}
    rows = conn.execute(
        f"""
        SELECT code, date, factor
          FROM price_kline_tdxhub
         WHERE freq = 'daily'
           AND adjust = 'qfq'
           AND code IN ({", ".join("?" for _ in codes)})
           AND (
               open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
               OR volume IS NULL OR amount IS NULL
           )
         ORDER BY code, date
        """,
        codes,
    ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row[0]).zfill(6), []).append(
            {"code": str(row[0]).zfill(6), "date": str(row[1])[:10], "factor": row[2]}
        )
    return out


def _load_valid_factor_timeline(conn: Any, code: str) -> list[tuple[str, float]]:
    rows = conn.execute(
        """
        SELECT date, factor
          FROM price_kline_tdxhub
         WHERE code = ?
           AND freq = 'daily'
           AND adjust = 'qfq'
           AND open > 0 AND high > 0 AND low > 0 AND close > 0
           AND volume > 0 AND amount > 0
           AND factor > 0
         ORDER BY date
        """,
        (code,),
    ).fetchall()
    return [(str(row[0])[:10], float(row[1])) for row in rows if _finite_positive(row[1])]


def _choose_repair_factor(
    date: str,
    stored_factor: Any,
    timeline: list[tuple[str, float]],
) -> tuple[float | None, str]:
    if timeline:
        dates = [item[0] for item in timeline]
        idx = bisect_right(dates, date)
        if idx < len(timeline):
            return timeline[idx][1], "next_valid_factor"
        if idx > 0:
            return timeline[idx - 1][1], "previous_valid_factor"
    factor = _finite_positive(stored_factor)
    if factor is not None:
        return factor, "stored_factor"
    return None, "missing_factor"


def _raw_row_valid(row: dict[str, Any]) -> bool:
    prices = {field: _finite_positive(row.get(field)) for field in ("open", "high", "low", "close")}
    volume = _finite_positive(row.get("volume"))
    amount = _finite_positive(row.get("amount"))
    if any(value is None for value in prices.values()) or volume is None or amount is None:
        return False
    high = prices["high"] or 0.0
    low = prices["low"] or 0.0
    return high >= max(prices["open"] or 0.0, prices["close"] or 0.0, low) and low <= min(
        prices["open"] or 0.0,
        prices["close"] or 0.0,
        high,
    )


def _repair_rows_for_code(
    conn: Any,
    *,
    client: Any,
    client_source: str,
    code: str,
    missing_rows: list[dict[str, Any]],
    batch_id: str,
    pages: int,
    per_stock_retry_attempts: int,
    connect_timeout: float,
    dry_run: bool,
    stats: ProcessingToolStats,
) -> dict[str, int]:
    try:
        records = pull_one_stock(client, code, pages=pages, adjust=None, raise_errors=True)
        source_name = f"{client_source}_raw_factor_only_repair"
    except Exception as exc:
        if per_stock_retry_attempts <= 0:
            for row in missing_rows:
                stats.reject(["tdxhub_raw_fetch_failed"], sample={"code": code, "date": row["date"], "error": str(exc)})
            return {"repaired": 0, "rejected": len(missing_rows)}
        try:
            records, retry_source = pull_one_stock_with_retry(
                code,
                pages=pages,
                adjust=None,
                max_attempts=per_stock_retry_attempts,
                connect_timeout=connect_timeout,
            )
            source_name = f"{retry_source}_raw_factor_only_repair"
        except Exception as retry_exc:
            for row in missing_rows:
                stats.reject(
                    ["tdxhub_raw_fetch_failed"],
                    sample={"code": code, "date": row["date"], "error": str(retry_exc)},
                )
            return {"repaired": 0, "rejected": len(missing_rows)}

    raw_by_date = {
        row["date"]: row
        for row in normalize(records, batch_id, source_name=source_name)
        if row.get("date")
    }
    timeline = _load_valid_factor_timeline(conn, code)
    updates = []
    factor_sources: dict[str, int] = {}
    for missing in missing_rows:
        date = missing["date"]
        raw = raw_by_date.get(date)
        if raw is None:
            stats.reject(["tdxhub_raw_date_missing"], sample={"code": code, "date": date})
            continue
        if not _raw_row_valid(raw):
            stats.reject(["tdxhub_raw_invalid_ohlcv"], sample={"code": code, "date": date, "raw": raw})
            continue
        factor, factor_source = _choose_repair_factor(date, missing.get("factor"), timeline)
        if factor is None:
            stats.reject(["tdxhub_qfq_factor_missing"], sample={"code": code, "date": date})
            continue
        factor_sources[factor_source] = factor_sources.get(factor_source, 0) + 1
        updates.append(
            (
                float(raw["open"]) * factor,
                float(raw["high"]) * factor,
                float(raw["low"]) * factor,
                float(raw["close"]) * factor,
                float(raw["volume"]),
                float(raw["amount"]),
                factor,
                f"{source_name}_{factor_source}",
                batch_id,
                code,
                date,
            )
        )
        stats.accept()

    if updates and not dry_run:
        conn.executemany(
            """
            UPDATE price_kline_tdxhub
               SET open = ?,
                   high = ?,
                   low = ?,
                   close = ?,
                   volume = ?,
                   amount = ?,
                   factor = ?,
                   source = ?,
                   batch_id = ?,
                   ingested_at = CURRENT_TIMESTAMP
             WHERE code = ?
               AND date = ?
               AND freq = 'daily'
               AND adjust = 'qfq'
            """,
            updates,
        )
    return {
        "repaired": len(updates),
        "rejected": max(len(missing_rows) - len(updates), 0),
        **{f"factor_source:{key}": value for key, value in factor_sources.items()},
    }


def repair_factor_only_rows(
    *,
    limit_codes: int = 0,
    pages: int = 4,
    connect_timeout: float = 1.2,
    max_server_attempts: int = 20,
    per_stock_retry_attempts: int = 3,
    log_every: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    conn = get_market_conn()
    started_at = utc_now_iso()
    started = time.perf_counter()
    batch_id = f"tdxhub_factor_only_repair_{time.strftime('%Y%m%d_%H%M%S')}"
    stats = ProcessingToolStats(
        tool_name="repair_tdxhub_factor_only_kline",
        policy_id=POLICY_ID,
        source_name="tdxhub_raw",
    )
    try:
        grouped = _load_factor_only_rows(conn, limit_codes=limit_codes)
        total_missing = sum(len(rows) for rows in grouped.values())
        logger.info("factor-only rows: codes=%d rows=%d dry_run=%s", len(grouped), total_missing, dry_run)
        if not grouped:
            return {"status": "success", "batch_id": batch_id, "codes": 0, "input_rows": 0, "repaired_rows": 0}

        (ok, client), client_source = call_tdx_quotes_with_retry(
            lambda client: (True, client),
            action_name="repair_tdxhub_factor_only_kline.open",
            max_attempts=max_server_attempts,
            connect_timeout=connect_timeout,
        )
        if not ok:
            raise RuntimeError("tdxhub client open failed")

        repaired = 0
        rejected = 0
        factor_sources: dict[str, int] = {}
        for idx, (code, rows) in enumerate(grouped.items(), start=1):
            result = _repair_rows_for_code(
                conn,
                client=client,
                client_source=client_source,
                code=code,
                missing_rows=rows,
                batch_id=batch_id,
                pages=pages,
                per_stock_retry_attempts=per_stock_retry_attempts,
                connect_timeout=connect_timeout,
                dry_run=dry_run,
                stats=stats,
            )
            repaired += int(result.get("repaired") or 0)
            rejected += int(result.get("rejected") or 0)
            for key, value in result.items():
                if key.startswith("factor_source:"):
                    factor_sources[key.split(":", 1)[1]] = factor_sources.get(key.split(":", 1)[1], 0) + int(value)
            if idx % max(1, log_every) == 0:
                if not dry_run:
                    conn.commit()
                elapsed = time.perf_counter() - started
                logger.info(
                    "progress %d/%d repaired=%d rejected=%d rate=%.2f code/s",
                    idx,
                    len(grouped),
                    repaired,
                    rejected,
                    idx / elapsed if elapsed > 0 else 0.0,
                )

        remaining = int(
            conn.execute(
                """
                SELECT COUNT(*)
                  FROM price_kline_tdxhub
                 WHERE freq = 'daily'
                   AND adjust = 'qfq'
                   AND (
                       open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                       OR volume IS NULL OR amount IS NULL
                   )
                """
            ).fetchone()[0]
            or 0
        )
        duration_s = round(time.perf_counter() - started, 3)
        status = "success" if rejected == 0 else "partial"
        if not dry_run:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_import_batch (
                    batch_id, source_type, source_name, freq, adjust, rows_imported,
                    min_date, max_date, started_at, finished_at, status, detail
                )
                SELECT ?, 'repair', 'tdxhub_raw_factor_only_repair', 'daily', 'qfq',
                       ?, MIN(date), MAX(date), ?, ?, ?, ?
                  FROM price_kline_tdxhub
                 WHERE batch_id = ?
                """,
                (
                    batch_id,
                    repaired,
                    started_at,
                    utc_now_iso(),
                    status,
                    json.dumps(
                        {
                            "policy_id": POLICY_ID,
                            "input_rows": total_missing,
                            "repaired_rows": repaired,
                            "rejected_rows": rejected,
                            "factor_sources": factor_sources,
                            "dry_run": dry_run,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    batch_id,
                ),
            )
        record_data_processing_tool_run(
            conn,
            stats=stats,
            run_id=batch_id,
            status=status,
            input_table="price_kline_tdxhub.factor_only_rows",
            output_table="price_kline_tdxhub",
            batch_id=batch_id,
            metadata={
                "pages": pages,
                "connect_timeout": connect_timeout,
                "max_server_attempts": max_server_attempts,
                "per_stock_retry_attempts": per_stock_retry_attempts,
                "factor_sources": factor_sources,
                "remaining_factor_only_rows": remaining,
                "dry_run": dry_run,
            },
            record_clean_runs=True,
        )
        record_actual_version(conn, "price_kline_tdxhub")
        record_pipeline_run(
            conn,
            run_id=batch_id,
            pipeline_name="repair_tdxhub_factor_only_kline",
            status=status,
            started_at=started_at,
            ended_at=utc_now_iso(),
            duration_s=duration_s,
            commit_sha=git_commit_sha(),
            input_tables=["price_kline_tdxhub", "tdxhub.raw_bars"],
            output_tables=["price_kline_tdxhub", "mart_data_processing_tool_run"],
            perf_summary={
                "stage_timings": {"repair_factor_only_rows_s": duration_s},
                "codes": len(grouped),
                "input_rows": total_missing,
                "repaired_rows": repaired,
                "rejected_rows": rejected,
                "remaining_factor_only_rows": remaining,
                "factor_sources": factor_sources,
                "dry_run": dry_run,
            },
        )
        conn.commit()
        return {
            "status": status,
            "batch_id": batch_id,
            "codes": len(grouped),
            "input_rows": total_missing,
            "repaired_rows": repaired,
            "rejected_rows": rejected,
            "remaining_factor_only_rows": remaining,
            "duration_s": duration_s,
            "factor_sources": factor_sources,
            "dry_run": dry_run,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-codes", type=int, default=0)
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--connect-timeout", type=float, default=1.2)
    parser.add_argument("--max-server-attempts", type=int, default=20)
    parser.add_argument("--per-stock-retry-attempts", type=int, default=3)
    parser.add_argument("--log-every", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = repair_factor_only_rows(
        limit_codes=args.limit_codes,
        pages=args.pages,
        connect_timeout=args.connect_timeout,
        max_server_attempts=args.max_server_attempts,
        per_stock_retry_attempts=args.per_stock_retry_attempts,
        log_every=args.log_every,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
