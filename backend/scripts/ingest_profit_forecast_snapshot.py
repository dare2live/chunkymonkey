#!/usr/bin/env python3
"""Daily immutable PIT snapshot of analyst profit forecast EPS consensus.

Codex round 19+ verdict: 现 latest snapshot (5 天 ingest) 不能回灌历史训练 (LEAKAGE).
方案: 从今天起 daily snapshot 累积, 不覆盖历史. 数月后才进 training/backtest.

Schema (per Codex recommendation):
    raw_profit_forecast_snapshot_daily:
        snapshot_date (PIT key, 写盘当日)
        stock_code
        security_name
        forecast_inst_count       (研报数, 一致预期来源数)
        eps_forecast_this_year    (本年 EPS 一致预期)
        eps_forecast_next_year    (次年 EPS 一致预期)
        eps_forecast_two_years    (后年)
        profit_yoy_this_year      (本年利润同比)
        source                    ('akshare_em' | ...)
        source_label              ('stock_profit_forecast_em' | ...)
        as_of_date                ('latest' for now, akshare 没暴露; 留 schema 给将来 PIT-aware 源)
        fetched_at                (ingest 时间戳)
        raw_json                  (原始记录 JSON, 防字段漂移)

INSERT OR IGNORE on (snapshot_date, stock_code) — immutable snapshot, 不覆盖历史.

usage:
    PYTHONPATH=backend python backend/scripts/ingest_profit_forecast_snapshot.py
    PYTHONPATH=backend python backend/scripts/ingest_profit_forecast_snapshot.py --source akshare_em
    PYTHONPATH=backend python backend/scripts/ingest_profit_forecast_snapshot.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ingest_profit_forecast")


REPO = Path(__file__).resolve().parents[2]
SMART_DB = REPO / "data" / "smartmoney.duckdb"


DDL = """
CREATE TABLE IF NOT EXISTS raw_profit_forecast_snapshot_daily (
    snapshot_date            TEXT NOT NULL,
    stock_code               TEXT NOT NULL,
    security_name            TEXT,
    forecast_inst_count      INTEGER,
    eps_forecast_this_year   DOUBLE,
    eps_forecast_next_year   DOUBLE,
    eps_forecast_two_years   DOUBLE,
    profit_yoy_this_year     DOUBLE,
    source                   TEXT,
    source_label             TEXT,
    as_of_date               TEXT,
    fetched_at               TIMESTAMP,
    raw_json                 TEXT,
    PRIMARY KEY (snapshot_date, stock_code, source)
);
CREATE INDEX IF NOT EXISTS idx_pfs_code ON raw_profit_forecast_snapshot_daily(stock_code);
CREATE INDEX IF NOT EXISTS idx_pfs_date ON raw_profit_forecast_snapshot_daily(snapshot_date);
"""


def _normalize_stock_code(code: str | None) -> str | None:
    if not code:
        return None
    s = str(code).strip()
    if len(s) == 6 and s.isdigit():
        return s
    # akshare 返回 '600000.SH' 类
    if "." in s:
        s = s.split(".")[0]
    return s if len(s) == 6 and s.isdigit() else None


def fetch_akshare_em(symbol: str = "") -> list[dict]:
    """akshare 一致预期 EPS."""
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare not installed")
        return []
    try:
        df = ak.stock_profit_forecast_em(symbol=symbol)
        return df.to_dict("records")
    except Exception as e:
        log.warning(f"akshare stock_profit_forecast_em failed: {e}")
        return []


def parse_akshare_row(record: dict, snapshot_date: str, fetched_at: datetime) -> dict | None:
    """Parse one akshare row to our schema.

    2026-05 实测 akshare 返回 13 cols: 序号/代码/名称/研报数/机构投资评级/
    2025/2026/2027/2028 预测每股收益. 没暴露具体 "this year" / "next year" 字段名,
    我们按 snapshot_date 的年份动态映射:
      this_year = forecast for year (snapshot_year)
      next_year = forecast for year (snapshot_year + 1)
      two_years = forecast for year (snapshot_year + 2)
    """
    code = _normalize_stock_code(record.get("代码") or record.get("stock_code"))
    if not code:
        return None

    # Dynamic year mapping (snapshot_date YYYY-MM-DD)
    try:
        snap_year = int(snapshot_date[:4])
    except (ValueError, IndexError):
        snap_year = datetime.now().year  # rule-compliance: ok evidence=current-year-fallback

    eps_this = _safe_float(record.get(f"{snap_year}预测每股收益") or record.get("每股收益"))
    eps_next = _safe_float(record.get(f"{snap_year + 1}预测每股收益") or record.get("次年每股收益"))
    eps_two = _safe_float(record.get(f"{snap_year + 2}预测每股收益") or record.get("两年每股收益"))

    return {
        "snapshot_date": snapshot_date,
        "stock_code": code,
        "security_name": record.get("名称") or record.get("stock_name"),
        "forecast_inst_count": _safe_int(record.get("研报数") or record.get("forecast_inst_count")),
        "eps_forecast_this_year": eps_this,
        "eps_forecast_next_year": eps_next,
        "eps_forecast_two_years": eps_two,
        "profit_yoy_this_year": _safe_float(record.get("净利润同比") or record.get("profit_yoy")),
        "source": "akshare_em",
        "source_label": "stock_profit_forecast_em",
        "as_of_date": "latest",  # akshare 没暴露 PIT as_of, 留字段给未来 PIT-aware 源
        "fetched_at": fetched_at,
        "raw_json": json.dumps(record, ensure_ascii=False, default=str),
    }


def _safe_float(v):
    try:
        f = float(v)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    f = _safe_float(v)
    return int(f) if f is not None else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily PIT snapshot of profit forecast")
    parser.add_argument("--source", default="akshare_em", choices=["akshare_em"])
    parser.add_argument("--snapshot-date", default=None,
                        help="覆盖 default today (用于 backfill 类操作 — 慎用)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只 fetch + parse, 不写库")
    args = parser.parse_args()

    t0 = time.time()
    snapshot_date = args.snapshot_date or datetime.now().strftime("%Y-%m-%d")
    fetched_at = datetime.now(UTC)
    log.info(f"=== Ingest profit forecast snapshot ({args.source}, date={snapshot_date}) ===")

    # 1. Fetch raw
    if args.source == "akshare_em":
        records = fetch_akshare_em()
    else:
        log.error(f"Unknown source: {args.source}")
        return 1

    if not records:
        log.warning(f"No records fetched from {args.source}")
        return 1
    log.info(f"  raw records: {len(records):,}")

    # 2. Parse to schema
    rows = []
    for rec in records:
        parsed = parse_akshare_row(rec, snapshot_date, fetched_at)
        if parsed:
            rows.append(parsed)
    log.info(f"  parsed rows: {len(rows):,}")

    if args.dry_run:
        log.info("  --dry-run: 跳过写库. Sample:")
        for r in rows[:2]:
            for k, v in r.items():
                vs = str(v)[:80] if v is not None else "NULL"
                log.info(f"    {k}: {vs}")
        return 0

    # 3. INSERT OR IGNORE (immutable snapshot, 不覆盖历史)
    conn = duckdb.connect(str(SMART_DB))
    try:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        cols = list(rows[0].keys())
        placeholders = ",".join(["?"] * len(cols))
        col_list = ",".join(cols)
        # Check existing snapshot_date count for log
        n_existing = conn.execute(
            "SELECT COUNT(*) FROM raw_profit_forecast_snapshot_daily "
            "WHERE snapshot_date = ?", [snapshot_date]
        ).fetchone()[0]
        if n_existing > 0:
            log.warning(
                f"  snapshot_date={snapshot_date} already has {n_existing} rows. "
                f"INSERT OR IGNORE will skip dups (immutable snapshot)."
            )
        # No explicit transaction — each INSERT auto-commits.
        # PK conflicts skip (immutable snapshot); type errors logged + skipped (not aborting whole batch).
        inserted = 0
        skipped_pk = 0
        skipped_err = 0
        for r in rows:
            try:
                conn.execute(
                    f"INSERT INTO raw_profit_forecast_snapshot_daily ({col_list}) "
                    f"VALUES ({placeholders})",
                    [r[c] for c in cols],
                )
                inserted += 1
            except duckdb.ConstraintException:
                # PK conflict on (snapshot_date, stock_code, source) → immutable, skip
                # rule-compliance: ok evidence=immutable-snapshot-design
                skipped_pk += 1
            except Exception as ins_err:
                # Per-row error 不中断 batch
                # rule-compliance: ok evidence=ingest-best-effort-batch
                log.warning(f"  insert err for {r.get('stock_code')}: {ins_err}")
                skipped_err += 1

        # Audit
        r = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT stock_code) "
            "FROM raw_profit_forecast_snapshot_daily WHERE snapshot_date = ?",
            [snapshot_date]
        ).fetchone()
        log.info(f"  {snapshot_date} table state: {r[0]:,} rows / {r[1]:,} stocks (after this ingest)")
        log.info(f"  new inserted: {inserted}, skipped (PK dup): {skipped_pk}, errors: {skipped_err}")
    finally:
        conn.close()

    log.info(f"=== Done in {time.time()-t0:.1f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
