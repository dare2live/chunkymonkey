#!/usr/bin/env python3
"""Probe free fund-flow data coverage before allowing it into model training."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import get_conn


logger = logging.getLogger("fund_flow_probe")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_fund_flow_probe (
    run_id TEXT PRIMARY KEY,
    sample_size INTEGER,
    ok_count INTEGER,
    failed_count INTEGER,
    avg_rows REAL,
    min_date TEXT,
    max_date TEXT,
    avg_coverage_days REAL,
    coverage_ratio_100d REAL,
    fields_json TEXT,
    failures_json TEXT,
    decision TEXT,
    built_at TEXT
);
"""


def _market(code: str) -> str:
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def load_sample_codes(conn, limit: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT stock_code
        FROM fact_feature_panel
        WHERE date = (SELECT MAX(date) FROM fact_feature_panel)
          AND ret_20d IS NOT NULL
        ORDER BY ABS(HASH(stock_code))
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--min-coverage", type=float, default=0.90)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError("akshare 不可用, 无法做 fund-flow probe") from exc

    conn = get_conn()
    try:
        codes = load_sample_codes(conn, args.sample_size)
        stats: list[dict] = []
        failures: list[dict] = []
        fields: set[str] = set()
        for code in codes:
            try:
                df = ak.stock_individual_fund_flow(stock=code, market=_market(code))
                fields.update(map(str, df.columns))
                date_col = "日期" if "日期" in df.columns else df.columns[0]
                dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
                stats.append({
                    "code": code,
                    "rows": len(df),
                    "min_date": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
                    "max_date": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
                    "coverage_days": int(dates.nunique()),
                })
                logger.info("%s rows=%d range=%s~%s", code, len(df), stats[-1]["min_date"], stats[-1]["max_date"])
            except Exception as exc:
                failures.append({"code": code, "error": str(exc)[:240]})
                logger.warning("%s failed: %s", code, exc)

        ok_count = len(stats)
        failed_count = len(failures)
        avg_rows = float(pd.Series([s["rows"] for s in stats]).mean()) if stats else 0.0
        avg_cov = float(pd.Series([s["coverage_days"] for s in stats]).mean()) if stats else 0.0
        coverage_ratio = ok_count / max(len(codes), 1)
        min_date = min([s["min_date"] for s in stats if s["min_date"]], default=None)
        max_date = max([s["max_date"] for s in stats if s["max_date"]], default=None)
        decision = "pass_probe_only" if coverage_ratio >= args.min_coverage and avg_cov >= 80 else "reject_for_training"
        payload = {
            "sample_size": len(codes),
            "ok_count": ok_count,
            "failed_count": failed_count,
            "avg_rows": avg_rows,
            "min_date": min_date,
            "max_date": max_date,
            "avg_coverage_days": avg_cov,
            "coverage_ratio_100d": coverage_ratio,
            "fields": sorted(fields),
            "failures": failures,
            "decision": decision,
        }
        logger.info("probe result: %s", json.dumps(payload, ensure_ascii=False))
        if args.write:
            conn.executescript(DDL)
            run_id = f"fund_flow_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            conn.execute(
                """
                INSERT OR REPLACE INTO mart_fund_flow_probe
                (run_id, sample_size, ok_count, failed_count, avg_rows, min_date, max_date,
                 avg_coverage_days, coverage_ratio_100d, fields_json, failures_json, decision, built_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, len(codes), ok_count, failed_count, avg_rows, min_date, max_date,
                    avg_cov, coverage_ratio, json.dumps(sorted(fields), ensure_ascii=False),
                    json.dumps(failures, ensure_ascii=False), decision, datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            logger.info("written mart_fund_flow_probe run_id=%s", run_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
