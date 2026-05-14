"""P0a feature × label JOIN v3 — Codex 7-day plan Day 2-3 (PLAN_V3 v3.2 P0a extension).

v2 (84 features) → v3 (102 features) 加 18 列:
- Day 2 (这版 CTE 已实现): 调研热度 4 + 估值 z-score 4 + 板块 momentum 5 = 13 列
- Day 3 (本文件已加占位 CTE + 列, 待实测 NULL 填充率后激活): 机构路径 A 5 列 + industry_pit_confidence 1

PIT 严格 (Rule 7 + Codex Q3):
- **survey**: mart_stock_survey_features.as_of_date <= signal_date ASOF (PIT, calendar-gated)
- **valuation_z**: fact_financial_pit_daily rolling 1Y z-score (PIT-safe)
  替代 raw_aif10_valuation_quantile (latest-snapshot only, 无时间字段, 历史回测 leakage)
- **sector**: mart_stock_industry_pit ASOF → fact_sector_momentum_daily date <= signal_date
- **industry_path_A** (Codex Q3 SQL Day 3): mart_stock_industry_pit × fact_top10_holder_period.effective_date <= signal_date

Codex review (a8c34359a) 反馈修复:
- C1+M4: COALESCE(inst_quality, 0) 偏 wavg → 改 WHERE inst_quality IS NOT NULL + 加 inst_match_ratio
- M1: industry fallback 历史 leakage → 加 industry_pit_confidence 字段下游可 filter
- M2: top_inst quantile 全局 mix future → 改 per-signal_date quantile
- M3+Mi1: 实际 102 features (alpha158 64 + ...), 240 PRECEDING = 241 行 → 改 239/59 + 文档

⚠ critical PIT 缺口 (v3 已知, TODO v3.5):
- mart_institution_profile.win_rate_60d 无 as_of_date → 历史 signal_date 用 latest quality 是 leakage
- 修法待办: 入库 mart_institution_profile_pit (daily snapshot) → ASOF JOIN as_of_date<=signal_date
- 当前 v3 接受这个 leakage, 给 inst_path_a 列加 _NOT_PIT 注释; 下游 P0b 训练前 audit gate raise warning

入库: mart_p0a_feature_label_panel_v3 (新表; v2 保留兼容回溯)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Iterable

from services.duck_adapter import connect as duck_connect

log = logging.getLogger("labels.feature_join_v3")

FEATURE_PANEL_VERSION_V3 = "p0a_v3"


FEATURE_PANEL_DDL_V3 = """
CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v3 (
    stock_code TEXT NOT NULL,
    signal_date DATE NOT NULL,
    -- Label fields (from mart_p0a_label_panel)
    entry_date DATE,
    unable_at_entry BOOLEAN,
    fwd_cost_after_5d  DOUBLE,
    fwd_cost_after_10d DOUBLE,
    fwd_cost_after_20d DOUBLE,
    -- Alpha158 features (65 cols)
    a158_kmid  DOUBLE, a158_klen DOUBLE, a158_kmid2 DOUBLE,
    a158_kup   DOUBLE, a158_kup2 DOUBLE, a158_klow  DOUBLE, a158_klow2 DOUBLE,
    a158_ksft  DOUBLE, a158_ksft2 DOUBLE,
    a158_roc5  DOUBLE, a158_ma5  DOUBLE, a158_std5  DOUBLE,
    a158_max5  DOUBLE, a158_min5 DOUBLE, a158_rsv5  DOUBLE,
    a158_qtl5  DOUBLE, a158_cntp5 DOUBLE, a158_sump5 DOUBLE,
    a158_vma5  DOUBLE, a158_vstd5 DOUBLE,
    a158_roc10 DOUBLE, a158_ma10 DOUBLE, a158_std10 DOUBLE,
    a158_max10 DOUBLE, a158_min10 DOUBLE, a158_rsv10 DOUBLE,
    a158_qtl10 DOUBLE, a158_cntp10 DOUBLE, a158_sump10 DOUBLE,
    a158_vma10 DOUBLE, a158_vstd10 DOUBLE,
    a158_roc20 DOUBLE, a158_ma20 DOUBLE, a158_std20 DOUBLE,
    a158_max20 DOUBLE, a158_min20 DOUBLE, a158_rsv20 DOUBLE,
    a158_qtl20 DOUBLE, a158_cntp20 DOUBLE, a158_sump20 DOUBLE,
    a158_vma20 DOUBLE, a158_vstd20 DOUBLE,
    a158_roc30 DOUBLE, a158_ma30 DOUBLE, a158_std30 DOUBLE,
    a158_max30 DOUBLE, a158_min30 DOUBLE, a158_rsv30 DOUBLE,
    a158_qtl30 DOUBLE, a158_cntp30 DOUBLE, a158_sump30 DOUBLE,
    a158_vma30 DOUBLE, a158_vstd30 DOUBLE,
    a158_roc60 DOUBLE, a158_ma60 DOUBLE, a158_std60 DOUBLE,
    a158_max60 DOUBLE, a158_min60 DOUBLE, a158_rsv60 DOUBLE,
    a158_qtl60 DOUBLE, a158_cntp60 DOUBLE, a158_sump60 DOUBLE,
    a158_vma60 DOUBLE, a158_vstd60 DOUBLE,
    -- Risk factors
    vol_30d   DOUBLE, vol_60d  DOUBLE, vol_120d DOUBLE,
    sharpe_60d DOUBLE,
    mom_30d  DOUBLE, mom_120d DOUBLE,
    -- Financial PIT raw (v2)
    pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE, roe_q DOUBLE,
    -- Event dummies
    event_lhb_7d  BOOLEAN, event_lhb_30d  BOOLEAN,
    event_inst_7d BOOLEAN, event_inst_30d BOOLEAN,
    -- Formula trigger dummies (v2)
    formula_macd_triggered      BOOLEAN,
    formula_dyma_triggered      BOOLEAN,
    formula_turtle20_triggered  BOOLEAN,
    formula_turtle55_triggered  BOOLEAN,
    formula_reversal_triggered  BOOLEAN,
    formula_n_triggered         INTEGER,
    -- v3 Day 2: 调研热度 4
    survey_count_30d  INTEGER,
    survey_count_60d  INTEGER,
    survey_inst_30d   INTEGER,
    survey_inst_60d   INTEGER,
    -- v3 Day 2: 估值 z-score 4 (rolling 1Y, 替代 aif10 latest-snapshot)
    pe_ttm_z_1y  DOUBLE,
    pb_z_1y      DOUBLE,
    ps_ttm_z_1y  DOUBLE,
    roe_q_z_4q   DOUBLE,
    -- v3 Day 2: 板块 momentum 5
    sector_ret_5d        DOUBLE,
    sector_ret_20d       DOUBLE,
    sector_ret_60d       DOUBLE,
    sector_excess_20d    DOUBLE,
    sector_excess_60d    DOUBLE,
    -- v3 Day 3: 机构路径 A 5 (Codex Q3 SQL, mart_stock_industry_pit × fact_top10_holder_period)
    -- ⚠ inst_quality_* 用 mart_institution_profile.win_rate_60d (latest, NOT PIT)
    -- TODO v3.5: 接 mart_institution_profile_pit (as_of_date <= signal_date)
    inst_quality_wavg       DOUBLE,    -- SUM(hold_ratio × quality) / SUM(hold_ratio), 仅匹配机构
    inst_quality_max        DOUBLE,    -- MAX(quality) 仅匹配机构
    inst_total_holding_ratio DOUBLE,   -- SUM(hold_ratio_total) — 全持仓 (含未匹配)
    inst_holder_cnt         INTEGER,   -- COUNT(DISTINCT holder_name_norm) — 全持仓
    top_inst_holding_ratio  DOUBLE,    -- per-signal_date 0.8 quantile 以上机构持仓占总比
    -- v3 Day 3 PIT 元数据
    industry_pit_confidence TEXT,      -- 'observed_snapshot' | 'current_label_fallback' (M1: 下游可 filter)
    -- Metadata
    feature_version TEXT NOT NULL,
    built_at        TEXT NOT NULL,
    PRIMARY KEY (stock_code, signal_date)
);
"""


_FEATURE_JOIN_SQL_V3 = """
WITH
grid AS (
    SELECT s.stock_code, sd.signal_date
    FROM tmp_stocks s
    CROSS JOIN tmp_signal_dates sd
),
lhb_agg AS (
    SELECT
        g.stock_code, g.signal_date,
        COUNT(*) FILTER (WHERE lhb.trade_date::DATE >= g.signal_date - INTERVAL 7 DAY)  AS cnt_7d,
        COUNT(*) FILTER (WHERE lhb.trade_date::DATE >= g.signal_date - INTERVAL 30 DAY) AS cnt_30d
    FROM grid g
    LEFT JOIN fact_lhb_event lhb
      ON lhb.stock_code = g.stock_code
     AND lhb.trade_date::DATE <= g.signal_date
     AND lhb.trade_date::DATE >= g.signal_date - INTERVAL 30 DAY
    GROUP BY g.stock_code, g.signal_date
),
inst_agg AS (
    SELECT
        g.stock_code, g.signal_date,
        COUNT(*) FILTER (WHERE STRPTIME(inst.notice_date, '%Y%m%d')::DATE >= g.signal_date - INTERVAL 7 DAY)  AS cnt_7d,
        COUNT(*) FILTER (WHERE STRPTIME(inst.notice_date, '%Y%m%d')::DATE >= g.signal_date - INTERVAL 30 DAY) AS cnt_30d
    FROM grid g
    LEFT JOIN fact_institution_event inst
      ON inst.stock_code = g.stock_code
     AND STRPTIME(inst.notice_date, '%Y%m%d')::DATE <= g.signal_date
     AND STRPTIME(inst.notice_date, '%Y%m%d')::DATE >= g.signal_date - INTERVAL 30 DAY
    GROUP BY g.stock_code, g.signal_date
),
risk_asof AS (
    SELECT
        stock_code, signal_date,
        vol_30d, vol_60d, vol_120d, sharpe_60d, mom_30d, mom_120d
    FROM (
        SELECT
            g.stock_code, g.signal_date,
            r.vol_30d, r.vol_60d, r.vol_120d, r.sharpe_60d, r.mom_30d, r.mom_120d,
            ROW_NUMBER() OVER (
                PARTITION BY g.stock_code, g.signal_date
                ORDER BY r.calc_date DESC NULLS LAST
            ) AS rn
        FROM grid g
        LEFT JOIN fact_risk_factors r
          ON r.stock_code = g.stock_code
         AND r.calc_date::DATE <= g.signal_date
    ) WHERE rn = 1
),
formula_trigger AS (
    -- fact_technical_trigger (有 formula_id + state), 不是 fact_signal_context (上下文只有量价)
    SELECT
        g.stock_code, g.signal_date,
        MAX(CASE WHEN ftt.formula_id = 'macd_golden_cross' THEN 1 ELSE 0 END)::BOOLEAN AS formula_macd_triggered,
        MAX(CASE WHEN ftt.formula_id = 'dynamic_ma_iterative_cross' THEN 1 ELSE 0 END)::BOOLEAN AS formula_dyma_triggered,
        MAX(CASE WHEN ftt.formula_id = 'turtle_breakout_20' THEN 1 ELSE 0 END)::BOOLEAN AS formula_turtle20_triggered,
        MAX(CASE WHEN ftt.formula_id = 'turtle_breakout_55' THEN 1 ELSE 0 END)::BOOLEAN AS formula_turtle55_triggered,
        MAX(CASE WHEN ftt.formula_id = 'reversal_1m_deep' THEN 1 ELSE 0 END)::BOOLEAN AS formula_reversal_triggered,
        COUNT(DISTINCT ftt.formula_id) AS formula_n_triggered
    FROM grid g
    LEFT JOIN fact_technical_trigger ftt
      ON ftt.stock_code = g.stock_code
     AND CAST(ftt.date AS DATE) = g.signal_date
     -- Codex C1 (a163ca58): 生产 state 是 NULL/just_crossed, 不是 'triggered'
     -- fact_technical_trigger 本身就是触发记录 (表名), 不需 state filter
    GROUP BY g.stock_code, g.signal_date
),
-- v3 Day 2 ① 调研热度 ASOF (PIT-safe, as_of_date <= signal_date)
survey_asof AS (
    SELECT
        stock_code, signal_date,
        survey_count_30d, survey_count_60d, survey_inst_30d, survey_inst_60d
    FROM (
        SELECT
            g.stock_code, g.signal_date,
            sf.survey_count_30d, sf.survey_count_60d,
            sf.survey_inst_30d, sf.survey_inst_60d,
            ROW_NUMBER() OVER (
                PARTITION BY g.stock_code, g.signal_date
                ORDER BY sf.as_of_date DESC
            ) AS rn
        FROM grid g
        LEFT JOIN mart_stock_survey_features sf
          ON sf.stock_code = g.stock_code
         AND CAST(sf.as_of_date AS DATE) <= g.signal_date
         AND CAST(sf.as_of_date AS DATE) >= g.signal_date - INTERVAL 90 DAY
    ) WHERE rn = 1
),
-- v3 Day 2 ② 估值 z-score (PIT-safe rolling 1Y, 替代 aif10 latest-snapshot leakage)
fin_z_history AS (
    -- fact_financial_pit_daily.trade_date 生产是 TEXT, 这里 CAST AS DATE 保证 JOIN 类型一致
    SELECT
        stock_code, CAST(trade_date AS DATE) AS trade_date,
        pe_ttm, pb, ps_ttm, roe_q,
        (pe_ttm - AVG(pe_ttm) OVER w_1y) / NULLIF(STDDEV(pe_ttm) OVER w_1y, 0) AS pe_ttm_z_1y,
        (pb     - AVG(pb)     OVER w_1y) / NULLIF(STDDEV(pb)     OVER w_1y, 0) AS pb_z_1y,
        (ps_ttm - AVG(ps_ttm) OVER w_1y) / NULLIF(STDDEV(ps_ttm) OVER w_1y, 0) AS ps_ttm_z_1y,
        (roe_q  - AVG(roe_q)  OVER w_4q) / NULLIF(STDDEV(roe_q)  OVER w_4q, 0) AS roe_q_z_4q
    FROM fact_financial_pit_daily
    WINDOW
        w_1y AS (PARTITION BY stock_code ORDER BY CAST(trade_date AS DATE)
                 ROWS BETWEEN 239 PRECEDING AND CURRENT ROW),  -- 239 PRECEDING + 当行 = 240 rows = 1Y trading days
        w_4q AS (PARTITION BY stock_code ORDER BY CAST(trade_date AS DATE)
                 ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)    -- 59 + 当行 = 60 rows = 4Q trading days (~)
),
fin_asof AS (
    SELECT stock_code, signal_date,
           pe_ttm, pb, ps_ttm, roe_q,
           pe_ttm_z_1y, pb_z_1y, ps_ttm_z_1y, roe_q_z_4q
    FROM (
        SELECT
            g.stock_code, g.signal_date,
            f.pe_ttm, f.pb, f.ps_ttm, f.roe_q,
            f.pe_ttm_z_1y, f.pb_z_1y, f.ps_ttm_z_1y, f.roe_q_z_4q,
            ROW_NUMBER() OVER (
                PARTITION BY g.stock_code, g.signal_date
                ORDER BY f.trade_date DESC
            ) AS rn
        FROM grid g
        LEFT JOIN fin_z_history f
          ON f.stock_code = g.stock_code
         AND f.trade_date <= g.signal_date  -- both DATE after CAST in fin_z_history
    ) WHERE rn = 1
),
-- v3 Day 2 ③ 板块 momentum via mart_stock_industry_pit (PIT industry mapping)
--           → fact_sector_momentum_daily (PIT sector × date)
-- 注: industry_pit 大部分为 'current_label_fallback' (effective_from=1900-01-01)
-- 跟 backfill_sector_momentum 同妥协 — 接受 industry 漂移 ("3 年内基本不变")
industry_pit_asof AS (
    -- M1 fix: 输出 confidence_level 让下游可 filter 'current_label_fallback' leakage
    -- 当前接受 fallback (跟 backfill_sector_momentum 同妥协, dim_stock_tdx_industry_history 数据稀疏)
    -- audit / P0b filter 时若需要严格 PIT 应限 confidence_level='observed_snapshot'
    SELECT
        g.stock_code, g.signal_date,
        ip.tdx_l1_name AS sector_name,
        ip.confidence_level AS industry_pit_confidence
    FROM grid g
    LEFT JOIN mart_stock_industry_pit ip
      ON ip.stock_code = g.stock_code
     AND CAST(ip.effective_from AS DATE) <= g.signal_date
     AND CAST(ip.effective_to AS DATE) >= g.signal_date
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY g.stock_code, g.signal_date
        ORDER BY ip.is_historical_pit DESC, ip.effective_from DESC
    ) = 1
),
sector_asof AS (
    SELECT
        stock_code, signal_date,
        industry_pit_confidence,
        sector_ret_5d, sector_ret_20d, sector_ret_60d,
        sector_excess_20d, sector_excess_60d
    FROM (
        SELECT
            ipa.stock_code, ipa.signal_date,
            ipa.industry_pit_confidence,
            sm.ret_5d AS sector_ret_5d,
            sm.ret_20d AS sector_ret_20d,
            sm.ret_60d AS sector_ret_60d,
            sm.excess_20d AS sector_excess_20d,
            sm.excess_60d AS sector_excess_60d,
            ROW_NUMBER() OVER (
                PARTITION BY ipa.stock_code, ipa.signal_date
                ORDER BY sm.date DESC
            ) AS rn
        FROM industry_pit_asof ipa
        LEFT JOIN fact_sector_momentum_daily sm
          ON sm.sector_name = ipa.sector_name
         AND CAST(sm.date AS DATE) <= ipa.signal_date
    ) WHERE rn = 1
),
-- v3 Day 3 ④ 机构路径 A (Codex Q3 SQL): mart_stock_industry_pit × fact_top10_holder_period
--          PIT 双重: industry_pit.effective_from<=signal AND holder.effective_date<=signal
-- 注: fact_top10_holder_period.effective_date = "公告日+1 交易日" (DDL PIT 设计)
-- 注: 同行业内 holder count 用 inst_holder_cnt, top 20% quality 占比用 top_inst_holding_ratio
holder_asof_with_inst_quality AS (
    -- Codex M4 fix: 不 COALESCE NULL→0 (避免 unmatched 当 worst quality 偏 wavg)
    -- 后续 inst_path_a 拆分:
    --   - inst_total_holding_ratio / inst_holder_cnt 用全持仓 (含 unmatched)
    --   - inst_quality_{wavg,max} / top_inst_holding_ratio 仅用 inst_quality IS NOT NULL
    SELECT
        g.stock_code, g.signal_date,
        h.holder_name, h.holder_name_norm,
        h.hold_ratio_total,
        ip2.win_rate_60d AS inst_quality,  -- ⚠ NOT PIT, mart_institution_profile latest; v3.5 接 PIT snapshot
        ROW_NUMBER() OVER (
            PARTITION BY g.stock_code, g.signal_date, h.holder_name_norm
            ORDER BY STRPTIME(h.effective_date, '%Y%m%d')::DATE DESC, h.report_date DESC
        ) AS rn
    FROM grid g
    LEFT JOIN fact_top10_holder_period h
      ON h.stock_code = g.stock_code
     AND STRPTIME(h.effective_date, '%Y%m%d')::DATE <= g.signal_date
     AND STRPTIME(h.effective_date, '%Y%m%d')::DATE >= g.signal_date - INTERVAL 180 DAY
    LEFT JOIN mart_institution_profile ip2
      ON ip2.institution_name = h.holder_name_norm
    WHERE h.holder_set = 'free'  -- 优先用流通持仓 (避大股东锁仓 stub)
),
-- Codex M2 fix: per-signal_date 0.8 quantile (排除 unmatched, 防全局 quantile mix future)
inst_quality_q80_per_date AS (
    SELECT signal_date,
           QUANTILE_CONT(inst_quality, 0.8) AS q80
    FROM holder_asof_with_inst_quality
    WHERE rn = 1
      AND inst_quality IS NOT NULL
    GROUP BY signal_date
),
inst_path_a AS (
    SELECT
        h.stock_code, h.signal_date,
        SUM(h.hold_ratio_total * h.inst_quality) FILTER (WHERE h.inst_quality IS NOT NULL) /
            NULLIF(SUM(h.hold_ratio_total)        FILTER (WHERE h.inst_quality IS NOT NULL), 0) AS inst_quality_wavg,
        MAX(h.inst_quality)                                                                    AS inst_quality_max,
        SUM(h.hold_ratio_total)                                                                AS inst_total_holding_ratio,
        COUNT(DISTINCT h.holder_name_norm)                                                     AS inst_holder_cnt,
        SUM(h.hold_ratio_total) FILTER (
            WHERE h.inst_quality IS NOT NULL AND h.inst_quality >= q.q80
        ) / NULLIF(SUM(h.hold_ratio_total), 0)                                                 AS top_inst_holding_ratio
    FROM holder_asof_with_inst_quality h
    LEFT JOIN inst_quality_q80_per_date q ON q.signal_date = h.signal_date
    WHERE h.rn = 1
    GROUP BY h.stock_code, h.signal_date
)
INSERT INTO mart_p0a_feature_label_panel_v3 (
    stock_code, signal_date, entry_date, unable_at_entry,
    fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
    a158_kmid, a158_klen, a158_kmid2, a158_kup, a158_kup2, a158_klow, a158_klow2,
    a158_ksft, a158_ksft2,
    a158_roc5, a158_ma5, a158_std5, a158_max5, a158_min5, a158_rsv5,
    a158_qtl5, a158_cntp5, a158_sump5, a158_vma5, a158_vstd5,
    a158_roc10, a158_ma10, a158_std10, a158_max10, a158_min10, a158_rsv10,
    a158_qtl10, a158_cntp10, a158_sump10, a158_vma10, a158_vstd10,
    a158_roc20, a158_ma20, a158_std20, a158_max20, a158_min20, a158_rsv20,
    a158_qtl20, a158_cntp20, a158_sump20, a158_vma20, a158_vstd20,
    a158_roc30, a158_ma30, a158_std30, a158_max30, a158_min30, a158_rsv30,
    a158_qtl30, a158_cntp30, a158_sump30, a158_vma30, a158_vstd30,
    a158_roc60, a158_ma60, a158_std60, a158_max60, a158_min60, a158_rsv60,
    a158_qtl60, a158_cntp60, a158_sump60, a158_vma60, a158_vstd60,
    vol_30d, vol_60d, vol_120d, sharpe_60d, mom_30d, mom_120d,
    pe_ttm, pb, ps_ttm, roe_q,
    event_lhb_7d, event_lhb_30d, event_inst_7d, event_inst_30d,
    formula_macd_triggered, formula_dyma_triggered,
    formula_turtle20_triggered, formula_turtle55_triggered, formula_reversal_triggered,
    formula_n_triggered,
    survey_count_30d, survey_count_60d, survey_inst_30d, survey_inst_60d,
    pe_ttm_z_1y, pb_z_1y, ps_ttm_z_1y, roe_q_z_4q,
    sector_ret_5d, sector_ret_20d, sector_ret_60d,
    sector_excess_20d, sector_excess_60d,
    inst_quality_wavg, inst_quality_max, inst_total_holding_ratio,
    inst_holder_cnt, top_inst_holding_ratio,
    industry_pit_confidence,
    feature_version, built_at
)
SELECT
    g.stock_code, g.signal_date,
    l.entry_date, l.unable_at_entry,
    l.fwd_cost_after_5d, l.fwd_cost_after_10d, l.fwd_cost_after_20d,
    a.a158_kmid, a.a158_klen, a.a158_kmid2, a.a158_kup, a.a158_kup2, a.a158_klow, a.a158_klow2,
    a.a158_ksft, a.a158_ksft2,
    a.a158_roc5, a.a158_ma5, a.a158_std5, a.a158_max5, a.a158_min5, a.a158_rsv5,
    a.a158_qtl5, a.a158_cntp5, a.a158_sump5, a.a158_vma5, a.a158_vstd5,
    a.a158_roc10, a.a158_ma10, a.a158_std10, a.a158_max10, a.a158_min10, a.a158_rsv10,
    a.a158_qtl10, a.a158_cntp10, a.a158_sump10, a.a158_vma10, a.a158_vstd10,
    a.a158_roc20, a.a158_ma20, a.a158_std20, a.a158_max20, a.a158_min20, a.a158_rsv20,
    a.a158_qtl20, a.a158_cntp20, a.a158_sump20, a.a158_vma20, a.a158_vstd20,
    a.a158_roc30, a.a158_ma30, a.a158_std30, a.a158_max30, a.a158_min30, a.a158_rsv30,
    a.a158_qtl30, a.a158_cntp30, a.a158_sump30, a.a158_vma30, a.a158_vstd30,
    a.a158_roc60, a.a158_ma60, a.a158_std60, a.a158_max60, a.a158_min60, a.a158_rsv60,
    a.a158_qtl60, a.a158_cntp60, a.a158_sump60, a.a158_vma60, a.a158_vstd60,
    r.vol_30d, r.vol_60d, r.vol_120d, r.sharpe_60d, r.mom_30d, r.mom_120d,
    fz.pe_ttm, fz.pb, fz.ps_ttm, fz.roe_q,
    COALESCE(lh.cnt_7d, 0)  > 0,
    COALESCE(lh.cnt_30d, 0) > 0,
    COALESCE(ie.cnt_7d, 0)  > 0,
    COALESCE(ie.cnt_30d, 0) > 0,
    COALESCE(ft.formula_macd_triggered,    FALSE),
    COALESCE(ft.formula_dyma_triggered,    FALSE),
    COALESCE(ft.formula_turtle20_triggered, FALSE),
    COALESCE(ft.formula_turtle55_triggered, FALSE),
    COALESCE(ft.formula_reversal_triggered, FALSE),
    COALESCE(ft.formula_n_triggered, 0),
    sv.survey_count_30d, sv.survey_count_60d, sv.survey_inst_30d, sv.survey_inst_60d,
    fz.pe_ttm_z_1y, fz.pb_z_1y, fz.ps_ttm_z_1y, fz.roe_q_z_4q,
    sa.sector_ret_5d, sa.sector_ret_20d, sa.sector_ret_60d,
    sa.sector_excess_20d, sa.sector_excess_60d,
    ipa.inst_quality_wavg, ipa.inst_quality_max, ipa.inst_total_holding_ratio,
    ipa.inst_holder_cnt, ipa.top_inst_holding_ratio,
    sa.industry_pit_confidence,
    ? AS feature_version,
    ? AS built_at
FROM grid g
LEFT JOIN mart_p0a_label_panel l
    ON l.stock_code = g.stock_code AND l.signal_date = g.signal_date
LEFT JOIN a158.fact_alpha158_panel a
    ON a.stock_code = g.stock_code AND a.date = g.signal_date
LEFT JOIN risk_asof r
    ON r.stock_code = g.stock_code AND r.signal_date = g.signal_date
LEFT JOIN fin_asof fz
    ON fz.stock_code = g.stock_code AND fz.signal_date = g.signal_date
LEFT JOIN lhb_agg lh
    ON lh.stock_code = g.stock_code AND lh.signal_date = g.signal_date
LEFT JOIN inst_agg ie
    ON ie.stock_code = g.stock_code AND ie.signal_date = g.signal_date
LEFT JOIN formula_trigger ft
    ON ft.stock_code = g.stock_code AND ft.signal_date = g.signal_date
LEFT JOIN survey_asof sv
    ON sv.stock_code = g.stock_code AND sv.signal_date = g.signal_date
LEFT JOIN sector_asof sa
    ON sa.stock_code = g.stock_code AND sa.signal_date = g.signal_date
LEFT JOIN inst_path_a ipa
    ON ipa.stock_code = g.stock_code AND ipa.signal_date = g.signal_date
"""


def build_p0a_feature_label_panel_v3(
    db_path: str,
    alpha158_db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str],
    output_table: str = "mart_p0a_feature_label_panel_v3",
) -> dict:
    # Codex Mi2 (a163ca58): SQL INSERT 写死 mart_p0a_feature_label_panel_v3, 非默认 output_table 不工作
    if output_table != "mart_p0a_feature_label_panel_v3":
        raise NotImplementedError(
            f"output_table={output_table} not supported; SQL hardcoded to mart_p0a_feature_label_panel_v3"
        )
    """Build P0a feature × label panel v3.

    v2 (85) → v3 (103) 加 18 列:
    - Day 2: survey 4 + valuation_z 4 + sector 5 = 13
    - Day 3: 机构路径 A 5

    Args:
        db_path: smartmoney.duckdb 主 DB.
        alpha158_db_path: alpha158.duckdb (ATTACH a158).
        signal_dates: 待 build 的 signal_dates.
        stock_codes: 待 build 的 stock_codes.
        output_table: 默认 mart_p0a_feature_label_panel_v3.

    Returns:
        {"rows_built": int, "feature_version": "p0a_v3", "built_at": str}
    """
    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes)
    if not signal_dates or not stock_codes:
        return {"rows_built": 0, "feature_version": FEATURE_PANEL_VERSION_V3}

    conn = duck_connect(db_path, attach={"a158": alpha158_db_path})
    try:
        conn.execute(FEATURE_PANEL_DDL_V3)
        conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
        conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
        conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
        conn.execute("DROP TABLE IF EXISTS tmp_stocks")
        conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
        conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

        conn.execute(
            f"DELETE FROM {output_table} WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            f"  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        )

        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        conn.execute(_FEATURE_JOIN_SQL_V3, [FEATURE_PANEL_VERSION_V3, built_at])
        n = conn.execute(
            f"SELECT COUNT(*) FROM {output_table} WHERE signal_date IN "
            f"(SELECT signal_date FROM tmp_signal_dates) "
            f"AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        ).fetchone()[0]

        try:
            from services.data_governance import validate_rows_before_insert
            cur = conn._con.execute(f"SELECT * FROM {output_table} ORDER BY built_at DESC LIMIT 100")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            if rows:
                verify = validate_rows_before_insert(
                    rows, cols, output_table, max_violation_rate=1.0, skip_missing_table=True,
                )
                log.info(f"  governance: {verify['passed']}/{verify['total']} pass; rate={verify['rate']:.4%}")
        except Exception as e:
            log.warning(f"governance verify failed: {e}")

        return {
            "rows_built": n,
            "feature_version": FEATURE_PANEL_VERSION_V3,
            "built_at": built_at,
        }
    finally:
        conn.close()
