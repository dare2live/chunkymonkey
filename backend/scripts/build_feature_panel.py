#!/usr/bin/env python3
"""Phase 2: 特征工程 → fact_feature_panel (daily panel, stock × date × features)

三 Pillar + regime + 标签的统一组装, 供 Phase 3 qlib+optuna 建模.

Pillars
  A 事件 (rolling count + days_since):
    inst_event_count_30d/60d, exec_buy_count_90d, lhb_inst_buy_30d/60d
    jgdy_count_60d, dzjy_count_60d
    days_since_last_exec_buy, days_since_last_lhb
  B 面板 (daily):
    return_1d/5d/20d/60d, vol_zscore_20d, close_ma_ratio_5/20/60/250
    rz_balance (两融), rz_chg_5d_pct
  C 基本面 (季度 forward-fill to daily):
    shareholder_count_qoq, inst_count_qoq, fund_count_qoq, qfii_count_qoq
    yjyg_lower_pct, yjyg_upper_pct, roe, eps_basic
  Regime:
    hs300_ret_20d, hs300_ret_60d, regime_flag (up/flat/down)
  Label:
    forward_return_20d = close[t+21]/close[t+1] - 1  (T+1 entry, 20d 持有)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import get_conn
from services.market_db import get_market_conn

logger = logging.getLogger("feature_panel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


PANEL_DDL = """
DROP TABLE IF EXISTS fact_feature_panel;
CREATE TABLE fact_feature_panel (
    stock_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    -- Pillar B 价量
    close REAL, ret_1d REAL, ret_5d REAL, ret_20d REAL, ret_60d REAL,
    vol_z20d REAL, ma_ratio_5 REAL, ma_ratio_20 REAL, ma_ratio_60 REAL, ma_ratio_250 REAL,
    rz_balance REAL, rz_chg_5d_pct REAL,
    -- Pillar A 事件 rolling
    inst_event_count_30d INTEGER, inst_event_count_60d INTEGER,
    exec_buy_count_90d INTEGER, exec_buy_ge1_count_90d INTEGER,
    lhb_inst_buy_count_30d INTEGER, lhb_inst_buy_count_60d INTEGER,
    jgdy_count_60d INTEGER,
    dzjy_count_60d INTEGER,
    days_since_exec_buy INTEGER, days_since_lhb INTEGER,
    -- Pillar C 基本面 (latest quarter fw-fill)
    shareholder_count_qoq REAL, inst_count_qoq REAL,
    fund_count_qoq REAL, qfii_count_qoq REAL,
    yjyg_lower_pct REAL, yjyg_upper_pct REAL,
    roe REAL, eps_basic REAL,
    -- Regime
    hs300_ret_20d REAL, hs300_ret_60d REAL, regime_flag TEXT,
    -- Label
    forward_ret_20d REAL,
    built_at TEXT,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fp_code ON fact_feature_panel(stock_code);
CREATE INDEX IF NOT EXISTS idx_fp_date ON fact_feature_panel(date);
"""


def load_price_panel(mkt_conn, start_date: str) -> pd.DataFrame:
    logger.info("加载 price_kline_tdxhub (start=%s)", start_date)
    df = pd.read_sql_query(
        """SELECT code as stock_code, date, open, high, low, close, volume, amount
           FROM price_kline_tdxhub
           WHERE freq='daily' AND adjust='qfq' AND date >= ?
           ORDER BY stock_code, date""",
        mkt_conn, params=(start_date,),
    )
    logger.info("price_kline %d rows, %d codes", len(df), df['stock_code'].nunique())
    return df


def compute_pillar_b(df: pd.DataFrame) -> pd.DataFrame:
    """价量因子. 输入 price panel DataFrame."""
    df = df.sort_values(['stock_code', 'date']).reset_index(drop=True)
    g = df.groupby('stock_code', sort=False)

    df['ret_1d']  = g['close'].pct_change(1)
    df['ret_5d']  = g['close'].pct_change(5)
    df['ret_20d'] = g['close'].pct_change(20)
    df['ret_60d'] = g['close'].pct_change(60)
    df['vol_z20d'] = (
        df.groupby('stock_code')['volume']
          .transform(lambda s: (s - s.rolling(20).mean()) / s.rolling(20).std())
    )
    for n in [5, 20, 60, 250]:
        df[f'ma_{n}'] = g['close'].transform(lambda s: s.rolling(n, min_periods=max(2, n//5)).mean())
        df[f'ma_ratio_{n}'] = df['close'] / df[f'ma_{n}'] - 1
    df.drop(columns=[f'ma_{n}' for n in [5, 20, 60, 250]], inplace=True)
    return df


def compute_label(df: pd.DataFrame) -> pd.DataFrame:
    """forward_ret_20d = close[t+21]/close[t+1] - 1 (T+1 entry, 20d 持有)"""
    df = df.sort_values(['stock_code', 'date']).copy()
    # entry at t+1 close, exit at t+21 close  (20 trading days later)
    df['close_tp1'] = df.groupby('stock_code', sort=False)['close'].shift(-1)
    df['close_tp21'] = df.groupby('stock_code', sort=False)['close'].shift(-21)
    df['forward_ret_20d'] = df['close_tp21'] / df['close_tp1'] - 1
    df.drop(columns=['close_tp1', 'close_tp21'], inplace=True)
    return df


def load_margin_feature(conn, start_date: str) -> pd.DataFrame:
    logger.info("加载 raw_margin_daily")
    df = pd.read_sql_query(
        """SELECT stock_code, trade_date as date, rz_balance
           FROM raw_margin_daily WHERE trade_date >= ?""",
        conn, params=(start_date.replace('-', ''),),
    )
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'].astype(str)).dt.strftime('%Y-%m-%d')
    df = df.sort_values(['stock_code', 'date'])
    df['rz_chg_5d'] = df.groupby('stock_code', sort=False)['rz_balance'].pct_change(5)
    df.rename(columns={'rz_chg_5d': 'rz_chg_5d_pct'}, inplace=True)
    return df[['stock_code', 'date', 'rz_balance', 'rz_chg_5d_pct']]


def compute_pillar_a(smart_conn, price_panel: pd.DataFrame) -> pd.DataFrame:
    """事件类 rolling count + days_since. 基于 fact_institution_event +
    fact_executive_trade_event + fact_lhb_event + fact_jgdy_event + fact_dzjy_event."""

    def _rolling_count(events_df: pd.DataFrame, price_panel: pd.DataFrame,
                       date_col: str, count_col: str, windows: list[int]) -> pd.DataFrame:
        if events_df.empty:
            for w in windows:
                price_panel[f'{count_col}_{w}d'] = 0
            return price_panel
        events_df = events_df.copy()
        events_df[date_col] = pd.to_datetime(events_df[date_col].astype(str).str.replace('-', '', regex=False),
                                              format='%Y%m%d', errors='coerce').dt.strftime('%Y-%m-%d')
        ev = events_df.groupby(['stock_code', date_col]).size().reset_index(name='n')
        ev.columns = ['stock_code', 'date', 'n']
        # merge onto price panel, fill 0, then rolling
        m = price_panel[['stock_code', 'date']].merge(ev, on=['stock_code', 'date'], how='left').fillna({'n': 0})
        m = m.sort_values(['stock_code', 'date'])
        for w in windows:
            m[f'{count_col}_{w}d'] = m.groupby('stock_code', sort=False)['n'].transform(
                lambda s: s.rolling(w, min_periods=1).sum()
            )
        return m.drop(columns=['n'])

    logger.info("Pillar A: 事件 rolling counts")
    # A.1 institution events (十大/QFII 等)
    inst_ev = pd.read_sql_query(
        "SELECT stock_code, notice_date FROM fact_institution_event WHERE notice_date >= ?",
        smart_conn, params=(price_panel['date'].min().replace('-', ''),),
    )
    m = _rolling_count(inst_ev, price_panel, 'notice_date', 'inst_event_count', [30, 60])
    price_panel = price_panel.merge(m, on=['stock_code', 'date'], how='left')

    # A.2 executive_trade buy events
    exec_ev = pd.read_sql_query(
        """SELECT stock_code, notice_date, total_change_pct_total
           FROM fact_executive_trade_event
           WHERE direction='buy' AND notice_date >= ?""",
        smart_conn, params=(price_panel['date'].min(),),
    )
    exec_all = exec_ev.copy()
    exec_all['date_norm'] = pd.to_datetime(exec_all['notice_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    m_all = _rolling_count(exec_all[['stock_code', 'date_norm']].rename(columns={'date_norm': 'notice_date'}),
                           price_panel[['stock_code', 'date']], 'notice_date', 'exec_buy_count', [90])
    price_panel = price_panel.merge(m_all[['stock_code', 'date', 'exec_buy_count_90d']], on=['stock_code', 'date'], how='left')

    # ≥1% 单独算
    exec_ge1 = exec_ev[exec_ev['total_change_pct_total'] >= 1.0].copy()
    exec_ge1['date_norm'] = pd.to_datetime(exec_ge1['notice_date'], errors='coerce').dt.strftime('%Y-%m-%d')
    m_ge1 = _rolling_count(exec_ge1[['stock_code', 'date_norm']].rename(columns={'date_norm': 'notice_date'}),
                           price_panel[['stock_code', 'date']], 'notice_date', 'exec_buy_ge1_count', [90])
    price_panel = price_panel.merge(m_ge1[['stock_code', 'date', 'exec_buy_ge1_count_90d']], on=['stock_code', 'date'], how='left')

    # A.3 LHB 事件 (只取 is_inst_net_buy=1)
    try:
        lhb_ev = pd.read_sql_query(
            "SELECT stock_code, trade_date FROM fact_lhb_event WHERE is_inst_net_buy=1 AND trade_date >= ?",
            smart_conn, params=(price_panel['date'].min(),),
        )
        m = _rolling_count(lhb_ev, price_panel[['stock_code', 'date']], 'trade_date', 'lhb_inst_buy_count', [30, 60])
        price_panel = price_panel.merge(m, on=['stock_code', 'date'], how='left')
    except Exception as e:
        logger.warning("lhb 合并失败: %s", e)
        price_panel['lhb_inst_buy_count_30d'] = 0
        price_panel['lhb_inst_buy_count_60d'] = 0

    # A.4 jgdy + dzjy (optional, may not exist yet)
    for tbl, dcol, outcol in [('fact_jgdy_event', 'notice_date', 'jgdy_count'),
                               ('fact_dzjy_event', 'trade_date', 'dzjy_count')]:
        try:
            ev = pd.read_sql_query(f"SELECT stock_code, {dcol} FROM {tbl}", smart_conn)
            m = _rolling_count(ev, price_panel[['stock_code', 'date']], dcol, outcol, [60])
            price_panel = price_panel.merge(m, on=['stock_code', 'date'], how='left')
        except Exception as e:
            logger.warning("%s 合并跳过: %s", tbl, e)
            price_panel[f'{outcol}_60d'] = 0

    # A.5 days_since_last
    for ev_sql, col in [
        ("SELECT stock_code, notice_date FROM fact_executive_trade_event WHERE direction='buy'", 'exec_buy'),
        ("SELECT stock_code, trade_date as notice_date FROM fact_lhb_event WHERE is_inst_net_buy=1", 'lhb'),
    ]:
        try:
            ev = pd.read_sql_query(ev_sql, smart_conn)
            if ev.empty:
                price_panel[f'days_since_{col}'] = -1
                continue
            ev['date_norm'] = pd.to_datetime(ev['notice_date'], errors='coerce').dt.strftime('%Y-%m-%d')
            ev = ev[['stock_code', 'date_norm']].sort_values(['stock_code', 'date_norm'])
            # 对每个 (stock_code, date), 找到 <= date 最大的 event date
            ev_sorted = ev.rename(columns={'date_norm': 'event_date'})
            merged = price_panel[['stock_code', 'date']].merge(ev_sorted, on='stock_code')
            merged = merged[merged['event_date'] <= merged['date']]
            latest = merged.groupby(['stock_code', 'date'])['event_date'].max().reset_index()
            latest['days_since'] = (pd.to_datetime(latest['date']) - pd.to_datetime(latest['event_date'])).dt.days
            price_panel = price_panel.merge(latest[['stock_code', 'date', 'days_since']].rename(columns={'days_since': f'days_since_{col}'}),
                                            on=['stock_code', 'date'], how='left')
        except Exception as e:
            logger.warning("days_since %s ERR: %s", col, e)
            price_panel[f'days_since_{col}'] = -1

    return price_panel


def compute_pillar_c(smart_conn, price_panel: pd.DataFrame) -> pd.DataFrame:
    """基本面 季度 forward-fill. 计算 QoQ 变化."""
    logger.info("Pillar C: 基本面 (季度 fw-fill + QoQ)")
    ffq = pd.read_sql_query(
        """SELECT stock_code, report_date,
                  shareholder_count, inst_count, fund_count, qfii_count,
                  yjyg_lower_pct, yjyg_upper_pct, roe, eps_basic
           FROM fact_fundamental_quarterly ORDER BY stock_code, report_date""",
        smart_conn,
    )
    if ffq.empty:
        # 填 null
        for c in ['shareholder_count_qoq', 'inst_count_qoq', 'fund_count_qoq',
                  'qfii_count_qoq', 'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic']:
            price_panel[c] = None
        return price_panel

    # QoQ: 按股排序后, diff/pct_change
    ffq['report_date'] = pd.to_datetime(ffq['report_date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
    ffq = ffq.sort_values(['stock_code', 'report_date'])
    for col in ['shareholder_count', 'inst_count', 'fund_count', 'qfii_count']:
        ffq[f'{col}_qoq'] = ffq.groupby('stock_code', sort=False)[col].pct_change()

    # merge_asof: 每日 panel 取最近 <= date 的 quarter row
    ffq_keep = ffq[['stock_code', 'report_date',
                    'shareholder_count_qoq', 'inst_count_qoq',
                    'fund_count_qoq', 'qfii_count_qoq',
                    'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic']].copy()
    ffq_keep.rename(columns={'report_date': 'date'}, inplace=True)

    # merge_asof 要求数值/日期类型, str 不行 -> 用 Timestamp
    panel_sorted = price_panel.copy()
    panel_sorted['date_dt'] = pd.to_datetime(panel_sorted['date'])
    panel_sorted = panel_sorted.sort_values(['stock_code', 'date_dt'])
    ffq_sorted = ffq_keep.copy()
    ffq_sorted['date_dt'] = pd.to_datetime(ffq_sorted['date'])
    ffq_sorted = ffq_sorted.sort_values(['stock_code', 'date_dt'])
    ffq_sorted = ffq_sorted.drop(columns=['date'])

    qoq_cols = ['shareholder_count_qoq', 'inst_count_qoq', 'fund_count_qoq',
                'qfii_count_qoq', 'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic']
    merged_parts = []
    for code, grp in panel_sorted.groupby('stock_code', sort=False):
        ff_sub = ffq_sorted[ffq_sorted['stock_code'] == code]
        if ff_sub.empty:
            for c in qoq_cols:
                grp[c] = None
            merged_parts.append(grp.drop(columns=['date_dt']))
            continue
        m = pd.merge_asof(
            grp,
            ff_sub.drop(columns=['stock_code']),
            on='date_dt', direction='backward',
        )
        m = m.drop(columns=['date_dt'])
        merged_parts.append(m)
    return pd.concat(merged_parts, ignore_index=True) if merged_parts else price_panel


def compute_regime(mkt_conn) -> pd.DataFrame:
    """HS300 20d/60d 动量 → regime_flag"""
    logger.info("Regime: HS300 动量")
    hs = pd.read_sql_query(
        """SELECT date, close FROM price_kline_tdxhub
           WHERE code='510300' AND freq='daily' AND adjust='qfq' ORDER BY date""",
        mkt_conn,
    )
    if hs.empty:
        # fallback: 用 price_kline
        hs = pd.read_sql_query(
            """SELECT date, close FROM price_kline
               WHERE code='510300' AND freq='daily' AND adjust='qfq' ORDER BY date""",
            mkt_conn,
        )
    hs['hs300_ret_20d'] = hs['close'].pct_change(20)
    hs['hs300_ret_60d'] = hs['close'].pct_change(60)
    def regime(r):
        if pd.isna(r): return 'na'
        if r > 0.03: return 'up'
        if r < -0.03: return 'down'
        return 'flat'
    hs['regime_flag'] = hs['hs300_ret_20d'].apply(regime)
    return hs[['date', 'hs300_ret_20d', 'hs300_ret_60d', 'regime_flag']]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2020-01-01', help='Panel 起始日期')
    parser.add_argument('--limit-codes', type=int, default=0,
                        help='调试: 只处理前 N 只股')
    args = parser.parse_args()

    mkt = get_market_conn()
    smart = get_conn()

    t0 = time.time()
    # 1. Price panel + pillar B
    price = load_price_panel(mkt, args.start)
    if args.limit_codes:
        keep_codes = price['stock_code'].unique()[:args.limit_codes]
        price = price[price['stock_code'].isin(keep_codes)].copy()
        logger.info("debug: 限制 %d 只", args.limit_codes)

    panel = compute_pillar_b(price)
    panel = compute_label(panel)

    # margin
    margin = load_margin_feature(smart, args.start)
    panel = panel.merge(margin, on=['stock_code', 'date'], how='left')

    # 2. Pillar A events
    panel = compute_pillar_a(smart, panel)

    # 3. Pillar C 基本面
    panel = compute_pillar_c(smart, panel)

    # 4. Regime (merge on date, broadcast to all stocks)
    regime_df = compute_regime(mkt)
    panel = panel.merge(regime_df, on='date', how='left')

    # 5. 写入
    logger.info("Panel shape %s, 准备写入", panel.shape)
    smart.executescript(PANEL_DDL)
    keep = [c for c in [
        'stock_code', 'date', 'close',
        'ret_1d', 'ret_5d', 'ret_20d', 'ret_60d',
        'vol_z20d', 'ma_ratio_5', 'ma_ratio_20', 'ma_ratio_60', 'ma_ratio_250',
        'rz_balance', 'rz_chg_5d_pct',
        'inst_event_count_30d', 'inst_event_count_60d',
        'exec_buy_count_90d', 'exec_buy_ge1_count_90d',
        'lhb_inst_buy_count_30d', 'lhb_inst_buy_count_60d',
        'jgdy_count_60d', 'dzjy_count_60d',
        'days_since_exec_buy', 'days_since_lhb',
        'shareholder_count_qoq', 'inst_count_qoq',
        'fund_count_qoq', 'qfii_count_qoq',
        'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic',
        'hs300_ret_20d', 'hs300_ret_60d', 'regime_flag',
        'forward_ret_20d',
    ] if c in panel.columns]
    panel['built_at'] = datetime.utcnow().isoformat()
    keep.append('built_at')
    panel[keep].to_sql('fact_feature_panel', smart, if_exists='append',
                       index=False, method='multi', chunksize=1000)
    smart.commit()

    # 6. 验证
    row = smart.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT date) FROM fact_feature_panel").fetchone()
    logger.info("=" * 50)
    logger.info("fact_feature_panel 写入: rows=%d  codes=%d  dates=%d  耗时 %.1f 分钟",
                row[0], row[1], row[2], (time.time() - t0) / 60)

    # label 覆盖率
    row2 = smart.execute("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN forward_ret_20d IS NOT NULL THEN 1 ELSE 0 END) with_label
        FROM fact_feature_panel
    """).fetchone()
    logger.info("forward_ret_20d 非空 %d/%d (%.1f%%)", row2[1], row2[0], 100*row2[1]/max(1,row2[0]))

    mkt.close(); smart.close()


if __name__ == "__main__":
    main()
