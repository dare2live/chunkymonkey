#!/usr/bin/env python3
"""M5 step 2：ak.stock_ggcg_em → raw_executive_trade → fact_executive_trade_event

数据源：东方财富-高管持股（实际包含所有股东增减持，非仅限高管）
主键：(notice_date, stock_code, direction)  —— 同日同股多股东合并

设计：
  raw_executive_trade   每条原始记录（股东级）
  fact_executive_trade_event  按 (notice_date, stock_code, direction) 聚合 + forward return
    增持 = buy, 减持 = sell
    是否个人：股东名称不含公司关键字

Label：gain_20d/60d/max_drawdown_20d/60d（从 price_kline qfq 重算，与 fact_lhb_event 口径一致）

幂等：每次 DROP 重建 raw + fact。不增量。
"""
from __future__ import annotations

import argparse
import logging
import sys
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.market_db import get_canonical_kline_qfq_relation, get_market_conn

logger = logging.getLogger("build_exec_trade")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
KLINE_DAILY_QFQ_RELATION = get_canonical_kline_qfq_relation()


RAW_DDL = """
DROP TABLE IF EXISTS raw_executive_trade;
CREATE TABLE raw_executive_trade (
    notice_date         TEXT NOT NULL,
    stock_code          TEXT NOT NULL,
    stock_name          TEXT,
    shareholder_name    TEXT,
    direction           TEXT,
    change_qty_wan      REAL,
    change_pct_total    REAL,
    change_pct_float    REAL,
    after_qty_wan       REAL,
    after_pct_total     REAL,
    start_date          TEXT,
    end_date            TEXT,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rxt_stock ON raw_executive_trade(stock_code);
CREATE INDEX IF NOT EXISTS idx_rxt_date ON raw_executive_trade(notice_date);
"""


FACT_DDL = """
DROP TABLE IF EXISTS fact_executive_trade_event;
CREATE TABLE fact_executive_trade_event (
    notice_date             TEXT NOT NULL,
    stock_code              TEXT NOT NULL,
    direction               TEXT NOT NULL,
    n_shareholders          INTEGER,
    total_change_qty_wan    REAL,
    total_change_pct_total  REAL,
    max_change_pct_total    REAL,
    any_individual          INTEGER,
    any_corporate           INTEGER,
    gain_20d                REAL,
    gain_60d                REAL,
    max_drawdown_20d        REAL,
    max_drawdown_60d        REAL,
    built_at                TEXT,
    PRIMARY KEY (notice_date, stock_code, direction)
);
CREATE INDEX IF NOT EXISTS idx_fxt_stock ON fact_executive_trade_event(stock_code);
CREATE INDEX IF NOT EXISTS idx_fxt_date ON fact_executive_trade_event(notice_date);
CREATE INDEX IF NOT EXISTS idx_fxt_dir ON fact_executive_trade_event(direction);
"""


CORPORATE_HINTS = ("公司", "集团", "基金", "管理", "合伙", "企业", "投资", "科技",
                   "有限", "股份", "控股", "实业", "产业", "资产")


def is_corporate(name: str) -> bool:
    if not name:
        return False
    return any(k in name for k in CORPORATE_HINTS)


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    empty = getattr(payload, "empty", None)
    if empty is not None:
        try:
            if bool(empty):
                return []
        except Exception:
            pass
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            return [dict(row) for row in to_dict("records")]
        except TypeError:
            return []
    if isinstance(payload, dict):
        return [dict(payload)]
    if isinstance(payload, (str, bytes)):
        return []
    rows = []
    try:
        iterator = iter(payload)
    except TypeError:
        return []
    for row in iterator:
        if isinstance(row, dict):
            rows.append(dict(row))
            continue
        if hasattr(row, "_asdict"):
            rows.append(dict(row._asdict()))
            continue
        try:
            rows.append(dict(row))
        except Exception:
            continue
    return rows


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return value != value
    except Exception:
        return False


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_numbers(values: list[Any]) -> float | None:
    numbers = [_to_float(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return sum(numbers) if numbers else None


def _max_number(values: list[Any]) -> float | None:
    numbers = [_to_float(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return max(numbers) if numbers else None


def _coverage(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get(key) is not None) / len(rows)


def fetch_raw() -> list[dict[str, Any]]:
    import akshare as ak
    logger.info("调用 ak.stock_ggcg_em(symbol='全部')")
    rows = _records_from_payload(ak.stock_ggcg_em(symbol="全部"))
    logger.info("返回 %d 行", len(rows))
    return rows


def normalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rename = {
        "代码": "stock_code",
        "名称": "stock_name",
        "股东名称": "shareholder_name",
        "持股变动信息-增减": "direction",
        "持股变动信息-变动数量": "change_qty_wan",
        "持股变动信息-占总股本比例": "change_pct_total",
        "持股变动信息-占流通股比例": "change_pct_float",
        "变动后持股情况-持股总数": "after_qty_wan",
        "变动后持股情况-占总股本比例": "after_pct_total",
        "变动开始日": "start_date",
        "变动截止日": "end_date",
        "公告日": "notice_date",
    }
    keep = [
        "notice_date", "stock_code", "stock_name", "shareholder_name",
        "direction", "change_qty_wan", "change_pct_total", "change_pct_float",
        "after_qty_wan", "after_pct_total", "start_date", "end_date",
    ]
    normalized = []
    for raw in rows:
        row = {rename.get(key, key): value for key, value in raw.items()}
        direction = str(row.get("direction") or "").strip()
        notice_date = row.get("notice_date")
        if direction not in {"增持", "减持"} or _is_missing(notice_date):
            continue
        out = {key: row.get(key) for key in keep}
        out["stock_code"] = str(out.get("stock_code") or "").zfill(6)
        normalized.append(out)
    return normalized


def aggregate_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logger.info("按 (notice_date, stock_code, direction) 聚合")
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in raw:
        key = (
            str(row.get("notice_date") or ""),
            str(row.get("stock_code") or "").zfill(6),
            str(row.get("direction") or ""),
        )
        groups.setdefault(key, []).append({**row, "stock_code": key[1]})

    ordered_groups = sorted(groups.items())
    agg = []
    for (notice_date, stock_code, direction), rows in ordered_groups:
        direction_norm = {"增持": "buy", "减持": "sell"}.get(direction)
        if not direction_norm:
            continue
        shareholder_names = [
            str(row.get("shareholder_name") or "")
            for row in rows
            if not _is_missing(row.get("shareholder_name"))
        ]
        agg.append({
            "notice_date": notice_date,
            "stock_code": stock_code,
            "direction": direction_norm,
            "n_shareholders": len(shareholder_names),
            "total_change_qty_wan": _sum_numbers([row.get("change_qty_wan") for row in rows]),
            "total_change_pct_total": _sum_numbers([row.get("change_pct_total") for row in rows]),
            "max_change_pct_total": _max_number([row.get("change_pct_total") for row in rows]),
            "any_individual": 1 if any(not is_corporate(name) for name in shareholder_names) else 0,
            "any_corporate": 1 if any(is_corporate(name) for name in shareholder_names) else 0,
        })
    logger.info("聚合后 %d 事件（buy=%d, sell=%d）",
                len(agg),
                sum(1 for row in agg if row["direction"] == "buy"),
                sum(1 for row in agg if row["direction"] == "sell"))
    return agg


def _price_code(row: dict[str, Any]) -> str:
    raw_code = str(row.get("code") or "")
    return raw_code.zfill(6) if raw_code else ""


def _price_date(row: dict[str, Any]) -> str:
    return str(row.get("date") or "")


def _build_price_index(prices: list[dict[str, Any]]) -> dict[str, tuple[list[str], list[float | None]]]:
    grouped: dict[str, tuple[list[str], list[float | None]]] = {}
    ordered_prices = sorted(prices, key=lambda row: (_price_code(row), _price_date(row)))
    for price in ordered_prices:
        code = _price_code(price)
        date = _price_date(price)
        if not code or not date:
            continue
        dates, closes = grouped.setdefault(code, ([], []))
        dates.append(date)
        closes.append(_to_float(price.get("close")))
    return grouped


def _window_return(
    closes: list[float | None],
    entry_idx: int,
    entry_price: float,
    horizon: int,
) -> tuple[float | None, float | None]:
    window = closes[entry_idx + 1 : entry_idx + 1 + horizon]
    valid_closes = [close for close in window if close is not None]
    if not valid_closes:
        return None, None
    return valid_closes[-1] / entry_price - 1, min(close / entry_price - 1 for close in valid_closes)


def _set_window_return(
    row: dict[str, Any],
    closes: list[float | None],
    entry_idx: int,
    entry_price: float,
    horizon: int,
    gain_col: str,
    mdd_col: str,
) -> None:
    gain, max_drawdown = _window_return(closes, entry_idx, entry_price, horizon)
    if gain is None:
        return
    row[gain_col] = gain
    row[mdd_col] = max_drawdown


def _apply_forward_returns(
    events: list[dict[str, Any]],
    prices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    price_index = _build_price_index(prices)

    out = []
    for event in events:
        row = dict(event)
        row["gain_20d"] = None
        row["gain_60d"] = None
        row["max_drawdown_20d"] = None
        row["max_drawdown_60d"] = None

        code = str(row.get("stock_code") or "").zfill(6)
        notice_date = str(row.get("notice_date") or "")
        dates, closes = price_index.get(code, ([], []))
        entry_idx = bisect_right(dates, notice_date)
        if entry_idx >= len(dates):
            out.append(row)
            continue
        entry_price = closes[entry_idx]
        if entry_price is None or entry_price <= 0:
            out.append(row)
            continue
        _set_window_return(row, closes, entry_idx, entry_price, 20, "gain_20d", "max_drawdown_20d")
        _set_window_return(row, closes, entry_idx, entry_price, 60, "gain_60d", "max_drawdown_60d")
        out.append(row)
    return out


def _load_prices_for_codes(mkt: Any, codes: list[str]) -> list[dict[str, Any]]:
    unique_codes = sorted({code for code in codes if code})
    if not unique_codes:
        return []
    temp_table = "tmp_executive_trade_codes"
    mkt.execute(f"DROP TABLE IF EXISTS {temp_table}")
    mkt.execute(f"CREATE TEMP TABLE {temp_table} (code TEXT)")
    try:
        mkt.executemany(
            f"INSERT INTO {temp_table} (code) VALUES (?)",
            [(code,) for code in unique_codes],
        )
        cursor = mkt.execute(
            f"""
            SELECT k.code, k.date, k.close
              FROM {KLINE_DAILY_QFQ_RELATION} k
              JOIN {temp_table} c ON c.code = k.code
             WHERE k.freq='daily' AND k.adjust='qfq'
             ORDER BY k.code, k.date
            """
        )
        return _records_from_cursor(cursor)
    finally:
        mkt.execute(f"DROP TABLE IF EXISTS {temp_table}")


def compute_forward_returns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logger.info("加载 canonical K-line")
    codes = sorted({str(row.get("stock_code") or "").zfill(6) for row in events if row.get("stock_code")})
    if not codes:
        return _apply_forward_returns(events, [])
    mkt = get_market_conn()
    try:
        prices = _load_prices_for_codes(mkt, codes)
    finally:
        mkt.close()
    covered_codes = {row["code"] for row in prices}
    logger.info("price_kline 行 %d（覆盖 %d 股票）", len(prices), len(covered_codes))

    out = _apply_forward_returns(events, prices)
    logger.info("forward return 覆盖率 20d=%.1f%%  60d=%.1f%%",
                100 * _coverage(out, "gain_20d"),
                100 * _coverage(out, "gain_60d"))
    return out


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _insert_rows(conn, table_name: str, rows: list[dict[str, Any]], cols: list[str]) -> None:
    if not rows:
        return
    columns = ", ".join(_quote_ident(col) for col in cols)
    placeholders = ", ".join(["?"] * len(cols))
    conn.executemany(
        f"INSERT INTO {_quote_ident(table_name)} ({columns}) VALUES ({placeholders})",
        [tuple(row.get(col) for col in cols) for row in rows],
    )


def write_raw(conn, raw: list[dict[str, Any]]) -> None:
    conn.executescript(RAW_DDL)
    cols = [
        "notice_date", "stock_code", "stock_name", "shareholder_name",
        "direction", "change_qty_wan", "change_pct_total", "change_pct_float",
        "after_qty_wan", "after_pct_total", "start_date", "end_date",
    ]
    _insert_rows(conn, "raw_executive_trade", raw, cols)
    conn.commit()
    logger.info("写入 raw_executive_trade %d 行", len(raw))


def write_fact(conn, events: list[dict[str, Any]]) -> None:
    conn.executescript(FACT_DDL)
    built_at = datetime.utcnow().isoformat()
    cols = [
        "notice_date", "stock_code", "direction", "n_shareholders",
        "total_change_qty_wan", "total_change_pct_total", "max_change_pct_total",
        "any_individual", "any_corporate",
        "gain_20d", "gain_60d", "max_drawdown_20d", "max_drawdown_60d", "built_at",
    ]
    out = [{**row, "built_at": built_at} for row in events]
    _insert_rows(conn, "fact_executive_trade_event", out, cols)
    conn.commit()
    logger.info("写入 fact_executive_trade_event %d 行", len(events))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    raw = fetch_raw()
    norm = normalize(raw)
    agg = aggregate_events(norm)
    enriched = compute_forward_returns(agg)

    if args.dry_run:
        logger.info("DRY RUN: 不落库; raw=%d events=%d", len(norm), len(enriched))
        return

    conn = get_conn()
    try:
        write_raw(conn, norm)
        write_fact(conn, enriched)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
