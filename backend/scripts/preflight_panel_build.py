#!/usr/bin/env python3
"""Pre-flight gate for feature/label panel builds."""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.labels.universe import pit_active_ever
from services.utils import latest_completed_trade_date

SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"

log = logging.getLogger("preflight_panel_build")


@dataclass(frozen=True)
class PreflightConfig:
    current_date: date
    lookback_days: int = 30
    min_coverage_pct: float = 0.95
    watermark_sla_days: int = 7
    kline_relation: str = "v_price_kline_qfq"


class PreflightError(RuntimeError):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def compare_panel_row_counts(
    conn: Any,
    *,
    baseline_table: str = "mart_p0a_feature_label_panel_v4",
    candidate_table: str = "mart_p0a_feature_label_panel_v6",
) -> dict:
    """Print and enforce candidate panel row count >= baseline panel row count."""
    missing = [table for table in (baseline_table, candidate_table) if not _table_exists(conn, table)]
    if missing:
        raise PreflightError([f"panel row count compare missing tables: {missing}"])

    baseline_rows = int(conn.execute(f"SELECT COUNT(*) FROM {baseline_table}").fetchone()[0])
    candidate_rows = int(conn.execute(f"SELECT COUNT(*) FROM {candidate_table}").fetchone()[0])
    diff = candidate_rows - baseline_rows
    print(
        f"panel row count diff: {baseline_table}={baseline_rows} "
        f"{candidate_table}={candidate_rows} diff={diff}"
    )
    if diff < 0:
        raise PreflightError([
            f"{candidate_table} row count decreased vs {baseline_table}: diff={diff}"
        ])
    return {
        "baseline_table": baseline_table,
        "candidate_table": candidate_table,
        "baseline_rows": baseline_rows,
        "candidate_rows": candidate_rows,
        "diff": diff,
    }


def _max_kline_watermark(smart_conn: Any) -> date | None:
    cols = _columns(smart_conn, "mart_data_source_watermark")
    if not cols:
        return None
    date_col = "max_data_date" if "max_data_date" in cols else "last_data_date" if "last_data_date" in cols else None
    if date_col is None:
        return None

    rows = smart_conn.execute(
        f"""
        SELECT {date_col}
        FROM mart_data_source_watermark
        WHERE data_domain LIKE '%kline%'
           OR source_name LIKE '%kline%'
           OR source_name LIKE '%quote%'
        """
    ).fetchall()
    parsed = [d for d in (_parse_date(r[0]) for r in rows) if d is not None]
    return max(parsed) if parsed else None


def check_watermark_freshness(smart_conn: Any, config: PreflightConfig) -> dict:
    max_date = _max_kline_watermark(smart_conn)
    if max_date is None:
        return {"ok": False, "error": "mart_data_source_watermark has no parseable kline max data date"}
    lag_days = (config.current_date - max_date).days
    return {
        "ok": lag_days <= config.watermark_sla_days,
        "max_data_date": max_date.isoformat(),
        "lag_days": lag_days,
        "sla_days": config.watermark_sla_days,
    }


def check_kline_coverage(smart_conn: Any, market_conn: Any, config: PreflightConfig) -> dict:
    start_date = config.current_date - timedelta(days=config.lookback_days)
    rows = market_conn.execute(
        f"""
        SELECT TRY_CAST(date AS DATE) AS trade_date, COUNT(DISTINCT code) AS n_codes
        FROM {config.kline_relation}
        WHERE freq = 'daily'
          AND adjust = 'qfq'
          AND TRY_CAST(date AS DATE) >= ?
          AND TRY_CAST(date AS DATE) <= ?
        GROUP BY trade_date
        ORDER BY trade_date
        """,
        [start_date, config.current_date],
    ).fetchall()
    if not rows:
        return {"ok": False, "error": "v_price_kline_qfq has no rows in recent lookback window"}

    failures = []
    checked = []
    for row in rows:
        trade_date = _parse_date(row[0])
        n_codes = int(row[1])
        if trade_date is None:
            continue
        universe = len(pit_active_ever(smart_conn, trade_date))
        threshold = universe * config.min_coverage_pct
        item = {
            "trade_date": trade_date.isoformat(),
            "n_codes": n_codes,
            "universe": universe,
            "min_required": threshold,
            "coverage_pct": n_codes / universe if universe else 0.0,
        }
        checked.append(item)
        if universe <= 0 or n_codes < threshold:
            failures.append(item)

    return {
        "ok": not failures,
        "checked_days": len(checked),
        "failures": failures,
    }


def run_preflight(smart_conn: Any, market_conn: Any, config: PreflightConfig) -> dict:
    coverage = check_kline_coverage(smart_conn, market_conn, config)
    watermark = check_watermark_freshness(smart_conn, config)

    errors = []
    if not coverage["ok"]:
        if "error" in coverage:
            errors.append(coverage["error"])
        else:
            sample = coverage["failures"][:5]
            errors.append(f"kline coverage below threshold: {sample}")
    if not watermark["ok"]:
        if "error" in watermark:
            errors.append(watermark["error"])
        else:
            errors.append(
                "kline watermark stale: "
                f"max_data_date={watermark['max_data_date']} "
                f"lag_days={watermark['lag_days']} sla_days={watermark['sla_days']}"
            )

    if errors:
        raise PreflightError(errors)

    return {"ok": True, "coverage": coverage, "watermark": watermark}


def run_preflight_or_exit(smart_conn: Any, market_conn: Any, config: PreflightConfig) -> dict:
    try:
        result = run_preflight(smart_conn, market_conn, config)
    except PreflightError as exc:
        for error in exc.errors:
            log.error(error)
        sys.exit(1)

    log.info(
        "preflight ok: checked_days=%s watermark_max_data_date=%s lag_days=%s",
        result["coverage"]["checked_days"],
        result["watermark"]["max_data_date"],
        result["watermark"]["lag_days"],
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run panel build pre-flight gate")
    parser.add_argument("--smart-db", default=str(SMART_DB))
    parser.add_argument("--market-db", default=str(MARKET_DB))
    parser.add_argument("--current-date", default=None,
                        help="YYYY-MM-DD; default latest_completed_trade_date(smart_db)")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--min-coverage-pct", type=float, default=0.95)
    parser.add_argument("--watermark-sla-days", type=int, default=7)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    smart_conn = duckdb.connect(args.smart_db, read_only=True)
    market_conn = duckdb.connect(args.market_db, read_only=True)
    try:
        if args.current_date:
            current = _parse_date(args.current_date) or date.fromisoformat(args.current_date)
        else:
            current_str = latest_completed_trade_date(smart_conn)
            if not current_str:
                log.error("latest_completed_trade_date returned None — kline 数据缺失? 拒启动")
                return 2
            current = date.fromisoformat(current_str)
        config = PreflightConfig(
            current_date=current,
            lookback_days=args.lookback_days,
            min_coverage_pct=args.min_coverage_pct,
            watermark_sla_days=args.watermark_sla_days,
        )
        run_preflight_or_exit(smart_conn, market_conn, config)
    finally:
        smart_conn.close()
        market_conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
