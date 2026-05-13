"""Phase η++++++ — 重建 mart_stage_formula_fitness 完整 6×6×5×7 矩阵.

旧表 322 行只有 2 类 fund_stage (`未充分演绎` + `unknown`), 不能用.
新表: JOIN mart_stock_formula_optuna_v2 + mart_stock_picture_daily 拿 fund_stage
       → 按 (fund_stage × tech_stage × formula × hp) 加权聚合
       → 1 行 1 桶组合, rank_in_stage 标 best

用法:
  PYTHONPATH=backend python backend/scripts/rebuild_stage_formula_fitness.py
"""
from __future__ import annotations

import logging
import time

from services.db import get_conn


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("rebuild_stage_formula_fitness")


DDL = """
DROP TABLE IF EXISTS mart_stage_formula_fitness;
CREATE TABLE IF NOT EXISTS mart_stage_formula_fitness (
    fundamental_stage  TEXT NOT NULL,    -- 6 类 (失效破坏/已充分演绎/.../周期复苏)
    technical_stage    TEXT NOT NULL,    -- 5 类 (1/1.5/2/3/4) + ?
    formula_id         TEXT NOT NULL,
    formula_variant    TEXT NOT NULL,
    holding_days       INTEGER NOT NULL,
    n_signals          INTEGER NOT NULL, -- 加权后的 sum(n_signals)
    n_stocks           INTEGER,          -- distinct stocks 数 (信心补充)
    win_rate           REAL,             -- 加权
    avg_ret            REAL,             -- 加权
    avg_dd             REAL,             -- 加权
    calmar             REAL,             -- 算后
    sharpe             REAL,             -- 算后
    rank_in_stage      INTEGER,          -- 在 (fund×tech) 内按 sharpe 排名
    is_recommended     BOOLEAN,          -- rank=1 + n>=20 + win>0.55
    eval_start_date    TEXT,
    eval_end_date      TEXT,
    built_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fundamental_stage, technical_stage, formula_variant, holding_days)
);
CREATE INDEX IF NOT EXISTS idx_msff_stage_formula ON mart_stage_formula_fitness(fundamental_stage, technical_stage);
"""


def main():
    t0 = time.time()
    conn = get_conn()
    try:
        conn.executescript(DDL)

        log.info("聚合 (fund × tech × formula × hp) → fitness ...")
        rows = conn.execute("""
        WITH joined AS (
          SELECT
            COALESCE(p.fundamental_stage, '中性') AS fund_stage,
            v.stage_bin AS tech_stage,
            v.formula_id, v.formula_variant, v.holding_days,
            v.n_signals, v.win_rate, v.avg_ret, v.avg_max_dd,
            -- sharpe winsorize (v2 表中 single sharpe 可能 -8e14, cap 到 [-5, +5])
            GREATEST(LEAST(v.sharpe, 5.0), -5.0) AS sharpe_capped,
            v.stock_code
          FROM mart_stock_formula_optuna_v2 v
          LEFT JOIN mart_stock_picture_daily p
            ON p.stock_code = v.stock_code
           AND p.snapshot_date = (SELECT MAX(snapshot_date) FROM mart_stock_picture_daily)
          WHERE v.n_signals >= 3
            AND abs(v.avg_ret) <= 0.5     -- filter outlier (复权异常 +500% 等)
            AND v.avg_max_dd >= -0.5      -- 不接 dd <-50% 的桶
        ),
        agg AS (
          SELECT fund_stage, tech_stage, formula_id, formula_variant, holding_days,
                 SUM(n_signals) AS n_signals,
                 COUNT(DISTINCT stock_code) AS n_stocks,
                 SUM(n_signals * win_rate) / NULLIF(SUM(n_signals), 0) AS win_rate,
                 SUM(n_signals * avg_ret) / NULLIF(SUM(n_signals), 0) AS avg_ret,
                 SUM(n_signals * avg_max_dd) / NULLIF(SUM(n_signals), 0) AS avg_dd,
                 -- weighted avg sharpe (cap 后)
                 SUM(n_signals * sharpe_capped) / NULLIF(SUM(n_signals), 0) AS sharpe
            FROM joined
           GROUP BY 1, 2, 3, 4, 5
        ),
        with_calmar AS (
          SELECT *,
                 CASE WHEN abs(avg_dd) > 0.001 THEN avg_ret / abs(avg_dd) ELSE 0 END AS calmar
            FROM agg
        ),
        ranked AS (
          SELECT *,
                 -- 用 calmar 排序 (用户最关心), 而非 sharpe (易异常)
                 ROW_NUMBER() OVER (
                   PARTITION BY fund_stage, tech_stage
                   ORDER BY calmar DESC NULLS LAST
                 ) AS rank_in_stage
            FROM with_calmar
        )
        SELECT fund_stage, tech_stage, formula_id, formula_variant, holding_days,
               n_signals, n_stocks, win_rate, avg_ret, avg_dd, calmar, sharpe,
               rank_in_stage,
               (rank_in_stage = 1 AND n_signals >= 20 AND win_rate >= 0.55) AS is_recommended
          FROM ranked
        """).fetchall()

        log.info(f"  聚合后: {len(rows):,} 行 (fund×tech×formula×hp)")

        # 写库
        log.info("写库 ...")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_stage_formula_fitness")
            insert_rows = [(
                r[0], r[1], r[2], r[3], int(r[4]),
                int(r[5]), int(r[6] or 0),
                float(r[7]) if r[7] is not None else None,
                float(r[8]) if r[8] is not None else None,
                float(r[9]) if r[9] is not None else None,
                float(r[10]) if r[10] is not None else None,
                float(r[11]) if r[11] is not None else None,
                int(r[12]),
                bool(r[13]),
                "2024-01-01", "2026-05-11",
            ) for r in rows]
            conn.executemany(
                """INSERT INTO mart_stage_formula_fitness (
                    fundamental_stage, technical_stage, formula_id, formula_variant, holding_days,
                    n_signals, n_stocks, win_rate, avg_ret, avg_dd, calmar, sharpe,
                    rank_in_stage, is_recommended, eval_start_date, eval_end_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                insert_rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        log.info(f"=== 完成 {len(insert_rows):,} 行 ({time.time()-t0:.0f}s) ===")

        # 报告
        print()
        print(f"{'='*120}")
        print(f"  fund × tech 维度覆盖")
        print(f"{'='*120}")
        for r in conn.execute("""
            SELECT fundamental_stage, technical_stage, COUNT(*) AS n,
                   AVG(sharpe) AS avg_sharpe, SUM(CASE WHEN is_recommended THEN 1 ELSE 0 END) AS n_rec
              FROM mart_stage_formula_fitness
             GROUP BY 1, 2 ORDER BY 1, 2""").fetchall():
            print(f"  fund={r[0]:<10} tech={r[1]:<4} n={r[2]:>3} avg_sharpe={r[3] or 0:+.3f} rec={r[4]}")

        print()
        print(f"{'='*120}")
        print(f"  is_recommended 行 (top 1 in each stage, n≥20, win≥55%)")
        print(f"{'='*120}")
        for r in conn.execute("""
            SELECT fundamental_stage, technical_stage, formula_variant, holding_days,
                   n_signals, win_rate, avg_ret, sharpe
              FROM mart_stage_formula_fitness
             WHERE is_recommended
             ORDER BY sharpe DESC LIMIT 20""").fetchall():
            print(f"  {r[0]:<10} stage={r[1]} {r[2]:<32} hp={r[3]:>3}d  "
                  f"n={r[4]:>4} win={r[5]*100:>4.0f}% ret={r[6]*100:>+5.1f}% sharpe={r[7]:+.2f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
