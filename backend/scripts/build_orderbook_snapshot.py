#!/usr/bin/env python3
"""Phase 1 子任务 4: tdxhub quotes 五档盘口日度快照

tdxhub `Quotes.quotes(symbol=[codes])` 返回实时 5 档 bid/ask + 内外盘 + 主动买/卖量.
设计为每日收盘后 cron 运行, 保存一份 panel feature.

特征衍生:
  orderbook_imbalance_1   = (bid_vol1 - ask_vol1) / (bid_vol1 + ask_vol1)
  orderbook_imbalance_5   = sum(bid_vol 1-5) / sum(bid+ask vol 1-5) - 0.5
  orderbook_spread_bps    = (ask1 - bid1) / price * 10000
  inside_outside_ratio    = b_vol / s_vol   (内外盘比 = 主买/主卖量)
  active_buy_ratio        = cur_vol / vol   (主动买量/总量)

每次 quotes 支持 80 股左右批量, 全市场 5200 股需 65 次调用.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

STOCK_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(STOCK_ROOT / "tdxhub"))

from tdxhub.quotes import Quotes

from services.market_db import get_market_conn

logger = logging.getLogger("orderbook_snapshot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_orderbook_snapshot (
    snapshot_date  TEXT NOT NULL,
    stock_code     TEXT NOT NULL,
    price          REAL,
    last_close     REAL,
    bid1 REAL, ask1 REAL, bid_vol1 REAL, ask_vol1 REAL,
    bid2 REAL, ask2 REAL, bid_vol2 REAL, ask_vol2 REAL,
    bid3 REAL, ask3 REAL, bid_vol3 REAL, ask_vol3 REAL,
    bid4 REAL, ask4 REAL, bid_vol4 REAL, ask_vol4 REAL,
    bid5 REAL, ask5 REAL, bid_vol5 REAL, ask_vol5 REAL,
    vol REAL, amount REAL,
    cur_vol REAL,      -- 主动买量
    b_vol REAL, s_vol REAL,   -- 内外盘
    imbalance_1 REAL,
    imbalance_5 REAL,
    spread_bps REAL,
    active_buy_ratio REAL,
    inside_outside_ratio REAL,
    built_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_ob_code ON fact_orderbook_snapshot(stock_code);
CREATE INDEX IF NOT EXISTS idx_ob_date ON fact_orderbook_snapshot(snapshot_date);
"""


def _is_a_share(code: str, market: int) -> bool:
    c = str(code).zfill(6)
    if market == 1:
        return c.startswith('60') or c.startswith('68')
    if market == 0:
        return c.startswith('00') or c.startswith('30')
    return False


def load_a_stock_list(client) -> list[str]:
    codes = []
    for mkt in [1, 0]:
        rows = client.stocks_records(market=mkt)
        for row in rows:
            if _is_a_share(row['code'], mkt):
                codes.append(str(row['code']).zfill(6))
    return codes


def _safe_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def compute_derived_features(rows: list[dict]) -> list[dict]:
    """从原始 quotes 输出衍生出我们关心的因子."""
    out = []
    for row in rows:
        item = dict(row)
        for key in (
            "price", "last_close", "bid1", "ask1", "bid_vol1", "ask_vol1",
            "bid2", "ask2", "bid_vol2", "ask_vol2", "bid3", "ask3",
            "bid_vol3", "ask_vol3", "bid4", "ask4", "bid_vol4", "ask_vol4",
            "bid5", "ask5", "bid_vol5", "ask_vol5", "vol", "amount",
            "cur_vol", "b_vol", "s_vol",
        ):
            item[key] = _safe_float(item.get(key))
        bid_total = sum(item.get(f"bid_vol{idx}") or 0 for idx in range(1, 6))
        ask_total = sum(item.get(f"ask_vol{idx}") or 0 for idx in range(1, 6))
        total_bid_ask = bid_total + ask_total
        item["imbalance_1"] = _ratio(
            (item["bid_vol1"] or 0) - (item["ask_vol1"] or 0),
            (item["bid_vol1"] or 0) + (item["ask_vol1"] or 0),
        )
        item["imbalance_5"] = _ratio(bid_total - ask_total, total_bid_ask)
        spread = (
            item["ask1"] - item["bid1"]
            if item["ask1"] is not None and item["bid1"] is not None
            else None
        )
        spread_ratio = _ratio(spread, item["price"])
        item["spread_bps"] = spread_ratio * 10000 if spread_ratio is not None else None
        item["active_buy_ratio"] = _ratio(item["cur_vol"], item["vol"])
        item["inside_outside_ratio"] = _ratio(item["b_vol"], item["s_vol"])
        out.append(item)
    return out


CORE_COLS = [
    'price', 'last_close',
    'bid1', 'ask1', 'bid_vol1', 'ask_vol1',
    'bid2', 'ask2', 'bid_vol2', 'ask_vol2',
    'bid3', 'ask3', 'bid_vol3', 'ask_vol3',
    'bid4', 'ask4', 'bid_vol4', 'ask_vol4',
    'bid5', 'ask5', 'bid_vol5', 'ask_vol5',
    'vol', 'amount', 'cur_vol', 'b_vol', 's_vol',
    'imbalance_1', 'imbalance_5', 'spread_bps',
    'active_buy_ratio', 'inside_outside_ratio',
]


def normalize_snapshot_rows(rows: list[dict], snapshot_date: str) -> list[dict]:
    out = []
    seen = set()
    for row in rows:
        stock_code = str(row.get("code") or "").zfill(6)
        if not stock_code:
            continue
        key = (snapshot_date, stock_code)
        if key in seen:
            continue
        seen.add(key)
        item = {"snapshot_date": snapshot_date, "stock_code": stock_code}
        for col in CORE_COLS:
            item[col] = row.get(col)
        out.append(item)
    return out


def write_batch(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    columns = ["snapshot_date", "stock_code"] + CORE_COLS
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO fact_orderbook_snapshot ({", ".join(columns)})
        VALUES ({placeholders})
        """,
        [tuple(row.get(column) for column in columns) for row in rows],
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-size', type=int, default=60,
                        help='每次 quotes() 调用股数 (TDX 上限 ~80)')
    parser.add_argument('--limit', type=int, default=0, help='只跑前 N 只 (debug)')
    parser.add_argument('--snapshot-date', default=None,
                        help='快照日期 YYYYMMDD, 默认今日')
    args = parser.parse_args()

    client = Quotes.factory(market='std', multithread=True, heartbeat=False)
    conn = get_market_conn()
    conn.executescript(TABLE_DDL)

    codes = load_a_stock_list(client)
    if args.limit:
        codes = codes[:args.limit]
    logger.info("A 股 %d 只准备拉 orderbook", len(codes))

    snapshot_date = args.snapshot_date or datetime.now().strftime('%Y%m%d')

    t0 = time.time()
    total_written = 0
    n_failed = 0
    for i in range(0, len(codes), args.batch_size):
        batch = codes[i:i + args.batch_size]
        try:
            records = client.quotes_records(symbol=batch)
        except Exception as e:
            logger.warning("批 %d ERR: %s", i // args.batch_size, e)
            n_failed += len(batch)
            continue
        if not records:
            n_failed += len(batch)
            continue
        snapshot_rows = normalize_snapshot_rows(compute_derived_features(records), snapshot_date)
        total_written += write_batch(conn, snapshot_rows)
        if (i // args.batch_size + 1) % 20 == 0:
            conn.commit()
            rate = (i + len(batch)) / max(1, time.time() - t0)
            eta = (len(codes) - i - len(batch)) / rate / 60 if rate > 0 else 0
            logger.info("进度 %d/%d (%.0f股/s)  写入 %d  ETA %.1f min  fail %d",
                        i + len(batch), len(codes), rate, total_written, eta, n_failed)

    conn.commit()
    dt = time.time() - t0
    logger.info("=" * 50)
    logger.info("完成: snapshot=%s, 写入 %d 行, 失败 %d, 耗时 %.1f 分钟",
                snapshot_date, total_written, n_failed, dt / 60)

    conn.close()


if __name__ == "__main__":
    main()
