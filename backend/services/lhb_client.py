"""lhb_client.py — 龙虎榜日度明细同步

来源：akshare.stock_lhb_detail_em(start_date='YYYYMMDD', end_date='YYYYMMDD')
  21 字段：代码/名称/上榜日/解读/收盘价/涨跌幅/龙虎榜净买额/买入额/卖出额/成交额/
           市场总成交额/净买额占总成交比/成交额占总成交比/换手率/流通市值/上榜原因/
           上榜后 1/2/5/10 日收益

同一只股票同一天可能因多条原因同时上榜（如"日涨幅偏离值 7%"+"日换手率达到 20%"），
用 (trade_date, stock_code, rank_reason) 自然键去重。

表：
  raw_lhb_daily   龙虎榜日度明细（按自然键 upsert）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger("cm-api")

LHB_SOURCE = "akshare_stock_lhb_detail_em"


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def ensure_tables(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_lhb_daily (
            trade_date       TEXT NOT NULL,
            stock_code       TEXT NOT NULL,
            rank_reason      TEXT NOT NULL,
            stock_name       TEXT,
            interpretation   TEXT,
            close_price      REAL,
            change_pct       REAL,
            net_buy          REAL,
            buy_amount       REAL,
            sell_amount      REAL,
            turnover         REAL,
            market_turnover  REAL,
            net_buy_pct      REAL,
            turnover_pct     REAL,
            turnover_rate    REAL,
            float_cap        REAL,
            post_1d          REAL,
            post_2d          REAL,
            post_5d          REAL,
            post_10d         REAL,
            source           TEXT,
            ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, stock_code, rank_reason)
        );
        CREATE INDEX IF NOT EXISTS idx_rlhb_stock ON raw_lhb_daily(stock_code);
        CREATE INDEX IF NOT EXISTS idx_rlhb_trade ON raw_lhb_daily(trade_date);
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
    text = text.replace(",", "").replace(" ", "").replace("%", "")
    try:
        return float(text)
    except Exception:
        return None


def _normalize_date(value) -> Optional[str]:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        digits = digits[:8]
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(text) == 10 and "-" in text:
        return text
    return None


def _normalize_stock_code(value) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


# ─────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────

def _fetch_lhb(start_date: str, end_date: str):
    """龙虎榜 datacenter-web RPT_DAILYBILLBOARD_DETAILSNEW (替代 akshare.stock_lhb_detail_em).

    start_date / end_date: YYYYMMDD, 返回 DataFrame (列名兼容旧 akshare 版).
    """
    from services.eastmoney_skill import fetch_all_pages
    import pandas as _pd

    start_iso = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    end_iso = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
    rows = fetch_all_pages(
        report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
        page_size=5000,
        sort_columns="SECURITY_CODE,TRADE_DATE",
        sort_types="1,-1",
        columns=(
            "SECURITY_CODE,SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLAIN,CLOSE_PRICE,CHANGE_RATE,"
            "BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_DEAL_AMT,ACCUM_AMOUNT,"
            "DEAL_NET_RATIO,DEAL_AMOUNT_RATIO,TURNOVERRATE,FREE_MARKET_CAP,EXPLANATION,D1_CLOSE_ADJCHRATE,"
            "D2_CLOSE_ADJCHRATE,D5_CLOSE_ADJCHRATE,D10_CLOSE_ADJCHRATE,SECURITY_TYPE_CODE"
        ),
        filter_expr=f"(TRADE_DATE<='{end_iso}')(TRADE_DATE>='{start_iso}')",
    )
    if not rows:
        return _pd.DataFrame()
    df = _pd.DataFrame(rows)
    df.rename(columns={
        "SECURITY_CODE": "代码",
        "SECURITY_NAME_ABBR": "名称",
        "TRADE_DATE": "上榜日",
        "EXPLAIN": "解读",
        "CLOSE_PRICE": "收盘价",
        "CHANGE_RATE": "涨跌幅",
        "BILLBOARD_NET_AMT": "龙虎榜净买额",
        "BILLBOARD_BUY_AMT": "龙虎榜买入额",
        "BILLBOARD_SELL_AMT": "龙虎榜卖出额",
        "BILLBOARD_DEAL_AMT": "龙虎榜成交额",
        "ACCUM_AMOUNT": "市场总成交额",
        "DEAL_NET_RATIO": "净买额占总成交比",
        "DEAL_AMOUNT_RATIO": "成交额占总成交比",
        "TURNOVERRATE": "换手率",
        "FREE_MARKET_CAP": "流通市值",
        "EXPLANATION": "上榜原因",
        "D1_CLOSE_ADJCHRATE": "上榜后1日",
        "D2_CLOSE_ADJCHRATE": "上榜后2日",
        "D5_CLOSE_ADJCHRATE": "上榜后5日",
        "D10_CLOSE_ADJCHRATE": "上榜后10日",
    }, inplace=True)
    return df


async def fetch_lhb_range(start_date: str, end_date: str, retries: int = 3) -> pd.DataFrame:
    """区间拉取，start/end 格式 YYYYMMDD。"""
    loop = asyncio.get_running_loop()
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            df = await loop.run_in_executor(None, _fetch_lhb, start_date, end_date)
            return df if df is not None else pd.DataFrame()
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"[龙虎榜] {start_date}~{end_date} 拉取失败 重试 {attempt + 1}/{retries}: {exc}"
            )
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"lhb_source_failed:{start_date}:{end_date}:{last_error}")


# ─────────────────────────────────────────────────────────────────────
# Normalize + Upsert
# ─────────────────────────────────────────────────────────────────────

_COL_REQUIRED = (
    "代码", "名称", "上榜日", "上榜原因",
    "收盘价", "涨跌幅", "龙虎榜净买额", "龙虎榜买入额", "龙虎榜卖出额",
    "龙虎榜成交额", "市场总成交额", "净买额占总成交比", "成交额占总成交比",
    "换手率", "流通市值",
)


def _normalize_rows(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    missing = [c for c in _COL_REQUIRED if c not in df.columns]
    if missing:
        raise RuntimeError(f"lhb_columns_missing:{missing}")
    out: list[dict] = []
    for r in df.to_dict("records"):
        stock_code = _normalize_stock_code(r.get("代码"))
        trade_date = _normalize_date(r.get("上榜日"))
        rank_reason = str(r.get("上榜原因") or "").strip()
        if not stock_code or not trade_date or not rank_reason:
            continue
        out.append({
            "trade_date": trade_date,
            "stock_code": stock_code,
            "rank_reason": rank_reason,
            "stock_name": r.get("名称"),
            "interpretation": r.get("解读"),
            "close_price": _parse_float(r.get("收盘价")),
            "change_pct": _parse_float(r.get("涨跌幅")),
            "net_buy": _parse_float(r.get("龙虎榜净买额")),
            "buy_amount": _parse_float(r.get("龙虎榜买入额")),
            "sell_amount": _parse_float(r.get("龙虎榜卖出额")),
            "turnover": _parse_float(r.get("龙虎榜成交额")),
            "market_turnover": _parse_float(r.get("市场总成交额")),
            "net_buy_pct": _parse_float(r.get("净买额占总成交比")),
            "turnover_pct": _parse_float(r.get("成交额占总成交比")),
            "turnover_rate": _parse_float(r.get("换手率")),
            "float_cap": _parse_float(r.get("流通市值")),
            "post_1d": _parse_float(r.get("上榜后1日")),
            "post_2d": _parse_float(r.get("上榜后2日")),
            "post_5d": _parse_float(r.get("上榜后5日")),
            "post_10d": _parse_float(r.get("上榜后10日")),
        })
    return out


def _upsert_rows(conn, rows: list[dict]) -> int:
    """DuckDB 上 INSERT OR REPLACE; PK = (trade_date, stock_code, rank_reason)."""
    if not rows:
        return 0
    cols = [
        "trade_date", "stock_code", "rank_reason",
        "stock_name", "interpretation", "close_price", "change_pct",
        "net_buy", "buy_amount", "sell_amount", "turnover", "market_turnover",
        "net_buy_pct", "turnover_pct", "turnover_rate", "float_cap",
        "post_1d", "post_2d", "post_5d", "post_10d", "source",
    ]
    placeholders = ", ".join(["?"] * (len(cols) + 1))  # +1 for ingested_at
    sql = (
        f"INSERT OR REPLACE INTO raw_lhb_daily "
        f"({', '.join(cols)}, ingested_at) VALUES ({placeholders})"
    )
    now_iso = datetime.now().isoformat()
    enriched = [dict(r, source=LHB_SOURCE) for r in rows]
    payload = [tuple(r.get(c) for c in cols) + (now_iso,) for r in enriched]
    conn.executemany(sql, payload)
    conn.commit()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────

async def sync_lhb_range(
    conn: Any,
    start_date: str,
    end_date: str,
) -> dict:
    """start/end 格式 'YYYY-MM-DD'。一次请求覆盖整个区间（推荐按月拉）。"""
    ensure_tables(conn)
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")
    try:
        df = await fetch_lhb_range(s, e)
    except Exception as exc:
        logger.warning(f"[龙虎榜] {start_date}~{end_date} 拉取失败: {exc}")
        return {
            "source": LHB_SOURCE,
            "start_date": start_date,
            "end_date": end_date,
            "status": "source_unavailable",
            "error": str(exc),
            "written_rows": 0,
        }

    rows = _normalize_rows(df)
    written = _upsert_rows(conn, rows)
    logger.info(
        f"[龙虎榜] {start_date}~{end_date} 同步完成: raw={len(df)} 行 written={written}"
    )
    return {
        "source": LHB_SOURCE,
        "start_date": start_date,
        "end_date": end_date,
        "status": "ok" if written > 0 else "empty",
        "written_rows": written,
        "raw_rows": int(len(df)),
    }


def _iter_monthly_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """按月切分 (YYYY-MM-01, YYYY-MM-末日) 的 (start, end) 对。"""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    windows: list[tuple[str, str]] = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        win_start = max(cur, start)
        win_end = min(nxt - timedelta(days=1), end)
        windows.append((win_start.strftime("%Y-%m-%d"), win_end.strftime("%Y-%m-%d")))
        cur = nxt
    return windows


async def backfill_lhb_history(
    conn: Any,
    start_date: str,
    end_date: Optional[str] = None,
) -> dict:
    """按月切分后逐窗口拉取并 upsert。"""
    ensure_tables(conn)
    end_date = end_date or date.today().strftime("%Y-%m-%d")
    windows = _iter_monthly_windows(start_date, end_date)
    total = 0
    detail: list[dict] = []
    failed: list[tuple[str, str]] = []
    for s, e in windows:
        result = await sync_lhb_range(conn, s, e)
        detail.append(result)
        total += int(result.get("written_rows") or 0)
        if result.get("status") == "source_unavailable":
            failed.append((s, e))
    return {
        "status": "ok" if not failed else "partial",
        "start_date": start_date,
        "end_date": end_date,
        "windows": windows,
        "failed_windows": failed,
        "written_rows": total,
        "detail": detail,
    }
