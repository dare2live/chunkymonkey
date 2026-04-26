#!/usr/bin/env python3
"""路线 B: Tushare Pro moneyflow → raw_fund_flow_daily 全量历史回填.

前置:
  pip install tushare
  方式 1: export TUSHARE_TOKEN=xxxxxxxxxxxx
  方式 2: echo 'xxxxxxxxxxxx' > ~/.tushare_token

用法:
  cd /Users/dp/Documents/M/stock/backend
  # 全量回填最近 N 个交易日
  python3 -m scripts.fetch_fund_flow_tushare --start 20250101 --end 20260426

  # 增量 (从 raw_fund_flow_daily 已有最新日 + 1 起拉到 trade_cal 最新交易日)
  python3 -m scripts.fetch_fund_flow_tushare --resume

  # 单日测试
  python3 -m scripts.fetch_fund_flow_tushare --date 20260424

策略:
  - 按 trade_date **一次拉全市场** (pro.moneyflow(trade_date='YYYYMMDD'))
    单次返回 ~5500 票, 250 天 ≈ 1 分钟, 远优于按 stock_code 5500 次单拉
  - 限流: Tushare Pro 默认 200 次/分钟. 我们按日 ~250 次/run, 远低于上限

字段映射 (Tushare → raw_fund_flow_daily):
  - 单位: Tushare 万元 → 东财元 (× 10000)
  - 主力定义同 (超大单 + 大单)
  - main_net_pct / *_net_pct: Tushare moneyflow 不返回, 置 NULL
  - close_price / pct_change: moneyflow 不返回, 置 NULL
  - source = 'tushare_pro_moneyflow'

入库: 同 raw_fund_flow_daily, INSERT OR REPLACE 主键 (trade_date, stock_code).
若主键已存在 (例如 push2delay 已写入的当日), Tushare 数据会覆盖 (因其字段更全).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import get_conn

logger = logging.getLogger("fetch_fund_flow_tushare")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def load_token() -> str:
    """优先环境变量, 回退 ~/.tushare_token 文件."""
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    token_file = Path.home() / ".tushare_token"
    if token_file.exists():
        token = token_file.read_text().strip()
        if token:
            return token
    raise RuntimeError(
        "未找到 Tushare token. 设置 TUSHARE_TOKEN 环境变量, "
        "或写入 ~/.tushare_token (chmod 600)."
    )


def init_pro():
    try:
        import tushare as ts
    except ImportError:
        raise RuntimeError("Tushare 未安装. 运行: pip install tushare")
    token = load_token()
    ts.set_token(token)
    return ts.pro_api()


def fetch_trade_cal(pro, start: str, end: str) -> list[str]:
    """Tushare 交易日历, 返回 YYYYMMDD list (仅交易日)."""
    df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open=1)
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].astype(str).tolist())


def fetch_moneyflow_one_day(pro, trade_date: str) -> pd.DataFrame | None:
    """单日全市场资金流. trade_date: YYYYMMDD."""
    df = pro.moneyflow(trade_date=trade_date)
    if df is None or df.empty:
        return None
    return df


def normalize_to_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Tushare moneyflow → raw_fund_flow_daily 列对齐."""
    out = pd.DataFrame()
    # YYYYMMDD → YYYY-MM-DD
    out["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    parts = df["ts_code"].str.split(".", expand=True)
    out["stock_code"] = parts[0]
    out["market"] = parts[1].str.lower()
    out["close_price"] = None
    out["pct_change"] = None

    # Tushare 单位: 万元. 东财: 元. → × 10000
    UNIT = 10000.0
    out["main_net_amount"] = df["net_mf_amount"].astype(float) * UNIT
    out["main_net_pct"] = None  # Tushare moneyflow 不直接给净占比

    out["super_large_net_amount"] = (df["buy_elg_amount"].astype(float) - df["sell_elg_amount"].astype(float)) * UNIT
    out["super_large_net_pct"] = None
    out["large_net_amount"] = (df["buy_lg_amount"].astype(float) - df["sell_lg_amount"].astype(float)) * UNIT
    out["large_net_pct"] = None
    out["medium_net_amount"] = (df["buy_md_amount"].astype(float) - df["sell_md_amount"].astype(float)) * UNIT
    out["medium_net_pct"] = None
    out["small_net_amount"] = (df["buy_sm_amount"].astype(float) - df["sell_sm_amount"].astype(float)) * UNIT
    out["small_net_pct"] = None

    out["source"] = "tushare_pro_moneyflow"
    out["ingested_at"] = datetime.now().isoformat()
    return out


def upsert(conn, df: pd.DataFrame) -> int:
    """INSERT OR REPLACE 主键 (trade_date, stock_code)."""
    if df.empty:
        return 0
    cols = list(df.columns)
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO raw_fund_flow_daily ({col_list}) VALUES ({placeholders})"
    rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def latest_in_db(conn) -> str | None:
    row = conn.execute(
        "SELECT MAX(trade_date) FROM raw_fund_flow_daily WHERE source = 'tushare_pro_moneyflow'"
    ).fetchone()
    return row[0] if row and row[0] else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", help="结束日期 YYYYMMDD, 默认今天")
    parser.add_argument("--date", help="单日测试 YYYYMMDD (覆盖 --start/--end)")
    parser.add_argument("--resume", action="store_true",
                        help="从已有 tushare 数据最新日 +1 起增量到今天")
    parser.add_argument("--rate-limit", type=float, default=0.3,
                        help="每个交易日之间 sleep 秒数, Tushare 默认 200/min, 0.3s 留余量")
    parser.add_argument("--max-days", type=int, default=0,
                        help="最多拉多少个交易日, 0=不限")
    args = parser.parse_args()

    pro = init_pro()
    conn = get_conn()

    today = date.today().strftime("%Y%m%d")

    if args.date:
        dates = [args.date]
    elif args.resume:
        latest = latest_in_db(conn)
        if latest:
            start_dt = datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)
            start = start_dt.strftime("%Y%m%d")
        else:
            # 默认回填最近 250 个自然日
            start = (date.today() - timedelta(days=400)).strftime("%Y%m%d")
        end = today
        logger.info(f"[resume] 起 {start} 止 {end}")
        dates = fetch_trade_cal(pro, start, end)
    else:
        start = args.start or (date.today() - timedelta(days=400)).strftime("%Y%m%d")
        end = args.end or today
        logger.info(f"[range] 起 {start} 止 {end}")
        dates = fetch_trade_cal(pro, start, end)

    if not dates:
        logger.warning("未取到任何交易日, 退出")
        return

    if args.max_days > 0:
        dates = dates[-args.max_days:]

    logger.info(f"准备拉取 {len(dates)} 个交易日: {dates[0]} ~ {dates[-1]}")
    total_rows = 0
    n_success, n_empty, n_failed = 0, 0, 0
    failures = []

    for i, d in enumerate(dates, 1):
        try:
            df = fetch_moneyflow_one_day(pro, d)
            if df is None or df.empty:
                logger.info(f"[{i}/{len(dates)}] {d} 空返回, 跳过")
                n_empty += 1
                continue
            normalized = normalize_to_schema(df)
            written = upsert(conn, normalized)
            logger.info(f"[{i}/{len(dates)}] {d} 写入 {written} 行")
            total_rows += written
            n_success += 1
        except Exception as exc:
            logger.exception(f"[{i}/{len(dates)}] {d} 失败: {exc}")
            n_failed += 1
            failures.append({"date": d, "error": str(exc)[:200]})

        if args.rate_limit > 0 and i < len(dates):
            time.sleep(args.rate_limit)

    logger.info(
        f"完成: 交易日 {len(dates)} (成功 {n_success} / 空 {n_empty} / 失败 {n_failed}), "
        f"累计写入 {total_rows} 行"
    )
    if failures:
        logger.warning(f"失败明细: {failures[:10]}")


if __name__ == "__main__":
    main()
