"""P0a feature × label JOIN v2 — 加 formula_trigger 特征 (PLAN_V3 §99 P0a).

v1 (services/labels/feature_join.py) 79 features → v2 加 6 features:
- formula_{macd, dyma, turtle20, turtle55, reversal}_triggered (signal_date 当日公式触发)
- formula_n_triggered (当日触发公式数量)

→ 总 85 features, 同 mart_p0a_label_panel labels JOIN 出 mart_p0a_feature_label_panel_v2.

来源:
- fact_signal_context (PIT, stock × date × formula_id × state 触发记录)

PIT 严格 (Rule 7):
- formula_trigger: fact_signal_context.date::DATE = signal_date 严格 PIT (当日触发).

**Codex review (acf48d35a80850383) Q1 CRITICAL 修复**:
- 删除 stage_opt_per_stock 三列 (stage_opt_best_sharpe / best_avg_ret / total_traded):
  `MAX(COALESCE(oos_sharpe, sharpe)) GROUP BY stock_code` 全期 MAX 是**系统性 leakage**
  — 给每个 signal_date 历史 row 用了未来 Optuna OOS 结果. 不是 PIT.
- TODO v3: 重跑 Optuna walk-forward expanding_monthly, 入库 (stock × cutoff_date × best_sharpe),
  然后 ASOF cutoff_date <= signal_date JOIN.
- Q2 nice-to-have: formula_id 5-enum 硬编码 + 后续加 audit 报告 unknown formulas.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Iterable

from services.duck_adapter import connect as duck_connect

log = logging.getLogger("labels.feature_join_v2")

FEATURE_PANEL_VERSION_V2 = "p0a_v2"


FEATURE_PANEL_DDL_V2 = """
CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v2 (
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
    -- Financial PIT
    pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE, roe_q DOUBLE,
    -- Event dummies
    event_lhb_7d  BOOLEAN, event_lhb_30d  BOOLEAN,
    event_inst_7d BOOLEAN, event_inst_30d BOOLEAN,
    -- v2 新加 formula trigger dummies (Codex review acf48d35a80850383 Q1: 删除 stage_opt cols leakage)
    formula_macd_triggered      BOOLEAN,
    formula_dyma_triggered      BOOLEAN,
    formula_turtle20_triggered  BOOLEAN,
    formula_turtle55_triggered  BOOLEAN,
    formula_reversal_triggered  BOOLEAN,
    formula_n_triggered         INTEGER,
    -- Metadata
    feature_version TEXT NOT NULL,
    built_at        TEXT NOT NULL,
    PRIMARY KEY (stock_code, signal_date)
);
"""


# v2 SQL: 在 v1 基础上加 formula_trigger CTE (stage_opt 删除, Codex Q1 leakage).
# 入参: tmp_signal_dates / tmp_stocks 已 stage; a158.fact_alpha158_panel ATTACHed.
_FEATURE_JOIN_SQL_V2 = """
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
        vol_30d, vol_60d, vol_120d, sharpe_60d,
        mom_30d, mom_120d
    FROM (
        SELECT
            g.stock_code, g.signal_date,
            r.vol_30d, r.vol_60d, r.vol_120d, r.sharpe_60d,
            r.mom_30d, r.mom_120d,
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
-- v2 新加: signal_date 当日公式触发 dummy
-- (Codex review acf48d35a80850383 Q1 CRITICAL: 删除 stage_opt_per_stock CTE,
--  MAX GROUP BY stock_code 是全期 leakage, 不切 signal_date. v3 重 Optuna walk-forward 后启用.)
formula_trigger AS (
    SELECT
        g.stock_code, g.signal_date,
        MAX(CASE WHEN fsc.formula_id = 'macd_golden_cross' THEN 1 ELSE 0 END)::BOOLEAN AS formula_macd_triggered,
        MAX(CASE WHEN fsc.formula_id = 'dynamic_ma_iterative_cross' THEN 1 ELSE 0 END)::BOOLEAN AS formula_dyma_triggered,
        MAX(CASE WHEN fsc.formula_id = 'turtle_breakout_20' THEN 1 ELSE 0 END)::BOOLEAN AS formula_turtle20_triggered,
        MAX(CASE WHEN fsc.formula_id = 'turtle_breakout_55' THEN 1 ELSE 0 END)::BOOLEAN AS formula_turtle55_triggered,
        MAX(CASE WHEN fsc.formula_id = 'reversal_1m_deep' THEN 1 ELSE 0 END)::BOOLEAN AS formula_reversal_triggered,
        COUNT(DISTINCT fsc.formula_id) AS formula_n_triggered
    FROM grid g
    LEFT JOIN fact_signal_context fsc
      ON fsc.stock_code = g.stock_code
     AND fsc.date::DATE = g.signal_date
     AND fsc.state = 'triggered'
    GROUP BY g.stock_code, g.signal_date
)
INSERT INTO mart_p0a_feature_label_panel_v2 (
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
    vol_30d, vol_60d, vol_120d, sharpe_60d,
    mom_30d, mom_120d,
    pe_ttm, pb, ps_ttm, roe_q,
    event_lhb_7d, event_lhb_30d, event_inst_7d, event_inst_30d,
    formula_macd_triggered, formula_dyma_triggered,
    formula_turtle20_triggered, formula_turtle55_triggered, formula_reversal_triggered,
    formula_n_triggered,
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
    r.vol_30d, r.vol_60d, r.vol_120d, r.sharpe_60d,
    r.mom_30d, r.mom_120d,
    f.pe_ttm, f.pb, f.ps_ttm, f.roe_q,
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
    ? AS feature_version,
    ? AS built_at
FROM grid g
LEFT JOIN mart_p0a_label_panel l
    ON l.stock_code = g.stock_code AND l.signal_date = g.signal_date
LEFT JOIN a158.fact_alpha158_panel a
    ON a.stock_code = g.stock_code AND a.date = g.signal_date
LEFT JOIN risk_asof r
    ON r.stock_code = g.stock_code AND r.signal_date = g.signal_date
LEFT JOIN fact_financial_pit_daily f
    ON f.stock_code = g.stock_code AND f.trade_date = g.signal_date
LEFT JOIN lhb_agg lh
    ON lh.stock_code = g.stock_code AND lh.signal_date = g.signal_date
LEFT JOIN inst_agg ie
    ON ie.stock_code = g.stock_code AND ie.signal_date = g.signal_date
LEFT JOIN formula_trigger ft
    ON ft.stock_code = g.stock_code AND ft.signal_date = g.signal_date
"""


def build_p0a_feature_label_panel_v2(
    db_path: str,
    alpha158_db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str],
    output_table: str = "mart_p0a_feature_label_panel_v2",
) -> dict:
    """Build P0a feature × label panel v2 (+ formula_trigger 特征, stage_opt 删除见 Codex Q1)."""
    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes)
    if not signal_dates or not stock_codes:
        return {"rows_built": 0, "feature_version": FEATURE_PANEL_VERSION_V2}

    conn = duck_connect(db_path, attach={"a158": alpha158_db_path})
    try:
        conn.execute(FEATURE_PANEL_DDL_V2)
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
        conn.execute(_FEATURE_JOIN_SQL_V2, [FEATURE_PANEL_VERSION_V2, built_at])
        n = conn.execute(
            f"SELECT COUNT(*) FROM {output_table} WHERE signal_date IN "
            f"(SELECT signal_date FROM tmp_signal_dates) "
            f"AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        ).fetchone()[0]

        # Post-insert governance verify (Phase ψ.γ.dict.2)
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
            "feature_version": FEATURE_PANEL_VERSION_V2,
            "built_at": built_at,
        }
    finally:
        conn.close()
