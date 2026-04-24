#!/usr/bin/env python3
"""Phase 1 子任务 2: tdxhub Affair.parse 季度财务全量入库 → fact_fundamental_quarterly

数据源: tdxhub.Affair(filename=gpcw<YYYYMMDD>.zip)
  - gpcw<YYYYMMDD>.zip 按季末日期命名 (每季度 1 份)
  - 通达信官方财务服务器 120.76.152.87 聚合三大报表 + 机构持仓 + 业绩预告
  - 583 列, 我们只保留对建模有价值的 ~35 个核心列

覆盖范围:
  - 文件: 1988-12 ~ 2026-09 共 147 份, ~3.8 份/年
  - 最新完整披露季度: 2024-12 (2025-Q1 起逐步披露)
  - 只入库 2020-03 起的季度 (与 price_kline_tdxhub 覆盖对齐, 2019-08+)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, '/Users/dp/Documents/M/tdxhub')

import pandas as pd
from mootdx.affair import Affair

from services.db import get_conn

logger = logging.getLogger("fundamental_quarterly")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


# 从 583 列里选建模核心字段 (按主题分组)
CORE_FIELDS = {
    # 每股指标
    "基本每股收益": "eps_basic",
    "扣除非经常性损益每股收益": "eps_deducted",
    "每股未分配利润": "undist_profit_per_share",
    "每股净资产": "book_value_per_share",
    "每股经营现金流量": "ocf_per_share",
    # 盈利能力
    "净资产收益率": "roe",
    "加权净资产收益率(每股指标)": "roe_weighted",
    # 股东 / 机构持股 (重点!)
    "股东人数(户)": "shareholder_count",
    "机构总量（家）": "inst_count",
    "机构持股总量(股)": "inst_holding_shares",
    "QFII机构数": "qfii_count",
    "QFII持股量": "qfii_shares",
    "基金机构数": "fund_count",
    "基金持股量": "fund_shares",
    "社保机构数": "ssf_count",
    "社保持股量": "ssf_shares",
    "保险机构数": "insurance_count",
    "保险持股量": "insurance_shares",
    "私募机构数": "pe_count",
    "私募持股量": "pe_shares",
    "券商机构数": "broker_count",
    "券商持股量": "broker_shares",
    "第一大股东的持股数量": "top1_shareholder_shares",
    "十大股东持股数量合计(股)": "top10_shareholder_shares",
    "十大流通股东持股数量合计(股)": "top10_float_shareholder_shares",
    "国家队持股数量（万股)": "national_team_shares_10k",
    # 业绩预告 (重点!)
    "业绩预告-本期净利润同比增幅下限%": "yjyg_lower_pct",
    "业绩预告-本期净利润同比增幅上限%": "yjyg_upper_pct",
    # 业绩快报 (重点!)
    "每股收益（业绩快报）": "yjkb_eps",
    "归母净利润（业绩快报）": "yjkb_net_profit",
    "扣非净利润（业绩快报）": "yjkb_net_profit_deducted",
    "加权净资产收益率（业绩快报）": "yjkb_roe_weighted",
    # 股本与流通
    "总股本": "total_shares",
    "已上市流通A股": "float_a_shares",
    "自由流通股(股)": "free_float_shares",
    # 利润表关键
    "营业收入(万元)": "revenue_10k",
    "净利润(万元)": "net_profit_10k",
    "归母净利润（扣非）": "net_profit_deducted_parent",
    # 资产负债表关键
    "资产总计(万元)": "total_assets_10k",
    "负债合计(万元)": "total_liab_10k",
    "归属于母公司股东权益(资产负债表)": "equity_parent",
    # 现金流
    "经营活动产生的现金流量净额(万元)": "ocf_10k",
}


DDL = """
CREATE TABLE IF NOT EXISTS fact_fundamental_quarterly (
    stock_code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    {extra_cols}
    built_at TEXT,
    PRIMARY KEY (stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_ffq_code ON fact_fundamental_quarterly(stock_code);
CREATE INDEX IF NOT EXISTS idx_ffq_date ON fact_fundamental_quarterly(report_date);
"""


def build_ddl() -> str:
    extra = ",\n    ".join(f"{v} REAL" for v in CORE_FIELDS.values())
    return DDL.format(extra_cols=extra + ",\n    ")


def parse_one_quarter(tmpdir: str, filename: str) -> pd.DataFrame:
    try:
        Affair.fetch(downdir=tmpdir, filename=filename)
    except Exception as e:
        logger.warning("fetch %s 失败: %s", filename, e)
        return pd.DataFrame()
    path = os.path.join(tmpdir, filename)
    if not os.path.exists(path) or os.path.getsize(path) < 10_000:  # 占位或空文件
        return pd.DataFrame()
    try:
        df = Affair.parse(downdir=tmpdir, filename=filename)
    except Exception as e:
        logger.warning("parse %s 失败: %s", filename, e)
        return pd.DataFrame()
    if df.empty:
        return df

    # 只保留关心的核心列（缺失列跳过）
    have = [c for c in CORE_FIELDS if c in df.columns]
    df2 = df[have].copy()
    df2 = df2.rename(columns={k: CORE_FIELDS[k] for k in have})
    df2['stock_code'] = df2.index.astype(str).str.zfill(6)
    df2['report_date'] = filename[4:12]  # YYYYMMDD
    # 同一 (stock_code, report_date) 偶见重复行 (数据源 artifact)
    df2 = df2.drop_duplicates(subset=['stock_code', 'report_date'], keep='first')
    # 清理
    os.remove(path)
    return df2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='20230101',
                        help='只入库 report_date >= 此值的季度 (默认 2020-03 起)')
    parser.add_argument('--end', default='',
                        help='报告期上限 (默认不限)')
    parser.add_argument('--truncate', action='store_true',
                        help='清空 fact_fundamental_quarterly 后重建')
    parser.add_argument('--limit-files', type=int, default=0,
                        help='调试: 只处理前 N 份')
    args = parser.parse_args()

    conn = get_conn()
    conn.executescript(build_ddl())

    if args.truncate:
        conn.execute("DELETE FROM fact_fundamental_quarterly")
        conn.commit()
        logger.info("fact_fundamental_quarterly 已清空")

    files = Affair.files()
    # 只处理 gpcw<YYYYMMDD>.zip, filesize > 100KB (排除占位 164B)
    queue = []
    for f in files:
        name = f['filename']
        if not name.startswith('gpcw'): continue
        if f.get('filesize', 0) < 100_000: continue
        date = name[4:12]
        if date < args.start: continue
        if args.end and date > args.end: continue
        queue.append(name)
    queue.sort()  # 按日期
    if args.limit_files > 0:
        queue = queue[:args.limit_files]
    logger.info("共 %d 份季报待解析 (>=%s)", len(queue), args.start)

    tmpdir = tempfile.mkdtemp(prefix='tdxhub_ffq_')
    logger.info("tmpdir: %s", tmpdir)

    built_at = datetime.utcnow().isoformat()
    t0 = time.time()
    n_total = 0
    for i, fname in enumerate(queue):
        df = parse_one_quarter(tmpdir, fname)
        if df.empty:
            logger.info("%s 无数据或 fetch/parse 失败", fname)
            continue
        df['built_at'] = built_at
        cols = ['stock_code', 'report_date'] + list(CORE_FIELDS.values()) + ['built_at']
        have_cols = [c for c in cols if c in df.columns]
        df[have_cols].to_sql('fact_fundamental_quarterly', conn, if_exists='append',
                              index=False, method='multi', chunksize=500)
        n_total += len(df)
        logger.info("[%d/%d] %s rows=%d  累计 %d", i + 1, len(queue), fname, len(df), n_total)
        conn.commit()

    dt = time.time() - t0
    logger.info("=" * 50)
    logger.info("完成 %d 份, 累计 %d 行, 耗时 %.1f 分钟", len(queue), n_total, dt / 60)

    # 全局统计
    row = conn.execute("""
        SELECT MIN(report_date), MAX(report_date),
               COUNT(DISTINCT stock_code), COUNT(DISTINCT report_date)
        FROM fact_fundamental_quarterly
    """).fetchone()
    logger.info("fact_fundamental_quarterly: %s ~ %s, 股票 %d, 季度 %d", *row)

    conn.close()


if __name__ == "__main__":
    main()
