#!/usr/bin/env python3
"""Phase 1 子任务 5: akshare 面板 + 事件数据拉取 (6 个接口)

全部走 akshare. 未用 stock_cyq_em (py_mini_racer 本地库缺失).

接口清单
  1. stock_jgdy_tj_em         机构调研事件流 → fact_jgdy_event
  2. stock_dzjy_mrmx          大宗交易事件流 → fact_dzjy_event
  3. stock_hsgt_hold_stock_em 陆股通 daily panel → fact_hsgt_daily
  4. stock_hot_rank_em        热度 daily panel → fact_hot_rank_daily
  5. stock_research_report_em 新研报事件流 → fact_research_report
  6. stock_profit_forecast_em 分析师 consensus → fact_profit_forecast

每个接口一个 build_* 函数, main() 批量执行.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak
import pandas as pd

from services.db import get_conn

logger = logging.getLogger("akshare_panel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


# ═══════════════════════════════════════════════════════════════════════
# 1. 机构调研事件流
# ═══════════════════════════════════════════════════════════════════════

def _insert_ignore(conn, table: str, df: pd.DataFrame, cols: list[str]):
    """INSERT OR IGNORE 批量写入 (避免 UNIQUE 冲突)."""
    if df.empty:
        return 0
    placeholders = ','.join(['?'] * len(cols))
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    data = df[cols].where(pd.notnull(df[cols]), None).values.tolist()
    conn.executemany(sql, data)
    return len(data)


def build_jgdy_events(conn, dates: list[str]) -> int:
    """按天拉 stock_jgdy_tj_em, 汇总到 fact_jgdy_event(notice_date, stock_code,
    inst_count, survey_type)."""
    conn.executescript("""
    DROP TABLE IF EXISTS fact_jgdy_event;
    CREATE TABLE fact_jgdy_event (
        notice_date TEXT NOT NULL,
        stock_code  TEXT NOT NULL,
        stock_name  TEXT,
        inst_count  INTEGER,
        visit_count INTEGER,
        built_at    TEXT,
        PRIMARY KEY (notice_date, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_jgdy_date ON fact_jgdy_event(notice_date);
    CREATE INDEX IF NOT EXISTS idx_jgdy_code ON fact_jgdy_event(stock_code);
    """)
    total = 0
    built_at = datetime.utcnow().isoformat()
    for d in dates:
        try:
            df = ak.stock_jgdy_tj_em(date=d)
        except Exception as e:
            logger.warning("jgdy %s: %s", d, e)
            continue
        if df is None or df.empty:
            continue
        # 东财字段: 代码 / 名称 / 接待机构数量 / 调研家数 / 公告日期
        cols_map = {'代码': 'stock_code', '名称': 'stock_name',
                    '接待机构数量': 'inst_count', '公告日期': 'notice_date'}
        present = {k: v for k, v in cols_map.items() if k in df.columns}
        if 'stock_code' not in present.values():
            continue
        df2 = df[list(present.keys())].rename(columns=present).copy()
        df2['stock_code'] = df2['stock_code'].astype(str).str.zfill(6)
        df2['notice_date'] = df2['notice_date'].astype(str).str.replace('-', '', regex=False)
        df2['visit_count'] = df2.get('visit_count', 1)
        df2['built_at'] = built_at
        df2 = df2.drop_duplicates(subset=['notice_date', 'stock_code'], keep='first')
        cols = ['notice_date', 'stock_code', 'stock_name', 'inst_count', 'visit_count', 'built_at']
        have = [c for c in cols if c in df2.columns]
        # 用 INSERT OR IGNORE 处理跨批重复
        _insert_ignore(conn, 'fact_jgdy_event', df2, have)
        total += len(df2)
        conn.commit()  # 每日 commit 避免长事务锁 db
    logger.info("fact_jgdy_event %d 条", total)
    return total


# ═══════════════════════════════════════════════════════════════════════
# 2. 大宗交易事件流
# ═══════════════════════════════════════════════════════════════════════

def build_dzjy_events(conn, date_windows: list[tuple[str, str]]) -> int:
    conn.executescript("""
    DROP TABLE IF EXISTS fact_dzjy_event;
    CREATE TABLE fact_dzjy_event (
        trade_date    TEXT NOT NULL,
        stock_code    TEXT NOT NULL,
        stock_name    TEXT,
        price         REAL,
        prem_pct      REAL,
        volume_wan    REAL,
        amount_wan    REAL,
        buyer_name    TEXT,
        seller_name   TEXT,
        built_at      TEXT,
        PRIMARY KEY (trade_date, stock_code, buyer_name, seller_name)
    );
    CREATE INDEX IF NOT EXISTS idx_dzjy_date ON fact_dzjy_event(trade_date);
    CREATE INDEX IF NOT EXISTS idx_dzjy_code ON fact_dzjy_event(stock_code);
    """)
    total = 0
    built_at = datetime.utcnow().isoformat()
    for start, end in date_windows:
        try:
            df = ak.stock_dzjy_mrmx(symbol='A股', start_date=start, end_date=end)
        except Exception as e:
            logger.warning("dzjy %s~%s: %s", start, end, e)
            continue
        if df is None or df.empty:
            continue
        # 东财字段常见: 交易日期 / 证券代码 / 证券简称 / 成交价 / 折溢率 / 成交量 / 成交额 / 买方营业部 / 卖方营业部
        cols_map = {
            '交易日期': 'trade_date', '证券代码': 'stock_code', '证券简称': 'stock_name',
            '成交价': 'price', '折溢率': 'prem_pct',
            '成交量': 'volume_wan', '成交额': 'amount_wan',
            '买方营业部': 'buyer_name', '卖方营业部': 'seller_name',
        }
        present = {k: v for k, v in cols_map.items() if k in df.columns}
        df2 = df[list(present.keys())].rename(columns=present).copy()
        if 'trade_date' in df2:
            df2['trade_date'] = df2['trade_date'].astype(str).str.replace('-', '', regex=False)
        if 'stock_code' in df2:
            df2['stock_code'] = df2['stock_code'].astype(str).str.zfill(6)
        df2['buyer_name'] = df2.get('buyer_name', '').astype(str).fillna('').str.slice(0, 60)
        df2['seller_name'] = df2.get('seller_name', '').astype(str).fillna('').str.slice(0, 60)
        df2['built_at'] = built_at
        df2 = df2.drop_duplicates(subset=['trade_date', 'stock_code', 'buyer_name', 'seller_name'], keep='first')
        cols = ['trade_date', 'stock_code', 'stock_name', 'price', 'prem_pct',
                'volume_wan', 'amount_wan', 'buyer_name', 'seller_name', 'built_at']
        have = [c for c in cols if c in df2.columns]
        _insert_ignore(conn, 'fact_dzjy_event', df2, have)
        total += len(df2)
        conn.commit()
    logger.info("fact_dzjy_event %d 条", total)
    return total


# ═══════════════════════════════════════════════════════════════════════
# 3. 陆股通每日个股持股
# ═══════════════════════════════════════════════════════════════════════

def build_hsgt_daily(conn, today_only: bool = True) -> int:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS fact_hsgt_daily (
        snapshot_date TEXT NOT NULL,
        stock_code    TEXT NOT NULL,
        stock_name    TEXT,
        hold_shares   REAL,
        hold_market_value REAL,
        hold_pct_of_float REAL,
        built_at      TEXT,
        PRIMARY KEY (snapshot_date, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_hsgt_d_code ON fact_hsgt_daily(stock_code);
    CREATE INDEX IF NOT EXISTS idx_hsgt_d_date ON fact_hsgt_daily(snapshot_date);
    """)
    total = 0
    built_at = datetime.utcnow().isoformat()
    today = datetime.now().strftime('%Y%m%d')
    try:
        df = ak.stock_hsgt_hold_stock_em(market='沪股通', indicator='今日排行')
    except Exception as e:
        logger.warning("hsgt sh: %s", e)
        return 0
    try:
        df2 = ak.stock_hsgt_hold_stock_em(market='深股通', indicator='今日排行')
    except Exception as e:
        logger.warning("hsgt sz: %s", e)
        df2 = pd.DataFrame()
    if not df.empty and not df2.empty:
        full = pd.concat([df, df2], ignore_index=True)
    else:
        full = df if not df.empty else df2

    if full.empty:
        return 0

    cols_map = {'代码': 'stock_code', '股票代码': 'stock_code',
                '名称': 'stock_name', '股票简称': 'stock_name',
                '今日持股-股数': 'hold_shares', '持股数量': 'hold_shares',
                '今日持股-市值': 'hold_market_value', '持股市值': 'hold_market_value',
                '今日持股-占流通股比': 'hold_pct_of_float',
                '持股数量占发行股百分比': 'hold_pct_of_float',
                '日期': 'snapshot_date'}
    # 当一个目标列有多个候选源列时，取第一个有值的
    mapped = {}
    for src, tgt in cols_map.items():
        if src in full.columns and tgt not in mapped:
            mapped[src] = tgt
    full2 = full[list(mapped.keys())].rename(columns=mapped).copy()
    # 用文件里的 `日期` 作为 snapshot 更准确
    if 'snapshot_date' in full2.columns:
        full2['snapshot_date'] = full2['snapshot_date'].astype(str).str.replace('-', '', regex=False)
    else:
        full2['snapshot_date'] = today
    full2['stock_code'] = full2['stock_code'].astype(str).str.zfill(6)
    full2['built_at'] = built_at
    full2 = full2.drop_duplicates(subset=['snapshot_date', 'stock_code'], keep='first')
    cols = ['snapshot_date', 'stock_code', 'stock_name', 'hold_shares',
            'hold_market_value', 'hold_pct_of_float', 'built_at']
    have = [c for c in cols if c in full2.columns]
    full2[have].to_sql('fact_hsgt_daily', conn, if_exists='append', index=False,
                       method='multi', chunksize=500)
    conn.commit()
    total = len(full2)
    logger.info("fact_hsgt_daily +%d 条 (snapshot=%s)", total, today)
    return total


# ═══════════════════════════════════════════════════════════════════════
# 4. 热度 daily panel (只拉当日 top 100)
# ═══════════════════════════════════════════════════════════════════════

def build_hot_rank_daily(conn) -> int:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS fact_hot_rank_daily (
        snapshot_date TEXT NOT NULL,
        stock_code    TEXT NOT NULL,
        stock_name    TEXT,
        rank_value    INTEGER,
        hot_score     REAL,
        built_at      TEXT,
        PRIMARY KEY (snapshot_date, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_hot_d_code ON fact_hot_rank_daily(stock_code);
    """)
    today = datetime.now().strftime('%Y%m%d')
    built_at = datetime.utcnow().isoformat()
    try:
        df = ak.stock_hot_rank_em()
    except Exception as e:
        logger.warning("hot_rank: %s", e)
        return 0
    if df is None or df.empty:
        return 0
    cols_map = {'当前排名': 'rank_value', '代码': 'stock_code',
                '股票名称': 'stock_name', '最新价': 'hot_score'}
    present = {k: v for k, v in cols_map.items() if k in df.columns}
    df2 = df[list(present.keys())].rename(columns=present).copy()
    df2['stock_code'] = df2['stock_code'].astype(str).str.zfill(6).str.slice(-6)
    df2['snapshot_date'] = today
    df2['built_at'] = built_at
    df2 = df2.drop_duplicates(subset=['snapshot_date', 'stock_code'], keep='first')
    cols = ['snapshot_date', 'stock_code', 'stock_name', 'rank_value', 'hot_score', 'built_at']
    have = [c for c in cols if c in df2.columns]
    df2[have].to_sql('fact_hot_rank_daily', conn, if_exists='append', index=False,
                     method='multi', chunksize=500)
    conn.commit()
    logger.info("fact_hot_rank_daily +%d 条 (snapshot=%s)", len(df2), today)
    return len(df2)


# ═══════════════════════════════════════════════════════════════════════
# 5. 新研报事件 (按股拉: 先拉全市场股票列表再过滤)
# ═══════════════════════════════════════════════════════════════════════

def build_research_report(conn, top_stocks: list[str]) -> int:
    conn.executescript("""
    DROP TABLE IF EXISTS fact_research_report;
    CREATE TABLE fact_research_report (
        report_date TEXT NOT NULL,
        stock_code  TEXT NOT NULL,
        title       TEXT,
        institution TEXT,
        rating      TEXT,
        last_rating TEXT,
        target_price REAL,
        profit_2y_cagr REAL,
        built_at    TEXT,
        PRIMARY KEY (report_date, stock_code, institution, title)
    );
    CREATE INDEX IF NOT EXISTS idx_rr_code ON fact_research_report(stock_code);
    CREATE INDEX IF NOT EXISTS idx_rr_date ON fact_research_report(report_date);
    """)
    built_at = datetime.utcnow().isoformat()
    total = 0
    for i, code in enumerate(top_stocks):
        try:
            df = ak.stock_research_report_em(symbol=code)
        except Exception as e:
            logger.warning("rr %s: %s", code, e)
            continue
        if df is None or df.empty:
            continue
        # 东财字段: 报告日期 / 标题 / 机构 / 评级变动 / 目标价等
        cols_map = {'日期': 'report_date', '报告标题': 'title',
                    '研报机构': 'institution', '评级': 'rating',
                    '最新评级': 'rating', '最新目标价': 'target_price'}
        present = {k: v for k, v in cols_map.items() if k in df.columns}
        df2 = df[list(present.keys())].rename(columns=present).copy()
        df2['stock_code'] = code
        if 'report_date' in df2:
            df2['report_date'] = df2['report_date'].astype(str).str.replace('-', '', regex=False)
        df2['institution'] = df2.get('institution', '').astype(str).fillna('').str.slice(0, 60)
        df2['title'] = df2.get('title', '').astype(str).fillna('').str.slice(0, 120)
        df2['built_at'] = built_at
        df2 = df2.drop_duplicates(subset=['report_date', 'stock_code', 'institution', 'title'], keep='first')
        cols = ['report_date', 'stock_code', 'title', 'institution', 'rating',
                'target_price', 'built_at']
        have = [c for c in cols if c in df2.columns]
        _insert_ignore(conn, 'fact_research_report', df2, have)
        total += len(df2)
        if (i + 1) % 100 == 0:
            conn.commit()
            logger.info("rr 进度 %d/%d 累计 %d", i + 1, len(top_stocks), total)
    conn.commit()
    logger.info("fact_research_report %d 条", total)
    return total


# ═══════════════════════════════════════════════════════════════════════
# 6. 分析师 consensus 盈利预测 (全市场 snapshot)
# ═══════════════════════════════════════════════════════════════════════

def build_profit_forecast(conn) -> int:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS fact_profit_forecast_daily (
        snapshot_date TEXT NOT NULL,
        stock_code    TEXT NOT NULL,
        stock_name    TEXT,
        forecast_inst_count INTEGER,
        eps_forecast_this_year REAL,
        eps_forecast_next_year REAL,
        profit_yoy REAL,
        built_at      TEXT,
        PRIMARY KEY (snapshot_date, stock_code)
    );
    CREATE INDEX IF NOT EXISTS idx_pf_code ON fact_profit_forecast_daily(stock_code);
    """)
    built_at = datetime.utcnow().isoformat()
    today = datetime.now().strftime('%Y%m%d')
    try:
        df = ak.stock_profit_forecast_em(symbol='')
    except Exception as e:
        logger.warning("profit_forecast: %s", e)
        return 0
    if df is None or df.empty:
        return 0
    # 东财字段: 代码 / 名称 / 研报数 / 每股收益 等 (名称随版本变)
    cols_map = {'代码': 'stock_code', '名称': 'stock_name', '研报数': 'forecast_inst_count',
                '每股收益': 'eps_forecast_this_year'}
    present = {k: v for k, v in cols_map.items() if k in df.columns}
    if 'stock_code' not in present.values():
        logger.warning("profit_forecast: 字段命名异常, 跳过")
        return 0
    df2 = df[list(present.keys())].rename(columns=present).copy()
    df2['stock_code'] = df2['stock_code'].astype(str).str.zfill(6)
    df2['snapshot_date'] = today
    df2['built_at'] = built_at
    df2 = df2.drop_duplicates(subset=['snapshot_date', 'stock_code'], keep='first')
    cols = ['snapshot_date', 'stock_code', 'stock_name', 'forecast_inst_count',
            'eps_forecast_this_year', 'built_at']
    have = [c for c in cols if c in df2.columns]
    df2[have].to_sql('fact_profit_forecast_daily', conn, if_exists='append', index=False,
                     method='multi', chunksize=500)
    conn.commit()
    logger.info("fact_profit_forecast_daily +%d 条 (snapshot=%s)", len(df2), today)
    return len(df2)


# ═══════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════

def _date_range(start: str, end: str):
    """生成每日日期字符串列表 YYYYMMDD."""
    s = datetime.strptime(start, '%Y%m%d')
    e = datetime.strptime(end, '%Y%m%d')
    out = []
    cur = s
    while cur <= e:
        out.append(cur.strftime('%Y%m%d'))
        cur = cur.replace(day=cur.day) if cur.day < 28 else cur  # placeholder
        cur += pd.Timedelta(days=1)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', default='jgdy,dzjy,hsgt,hot,profit_forecast',
                        help='逗号分隔: jgdy, dzjy, hsgt, hot, rr, profit_forecast')
    parser.add_argument('--jgdy-start', default='20230101')
    parser.add_argument('--jgdy-end', default=datetime.now().strftime('%Y%m%d'))
    parser.add_argument('--dzjy-start', default='20230101')
    parser.add_argument('--dzjy-end', default=datetime.now().strftime('%Y%m%d'))
    parser.add_argument('--rr-top-n', type=int, default=500,
                        help='研报只拉按机构调研热度 top N (避免全市场 5k 股)')
    args = parser.parse_args()

    tasks = set(args.tasks.split(','))
    conn = get_conn()
    t_overall = time.time()

    if 'jgdy' in tasks:
        t0 = time.time()
        dates = _date_range(args.jgdy_start, args.jgdy_end)
        logger.info("jgdy 日期数 %d", len(dates))
        build_jgdy_events(conn, dates)
        logger.info("jgdy 耗时 %.1fs", time.time() - t0)

    if 'dzjy' in tasks:
        t0 = time.time()
        # 按月拉, 一次 30 天
        windows = []
        s = datetime.strptime(args.dzjy_start, '%Y%m%d')
        e = datetime.strptime(args.dzjy_end, '%Y%m%d')
        cur = s
        while cur <= e:
            nxt = min(cur + pd.Timedelta(days=30), e)
            windows.append((cur.strftime('%Y%m%d'), nxt.strftime('%Y%m%d')))
            cur = nxt + pd.Timedelta(days=1)
        logger.info("dzjy 月窗口数 %d", len(windows))
        build_dzjy_events(conn, windows)
        logger.info("dzjy 耗时 %.1fs", time.time() - t0)

    if 'hsgt' in tasks:
        t0 = time.time()
        build_hsgt_daily(conn, today_only=True)
        logger.info("hsgt 耗时 %.1fs", time.time() - t0)

    if 'hot' in tasks:
        t0 = time.time()
        build_hot_rank_daily(conn)
        logger.info("hot 耗时 %.1fs", time.time() - t0)

    if 'rr' in tasks:
        t0 = time.time()
        # 取 jgdy 里 inst_count 最多的前 N 只股票 (热门) 拉研报
        rows = conn.execute("""
            SELECT stock_code FROM fact_jgdy_event
            GROUP BY stock_code ORDER BY SUM(inst_count) DESC LIMIT ?
        """, (args.rr_top_n,)).fetchall()
        top_codes = [r[0] for r in rows]
        if not top_codes:
            # fallback: 用 fact_institution_event 里的股票
            rows = conn.execute("""
                SELECT stock_code, COUNT(*) c FROM fact_institution_event
                WHERE notice_date >= '20230101'
                GROUP BY stock_code ORDER BY c DESC LIMIT ?
            """, (args.rr_top_n,)).fetchall()
            top_codes = [r[0] for r in rows]
        logger.info("rr top_codes %d 只", len(top_codes))
        build_research_report(conn, top_codes)
        logger.info("rr 耗时 %.1fs", time.time() - t0)

    if 'profit_forecast' in tasks:
        t0 = time.time()
        build_profit_forecast(conn)
        logger.info("profit_forecast 耗时 %.1fs", time.time() - t0)

    logger.info("=" * 50)
    logger.info("全部任务完成, 总耗时 %.1f 分钟", (time.time() - t_overall) / 60)
    conn.close()


if __name__ == "__main__":
    main()
