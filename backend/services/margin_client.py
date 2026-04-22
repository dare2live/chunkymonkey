"""margin_client.py — 融资融券日度数据同步

沪市 + 深市两个独立接口，字段不完全对齐；本 client 映射为统一 schema。

来源：
- 上交所: akshare.stock_margin_detail_sse(date='YYYYMMDD')
  字段: 信用交易日期, 标的证券代码, 标的证券简称, 融资余额, 融资买入额, 融资偿还额,
        融券余量, 融券卖出量, 融券偿还量
- 深交所: akshare.stock_margin_detail_szse(date='YYYYMMDD')
  字段: 证券代码, 证券简称, 融资买入额, 融资余额, 融券卖出量, 融券余量, 融券余额, 融资融券余额

表：
  raw_margin_daily   日度融资融券明细（按 (trade_date, stock_code, market) upsert）
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger("cm-api")

MARGIN_SOURCE_SH = "akshare_stock_margin_detail_sse"
MARGIN_SOURCE_SZ = "akshare_stock_margin_detail_szse"


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_margin_daily (
            trade_date       TEXT NOT NULL,
            stock_code       TEXT NOT NULL,
            market           TEXT NOT NULL,
            stock_name       TEXT,
            rz_balance       REAL,
            rz_buy           REAL,
            rz_repay         REAL,
            rq_balance       REAL,
            rq_shares        REAL,
            rq_sell          REAL,
            rq_repay         REAL,
            rzrq_balance     REAL,
            source           TEXT,
            ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, stock_code, market)
        );
        CREATE INDEX IF NOT EXISTS idx_rmd_stock ON raw_margin_daily(stock_code);
        CREATE INDEX IF NOT EXISTS idx_rmd_trade ON raw_margin_daily(trade_date);
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _parse_float(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return None if value != value else float(value)
        except Exception:
            return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"} or text in {"--", "-"}:
        return None
    text = text.replace(",", "").replace(" ", "")
    try:
        return float(text)
    except Exception:
        return None


def _normalize_stock_code(value) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def _iso_date(yyyymmdd: str) -> str:
    s = str(yyyymmdd).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


# ─────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────

def _fetch_sh(date_yyyymmdd: str):
    import akshare as ak
    return ak.stock_margin_detail_sse(date=date_yyyymmdd)


def _fetch_sz(date_yyyymmdd: str):
    import akshare as ak
    return ak.stock_margin_detail_szse(date=date_yyyymmdd)


async def fetch_margin_day(date_yyyymmdd: str, retries: int = 3) -> dict:
    """一日拉 SH+SZ，返回 {'sh': df, 'sz': df}（单边失败不阻塞另一边）。"""
    loop = asyncio.get_running_loop()
    out: dict = {}
    for market, fn in (("sh", _fetch_sh), ("sz", _fetch_sz)):
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                df = await loop.run_in_executor(None, fn, date_yyyymmdd)
                out[market] = df
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"[两融] {market.upper()} {date_yyyymmdd} 拉取失败 重试 {attempt + 1}/{retries}: {exc}"
                )
                await asyncio.sleep(1.5 * (attempt + 1))
        else:
            out[market] = None
            logger.error(f"[两融] {market.upper()} {date_yyyymmdd} 最终失败: {last_error}")
    return out


# ─────────────────────────────────────────────────────────────────────
# Normalize
# ─────────────────────────────────────────────────────────────────────

def _normalize_sh(df: pd.DataFrame, trade_date: str) -> list[dict]:
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for r in df.to_dict("records"):
        stock_code = _normalize_stock_code(r.get("标的证券代码"))
        if not stock_code:
            continue
        out.append({
            "trade_date": trade_date,
            "stock_code": stock_code,
            "market": "SH",
            "stock_name": r.get("标的证券简称"),
            "rz_balance": _parse_float(r.get("融资余额")),
            "rz_buy": _parse_float(r.get("融资买入额")),
            "rz_repay": _parse_float(r.get("融资偿还额")),
            "rq_balance": None,
            "rq_shares": _parse_float(r.get("融券余量")),
            "rq_sell": _parse_float(r.get("融券卖出量")),
            "rq_repay": _parse_float(r.get("融券偿还量")),
            "rzrq_balance": None,
            "source": MARGIN_SOURCE_SH,
        })
    return out


def _normalize_sz(df: pd.DataFrame, trade_date: str) -> list[dict]:
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for r in df.to_dict("records"):
        stock_code = _normalize_stock_code(r.get("证券代码"))
        if not stock_code:
            continue
        rz_balance = _parse_float(r.get("融资余额"))
        rq_balance = _parse_float(r.get("融券余额"))
        rzrq_balance = _parse_float(r.get("融资融券余额"))
        if rzrq_balance is None and rz_balance is not None and rq_balance is not None:
            rzrq_balance = rz_balance + rq_balance
        out.append({
            "trade_date": trade_date,
            "stock_code": stock_code,
            "market": "SZ",
            "stock_name": r.get("证券简称"),
            "rz_balance": rz_balance,
            "rz_buy": _parse_float(r.get("融资买入额")),
            "rz_repay": None,
            "rq_balance": rq_balance,
            "rq_shares": _parse_float(r.get("融券余量")),
            "rq_sell": _parse_float(r.get("融券卖出量")),
            "rq_repay": None,
            "rzrq_balance": rzrq_balance,
            "source": MARGIN_SOURCE_SZ,
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# Upsert
# ─────────────────────────────────────────────────────────────────────

def _upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO raw_margin_daily (
            trade_date, stock_code, market, stock_name,
            rz_balance, rz_buy, rz_repay,
            rq_balance, rq_shares, rq_sell, rq_repay,
            rzrq_balance, source
        )
        VALUES (
            :trade_date, :stock_code, :market, :stock_name,
            :rz_balance, :rz_buy, :rz_repay,
            :rq_balance, :rq_shares, :rq_sell, :rq_repay,
            :rzrq_balance, :source
        )
        ON CONFLICT(trade_date, stock_code, market) DO UPDATE SET
            stock_name   = excluded.stock_name,
            rz_balance   = excluded.rz_balance,
            rz_buy       = excluded.rz_buy,
            rz_repay     = excluded.rz_repay,
            rq_balance   = excluded.rq_balance,
            rq_shares    = excluded.rq_shares,
            rq_sell      = excluded.rq_sell,
            rq_repay     = excluded.rq_repay,
            rzrq_balance = excluded.rzrq_balance,
            source       = excluded.source,
            ingested_at  = CURRENT_TIMESTAMP
        """,
        rows,
    )
    conn.commit()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────

async def sync_margin_day(
    conn: sqlite3.Connection,
    trade_date: str,
) -> dict:
    """trade_date 形如 '2026-04-21'。"""
    ensure_tables(conn)
    yyyymmdd = trade_date.replace("-", "")
    frames = await fetch_margin_day(yyyymmdd)
    sh_rows = _normalize_sh(frames.get("sh"), trade_date)
    sz_rows = _normalize_sz(frames.get("sz"), trade_date)
    total = _upsert_rows(conn, sh_rows + sz_rows)
    return {
        "trade_date": trade_date,
        "status": "ok" if total > 0 else "empty",
        "written_rows": total,
        "sh_rows": len(sh_rows),
        "sz_rows": len(sz_rows),
    }


def _trading_days_between(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT trade_date FROM dim_trading_calendar
        WHERE is_trading = 1
          AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
        """,
        (start_date, end_date),
    ).fetchall()
    return [r[0] for r in rows]


async def backfill_margin_history(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: Optional[str] = None,
    skip_existing: bool = True,
) -> dict:
    """沿 dim_trading_calendar 逐日回填。skip_existing=True 时跳过已有数据的日期。"""
    ensure_tables(conn)
    end_date = end_date or date.today().strftime("%Y-%m-%d")
    trading_days = _trading_days_between(conn, start_date, end_date)
    if not trading_days:
        return {"status": "no_trading_days", "written_rows": 0, "days": 0}

    existing_days: set[str] = set()
    if skip_existing:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM raw_margin_daily "
            "WHERE trade_date >= ? AND trade_date <= ?",
            (start_date, end_date),
        ).fetchall()
        existing_days = {r[0] for r in rows}

    total_written = 0
    days_run = 0
    days_skip = 0
    failed_days: list[str] = []

    for d in trading_days:
        if d in existing_days:
            days_skip += 1
            continue
        try:
            result = await sync_margin_day(conn, d)
            total_written += int(result.get("written_rows") or 0)
            days_run += 1
            if result.get("status") != "ok":
                logger.warning(f"[两融] {d} written=0")
        except Exception as exc:
            logger.error(f"[两融] {d} 回填失败: {exc}")
            failed_days.append(d)

    return {
        "status": "ok" if not failed_days else "partial",
        "start_date": start_date,
        "end_date": end_date,
        "days": len(trading_days),
        "days_run": days_run,
        "days_skipped": days_skip,
        "failed_days": failed_days,
        "written_rows": total_written,
    }
