#!/usr/bin/env python3
"""Phase 6 DuckDB 版: 特征面板构建

架构分工
- DuckDB SQL: 所有 panel 级聚合 / 窗口函数 / ASOF JOIN (替代 pandas groupby rolling)
- pandas: 最终 DataFrame -> SQLite 写入 (to_sql)
- qlib Alpha158: 由独立脚本 build_alpha158_features.py 产出, 本脚本以 LEFT JOIN 挂上

Pillar 分工同原版 (A/B/C + regime). DuckDB 把 27 min pandas 压到 <5 min.
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
from services.db import get_conn
from services.analytics import get_duck

logger = logging.getLogger("feature_panel_duck")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


PANEL_DDL = """
DROP TABLE IF EXISTS fact_feature_panel;
CREATE TABLE fact_feature_panel (
    stock_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    close REAL,
    -- Pillar B 价量
    ret_1d REAL, ret_5d REAL, ret_20d REAL, ret_60d REAL,
    vol_z20d REAL, ma_ratio_5 REAL, ma_ratio_20 REAL, ma_ratio_60 REAL, ma_ratio_250 REAL,
    rz_balance REAL, rz_chg_5d_pct REAL,
    -- Alpha158 inspired
    kmid REAL, klen REAL, kup REAL, klow REAL, ksft REAL,
    vol_ratio_5_20 REAL, vol_std_5d REAL, vol_std_20d REAL,
    range_pos_20 REAL, range_pos_60 REAL,
    momentum_diff REAL, amount_chg_5d REAL,
    -- Pillar A 事件
    inst_event_count_30d INTEGER, inst_event_count_60d INTEGER,
    exec_buy_count_90d INTEGER, exec_buy_ge1_count_90d INTEGER,
    lhb_inst_buy_count_30d INTEGER, lhb_inst_buy_count_60d INTEGER,
    jgdy_count_60d INTEGER,
    dzjy_count_60d INTEGER,
    days_since_exec_buy INTEGER, days_since_lhb INTEGER,
    -- Pillar C 基本面
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
CREATE INDEX IF NOT EXISTS idx_fp_date_label ON fact_feature_panel(date, forward_ret_20d);
CREATE INDEX IF NOT EXISTS idx_fp_label ON fact_feature_panel(forward_ret_20d) WHERE forward_ret_20d IS NOT NULL;
"""


def build_panel(start_date: str) -> pd.DataFrame:
    duck = get_duck()
    t0 = time.time()

    logger.info("Step 1: Pillar B 价量 + Alpha158-inspired 特征 (DuckDB window)")
    # Step 1a: 先算每日 pct_change (供 vol_std 使用)
    pillar_b = duck.execute(f"""
        WITH px AS (
            SELECT code as stock_code, date,
                   open, high, low, close, volume, amount,
                   (close / NULLIF(LAG(close, 1) OVER (PARTITION BY code ORDER BY date), 0) - 1) AS close_ret_1d
            FROM market.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq' AND date >= '{start_date}'
        )
        SELECT
            stock_code, date, close,
            close_ret_1d AS ret_1d,
            (close / NULLIF(LAG(close, 5) OVER w, 0) - 1) AS ret_5d,
            (close / NULLIF(LAG(close, 20) OVER w, 0) - 1) AS ret_20d,
            (close / NULLIF(LAG(close, 60) OVER w, 0) - 1) AS ret_60d,
            (volume - AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING))
                / NULLIF(STDDEV_SAMP(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0)
                AS vol_z20d,
            (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING), 0) - 1) AS ma_ratio_5,
            (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0) - 1) AS ma_ratio_20,
            (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING), 0) - 1) AS ma_ratio_60,
            (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 249 PRECEDING), 0) - 1) AS ma_ratio_250,
            ((close - open) / NULLIF(open, 0)) AS kmid,
            ((high - low) / NULLIF(open, 0)) AS klen,
            ((high - GREATEST(open, close)) / NULLIF(open, 0)) AS kup,
            ((LEAST(open, close) - low) / NULLIF(open, 0)) AS klow,
            ((2 * close - high - low) / NULLIF(open, 0)) AS ksft,
            (AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING)
             / NULLIF(AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0)) AS vol_ratio_5_20,
            STDDEV_SAMP(close_ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING) AS vol_std_5d,
            STDDEV_SAMP(close_ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING) AS vol_std_20d,
            (close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING))
                / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING)
                         - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0) AS range_pos_20,
            (close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING))
                / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING)
                         - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING), 0) AS range_pos_60,
            NULL AS momentum_diff,
            (amount / NULLIF(LAG(amount, 5) OVER w, 0) - 1) AS amount_chg_5d
        FROM px
        WINDOW w AS (PARTITION BY stock_code ORDER BY date)
    """).df()
    pillar_b['momentum_diff'] = pillar_b['ret_5d'] - pillar_b['ret_20d']
    logger.info("Pillar B done: %d rows, %.1fs", len(pillar_b), time.time() - t0)

    t1 = time.time()
    logger.info("Step 2: Pillar B 两融 (ASOF JOIN)")
    # 两融 merge
    pillar_b['_date_dt'] = pd.to_datetime(pillar_b['date'])
    duck.register('panel_b', pillar_b)
    margin_joined = duck.execute("""
        WITH margin AS (
            SELECT stock_code, trade_date, rz_balance,
                   (rz_balance / NULLIF(LAG(rz_balance, 5) OVER (PARTITION BY stock_code ORDER BY trade_date), 0) - 1) AS rz_chg_5d_pct
            FROM smart.raw_margin_daily
        )
        SELECT p.*,
               m.rz_balance, m.rz_chg_5d_pct
        FROM panel_b p
        LEFT JOIN margin m
          ON p.stock_code = m.stock_code
         AND p.date = SUBSTR(m.trade_date, 1, 4) || '-' || SUBSTR(m.trade_date, 5, 2) || '-' || SUBSTR(m.trade_date, 7, 2)
    """).df()
    logger.info("Pillar B margin merge done: %d rows, %.1fs", len(margin_joined), time.time() - t1)
    pillar_b = margin_joined

    # 添加 label forward_ret_20d = close[t+21] / close[t+1] - 1
    t2 = time.time()
    logger.info("Step 3: forward_ret_20d label")
    duck.register('panel_b2', pillar_b)
    pillar_b = duck.execute("""
        SELECT *,
               (LEAD(close, 21) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_20d
        FROM panel_b2
        WINDOW w AS (PARTITION BY stock_code ORDER BY date)
    """).df()
    logger.info("label done: %.1fs", time.time() - t2)

    # Pillar A 事件 rolling counts
    t3 = time.time()
    logger.info("Step 4: Pillar A 事件 rolling counts (DuckDB SQL)")

    def _rolling_event_count(evt_sql: str, count_col: str, windows: list[int]) -> None:
        """对每个 event 源表生成 rolling count 列, 合并到 pillar_b."""
        nonlocal pillar_b
        # 先注册 pillar 以便 join
        duck.register('current_panel', pillar_b)
        # 事件按 (stock_code, event_date) group COUNT → panel 做 left join + window sum
        query = f"""
            WITH ev_raw AS ({evt_sql}),
            ev_daily AS (
                SELECT stock_code, event_date AS date, COUNT(*)::INT AS n
                FROM ev_raw GROUP BY stock_code, event_date
            ),
            panel_ev AS (
                SELECT p.stock_code, p.date,
                       COALESCE(e.n, 0) AS n
                FROM current_panel p
                LEFT JOIN ev_daily e ON e.stock_code = p.stock_code AND e.date = p.date
            )
            SELECT stock_code, date,
                   {', '.join(
                       f"SUM(n) OVER (PARTITION BY stock_code ORDER BY date ROWS {w-1} PRECEDING) AS {count_col}_{w}d"
                       for w in windows
                   )}
            FROM panel_ev
        """
        ev_df = duck.execute(query).df()
        for w in windows:
            col = f"{count_col}_{w}d"
            pillar_b = pillar_b.merge(
                ev_df[['stock_code', 'date', col]],
                on=['stock_code', 'date'], how='left',
            )
            pillar_b[col] = pillar_b[col].fillna(0).astype('int32')

    # institution event
    _rolling_event_count(
        "SELECT stock_code, "
        "SUBSTR(notice_date,1,4) || '-' || SUBSTR(notice_date,5,2) || '-' || SUBSTR(notice_date,7,2) AS event_date "
        "FROM smart.fact_institution_event",
        "inst_event_count", [30, 60],
    )
    # executive buy all
    _rolling_event_count(
        "SELECT stock_code, notice_date AS event_date "
        "FROM smart.fact_executive_trade_event WHERE direction='buy'",
        "exec_buy_count", [90],
    )
    # executive buy ≥1%
    _rolling_event_count(
        "SELECT stock_code, notice_date AS event_date "
        "FROM smart.fact_executive_trade_event "
        "WHERE direction='buy' AND total_change_pct_total >= 1.0",
        "exec_buy_ge1_count", [90],
    )
    # LHB 机构买
    _rolling_event_count(
        "SELECT stock_code, trade_date AS event_date "
        "FROM smart.fact_lhb_event WHERE is_inst_net_buy=1",
        "lhb_inst_buy_count", [30, 60],
    )
    # 机构调研 (jgdy)
    try:
        _rolling_event_count(
            "SELECT stock_code, "
            "SUBSTR(notice_date,1,4) || '-' || SUBSTR(notice_date,5,2) || '-' || SUBSTR(notice_date,7,2) AS event_date "
            "FROM smart.fact_jgdy_event",
            "jgdy_count", [60],
        )
    except Exception as e:
        logger.warning("jgdy rolling skip: %s", e)
        pillar_b['jgdy_count_60d'] = 0
    # 大宗交易 (dzjy)
    try:
        _rolling_event_count(
            "SELECT stock_code, "
            "SUBSTR(trade_date,1,4) || '-' || SUBSTR(trade_date,5,2) || '-' || SUBSTR(trade_date,7,2) AS event_date "
            "FROM smart.fact_dzjy_event",
            "dzjy_count", [60],
        )
    except Exception as e:
        logger.warning("dzjy rolling skip: %s", e)
        pillar_b['dzjy_count_60d'] = 0

    logger.info("Pillar A rolling counts done: %.1fs", time.time() - t3)

    # days_since
    t4 = time.time()
    logger.info("Step 5: days_since 特征")
    duck.register('current_panel', pillar_b)
    # 高管增持最近日期
    for ev_sql, col in [
        ("SELECT stock_code, notice_date AS event_date "
         "FROM smart.fact_executive_trade_event WHERE direction='buy'", "exec_buy"),
        ("SELECT stock_code, trade_date AS event_date "
         "FROM smart.fact_lhb_event WHERE is_inst_net_buy=1", "lhb"),
    ]:
        try:
            ds = duck.execute(f"""
                WITH ev AS ({ev_sql}),
                panel_ev AS (
                    SELECT p.stock_code, p.date, p.date::DATE AS date_dt,
                           MAX(CASE WHEN e.event_date::DATE <= p.date::DATE THEN e.event_date::DATE END) AS last_ev
                    FROM current_panel p
                    LEFT JOIN ev e ON e.stock_code = p.stock_code
                    GROUP BY p.stock_code, p.date
                )
                SELECT stock_code, date,
                       CASE WHEN last_ev IS NULL THEN -1
                            ELSE (date_dt - last_ev)::INT END AS days_since_{col}
                FROM panel_ev
            """).df()
            pillar_b = pillar_b.merge(ds, on=['stock_code', 'date'], how='left')
            pillar_b[f'days_since_{col}'] = pillar_b[f'days_since_{col}'].fillna(-1).astype('int32')
        except Exception as e:
            logger.warning("days_since %s ERR: %s", col, e)
            pillar_b[f'days_since_{col}'] = -1
    logger.info("days_since done: %.1fs", time.time() - t4)

    # Pillar C 基本面 (ASOF JOIN 季度 forward-fill)
    t5 = time.time()
    logger.info("Step 6: Pillar C 基本面 (ASOF JOIN)")
    duck.register('current_panel', pillar_b)
    pillar_c_joined = duck.execute("""
        WITH ffq AS (
            SELECT stock_code,
                   SUBSTR(report_date,1,4) || '-' || SUBSTR(report_date,5,2) || '-' || SUBSTR(report_date,7,2) AS date,
                   shareholder_count, inst_count, fund_count, qfii_count,
                   yjyg_lower_pct, yjyg_upper_pct, roe, eps_basic,
                   (shareholder_count / NULLIF(LAG(shareholder_count) OVER w, 0) - 1) AS shareholder_count_qoq,
                   (inst_count / NULLIF(LAG(inst_count) OVER w, 0) - 1) AS inst_count_qoq,
                   (fund_count / NULLIF(LAG(fund_count) OVER w, 0) - 1) AS fund_count_qoq,
                   (qfii_count / NULLIF(LAG(qfii_count) OVER w, 0) - 1) AS qfii_count_qoq
            FROM smart.fact_fundamental_quarterly
            WINDOW w AS (PARTITION BY stock_code ORDER BY report_date)
        )
        SELECT p.*,
               f.shareholder_count_qoq, f.inst_count_qoq, f.fund_count_qoq, f.qfii_count_qoq,
               f.yjyg_lower_pct, f.yjyg_upper_pct, f.roe, f.eps_basic
        FROM current_panel p
        ASOF LEFT JOIN ffq f
          ON p.stock_code = f.stock_code AND p.date >= f.date
    """).df()
    logger.info("Pillar C done: %.1fs", time.time() - t5)
    pillar_b = pillar_c_joined

    # Regime
    t6 = time.time()
    logger.info("Step 7: Regime 市场状态")
    regime_df = duck.execute("""
        SELECT date,
               (close / NULLIF(LAG(close, 20) OVER (ORDER BY date), 0) - 1) AS hs300_ret_20d,
               (close / NULLIF(LAG(close, 60) OVER (ORDER BY date), 0) - 1) AS hs300_ret_60d
        FROM market.price_kline_tdxhub
        WHERE code='510300' AND freq='daily' AND adjust='qfq'
        ORDER BY date
    """).df()
    def _regime(r):
        if pd.isna(r): return 'na'
        if r > 0.03: return 'up'
        if r < -0.03: return 'down'
        return 'flat'
    regime_df['regime_flag'] = regime_df['hs300_ret_20d'].apply(_regime)
    pillar_b = pillar_b.merge(regime_df, on='date', how='left')
    logger.info("Regime done: %.1fs", time.time() - t6)

    logger.info("TOTAL build_panel: %d rows, %.1f min", len(pillar_b), (time.time() - t0) / 60)
    # 列去重
    pillar_b = pillar_b.loc[:, ~pillar_b.columns.duplicated()]
    if '_date_dt' in pillar_b.columns:
        pillar_b = pillar_b.drop(columns=['_date_dt'])
    return pillar_b


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2020-01-01')
    args = parser.parse_args()

    panel = build_panel(args.start)

    logger.info("Panel shape %s, 准备写入 SQLite (DuckDB INSERT FROM SELECT)", panel.shape)
    panel['built_at'] = datetime.utcnow().isoformat()

    keep_cols = [
        'stock_code', 'date', 'close',
        'ret_1d', 'ret_5d', 'ret_20d', 'ret_60d',
        'vol_z20d', 'ma_ratio_5', 'ma_ratio_20', 'ma_ratio_60', 'ma_ratio_250',
        'rz_balance', 'rz_chg_5d_pct',
        'kmid', 'klen', 'kup', 'klow', 'ksft',
        'vol_ratio_5_20', 'vol_std_5d', 'vol_std_20d',
        'range_pos_20', 'range_pos_60',
        'momentum_diff', 'amount_chg_5d',
        'inst_event_count_30d', 'inst_event_count_60d',
        'exec_buy_count_90d', 'exec_buy_ge1_count_90d',
        'lhb_inst_buy_count_30d', 'lhb_inst_buy_count_60d',
        'jgdy_count_60d', 'dzjy_count_60d',
        'days_since_exec_buy', 'days_since_lhb',
        'shareholder_count_qoq', 'inst_count_qoq',
        'fund_count_qoq', 'qfii_count_qoq',
        'yjyg_lower_pct', 'yjyg_upper_pct', 'roe', 'eps_basic',
        'hs300_ret_20d', 'hs300_ret_60d', 'regime_flag',
        'forward_ret_20d', 'built_at',
    ]
    keep = [c for c in keep_cols if c in panel.columns]
    panel_out = panel[keep].reset_index(drop=True)

    # Step 7: 用 DuckDB 直接写 SQLite (INSERT FROM SELECT)
    # 先通过 pandas 建表结构 (DDL), 再用 DuckDB 批量灌数据
    t_w0 = time.time()
    smart = get_conn()
    smart.executescript(PANEL_DDL)
    smart.execute("PRAGMA synchronous=NORMAL")
    smart.execute("PRAGMA cache_size=-524288")  # 512 MB
    smart.close()

    duck = get_duck()
    duck.register('panel_out', panel_out)
    logger.info("DuckDB INSERT INTO smart.fact_feature_panel SELECT * FROM panel_out ...")
    duck.execute(f"INSERT INTO smart.fact_feature_panel ({', '.join(keep)}) SELECT * FROM panel_out")
    logger.info("写入完成: %.1fs", time.time() - t_w0)

    # 验证写入
    smart = get_conn()
    row = smart.execute("""
        SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT date),
               SUM(CASE WHEN forward_ret_20d IS NOT NULL THEN 1 ELSE 0 END)
        FROM fact_feature_panel
    """).fetchone()
    logger.info("fact_feature_panel: rows=%d codes=%d dates=%d label_non_null=%d",
                row[0], row[1], row[2], row[3])
    smart.close()


if __name__ == "__main__":
    main()
