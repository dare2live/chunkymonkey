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
import logging
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, '/Users/dp/Documents/M/tdxhub')
warnings.filterwarnings('ignore')

import pandas as pd
from tdxhub.quotes import Quotes

from services.market_db import get_market_conn

logger = logging.getLogger("price_kline_tdxhub")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


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
"""


def _is_a_share(code: str, market: int) -> bool:
    c = str(code).zfill(6)
    if market == 1:  # sh
        return c.startswith('60') or c.startswith('68')
    if market == 0:  # sz
        return c.startswith('00') or c.startswith('30')
    return False


def load_a_stock_list(client) -> list[tuple[str, int]]:
    sh = client.stocks(market=1)
    sz = client.stocks(market=0)
    codes = []
    for _, row in sh.iterrows():
        if _is_a_share(row['code'], 1):
            codes.append((str(row['code']).zfill(6), 1))
    for _, row in sz.iterrows():
        if _is_a_share(row['code'], 0):
            codes.append((str(row['code']).zfill(6), 0))
    logger.info("A 股代码总计 %d (沪 %d, 深 %d)",
                len(codes),
                sum(1 for _, m in codes if m == 1),
                sum(1 for _, m in codes if m == 0))
    return codes


def pull_one_stock(client, code: str, pages: int = 2) -> pd.DataFrame:
    """拉 `pages` 页 qfq bars, 每页 800 根. 返回聚合后的 DataFrame."""
    parts = []
    for start in range(0, pages * 800, 800):
        try:
            df = client.bars(symbol=code, frequency=9, start=start, offset=800, adjust='qfq')
        except Exception as e:
            logger.warning("code=%s start=%d ERR: %s", code, start, e)
            continue
        if df is None or df.empty:
            break
        parts.append(df)
        if len(df) < 800:
            break
    if not parts:
        return pd.DataFrame()
    full = pd.concat(parts, ignore_index=False)
    full['code'] = code
    return full


def normalize(df: pd.DataFrame, batch_id: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out['date'] = out.index.astype(str).str.slice(0, 10)
    # 去掉日内分钟重复
    out = out.drop_duplicates(subset=['code', 'date'])
    out['freq'] = 'daily'
    out['adjust'] = 'qfq'
    out['volume'] = out['vol'].astype(float)
    out['source'] = 'tdxhub'
    out['batch_id'] = batch_id
    cols = ['code', 'date', 'freq', 'adjust', 'open', 'high', 'low', 'close',
            'volume', 'amount', 'factor', 'source', 'batch_id']
    return out[cols].reset_index(drop=True)


def write_batch(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df.to_sql('price_kline_tdxhub', conn, if_exists='append', index=False,
              method='multi', chunksize=500)
    return len(df)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pages', type=int, default=2,
                        help='每股拉 N 页（每页 800 根），默认 2 覆盖约 6 年历史')
    parser.add_argument('--limit', type=int, default=0,
                        help='只跑前 N 只股（调试用）, 0=全量')
    parser.add_argument('--skip-existing', action='store_true',
                        help='若 (code, date) 已在 price_kline_tdxhub，则跳过（增量）')
    parser.add_argument('--truncate', action='store_true',
                        help='清空 price_kline_tdxhub 后全量重拉')
    args = parser.parse_args()

    client = Quotes.factory(market='std', multithread=True, heartbeat=False)
    conn = get_market_conn()
    conn.executescript(TABLE_DDL)

    if args.truncate:
        conn.execute("DELETE FROM price_kline_tdxhub")
        conn.commit()
        logger.info("price_kline_tdxhub 已清空")

    stock_list = load_a_stock_list(client)
    if args.limit > 0:
        stock_list = stock_list[:args.limit]
        logger.info("限制跑前 %d 只", args.limit)

    batch_id = f"tdxhub_{time.strftime('%Y%m%d_%H%M%S')}"
    t0 = time.time()
    n_stocks_done = 0
    n_rows_written = 0
    n_failed = []

    # 已有 code set (skip_existing)
    done_codes = set()
    if args.skip_existing:
        done_codes = {r[0] for r in conn.execute("SELECT DISTINCT code FROM price_kline_tdxhub").fetchall()}
        logger.info("skip_existing: 已有 %d 只股将跳过", len(done_codes))

    for i, (code, _market) in enumerate(stock_list):
        if code in done_codes:
            continue
        df = pull_one_stock(client, code, pages=args.pages)
        if df.empty:
            n_failed.append(code)
            continue
        norm = normalize(df, batch_id)
        try:
            n = write_batch(conn, norm)
            n_rows_written += n
            n_stocks_done += 1
        except Exception as e:
            logger.warning("code=%s write 失败: %s", code, e)
            n_failed.append(code)
            continue
        if (i + 1) % 200 == 0:
            conn.commit()
            dt = time.time() - t0
            rate = (i + 1) / dt if dt > 0 else 0
            eta = (len(stock_list) - (i + 1)) / rate / 60 if rate > 0 else 0
            logger.info("进度 %d/%d (%.1f股/s)  写入 %d 行  ETA %.1f min  失败 %d",
                        i + 1, len(stock_list), rate, n_rows_written, eta, len(n_failed))

    conn.commit()
    dt = time.time() - t0
    logger.info("=" * 60)
    logger.info("完成: %d 股成功 / %d 股失败 / %d 行写入 / 耗时 %.1f 分钟",
                n_stocks_done, len(n_failed), n_rows_written, dt / 60)

    # 失败列表
    if n_failed:
        logger.info("前 20 个失败 code: %s", n_failed[:20])

    # 全局范围
    row = conn.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(DISTINCT code) FROM price_kline_tdxhub").fetchone()
    logger.info("price_kline_tdxhub 整体: %s ~ %s, 交易日 %d, 股票 %d", *row)

    conn.close()


if __name__ == "__main__":
    main()
