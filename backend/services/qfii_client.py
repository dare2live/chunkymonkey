"""qfii_client.py — QFII 季度持股数据同步

北向陆股通个股明细自 2024-08-16 停更后，QFII 季报是仍在披露的外资持仓维度。
来源：akshare.stock_gdfx_holding_detail_em(date, indicator="QFII", symbol=<新进|增加|减少|不变>)
  - date: "YYYYMMDD" 季度末
  - symbol: 持股变动类型，需轮询 4 个值覆盖全部状态

一个 (报告期, 股票, 股东) 在一个季度里对应且仅对应一条记录，其中 change_type
列保留 4 种状态之一，天然形成自然主键。

表：
  raw_qfii_holding_quarterly   原始季度明细（按自然键 upsert，可幂等重跑）

口径：
  report_date   报告期（YYYY-MM-DD，对齐季度末）
  notice_date   公告日（AkShare 返回的真实披露日，用于防穿越）
  hold_shares   期末持股数量
  change_type   新进 / 增加 / 减少 / 不变
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger("cm-api")

QFII_SOURCE = "akshare_stock_gdfx_holding_detail_em"
QFII_SYMBOLS = ("新进", "增加", "减少", "不变")
QFII_QUARTER_ENDS = ("03-31", "06-30", "09-30", "12-31")
QFII_NOTICE_LAG_DAYS = 30  # 报告期末 +30 天后才大概率有足量披露


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def ensure_tables(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_qfii_holding_quarterly (
            report_date            TEXT NOT NULL,
            stock_code             TEXT NOT NULL,
            holder_name            TEXT NOT NULL,
            stock_name             TEXT,
            holder_type            TEXT,
            hold_shares            REAL,
            hold_shares_change     REAL,
            hold_shares_change_pct REAL,
            change_type            TEXT,
            hold_market_cap        REAL,
            holder_rank            INTEGER,
            notice_date            TEXT,
            source                 TEXT,
            ingested_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (report_date, stock_code, holder_name)
        );
        CREATE INDEX IF NOT EXISTS idx_rqhq_stock    ON raw_qfii_holding_quarterly(stock_code);
        CREATE INDEX IF NOT EXISTS idx_rqhq_notice   ON raw_qfii_holding_quarterly(notice_date);
        CREATE INDEX IF NOT EXISTS idx_rqhq_report   ON raw_qfii_holding_quarterly(report_date);
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _normalize_date(value) -> Optional[str]:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"} or text in {"--", "-"}:
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
    text = text.replace(",", "").replace("%", "").replace(" ", "")
    try:
        return float(text)
    except Exception:
        return None


def _parse_int(value) -> Optional[int]:
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


def _normalize_stock_code(value) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def _report_date_yyyymmdd(report_date: str) -> str:
    """'2025-12-31' → '20251231'"""
    return report_date.replace("-", "")


def enumerate_quarter_ends(start_date: str, end_date: str) -> list[str]:
    """返回 start_date ~ end_date 之间所有季度末 (YYYY-MM-DD 格式)。"""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    out = []
    for year in range(start.year, end.year + 1):
        for md in QFII_QUARTER_ENDS:
            d = date.fromisoformat(f"{year}-{md}")
            if start <= d <= end:
                out.append(d.strftime("%Y-%m-%d"))
    return out


def latest_plannable_report_date(today: Optional[date] = None) -> Optional[str]:
    """返回相对 today 最近可能已披露的季度末 (YYYY-MM-DD)。

    规则：今天距离季度末至少 QFII_NOTICE_LAG_DAYS 天才认为足量披露。
    """
    today = today or date.today()
    cutoff = today - timedelta(days=QFII_NOTICE_LAG_DAYS)
    latest = None
    for year in (cutoff.year, cutoff.year - 1):
        for md in QFII_QUARTER_ENDS:
            d = date.fromisoformat(f"{year}-{md}")
            if d <= cutoff:
                if latest is None or d > latest:
                    latest = d
    return latest.strftime("%Y-%m-%d") if latest else None


# ─────────────────────────────────────────────────────────────────────
# Fetch
# ─────────────────────────────────────────────────────────────────────

def _fetch_qfii_by_symbol(report_date_yyyymmdd: str, symbol: str):
    """QFII 季度持股 datacenter-web RPT_DMSK_HOLDERS (替代 akshare.stock_gdfx_holding_detail_em).

    report_date_yyyymmdd: 季度末日期 YYYYMMDD (如 20251231).
    symbol: 持股变动 {"新进", "增加", "不变", "减少"}.
    """
    from services.eastmoney_skill import fetch_all_pages
    import pandas as _pd

    end_iso = f"{report_date_yyyymmdd[:4]}-{report_date_yyyymmdd[4:6]}-{report_date_yyyymmdd[6:]}"
    rows = fetch_all_pages(
        report_name="RPT_DMSK_HOLDERS",
        page_size=50,
        sort_columns="NOTICE_DATE,SECURITY_CODE,RANK",
        sort_types="-1,1,1",
        filter_expr=(
            f'(HOLDER_NEWTYPE="QFII")'
            f'(HOLDNUM_CHANGE_NAME="{symbol}")'
            f"(END_DATE='{end_iso}')"
        ),
    )
    if not rows:
        return _pd.DataFrame()
    df = _pd.DataFrame(rows)
    df.rename(columns={
        "HOLDER_NAME": "股东名称",
        "HOLDER_NEWTYPE": "股东类型",
        "RANK": "股东排名",
        "SECURITY_CODE": "股票代码",
        "SECURITY_NAME_ABBR": "股票简称",
        "END_DATE": "报告期",
        "HOLD_NUM": "期末持股-数量",
        "HOLD_NUM_CHANGE": "期末持股-数量变化",
        "HOLD_RATIO_CHANGE": "期末持股-数量变化比例",
        "HOLDNUM_CHANGE_NAME": "期末持股-持股变动",
        "HOLDER_MARKET_CAP": "期末持股-流通市值",
        "NOTICE_DATE": "公告日",
    }, inplace=True)
    return df


async def fetch_qfii_quarter(report_date: str, retries: int = 3) -> pd.DataFrame:
    """拉取某季度末的 4 个变动类型，合并为一个 DataFrame。"""
    loop = asyncio.get_running_loop()
    yyyymmdd = _report_date_yyyymmdd(report_date)
    frames: list[pd.DataFrame] = []
    for symbol in QFII_SYMBOLS:
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                df = await loop.run_in_executor(None, _fetch_qfii_by_symbol, yyyymmdd, symbol)
                if df is None or df.empty:
                    break
                frames.append(df)
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"[QFII] 拉取失败 {report_date} / {symbol} 重试 {attempt + 1}/{retries}: {exc}"
                )
                await asyncio.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"qfii_source_failed:{report_date}:{symbol}:{last_error}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────
# Normalize + Upsert
# ─────────────────────────────────────────────────────────────────────

_COL_REQUIRED = (
    "股东名称", "股东类型", "股票代码", "股票简称", "报告期",
    "期末持股-数量", "期末持股-数量变化", "期末持股-数量变化比例",
    "期末持股-持股变动", "期末持股-流通市值", "公告日", "股东排名",
)


def _normalize_rows(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    missing = [c for c in _COL_REQUIRED if c not in df.columns]
    if missing:
        raise RuntimeError(f"qfii_columns_missing:{missing}")
    out: list[dict] = []
    for r in df.to_dict("records"):
        stock_code = _normalize_stock_code(r.get("股票代码"))
        holder_name = str(r.get("股东名称") or "").strip()
        report_date = _normalize_date(r.get("报告期"))
        if not stock_code or not holder_name or not report_date:
            continue
        out.append({
            "report_date": report_date,
            "stock_code": stock_code,
            "holder_name": holder_name,
            "stock_name": r.get("股票简称"),
            "holder_type": r.get("股东类型"),
            "hold_shares": _parse_float(r.get("期末持股-数量")),
            "hold_shares_change": _parse_float(r.get("期末持股-数量变化")),
            "hold_shares_change_pct": _parse_float(r.get("期末持股-数量变化比例")),
            "change_type": r.get("期末持股-持股变动"),
            "hold_market_cap": _parse_float(r.get("期末持股-流通市值")),
            "holder_rank": _parse_int(r.get("股东排名")),
            "notice_date": _normalize_date(r.get("公告日")),
        })
    return out


def _upsert_rows(conn: Any, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur = conn.executemany(
        """
        INSERT INTO raw_qfii_holding_quarterly (
            report_date, stock_code, holder_name, stock_name, holder_type,
            hold_shares, hold_shares_change, hold_shares_change_pct,
            change_type, hold_market_cap, holder_rank, notice_date, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date, stock_code, holder_name) DO UPDATE SET
            stock_name             = excluded.stock_name,
            holder_type            = excluded.holder_type,
            hold_shares            = excluded.hold_shares,
            hold_shares_change     = excluded.hold_shares_change,
            hold_shares_change_pct = excluded.hold_shares_change_pct,
            change_type            = excluded.change_type,
            hold_market_cap        = excluded.hold_market_cap,
            holder_rank            = excluded.holder_rank,
            notice_date            = excluded.notice_date,
            source                 = excluded.source,
            ingested_at            = CURRENT_TIMESTAMP
        """,
        [
            (
                r.get("report_date"), r.get("stock_code"), r.get("holder_name"),
                r.get("stock_name"), r.get("holder_type"), r.get("hold_shares"),
                r.get("hold_shares_change"), r.get("hold_shares_change_pct"),
                r.get("change_type"), r.get("hold_market_cap"), r.get("holder_rank"),
                r.get("notice_date"), QFII_SOURCE,
            )
            for r in rows
        ],
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(rows)


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────

async def sync_qfii_quarter(
    conn: Any,
    report_date: str,
) -> dict:
    """同步指定季度的 QFII 持股。report_date 形如 '2025-12-31'。"""
    ensure_tables(conn)
    try:
        df = await fetch_qfii_quarter(report_date)
    except Exception as exc:
        logger.warning(f"[QFII] 季度 {report_date} 拉取失败: {exc}")
        return {
            "source": QFII_SOURCE,
            "report_date": report_date,
            "status": "source_unavailable",
            "error": str(exc),
            "written_rows": 0,
        }

    rows = _normalize_rows(df)
    written = _upsert_rows(conn, rows)
    logger.info(
        f"[QFII] 季度 {report_date} 同步完成: raw={len(df)} 行, written={written} 条"
    )
    return {
        "source": QFII_SOURCE,
        "report_date": report_date,
        "status": "ok" if written > 0 else "empty",
        "written_rows": written,
        "raw_rows": int(len(df)),
    }


async def backfill_qfii_history(
    conn: Any,
    start_date: str,
    end_date: Optional[str] = None,
) -> dict:
    """回填一段历史，end_date 省略则取 latest_plannable_report_date()。"""
    ensure_tables(conn)
    end_date = end_date or latest_plannable_report_date()
    if not end_date:
        return {"source": QFII_SOURCE, "status": "no_plannable_quarter", "written_rows": 0, "quarters": []}

    quarters = enumerate_quarter_ends(start_date, end_date)
    detail: list[dict] = []
    total = 0
    for q in quarters:
        d = await sync_qfii_quarter(conn, q)
        detail.append(d)
        total += int(d.get("written_rows") or 0)
    return {
        "source": QFII_SOURCE,
        "status": "ok",
        "start_date": start_date,
        "end_date": end_date,
        "quarters": quarters,
        "written_rows": total,
        "detail": detail,
    }
