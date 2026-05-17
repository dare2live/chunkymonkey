#!/usr/bin/env python3
"""Forecast upside live preview (per Codex round 19+ verdict, SHADOW only).

Combines:
- fact_financial_pit_daily (PE/PB latest)
- mart_stock_industry_pit (PIT industry for industry_pe_median)
- raw_profit_forecast_snapshot_daily (akshare 一致预期 EPS)
- v_price_kline_qfq (latest close)

Compute upside per share = (fy1_eps × target_pe / close) - 1.
Output: mart_forecast_upside_live (SHADOW only, NOT for training).

Codex CRITICAL: this is forward live preview, NOT historical backtest.
- 历史 backtest 必须等 daily PIT snapshot 累积数月再跑
- 当前 raw_profit_forecast_snapshot_daily 只 5 天 ingest (实际只有 today's)
- 仅用作 daily live preview, paper_sim live 验证

usage:
    PYTHONPATH=backend python backend/scripts/compute_forecast_upside_live.py
    PYTHONPATH=backend python backend/scripts/compute_forecast_upside_live.py --target-pe-source blend
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("forecast_upside_live")

REPO = Path(__file__).resolve().parents[2]
SMART_DB = REPO / "data" / "smartmoney.duckdb"
MARKET_DB = REPO / "data" / "market.duckdb"


DDL = """
CREATE TABLE IF NOT EXISTS mart_forecast_upside_live (
    snapshot_date            TEXT NOT NULL,
    stock_code               TEXT NOT NULL,
    fy1_eps_consensus        DOUBLE,
    fy2_eps_consensus        DOUBLE,
    forecast_inst_count      INTEGER,
    close                    DOUBLE,
    pe_ttm                   DOUBLE,
    industry_l1              TEXT,
    industry_pe_median       DOUBLE,
    target_pe_self_median    DOUBLE,
    target_pe_blend          DOUBLE,
    upside_self              DOUBLE,
    upside_industry          DOUBLE,
    upside_blend             DOUBLE,
    upside_consensus_pe      DOUBLE,
    target_pe_source         TEXT,
    built_at                 TIMESTAMP,
    is_shadow_only           BOOLEAN DEFAULT TRUE,  -- SHADOW: 不可入 training
    PRIMARY KEY (snapshot_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_ful_code ON mart_forecast_upside_live(stock_code);
CREATE INDEX IF NOT EXISTS idx_ful_date ON mart_forecast_upside_live(snapshot_date);
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Live forecast upside preview (SHADOW)")
    parser.add_argument("--snapshot-date", default=None,
                        help="default today; for backfill use specific date")
    parser.add_argument("--target-pe-source", default="blend",
                        choices=["self_median", "industry_median", "blend", "consensus_pe"])
    parser.add_argument("--blend-self-weight", type=float, default=0.6)  # rule-compliance: ok evidence=Codex-round-19-recommend
    parser.add_argument("--target-pe-floor", type=float, default=5.0)  # rule-compliance: ok evidence=Codex-round-19-recommend
    parser.add_argument("--target-pe-cap", type=float, default=80.0)  # rule-compliance: ok evidence=Codex-round-19-recommend
    parser.add_argument("--self-pe-window-days", type=int, default=480)  # rule-compliance: ok evidence=Codex-round-19-recommend
    args = parser.parse_args()

    t0 = time.time()
    snapshot_date = args.snapshot_date or datetime.now().strftime("%Y-%m-%d")
    built_at = datetime.now().isoformat(timespec="seconds")  # ISO string, avoid pytz
    log.info(f"=== Live forecast upside ({snapshot_date}, target_pe={args.target_pe_source}) ===")

    # Open DBs
    conn = duckdb.connect(str(SMART_DB))
    try:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)

        # Attach market.duckdb for v_price_kline_qfq
        conn.execute(f"ATTACH '{MARKET_DB}' AS mkt (READ_ONLY)")

        # Single CTE: forecast × industry × latest_pe × close
        # blend_self_weight & bounds passed via SQL params
        sql = f"""
            WITH forecast AS (
                SELECT stock_code, eps_forecast_this_year AS fy1_eps,
                       eps_forecast_next_year AS fy2_eps,
                       forecast_inst_count
                  FROM raw_profit_forecast_snapshot_daily
                 WHERE snapshot_date = ?
            ),
            industry_pe AS (
                -- 行业 PE 中位 from latest fact_financial_pit_daily JOIN PIT industry
                SELECT sip.tdx_l1_name AS industry_l1,
                       MEDIAN(f.pe_ttm) AS industry_pe_median
                  FROM mart_stock_industry_pit sip
                  JOIN fact_financial_pit_daily f
                    ON f.stock_code = sip.stock_code
                   AND f.trade_date = (SELECT MAX(trade_date) FROM fact_financial_pit_daily)
                 WHERE sip.confidence_level = 'observed_snapshot'
                   AND sip.effective_from <= ?
                   AND (sip.effective_to > ? OR sip.effective_to IS NULL)
                   AND f.pe_ttm > 0
                 GROUP BY 1
            ),
            stock_pit_industry AS (
                SELECT stock_code, tdx_l1_name AS industry_l1
                  FROM mart_stock_industry_pit
                 WHERE confidence_level = 'observed_snapshot'
                   AND effective_from <= ?
                   AND (effective_to > ? OR effective_to IS NULL)
            ),
            stock_self_pe AS (
                -- 本股 rolling N 日 PE 中位 (PIT-safe trailing window)
                SELECT stock_code,
                       MEDIAN(pe_ttm) AS pe_self_median
                  FROM fact_financial_pit_daily
                 WHERE CAST(trade_date AS DATE) >= CAST(? AS DATE) - INTERVAL '? days'
                   AND CAST(trade_date AS DATE) <= CAST(? AS DATE)
                   AND pe_ttm > 0
                 GROUP BY 1
            ),
            latest_pe AS (
                SELECT stock_code, pe_ttm
                  FROM fact_financial_pit_daily
                 WHERE trade_date = (SELECT MAX(trade_date) FROM fact_financial_pit_daily)
            ),
            latest_close AS (
                -- Latest close PER STOCK (not single global latest date — many stocks won't trade today)
                SELECT code AS stock_code, close
                  FROM (
                    SELECT code, date, close,
                           ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                      FROM mkt.v_price_kline_qfq
                     WHERE adjust='qfq' AND freq='daily'
                       AND CAST(date AS DATE) >= CAST(? AS DATE) - INTERVAL '10 days'
                  )
                 WHERE rn = 1
            )
            SELECT
                ? AS snapshot_date,
                f.stock_code,
                f.fy1_eps AS fy1_eps_consensus,
                f.fy2_eps AS fy2_eps_consensus,
                f.forecast_inst_count,
                c.close,
                p.pe_ttm,
                si.industry_l1,
                ip.industry_pe_median,
                sp.pe_self_median AS target_pe_self_median,
                LEAST(?, GREATEST(?,
                    ? * COALESCE(sp.pe_self_median, ip.industry_pe_median) +
                    (1 - ?) * COALESCE(ip.industry_pe_median, sp.pe_self_median)
                )) AS target_pe_blend,
                -- Upside variants
                (f.fy1_eps * sp.pe_self_median / NULLIF(c.close, 0)) - 1 AS upside_self,
                (f.fy1_eps * ip.industry_pe_median / NULLIF(c.close, 0)) - 1 AS upside_industry,
                (f.fy1_eps * LEAST(?, GREATEST(?,
                    ? * COALESCE(sp.pe_self_median, ip.industry_pe_median) +
                    (1 - ?) * COALESCE(ip.industry_pe_median, sp.pe_self_median)
                )) / NULLIF(c.close, 0)) - 1 AS upside_blend,
                (f.fy1_eps * p.pe_ttm / NULLIF(c.close, 0)) - 1 AS upside_consensus_pe,
                ? AS target_pe_source,
                ? AS built_at,
                TRUE AS is_shadow_only
              FROM forecast f
              LEFT JOIN stock_pit_industry si ON si.stock_code = f.stock_code
              LEFT JOIN industry_pe ip       ON ip.industry_l1 = si.industry_l1
              LEFT JOIN stock_self_pe sp     ON sp.stock_code = f.stock_code
              LEFT JOIN latest_pe p          ON p.stock_code = f.stock_code
              LEFT JOIN latest_close c       ON c.stock_code = f.stock_code
             WHERE f.fy1_eps > 0   -- 排除负预期 EPS (亏损股)
        """

        # DuckDB doesn't support INTERVAL with parameter — substitute inline window_days
        sql_filled = sql.replace("INTERVAL '? days'", f"INTERVAL '{args.self_pe_window_days} days'")
        params = [
            snapshot_date,  # forecast filter
            snapshot_date, snapshot_date,  # industry_pe filter
            snapshot_date, snapshot_date,  # stock_pit_industry filter
            snapshot_date, snapshot_date,  # stock_self_pe range
            snapshot_date,  # latest_close 10d lookback anchor
            snapshot_date,  # output snapshot_date
            args.target_pe_cap, args.target_pe_floor,
            args.blend_self_weight, args.blend_self_weight,
            args.target_pe_cap, args.target_pe_floor,
            args.blend_self_weight, args.blend_self_weight,
            args.target_pe_source,
            built_at,
        ]
        rows = conn.execute(sql_filled, params).fetchall()
        log.info(f"  computed {len(rows):,} rows")

        # Bulk INSERT into mart_forecast_upside_live (ON CONFLICT skip — immutable snapshot)
        if rows:
            cols = [
                "snapshot_date", "stock_code", "fy1_eps_consensus", "fy2_eps_consensus",
                "forecast_inst_count", "close", "pe_ttm", "industry_l1",
                "industry_pe_median", "target_pe_self_median", "target_pe_blend",
                "upside_self", "upside_industry", "upside_blend", "upside_consensus_pe",
                "target_pe_source", "built_at", "is_shadow_only",
            ]
            placeholders = ",".join(["?"] * len(cols))
            col_list = ",".join(cols)
            # Clear today's slice + re-insert (idempotent re-runs)
            conn.execute("DELETE FROM mart_forecast_upside_live WHERE snapshot_date = ?", [snapshot_date])
            inserted = 0
            for r in rows:
                try:
                    conn.execute(
                        f"INSERT INTO mart_forecast_upside_live ({col_list}) VALUES ({placeholders})",
                        list(r),
                    )
                    inserted += 1
                except Exception as e:
                    # rule-compliance: ok evidence=ingest-best-effort-batch
                    log.warning(f"insert err: {e}")
            log.info(f"  inserted {inserted:,} rows")

            # Quick top-K by upside_blend
            top = conn.execute("""
                SELECT stock_code, industry_l1, fy1_eps_consensus, close, pe_ttm,
                       industry_pe_median, target_pe_blend, upside_blend
                  FROM mart_forecast_upside_live
                 WHERE snapshot_date = ?
                   AND upside_blend IS NOT NULL
                   AND forecast_inst_count >= 5
                 ORDER BY upside_blend DESC
                 LIMIT 10
            """, [snapshot_date]).fetchall()
            log.info("Top 10 by upside_blend (forecast_inst_count >= 5):")
            for r in top:
                code, ind, eps, cls, pe, ind_pe, tgt, up = r
                pe_s = f"{pe:.1f}" if pe is not None else "NA"
                ind_pe_s = f"{ind_pe:.1f}" if ind_pe is not None else "NA"
                tgt_s = f"{tgt:.1f}" if tgt is not None else "NA"
                up_s = f"{up*100:.1f}%" if up is not None else "NA"
                log.info(f"  {code} ({ind}): eps={eps:.2f}, close={cls:.2f}, "
                         f"pe={pe_s}, ind_pe={ind_pe_s}, target={tgt_s}, upside={up_s}")
    finally:
        conn.close()

    log.info(f"=== Done in {time.time()-t0:.1f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
