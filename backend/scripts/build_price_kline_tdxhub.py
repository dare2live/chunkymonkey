#!/usr/bin/env python3
"""Phase 1 子任务 1: 用 tdxhub 重建 price_kline 为统一 qfq 基准, 回填历史到 2019-09+

原因:
  - 现有 price_kline (chatgpt_import 源) 只到 2023-01-03
  - Risk 1 OOS (2021-2022) 被数据阻断
  - tdxhub bars + adjust='qfq' 能回到 2019-09, 同花顺复权基准 (和现有 chatgpt 源
    绝对值差 ~1.19x 但收益率一致, 必须整体切换避免跨基准跳变)

策略:
  - 并发按股 × 2 页 (start=0 + start=800) 拉 A 股 (约 5 200 只)
  - 写入新表 price_kline_tdxhub, 跑通后 swap 为主表
  - 保留旧表 rename 为 price_kline_legacy 以供对照
"""
from __future__ import annotations

import argparse
import os
import hashlib
import logging
import sys
import time
import warnings
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings('ignore')

from services.market_db import get_market_conn
from services.db import get_conn as get_business_conn
from services.tdx_source import call_tdx_quotes_with_retry
from services.utils import latest_completed_trade_date

logger = logging.getLogger("price_kline_tdxhub")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

LOCAL_ACTIVE_A_STOCK_MIN_ROWS = 3000
DEFAULT_WRITE_BATCH_ROWS = 5000
DEFAULT_RAW_INCREMENTAL_WORKERS = 8
DEFAULT_QFQ_WORKERS = 4


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS price_kline_tdxhub (
    code          TEXT NOT NULL,
    date          TEXT NOT NULL,
    freq          TEXT NOT NULL DEFAULT 'daily',
    adjust        TEXT NOT NULL DEFAULT 'qfq',
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        REAL,
    amount        REAL,
    factor        REAL,
    source        TEXT DEFAULT 'tdxhub',
    batch_id      TEXT,
    ingested_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date, freq, adjust)
);
CREATE INDEX IF NOT EXISTS idx_pkt_code ON price_kline_tdxhub(code);
CREATE INDEX IF NOT EXISTS idx_pkt_date ON price_kline_tdxhub(date);

CREATE TABLE IF NOT EXISTS price_kline_tdxhub_adjustment_event (
    code          TEXT NOT NULL,
    event_date    TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    adjust_factor REAL NOT NULL,
    prev_close    REAL,
    source        TEXT,
    batch_id      TEXT,
    applied_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, event_date, event_hash)
);
CREATE INDEX IF NOT EXISTS idx_pkt_adj_code_date
    ON price_kline_tdxhub_adjustment_event(code, event_date);
"""


def _is_a_share(code: str, market: int) -> bool:
    c = str(code).zfill(6)
    if market == 1:  # sh
        return c.startswith('60') or c.startswith('68')
    if market == 0:  # sz
        return c.startswith('00') or c.startswith('30')
    return False


def _tdx_market_from_code(code: str) -> int | None:
    c = str(code or "").strip().zfill(6)
    if c.startswith(("60", "68")):
        return 1
    if c.startswith(("00", "30")):
        return 0
    return None


def _row_get(row, key: str, index: int):
    try:
        return row[key]
    except Exception:
        return row[index]


def load_a_stock_list(client) -> list[tuple[str, int]]:
    sh = client.stocks_records(market=1)
    sz = client.stocks_records(market=0)
    codes = []
    for row in sh:
        if _is_a_share(row['code'], 1):
            codes.append((str(row['code']).zfill(6), 1))
    for row in sz:
        if _is_a_share(row['code'], 0):
            codes.append((str(row['code']).zfill(6), 0))
    logger.info("A 股代码总计 %d (沪 %d, 深 %d)",
                len(codes),
                sum(1 for _, m in codes if m == 1),
                sum(1 for _, m in codes if m == 0))
    return codes


def open_quotes_client_with_retry(
    *,
    max_attempts: int | None = None,
    connect_timeout: float | None = None,
):
    (stock_list, client), source = call_tdx_quotes_with_retry(
        lambda client: (load_a_stock_list(client), client),
        action_name="price_kline_tdxhub.stock_list",
        max_attempts=max_attempts,
        connect_timeout=connect_timeout,
    )
    logger.info("A 股列表来源: %s", source)
    return stock_list, client, source


def load_a_stock_list_with_retry() -> list[tuple[str, int]]:
    stock_list, _client, _source = open_quotes_client_with_retry()
    return stock_list


def load_local_active_a_stock_list(
    *,
    min_rows: int = LOCAL_ACTIVE_A_STOCK_MIN_ROWS,
) -> tuple[list[tuple[str, int]], str]:
    """Return locally cached active A-share codes without touching network."""

    try:
        biz_conn = get_business_conn()
    except Exception as exc:
        return [], f"dim_active_a_stock_unavailable:{type(exc).__name__}"
    try:
        exists = biz_conn.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_name = 'dim_active_a_stock'
             LIMIT 1
            """
        ).fetchone()
        if not exists:
            return [], "dim_active_a_stock_missing"
        rows = biz_conn.execute(
            """
            SELECT stock_code, market
              FROM dim_active_a_stock
             WHERE stock_code IS NOT NULL
             ORDER BY stock_code
            """
        ).fetchall()
    except Exception as exc:
        return [], f"dim_active_a_stock_read_error:{type(exc).__name__}"
    finally:
        biz_conn.close()

    codes: list[tuple[str, int]] = []
    for row in rows:
        code = str(_row_get(row, "stock_code", 0) or "").strip().zfill(6)
        market = _tdx_market_from_code(code)
        if market is None or not _is_a_share(code, market):
            continue
        codes.append((code, market))
    if len(codes) < min_rows:
        return [], f"dim_active_a_stock_insufficient:{len(codes)}"
    logger.info(
        "本地 A 股主数据 %d 只 (沪 %d, 深 %d)",
        len(codes),
        sum(1 for _, m in codes if m == 1),
        sum(1 for _, m in codes if m == 0),
    )
    return codes, "dim_active_a_stock"


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scale_price(value, factor: float) -> float | None:
    number = _safe_float(value)
    return None if number is None else float(number) * float(factor)


def _date_text(row: dict) -> str:
    value = row.get("datetime") or row.get("date")
    return str(value or "")[:10]


def pull_one_stock(
    client,
    code: str,
    pages: int = 2,
    *,
    adjust: str | None = "qfq",
    raise_errors: bool = False,
) -> list[dict]:
    """拉 `pages` 页 bars, 每页 800 根. 返回聚合后的 records."""
    parts = []
    for start in range(0, pages * 800, 800):
        try:
            kwargs = {"symbol": code, "frequency": 9, "start": start, "offset": 800}
            if adjust:
                kwargs["adjust"] = adjust
            records = client.bars_records(**kwargs)
        except Exception as e:
            if raise_errors:
                raise
            logger.warning("code=%s start=%d ERR: %s", code, start, e)
            continue
        if not records:
            break
        parts.extend(dict(row) for row in records)
        if len(records) < 800:
            break
    if not parts:
        if raise_errors:
            raise ValueError("empty")
        return []
    for row in parts:
        row["code"] = code
    return parts


def pull_one_stock_with_retry(
    code: str,
    pages: int = 2,
    *,
    adjust: str | None = "qfq",
    max_attempts: int | None = None,
    connect_timeout: float | None = None,
    prefer_last_success: bool = True,
) -> tuple[list[dict], str]:
    records, source = call_tdx_quotes_with_retry(
        lambda client: pull_one_stock(client, code, pages=pages, adjust=adjust, raise_errors=True),
        action_name=f"price_kline_tdxhub.bars[{code}]",
        max_attempts=max_attempts,
        connect_timeout=connect_timeout,
        prefer_last_success=prefer_last_success,
    )
    return records, source


def normalize(rows: list[dict], batch_id: str, source_name: str = "tdxhub") -> list[dict]:
    if not rows:
        return []
    out = []
    seen = set()
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        day = _date_text(row)
        if not code or not day:
            continue
        key = (code, day)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "code": code,
            "date": day,
            "freq": "daily",
            "adjust": "qfq",
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("vol", row.get("volume"))),
            "amount": _safe_float(row.get("amount")),
            "factor": _safe_float(row.get("factor")) if row.get("factor") is not None else 1.0,
            "source": source_name or "tdxhub",
            "batch_id": batch_id,
        })
    return out


def write_batch(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_price_kline_tdxhub_write (
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
    conn.execute("DELETE FROM tmp_price_kline_tdxhub_write")
    conn.executemany(
        """
        INSERT INTO tmp_price_kline_tdxhub_write
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["code"], row["date"], row["freq"], row["adjust"],
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row["amount"], row["factor"], row["source"],
                row["batch_id"],
            )
            for row in rows
        ],
    )
    conn.execute(
        """
        DELETE FROM price_kline_tdxhub AS target
              USING tmp_price_kline_tdxhub_write AS incoming
              WHERE target.code = incoming.code
                AND target.date = incoming.date
                AND target.freq = incoming.freq
                AND target.adjust = incoming.adjust
        """
    )
    conn.execute(
        """
        INSERT INTO price_kline_tdxhub (
            code, date, freq, adjust, open, high, low, close,
            volume, amount, factor, source, batch_id
        )
        SELECT code, date, freq, adjust, open, high, low, close,
               volume, amount, factor, source, batch_id
          FROM tmp_price_kline_tdxhub_write
        """
    )
    return len(rows)


def load_latest_dates(conn) -> dict[str, str]:
    """Return each stock's latest stored qfq daily date for incremental fills."""

    rows = conn.execute(
        """
        SELECT code, MAX(date) AS latest_date
          FROM price_kline_tdxhub
         WHERE freq = 'daily' AND adjust = 'qfq'
         GROUP BY code
        """
    ).fetchall()
    return {str(row[0]).zfill(6): str(row[1]) for row in rows if row[1]}


def load_fallback_latest_date(conn) -> str | None:
    """Return fallback daily qfq max date, used as incremental catch-up target."""

    try:
        row = conn.execute(
            """
            SELECT MAX(date)
              FROM price_kline
             WHERE freq = 'daily' AND adjust = 'qfq'
            """
        ).fetchone()
    except Exception:
        return None
    return str(row[0]) if row and row[0] else None


def load_calendar_target_date() -> str | None:
    """Return the latest completed A-share trading day from dim_trading_calendar."""

    try:
        biz_conn = get_business_conn()
    except Exception as exc:
        logger.warning("交易日历连接失败，将使用 fallback K 线日期兜底: %s", exc)
        return None
    try:
        return latest_completed_trade_date(biz_conn)
    except Exception as exc:
        logger.warning("交易日历读取失败，将使用 fallback K 线日期兜底: %s", exc)
        return None
    finally:
        biz_conn.close()


def choose_incremental_target_date(conn, explicit_target_date: str | None = None) -> tuple[str | None, str]:
    """Choose the incremental catch-up target date.

    CLI target wins for manual backfills. Otherwise the trading calendar is the source
    of truth, with fallback price_kline used only when the calendar is unavailable.
    """

    if explicit_target_date:
        return explicit_target_date, "cli"

    calendar_date = load_calendar_target_date()
    fallback_date = load_fallback_latest_date(conn)
    if calendar_date:
        if fallback_date and fallback_date != calendar_date:
            logger.info(
                "交易日历目标日期 %s，fallback price_kline 最新日期 %s 仅作校验",
                calendar_date,
                fallback_date,
            )
        return calendar_date, "dim_trading_calendar"
    if fallback_date:
        return fallback_date, "fallback_price_kline"
    return None, "none"


def filter_stale_stock_list(
    stock_list: list[tuple[str, int]],
    latest_dates: dict[str, str],
    target_date: str | None,
) -> list[tuple[str, int]]:
    if not target_date:
        return stock_list
    return [
        (code, market)
        for code, market in stock_list
        if latest_dates.get(code, "") < target_date
    ]


def load_xdxr_gap_codes(
    conn,
    latest_dates: dict[str, str],
    target_date: str | None,
) -> set[str]:
    """Return codes with xdxr events inside the raw incremental gap.

    Raw TDX bars are not qfq-adjusted. If an xdxr event falls between the
    stored qfq date and the catch-up target, raw bars would pollute the qfq
    primary table. Those rows must be left to fallback or a future adjusted
    reconstruction path.
    """

    if not latest_dates or not target_date:
        return set()
    rows = [(code, latest) for code, latest in latest_dates.items() if latest < target_date]
    if not rows:
        return set()
    try:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_tdxhub_latest_dates(code TEXT, latest_date TEXT)")
        conn.execute("DELETE FROM tmp_tdxhub_latest_dates")
        conn.executemany("INSERT INTO tmp_tdxhub_latest_dates VALUES (?, ?)", rows)
        out = conn.execute(
            """
            SELECT DISTINCT x.code
              FROM price_xdxr x
              JOIN tmp_tdxhub_latest_dates l ON l.code = x.code
             WHERE x.date > l.latest_date
               AND x.date <= ?
            """,
            (target_date,),
        ).fetchall()
    except Exception:
        return set()
    return {str(row[0]).zfill(6) for row in out if row[0]}


def load_xdxr_gap_events(
    conn,
    latest_dates: dict[str, str],
    target_date: str | None,
) -> dict[str, list[dict]]:
    """Return price-adjusting xdxr events inside each stock's incremental gap."""

    if not latest_dates or not target_date:
        return {}
    rows = [(code, latest) for code, latest in latest_dates.items() if latest < target_date]
    if not rows:
        return {}
    try:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_tdxhub_latest_dates(code TEXT, latest_date TEXT)")
        conn.execute("DELETE FROM tmp_tdxhub_latest_dates")
        conn.executemany("INSERT INTO tmp_tdxhub_latest_dates VALUES (?, ?)", rows)
        out = conn.execute(
            """
            SELECT x.code,
                   x.date,
                   x.category,
                   x.name,
                   x.fenhong,
                   x.peigujia,
                   x.songzhuangu,
                   x.peigu
              FROM price_xdxr x
              JOIN tmp_tdxhub_latest_dates l ON l.code = x.code
             WHERE x.category = 1
               AND x.date > l.latest_date
               AND x.date <= ?
             ORDER BY x.code, x.date
            """,
            (target_date,),
        ).fetchall()
    except Exception:
        return {}

    events: dict[str, list[dict]] = {}
    for row in out:
        code = str(row[0]).zfill(6)
        events.setdefault(code, []).append({
            "code": code,
            "date": str(row[1])[:10],
            "category": int(row[2] or 0),
            "name": row[3],
            "fenhong": _safe_float(row[4]) or 0.0,
            "peigujia": _safe_float(row[5]) or 0.0,
            "songzhuangu": _safe_float(row[6]) or 0.0,
            "peigu": _safe_float(row[7]) or 0.0,
        })
    return events


def xdxr_event_hash(event: dict) -> str:
    payload = "|".join(
        str(event.get(key) or "")
        for key in ("code", "date", "category", "fenhong", "peigujia", "songzhuangu", "peigu")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_xdxr_adjustment_factor(prev_close: float | None, event: dict) -> float | None:
    """Compute the qfq multiplier for rows before a TDX xdxr event date.

    TDX fields are per 10 shares. For a cash dividend, bonus issue, and rights
    issue, the pre-event price is mapped to the post-event qfq scale by:
    (P - cash/10 + rights_price * rights/10) / (P * (1 + bonus/10 + rights/10)).
    """

    prev = _safe_float(prev_close)
    if prev is None or prev <= 0:
        return None
    cash = (_safe_float(event.get("fenhong")) or 0.0) / 10.0
    bonus = (_safe_float(event.get("songzhuangu")) or 0.0) / 10.0
    rights = (_safe_float(event.get("peigu")) or 0.0) / 10.0
    rights_price = _safe_float(event.get("peigujia")) or 0.0
    if cash == 0 and bonus == 0 and rights == 0:
        return 1.0
    numerator = prev - cash + rights_price * rights
    denominator = prev * (1.0 + bonus + rights)
    if numerator <= 0 or denominator <= 0:
        return None
    return numerator / denominator


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _recent_factor_window_start(event_date: str, days: int = 14) -> str:
    try:
        day = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return "1900-01-01"
    return (day - timedelta(days=days)).strftime("%Y-%m-%d")


def calibrate_xdxr_factor_from_fallback(
    conn,
    code: str,
    event_date: str,
    raw_rows: list[dict],
    formula_factor: float | None,
    *,
    tolerance: float = 0.001,
) -> tuple[float | None, str]:
    """Optionally calibrate an xdxr factor against fallback qfq rows.

    TDX xdxr fields are the primary adjustment facts, but provider field
    semantics can vary on cash/bonus mixtures. When fallback qfq exists for the
    same raw incremental dates, use it as an explicit calibration source only
    if it materially disagrees with the formula factor.
    """

    if formula_factor is None or not raw_rows:
        return formula_factor, "formula"
    raw_by_date = {
        row["date"]: _safe_float(row.get("close"))
        for row in raw_rows
        if row.get("date") and row["date"] < event_date and _safe_float(row.get("close"))
    }
    if not raw_by_date:
        return formula_factor, "formula"
    window_start = _recent_factor_window_start(event_date)
    dates = [day for day in sorted(raw_by_date) if window_start <= day < event_date]
    if not dates:
        return formula_factor, "formula"
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_xdxr_factor_dates(date TEXT)")
    conn.execute("DELETE FROM tmp_xdxr_factor_dates")
    conn.executemany("INSERT INTO tmp_xdxr_factor_dates VALUES (?)", [(day,) for day in dates])
    try:
        fallback_rows = conn.execute(
            """
            SELECT f.date, f.close
              FROM price_kline f
              JOIN tmp_xdxr_factor_dates d ON d.date = f.date
             WHERE f.code = ? AND f.freq = 'daily' AND f.adjust = 'qfq'
            """,
            (code,),
        ).fetchall()
    except Exception:
        return formula_factor, "formula"
    ratios = []
    for row in fallback_rows:
        raw_close = raw_by_date.get(str(row[0])[:10])
        fallback_close = _safe_float(row[1])
        if raw_close and raw_close > 0 and fallback_close and fallback_close > 0:
            ratios.append(fallback_close / raw_close)
    calibrated = _median(ratios)
    if calibrated is None or calibrated <= 0:
        return formula_factor, "formula"
    if abs(calibrated / formula_factor - 1.0) <= tolerance:
        return formula_factor, "formula"
    return calibrated, "fallback_calibrated"


def infer_applied_xdxr_factor_from_fallback(
    conn,
    code: str,
    event_date: str,
    current_factor: float,
) -> float | None:
    window_start = _recent_factor_window_start(event_date)
    rows = conn.execute(
        """
        SELECT t.date, t.close, f.close
          FROM price_kline_tdxhub t
          JOIN price_kline f
            ON f.code = t.code
           AND f.date = t.date
           AND f.freq = t.freq
           AND f.adjust = t.adjust
         WHERE t.code = ?
           AND t.freq = 'daily'
           AND t.adjust = 'qfq'
           AND t.date >= ?
           AND t.date < ?
        """,
        (code, window_start, event_date),
    ).fetchall()
    ratios = []
    for row in rows:
        adjusted_close = _safe_float(row[1])
        fallback_close = _safe_float(row[2])
        if adjusted_close and fallback_close and current_factor > 0:
            raw_close = adjusted_close / current_factor
            if raw_close > 0:
                ratios.append(fallback_close / raw_close)
    return _median(ratios)


def _load_previous_tdxhub_close(conn, code: str, event_date: str) -> float | None:
    row = conn.execute(
        """
        SELECT close
          FROM price_kline_tdxhub
         WHERE code = ? AND freq = 'daily' AND adjust = 'qfq' AND date < ?
         ORDER BY date DESC
         LIMIT 1
        """,
        (code, event_date),
    ).fetchone()
    return _safe_float(row[0]) if row else None


def _load_applied_adjustment_factor(conn, code: str, event_date: str, event_hash: str) -> float | None:
    try:
        row = conn.execute(
            """
            SELECT adjust_factor
              FROM price_kline_tdxhub_adjustment_event
             WHERE code = ? AND event_date = ? AND event_hash = ?
             LIMIT 1
            """,
            (code, event_date, event_hash),
        ).fetchone()
    except Exception:
        return None
    return _safe_float(row[0]) if row else None


def apply_xdxr_adjustment_events(
    conn,
    code: str,
    events: list[dict],
    *,
    source_name: str,
    batch_id: str,
    raw_rows: list[dict] | None = None,
) -> list[dict]:
    """Apply unapplied xdxr factors to existing tdxhub qfq rows and return factors.

    The adjustment event table makes the operation idempotent across reruns.
    """

    applied_events = []
    for event in sorted(events or [], key=lambda item: item.get("date") or ""):
        event_date = str(event.get("date") or "")[:10]
        if not event_date:
            continue
        event_hash = xdxr_event_hash(event)
        existing_factor = _load_applied_adjustment_factor(conn, code, event_date, event_hash)
        if existing_factor is not None:
            applied_events.append({**event, "event_hash": event_hash, "adjust_factor": existing_factor})
            continue

        prev_close = _load_previous_tdxhub_close(conn, code, event_date)
        factor = compute_xdxr_adjustment_factor(prev_close, event)
        factor, factor_source = calibrate_xdxr_factor_from_fallback(
            conn,
            code,
            event_date,
            raw_rows or [],
            factor,
        )
        if factor is None:
            logger.warning("code=%s xdxr=%s 无法计算复权因子 prev_close=%s", code, event_date, prev_close)
            continue
        if factor != 1.0:
            conn.execute(
                """
                UPDATE price_kline_tdxhub
                   SET open = CASE WHEN open IS NULL THEN NULL ELSE open * ? END,
                       high = CASE WHEN high IS NULL THEN NULL ELSE high * ? END,
                       low = CASE WHEN low IS NULL THEN NULL ELSE low * ? END,
                       close = CASE WHEN close IS NULL THEN NULL ELSE close * ? END,
                       factor = COALESCE(factor, 1.0) * ?
                 WHERE code = ?
                   AND freq = 'daily'
                   AND adjust = 'qfq'
                   AND date < ?
                """,
                (factor, factor, factor, factor, factor, code, event_date),
            )
        conn.execute(
            """
            INSERT INTO price_kline_tdxhub_adjustment_event (
                code, event_date, event_hash, adjust_factor, prev_close, source, batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                event_date,
                event_hash,
                factor,
                prev_close,
                source_name if factor_source == "formula" else f"{source_name}_{factor_source}",
                batch_id,
            ),
        )
        applied_events.append({**event, "event_hash": event_hash, "adjust_factor": factor})
    return applied_events


def recalibrate_existing_xdxr_adjustments_from_fallback(
    conn,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    tolerance: float = 0.001,
) -> dict:
    """Recalibrate already-applied factors when fallback evidence disagrees."""

    where = []
    params = []
    if start_date:
        where.append("event_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("event_date <= ?")
        params.append(end_date)
    sql = (
        "SELECT code, event_date, event_hash, adjust_factor "
        "FROM price_kline_tdxhub_adjustment_event"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " ORDER BY code, event_date"
    )
    rows = conn.execute(sql, params).fetchall()
    changed = 0
    for row in rows:
        code = str(row[0]).zfill(6)
        event_date = str(row[1])[:10]
        event_hash = row[2]
        current_factor = _safe_float(row[3])
        if current_factor is None or current_factor <= 0:
            continue
        calibrated = infer_applied_xdxr_factor_from_fallback(conn, code, event_date, current_factor)
        if calibrated is None or calibrated <= 0:
            continue
        if abs(calibrated / current_factor - 1.0) <= tolerance:
            continue
        correction = calibrated / current_factor
        conn.execute(
            """
            UPDATE price_kline_tdxhub
               SET open = CASE WHEN open IS NULL THEN NULL ELSE open * ? END,
                   high = CASE WHEN high IS NULL THEN NULL ELSE high * ? END,
                   low = CASE WHEN low IS NULL THEN NULL ELSE low * ? END,
                   close = CASE WHEN close IS NULL THEN NULL ELSE close * ? END,
                   factor = COALESCE(factor, 1.0) * ?
             WHERE code = ?
               AND freq = 'daily'
               AND adjust = 'qfq'
               AND date < ?
            """,
            (correction, correction, correction, correction, correction, code, event_date),
        )
        conn.execute(
            """
            UPDATE price_kline_tdxhub_adjustment_event
               SET adjust_factor = ?,
                   source = CASE
                       WHEN source LIKE '%fallback_calibrated%' THEN source
                       ELSE source || '_fallback_calibrated'
                   END
             WHERE code = ? AND event_date = ? AND event_hash = ?
            """,
            (calibrated, code, event_date, event_hash),
        )
        changed += 1
    return {"checked": len(rows), "changed": changed}


def adjust_rows_for_xdxr_events(rows: list[dict], events: list[dict]) -> tuple[list[dict], int]:
    """Apply future xdxr event factors to raw incremental rows before event dates."""

    if not rows or not events:
        return rows, 0
    adjusted = []
    n_adjusted = 0
    for row in rows:
        factor = 1.0
        row_date = row["date"]
        for event in events:
            event_date = str(event.get("date") or "")[:10]
            if event_date and row_date < event_date:
                factor *= float(event.get("adjust_factor") or 1.0)
        if factor == 1.0:
            adjusted.append(row)
            continue
        updated = dict(row)
        for field in ("open", "high", "low", "close"):
            updated[field] = _scale_price(updated.get(field), factor)
        updated["factor"] = (_safe_float(updated.get("factor")) or 1.0) * factor
        if "xdxr_adjusted" not in str(updated.get("source") or ""):
            updated["source"] = f"{updated.get('source')}_xdxr_adjusted"
        adjusted.append(updated)
        n_adjusted += 1
    return adjusted, n_adjusted


def filter_raw_incremental_qfq_safe(rows: list[dict], xdxr_gap_codes: set[str]) -> tuple[list[dict], int]:
    """Drop raw incremental rows that cannot be treated as qfq safely."""

    if not rows or not xdxr_gap_codes:
        return rows, 0
    kept = [row for row in rows if row["code"] not in xdxr_gap_codes]
    return kept, len(rows) - len(kept)


def filter_after_latest(rows: list[dict], latest_dates: dict[str, str]) -> list[dict]:
    """Keep only rows newer than the per-code stored max date."""

    out = []
    for row in rows:
        latest = latest_dates.get(row["code"])
        if latest and row["date"] <= latest:
            continue
        out.append(row)
    return out


def fetch_one_stock_normalized(
    code: str,
    *,
    pages: int,
    adjust: str | None,
    batch_id: str,
    connect_timeout: float,
    max_attempts: int,
    prefer_last_success: bool = False,
) -> tuple[list[dict], str]:
    """Fetch one stock through the shared TDX server pool and normalize rows."""

    records, source_name = pull_one_stock_with_retry(
        code,
        pages=pages,
        adjust=adjust,
        max_attempts=max_attempts,
        connect_timeout=connect_timeout,
        prefer_last_success=prefer_last_success,
    )
    if adjust is None:
        source_name = f"{source_name}_raw_incremental"
    return normalize(records, batch_id, source_name=source_name), source_name


def resolve_kline_worker_count(
    *,
    explicit_workers: int | None,
    env_workers: int,
    pull_adjust: str | None,
) -> int:
    if explicit_workers is not None:
        return max(1, int(explicit_workers or 1))
    if env_workers > 0:
        return int(env_workers)
    return DEFAULT_RAW_INCREMENTAL_WORKERS if pull_adjust is None else DEFAULT_QFQ_WORKERS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pages', type=int, default=2,
                        help='每股拉 N 页（每页 800 根），默认 2 覆盖约 6 年历史')
    parser.add_argument('--limit', type=int, default=0,
                        help='只跑前 N 只股（调试用）, 0=全量')
    parser.add_argument('--skip-existing', action='store_true',
                        help='按每股 MAX(date) 只写新增日期（增量补缺口），不再整只股票跳过')
    parser.add_argument('--truncate', action='store_true',
                        help='清空 price_kline_tdxhub 后全量重拉')
    parser.add_argument('--connect-timeout', type=float, default=1.5,
                        help='tdxhub 单服务器连接/握手超时秒数，默认 1.5')
    parser.add_argument('--max-server-attempts', type=int, default=8,
                        help='启动选路最多尝试服务器数，默认 8')
    parser.add_argument('--per-stock-retry-attempts', type=int, default=0,
                        help='单股拉取失败后的额外服务器重试数，默认 0 以避免慢服务器放大')
    parser.add_argument('--workers', type=int, default=None,
                        help='K 线并发拉取 worker 数；写库仍单线程。默认：raw 增量 8，qfq 全量 4；可用 CM_TDX_KLINE_WORKERS 覆盖')
    parser.add_argument('--max-inflight', type=int, default=0,
                        help='并发模式最多同时挂起的股票请求，默认 workers*4')
    parser.add_argument('--log-every', type=int, default=50,
                        help='每 N 只股票输出一次进度，默认 50')
    parser.add_argument('--write-batch-rows', type=int, default=DEFAULT_WRITE_BATCH_ROWS,
                        help='累计多少行后批量写入 DuckDB，默认 5000；写库仍单线程')
    parser.add_argument('--target-date', default=None,
                        help='增量追新的目标日期，默认使用交易日历最近已完成交易日')
    parser.add_argument('--allow-raw-incremental', action=argparse.BooleanOptionalAction, default=True,
                        help='增量模式使用 tdxhub 原始近端 K 线补缺口并写 factor=1.0，默认启用')
    parser.add_argument('--apply-xdxr-adjustment', action=argparse.BooleanOptionalAction, default=True,
                        help='raw 增量遇到缺口内 xdxr 时，用 price_xdxr 重建 qfq 并记录幂等调整事件，默认启用')
    parser.add_argument('--recalibrate-existing-xdxr', action='store_true',
                        help='不抓行情，仅用 fallback qfq 校准已应用的 tdxhub xdxr 复权事件')
    parser.add_argument('--recalibrate-start-date', default=None,
                        help='校准已应用 xdxr 事件的起始事件日期')
    parser.add_argument('--recalibrate-end-date', default=None,
                        help='校准已应用 xdxr 事件的结束事件日期')
    args = parser.parse_args()

    conn = get_market_conn()
    conn.executescript(TABLE_DDL)
    if args.recalibrate_existing_xdxr:
        result = recalibrate_existing_xdxr_adjustments_from_fallback(
            conn,
            start_date=args.recalibrate_start_date,
            end_date=args.recalibrate_end_date,
        )
        conn.commit()
        logger.info("已校准现有 xdxr 复权事件: %s", result)
        conn.close()
        return

    if args.truncate:
        conn.execute("DELETE FROM price_kline_tdxhub")
        conn.commit()
        logger.info("price_kline_tdxhub 已清空")

    batch_id = f"tdxhub_{time.strftime('%Y%m%d_%H%M%S')}"
    pull_adjust = "qfq"
    if args.skip_existing and args.allow_raw_incremental:
        pull_adjust = None
        logger.info("skip_existing: 使用 tdxhub 原始近端 K 线补新增日期，factor=1.0")
    env_workers = int(os.getenv("CM_TDX_KLINE_WORKERS", "0") or 0)
    workers = resolve_kline_worker_count(
        explicit_workers=args.workers,
        env_workers=env_workers,
        pull_adjust=pull_adjust,
    )
    max_inflight = max(workers, int(args.max_inflight or workers * 4))
    per_stock_total_attempts = max(1, int(args.per_stock_retry_attempts or 0) + 1)
    logger.info(
        "fetch mode: workers=%d max_inflight=%d per_stock_total_attempts=%d",
        workers,
        max_inflight,
        per_stock_total_attempts,
    )
    t0 = time.time()
    n_stocks_done = 0
    n_rows_written = 0
    n_failed = []
    n_raw_xdxr_dropped = 0
    n_xdxr_adjusted_rows = 0
    n_xdxr_adjustment_events = 0
    write_batch_rows = max(1, int(args.write_batch_rows or DEFAULT_WRITE_BATCH_ROWS))
    pending_write_rows: list[dict] = []

    stock_list: list[tuple[str, int]] = []
    client = None
    client_source = ""
    stock_list_source = ""
    stock_list_already_filtered = False

    # 已有每股 max(date), 用于增量只补新日期. 这一步必须早于 TDXHub 触网。
    latest_dates = {}
    xdxr_gap_codes: set[str] = set()
    xdxr_gap_events: dict[str, list[dict]] = {}
    if args.skip_existing:
        latest_dates = load_latest_dates(conn)
        logger.info("skip_existing: 已加载 %d 只股的最新日期, 将只补新增交易日", len(latest_dates))
        target_date, target_source = choose_incremental_target_date(conn, args.target_date)
        local_stock_list, local_source = load_local_active_a_stock_list()
        if target_date and local_stock_list:
            if args.limit > 0:
                local_stock_list = local_stock_list[:args.limit]
                logger.info("限制跑前 %d 只", args.limit)
            before_count = len(local_stock_list)
            stock_list = filter_stale_stock_list(local_stock_list, latest_dates, target_date)
            stock_list_source = local_source
            stock_list_already_filtered = True
            logger.info(
                "skip_existing: 目标日期 %s (%s), 本地预检 stale 股票 %d/%d",
                target_date,
                target_source,
                len(stock_list),
                before_count,
            )
            if not stock_list:
                dt = time.time() - t0
                logger.info(
                    "skip_existing: 本地交易日历和主数据确认无待补 K 线，跳过 TDXHub 连接和行情请求"
                )
                logger.info("=" * 60)
                logger.info("完成: 0 股成功 / 0 股失败 / 0 行写入 / 耗时 %.2f 秒", dt)
                row = conn.execute(
                    "SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(DISTINCT code) "
                    "FROM price_kline_tdxhub"
                ).fetchone()
                logger.info("price_kline_tdxhub 整体: %s ~ %s, 交易日 %d, 股票 %d", *row)
                conn.close()
                return
        elif local_source:
            logger.info("skip_existing: 本地 A 股主数据不可用于预检 (%s)，回退 TDXHub 代码表", local_source)

    if not stock_list:
        stock_list, client, client_source = open_quotes_client_with_retry(
            max_attempts=args.max_server_attempts,
            connect_timeout=args.connect_timeout,
        )
        stock_list_source = client_source
        if args.limit > 0:
            stock_list = stock_list[:args.limit]
            logger.info("限制跑前 %d 只", args.limit)

    if args.skip_existing:
        if target_date:
            if not stock_list_already_filtered:
                before_count = len(stock_list)
                stock_list = filter_stale_stock_list(stock_list, latest_dates, target_date)
                logger.info(
                    "skip_existing: 目标日期 %s (%s), stale 股票 %d/%d",
                    target_date,
                    target_source,
                    len(stock_list),
                    before_count,
                )
            if pull_adjust is None and args.apply_xdxr_adjustment:
                xdxr_gap_events = load_xdxr_gap_events(conn, latest_dates, target_date)
                xdxr_gap_codes = set(xdxr_gap_events)
                if xdxr_gap_codes:
                    logger.info(
                        "skip_existing: %d 只股票缺口内存在价格调整 xdxr，将用 tdxhub+xdxr 重建 qfq",
                        len(xdxr_gap_codes),
                    )
            elif pull_adjust is None:
                xdxr_gap_codes = load_xdxr_gap_codes(conn, latest_dates, target_date)
                if xdxr_gap_codes:
                    logger.info(
                        "skip_existing: %d 只股票缺口内存在 xdxr，将跳过 raw qfq 写入并交给 fallback",
                        len(xdxr_gap_codes),
                    )

    if stock_list_source:
        logger.info("A 股同步清单来源: %s", stock_list_source)

    def flush_pending_rows() -> int:
        nonlocal n_rows_written
        nonlocal pending_write_rows
        if not pending_write_rows:
            return 0
        rows = pending_write_rows
        pending_write_rows = []
        n = write_batch(conn, rows)
        n_rows_written += n
        return n

    def process_normalized_stock(code: str, norm: list[dict], source_name: str) -> None:
        nonlocal n_rows_written
        nonlocal n_stocks_done
        nonlocal n_raw_xdxr_dropped
        nonlocal n_xdxr_adjusted_rows
        nonlocal n_xdxr_adjustment_events
        nonlocal pending_write_rows
        if args.skip_existing:
            if pull_adjust is None:
                code_events = xdxr_gap_events.get(code) or []
                if code_events:
                    adjusted_events = apply_xdxr_adjustment_events(
                        conn,
                        code,
                        code_events,
                        source_name=source_name,
                        batch_id=batch_id,
                        raw_rows=norm,
                    )
                    if adjusted_events:
                        norm, adjusted_count = adjust_rows_for_xdxr_events(norm, adjusted_events)
                        n_xdxr_adjusted_rows += adjusted_count
                        n_xdxr_adjustment_events += len(adjusted_events)
                    else:
                        norm, dropped = filter_raw_incremental_qfq_safe(norm, {code})
                        n_raw_xdxr_dropped += dropped
                elif code in xdxr_gap_codes:
                    norm, dropped = filter_raw_incremental_qfq_safe(norm, {code})
                    n_raw_xdxr_dropped += dropped
            norm = filter_after_latest(norm, latest_dates)
            if not norm:
                return
        pending_write_rows.extend(norm)
        n_stocks_done += 1
        if len(pending_write_rows) >= write_batch_rows:
            flush_pending_rows()

    def log_progress(completed: int) -> None:
        if completed % max(1, args.log_every) == 0:
            flush_pending_rows()
            conn.commit()
            dt = time.time() - t0
            rate = completed / dt if dt > 0 else 0
            eta = (len(stock_list) - completed) / rate / 60 if rate > 0 else 0
            logger.info("进度 %d/%d (%.1f股/s)  写入 %d 行  ETA %.1f min  失败 %d",
                        completed, len(stock_list), rate, n_rows_written, eta, len(n_failed))

    if workers <= 1:
        for i, (code, _market) in enumerate(stock_list):
            try:
                if client is not None:
                    records = pull_one_stock(client, code, pages=args.pages, adjust=pull_adjust, raise_errors=True)
                    source_name = f"{client_source}_raw_incremental" if pull_adjust is None else client_source
                    norm = normalize(records, batch_id, source_name=source_name)
                else:
                    norm, source_name = fetch_one_stock_normalized(
                        code,
                        pages=args.pages,
                        adjust=pull_adjust,
                        batch_id=batch_id,
                        connect_timeout=args.connect_timeout,
                        max_attempts=per_stock_total_attempts,
                        prefer_last_success=True,
                    )
            except Exception as e:
                if args.per_stock_retry_attempts > 0:
                    try:
                        norm, source_name = fetch_one_stock_normalized(
                            code,
                            pages=args.pages,
                            adjust=pull_adjust,
                            batch_id=batch_id,
                            connect_timeout=args.connect_timeout,
                            max_attempts=max(1, int(args.per_stock_retry_attempts)),
                            prefer_last_success=True,
                        )
                    except Exception as retry_e:
                        logger.warning("code=%s 拉取失败: %s; retry_failed=%s", code, e, retry_e)
                        n_failed.append(code)
                        log_progress(i + 1)
                        continue
                else:
                    logger.warning("code=%s 拉取失败: %s", code, e)
                    n_failed.append(code)
                    log_progress(i + 1)
                    continue
            try:
                process_normalized_stock(code, norm, source_name)
            except Exception as e:
                logger.warning("code=%s write 失败: %s", code, e)
                n_failed.append(code)
            log_progress(i + 1)
    else:
        try:
            client.close()
        except Exception:
            pass
        completed = 0
        stock_iter = iter(enumerate(stock_list))
        futures = {}

        def submit_next(pool: ThreadPoolExecutor) -> bool:
            try:
                _idx, (code, _market) = next(stock_iter)
            except StopIteration:
                return False
            future = pool.submit(
                fetch_one_stock_normalized,
                code,
                pages=args.pages,
                adjust=pull_adjust,
                batch_id=batch_id,
                connect_timeout=args.connect_timeout,
                max_attempts=per_stock_total_attempts,
                # Parallel fetches should spread across the server pool instead
                # of queuing behind the single most recent successful server.
                prefer_last_success=False,
            )
            futures[future] = code
            return True

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tdx-kline") as pool:
            for _ in range(min(max_inflight, len(stock_list))):
                if not submit_next(pool):
                    break
            while futures:
                done, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    code = futures.pop(future)
                    try:
                        norm, source_name = future.result()
                    except Exception as e:
                        logger.warning("code=%s 拉取失败: %s", code, e)
                        n_failed.append(code)
                    else:
                        try:
                            process_normalized_stock(code, norm, source_name)
                        except Exception as e:
                            logger.warning("code=%s write 失败: %s", code, e)
                            n_failed.append(code)
                    completed += 1
                    log_progress(completed)
                    submit_next(pool)

    flush_pending_rows()
    conn.commit()
    dt = time.time() - t0
    logger.info("=" * 60)
    logger.info("完成: %d 股成功 / %d 股失败 / %d 行写入 / 耗时 %.1f 分钟",
                n_stocks_done, len(n_failed), n_rows_written, dt / 60)
    if n_raw_xdxr_dropped:
        logger.info("raw incremental 因 xdxr 缺口跳过: %d 行", n_raw_xdxr_dropped)
    if n_xdxr_adjustment_events:
        logger.info(
            "tdxhub+xdxr qfq 重建: %d 个事件 / %d 行新增 raw 记录已应用未来复权因子",
            n_xdxr_adjustment_events,
            n_xdxr_adjusted_rows,
        )

    # 失败列表
    if n_failed:
        logger.info("前 20 个失败 code: %s", n_failed[:20])

    # 全局范围
    row = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(DISTINCT code) "
        "FROM price_kline_tdxhub"
    ).fetchone()
    logger.info("price_kline_tdxhub 整体: %s ~ %s, 交易日 %d, 股票 %d", *row)

    conn.close()


if __name__ == "__main__":
    main()
