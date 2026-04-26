#!/usr/bin/env python3
"""M9.2 主力资金流 daily ingestion (akshare → raw_fund_flow_daily).

数据源:
  1) akshare ak.stock_individual_fund_flow (eastmoney push2his 后台, 历史 120-250 日)
  2) eastmoney push2delay 直接接口 (只返回最新交易日, 用作网络受限时的 daily fallback)
说明: eastmoney 接口默认返回最多 120-250 个交易日历史. 这是已知限制,
本脚本只拉它能给的, 不假设覆盖到 2023.

用法:
    cd /Users/dp/Documents/M/stock/backend
    python3 -m scripts.fetch_fund_flow_daily            # 全 5507 票全量拉
    python3 -m scripts.fetch_fund_flow_daily --resume   # 跳过已有最新数据的票
    python3 -m scripts.fetch_fund_flow_daily --since 2025-10-01  # 增量
    python3 -m scripts.fetch_fund_flow_daily --max-stocks 100 --rate-limit 0.5  # debug
    python3 -m scripts.fetch_fund_flow_daily --source delay  # push2delay 最新日 fallback

字段映射 (akshare 中文 → 英文):
  日期            → trade_date
  收盘价          → close_price
  涨跌幅          → pct_change
  主力净流入-净额 → main_net_amount
  主力净流入-净占比 → main_net_pct
  超大单净流入-净额 → super_large_net_amount
  超大单净流入-净占比 → super_large_net_pct
  大单净流入-净额 → large_net_amount
  大单净流入-净占比 → large_net_pct
  中单净流入-净额 → medium_net_amount
  中单净流入-净占比 → medium_net_pct
  小单净流入-净额 → small_net_amount
  小单净流入-净占比 → small_net_pct
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests

from services.db import get_conn


logger = logging.getLogger("fetch_fund_flow")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS raw_fund_flow_daily (
    trade_date              TEXT NOT NULL,
    stock_code              TEXT NOT NULL,
    market                  TEXT,
    close_price             REAL,
    pct_change              REAL,
    main_net_amount         REAL,    -- 主力净流入-净额 (单位: 元)
    main_net_pct            REAL,    -- 主力净流入-净占比 (%)
    super_large_net_amount  REAL,
    super_large_net_pct     REAL,
    large_net_amount        REAL,
    large_net_pct           REAL,
    medium_net_amount       REAL,
    medium_net_pct          REAL,
    small_net_amount        REAL,
    small_net_pct           REAL,
    source                  TEXT DEFAULT 'akshare_eastmoney',
    ingested_at             TEXT,
    PRIMARY KEY (trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_rffd_stock_date ON raw_fund_flow_daily(stock_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_rffd_date ON raw_fund_flow_daily(trade_date);

CREATE TABLE IF NOT EXISTS mart_fund_flow_fetch_log (
    run_id          TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    n_total         INTEGER,
    n_success       INTEGER,
    n_failed        INTEGER,
    n_empty         INTEGER,
    n_rows_written  INTEGER,
    failures_json   TEXT,
    notes           TEXT,
    PRIMARY KEY (run_id)
);
"""


# akshare 中文列 → 英文列
COL_MAP = {
    "日期": "trade_date",
    "收盘价": "close_price",
    "涨跌幅": "pct_change",
    "主力净流入-净额": "main_net_amount",
    "主力净流入-净占比": "main_net_pct",
    "超大单净流入-净额": "super_large_net_amount",
    "超大单净流入-净占比": "super_large_net_pct",
    "大单净流入-净额": "large_net_amount",
    "大单净流入-净占比": "large_net_pct",
    "中单净流入-净额": "medium_net_amount",
    "中单净流入-净占比": "medium_net_pct",
    "小单净流入-净额": "small_net_amount",
    "小单净流入-净占比": "small_net_pct",
}


def detect_market(code: str) -> str:
    """6 / 5 / 9 开头 → sh, 其他 → sz. 与 raw_margin_daily / probe_fund_flow.py 同口径."""
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def load_stock_codes(conn) -> list[tuple[str, str]]:
    """从 dim_active_a_stock 取活跃 A 股池."""
    rows = conn.execute(
        """
        SELECT stock_code, market
        FROM dim_active_a_stock
        ORDER BY stock_code
        """
    ).fetchall()
    return [(r[0], (r[1] or "").lower() or detect_market(r[0])) for r in rows]


def latest_per_stock(duck) -> dict[str, str]:
    """读取已有 raw_fund_flow_daily 中每只票的最新 trade_date."""
    try:
        rows = duck.execute(
            "SELECT stock_code, MAX(trade_date) FROM raw_fund_flow_daily GROUP BY stock_code"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def normalize_df(
    df: pd.DataFrame,
    stock_code: str,
    market: str,
    *,
    source: str = "akshare_eastmoney",
) -> pd.DataFrame:
    """akshare DataFrame → 标准化, 返回与 raw_fund_flow_daily 列对齐的 DataFrame."""
    out = df.rename(columns=COL_MAP).copy()
    keep_cols = list(COL_MAP.values())
    # 万一 akshare 改字段名, 缺的列填 None
    for c in keep_cols:
        if c not in out.columns:
            out[c] = None
    out = out[keep_cols]
    # 日期格式化
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out = out.dropna(subset=["trade_date"])
    # 数值列强制转 float
    num_cols = [c for c in keep_cols if c != "trade_date"]
    for c in num_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["stock_code"] = stock_code
    out["market"] = market
    out["source"] = source
    out["ingested_at"] = datetime.utcnow().isoformat()
    return out[
        [
            "trade_date", "stock_code", "market", "close_price", "pct_change",
            "main_net_amount", "main_net_pct",
            "super_large_net_amount", "super_large_net_pct",
            "large_net_amount", "large_net_pct",
            "medium_net_amount", "medium_net_pct",
            "small_net_amount", "small_net_pct",
            "source", "ingested_at",
        ]
    ]


def _fetch_eastmoney_fund_flow(
    stock_code: str,
    market: str,
    *,
    base_url: str,
    timeout: int = 15,
) -> pd.DataFrame:
    """通用东财资金流拉取. base_url 决定历史深度:
    - push2his.eastmoney.com  → 历史 ~250 个交易日
    - push2delay.eastmoney.com → 仅最新交易日 1 行 (接口设计上限)

    其他参数完全相同, 仅 URL 子域不同.
    """
    market_map = {"sh": 1, "sz": 0, "bj": 0}
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{market_map.get(market, 0)}.{stock_code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    }
    session = requests.Session()
    session.trust_env = False
    resp = session.get(base_url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    klines = ((data or {}).get("data") or {}).get("klines") or []
    if not klines:
        return pd.DataFrame(columns=list(COL_MAP.keys()))
    temp_df = pd.DataFrame([item.split(",") for item in klines])
    temp_df.columns = [
        "日期",
        "主力净流入-净额",
        "小单净流入-净额",
        "中单净流入-净额",
        "大单净流入-净额",
        "超大单净流入-净额",
        "主力净流入-净占比",
        "小单净流入-净占比",
        "中单净流入-净占比",
        "大单净流入-净占比",
        "超大单净流入-净占比",
        "收盘价",
        "涨跌幅",
        "-",
        "--",
    ]
    return temp_df[
        [
            "日期",
            "收盘价",
            "涨跌幅",
            "主力净流入-净额",
            "主力净流入-净占比",
            "超大单净流入-净额",
            "超大单净流入-净占比",
            "大单净流入-净额",
            "大单净流入-净占比",
            "中单净流入-净额",
            "中单净流入-净占比",
            "小单净流入-净额",
            "小单净流入-净占比",
        ]
    ]


def fetch_his_fund_flow(stock_code: str, market: str) -> pd.DataFrame:
    """push2his: 历史资金流, ~250 个交易日. Surge fake-ip 通常会挡这个域名."""
    return _fetch_eastmoney_fund_flow(
        stock_code, market,
        base_url="https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    )


def fetch_delay_fund_flow(stock_code: str, market: str) -> pd.DataFrame:
    """push2delay: 仅最新交易日 1 行. 接口设计上限, 不能拉历史."""
    return _fetch_eastmoney_fund_flow(
        stock_code, market,
        base_url="https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get",
    )


def write_batch(duck, df: pd.DataFrame) -> int:
    """INSERT OR REPLACE 一只票的全部行."""
    if df.empty:
        return 0
    duck.register("_ff_batch", df)
    duck.execute("INSERT OR REPLACE INTO raw_fund_flow_daily SELECT * FROM _ff_batch")
    duck.unregister("_ff_batch")
    return len(df)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true",
                        help="跳过已有 trade_date >= today-7 的票 (避免重复拉今日新值)")
    parser.add_argument("--since", default=None,
                        help="只保留 trade_date >= --since 的行 (YYYY-MM-DD)")
    parser.add_argument("--max-stocks", type=int, default=0,
                        help="0 = 全 A 股; >0 = debug 限制")
    parser.add_argument("--rate-limit", type=float, default=0.3,
                        help="每只票之间 sleep 秒数, 默认 0.3, 礼貌 eastmoney")
    parser.add_argument("--retry", type=int, default=2,
                        help="单只票失败重试次数")
    parser.add_argument("--source", choices=["auto", "akshare", "delay"], default="auto",
                        help="auto=先 akshare 历史接口, 失败后 delay 最新日; delay=只拉 push2delay 最新日")
    args = parser.parse_args()

    ak = None
    if args.source in ("auto", "akshare"):
        try:
            import akshare as ak
        except ImportError:
            if args.source == "akshare":
                logger.error("akshare 未安装. pip install akshare")
                sys.exit(1)
            logger.warning("akshare 未安装, 自动切到 push2delay fallback")
            args.source = "delay"
        else:
            logger.info("akshare version: %s", ak.__version__)
    if args.source == "delay":
        logger.info("使用 eastmoney push2delay fallback: 仅返回最新交易日资金流")

    conn = get_conn()
    duck = conn.raw if hasattr(conn, "raw") else conn
    conn.executescript(DDL)

    codes = load_stock_codes(conn)
    if args.max_stocks > 0:
        codes = codes[: args.max_stocks]
    logger.info("准备拉 %d 只 A 股 (rate_limit=%.2fs)", len(codes), args.rate_limit)

    last_dates = latest_per_stock(duck) if args.resume else {}
    today_str = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    run_id = f"fund_flow_fetch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    started_at = datetime.utcnow().isoformat()
    n_success = n_failed = n_empty = n_skip = 0
    rows_written = 0
    failures: list[dict] = []
    t0 = time.time()

    for i, (code, market) in enumerate(codes):
        # resume 跳过已是最新的
        if args.resume and last_dates.get(code, "") >= cutoff:
            n_skip += 1
            if (i + 1) % 500 == 0:
                logger.info("  [%d/%d] skip count=%d", i + 1, len(codes), n_skip)
            continue

        df = None
        last_err = None
        source_used = "akshare_eastmoney"
        for attempt in range(args.retry + 1):
            try:
                if args.source == "delay":
                    df = fetch_delay_fund_flow(code, market)
                    source_used = "eastmoney_push2delay_latest"
                else:
                    df = ak.stock_individual_fund_flow(stock=code, market=market)
                    source_used = "akshare_eastmoney"
                break
            except Exception as exc:
                last_err = str(exc)[:200]
                if attempt < args.retry:
                    time.sleep(1.5 + attempt)
                    continue
                df = None

        if df is None and args.source == "auto":
            try:
                df = fetch_delay_fund_flow(code, market)
                source_used = "eastmoney_push2delay_latest"
                last_err = None
            except Exception as exc:
                last_err = f"akshare failed; delay fallback failed: {str(exc)[:160]}"
                df = None

        if df is None:
            n_failed += 1
            failures.append({"code": code, "market": market, "error": last_err})
            if n_failed <= 20:  # 头 20 个失败打详细
                logger.warning("[%d/%d] %s.%s FAIL: %s", i + 1, len(codes), code, market, last_err)
            time.sleep(args.rate_limit)
            continue
        if df.empty:
            n_empty += 1
            time.sleep(args.rate_limit)
            continue

        try:
            norm = normalize_df(df, code, market, source=source_used)
            if args.since:
                norm = norm[norm["trade_date"] >= args.since]
            n = write_batch(duck, norm)
            conn.commit()
            rows_written += n
            n_success += 1
            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                eta = elapsed / max(i + 1, 1) * (len(codes) - i - 1)
                logger.info(
                    "[%d/%d] ok=%d empty=%d fail=%d skip=%d rows=%d elapsed=%ds eta=%ds",
                    i + 1, len(codes), n_success, n_empty, n_failed, n_skip,
                    rows_written, int(elapsed), int(eta),
                )
        except Exception as exc:
            n_failed += 1
            failures.append({"code": code, "error": f"normalize/write: {str(exc)[:200]}"})
            logger.error("[%d/%d] %s normalize/write FAIL: %s", i + 1, len(codes), code, exc)

        time.sleep(args.rate_limit)

    finished_at = datetime.utcnow().isoformat()
    elapsed = time.time() - t0
    notes = f"全 A 股资金流拉取, source={args.source}"

    import json
    duck.execute(
        """
        INSERT OR REPLACE INTO mart_fund_flow_fetch_log
        (run_id, started_at, finished_at, n_total, n_success, n_failed, n_empty,
         n_rows_written, failures_json, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [run_id, started_at, finished_at, len(codes), n_success, n_failed, n_empty,
         rows_written, json.dumps(failures, ensure_ascii=False), notes],
    )
    conn.commit()

    logger.info("=" * 78)
    logger.info("ingestion 完成 (run_id=%s)", run_id)
    logger.info("  total=%d  ok=%d  empty=%d  fail=%d  skip=%d", len(codes), n_success, n_empty, n_failed, n_skip)
    logger.info("  rows_written=%d", rows_written)
    logger.info("  耗时 %ds (%.1f min)", int(elapsed), elapsed / 60)
    if n_failed:
        logger.warning("  失败 %d 只, 详见 mart_fund_flow_fetch_log.failures_json", n_failed)
    conn.close()


if __name__ == "__main__":
    main()
