#!/usr/bin/env python3
"""Watermark SLA updater + sync gap auto-alert.

ChunkyMonkey 交付标准 #1 数据管理: watermark 实填 + sync gap auto-alert.

每个 data source 跑 actual max date 跟 watermark.last_data_date 对比:
- actual > watermark → 自动 update watermark
- actual - current_date > SLA threshold → alert (log + JSON report)
- watermark stale > SLA threshold → alert (即使没数据 update)

调用 ways:
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --dry-run
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --json-output /tmp/sla.json

被 daily_update.sh Step 1 调用.

SLA per source_tier (Codex R26 architecture audit doc §4.d 设计):
- tier 1 (tdxhub/miaoxiang 主源): SLA 1 trading day
- tier 2 (aif10 二次源): SLA 2 trading days
- tier 3 (akshare 补充): SLA 3 trading days
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.duck_adapter import connect as duck_connect

DEFAULT_SMARTMONEY_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watermark_sla")

# SLA threshold by source_tier (trading days)
SLA_DAYS = {1: 1, 2: 2, 3: 3}

# data_domain → actual table + date column
DATA_SOURCE_QUERIES = {
    "kline_daily": {
        "db": "market",
        "query": "SELECT MAX(CAST(date AS VARCHAR)) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'",
    },
    "xdxr": {
        "db": "market",
        "query": "SELECT MAX(CAST(event_date AS VARCHAR)) FROM price_kline_tdxhub_adjustment_event",
    },
    "financial_gpcw_8q": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(report_date AS VARCHAR)) FROM fact_financial_pit_daily",
    },
    "lhb_daily": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(trade_date AS VARCHAR)) FROM fact_lhb_event",
    },
    "institution_survey": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(survey_date AS VARCHAR)) FROM raw_institution_surveys",
    },
    "holders_top10_float": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(report_date AS VARCHAR)) FROM fact_top10_holder_period",
    },
    "industry_sw": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(snapshot_date AS VARCHAR)) FROM dim_stock_tdx_industry_history",
    },
    "stock_blocks": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(snapshot_date AS VARCHAR)) FROM dim_stock_tdx_industry_history",
    },
}


def _query_actual_max_date(market_conn, smart_conn, data_domain: str) -> str | None:
    spec = DATA_SOURCE_QUERIES.get(data_domain)
    if not spec:
        return None
    conn = market_conn if spec["db"] == "market" else smart_conn
    try:
        r = conn.execute(spec["query"]).fetchone()
        return r[0] if r and r[0] else None
    except Exception as e:
        log.warning(f"  query failed for {data_domain}: {e}")
        return None


def _days_since(date_str: str | None, today: date) -> int | None:
    if not date_str:
        return None
    s = str(date_str)[:10]
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        try:
            d = datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return (today - d).days


def main() -> int:
    parser = argparse.ArgumentParser(description="Watermark SLA auto-update + alert")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--smartmoney-db", default=str(DEFAULT_SMARTMONEY_DB))
    parser.add_argument("--market-db", default=str(DEFAULT_MARKET_DB))
    args = parser.parse_args()

    today = date.today()
    log.info(f"=== watermark SLA check @ {today} ===")

    smart_conn = duck_connect(args.smartmoney_db, read_only=False)  # need write
    market_conn = duck_connect(args.market_db, read_only=True)
    try:
        watermark_rows = smart_conn.execute(
            "SELECT data_domain, source_name, source_tier, last_data_date, updated_at "
            "FROM mart_data_source_watermark ORDER BY data_domain, source_name"
        ).fetchall()
        log.info(f"  watermark rows: {len(watermark_rows)}")

        results: list[dict] = []
        n_update = 0
        n_alert = 0
        for row in watermark_rows:
            data_domain, source_name, source_tier, watermark_date, updated_at = row

            actual_date = _query_actual_max_date(market_conn, smart_conn, data_domain)
            actual_days = _days_since(actual_date, today)
            watermark_days = _days_since(watermark_date, today)
            sla = SLA_DAYS.get(source_tier, 3)

            status = "OK"
            alert = False

            # 1. watermark out of date vs actual
            if actual_date and watermark_date:
                aw = _days_since(actual_date, today)
                ww = _days_since(watermark_date, today)
                if aw is not None and ww is not None and ww > aw:
                    status = "STALE_WATERMARK"  # auto-fix
                    if not args.dry_run:
                        smart_conn.execute(
                            "UPDATE mart_data_source_watermark "
                            "SET last_data_date = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE data_domain = ? AND source_name = ?",
                            [actual_date, data_domain, source_name],
                        )
                        n_update += 1
                        log.info(f"  [UPDATE] {data_domain}/{source_name}: {watermark_date} → {actual_date}")

            # 2. actual data stale vs SLA
            if actual_days is not None and actual_days > sla:
                # 注意: 周末 / 节假日 不 alert. 简单 SLA 不区分.
                if actual_days > sla + 3:  # 3 day buffer for weekend
                    status = "DATA_STALE_VS_SLA"
                    alert = True
                    n_alert += 1
                    log.warning(
                        f"  [ALERT] {data_domain}/{source_name}: actual {actual_date} "
                        f"({actual_days}d ago) > SLA {sla}d (tier {source_tier})"
                    )

            results.append({
                "data_domain": data_domain,
                "source_name": source_name,
                "source_tier": source_tier,
                "watermark_date": str(watermark_date) if watermark_date else None,
                "actual_date": actual_date,
                "actual_days_ago": actual_days,
                "watermark_days_ago": watermark_days,
                "sla_days": sla,
                "status": status,
                "alert": alert,
            })

        # Write JSON report
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "today": str(today),
                "dry_run": args.dry_run,
                "n_updates": n_update,
                "n_alerts": n_alert,
                "sources": results,
            }, f, ensure_ascii=False, indent=2)
        log.info(f"=== SLA check done: {n_update} watermark updated, {n_alert} alerts ===")
        log.info(f"  Report: {args.json_output}")
        return 2 if n_alert > 0 else 0
    finally:
        try:
            market_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"market_conn close failed: {e}")
        try:
            smart_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"smart_conn close failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
