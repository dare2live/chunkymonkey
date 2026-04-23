#!/usr/bin/env python3
"""构建事件级特征矩阵 fact_event_features（§29.5 Layer C W3 首版）。

对最近 N 天（默认 60）的 new_entry/increase 事件，拼接：
  F1 institution × L2 擅长度评分（v_institution_l2_score）
  F3 forecast/研报预期（fact_stock_forecast_features 最新快照）
  F4 调研热度（mart_stock_survey_activity 最新）
  F5 股票阶段特征（fact_stock_stage_features 最新快照）
  F6 两融原始（raw_margin_daily 最新）+ 20d 净融资
  F7 机构个体业绩（mart_institution_profile）
  event F8：同期股票共振（近 90d 该股 stable 机构数）

Label：
  label_gain_30d / label_max_drawdown_30d（来自 fact_institution_event 已算字段）
  —— 首版用 30d 作 proxy（字段表没有 20d）；W4 Qlib 训练时可扩展

首版简化（W3 POC）：
  - 股票阶段/质量/survey 用"最新快照"而非"事件日最近快照"（接受 lookahead bias，因为
    数据快照密度不足 9-5 个快照日，回溯到事件日会大量 miss；W4 扩展时再严格化）
  - 不做 entry_lag、持仓参数特征（这些是策略参数不是事件特征）

用法：
  python -m backend.scripts.build_event_features --days 60        # 默认：最近 60 天
  python -m backend.scripts.build_event_features --days 180       # 扩到 180 天（W4 起点）
  python -m backend.scripts.build_event_features --days 60 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import get_conn

logger = logging.getLogger("build_event_features")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_event_features (
    institution_id           TEXT NOT NULL,
    stock_code               TEXT NOT NULL,
    notice_date              TEXT NOT NULL,
    event_type               TEXT,
    report_date              TEXT,
    tdx_l1_name              TEXT,
    tdx_l2_name              TEXT,

    -- F6 事件属性
    premium_pct              REAL,
    premium_bucket           TEXT,
    hold_amount              REAL,
    change_amount            REAL,
    report_to_notice_lag_days INTEGER,

    -- F1 机构 × L2 擅长度（Layer B）
    inst_l2_stable_score     REAL,
    inst_l2_verdict          TEXT,
    inst_l2_train_n          INTEGER,
    inst_l2_ho_n             INTEGER,
    inst_l2_ho_sharpe        REAL,

    -- F7 机构个体业绩（mart_institution_profile）
    inst_quality_score       REAL,
    inst_followability_score REAL,
    inst_buy_win_rate_60d    REAL,
    inst_buy_avg_gain_60d    REAL,

    -- F5 股票阶段特征
    stage_dist_ma250_pct     REAL,
    stage_return_3m          REAL,
    stage_return_6m          REAL,
    stage_above_ma250        INTEGER,
    stage_volatility_20d     REAL,

    -- F6 两融
    margin_rz_balance        REAL,
    margin_rz_balance_percentile REAL,

    -- F4 调研
    survey_inst_count_60d    INTEGER,
    survey_count_60d         INTEGER,

    -- F3 研报预期
    forecast_score_v1        REAL,
    forecast_20d_score       REAL,
    industry_qlib_percentile REAL,

    -- F8 共振（同股票近 90 天所有其他机构 L2 stable 数）
    resonance_n_stable_insts INTEGER,

    -- Label
    label_gain_30d           REAL,
    label_max_drawdown_30d   REAL,
    label_gain_60d           REAL,
    label_max_drawdown_60d   REAL,

    computed_at              TEXT,
    PRIMARY KEY (institution_id, stock_code, notice_date, report_date)
);
CREATE INDEX IF NOT EXISTS idx_fef_notice ON fact_event_features(notice_date);
CREATE INDEX IF NOT EXISTS idx_fef_stock ON fact_event_features(stock_code);
CREATE INDEX IF NOT EXISTS idx_fef_inst ON fact_event_features(institution_id);
"""



def build(days: int, dry_run: bool = False) -> pd.DataFrame:
    conn = get_conn()
    try:
        conn.executescript(TABLE_DDL)

        if days <= 0:
            cutoff = "00000000"
            logger.info("构建全量事件特征矩阵（notice_date 无下限）")
        else:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            logger.info("构建最近 %d 天（notice_date >= %s）事件特征矩阵", days, cutoff)

        # 主 SQL：一次性 left join 所有 feature 源
        sql = """
WITH base AS (
    SELECT fe.institution_id, fe.stock_code, fe.notice_date, fe.event_type, fe.report_date,
           fe.premium_pct, fe.premium_bucket, fe.hold_amount, fe.change_amount,
           CAST(julianday(substr(fe.notice_date,1,4)||'-'||substr(fe.notice_date,5,2)||'-'||substr(fe.notice_date,7,2))
             - julianday(substr(fe.report_date,1,4)||'-'||substr(fe.report_date,5,2)||'-'||substr(fe.report_date,7,2)) AS INTEGER) AS report_to_notice_lag_days,
           fe.gain_30d label_gain_30d, fe.max_drawdown_30d label_max_drawdown_30d,
           fe.gain_60d label_gain_60d, fe.max_drawdown_60d label_max_drawdown_60d
    FROM fact_institution_event fe
    WHERE fe.event_type IN ('new_entry','increase')
      AND fe.notice_date >= ?
      AND fe.notice_date IS NOT NULL AND fe.notice_date != ''
),
stage_latest AS (
    SELECT stock_code, dist_ma250_pct, return_3m, return_6m, above_ma250, volatility_20d,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY snapshot_date DESC) rn
    FROM fact_stock_stage_features
),
forecast_latest AS (
    SELECT stock_code, forecast_score_v1, forecast_20d_score, industry_qlib_percentile,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY snapshot_date DESC) rn
    FROM fact_stock_forecast_features
),
survey_latest AS (
    SELECT stock_code, inst_count_60d, survey_count_60d,
           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY as_of_date DESC) rn
    FROM mart_stock_survey_activity
),
margin_latest_date AS (
    SELECT MAX(trade_date) latest FROM raw_margin_daily
),
margin_latest AS (
    SELECT stock_code, rz_balance FROM raw_margin_daily WHERE trade_date = (SELECT latest FROM margin_latest_date)
),
margin_pct AS (
    SELECT stock_code, rz_balance,
           (SELECT COUNT(*) FROM margin_latest WHERE rz_balance <= ml.rz_balance AND rz_balance IS NOT NULL) * 100.0
             / NULLIF((SELECT COUNT(*) FROM margin_latest WHERE rz_balance IS NOT NULL), 0) percentile
    FROM margin_latest ml
),
ind_l2 AS (
    SELECT stock_code, tdx_l1_name, tdx_l2_name FROM dim_stock_tdx_industry
),
-- F8 resonance: 该股同期（notice_date 前后 90d）stable 机构数
resonance_agg AS (
    SELECT b.institution_id, b.stock_code, b.notice_date,
        COUNT(DISTINCT CASE WHEN v.verdict='stable' THEN fe2.institution_id END) n_stable_insts
    FROM base b
    LEFT JOIN fact_institution_event fe2 ON fe2.stock_code = b.stock_code
         AND fe2.event_type IN ('new_entry','increase')
         AND fe2.institution_id != b.institution_id
         AND ABS(
           julianday(substr(fe2.notice_date,1,4)||'-'||substr(fe2.notice_date,5,2)||'-'||substr(fe2.notice_date,7,2))
           - julianday(substr(b.notice_date,1,4)||'-'||substr(b.notice_date,5,2)||'-'||substr(b.notice_date,7,2))
         ) <= 90
    LEFT JOIN ind_l2 i ON i.stock_code = b.stock_code
    LEFT JOIN v_institution_l2_score v ON v.institution_id = fe2.institution_id AND v.l2_name = i.tdx_l2_name
    GROUP BY b.institution_id, b.stock_code, b.notice_date
)
SELECT
    b.institution_id, b.stock_code, b.notice_date, b.event_type, b.report_date,
    i.tdx_l1_name, i.tdx_l2_name,
    b.premium_pct, b.premium_bucket, b.hold_amount, b.change_amount, b.report_to_notice_lag_days,
    v.stable_score inst_l2_stable_score,
    v.verdict     inst_l2_verdict,
    v.train_n     inst_l2_train_n,
    v.ho_n        inst_l2_ho_n,
    v.ho_sharpe   inst_l2_ho_sharpe,
    mp.quality_score inst_quality_score,
    mp.followability_score inst_followability_score,
    mp.buy_win_rate_60d inst_buy_win_rate_60d,
    mp.buy_avg_gain_60d inst_buy_avg_gain_60d,
    s.dist_ma250_pct   stage_dist_ma250_pct,
    s.return_3m        stage_return_3m,
    s.return_6m        stage_return_6m,
    s.above_ma250      stage_above_ma250,
    s.volatility_20d   stage_volatility_20d,
    mpct.rz_balance    margin_rz_balance,
    mpct.percentile    margin_rz_balance_percentile,
    sv.inst_count_60d  survey_inst_count_60d,
    sv.survey_count_60d survey_count_60d,
    fc.forecast_score_v1 forecast_score_v1,
    fc.forecast_20d_score forecast_20d_score,
    fc.industry_qlib_percentile industry_qlib_percentile,
    ra.n_stable_insts resonance_n_stable_insts,
    b.label_gain_30d, b.label_max_drawdown_30d,
    b.label_gain_60d, b.label_max_drawdown_60d
FROM base b
LEFT JOIN ind_l2 i ON i.stock_code = b.stock_code
LEFT JOIN v_institution_l2_score v ON v.institution_id = b.institution_id AND v.l2_name = i.tdx_l2_name
LEFT JOIN mart_institution_profile mp ON mp.institution_id = b.institution_id
LEFT JOIN stage_latest s ON s.stock_code = b.stock_code AND s.rn = 1
LEFT JOIN forecast_latest fc ON fc.stock_code = b.stock_code AND fc.rn = 1
LEFT JOIN survey_latest sv ON sv.stock_code = b.stock_code AND sv.rn = 1
LEFT JOIN margin_pct mpct ON mpct.stock_code = b.stock_code
LEFT JOIN resonance_agg ra ON ra.institution_id = b.institution_id AND ra.stock_code = b.stock_code AND ra.notice_date = b.notice_date
"""
        df = pd.read_sql_query(sql, conn, params=(cutoff,))
        logger.info("查询返回 %d 条事件", len(df))
        if df.empty:
            return df

        df["computed_at"] = datetime.now().isoformat()

        if not dry_run:
            conn.execute("DELETE FROM fact_event_features WHERE notice_date >= ?", (cutoff,))
            # 用 INSERT OR REPLACE 容忍主键重复（防未预见的 dup）
            cols = list(df.columns)
            placeholders = ",".join("?" for _ in cols)
            sql = f"INSERT OR REPLACE INTO fact_event_features ({','.join(cols)}) VALUES ({placeholders})"
            records = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False, name=None)]
            conn.executemany(sql, records)
            conn.commit()
            logger.info("已写入 fact_event_features：%d 行（删除并重建 notice_date >= %s）", len(df), cutoff)
        return df
    finally:
        conn.close()


def report_coverage(df: pd.DataFrame):
    """按列计算非空率；打印 <50% 填充率的列（可能有问题）"""
    total = len(df)
    if total == 0:
        logger.warning("空 DataFrame，跳过覆盖率报告")
        return
    logger.info("=== 列非空率（%d 行样本）===", total)
    cov = {}
    for c in df.columns:
        nn = df[c].notna().sum()
        pct = round(nn * 100.0 / total, 1)
        cov[c] = pct
    low = [(c, p) for c, p in cov.items() if p < 70 and c not in ("computed_at",)]
    high = [(c, p) for c, p in cov.items() if p >= 70]
    logger.info("高覆盖（≥70%%）%d 列，低覆盖（<70%%）%d 列", len(high), len(low))
    for c, p in sorted(low, key=lambda x: x[1]):
        logger.info("  低覆盖: %s = %.1f%%", c, p)
    # 特征族汇总
    families = {
        "F1 inst_l2": [c for c in df.columns if c.startswith("inst_l2_")],
        "F7 inst_profile": [c for c in df.columns if c.startswith("inst_") and not c.startswith("inst_l2_")],
        "F5 stage": [c for c in df.columns if c.startswith("stage_")],
        "F6 margin": [c for c in df.columns if c.startswith("margin_")],
        "F4 survey": [c for c in df.columns if c.startswith("survey_")],
        "F3 forecast": [c for c in df.columns if c.startswith("forecast_") or c == "industry_qlib_percentile"],
        "F8 resonance": [c for c in df.columns if c.startswith("resonance_")],
        "label": [c for c in df.columns if c.startswith("label_")],
    }
    logger.info("=== 按族覆盖率 ===")
    for fam, cols in families.items():
        if not cols:
            continue
        avg = sum(cov[c] for c in cols) / len(cols)
        logger.info("  %s (%d 列): 平均 %.1f%%", fam, len(cols), avg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=0,
                        help="最近 N 天事件；0=全量（默认，§29.5 训练集构造推荐）")
    parser.add_argument("--dry-run", action="store_true", help="不写入数据库")
    args = parser.parse_args()

    df = build(days=args.days, dry_run=args.dry_run)
    report_coverage(df)


if __name__ == "__main__":
    main()
