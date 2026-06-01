#!/usr/bin/env python3
"""Build mart_stock_fund_flow_rank_snapshot_daily from akshare research-side rank snapshot."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.calendar import latest_completed_trade_date
from services.data_sources import resolve
from services.db import ensure_schema, get_conn
from services.schema_versions import record_actual_version
from services.source_watermarks import (
    derive_watermark,
    record_source_failure,
    resolve_source_failures,
    upsert_watermark,
)


log = logging.getLogger("build_fund_flow_rank_snapshot_daily")

TABLE_NAME = "mart_stock_fund_flow_rank_snapshot_daily"
DATA_DOMAIN = "stock_fund_flow_rank_snapshot"
SOURCE_NAME = "akshare"
SOURCE_TIER = 3
CAPABILITY = "individual_fund_flow_rank_snapshot"
DEFAULT_SYMBOL = "即时"
PARSER_VERSION = "akshare_stock_fund_flow_individual_snapshot_v1"

WATERMARK_SPEC = {
    "data_domain": DATA_DOMAIN,
    "source_name": SOURCE_NAME,
    "source_tier": SOURCE_TIER,
    "table": TABLE_NAME,
    "date_col": "snapshot_date",
    "parser_version": PARSER_VERSION,
    "fallback_reason": "research-side rank snapshot only; exact need_027 flow remains blocked/unknown",
}

INSERT_SQL = f"""
INSERT OR REPLACE INTO {TABLE_NAME} (
    snapshot_date,
    snapshot_symbol,
    rank_seq,
    stock_code,
    stock_name,
    latest_price,
    change_pct,
    turnover_rate,
    inflow_amount,
    outflow_amount,
    net_amount,
    turnover_amount,
    source_used,
    source_capability,
    built_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN check
            return None
    except Exception:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None:
            raise ValueError
        if value != value:  # NaN check
            raise ValueError
    except Exception:
        return int(default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _extract_records(raw: Any) -> tuple[list[dict[str, Any]], str]:
    if raw is None:
        return [], "none"

    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict):
            return [dict(row) for row in raw], "records"
        return [{"value": item} for item in raw], "list"

    if isinstance(raw, dict):
        if "data" in raw and isinstance(raw["data"], list):
            data = raw["data"]
            if not data or isinstance(data[0], dict):
                return [dict(row) for row in data], "dict[data]"
        return [dict(raw)], "dict"

    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict()
        if isinstance(records, list) and (not records or isinstance(records[0], dict)):
            return [dict(row) for row in records], type(raw).__name__
        if isinstance(records, dict):
            return [dict(records)], type(raw).__name__

    return [{"value": str(raw)}], type(raw).__name__


def _normalize_code(value: Any) -> str:
    code = str(value or "").strip()
    if not code:
        return ""
    if "." in code:
        left, right = code.split(".", 1)
        if left.isdigit() and right.upper() in {"SH", "SZ", "BJ"}:
            code = left
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    return code


def _normalize_rows(
    records: list[dict[str, Any]],
    *,
    snapshot_date: str,
    snapshot_symbol: str,
    source_used: str,
    built_at: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for idx, row in enumerate(records, start=1):
        stock_code = _normalize_code(row.get("股票代码") or row.get("代码") or row.get("stock_code"))
        if not stock_code:
            skipped += 1
            continue

        inflow_amount = _to_float(row.get("流入资金") or row.get("inflow_amount"))
        outflow_amount = _to_float(row.get("流出资金") or row.get("outflow_amount"))
        net_amount = _to_float(row.get("净额") or row.get("net_amount"))
        if net_amount is None and inflow_amount is not None and outflow_amount is not None:
            net_amount = inflow_amount - outflow_amount

        rows.append(
            {
                "snapshot_date": snapshot_date,
                "snapshot_symbol": snapshot_symbol,
                "rank_seq": _to_int(row.get("序号") or row.get("rank_seq") or row.get("rank"), idx),
                "stock_code": stock_code,
                "stock_name": row.get("股票简称") or row.get("名称") or row.get("stock_name"),
                "latest_price": _to_float(row.get("最新价") or row.get("latest_price")),
                "change_pct": _to_float(row.get("涨跌幅") or row.get("change_pct")),
                "turnover_rate": _to_float(row.get("换手率") or row.get("turnover_rate")),
                "inflow_amount": inflow_amount,
                "outflow_amount": outflow_amount,
                "net_amount": net_amount,
                "turnover_amount": _to_float(row.get("成交额") or row.get("turnover_amount")),
                "source_used": source_used,
                "source_capability": CAPABILITY,
                "built_at": built_at,
            }
        )

    if not rows:
        raise RuntimeError(f"{CAPABILITY} returned no usable rows")

    frame = pd.DataFrame(rows)
    frame = frame.drop_duplicates(subset=["stock_code"], keep="first")
    frame = frame.sort_values(["rank_seq", "stock_code"], kind="mergesort").reset_index(drop=True)
    if skipped:
        log.warning("skipped %s rows without stock_code", skipped)
    return frame


def _write_frame(conn, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    payload = [tuple(row) for row in frame.itertuples(index=False, name=None)]
    conn.execute(
        """
        DELETE FROM mart_stock_fund_flow_rank_snapshot_daily
         WHERE snapshot_date = ?
           AND snapshot_symbol = ?
        """,
        [frame.iloc[0]["snapshot_date"], frame.iloc[0]["snapshot_symbol"]],
    )
    conn.executemany(INSERT_SQL, payload)


def build_fund_flow_rank_snapshot_daily(
    snapshot_date: str | None = None,
    *,
    snapshot_symbol: str = DEFAULT_SYMBOL,
    prefer_source: str = SOURCE_NAME,
    conn=None,
    dry_run: bool = False,
) -> dict[str, Any]:
    close_conn = False
    if conn is None:
        conn = get_conn()
        close_conn = True

    try:
        ensure_schema(conn)
        if snapshot_date is None:
            snapshot_date = latest_completed_trade_date(conn)
            if not snapshot_date:
                raise RuntimeError("latest_completed_trade_date returned None; refuse wall-clock fallback")

        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        raw, source_used = resolve(CAPABILITY, prefer_source=prefer_source, symbol=snapshot_symbol)
        if source_used != SOURCE_NAME:
            log.warning("expected source=%s for %s but resolver used %s", SOURCE_NAME, CAPABILITY, source_used)

        records, payload_kind = _extract_records(raw)
        frame = _normalize_rows(
            records,
            snapshot_date=snapshot_date,
            snapshot_symbol=snapshot_symbol,
            source_used=str(source_used or SOURCE_NAME),
            built_at=started_at,
        )

        summary: dict[str, Any] = {
            "dry_run": bool(dry_run),
            "snapshot_date": snapshot_date,
            "snapshot_symbol": snapshot_symbol,
            "source_used": str(source_used or SOURCE_NAME),
            "payload_kind": payload_kind,
            "row_count": int(len(frame)),
            "min_rank_seq": int(frame["rank_seq"].min()),
            "max_rank_seq": int(frame["rank_seq"].max()),
        }

        if dry_run:
            return summary

        conn.execute("BEGIN")
        try:
            _write_frame(conn, frame)
            resolve_source_failures(
                conn,
                data_domain=DATA_DOMAIN,
                source_name=SOURCE_NAME,
                commit=False,
            )
            upsert_watermark(conn, derive_watermark(conn, WATERMARK_SPEC))
            record_actual_version(conn, TABLE_NAME)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        summary["watermark"] = {
            "data_domain": DATA_DOMAIN,
            "source_name": SOURCE_NAME,
            "source_tier": SOURCE_TIER,
        }
        return summary
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            log.warning("rollback failed while handling %s: %s", CAPABILITY, exc, exc_info=True)
        try:
            record_source_failure(
                conn,
                data_domain=DATA_DOMAIN,
                source_name=SOURCE_NAME,
                source_tier=SOURCE_TIER,
                error_type=type(exc).__name__,
                last_error=str(exc),
                commit=True,
            )
        except Exception:
            log.warning("failed to persist source failure evidence for %s", CAPABILITY, exc_info=True)
        raise
    finally:
        if close_conn:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mart_stock_fund_flow_rank_snapshot_daily")
    parser.add_argument("--snapshot-date", default=None, help="YYYY-MM-DD; defaults to latest completed trade date")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="akshare symbol/snapshot label (default: 即时)")
    parser.add_argument("--prefer-source", default=SOURCE_NAME, help="Preferred source name for resolve()")
    parser.add_argument("--dry-run", action="store_true", help="Normalize and summarize without writing tables")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    t0 = time.time()
    result = build_fund_flow_rank_snapshot_daily(
        snapshot_date=args.snapshot_date,
        snapshot_symbol=args.symbol,
        prefer_source=args.prefer_source,
        dry_run=bool(args.dry_run),
    )
    elapsed = time.time() - t0
    log.info(
        "Done: dry_run=%s date=%s symbol=%s rows=%s rank_range=%s..%s source=%s payload=%s elapsed=%.1fs",
        result["dry_run"],
        result["snapshot_date"],
        result["snapshot_symbol"],
        f"{result['row_count']:,}",
        result["min_rank_seq"],
        result["max_rank_seq"],
        result["source_used"],
        result["payload_kind"],
        elapsed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
