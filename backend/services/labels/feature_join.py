"""P0a feature × label JOIN (cross-DB: alpha158.duckdb ATTACH AS a158, 写 smartmoney.duckdb).

输出表 `mart_p0a_feature_label_panel`: P0a 训练主表, 每行 = (stock_code, signal_date)
含特征 + label, 由 P0b ML ranking 训练直接读.

特征来源:
1. **alpha158** (a158.fact_alpha158_panel, 65 列 a158_*): K线衍生量价特征
2. **风险因子** (fact_risk_factors): vol_30d/60d/120d, sharpe_60d, mom_30d/60d/120d
3. **财务 PIT** (fact_financial_pit_daily): pe_ttm, pb, ps_ttm, roe_q (signal_date ≤ trade_date 取最新)
4. **事件特征** (fact_lhb_event / fact_institution_event): 近 7/30 日有无 dummy
5. **流动性 / 上市** (后续): 暂略, P1 扩展

Label 来源:
- mart_p0a_label_panel (本 module 配套, 已含 5/10/20 fwd_cost_after + unable masks)

PIT 守门 (Rule 7):
- alpha158: date ≤ signal_date (date = signal_date 时是当日特征, signal 发出后才生成)
  实际接入: a158_panel.date 是 trading day, signal_date = a158 date. 特征只用 trailing.
- 风险因子: calc_date ≤ signal_date 取最新 (`MAX(calc_date) WHERE calc_date <= signal_date`)
- 财务 PIT: trade_date ≤ signal_date (PIT daily 表本身就是 ASOF, 直接 JOIN ON trade_date=signal_date)
- 事件: notice_date ≤ signal_date AND notice_date >= signal_date - {7,30} 算 dummy

KEEP universe 守门: 调用方传 stock_codes (services/universe.py::is_active_a_share).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Iterable

from services.duck_adapter import connect as duck_connect

log = logging.getLogger("labels.feature_join")

FEATURE_PANEL_VERSION = "p0a_v1"


FEATURE_PANEL_DDL = """
CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel (
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
    -- Risk factors (fact_risk_factors)
    vol_30d   DOUBLE, vol_60d  DOUBLE, vol_120d DOUBLE,
    sharpe_60d DOUBLE,
    mom_30d  DOUBLE, mom_120d DOUBLE,
    -- Financial PIT (fact_financial_pit_daily)
    pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE, roe_q DOUBLE,
    -- Event dummies (fact_lhb_event / fact_institution_event)
    event_lhb_7d  BOOLEAN, event_lhb_30d  BOOLEAN,
    event_inst_7d BOOLEAN, event_inst_30d BOOLEAN,
    -- Metadata
    feature_version TEXT NOT NULL,
    built_at        TEXT NOT NULL,
    PRIMARY KEY (stock_code, signal_date)
);
"""


# 主 JOIN SQL (Codex review fix: Q4 LATERAL → conditional aggregate hash join; Q5 PIT ingested_at).
#
# 入参:
#   tmp_signal_dates(signal_date DATE) + tmp_stocks(stock_code TEXT) 已 stage.
#   alpha158.duckdb 已 ATTACH AS a158, mart_p0a_label_panel 已 build.
#
# Q4 修复: 4 LATERAL nested-loop → 2 pre-aggregated CTE (lhb_agg + inst_agg),
#   每个 event 表只扫一次, 用 COUNT FILTER 同时算 7d / 30d windows, 然后 LEFT JOIN.
# Q5 修复: risk_factors ASOF 加 `AND r2.ingested_at <= signal_date::TIMESTAMP` 防 backfill 穿越.
# Bug fix: fact_lhb_event PIT 字段是 trade_date 不是 notice_date.
_FEATURE_JOIN_SQL = """
WITH
grid AS (
    SELECT s.stock_code, sd.signal_date
    FROM tmp_stocks s
    CROSS JOIN tmp_signal_dates sd
),
lhb_agg AS (
    -- 一次扫 fact_lhb_event, 跟每个 grid 行 join 算 7d / 30d 窗口.
    SELECT
        g.stock_code, g.signal_date,
        COUNT(*) FILTER (WHERE lhb.trade_date::DATE >= g.signal_date - INTERVAL 7 DAY)  AS cnt_7d,
        COUNT(*) FILTER (WHERE lhb.trade_date::DATE >= g.signal_date - INTERVAL 30 DAY) AS cnt_30d
    FROM grid g
    LEFT JOIN fact_lhb_event lhb
      ON lhb.stock_code = g.stock_code
     AND lhb.trade_date::DATE <= g.signal_date
     AND lhb.trade_date::DATE >= g.signal_date - INTERVAL 30 DAY  -- 30d 是最大窗, 7d 在 FILTER 里收
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
    -- PIT: risk_factors.calc_date 是 deterministic from K-line (vol_60d 用
    -- [calc_date - 60, calc_date] K-line, by construction PIT-safe), 所以直接用
    -- calc_date <= signal_date ASOF. 不加 ingested_at 过滤的理由:
    --   1. risk_factors 是 deterministic backfill compute, calc_date 来自 K-line 日期
    --      (不是人为标历史), 不存在"calc_date=T 但用 T+1 K-line"的情况.
    --   2. 当前 risk_factors backfill 一次性 ingested_at=2026-05-13, 启用 ingested_at
    --      filter 会过滤掉所有历史 → 100% NULL.
    -- TODO: 后续 risk_factors 增量 ingest 改成 ingested_at=calc_date+1 trade day,
    --   则可启用 `AND r.ingested_at <= g.signal_date::TIMESTAMP` 严格 PIT 防御.
    --   见 Codex review Q5 (ac55f8f69918a6ae0 thread): 当前 calc_date PIT-safe
    --   by construction 已足够, ingested_at filter 是 belt-and-suspenders.
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
)
INSERT INTO mart_p0a_feature_label_panel (
    stock_code, signal_date,
    entry_date, unable_at_entry,
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
    COALESCE(lh.cnt_7d, 0)  > 0 AS event_lhb_7d,
    COALESCE(lh.cnt_30d, 0) > 0 AS event_lhb_30d,
    COALESCE(ie.cnt_7d, 0)  > 0 AS event_inst_7d,
    COALESCE(ie.cnt_30d, 0) > 0 AS event_inst_30d,
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
"""


def build_p0a_feature_label_panel(
    db_path: str,
    alpha158_db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str],
    output_table: str = "mart_p0a_feature_label_panel",
) -> dict:
    """Build feature × label panel.

    前置: mart_p0a_label_panel 已 build (调 build_p0a_label_panel).

    Args:
        db_path: smartmoney.duckdb (写入 + risk/financial/event 源).
        alpha158_db_path: alpha158.duckdb (ATTACH AS a158 → fact_alpha158_panel).
        signal_dates: 训练 signal date list.
        stock_codes: KEEP universe.
        output_table: target.

    Returns:
        {"rows_built": int, "feature_version": str, "built_at": str}.
    """
    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes)
    if not signal_dates or not stock_codes:
        return {"rows_built": 0, "feature_version": FEATURE_PANEL_VERSION}

    conn = duck_connect(db_path, attach={"a158": alpha158_db_path})
    try:
        conn.execute(FEATURE_PANEL_DDL)

        conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
        conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
        conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
        conn.execute("DROP TABLE IF EXISTS tmp_stocks")
        conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
        conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

        # Idempotent: DELETE matching then INSERT
        conn.execute(
            f"DELETE FROM {output_table} WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            f"  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        )

        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        conn.execute(_FEATURE_JOIN_SQL, [FEATURE_PANEL_VERSION, built_at])
        n = conn.execute(
            f"SELECT COUNT(*) FROM {output_table} WHERE signal_date IN "
            f"(SELECT signal_date FROM tmp_signal_dates) "
            f"AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        ).fetchone()[0]

        # Post-insert governance verify (Phase ψ.γ.dict.2 字典 enforce wire)
        verify = _post_insert_governance_verify(conn, output_table, sample_size=100)
        log.info(f"  governance: {verify['passed']}/{verify['total']} rows pass dict; "
                 f"rate={verify['rate']:.4%}")

        return {
            "rows_built": n,
            "feature_version": FEATURE_PANEL_VERSION,
            "built_at": built_at,
            "governance_verify": verify,
        }
    finally:
        conn.close()


def _post_insert_governance_verify(conn, table_name: str, sample_size: int = 100) -> dict:
    """Post-insert field dictionary verify (Phase ψ.γ.dict.2 wire).

    SQL INSERT 完成后 sample N 行, 经 validate_rows_before_insert (skip if 表不在字典).
    返回 {passed, failed, rate, sample_violations}; 不 raise (post-hoc audit).
    """
    try:
        from services.data_governance import validate_rows_before_insert
    except ImportError:
        log.warning("data_governance not importable, skip verify")
        return {"passed": 0, "failed": 0, "total": 0, "rate": 0.0, "violations_sample": []}

    cur = conn._con.execute(f"SELECT * FROM {table_name} ORDER BY built_at DESC LIMIT {sample_size}")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        return {"passed": 0, "failed": 0, "total": 0, "rate": 0.0, "violations_sample": []}
    return validate_rows_before_insert(
        rows, cols, table_name,
        max_violation_rate=1.0,
        skip_missing_table=True,
    )
