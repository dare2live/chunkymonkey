#!/usr/bin/env python3
"""ETF B3: 板块轮动分析 — 基于 ETF 自身动量

输入:
  data/etf.duckdb
    etf_price_kline       (code, date, close, amount, ...)
    etf_asset_universe    (code, name, category, ...)

输出:
  mart_etf_sector_rotation
    snapshot_date, sector, etf_count,
    avg_ret_20d, avg_ret_60d, amount_chg_20d,
    rel_strength_4w (vs 全市场 ETF 平均), rel_strength_12w,
    rotation_score (综合), rotation_rank,
    rotation_label ('leader' / 'observer' / 'laggard'),
    leading_etf_code, leading_etf_name,
    updated_at

计算原则 (纯 SQL, 无 ML):
  rotation_score = (
    normalize(avg_ret_20d) * 0.35 +
    normalize(rel_strength_4w) * 0.30 +
    normalize(amount_chg_20d) * 0.20 +
    normalize(avg_ret_60d) * 0.15
  ) 各子分归一化到 0..100

leader:   rotation_rank <= 5 且 rotation_score >= 60
laggard:  rotation_rank > N-5 或 rotation_score < 35
observer: 其它
"""
from __future__ import annotations
import argparse, logging, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import duckdb

logger = logging.getLogger("etf_sector_rotation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

ETF_DB = Path(__file__).resolve().parent.parent.parent / "data" / "etf.duckdb"

DDL = """
CREATE TABLE IF NOT EXISTS mart_etf_sector_rotation (
    snapshot_date     DATE NOT NULL,
    sector            VARCHAR NOT NULL,
    etf_count         INTEGER,
    avg_ret_20d       REAL,
    avg_ret_60d       REAL,
    amount_chg_20d    REAL,
    rel_strength_4w   REAL,
    rel_strength_12w  REAL,
    rotation_score    REAL,
    rotation_rank     INTEGER,
    rotation_label    VARCHAR,
    leading_etf_code  VARCHAR,
    leading_etf_name  VARCHAR,
    updated_at        TEXT,
    PRIMARY KEY (snapshot_date, sector)
);
CREATE INDEX IF NOT EXISTS idx_mesr_sector ON mart_etf_sector_rotation(sector);
CREATE INDEX IF NOT EXISTS idx_mesr_score  ON mart_etf_sector_rotation(snapshot_date, rotation_score DESC);
"""


def compute(conn, snapshot_date: str | None = None) -> int:
    """Return: rows inserted."""
    if snapshot_date is None:
        row = conn.execute("SELECT MAX(date) FROM etf_price_kline").fetchone()
        snapshot_date = str(row[0]) if row and row[0] else None
    if not snapshot_date:
        logger.error("etf_price_kline 无数据")
        return 0
    logger.info("snapshot_date = %s", snapshot_date)

    # 预聚合 per-ETF 20d/60d 收益 + 20d 成交额变化 (ASOF 计算)
    conn.execute("DROP TABLE IF EXISTS _etf_metrics")
    conn.execute(
        """
        CREATE TEMP TABLE _etf_metrics AS
        WITH latest AS (
            SELECT code, date, close, amount,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) rn
            FROM etf_price_kline
            WHERE date <= ? AND freq='daily' AND adjust='qfq'
        )
        SELECT
            code,
            MAX(CASE WHEN rn = 1   THEN close END)  AS close_t0,
            MAX(CASE WHEN rn = 20  THEN close END)  AS close_20,
            MAX(CASE WHEN rn = 60  THEN close END)  AS close_60,
            MAX(CASE WHEN rn <= 20 THEN amount END) AS amount_recent_max,
            AVG(CASE WHEN rn <= 20 THEN amount END) AS amount_avg_20,
            AVG(CASE WHEN rn > 20 AND rn <= 40 THEN amount END) AS amount_avg_prev_20
        FROM latest
        WHERE rn <= 60
        GROUP BY code
        """,
        [snapshot_date],
    )

    # 计算每只 ETF 的 ret_20d / ret_60d / amount_chg_20d
    conn.execute("DROP TABLE IF EXISTS _etf_perf")
    conn.execute(
        """
        CREATE TEMP TABLE _etf_perf AS
        SELECT
            m.code,
            u.name,
            u.category,
            CASE WHEN m.close_20 IS NOT NULL AND m.close_20 > 0 THEN (m.close_t0 - m.close_20) / m.close_20 * 100 END AS ret_20d,
            CASE WHEN m.close_60 IS NOT NULL AND m.close_60 > 0 THEN (m.close_t0 - m.close_60) / m.close_60 * 100 END AS ret_60d,
            CASE WHEN m.amount_avg_prev_20 IS NOT NULL AND m.amount_avg_prev_20 > 0
                 THEN (m.amount_avg_20 - m.amount_avg_prev_20) / m.amount_avg_prev_20 * 100 END AS amount_chg_20d
        FROM _etf_metrics m
        JOIN etf_asset_universe u USING (code)
        WHERE u.is_active = 1 AND u.category IS NOT NULL AND u.category <> ''
        """
    )

    # 全市场 ETF 平均 (for rel strength baseline)
    market_avg = conn.execute(
        "SELECT AVG(ret_20d) AS ret20, AVG(ret_60d) AS ret60 FROM _etf_perf WHERE ret_20d IS NOT NULL"
    ).fetchone()
    mkt_ret20 = market_avg[0] or 0.0
    mkt_ret60 = market_avg[1] or 0.0
    logger.info("全市场 ETF 基准 ret20=%.2f%% ret60=%.2f%%", mkt_ret20, mkt_ret60)

    # 按 category 聚合 + rel strength
    conn.execute("DROP TABLE IF EXISTS _sector_agg")
    conn.execute(
        f"""
        CREATE TEMP TABLE _sector_agg AS
        SELECT
            category AS sector,
            COUNT(*) AS etf_count,
            AVG(ret_20d) AS avg_ret_20d,
            AVG(ret_60d) AS avg_ret_60d,
            AVG(amount_chg_20d) AS amount_chg_20d,
            AVG(ret_20d) - {mkt_ret20} AS rel_strength_4w,
            AVG(ret_60d) - {mkt_ret60} AS rel_strength_12w
        FROM _etf_perf
        WHERE ret_20d IS NOT NULL
        GROUP BY category
        HAVING COUNT(*) >= 2
        """
    )

    # 归一化 → rotation_score
    conn.execute("DROP TABLE IF EXISTS _sector_scored")
    conn.execute(
        """
        CREATE TEMP TABLE _sector_scored AS
        WITH bounds AS (
            SELECT
                MIN(avg_ret_20d) AS r20_min, MAX(avg_ret_20d) AS r20_max,
                MIN(avg_ret_60d) AS r60_min, MAX(avg_ret_60d) AS r60_max,
                MIN(amount_chg_20d) AS amt_min, MAX(amount_chg_20d) AS amt_max,
                MIN(rel_strength_4w) AS rs_min, MAX(rel_strength_4w) AS rs_max
            FROM _sector_agg
        )
        SELECT
            s.*,
            CASE WHEN b.r20_max > b.r20_min THEN (s.avg_ret_20d - b.r20_min) / (b.r20_max - b.r20_min) * 100 ELSE 50 END AS n_r20,
            CASE WHEN b.r60_max > b.r60_min THEN (s.avg_ret_60d - b.r60_min) / (b.r60_max - b.r60_min) * 100 ELSE 50 END AS n_r60,
            CASE WHEN b.amt_max > b.amt_min THEN (s.amount_chg_20d - b.amt_min) / (b.amt_max - b.amt_min) * 100 ELSE 50 END AS n_amt,
            CASE WHEN b.rs_max > b.rs_min THEN (s.rel_strength_4w - b.rs_min) / (b.rs_max - b.rs_min) * 100 ELSE 50 END AS n_rs
        FROM _sector_agg s CROSS JOIN bounds b
        """
    )

    conn.execute("DROP TABLE IF EXISTS _sector_final")
    conn.execute(
        """
        CREATE TEMP TABLE _sector_final AS
        SELECT
            sector, etf_count,
            avg_ret_20d, avg_ret_60d, amount_chg_20d,
            rel_strength_4w, rel_strength_12w,
            ROUND(n_r20 * 0.35 + n_rs * 0.30 + n_amt * 0.20 + n_r60 * 0.15, 2) AS rotation_score
        FROM _sector_scored
        """
    )

    # 每个板块的最强 ETF (ret_20d 最高)
    conn.execute("DROP TABLE IF EXISTS _sector_leader")
    conn.execute(
        """
        CREATE TEMP TABLE _sector_leader AS
        SELECT sector, code AS leading_etf_code, name AS leading_etf_name
        FROM (
            SELECT
                p.category AS sector, p.code, p.name,
                ROW_NUMBER() OVER (PARTITION BY p.category ORDER BY p.ret_20d DESC NULLS LAST) AS rn
            FROM _etf_perf p
        )
        WHERE rn = 1
        """
    )

    # 最终组装 + rotation_label
    conn.execute("DELETE FROM mart_etf_sector_rotation WHERE snapshot_date = ?", [snapshot_date])
    conn.execute(
        """
        INSERT INTO mart_etf_sector_rotation
        (snapshot_date, sector, etf_count, avg_ret_20d, avg_ret_60d,
         amount_chg_20d, rel_strength_4w, rel_strength_12w,
         rotation_score, rotation_rank, rotation_label,
         leading_etf_code, leading_etf_name, updated_at)
        WITH ranked AS (
            SELECT
                f.*,
                l.leading_etf_code, l.leading_etf_name,
                ROW_NUMBER() OVER (ORDER BY rotation_score DESC) AS rk,
                COUNT(*) OVER () AS total
            FROM _sector_final f
            LEFT JOIN _sector_leader l USING (sector)
        )
        SELECT
            CAST(? AS DATE),
            sector, etf_count,
            avg_ret_20d, avg_ret_60d, amount_chg_20d,
            rel_strength_4w, rel_strength_12w,
            rotation_score,
            CAST(rk AS INTEGER),
            CASE
                WHEN rk <= 5 AND rotation_score >= 60 THEN 'leader'
                WHEN rk > total - 5 OR rotation_score < 35 THEN 'laggard'
                ELSE 'observer'
            END AS rotation_label,
            leading_etf_code, leading_etf_name,
            CURRENT_TIMESTAMP::TEXT
        FROM ranked
        """,
        [snapshot_date],
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM mart_etf_sector_rotation WHERE snapshot_date = ?", [snapshot_date]).fetchone()[0]
    logger.info("✓ 写入 %d 个板块", n)
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None, help='YYYY-MM-DD, 默认 etf_price_kline 最新日期')
    args = parser.parse_args()

    conn = duckdb.connect(str(ETF_DB))
    conn.execute(DDL)
    n = compute(conn, args.date)

    # 打印 top 10 板块
    if n:
        rows = conn.execute(
            """
            SELECT sector, etf_count, rotation_score, rotation_rank, rotation_label,
                   avg_ret_20d, amount_chg_20d, rel_strength_4w, leading_etf_name
            FROM mart_etf_sector_rotation
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM mart_etf_sector_rotation)
            ORDER BY rotation_rank
            LIMIT 10
            """
        ).fetchall()
        logger.info("Top 10 板块:")
        for r in rows:
            logger.info(
                "  [%02d] %-18s  score=%5.1f %-9s  ret20d=%+5.1f%% amt20d=%+6.1f%% rel4w=%+5.2f  龙头: %s",
                r[3], (r[0] or '')[:18], r[2], r[4], r[5] or 0, r[6] or 0, r[7] or 0, r[8] or '-'
            )
    conn.close()


if __name__ == "__main__":
    main()
